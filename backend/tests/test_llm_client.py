"""Deterministic contract tests for the vision adapter (no paid API)."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from openai import APIStatusError, APITimeoutError, OpenAIError
from pydantic import SecretStr

from app.config import Settings
from app.llm.client import analyze_image
from app.llm.errors import (
    LlmConfigurationError,
    LlmInvalidOutputError,
    LlmRequestError,
)
from app.llm.schemas import ImageInput


def _settings(**overrides: Any) -> Settings:
    base = {
        "database_url": "sqlite:///:memory:",
        "llm_base_url": "https://example.test/v1",
        "llm_model": "test-vision-model",
        "llm_api_key": SecretStr("test-key"),
        "llm_timeout_seconds": 30.0,
    }
    base.update(overrides)
    return Settings(**base)


def _image() -> ImageInput:
    # Minimal PNG header bytes are enough for contract tests.
    return ImageInput(media_type="image/png", data=b"\x89PNG\r\n\x1a\nfake-bytes")


class _FakeCompletions:
    def __init__(self, handler):
        self._handler = handler
        self.last_kwargs: dict[str, Any] | None = None

    def create(self, **kwargs):
        self.last_kwargs = kwargs
        return self._handler(kwargs)


class _FakeChat:
    def __init__(self, handler):
        self.completions = _FakeCompletions(handler)


class _FakeClient:
    def __init__(self, handler):
        self.chat = _FakeChat(handler)


def _ok_response(payload: dict[str, Any], response_id: str = "req_test_1") -> SimpleNamespace:
    return SimpleNamespace(
        id=response_id,
        choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))],
    )


def test_analyze_image_sends_image_and_json_schema():
    captured: dict[str, Any] = {}

    def handler(kwargs):
        captured.update(kwargs)
        return _ok_response(
            {
                "summary": "红色与蓝色色块",
                "visible_texts": ["A42"],
                "primary_color": "red",
                "object_count": 2,
                "confidence": 0.9,
            }
        )

    outcome = analyze_image(
        _image(),
        prompt="描述图像",
        settings=_settings(),
        client=_FakeClient(handler),
    )

    assert outcome.request_id == "req_test_1"
    assert outcome.result.visible_texts == ["A42"]
    assert outcome.result.object_count == 2

    assert captured["model"] == "test-vision-model"
    user_content = captured["messages"][1]["content"]
    assert user_content[0]["type"] == "text"
    assert user_content[1]["type"] == "image_url"
    assert user_content[1]["image_url"]["url"].startswith("data:image/png;base64,")

    response_format = captured["response_format"]
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["name"] == "image_analysis_result"
    assert "primary_color" in response_format["json_schema"]["schema"]["properties"]


def test_analyze_image_rejects_missing_config():
    with pytest.raises(LlmConfigurationError) as exc_info:
        analyze_image(
            _image(),
            prompt="描述图像",
            settings=_settings(llm_api_key=SecretStr(""), llm_base_url="", llm_model=""),
            client=_FakeClient(lambda _kwargs: None),
        )
    assert exc_info.value.code == "llm_not_configured"


def test_analyze_image_invalid_structure():
    def handler(_kwargs):
        return _ok_response({"summary": "缺少字段"})

    with pytest.raises(LlmInvalidOutputError) as exc_info:
        analyze_image(
            _image(),
            prompt="描述图像",
            settings=_settings(),
            client=_FakeClient(handler),
        )
    assert exc_info.value.code == "llm_schema_validation_failed"


def test_analyze_image_invalid_json():
    def handler(_kwargs):
        return SimpleNamespace(
            id="req_bad_json",
            choices=[SimpleNamespace(message=SimpleNamespace(content="not-json"))],
        )

    with pytest.raises(LlmInvalidOutputError) as exc_info:
        analyze_image(
            _image(),
            prompt="描述图像",
            settings=_settings(),
            client=_FakeClient(handler),
        )
    assert exc_info.value.code == "llm_invalid_json"


def test_analyze_image_upstream_auth_error():
    request = httpx.Request("POST", "https://example.test/v1/chat/completions")
    response = httpx.Response(401, request=request)

    def handler(_kwargs):
        raise APIStatusError("unauthorized", response=response, body=None)

    with pytest.raises(LlmRequestError) as exc_info:
        analyze_image(
            _image(),
            prompt="描述图像",
            settings=_settings(),
            client=_FakeClient(handler),
        )
    assert exc_info.value.code == "llm_auth_failed"


def test_analyze_image_timeout():
    request = httpx.Request("POST", "https://example.test/v1/chat/completions")

    def handler(_kwargs):
        raise APITimeoutError(request=request)

    with pytest.raises(LlmRequestError) as exc_info:
        analyze_image(
            _image(),
            prompt="描述图像",
            settings=_settings(),
            client=_FakeClient(handler),
        )
    assert exc_info.value.code == "llm_timeout"


def test_analyze_image_generic_openai_error():
    def handler(_kwargs):
        raise OpenAIError("boom")

    with pytest.raises(LlmRequestError) as exc_info:
        analyze_image(
            _image(),
            prompt="描述图像",
            settings=_settings(),
            client=_FakeClient(handler),
        )
    assert exc_info.value.code == "llm_request_failed"


def test_analyze_image_accepts_single_element_array_wrapper():
    def handler(_kwargs):
        payload = [
            {
                "summary": "包装数组",
                "visible_texts": [],
                "primary_color": "blue",
                "object_count": 1,
                "confidence": 0.5,
            }
        ]
        return SimpleNamespace(
            id="req_wrap",
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))],
        )

    outcome = analyze_image(
        _image(),
        prompt="描述图像",
        settings=_settings(),
        client=_FakeClient(handler),
    )
    assert outcome.result.primary_color == "blue"
    assert outcome.request_id == "req_wrap"
