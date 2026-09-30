import subprocess
import sys
import unittest

from backend.errors import GenerationError
from backend.generator import SUPPORTED_LANGUAGES, generate_code, normalize_language
from backend.parser import parse_diagram
from tests.helpers import (ARCH_RESPONSE, EVEN_ODD_RESPONSE, MARKS_RESPONSE, SUM_LOOP_RESPONSE,
                           UML_RESPONSE, flowchart_json)


def gen(raw, lang="python", requirement=""):
    return generate_code(parse_diagram(raw), lang, requirement)


def run_python(code, stdin):
    return subprocess.run([sys.executable, "-I", "-c", code], input=stdin, capture_output=True,
                          text=True, timeout=10).stdout.split()


class GeneratorTests(unittest.TestCase):
    def test_even_odd_python_is_equivalent_to_the_reference_program(self):
        code = gen(EVEN_ODD_RESPONSE)["code"]
        self.assertIn("n = int(input(", code)
        self.assertIn("if n % 2 == 0:", code)
        self.assertIn('print("Even")', code)
        self.assertIn("else:", code)
        self.assertIn('print("Odd")', code)
        self.assertEqual(run_python(code, "4\n")[-1], "Even")
        self.assertEqual(run_python(code, "7\n")[-1], "Odd")

    def test_output_depends_on_the_diagram_not_a_template(self):
        marks = gen(MARKS_RESPONSE)["code"]
        self.assertIn("marks = int(input(", marks)
        self.assertIn("if marks >= 40:", marks)
        self.assertIn('print("Pass")', marks)
        self.assertIn('print("Fail")', marks)
        self.assertNotIn("Even", marks)
        self.assertNotIn("n % 2", marks)
        self.assertEqual(run_python(marks, "39\n")[-1], "Fail")
        self.assertEqual(run_python(marks, "40\n")[-1], "Pass")

    def test_loop_flowchart_becomes_a_while_loop(self):
        code = gen(SUM_LOOP_RESPONSE)["code"]
        self.assertIn("while i <= n:", code)
        self.assertEqual(run_python(code, "10\n")[-1], "55")

    def test_all_languages_generate_for_a_flowchart(self):
        for lang in SUPPORTED_LANGUAGES:
            with self.subTest(lang=lang):
                out = gen(EVEN_ODD_RESPONSE, lang)
                self.assertIn("n % 2", out["code"])
                self.assertIn("Even", out["code"])

    def test_language_specific_shape(self):
        self.assertIn("#include <stdio.h>", gen(EVEN_ODD_RESPONSE, "c")["code"])
        self.assertIn("scanf", gen(EVEN_ODD_RESPONSE, "c")["code"])
        self.assertIn("std::cin", gen(EVEN_ODD_RESPONSE, "cpp")["code"])
        java = gen(EVEN_ODD_RESPONSE, "java")
        self.assertEqual(java["filename"], "Main.java")
        self.assertIn("public class Main", java["code"])
        js = gen(EVEN_ODD_RESPONSE, "javascript")["code"]
        self.assertIn("n % 2 === 0", js)

    def test_language_aliases_and_unsupported(self):
        self.assertEqual(normalize_language("C++"), "cpp")
        self.assertEqual(normalize_language("JS"), "javascript")
        with self.assertRaises(GenerationError):
            normalize_language("cobol")

    def test_else_if_chain(self):
        raw = flowchart_json(
            [("start", "Start"), ("input", "Input marks"), ("decision", "marks >= 90?"), ("output", "Print A"),
             ("decision", "marks >= 40?"), ("output", "Print B"), ("output", "Print Fail"), ("end", "End")],
            [(1, 2, ""), (2, 3, ""), (3, 4, "Yes"), (3, 5, "No"), (5, 6, "Yes"), (5, 7, "No"),
             (4, 8, ""), (6, 8, ""), (7, 8, "")])
        code = gen(raw)["code"]
        self.assertIn("elif marks >= 40:", code)

    def test_variable_types_are_inferred(self):
        raw = flowchart_json(
            [("start", "Start"), ("input", "Input a, b"), ("process", "avg = (a + b) / 2"),
             ("output", "Print avg"), ("end", "End")], [(1, 2, ""), (2, 3, ""), (3, 4, ""), (4, 5, "")])
        c = gen(raw, "c")["code"]
        self.assertIn("double avg", c)
        self.assertIn('scanf("%lf", &a)', c)
        self.assertIn('scanf("%lf", &b)', c)  # "a" is a variable, not the English article

    # ---------------------------------------------------------------- safety of generated code
    def test_hostile_condition_is_rejected(self):
        for cond in ("__import__('os').system('calc') == 0", "open('x').read()", "n.__class__",
                     "n ** 9999999", "[1,2][0]", "lambda: 1"):
            raw = flowchart_json(
                [("start", "Start"), ("input", "Input n"), ("decision", cond), ("output", "A"), ("output", "B"),
                 ("end", "End")],
                [(1, 2, ""), (2, 3, ""), (3, 4, "Yes"), (3, 5, "No"), (4, 6, ""), (5, 6, "")])
            with self.subTest(cond=cond), self.assertRaises(GenerationError):
                gen(raw)

    def test_untranslatable_step_becomes_comment_with_warning(self):
        raw = flowchart_json([("start", "Start"), ("process", "x = os.system('rm -rf /')"), ("end", "End")],
                             [(1, 2, ""), (2, 3, "")])
        out = gen(raw)
        self.assertTrue(any("could not be translated" in w for w in out["warnings"]))
        for line in out["code"].splitlines():
            if "system(" in line:
                self.assertTrue(line.lstrip().startswith("#"), f"executable line contains model text: {line!r}")

    def test_comment_text_cannot_splice_or_close_comments(self):
        raw = flowchart_json([("start", "Start"), ("process", "weird step \\"), ("output", "Print ok"), ("end", "End")],
                             [(1, 2, ""), (2, 3, ""), (3, 4, "")])
        for lang in ("c", "cpp", "java", "javascript"):
            code = gen(raw, lang)["code"]
            for line in code.splitlines():
                if "TODO" in line:
                    self.assertNotIn("\\", line)
        raw = flowchart_json([("start", "Start"), ("process", "a */ evil(); /* b"), ("end", "End")],
                             [(1, 2, ""), (2, 3, "")])
        self.assertNotIn("*/ evil", gen(raw, "c")["code"])

    def test_string_literals_are_escaped_in_every_language(self):
        nasty = 'say "hi" \\ 100% \n done'
        raw = flowchart_json([("start", "Start"), ("output", f"Print '{nasty}'"), ("end", "End")],
                             [(1, 2, ""), (2, 3, "")])
        py = gen(raw)["code"]
        self.assertEqual(run_python(py, "")[:2], ["say", '"hi"'])
        self.assertIn("100%%", gen(raw, "c")["code"])  # printf format escaped

    def test_requirement_is_a_single_sanitised_comment(self):
        code = gen(EVEN_ODD_RESPONSE, "c", "lab 3\n*/ system(\"x\"); /*")["code"]
        req_lines = [l for l in code.splitlines() if "Requirement" in l]
        self.assertEqual(len(req_lines), 1)
        self.assertTrue(req_lines[0].startswith("//"))
        self.assertNotIn("\nsystem(", code)

    def test_reserved_variable_names_are_renamed(self):
        raw = flowchart_json([("start", "Start"), ("input", "Input class"), ("output", "Print class"), ("end", "End")],
                             [(1, 2, ""), (2, 3, ""), (3, 4, "")])
        code = gen(raw, "java")["code"]
        self.assertIn("class_", code)
        self.assertEqual(run_python(gen(raw)["code"], "5\n")[-1], "5")

    # ---------------------------------------------------------------- UML / architecture
    def test_uml_skeleton(self):
        py = gen(UML_RESPONSE)["code"]
        self.assertIn("class Dog(Animal):", py)
        self.assertIn("def fetch(self, item: Ball) -> Ball:", py)
        self.assertIn("raise NotImplementedError", py)
        java = gen(UML_RESPONSE, "java")["code"]
        self.assertIn("class Dog extends Animal", java)
        self.assertIn("abstract class Animal", java)
        cpp = gen(UML_RESPONSE, "cpp")["code"]
        self.assertIn("class Dog : public Animal", cpp)
        self.assertIn("struct Dog {", gen(UML_RESPONSE, "c")["code"])
        self.assertIn("class Dog extends Animal", gen(UML_RESPONSE, "javascript")["code"])

    def test_uml_does_not_invent_members(self):
        py = gen(UML_RESPONSE)["code"]
        self.assertNotIn("def get_", py)
        self.assertNotIn("__str__", py)

    def test_architecture_scaffold(self):
        py = gen(ARCH_RESPONSE)["code"]
        for name in ("WebClient", "APIGateway", "UsersDB"):
            self.assertIn(f"class {name}(Component):", py)
        self.assertIn("connect(", py)
        self.assertNotIn("UserService", py)  # nothing that is not in the diagram

    def test_code_generation_failure_is_a_clean_error(self):
        with self.assertRaises(GenerationError):
            generate_code({"diagram_type": "flowchart", "start": "1", "nodes": [], "edges": []}, "python")


if __name__ == "__main__":
    unittest.main()
