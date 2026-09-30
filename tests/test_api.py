"""API tests. They need FastAPI (pip install -r requirements.txt) and are skipped without it."""

import time
import unittest

try:
    from fastapi.testclient import TestClient
    from backend.app import create_app
except ImportError:  # pragma: no cover
    TestClient = None

from tests.helpers import (EVEN_ODD_RESPONSE, FakeClient, TempDirCase, make_settings, png_bytes, sample_bytes)


@unittest.skipIf(TestClient is None, "fastapi is not installed in this environment")
class ApiTests(TempDirCase, unittest.TestCase):
    def make(self, response=EVEN_ODD_RESPONSE, **kw):
        self.fake = FakeClient(response, **kw)
        return TestClient(create_app(self.fake, make_settings(self.tmp)))

    def submit(self, c, image=None, name="even_odd.png", ctype="image/png", language="python", requirement=""):
        files = {"image": (name, sample_bytes() if image is None else image, ctype)}
        return c.post("/api/generate", files=files, data={"language": language, "requirement": requirement})

    def wait(self, c, job_id, timeout=60):
        end = time.time() + timeout
        while time.time() < end:
            job = c.get(f"/api/jobs/{job_id}").json()
            if job["status"] != "running":
                return job
            time.sleep(0.05)
        self.fail("job did not finish")

    def test_health(self):
        with self.make() as c:
            r = c.get("/api/health")
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.json()["status"], "ok")

    def test_frontend_and_samples_are_served(self):
        with self.make() as c:
            page = c.get("/")
            self.assertEqual(page.status_code, 200)
            self.assertIn("Syntax AI", page.text)
            self.assertEqual(c.get("/app.js").status_code, 200)
            self.assertEqual(c.get("/styles.css").status_code, 200)
            self.assertEqual(c.get("/samples/even_odd.png").status_code, 200)

    def test_ai_status_ready_and_offline(self):
        with self.make() as c:
            st = c.get("/api/ai-status").json()
            self.assertEqual(st["state"], "ready")
            self.assertEqual(st["label"], "AI Ready · qwen2.5vl:3b")

        class Offline(FakeClient):
            def status(self):
                return {"reachable": False, "model_available": False, "model": "qwen2.5vl:3b",
                        "base_url": "http://localhost:11434", "installed_models": [], "error": "down"}
        c = TestClient(create_app(Offline(), make_settings(self.tmp)))
        st = c.get("/api/ai-status").json()
        self.assertEqual((st["state"], st["label"]), ("offline", "AI Offline"))

    def test_generate_even_odd_full_flow_and_history(self):
        with self.make() as c:
            r = self.submit(c)
            self.assertEqual(r.status_code, 202)
            job = self.wait(c, r.json()["job_id"])
            self.assertEqual(job["status"], "done", job)
            res = job["result"]
            self.assertIn("if n % 2 == 0:", res["code"])
            self.assertEqual(res["verification"]["status"], "VERIFIED")
            self.assertEqual(self.fake.vision_calls, 1)
            hist = c.get("/api/history").json()
            self.assertEqual(len(hist), 1)
            item = c.get(f"/api/history/{hist[0]['id']}").json()
            self.assertEqual(item["generated_code"], res["code"])
            self.assertEqual(c.delete(f"/api/history/{hist[0]['id']}").status_code, 200)
            self.assertEqual(c.get("/api/history").json(), [])
            self.assertEqual(c.get("/api/history/999").status_code, 404)

    def test_invalid_image_gives_clean_error_and_no_ai_call(self):
        with self.make() as c:
            job = self.wait(c, self.submit(c, image=b"nope", name="x.png").json()["job_id"])
            self.assertEqual(job["status"], "error")
            self.assertEqual(job["error"]["code"], "invalid_image")
            self.assertNotIn("Traceback", job["error"]["message"])
            self.assertEqual(self.fake.vision_calls, 0)

    def test_oversized_upload_rejected(self):
        with self.make() as c:
            r = self.submit(c, image=b"\0" * (3 * 1024 * 1024))
            self.assertEqual(r.status_code, 413)
            self.assertEqual(r.json()["error"]["code"], "invalid_image")

    def test_malformed_model_output_is_a_clean_job_error(self):
        with self.make("garbage") as c:
            job = self.wait(c, self.submit(c).json()["job_id"])
            self.assertEqual(job["error"]["code"], "malformed_model_response")

    def test_unknown_job_and_missing_file_field(self):
        with self.make() as c:
            self.assertEqual(c.get("/api/jobs/nope").status_code, 404)
            self.assertEqual(c.post("/api/generate", data={"language": "python"}).status_code, 422)

    def test_clear_history(self):
        with self.make() as c:
            self.wait(c, self.submit(c).json()["job_id"])
            self.assertEqual(c.delete("/api/history").json()["deleted"], 1)


if __name__ == "__main__":
    unittest.main()
