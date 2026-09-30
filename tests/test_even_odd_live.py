"""THE most important test: the real Even/Odd image goes through the real qwen2.5vl:3b via Ollama.

It is skipped automatically if Ollama or the model is not available (for example on a machine
without Ollama). On the demo machine it must run, not skip:

    python -m unittest tests.test_even_odd_live -v
"""

import re
import subprocess
import sys
import unittest

from backend.config import get_settings
from backend.ollama_client import OllamaClient
from backend.pipeline import run_pipeline
from tests.helpers import SAMPLES, TempDirCase, make_settings

_client = OllamaClient(get_settings())
_status = _client.status()
_READY = _status["reachable"] and _status["model_available"]


def norm(s: str) -> str:
    return re.sub(r"\s+", "", s.lower())


@unittest.skipUnless(_READY, f"Ollama/model not available ({_status['error']})")
class LiveEvenOddTest(TempDirCase, unittest.TestCase):
    def test_real_model_extracts_even_odd_logic_and_python_matches(self):
        settings = make_settings(self.tmp, vision_timeout=get_settings().vision_timeout)
        client = OllamaClient(settings)
        image = (SAMPLES / "even_odd.png").read_bytes()
        result = run_pipeline(image, "even_odd.png", "image/png", "python", "", client, settings)

        # exactly one vision request
        self.assertEqual(client.vision_calls, 1)

        # extracted logic: Input n / Decision n % 2 == 0 / True -> Even / False -> Odd
        ir = result["extracted_logic"]["ir"]
        nodes = {n["id"]: n for n in ir["nodes"]}
        self.assertTrue(any(n["type"] == "input" and "n" in re.findall(r"[a-z_]+", n["text"].lower())
                            for n in ir["nodes"]), "expected an 'Input n' step")
        decision = next((n for n in ir["nodes"] if n["type"] == "decision"), None)
        self.assertIsNotNone(decision, "expected a decision node")
        self.assertIn("n%2==0", norm(decision["condition"]))
        branches = {e["label"].lower(): nodes[e["to"]]["text"].lower() for e in ir["edges"] if e["from"] == decision["id"]}
        self.assertIn("even", branches.get("yes", ""))
        self.assertIn("odd", branches.get("no", ""))

        # generated Python is equivalent to the reference program
        code = result["code"]
        self.assertIn("n % 2 == 0", code)
        for stdin, expected in (("4\n", "Even"), ("7\n", "Odd"), ("0\n", "Even"), ("-3\n", "Odd")):
            out = subprocess.run([sys.executable, "-I", "-c", code], input=stdin, capture_output=True,
                                 text=True, timeout=10).stdout.split()
            self.assertEqual(out[-1], expected, f"input {stdin!r}")

        self.assertEqual(result["verification"]["status"], "VERIFIED")
        self.assertIsNotNone(result["id"])  # saved to SQLite history


if __name__ == "__main__":
    unittest.main()
