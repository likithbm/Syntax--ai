"""Vision step: ONE request to the vision model that returns the diagram as structured JSON."""

from __future__ import annotations

VISION_PROMPT = """You are a diagram reader. Look carefully at the image and describe the diagram it contains as JSON. Output JSON only, with no explanation.

Step 1. Decide the diagram type:
- "flowchart": boxes, ovals and diamonds joined by arrows
- "uml": a class diagram made of class boxes
- "architecture": components, services, databases or clients joined by arrows
- "unsupported": anything else

Step 2. Copy the content exactly as written in the image. Never invent, add or fix steps. Include every shape and every arrow.

FLOWCHART format:
{"diagram_type":"flowchart",
 "nodes":[{"id":"1","type":"start|end|input|output|process|decision","text":"<text exactly as written in the shape>","condition":"<decisions only: the condition without the question mark>"}],
 "edges":[{"from":"<id>","to":"<id>","label":"<Yes or No for arrows leaving a decision, otherwise empty>"}]}
Rules: oval = start or end; parallelogram = input or output; rectangle = process; diamond = decision. Every arrow is one edge. Every arrow leaving a diamond must carry the label "Yes" or "No" exactly as drawn.

UML format:
{"diagram_type":"uml",
 "classes":[{"name":"<ClassName>","stereotype":"class|interface|abstract","attributes":["<visibility><name>: <type>"],"methods":["<visibility><name>(<params>): <return type>"]}],
 "relationships":[{"from":"<ClassName>","to":"<ClassName>","type":"inheritance|implements|association|aggregation|composition|dependency","label":""}]}

ARCHITECTURE format:
{"diagram_type":"architecture",
 "components":[{"name":"<name>","type":"service|database|client|gateway|queue|other","description":"<only if written in the image>"}],
 "connections":[{"from":"<name>","to":"<name>","label":"<label on the arrow, if any>"}]}

If the image is not one of these diagram types, return {"diagram_type":"unsupported"}."""


def build_prompt() -> str:
    return VISION_PROMPT


def extract_diagram(client, image_png: bytes) -> str:
    """Send the prepared image to the vision model exactly once; return its raw text answer."""
    return client.analyze_image(image_png, build_prompt())
