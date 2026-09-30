import unittest

from backend.generator import generate_code
from backend.parser import parse_diagram
from backend.security import analyse, scan_code
from tests.helpers import EVEN_ODD_RESPONSE


def rules(code, lang):
    return {f["rule"] for f in scan_code(code, lang)}


class SecurityScannerTests(unittest.TestCase):
    def test_python_dangerous_calls(self):
        code = "\n".join(["import os, subprocess", "eval(user)", "exec(code)", "os.system('ls ' + x)",
                          "subprocess.run(cmd, shell=True)", "import pickle; pickle.loads(b)"])
        self.assertEqual(rules(code, "python"), {"eval", "exec", "os-system", "subprocess-shell", "pickle"})

    def test_secrets_detected_in_every_language(self):
        for lang in ("python", "c", "java", "javascript"):
            with self.subTest(lang=lang):
                r = rules('password = "hunter2hunter2"\napi_key = "abcd1234efgh5678"\n', lang)
                self.assertIn("secret-password", r)
                self.assertIn("secret-apikey", r)

    def test_known_credential_formats(self):
        self.assertIn("secret-format", rules('x = "AKIAABCDEFGHIJKLMNOP"', "python"))
        self.assertIn("secret-format", rules("token = ghp_" + "a" * 36, "javascript"))
        self.assertIn("secret-private-key", rules("-----BEGIN RSA PRIVATE KEY-----", "c"))

    def test_sql_injection_patterns(self):
        self.assertIn("sql-concat", rules('q = "SELECT * FROM users WHERE id = " + uid', "python"))
        self.assertIn("sql-fstring", rules('q = f"SELECT * FROM t WHERE id = {uid}"', "python"))
        self.assertIn("sql-format", rules('q = "DELETE FROM t WHERE id = %s" % uid', "python"))
        self.assertIn("sql-template", rules("const q = `SELECT * FROM t WHERE id = ${id}`;", "javascript"))
        self.assertIn("sql-concat", rules('String q = "SELECT * FROM t WHERE id=" + id;', "java"))

    def test_parameterised_query_is_not_flagged(self):
        self.assertEqual(rules('cur.execute("SELECT * FROM t WHERE id = ?", (uid,))', "python"), set())

    def test_c_and_java_and_js_specific(self):
        self.assertEqual(rules('system("ls"); gets(buf); strcpy(a, b); scanf("%s", buf);', "c"),
                         {"c-system", "c-gets", "c-strcpy", "c-scanf-s"})
        self.assertIn("java-exec", rules("Runtime.getRuntime().exec(cmd);", "java"))
        self.assertEqual(rules("eval(x); new Function('return 1'); el.innerHTML = s;", "javascript"),
                         {"eval", "js-function", "js-innerhtml"})

    def test_language_scoping(self):
        self.assertNotIn("eval", rules("eval(x)", "c"))
        self.assertNotIn("c-system", rules("system('x')", "python"))

    def test_commented_out_code_ignored_but_commented_secrets_flagged(self):
        self.assertEqual(rules("# eval(x)\n// system('x')", "python"), set())
        self.assertIn("secret-password", rules('# password = "supersecret"', "python"))

    def test_finding_shape_and_line_numbers(self):
        findings = scan_code("x = 1\n\ny = eval(z)\n", "python")
        self.assertEqual(len(findings), 1)
        f = findings[0]
        self.assertEqual(set(f), {"severity", "finding", "line", "recommendation", "rule"})
        self.assertEqual(f["line"], 3)
        self.assertEqual(f["severity"], "high")
        self.assertTrue(f["recommendation"])

    def test_report_is_labelled_heuristic_and_clean_code_passes(self):
        ir = parse_diagram(EVEN_ODD_RESPONSE)
        for lang in ("python", "c", "cpp", "java", "javascript"):
            report = analyse(generate_code(ir, lang)["code"], lang)
            self.assertEqual(report["findings"], [], lang)
            self.assertIn("heuristic", report["label"].lower())
            self.assertIn("not a professional SAST", report["label"])
        self.assertIn("1 potential issue", analyse("eval(x)", "python")["summary"])


if __name__ == "__main__":
    unittest.main()
