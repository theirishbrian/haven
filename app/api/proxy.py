"""Drop-in replacements for the OpenAI and Anthropic chat endpoints.

Every request is scrubbed before it leaves this process and restored before it
goes back to the client. When a client asks for streaming, Haven fetches the
whole reply, restores it, then replays it in streaming format. That costs a
little latency but means a token can never be split across two chunks.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response, StreamingResponse
from starlette.concurrency import run_in_threadpool

from app.core.config import Settings
from app.core.payloads import (
    Scrubber,
    anthropic_sse_events,
    openai_sse_chunks,
    restore_anthropic_response,
    restore_openai_response,
    scrub_anthropic_request,
    scrub_openai_request,
)
from app.core.sanitizer import TokenMap
from app.core.sanitizer import restore as restore_text
from app.database.audit import AuditEvent, AuditLog

log = logging.getLogger("haven.proxy")
router = APIRouter()

OPENAI_PASSTHROUGH_HEADERS = ("openai-organization", "openai-project")
ANTHROPIC_PASSTHROUGH_HEADERS = ("anthropic-version", "anthropic-beta")
PROVIDER_NAMES = {"openai": "OpenAI", "anthropic": "Anthropic"}


def _error(status: int, message: str, provider: str) -> JSONResponse:
    if provider == "anthropic":
        body: dict[str, Any] = {"type": "error", "error": {"type": "haven_error", "message": message}}
    else:
        body = {"error": {"message": message, "type": "haven_error", "code": None}}
    return JSONResponse(body, status_code=status)


def _openai_headers(request: Request, settings: Settings) -> dict[str, str] | None:
    headers = {k: request.headers[k] for k in OPENAI_PASSTHROUGH_HEADERS if k in request.headers}
    if settings.openai_api_key:
        headers["authorization"] = f"Bearer {settings.openai_api_key}"
    elif "authorization" in request.headers:
        headers["authorization"] = request.headers["authorization"]
    else:
        return None
    return headers


def _anthropic_headers(request: Request, settings: Settings) -> dict[str, str] | None:
    headers = {k: request.headers[k] for k in ANTHROPIC_PASSTHROUGH_HEADERS if k in request.headers}
    headers.setdefault("anthropic-version", "2023-06-01")
    if settings.anthropic_api_key:
        headers["x-api-key"] = settings.anthropic_api_key
    elif "x-api-key" in request.headers:
        headers["x-api-key"] = request.headers["x-api-key"]
    elif "authorization" in request.headers:
        headers["authorization"] = request.headers["authorization"]
    else:
        return None
    return headers


async def _record(request: Request, event: AuditEvent) -> None:
    audit: AuditLog | None = getattr(request.app.state, "audit", None)
    if audit is None:
        return
    try:
        await audit.record(event)
    except Exception:  # the audit log must never break a clinician's request
        log.exception("Could not write audit event")


async def _proxy(
    request: Request,
    *,
    provider: str,
    endpoint: str,
    url: str,
    headers: dict[str, str] | None,
    scrub: Callable[[dict[str, Any], Scrubber], dict[str, Any]],
    restore: Callable[[dict[str, Any], TokenMap], dict[str, Any]],
    usage_keys: tuple[str, str],
    stream_response: Callable[[dict[str, Any], dict[str, Any]], AsyncIterator[bytes]],
) -> Response:
    started = time.perf_counter()

    if headers is None:
        return _error(
            401,
            f"No {PROVIDER_NAMES[provider]} API key. Set it in Haven's .env or send it with the request.",
            provider,
        )
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return _error(400, "Request body must be JSON.", provider)
    if not isinstance(body, dict):
        return _error(400, "Request body must be a JSON object.", provider)

    stream = bool(body.get("stream"))
    scrubber = Scrubber(request.app.state.sanitiser)
    # spaCy is CPU-bound, so keep it off the event loop.
    upstream_body = await run_in_threadpool(scrub, body, scrubber)
    upstream_body["stream"] = False
    upstream_body.pop("stream_options", None)

    def event(status: int, data: dict[str, Any] | None = None) -> AuditEvent:
        usage = (data or {}).get("usage") or {}
        return AuditEvent(
            provider=provider,
            endpoint=endpoint,
            model=body.get("model") if isinstance(body.get("model"), str) else None,
            stream=stream,
            status_code=status,
            latency_ms=round((time.perf_counter() - started) * 1000),
            entity_counts=dict(scrubber.counts),
            input_tokens=usage.get(usage_keys[0]),
            output_tokens=usage.get(usage_keys[1]),
        )

    http: httpx.AsyncClient = request.app.state.http
    try:
        upstream = await http.post(url, json=upstream_body, headers=headers)
    except httpx.HTTPError as exc:
        await _record(request, event(502))
        return _error(502, f"Could not reach {PROVIDER_NAMES[provider]}: {type(exc).__name__}", provider)

    if upstream.status_code != 200:
        await _record(request, event(upstream.status_code))
        # Error messages sometimes quote the request, so restore those too.
        return Response(
            content=restore_text(upstream.text, scrubber.token_map),
            status_code=upstream.status_code,
            media_type=upstream.headers.get("content-type", "application/json"),
        )

    try:
        data = upstream.json()
    except json.JSONDecodeError:
        await _record(request, event(502))
        return _error(502, f"{PROVIDER_NAMES[provider]} returned a response Haven could not read.", provider)

    restored = restore(data, scrubber.token_map)
    await _record(request, event(200, data))

    if stream:
        return StreamingResponse(
            stream_response(restored, body),
            media_type="text/event-stream",
            headers={"cache-control": "no-cache"},
        )
    return JSONResponse(restored)


async def _openai_stream(data: dict[str, Any], original: dict[str, Any]) -> AsyncIterator[bytes]:
    include_usage = bool((original.get("stream_options") or {}).get("include_usage"))
    for chunk in openai_sse_chunks(data, include_usage):
        yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode()
    yield b"data: [DONE]\n\n"


async def _anthropic_stream(data: dict[str, Any], original: dict[str, Any]) -> AsyncIterator[bytes]:
    for name, payload in anthropic_sse_events(data):
        yield f"event: {name}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n".encode()


@router.post("/v1/chat/completions")
async def chat_completions(request: Request) -> Response:
    settings: Settings = request.app.state.settings
    return await _proxy(
        request,
        provider="openai",
        endpoint="/v1/chat/completions",
        url=f"{settings.openai_base_url.rstrip('/')}/v1/chat/completions",
        headers=_openai_headers(request, settings),
        scrub=scrub_openai_request,
        restore=restore_openai_response,
        usage_keys=("prompt_tokens", "completion_tokens"),
        stream_response=_openai_stream,
    )


@router.post("/v1/messages")
async def messages(request: Request) -> Response:
    settings: Settings = request.app.state.settings
    return await _proxy(
        request,
        provider="anthropic",
        endpoint="/v1/messages",
        url=f"{settings.anthropic_base_url.rstrip('/')}/v1/messages",
        headers=_anthropic_headers(request, settings),
        scrub=scrub_anthropic_request,
        restore=restore_anthropic_response,
        usage_keys=("input_tokens", "output_tokens"),
        stream_response=_anthropic_stream,
    )


@router.get("/haven/audit")
async def audit_recent(request: Request, limit: int = 100) -> JSONResponse:
    audit: AuditLog = request.app.state.audit
    rows = await audit.rows(limit=max(1, min(limit, 1000)))
    for row in rows:
        row["entity_counts"] = json.loads(str(row["entity_counts"]))
    return JSONResponse(rows)


@router.get("/haven/audit.csv")
async def audit_csv(request: Request) -> PlainTextResponse:
    audit: AuditLog = request.app.state.audit
    return PlainTextResponse(
        await audit.to_csv(),
        media_type="text/csv",
        headers={"content-disposition": 'attachment; filename="haven-audit.csv"'},
    )
