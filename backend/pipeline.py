"""The end-to-end pipeline.

    validate image -> check Ollama -> ONE vision request -> parse/validate -> generate locally
    -> heuristic security scan -> verify -> save history

Exactly one vision request is made per diagram. Everything after it is deterministic local code.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Callable

from . import database, security, verifier, vision
from .config import Settings, get_settings
from .errors import GenerationError, SyntaxAIError
from .generator import LANGUAGE_LABELS, generate_code, normalize_language
from .image_validation import prepare_for_model, validate_image
from .parser import parse_diagram, summarize_ir

log = logging.getLogger("syntaxai")

Progress = Callable[[str, str], None]


def run_pipeline(
    image_bytes: bytes,
    filename: str | None,
    content_type: str | None,
    language: str,
    requirement: str,
    client,
    settings: Settings | None = None,
    progress: Progress | None = None,
) -> dict:
    settings = settings or get_settings()
    report = progress or (lambda stage, message: None)
    timings: dict[str, float] = {}
    t_all = time.perf_counter()

    def timed(name: str, t0: float):
        timings[name] = round(time.perf_counter() - t0, 3)

    try:
        # 1. validate input --------------------------------------------------
        report("validating", "Validating image…")
        language = normalize_language(language)
        requirement = (requirement or "").strip()[:500]
        info = validate_image(image_bytes, filename, content_type, settings.max_image_mb)
        png = prepare_for_model(image_bytes, settings.max_image_side)

        # 2. is the AI available? (GET only, not a vision request) -------------
        report("checking_ai", "Checking Ollama and the model…")
        client.ensure_ready()

        # 3. the ONE vision request ---------------------------------------------
        report("vision", f"Reading the diagram with {settings.ai_model} (this can take a while on CPU)…")
        calls_before = getattr(client, "vision_calls", 0)
        t0 = time.perf_counter()
        raw = vision.extract_diagram(client, png)
        timed("vision", t0)
        vision_calls = getattr(client, "vision_calls", calls_before + 1) - calls_before

        # 4. validate the model output --------------------------------------------
        report("parsing", "Validating the extracted logic…")
        ir = parse_diagram(raw)

        # 5. generate code locally ----------------------------------------------------
        report("generating", f"Generating {LANGUAGE_LABELS[language]} code…")
        t0 = time.perf_counter()
        gen = generate_code(ir, language, requirement)
        timed("generate", t0)

        # 6. heuristic security scan --------------------------------------------------------
        report("security", "Running heuristic security analysis…")
        sec = security.analyse(gen["code"], language)

        # 7. verification ----------------------------------------------------------------------
        report("verifying", "Verifying the generated code…")
        t0 = time.perf_counter()
        ver = verifier.verify(gen["code"], language, ir, gen["filename"], settings)
        timed("verify", t0)

        logic = {"summary": summarize_ir(ir), "ir": ir}
        warnings = list(gen["warnings"])
        result = {
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "filename": info["filename"],
            "diagram_type": ir["diagram_type"],
            "language": language,
            "language_label": LANGUAGE_LABELS[language],
            "requirement": requirement,
            "extracted_logic": logic,
            "code": gen["code"],
            "code_filename": gen["filename"],
            "security": sec,
            "verification": ver,
            "warnings": warnings,
            "ai": {"model": settings.ai_model, "vision_calls": vision_calls},
        }

        # 8. save history (a failure here must not lose the result) -----------------------------
        report("saving", "Saving to history…")
        try:
            result["id"] = database.save_result(settings.db_path, {
                "timestamp": result["timestamp"], "image_filename": info["filename"],
                "diagram_type": ir["diagram_type"], "language": language, "requirement": requirement,
                "extracted_logic": logic, "generated_code": gen["code"], "code_filename": gen["filename"],
                "security_findings": sec, "verification_status": ver["status"],
                "verification_message": ver["message"], "model": settings.ai_model,
            })
        except Exception:
            log.exception("could not save history")
            result["id"] = None
            result["warnings"].append("The result could not be saved to history.")

        timings["total"] = round(time.perf_counter() - t_all, 3)
        result["timings"] = timings
        report("done", "Done")
        return result
    except SyntaxAIError:
        raise
    except Exception:
        log.exception("unexpected pipeline failure")
        raise GenerationError("Something went wrong while processing the diagram. Please try again.",
                              code="internal_error", status_code=500) from None
