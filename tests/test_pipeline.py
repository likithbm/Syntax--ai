import io
import unittest
from pathlib import Path

from PIL import Image

from backend import database
from backend.errors import (DiagramInterpretationError, ImageError, ModelMissingError, ModelResponseError,
                            OllamaOfflineError, UnsupportedDiagramError)
from backend.pipeline import run_pipeline
from tests.helpers import (ARCH_RESPONSE, EVEN_ODD_RESPONSE, MARKS_RESPONSE, SAMPLES, UML_RESPONSE, FakeClient,
                           TempDirCase, make_settings, png_bytes, sample_bytes)


class PipelineTests(TempDirCase, unittest.TestCase):
    def run_it(self, client, image=None, lang="python", requirement="", progress=None, name="even_odd.png"):
        settings = make_settings(self.tmp)
        image = sample_bytes(name) if image is None else image
        return run_pipeline(image, name, "image/png", lang, requirement, client, settings, progress), settings

    # ------------------------------------------------------------------ the core requirement
    def test_even_odd_end_to_end(self):
        client = FakeClient(EVEN_ODD_RESPONSE)
        result, settings = self.run_it(client)
        self.assertEqual(result["diagram_type"], "flowchart")
        summary = result["extracted_logic"]["summary"]
        self.assertIn("Input: Input n", summary)
        self.assertIn("Decision: n % 2 == 0", summary)
        self.assertIn("  True → Even", summary)
        self.assertIn("  False → Odd", summary)
        self.assertIn("if n % 2 == 0:", result["code"])
        self.assertEqual(result["verification"]["status"], "VERIFIED")
        self.assertEqual(result["security"]["findings"], [])
        self.assertEqual(len(database.list_history(settings.db_path)), 1)
        self.assertEqual(result["id"], database.list_history(settings.db_path)[0]["id"])

    def test_exactly_one_vision_request(self):
        client = FakeClient(EVEN_ODD_RESPONSE)
        result, _ = self.run_it(client)
        self.assertEqual(client.vision_calls, 1)
        self.assertEqual(len(client.prompts), 1)
        self.assertEqual(result["ai"]["vision_calls"], 1)

    def test_no_retry_after_malformed_response(self):
        client = FakeClient("this is not json at all")
        with self.assertRaises(ModelResponseError):
            self.run_it(client)
        self.assertEqual(client.vision_calls, 1)  # no automatic retries

    def test_the_actual_uploaded_pixels_reach_the_model(self):
        client = FakeClient(EVEN_ODD_RESPONSE)
        self.run_it(client)
        sent = Image.open(io.BytesIO(client.images[0])).convert("RGB")
        original = Image.open(SAMPLES / "even_odd.png").convert("RGB")
        self.assertEqual(sent.size, original.size)
        self.assertEqual(sent.tobytes(), original.tobytes())

    def test_generated_code_follows_the_model_answer_not_a_template(self):
        image = sample_bytes("even_odd.png")
        a = self.run_it(FakeClient(EVEN_ODD_RESPONSE), image=image)[0]["code"]
        b = self.run_it(FakeClient(MARKS_RESPONSE), image=image)[0]["code"]
        self.assertIn("n % 2 == 0", a)
        self.assertNotIn("marks", a)
        self.assertIn("marks >= 40", b)
        self.assertNotIn("Even", b)
        self.assertNotEqual(a, b)

    def test_vision_prompt_asks_for_json_and_forbids_inventing(self):
        client = FakeClient()
        self.run_it(client)
        prompt = client.prompts[0]
        self.assertIn("JSON", prompt)
        self.assertIn("Never invent", prompt)
        self.assertNotIn("Even", prompt)  # the prompt must not leak the sample answer
        self.assertNotIn("n % 2", prompt)

    # ------------------------------------------------------------------ other diagram types / languages
    def test_uml_and_architecture(self):
        uml, _ = self.run_it(FakeClient(UML_RESPONSE))
        self.assertEqual(uml["diagram_type"], "uml")
        self.assertIn("class Dog(Animal):", uml["code"])
        self.assertEqual(uml["verification"]["status"], "SYNTAX_OK")
        arch, _ = self.run_it(FakeClient(ARCH_RESPONSE), lang="java")
        self.assertEqual(arch["diagram_type"], "architecture")
        self.assertEqual(arch["code_filename"], "Main.java")

    def test_language_choice_and_requirement_comment(self):
        result, _ = self.run_it(FakeClient(), lang="c++", requirement="  CS101 lab 3 ")
        self.assertEqual(result["language"], "cpp")
        self.assertIn("std::cout", result["code"])
        self.assertIn("Requirement (recorded, not interpreted): CS101 lab 3", result["code"])

    # ------------------------------------------------------------------ errors (each must be a clean message)
    def test_ollama_offline_and_model_missing_stop_before_any_vision_call(self):
        for err, exc in ((OllamaOfflineError("AI Offline"), OllamaOfflineError),
                         (ModelMissingError("missing"), ModelMissingError)):
            client = FakeClient(ready_error=err)
            with self.assertRaises(exc):
                self.run_it(client)
            self.assertEqual(client.vision_calls, 0)

    def test_invalid_image_never_reaches_the_model(self):
        client = FakeClient()
        for bad in (b"", b"not an image", png_bytes(size=(5, 5))):
            with self.assertRaises(ImageError):
                self.run_it(client, image=bad)
        self.assertEqual(client.vision_calls, 0)

    def test_unsupported_and_unclear_diagrams(self):
        with self.assertRaises(UnsupportedDiagramError):
            self.run_it(FakeClient('{"diagram_type": "unsupported"}'))
        with self.assertRaises(DiagramInterpretationError) as ctx:
            self.run_it(FakeClient('{"diagram_type": "flowchart", "nodes": []}'))
        self.assertIn("Unable to reliably interpret this diagram", ctx.exception.message)

    def test_nothing_is_saved_when_the_run_fails(self):
        settings = make_settings(self.tmp)
        with self.assertRaises(ModelResponseError):
            run_pipeline(sample_bytes(), "x.png", "image/png", "python", "", FakeClient("nope"), settings)
        self.assertEqual(database.list_history(settings.db_path), [])

    def test_history_failure_does_not_lose_the_result(self):
        bad_db = Path(self.tmp) / "a_directory"
        bad_db.mkdir()
        settings = make_settings(self.tmp, db_path=bad_db)
        result = run_pipeline(sample_bytes(), "x.png", "image/png", "python", "", FakeClient(), settings)
        self.assertIsNone(result["id"])
        self.assertIn("if n % 2 == 0:", result["code"])
        self.assertTrue(any("history" in w for w in result["warnings"]))

    def test_unexpected_internal_error_is_a_generic_message(self):
        class Exploding(FakeClient):
            def analyze_image(self, *a):
                raise RuntimeError("secret internal path /etc/shadow")
        with self.assertRaises(Exception) as ctx:
            self.run_it(Exploding())
        self.assertNotIn("shadow", str(ctx.exception))
        self.assertNotIn("Traceback", str(ctx.exception))

    def test_progress_stages_are_reported_in_order(self):
        stages = []
        self.run_it(FakeClient(), progress=lambda s, m: stages.append(s))
        self.assertEqual(stages, ["validating", "checking_ai", "vision", "parsing", "generating",
                                  "security", "verifying", "saving", "done"])


if __name__ == "__main__":
    unittest.main()
