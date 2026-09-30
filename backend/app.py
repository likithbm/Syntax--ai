"""FastAPI application: routes only. All real work lives in the other modules."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__, database
from .config import FRONTEND_DIR, SAMPLES_DIR, Settings, get_settings
from .errors import SyntaxAIError
from .generator import LANGUAGE_LABELS, SUPPORTED_LANGUAGES
from .jobs import JobStore, run_job
from .ollama_client import OllamaClient
from .pipeline import run_pipeline

log = logging.getLogger("syntaxai")


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


def create_app(client=None, settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    client = client or OllamaClient(settings)
    store = JobStore()
    executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="syntaxai")

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        database.init_db(settings.db_path)
        yield
        executor.shutdown(wait=False, cancel_futures=True)

    app = FastAPI(title="Syntax AI", version=__version__, lifespan=lifespan)

    # ---- error handling: never leak stack traces ---------------------------------
    @app.exception_handler(SyntaxAIError)
    async def _syntaxai_error(_request: Request, exc: SyntaxAIError):
        return _error(exc.status_code, exc.code, exc.message)

    @app.exception_handler(Exception)
    async def _unexpected(_request: Request, exc: Exception):
        log.exception("unhandled error", exc_info=exc)
        return _error(500, "internal_error", "An unexpected error occurred. Please try again.")

    # ---- API ------------------------------------------------------------------------
    @app.get("/api/health")
    def health():
        return {"status": "ok", "app": "Syntax AI", "version": __version__}

    @app.get("/api/ai-status")
    def ai_status():
        st = client.status()
        if st["reachable"] and st["model_available"]:
            st["label"] = f"AI Ready · {st['model']}"
            st["state"] = "ready"
        elif st["reachable"]:
            st["label"] = f"Model missing · {st['model']}"
            st["state"] = "model_missing"
        else:
            st["label"] = "AI Offline"
            st["state"] = "offline"
        return st

    @app.get("/api/languages")
    def languages():
        return [{"id": k, "label": LANGUAGE_LABELS[k]} for k in SUPPORTED_LANGUAGES]

    @app.post("/api/generate", status_code=202)
    async def generate(
        image: UploadFile = File(...),
        language: str = Form("python"),
        requirement: str = Form(""),
    ):
        limit = int(settings.max_image_mb * 1024 * 1024)
        data = await image.read(limit + 1)
        if len(data) > limit:
            return _error(413, "invalid_image", f"The image is too large. Maximum size is {settings.max_image_mb:g} MB.")
        filename, content_type = image.filename, image.content_type
        job_id = store.create()

        def work(progress):
            return run_pipeline(data, filename, content_type, language, requirement, client, settings, progress)

        executor.submit(run_job, store, job_id, work)
        return {"job_id": job_id}

    @app.get("/api/jobs/{job_id}")
    def job_status(job_id: str):
        job = store.get(job_id)
        if job is None:
            return _error(404, "not_found", "Unknown or expired job.")
        return {"id": job["id"], "status": job["status"], "stage": job["stage"], "message": job["message"],
                "elapsed": job["elapsed"], "result": job["result"], "error": job["error"]}

    @app.get("/api/history")
    def history(limit: int = 50):
        return database.list_history(settings.db_path, limit)

    @app.get("/api/history/{item_id}")
    def history_item(item_id: int):
        item = database.get_history(settings.db_path, item_id)
        if item is None:
            return _error(404, "not_found", "History entry not found.")
        return item

    @app.delete("/api/history/{item_id}")
    def history_delete(item_id: int):
        if not database.delete_history(settings.db_path, item_id):
            return _error(404, "not_found", "History entry not found.")
        return {"deleted": item_id}

    @app.delete("/api/history")
    def history_clear():
        return {"deleted": database.clear_history(settings.db_path)}

    # ---- static files (must be mounted last) -------------------------------------------
    if SAMPLES_DIR.exists():
        app.mount("/samples", StaticFiles(directory=str(SAMPLES_DIR)), name="samples")
    if FRONTEND_DIR.exists():
        app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
    return app


app = create_app()
