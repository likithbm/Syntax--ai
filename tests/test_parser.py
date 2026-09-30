import json
import unittest

from backend.errors import DiagramInterpretationError, ModelResponseError, UnsupportedDiagramError
from backend.parser import UNCLEAR, extract_json, parse_diagram, summarize_ir
from tests.helpers import ARCH_RESPONSE, EVEN_ODD_RESPONSE, UML_RESPONSE, flowchart_json


class ParserTests(unittest.TestCase):
    def test_even_odd_structure(self):
        ir = parse_diagram(EVEN_ODD_RESPONSE)
        self.assertEqual(ir["diagram_type"], "flowchart")
        decision = next(n for n in ir["nodes"] if n["type"] == "decision")
        self.assertEqual(decision["condition"], "n % 2 == 0")
        summary = summarize_ir(ir)
        self.assertIn("Input: Input n", summary)
        self.assertIn("Decision: n % 2 == 0", summary)
        self.assertIn("  True → Even", summary)
        self.assertIn("  False → Odd", summary)

    def test_tolerates_code_fences_and_surrounding_prose(self):
        self.assertEqual(extract_json('```json\n{"a": 1}\n```'), {"a": 1})
        self.assertEqual(extract_json('Sure! Here it is: {"a": 1} hope that helps'), {"a": 1})

    def test_malformed_output_is_rejected(self):
        for raw in ("", "   ", "I cannot read this image", "{not json}", "[1, 2, 3]"):
            with self.subTest(raw=raw), self.assertRaises(ModelResponseError):
                parse_diagram(raw)

    def test_missing_or_empty_nodes(self):
        for raw in ('{"diagram_type": "flowchart"}', '{"diagram_type": "flowchart", "nodes": []}',
                    '{"diagram_type": "flowchart", "nodes": "x"}'):
            with self.subTest(raw=raw), self.assertRaises(DiagramInterpretationError) as ctx:
                parse_diagram(raw)
            self.assertEqual(ctx.exception.message, UNCLEAR)

    def test_edge_to_unknown_node_is_rejected_not_guessed(self):
        raw = flowchart_json([("start", "Start"), ("end", "End")], [(1, 9, "")])
        with self.assertRaises(DiagramInterpretationError):
            parse_diagram(raw)

    def test_decision_needs_labelled_yes_and_no(self):
        raw = flowchart_json(
            [("start", "Start"), ("decision", "x > 1?"), ("output", "A"), ("output", "B")],
            [(1, 2, ""), (2, 3, ""), (2, 4, "")])
        with self.assertRaises(DiagramInterpretationError) as ctx:
            parse_diagram(raw)
        self.assertIn("Yes and No", ctx.exception.message)

    def test_decision_branch_fields_are_accepted(self):
        data = json.loads(EVEN_ODD_RESPONSE)
        data["edges"] = [e for e in data["edges"] if e["from"] != "3"]
        data["nodes"][2]["yes"], data["nodes"][2]["no"] = "4", "5"
        ir = parse_diagram(json.dumps(data))
        labels = sorted(e["label"] for e in ir["edges"] if e["from"] == "3")
        self.assertEqual(labels, ["No", "Yes"])

    def test_node_types_inferred_from_text_when_model_is_vague(self):
        raw = flowchart_json(
            [("process", "Start"), ("process", "Read a, b"), ("process", "Print a"), ("process", "End")],
            [(1, 2, ""), (2, 3, ""), (3, 4, "")])
        ir = parse_diagram(raw)
        self.assertEqual([n["type"] for n in ir["nodes"]], ["start", "input", "output", "end"])

    def test_two_starts_rejected(self):
        raw = flowchart_json([("start", "Start"), ("start", "Start"), ("end", "End")], [(1, 3, ""), (2, 3, "")])
        with self.assertRaises(DiagramInterpretationError):
            parse_diagram(raw)

    def test_unreachable_shapes_are_dropped_with_a_note(self):
        raw = flowchart_json([("start", "Start"), ("output", "Hi"), ("output", "Stray"), ("end", "End")],
                             [(1, 2, ""), (2, 4, "")])
        ir = parse_diagram(raw)
        self.assertEqual(len(ir["nodes"]), 3)
        self.assertTrue(any("not connected" in w for w in ir["warnings"]))

    def test_unsupported_and_unknown_diagram_types(self):
        with self.assertRaises(UnsupportedDiagramError):
            parse_diagram('{"diagram_type": "sequence diagram", "nodes": []}')
        with self.assertRaises(UnsupportedDiagramError):
            parse_diagram('{"diagram_type": "unsupported"}')
        with self.assertRaises(UnsupportedDiagramError):
            parse_diagram('{"hello": "world"}')

    def test_uml_parsing(self):
        ir = parse_diagram(UML_RESPONSE)
        self.assertEqual(ir["diagram_type"], "uml")
        dog = next(c for c in ir["classes"] if c["name"] == "Dog")
        self.assertEqual(dog["attributes"][0], {"name": "breed", "type": "String", "visibility": "private"})
        self.assertEqual(dog["methods"][0]["name"], "fetch")
        self.assertEqual(dog["methods"][0]["params"], [{"name": "item", "type": "Ball"}])
        self.assertEqual(dog["methods"][0]["return_type"], "Ball")
        self.assertEqual(ir["relationships"], [{"from": "Dog", "to": "Animal", "type": "inheritance", "label": ""}])

    def test_uml_unknown_relationship_target_ignored_with_note(self):
        data = json.loads(UML_RESPONSE)
        data["relationships"].append({"from": "Dog", "to": "Ghost", "type": "association"})
        ir = parse_diagram(json.dumps(data))
        self.assertEqual(len(ir["relationships"]), 1)
        self.assertTrue(ir["warnings"])

    def test_architecture_parsing(self):
        ir = parse_diagram(ARCH_RESPONSE)
        self.assertEqual([c["name"] for c in ir["components"]], ["WebClient", "APIGateway", "UsersDB"])
        self.assertEqual(len(ir["connections"]), 2)

    def test_text_is_sanitised(self):
        raw = flowchart_json([("start", "Start\x00\n"), ("output", "Hi\x07 there"), ("end", "End")],
                             [(1, 2, ""), (2, 3, "")])
        ir = parse_diagram(raw)
        self.assertEqual(ir["nodes"][1]["text"], "Hi there")


if __name__ == "__main__":
    unittest.main()
