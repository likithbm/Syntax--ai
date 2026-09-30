"""Configuration. Read from environment variables (and an optional .env file)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:
    pass


PROJECT_ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIR = PROJECT_ROOT / "frontend"
SAMPLES_DIR = PROJECT_ROOT / "samples"


@dataclass(frozen=True)
class Settings:
    ai_base_url: str
    ai_model: str
    vision_timeout: float
    db_path: Path
    max_image_mb: float
    max_image_side: int
    compile_timeout: float
    run_timeout: float


def get_settings() -> Settings:
    """Read settings fresh each call."""

    def _float(name: str, default: float) -> float:
        try:
            return float(os.getenv(name, default))
        except (ValueError, TypeError):
            return default

    db = os.getenv("DB_PATH")

    return Settings(
        ai_base_url=os.getenv(
            "AI_BASE_URL",
            "http://localhost:11434"
        ).rstrip("/"),

        ai_model=os.getenv(
            "AI_MODEL",
            "qwen2.5vl:3b"
        ),

        # Maximum time allowed for the vision model
        vision_timeout=_float(
            "VISION_TIMEOUT",
            300
        ),

        # SQLite database
        db_path=(
            Path(db)
            if db
            else PROJECT_ROOT / "data" / "syntax_ai.db"
        ),

        # Upload limit
        max_image_mb=_float(
            "MAX_IMAGE_MB",
            8
        ),

        # Smaller image = less CPU work for Qwen vision
        max_image_side=int(
            _float(
                "MAX_IMAGE_SIDE",
                768
            )
        ),

        # Code compilation timeout
        compile_timeout=_float(
            "COMPILE_TIMEOUT",
            30
        ),

        # Generated-code execution timeout
        run_timeout=_float(
            "RUN_TIMEOUT",
            5
        ),
    )