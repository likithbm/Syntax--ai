"""Thin client for Ollama's local HTTP API (no third-party AI services)."""

from __future__ import annotations

import base64

import httpx

from .config import Settings, get_settings
from .errors import (
    ModelMissingError,
    ModelResponseError,
    OllamaError,
    OllamaOfflineError,
)


class OllamaClient:
    def __init__(
        self,
        settings: Settings | None = None,
        transport: httpx.BaseTransport | None = None,
    ):
        self.settings = settings or get_settings()
        self._transport = transport
        self.vision_calls = 0

    # -- helpers ---------------------------------------------------------

    @property
    def base_url(self) -> str:
        return self.settings.ai_base_url

    @property
    def model(self) -> str:
        return self.settings.ai_model

    def _http(self, timeout: float) -> httpx.Client:
        return httpx.Client(
            base_url=self.base_url,
            timeout=timeout,
            transport=self._transport,
        )

    def _model_matches(self, wanted: str, installed: str) -> bool:
        if wanted == installed:
            return True

        if ":" not in wanted:
            return (
                installed == f"{wanted}:latest"
                or installed.split(":")[0] == wanted
            )

        return False

    # -- status ----------------------------------------------------------

    def status(self) -> dict:
        info = {
            "reachable": False,
            "model_available": False,
            "model": self.model,
            "base_url": self.base_url,
            "installed_models": [],
            "error": None,
        }

        try:
            with self._http(timeout=3.0) as http:
                resp = http.get("/api/tags")

            resp.raise_for_status()

            info["reachable"] = True

            names = [
                m.get("name", "")
                for m in resp.json().get("models", [])
                if isinstance(m, dict)
            ]

            info["installed_models"] = names

            info["model_available"] = any(
                self._model_matches(self.model, name)
                for name in names
            )

            if not info["model_available"]:
                info["error"] = (
                    f"Model '{self.model}' is not installed. "
                    f"Run: ollama pull {self.model}"
                )

        except httpx.HTTPError:
            info["error"] = (
                f"Ollama is not reachable at {self.base_url}."
            )

        except ValueError:
            info["reachable"] = True
            info["error"] = (
                "Ollama returned an unexpected response."
            )

        return info

    # -- readiness -------------------------------------------------------

    def ensure_ready(self) -> None:
        st = self.status()

        if not st["reachable"]:
            raise OllamaOfflineError(
                f"AI Offline: Ollama is not reachable at "
                f"{self.base_url}. "
                "Start Ollama (open the Ollama app or run "
                "'ollama serve') and try again."
            )

        if not st["model_available"]:
            raise ModelMissingError(
                f"The model '{self.model}' is not installed in Ollama. "
                f"Run 'ollama pull {self.model}' and try again."
            )

    # -- single vision request ------------------------------------------

    def analyze_image(self, image_png: bytes, prompt: str) -> str:
        """
        Send the image to the vision model exactly once
        and return the raw text response.
        """

        payload = {
            "model": self.model,
            "stream": False,
            "format": "json",
            "keep_alive": "10m",
            "options": {
                "temperature": 0,
                "num_ctx": 3072,
                "num_predict": 512,
            },
            "messages": [
                {
                    "role": "user",
                    "content": prompt,
                    "images": [
                        base64.b64encode(image_png).decode("ascii")
                    ],
                }
            ],
        }

        self.vision_calls += 1

        try:
            with self._http(
                timeout=self.settings.vision_timeout
            ) as http:
                resp = http.post(
                    "/api/chat",
                    json=payload,
                )

        except httpx.ConnectError:
            raise OllamaOfflineError(
                f"AI Offline: Ollama is not reachable at "
                f"{self.base_url}. Start Ollama and try again."
            ) from None

        except httpx.TimeoutException:
            raise OllamaError(
                f"The vision model did not answer within "
                f"{self.settings.vision_timeout:g} seconds. "
                "Try a smaller/clearer image or increase "
                "VISION_TIMEOUT."
            ) from None

        except httpx.HTTPError:
            raise OllamaError(
                "Communication with Ollama failed. "
                "Please check that it is running."
            ) from None

        # Model not installed
        if resp.status_code == 404:
            raise ModelMissingError(
                f"The model '{self.model}' is not installed "
                f"in Ollama. Run 'ollama pull {self.model}'."
            )

        # Other Ollama errors
        if resp.status_code != 200:
            detail = ""

            try:
                detail = str(
                    resp.json().get("error", "")
                )[:200]
            except Exception:
                pass

            message = (
                f"Ollama returned an error "
                f"(HTTP {resp.status_code})."
            )

            if detail:
                message += f" {detail}"

            raise OllamaError(message)

        # Read response
        try:
            content = resp.json()["message"]["content"]

        except (
            ValueError,
            KeyError,
            TypeError,
        ):
            raise ModelResponseError(
                "Ollama returned a response in an unexpected format."
            ) from None

        if not isinstance(content, str) or not content.strip():
            raise ModelResponseError(
                "The vision model returned an empty response. "
                "Please try again."
            )

        return content