import threading
import time
import unittest

from backend.errors import ImageError
from backend.jobs import JobStore, run_job


class JobStoreTests(unittest.TestCase):
    def test_success_records_result_and_stages(self):
        store = JobStore()
        jid = store.create()
        self.assertEqual(store.get(jid)["status"], "running")

        def work(progress):
            progress("vision", "Reading…")
            self.assertEqual(store.get(jid)["stage"], "vision")
            return {"ok": True}
        run_job(store, jid, work)
        job = store.get(jid)
        self.assertEqual((job["status"], job["result"]), ("done", {"ok": True}))
        self.assertGreaterEqual(job["elapsed"], 0)

    def test_known_error_keeps_its_code_and_message(self):
        store = JobStore()
        jid = store.create()

        def work(progress):
            raise ImageError("bad picture")
        run_job(store, jid, work)
        self.assertEqual(store.get(jid)["error"], {"code": "invalid_image", "message": "bad picture"})

    def test_unexpected_error_never_leaks_details(self):
        store = JobStore()
        jid = store.create()

        def work(progress):
            raise RuntimeError("/etc/passwd stack detail")
        run_job(store, jid, work)
        job = store.get(jid)
        self.assertEqual(job["status"], "error")
        self.assertNotIn("passwd", job["error"]["message"])

    def test_unknown_job_and_pruning(self):
        store = JobStore(ttl_seconds=0)
        self.assertIsNone(store.get("missing"))
        old = store.create()
        run_job(store, old, lambda p: 1)
        time.sleep(0.01)
        store.create()  # creating a new job prunes finished stale ones
        self.assertIsNone(store.get(old))

    def test_thread_safety_smoke(self):
        store = JobStore()
        ids = [store.create() for _ in range(20)]
        threads = [threading.Thread(target=run_job, args=(store, i, lambda p: 1)) for i in ids]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertTrue(all(store.get(i)["status"] == "done" for i in ids))


if __name__ == "__main__":
    unittest.main()
