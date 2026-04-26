from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


class LlmError(RuntimeError):
    pass


@dataclass(frozen=True)
class OpenAICompatConfig:
    base_url: str
    api_key: str | None
    model: str
    model_fallbacks: list[str]
    context_tokens: int | None


def _credgoo_api_key(service: str) -> str | None:
    try:
        from credgoo import get_api_key  # type: ignore
    except Exception:
        return None
    try:
        key = get_api_key(service)
    except Exception as e:
        raise LlmError(f"credgoo failed to fetch api key for service={service!r}: {e}") from e
    if isinstance(key, str) and key:
        return key
    return None


def default_openai_compat_config(
    *,
    base_url: str | None,
    model: str | None,
    model_fallbacks: list[str] | None,
    api_key: str | None,
    credgoo_service: str | None = None,
    context_tokens: int | None = None,
) -> OpenAICompatConfig:
    resolved_base_url = (base_url or os.environ.get("OPENAI_BASE_URL") or "https://amd1.mooo.com:8123/v1").strip().strip("`").rstrip("/")
    if model:
        resolved_model = model
    else:
        env_model = os.environ.get("OPENAI_MODEL")
        if env_model:
            resolved_model = env_model
        elif "amd1.mooo.com:8123/v1" in resolved_base_url:
            resolved_model = "tu@qwen-coder-30b"
        else:
            resolved_model = "openai@gpt-4o-mini"
    resolved_key = api_key or os.environ.get("OPENAI_API_KEY")
    if not resolved_key:
        service = credgoo_service or os.environ.get("GHSORTER_CREDGOO_KEY") or "amd1"
        resolved_key = _credgoo_api_key(service)
    resolved_context = context_tokens
    if resolved_context is None:
        env = os.environ.get("OPENAI_CONTEXT_TOKENS") or os.environ.get("GHSORTER_CONTEXT_TOKENS")
        if env:
            try:
                resolved_context = int(env)
            except ValueError:
                resolved_context = None
        elif resolved_model == "tu@qwen-coder-30b":
            resolved_context = 16384
    resolved_fallbacks = model_fallbacks or []
    if resolved_model == "tu@qwen-coder-30b":
        resolved_fallbacks = []
    return OpenAICompatConfig(
        base_url=resolved_base_url,
        api_key=resolved_key,
        model=resolved_model,
        model_fallbacks=resolved_fallbacks,
        context_tokens=resolved_context,
    )


def _post_json(url: str, payload: dict[str, Any], *, headers: dict[str, str]) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    for k, v in headers.items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            body = ""
        detail = f"{e}"
        if body:
            detail = f"{detail}. Body: {body[:2000]}"
        raise LlmError(f"LLM request failed: {detail}") from e
    except Exception as e:
        raise LlmError(f"LLM request failed: {e}") from e
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise LlmError("LLM returned non-JSON response") from e


def chat_completions_json(
    cfg: OpenAICompatConfig,
    *,
    system: str,
    user: str,
    response_schema_hint: str,
    temperature: float = 0.2,
    max_tokens: int | None = None,
) -> dict[str, Any]:
    headers = {"Content-Type": "application/json"}
    if cfg.api_key:
        headers["Authorization"] = f"Bearer {cfg.api_key}"

    models = [cfg.model, *cfg.model_fallbacks]
    last_err: Exception | None = None
    for model in models:
        payload: dict[str, Any] = {
            "model": model,
            "temperature": temperature,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if max_tokens is not None:
            payload["max_tokens"] = int(max_tokens)
        url = f"{cfg.base_url}/chat/completions"
        try:
            res = _post_json(url, payload, headers=headers)
        except Exception as e:
            last_err = e
            continue

        content = (
            (((res.get("choices") or [{}])[0].get("message") or {}).get("content"))
            if isinstance(res, dict)
            else None
        )
        if not content or not isinstance(content, str):
            last_err = LlmError("LLM returned empty content")
            continue

        trimmed = content.strip()
        if trimmed.startswith("```"):
            trimmed = trimmed.strip("`")
            trimmed = trimmed.split("\n", 1)[-1]
        try:
            return json.loads(trimmed)
        except json.JSONDecodeError as e:
            last_err = LlmError(f"Failed to parse LLM JSON. Expected: {response_schema_hint}. Raw: {content[:4000]}")
            continue

    raise LlmError(str(last_err) if last_err else "LLM request failed")


def embeddings_vectors(cfg: OpenAICompatConfig, *, model: str, inputs: list[str]) -> list[list[float]]:
    headers = {"Content-Type": "application/json"}
    if cfg.api_key:
        headers["Authorization"] = f"Bearer {cfg.api_key}"

    payload: dict[str, Any] = {"model": model, "input": inputs}
    url = f"{cfg.base_url}/embeddings"
    res = _post_json(url, payload, headers=headers)
    data = res.get("data") if isinstance(res, dict) else None
    if not isinstance(data, list):
        raise LlmError("Embeddings response missing data")
    out: list[tuple[int, list[float]]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        idx = item.get("index")
        emb = item.get("embedding")
        if not isinstance(idx, int) or not isinstance(emb, list):
            continue
        vec = [float(x) for x in emb if isinstance(x, (int, float))]
        out.append((idx, vec))
    out.sort(key=lambda x: x[0])
    vecs = [v for _, v in out]
    if len(vecs) != len(inputs):
        raise LlmError(f"Embeddings response count mismatch: got {len(vecs)} expected {len(inputs)}")
    return vecs
