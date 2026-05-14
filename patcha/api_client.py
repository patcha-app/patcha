from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx


class _EmbeddingData:
    __slots__ = ("embedding",)

    def __init__(self, embedding: list[float]) -> None:
        self.embedding = embedding


class _EmbeddingResponse:
    __slots__ = ("data",)

    def __init__(self, data: list[_EmbeddingData]) -> None:
        self.data = data


class _Message:
    __slots__ = ("content",)

    def __init__(self, content: str) -> None:
        self.content = content


class _Choice:
    __slots__ = ("message",)

    def __init__(self, message: _Message) -> None:
        self.message = message


class _ChatResponse:
    __slots__ = ("choices",)

    def __init__(self, choices: list[_Choice]) -> None:
        self.choices = choices


class _Embeddings:
    def __init__(self, api_client: "PatchaAPIClient") -> None:
        self._client = api_client

    def create(self, *, model: str, input: str | list[str]) -> _EmbeddingResponse:
        resp = self._client.proxy_embeddings({"model": model, "input": input})
        if resp is None:
            raise RuntimeError("embeddings request failed: no response")
        if resp.status_code != 200:
            raise RuntimeError(f"embeddings request failed: HTTP {resp.status_code}")
        items = resp.json()["data"]
        return _EmbeddingResponse([_EmbeddingData(item["embedding"]) for item in items])


class _ChatCompletions:
    def __init__(self, api_client: "PatchaAPIClient") -> None:
        self._client = api_client

    def create(
        self, *, model: str, messages: list[dict], **kwargs: Any
    ) -> _ChatResponse:
        payload: dict[str, Any] = {"model": model, "messages": messages, **kwargs}
        resp = self._client.proxy_chat_completion(payload)
        if resp is None:
            raise RuntimeError("chat completion request failed: no response")
        if resp.status_code != 200:
            raise RuntimeError(
                f"chat completion request failed: HTTP {resp.status_code}"
            )
        choices = resp.json()["choices"]
        return _ChatResponse(
            [_Choice(_Message(c["message"]["content"])) for c in choices]
        )


class _Chat:
    def __init__(self, api_client: "PatchaAPIClient") -> None:
        self.completions = _ChatCompletions(api_client)


class PatchaLLMClient:
    """Drop-in replacement for openai.OpenAI that routes through patcha-api."""

    def __init__(self) -> None:
        self._api_client = PatchaAPIClient()
        self.embeddings = _Embeddings(self._api_client)
        self.chat = _Chat(self._api_client)


_ENV_PATH = Path.home() / ".patcha" / ".env"


def _save_tokens(access_token: str, refresh_token: str) -> None:
    lines: list[str] = []
    if _ENV_PATH.exists():
        lines = _ENV_PATH.read_text().splitlines()

    updated = {line.split("=", 1)[0]: line for line in lines if "=" in line}
    updated["PATCHA_ACCESS_TOKEN"] = f"PATCHA_ACCESS_TOKEN={access_token}"
    updated["PATCHA_REFRESH_TOKEN"] = f"PATCHA_REFRESH_TOKEN={refresh_token}"

    _ENV_PATH.parent.mkdir(parents=True, exist_ok=True)
    _ENV_PATH.write_text("\n".join(updated.values()) + "\n")


def _clear_tokens() -> None:
    if not _ENV_PATH.exists():
        return
    lines = [
        line
        for line in _ENV_PATH.read_text().splitlines()
        if not line.startswith("PATCHA_ACCESS_TOKEN=")
        and not line.startswith("PATCHA_REFRESH_TOKEN=")
    ]
    _ENV_PATH.write_text("\n".join(lines) + "\n")


class PatchaAPIClient:
    def __init__(self) -> None:
        from patcha.config import config as patcha_config

        self._base_url = patcha_config.patcha_api_url.rstrip("/")
        self._access_token = patcha_config.patcha_access_token or ""
        self._refresh_token = patcha_config.patcha_refresh_token or ""

    def _auth_headers(self) -> dict[str, str]:
        if self._access_token:
            return {"Authorization": f"Bearer {self._access_token}"}
        return {}

    @property
    def is_authenticated(self) -> bool:
        return bool(self._access_token)

    def login(self, email: str, password: str) -> bool:
        with httpx.Client(base_url=self._base_url, timeout=30.0) as client:
            resp = client.post(
                "/api/v1/auth/login", json={"email": email, "password": password}
            )
            if resp.status_code != 200:
                return False
            data = resp.json()
            self._access_token = data["access_token"]
            self._refresh_token = data["refresh_token"]
            _save_tokens(self._access_token, self._refresh_token)
            return True

    def register(self, email: str, password: str) -> bool:
        with httpx.Client(base_url=self._base_url, timeout=30.0) as client:
            resp = client.post(
                "/api/v1/auth/register", json={"email": email, "password": password}
            )
            if resp.status_code not in (200, 201):
                return False
            data = resp.json()
            self._access_token = data["access_token"]
            self._refresh_token = data["refresh_token"]
            _save_tokens(self._access_token, self._refresh_token)
            return True

    def logout(self) -> None:
        if self._refresh_token:
            try:
                with httpx.Client(base_url=self._base_url, timeout=10.0) as client:
                    client.post(
                        "/api/v1/auth/logout",
                        json={"refresh_token": self._refresh_token},
                        headers=self._auth_headers(),
                    )
            except httpx.RequestError:
                pass
        _clear_tokens()
        self._access_token = ""
        self._refresh_token = ""

    def refresh_tokens(self) -> bool:
        if not self._refresh_token:
            return False
        try:
            with httpx.Client(base_url=self._base_url, timeout=10.0) as client:
                resp = client.post(
                    "/api/v1/auth/refresh",
                    json={"refresh_token": self._refresh_token},
                )
                if resp.status_code != 200:
                    return False
                data = resp.json()
                self._access_token = data["access_token"]
                self._refresh_token = data["refresh_token"]
                _save_tokens(self._access_token, self._refresh_token)
                return True
        except httpx.RequestError:
            return False

    def check_update(self, current_version: str) -> dict[str, Any] | None:
        try:
            with httpx.Client(base_url=self._base_url, timeout=10.0) as client:
                resp = client.get(f"/api/v1/updates/check?version={current_version}")
                if resp.status_code == 200:
                    return resp.json()
        except httpx.RequestError:
            pass
        return None

    def list_models(self) -> list[dict[str, Any]]:
        try:
            with httpx.Client(base_url=self._base_url, timeout=10.0) as client:
                resp = client.get("/api/v1/models/")
                if resp.status_code == 200:
                    return resp.json()
        except httpx.RequestError:
            pass
        return []

    def get_model_download_url(self, model_id: str) -> str | None:
        try:
            with httpx.Client(base_url=self._base_url, timeout=10.0) as client:
                resp = client.post(
                    f"/api/v1/models/{model_id}/download-url",
                    headers=self._auth_headers(),
                )
                if resp.status_code == 200:
                    return resp.json().get("url")
        except httpx.RequestError:
            pass
        return None

    def proxy_chat_completion(self, payload: dict[str, Any]) -> httpx.Response | None:
        try:
            with httpx.Client(base_url=self._base_url, timeout=120.0) as client:
                return client.post(
                    "/api/v1/llm/chat/completions",
                    json=payload,
                    headers=self._auth_headers(),
                )
        except httpx.RequestError:
            return None

    def proxy_embeddings(self, payload: dict[str, Any]) -> httpx.Response | None:
        try:
            with httpx.Client(base_url=self._base_url, timeout=60.0) as client:
                return client.post(
                    "/api/v1/llm/embeddings",
                    json=payload,
                    headers=self._auth_headers(),
                )
        except httpx.RequestError:
            return None
