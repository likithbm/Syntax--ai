import base64
import json
import unittest

import httpx

from backend.errors import ModelMissingError, ModelResponseError, OllamaError, OllamaOfflineError
from backend.ollama_client import OllamaClient
from tests.helpers import make_settings


def client_with(handler, **over):
    return OllamaClient(make_settings("/tmp", **over), transport=httpx.MockTransport(handler))


def tags(models):
    return lambda req: httpx.Response(200, json={"models": [{"name": m} for m in models]})


class OllamaClientTests(unittest.TestCase):
    def test_status_ready(self):
        st = client_with(tags(["qwen2.5vl:3b", "llama3:8b"])).status()
        self.assertTrue(st["reachable"])
        self.assertTrue(st["model_available"])
        self.assertIsNone(st["error"])

    def test_status_model_missing(self):
        st = client_with(tags(["llama3:8b"])).status()
        self.assertTrue(st["reachable"])
        self.assertFalse(st["model_available"])
        self.assertIn("ollama pull qwen2.5vl:3b", st["error"])

    def test_status_offline(self):
        def boom(req):
            raise httpx.ConnectError("refused")
        st = client_with(boom).status()
        self.assertFalse(st["reachable"])
        self.assertIn("not reachable", st["error"])

    def test_ensure_ready_errors(self):
        def boom(req):
            raise httpx.ConnectError("refused")
        with self.assertRaises(OllamaOfflineError) as ctx:
            client_with(boom).ensure_ready()
        self.assertIn("AI Offline", ctx.exception.message)
        with self.assertRaises(ModelMissingError):
            client_with(tags(["other:1b"])).ensure_ready()
        client_with(tags(["qwen2.5vl:3b"])).ensure_ready()  # no error

    def test_model_name_without_tag_matches_latest(self):
        st = client_with(tags(["qwen2.5vl:latest"]), ai_model="qwen2.5vl").status()
        self.assertTrue(st["model_available"])

    def test_analyze_image_sends_actual_image_once(self):
        seen = []

        def handler(req):
            seen.append(json.loads(req.content))
            return httpx.Response(200, json={"message": {"role": "assistant", "content": '{"ok": true}'}})

        c = client_with(handler)
        png = b"\x89PNG-fake-bytes"
        self.assertEqual(c.analyze_image(png, "describe"), '{"ok": true}')
        self.assertEqual(len(seen), 1)
        self.assertEqual(c.vision_calls, 1)
        body = seen[0]
        self.assertEqual(body["model"], "qwen2.5vl:3b")
        self.assertIs(body["stream"], False)
        self.assertEqual(body["format"], "json")
        self.assertEqual(body["messages"][0]["content"], "describe")
        self.assertEqual(base64.b64decode(body["messages"][0]["images"][0]), png)

    def test_analyze_errors(self):
        def boom(req):
            raise httpx.ConnectError("refused")
        with self.assertRaises(OllamaOfflineError):
            client_with(boom).analyze_image(b"x", "p")

        def slow(req):
            raise httpx.ReadTimeout("slow")
        with self.assertRaises(OllamaError) as ctx:
            client_with(slow).analyze_image(b"x", "p")
        self.assertIn("did not answer", ctx.exception.message)

        with self.assertRaises(ModelMissingError):
            client_with(lambda r: httpx.Response(404, json={"error": "model not found"})).analyze_image(b"x", "p")
        with self.assertRaises(OllamaError):
            client_with(lambda r: httpx.Response(500, json={"error": "boom"})).analyze_image(b"x", "p")
        with self.assertRaises(ModelResponseError):
            client_with(lambda r: httpx.Response(200, text="not json")).analyze_image(b"x", "p")
        with self.assertRaises(ModelResponseError):
            client_with(lambda r: httpx.Response(200, json={"message": {"content": "  "}})).analyze_image(b"x", "p")

    def test_no_stack_trace_or_url_internals_in_messages(self):
        def boom(req):
            raise httpx.ConnectError("refused")
        try:
            client_with(boom).analyze_image(b"x", "p")
        except OllamaOfflineError as exc:
            self.assertNotIn("Traceback", exc.message)


if __name__ == "__main__":
    unittest.main()
