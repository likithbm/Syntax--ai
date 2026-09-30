"""Exception hierarchy. Every error carries a user-safe message (never a stack trace)."""


class SyntaxAIError(Exception):
    code = "internal_error"
    status_code = 500

    def __init__(self, message: str, *, code: str | None = None, status_code: int | None = None):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        if status_code:
            self.status_code = status_code


class OllamaOfflineError(SyntaxAIError):
    code = "ollama_offline"
    status_code = 503


class ModelMissingError(SyntaxAIError):
    code = "model_missing"
    status_code = 503


class OllamaError(SyntaxAIError):
    code = "ollama_error"
    status_code = 502


class ImageError(SyntaxAIError):
    code = "invalid_image"
    status_code = 400


class ModelResponseError(SyntaxAIError):
    """The vision model answered, but not with usable structured data."""

    code = "malformed_model_response"
    status_code = 422


class DiagramInterpretationError(SyntaxAIError):
    code = "diagram_unclear"
    status_code = 422


class UnsupportedDiagramError(SyntaxAIError):
    code = "unsupported_diagram"
    status_code = 422


class GenerationError(SyntaxAIError):
    code = "generation_failed"
    status_code = 422
