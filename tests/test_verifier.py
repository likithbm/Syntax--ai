import shutil
import unittest
from unittest import mock

from backend import verifier
from backend.flow import build_flow_model
from backend.generator import SUPPORTED_LANGUAGES, generate_code
from backend.parser import parse_diagram
from tests.helpers import (ARCH_RESPONSE, EVEN_ODD_RESPONSE, MARKS_RESPONSE, SUM_LOOP_RESPONSE, UML_RESPONSE,
                           make_settings)

TOOLS = {"python": "python3", "c": "gcc", "cpp": "g++", "java": "javac", "javascript": "node"}


def available(lang):
    if lang == "python":
        return True
    if lang == "java":
        return bool(shutil.which("javac") and shutil.which("java"))
    return bool(shutil.which(TOOLS[lang]))


def verify_raw(raw, lang, code=None, **settings):
    ir = parse_diagram(raw)
    g = generate_code(ir, lang)
    return verifier.verify(code or g["code"], lang, ir, g["filename"], make_settings("/tmp", **settings)), g, ir


class VerifierTests(unittest.TestCase):
    def test_even_odd_verified_in_every_available_language(self):
        for lang in SUPPORTED_LANGUAGES:
            with self.subTest(lang=lang):
                if not available(lang):
                    self.skipTest(f"{lang} toolchain not installed")
                v, _, _ = verify_raw(EVEN_ODD_RESPONSE, lang)
                self.assertEqual(v["status"], "VERIFIED", v)
                self.assertTrue(all(c["ok"] for c in v["checks"]))

    def test_loop_flowchart_verified(self):
        v, _, _ = verify_raw(SUM_LOOP_RESPONSE, "python")
        self.assertEqual(v["status"], "VERIFIED", v)

    def test_wrong_behaviour_is_caught(self):
        _, g, _ = verify_raw(MARKS_RESPONSE, "python")
        for label, code in {"swapped output": g["code"].replace('"Pass"', '"X"').replace('"Fail"', '"Pass"').replace('"X"', '"Fail"'),
                            "off-by-one boundary": g["code"].replace(">= 40", "> 40")}.items():
            with self.subTest(label):
                v, _, _ = verify_raw(MARKS_RESPONSE, "python", code=code)
                self.assertEqual(v["status"], "FAILED", label)
                self.assertNotEqual(v["status"], "VERIFIED")

    def test_syntax_error_crash_and_timeout_fail(self):
        _, g, _ = verify_raw(EVEN_ODD_RESPONSE, "python")
        v, _, _ = verify_raw(EVEN_ODD_RESPONSE, "python", code=g["code"].replace("if n", "if if n"))
        self.assertEqual(v["status"], "FAILED")
        self.assertIn("did not compile", v["message"])
        v, _, _ = verify_raw(EVEN_ODD_RESPONSE, "python", code=g["code"] + "\nraise SystemExit(3)\n")
        self.assertEqual(v["status"], "FAILED")
        self.assertIn("crashed", v["message"])
        v, _, _ = verify_raw(EVEN_ODD_RESPONSE, "python", code=g["code"] + "\nwhile True:\n    pass\n", run_timeout=1.0)
        self.assertEqual(v["status"], "FAILED")
        self.assertIn("time limit", v["message"])

    def test_c_boundary_bug_is_caught(self):
        if not available("c"):
            self.skipTest("gcc not installed")
        _, g, _ = verify_raw(MARKS_RESPONSE, "c")
        v, _, _ = verify_raw(MARKS_RESPONSE, "c", code=g["code"].replace(">= 40", "> 40"))
        self.assertEqual(v["status"], "FAILED")

    def test_unavailable_toolchain_is_never_reported_as_verified(self):
        with mock.patch.object(verifier, "_tools", return_value=None):
            v, _, _ = verify_raw(EVEN_ODD_RESPONSE, "c")
        self.assertEqual(v["status"], "UNVERIFIED")
        self.assertTrue(v["message"].startswith("UNVERIFIED – runtime/compiler unavailable"))
        self.assertNotEqual(v["status"], "VERIFIED")
        self.assertFalse(v["message"].startswith("VERIFIED"))

    def test_which_missing_binary_means_unverified(self):
        with mock.patch("shutil.which", return_value=None):
            v, _, _ = verify_raw(EVEN_ODD_RESPONSE, "javascript")
        self.assertEqual(v["status"], "UNVERIFIED")

    def test_uml_and_architecture_only_get_a_syntax_check(self):
        for raw in (UML_RESPONSE, ARCH_RESPONSE):
            for lang in SUPPORTED_LANGUAGES:
                if not available(lang):
                    continue
                with self.subTest(lang=lang):
                    v, _, _ = verify_raw(raw, lang)
                    self.assertEqual(v["status"], "SYNTAX_OK", v)
                    self.assertIn("not checked", v["message"])

    def test_simulation_matches_expected_semantics(self):
        model = build_flow_model(parse_diagram(EVEN_ODD_RESPONSE))
        self.assertEqual(verifier.simulate_flowchart(model, ["4"], "python").split(), ["Enter", "n:", "Even"])
        self.assertEqual(verifier.simulate_flowchart(model, ["7"], "python").split()[-1], "Odd")
        loop = build_flow_model(parse_diagram(SUM_LOOP_RESPONSE))
        self.assertEqual(verifier.simulate_flowchart(loop, ["10"], "python").split()[-1], "55")

    def test_outputs_match_tolerates_float_formatting(self):
        self.assertTrue(verifier.outputs_match("Enter x: 7.0\n", "Enter x: 7\n"))
        self.assertTrue(verifier.outputs_match("2.5", "2.500000"))
        self.assertFalse(verifier.outputs_match("Even", "Odd"))
        self.assertFalse(verifier.outputs_match("a b", "a"))

    def test_boundary_values_are_tested(self):
        model = build_flow_model(parse_diagram(MARKS_RESPONSE))
        pool = verifier._test_values(model)
        for v in ("39", "40", "41"):
            self.assertIn(v, pool)


if __name__ == "__main__":
    unittest.main()
