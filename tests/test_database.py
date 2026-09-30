import unittest
from pathlib import Path

from backend import database
from tests.helpers import TempDirCase


def record(**over):
    base = dict(image_filename="even_odd.png", diagram_type="flowchart", language="python",
                requirement="lab", extracted_logic={"summary": ["Input: n"], "ir": {"nodes": []}},
                generated_code="print('héllo')\n", code_filename="main.py",
                security_findings={"findings": []}, verification_status="VERIFIED",
                verification_message="ok", model="qwen2.5vl:3b")
    base.update(over)
    return base


class DatabaseTests(TempDirCase, unittest.TestCase):
    def test_save_and_get_round_trip(self):
        db = Path(self.tmp) / "sub" / "h.db"  # parent directory is created on demand
        item_id = database.save_result(db, record())
        got = database.get_history(db, item_id)
        self.assertEqual(got["image_filename"], "even_odd.png")
        self.assertEqual(got["diagram_type"], "flowchart")
        self.assertEqual(got["language"], "python")
        self.assertEqual(got["extracted_logic"]["summary"], ["Input: n"])
        self.assertEqual(got["generated_code"], "print('héllo')\n")
        self.assertEqual(got["security_findings"], {"findings": []})
        self.assertEqual(got["verification_status"], "VERIFIED")
        self.assertTrue(got["timestamp"])

    def test_list_is_newest_first_and_summarised(self):
        db = Path(self.tmp) / "h.db"
        ids = [database.save_result(db, record(image_filename=f"f{i}.png")) for i in range(3)]
        items = database.list_history(db)
        self.assertEqual([i["id"] for i in items], ids[::-1])
        self.assertNotIn("generated_code", items[0])
        self.assertEqual(len(database.list_history(db, limit=2)), 2)

    def test_delete_and_clear(self):
        db = Path(self.tmp) / "h.db"
        a, b = database.save_result(db, record()), database.save_result(db, record())
        self.assertTrue(database.delete_history(db, a))
        self.assertFalse(database.delete_history(db, a))
        self.assertIsNone(database.get_history(db, a))
        self.assertEqual(database.clear_history(db), 1)
        self.assertEqual(database.list_history(db), [])

    def test_empty_database_and_missing_id(self):
        db = Path(self.tmp) / "empty.db"
        self.assertEqual(database.list_history(db), [])
        self.assertIsNone(database.get_history(db, 999))

    def test_sql_injection_in_filename_is_stored_literally(self):
        db = Path(self.tmp) / "h.db"
        evil = "x'); DROP TABLE history;--.png"
        item_id = database.save_result(db, record(image_filename=evil))
        self.assertEqual(database.get_history(db, item_id)["image_filename"], evil)
        self.assertEqual(len(database.list_history(db)), 1)


if __name__ == "__main__":
    unittest.main()
