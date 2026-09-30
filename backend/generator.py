"""Local code generation from the validated intermediate representation. No AI calls."""

from __future__ import annotations

from .errors import GenerationError
from .flow import build_program, render_flowchart
from .scaffold import render_architecture, render_uml

SUPPORTED_LANGUAGES = ["python", "c", "cpp", "java", "javascript"]
LANGUAGE_LABELS = {"python": "Python", "c": "C", "cpp": "C++", "java": "Java", "javascript": "JavaScript"}
_ALIASES = {"py": "python", "python3": "python", "c++": "cpp", "cxx": "cpp", "js": "javascript",
            "node": "javascript", "nodejs": "javascript"}
_FLOW_FILES = {"python": "main.py", "c": "main.c", "cpp": "main.cpp", "java": "Main.java", "javascript": "main.js"}
_OTHER_FILES = {"python": "diagram.py", "c": "diagram.c", "cpp": "diagram.cpp", "java": "Diagram.java",
                "javascript": "diagram.js"}


def normalize_language(language: str | None) -> str:
    key = (language or "python").strip().lower()
    key = _ALIASES.get(key, key)
    if key not in SUPPORTED_LANGUAGES:
        raise GenerationError(
            f"Unsupported language '{language}'. Choose one of: Python, C, C++, Java, JavaScript."
        )
    return key


def generate_code(ir: dict, language: str, requirement: str = "") -> dict:
    """Return {"code", "filename", "language", "warnings"}; raises GenerationError."""
    language = normalize_language(language)
    dtype = ir.get("diagram_type")
    warnings = list(ir.get("warnings", []))
    try:
        if dtype == "flowchart":
            model, stmts = build_program(ir)
            code = render_flowchart(model, stmts, language, requirement)
            warnings += model.warnings
            filename = _FLOW_FILES[language]
        elif dtype == "uml":
            code = render_uml(ir, language, requirement)
            filename = _OTHER_FILES[language]
        elif dtype == "architecture":
            code = render_architecture(ir, language, requirement)
            filename = "Main.java" if language == "java" else _OTHER_FILES[language]
        else:
            raise GenerationError("Unsupported diagram type for code generation.")
    except GenerationError:
        raise
    except Exception:  # never leak internals
        raise GenerationError("Code generation failed for this diagram. Please try a clearer image.") from None
    return {"code": code, "filename": filename, "language": language, "warnings": warnings}
