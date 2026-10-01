"""Walk OpenAI and Anthropic request/response bodies, scrubbing or restoring text.

Only the fields that carry conversation text are touched. Everything else
(model, temperature, tool definitions, images) passes through unchanged.
"""

from __future__ import annotations

import copy
import json
from collections import Counter
from typing import Any

from app.core.sanitizer import Sanitiser, TokenMap, restore


class Scrubber:
    """Scrubs every text field in one request, sharing a single TokenMap."""

    def __init__(self, sanitiser: Sanitiser) -> None:
        self.sanitiser = sanitiser
        self.token_map = TokenMap()
        self.counts: Counter[str] = Counter()

    def text(self, value: str) -> str:
        result = self.sanitiser.scrub(value, self.token_map)
        self.counts.update(result.entity_counts)
        return result.text

    def json_strings(self, value: Any) -> Any:
        """Scrub every string inside a JSON-like value (tool arguments, tool inputs)."""
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, list):
            return [self.json_strings(v) for v in value]
        if isinstance(value, dict):
            return {k: self.json_strings(v) for k, v in value.items()}
        return value

    def json_text(self, value: str) -> str:
        """Scrub a string that holds JSON (OpenAI tool-call arguments)."""
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return self.text(value)
        return json.dumps(self.json_strings(parsed), ensure_ascii=False)


def restore_json_strings(value: Any, token_map: TokenMap) -> Any:
    if isinstance(value, str):
        return restore(value, token_map)
    if isinstance(value, list):
        return [restore_json_strings(v, token_map) for v in value]
    if isinstance(value, dict):
        return {k: restore_json_strings(v, token_map) for k, v in value.items()}
    return value


def restore_json_text(value: str, token_map: TokenMap) -> str:
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return restore(value, token_map)
    return json.dumps(restore_json_strings(parsed, token_map), ensure_ascii=False)


# --- OpenAI /v1/chat/completions -------------------------------------------


def scrub_openai_request(body: dict[str, Any], scrubber: Scrubber) -> dict[str, Any]:
    body = copy.deepcopy(body)
    for message in body.get("messages") or []:
        content = message.get("content")
        if isinstance(content, str):
            message["content"] = scrubber.text(content)
        elif isinstance(content, list):
            for part in content:
                if (
                    isinstance(part, dict)
                    and part.get("type") == "text"
                    and isinstance(part.get("text"), str)
                ):
                    part["text"] = scrubber.text(part["text"])
        for call in message.get("tool_calls") or []:
            fn = call.get("function") or {}
            if isinstance(fn.get("arguments"), str):
                fn["arguments"] = scrubber.json_text(fn["arguments"])
        # "name" on a message is a participant name, which is often a real person.
        if isinstance(message.get("name"), str) and message.get("role") in {"user", "assistant"}:
            message.pop("name")
    return body


def restore_openai_response(body: dict[str, Any], token_map: TokenMap) -> dict[str, Any]:
    body = copy.deepcopy(body)
    for choice in body.get("choices") or []:
        message = choice.get("message") or {}
        if isinstance(message.get("content"), str):
            message["content"] = restore(message["content"], token_map)
        if isinstance(message.get("refusal"), str):
            message["refusal"] = restore(message["refusal"], token_map)
        for call in message.get("tool_calls") or []:
            fn = call.get("function") or {}
            if isinstance(fn.get("arguments"), str):
                fn["arguments"] = restore_json_text(fn["arguments"], token_map)
    return body


def openai_sse_chunks(body: dict[str, Any], include_usage: bool) -> list[dict[str, Any]]:
    """Turn a complete chat.completion into the chunks a streaming client expects."""
    base = {
        "id": body.get("id"),
        "object": "chat.completion.chunk",
        "created": body.get("created"),
        "model": body.get("model"),
        "system_fingerprint": body.get("system_fingerprint"),
    }
    chunks: list[dict[str, Any]] = []
    for choice in body.get("choices") or []:
        index = choice.get("index", 0)
        message = choice.get("message") or {}
        delta: dict[str, Any] = {"role": message.get("role", "assistant")}
        if message.get("content") is not None:
            delta["content"] = message["content"]
        if message.get("refusal") is not None:
            delta["refusal"] = message["refusal"]
        if message.get("tool_calls"):
            delta["tool_calls"] = [{"index": i, **call} for i, call in enumerate(message["tool_calls"])]
        chunks.append({**base, "choices": [{"index": index, "delta": delta, "finish_reason": None}]})
        chunks.append(
            {**base, "choices": [{"index": index, "delta": {}, "finish_reason": choice.get("finish_reason")}]}
        )
    if include_usage:
        chunks.append({**base, "choices": [], "usage": body.get("usage")})
    return chunks


# --- Anthropic /v1/messages ------------------------------------------------


def _scrub_anthropic_blocks(blocks: list[Any], scrubber: Scrubber) -> None:
    for block in blocks:
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if kind == "text" and isinstance(block.get("text"), str):
            block["text"] = scrubber.text(block["text"])
        elif kind == "tool_use" and "input" in block:
            block["input"] = scrubber.json_strings(block["input"])
        elif kind == "tool_result":
            content = block.get("content")
            if isinstance(content, str):
                block["content"] = scrubber.text(content)
            elif isinstance(content, list):
                _scrub_anthropic_blocks(content, scrubber)
        elif kind == "document":
            source = block.get("source") or {}
            if source.get("type") == "text" and isinstance(source.get("data"), str):
                source["data"] = scrubber.text(source["data"])
        # "thinking" and "redacted_thinking" blocks came from the model, which only
        # ever saw tokens. They are signed, so they must go back byte for byte.


def scrub_anthropic_request(body: dict[str, Any], scrubber: Scrubber) -> dict[str, Any]:
    body = copy.deepcopy(body)
    system = body.get("system")
    if isinstance(system, str):
        body["system"] = scrubber.text(system)
    elif isinstance(system, list):
        _scrub_anthropic_blocks(system, scrubber)
    for message in body.get("messages") or []:
        content = message.get("content")
        if isinstance(content, str):
            message["content"] = scrubber.text(content)
        elif isinstance(content, list):
            _scrub_anthropic_blocks(content, scrubber)
    return body


def restore_anthropic_response(body: dict[str, Any], token_map: TokenMap) -> dict[str, Any]:
    body = copy.deepcopy(body)
    for block in body.get("content") or []:
        kind = block.get("type")
        if kind == "text" and isinstance(block.get("text"), str):
            block["text"] = restore(block["text"], token_map)
        elif kind == "tool_use" and "input" in block:
            block["input"] = restore_json_strings(block["input"], token_map)
        # Thinking blocks stay as tokens: restoring them would break their signature.
    return body


def anthropic_sse_events(body: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """Turn a complete Message into the event sequence a streaming client expects."""
    usage = body.get("usage") or {}
    start_message = {
        **{k: v for k, v in body.items() if k not in {"content", "stop_reason", "stop_sequence", "usage"}},
        "content": [],
        "stop_reason": None,
        "stop_sequence": None,
        "usage": {**usage, "output_tokens": 0},
    }
    events: list[tuple[str, dict[str, Any]]] = [
        ("message_start", {"type": "message_start", "message": start_message})
    ]
    for index, block in enumerate(body.get("content") or []):
        kind = block.get("type")
        if kind == "text":
            start, deltas = (
                {"type": "text", "text": ""},
                [{"type": "text_delta", "text": block.get("text", "")}],
            )
        elif kind == "tool_use":
            start = {**block, "input": {}}
            deltas = [{"type": "input_json_delta", "partial_json": json.dumps(block.get("input", {}))}]
        elif kind == "thinking":
            start = {"type": "thinking", "thinking": ""}
            deltas = [
                {"type": "thinking_delta", "thinking": block.get("thinking", "")},
                {"type": "signature_delta", "signature": block.get("signature", "")},
            ]
        else:
            start, deltas = block, []
        events.append(
            ("content_block_start", {"type": "content_block_start", "index": index, "content_block": start})
        )
        for delta in deltas:
            events.append(
                ("content_block_delta", {"type": "content_block_delta", "index": index, "delta": delta})
            )
        events.append(("content_block_stop", {"type": "content_block_stop", "index": index}))
    events.append(
        (
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": body.get("stop_reason"), "stop_sequence": body.get("stop_sequence")},
                "usage": {"output_tokens": usage.get("output_tokens", 0)},
            },
        )
    )
    events.append(("message_stop", {"type": "message_stop"}))
    return events
