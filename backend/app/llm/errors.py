"""Desensitized LLM errors for application use."""

from __future__ import annotations


class LlmError(RuntimeError):
    """Base error for the multimodal adapter."""

    def __init__(self, message: str, *, code: str, request_id: str | None = None):
        super().__init__(message)
        self.code = code
        self.request_id = request_id


class LlmConfigurationError(LlmError):
    """Raised when base URL / model / API key are missing."""


class LlmRequestError(LlmError):
    """Raised on timeout, auth failure, or upstream HTTP errors."""


class LlmInvalidOutputError(LlmError):
    """Raised when the provider response fails local schema validation."""
