"""Shared test helpers (fixtures only; the app itself never uses these)."""

from __future__ import annotations

import io
import json
import shutil
import tempfile
from pathlib import Path

from PIL import Image

from backend.config import Settings

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "samples"


def make_settings(tmp: str | Path, **over) -> Settings:
    base = dict(
        ai_base_url="http://localhost:11434", ai_model="qwen2.5vl:3b", vision_timeout=5.0,
        db_path=Path(tmp) / "test.db", max_image_mb=2.0, max_image_side=1280,
        compile_timeout=30.0, run_timeout=5.0,
    )
    base.update(over)
    return Settings(**base)


def flowchart_json(nodes, edges) -> str:
    """nodes: [(type, text)], edges: [(from_index, to_index, label)] with 1-based indexes."""
    return json.dumps({
        "diagram_type": "flowchart",
        "nodes": [{"id": str(i), "type": t, "text": x} for i, (t, x) in enumerate(nodes, start=1)],
        "edges": [{"from": str(a), "to": str(b), "label": lab} for a, b, lab in edges],
    })


# What a vision model would return for the Even/Odd sample (test fixture, not app data).
EVEN_ODD_RESPONSE = flowchart_json(
    [("start", "Start"), ("input", "Input n"), ("decision", "n % 2 == 0?"),
     ("output", "Even"), ("output", "Odd"), ("end", "End")],
    [(1, 2, ""), (2, 3, ""), (3, 4, "Yes"), (3, 5, "No"), (4, 6, ""), (5, 6, "")],
)
MARKS_RESPONSE = flowchart_json(
    [("start", "Start"), ("input", "Input marks"), ("decision", "marks >= 40?"),
     ("output", "Pass"), ("output", "Fail"), ("end", "End")],
    [(1, 2, ""), (2, 3, ""), (3, 4, "Yes"), (3, 5, "No"), (4, 6, ""), (5, 6, "")],
)
SUM_LOOP_RESPONSE = flowchart_json(
    [("start", "Start"), ("input", "Input n"), ("process", "sum = 0"), ("process", "i = 1"),
     ("decision", "i <= n?"), ("process", "sum = sum + i"), ("process", "i = i + 1"),
     ("output", "Print sum"), ("end", "End")],
    [(1, 2, ""), (2, 3, ""), (3, 4, ""), (4, 5, ""), (5, 6, "Yes"), (5, 8, "No"),
     (6, 7, ""), (7, 5, ""), (8, 9, "")],
)
UML_RESPONSE = json.dumps({
    "diagram_type": "uml",
    "classes": [
        {"name": "Animal", "stereotype": "abstract", "attributes": ["- name: String"], "methods": ["+ speak(): void"]},
        {"name": "Dog", "attributes": ["- breed: String"], "methods": ["+ fetch(item: Ball): Ball"]},
        {"name": "Ball", "attributes": ["radius: double"], "methods": []},
    ],
    "relationships": [{"from": "Dog", "to": "Animal", "type": "inheritance"}],
})
ARCH_RESPONSE = json.dumps({
    "diagram_type": "architecture",
    "components": [{"name": "Web Client", "type": "client"}, {"name": "API Gateway", "type": "gateway"},
                   {"name": "Users DB", "type": "database"}],
    "connections": [{"from": "Web Client", "to": "API Gateway", "label": "HTTPS"},
                    {"from": "API Gateway", "to": "Users DB", "label": "SQL"}],
})


def png_bytes(size=(200, 200), color="white", fmt="PNG", mode="RGB") -> bytes:
    buf = io.BytesIO()
    Image.new(mode, size, color).save(buf, format=fmt)
    return buf.getvalue()


def sample_bytes(name="even_odd.png") -> bytes:
    return (SAMPLES / name).read_bytes()


class FakeClient:
    """Stands in for OllamaClient in unit tests. Counts every vision request it receives."""

    def __init__(self, response=EVEN_ODD_RESPONSE, ready_error: Exception | None = None):
        self.response = response
        self.ready_error = ready_error
        self.vision_calls = 0
        self.images: list[bytes] = []
        self.prompts: list[str] = []

    def ensure_ready(self):
        if self.ready_error:
            raise self.ready_error

    def status(self):
        return {"reachable": True, "model_available": True, "model": "qwen2.5vl:3b",
                "base_url": "http://localhost:11434", "installed_models": ["qwen2.5vl:3b"], "error": None}

    def analyze_image(self, image_png: bytes, prompt: str) -> str:
        self.vision_calls += 1
        self.images.append(image_png)
        self.prompts.append(prompt)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


class TempDirCase:
    """Mixin: gives each test a temp directory."""

    def setUp(self):  # noqa: N802
        self.tmp = tempfile.mkdtemp(prefix="syntaxai_test_")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
