"""End-to-end proxy tests against a fake upstream. All patient details are made up."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.sanitizer import Sanitiser
from app.main import create_app

NOTE = (
    "Summarise: John Smith, DOB 04/12/1982, MRN: 00482913, lives at 14 Rathmines Road. "
    "Email john.smith@gmail.com. Low mood for three weeks, sertraline 50mg twice daily."
)
SECRETS = ["John Smith", "04/12/1982", "00482913", "14 Rathmines Road", "john.smith@gmail.com"]
KEPT = ["three weeks", "sertraline 50mg", "twice daily"]


class FakeUpstream:
    """Records what Haven sends and replies by echoing the last user message."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.status = 200
        self.fail = False

    @property
    def last_body(self) -> dict[str, Any]:
        return json.loads(self.requests[-1].content)

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.fail:
            raise httpx.ConnectError("boom")
        body = json.loads(request.content)
        if self.status != 200:
            echoed = body["messages"][-1]["content"]
            return httpx.Response(self.status, json={"error": {"message": f"Bad input: {echoed}"}})
        if request.url.path == "/v1/chat/completions":
            return httpx.Response(200, json=self._openai(body))
        return httpx.Response(200, json=self._anthropic(body))

    @staticmethod
    def _openai(body: dict[str, Any]) -> dict[str, Any]:
        last = body["messages"][-1]["content"]
        return {
            "id": "chatcmpl-1",
            "object": "chat.completion",
            "created": 1,
            "model": body["model"],
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": f"Echo: {last} Regards to [Person 1]."},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 50, "completion_tokens": 20, "total_tokens": 70},
        }

    @staticmethod
    def _anthropic(body: dict[str, Any]) -> dict[str, Any]:
        content = body["messages"][-1]["content"]
        last = (
            content if isinstance(content, str) else next(b["text"] for b in content if b["type"] == "text")
        )
        return {
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "model": body["model"],
            "content": [
                {"type": "text", "text": f"Echo: {last}"},
                {"type": "tool_use", "id": "tu_1", "name": "save_note", "input": {"patient": "[PERSON_1]"}},
            ],
            "stop_reason": "tool_use",
            "stop_sequence": None,
            "usage": {"input_tokens": 40, "output_tokens": 15},
        }


@pytest.fixture(scope="module")
def sanitiser() -> Sanitiser:
    return Sanitiser()


@pytest.fixture
def upstream() -> FakeUpstream:
    return FakeUpstream()


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "audit.db"


@pytest.fixture
def client(sanitiser: Sanitiser, upstream: FakeUpstream, db_path: Path) -> Iterator[TestClient]:
    settings = Settings(
        _env_file=None,
        OPENAI_API_KEY="sk-test",
        ANTHROPIC_API_KEY="sk-ant-test",
        audit_db_path=str(db_path),
    )
    app = create_app(settings=settings, sanitiser=sanitiser, transport=httpx.MockTransport(upstream.handler))
    with TestClient(app) as c:
        yield c


def assert_scrubbed(text: str) -> None:
    for secret in SECRETS:
        assert secret not in text, f"{secret!r} reached the provider"
    for phrase in KEPT:
        assert phrase in text, f"{phrase!r} was scrubbed but should be kept"


# --- OpenAI ------------------------------------------------------------------


def test_openai_scrubs_before_sending_and_restores_reply(client: TestClient, upstream: FakeUpstream) -> None:
    r = client.post(
        "/v1/chat/completions",
        json={
            "model": "gpt-4o",
            "messages": [
                {"role": "system", "content": "You are a clinical note assistant."},
                {"role": "user", "content": NOTE},
            ],
        },
    )
    assert r.status_code == 200
    assert_scrubbed(upstream.requests[-1].content.decode())
    assert upstream.requests[-1].headers["authorization"] == "Bearer sk-test"

    reply = r.json()["choices"][0]["message"]["content"]
    for secret in SECRETS:
        assert secret in reply
    assert reply.endswith("Regards to John Smith.")


def test_openai_content_parts_and_tool_arguments(client: TestClient, upstream: FakeUpstream) -> None:
    client.post(
        "/v1/chat/completions",
        json={
            "model": "gpt-4o",
            "messages": [
                {"role": "user", "name": "john_smith", "content": [{"type": "text", "text": NOTE}]},
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "c1",
                            "type": "function",
                            "function": {"name": "lookup", "arguments": json.dumps({"q": "John Smith"})},
                        }
                    ],
                },
                {"role": "tool", "tool_call_id": "c1", "content": "John Smith seen 04/12/1982"},
                {"role": "user", "content": "Thanks"},
            ],
        },
    )
    sent = upstream.requests[-1].content.decode()
    assert_scrubbed(sent)
    assert "john_smith" not in sent


def test_same_patient_same_token_across_history(client: TestClient, upstream: FakeUpstream) -> None:
    client.post(
        "/v1/chat/completions",
        json={
            "model": "gpt-4o",
            "messages": [
                {"role": "user", "content": "John Smith is anxious."},
                {"role": "assistant", "content": "Noted for John Smith."},
                {"role": "user", "content": "Draft a letter to John Smith."},
            ],
        },
    )
    messages = upstream.last_body["messages"]
    assert all("[PERSON_1]" in m["content"] for m in messages)


def test_openai_buffered_stream(client: TestClient, upstream: FakeUpstream) -> None:
    with client.stream(
        "POST",
        "/v1/chat/completions",
        json={
            "model": "gpt-4o",
            "stream": True,
            "stream_options": {"include_usage": True},
            "messages": [{"role": "user", "content": NOTE}],
        },
    ) as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        lines = [line for line in r.iter_lines() if line]

    assert upstream.last_body["stream"] is False
    assert "stream_options" not in upstream.last_body
    assert lines[-1] == "data: [DONE]"
    chunks = [json.loads(line.removeprefix("data: ")) for line in lines[:-1]]
    text = "".join(c["choices"][0]["delta"].get("content", "") for c in chunks if c["choices"])
    assert "John Smith" in text
    assert chunks[-1]["usage"]["completion_tokens"] == 20
    assert chunks[-2]["choices"][0]["finish_reason"] == "stop"


def test_upstream_error_is_passed_through_and_restored(client: TestClient, upstream: FakeUpstream) -> None:
    upstream.status = 400
    r = client.post(
        "/v1/chat/completions",
        json={"model": "gpt-4o", "messages": [{"role": "user", "content": "Note for John Smith"}]},
    )
    assert r.status_code == 400
    assert "John Smith" in r.text
    assert "John Smith" not in upstream.requests[-1].content.decode()


def test_unreachable_upstream_gives_502(client: TestClient, upstream: FakeUpstream) -> None:
    upstream.fail = True
    r = client.post(
        "/v1/chat/completions", json={"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]}
    )
    assert r.status_code == 502
    assert r.json()["error"]["type"] == "haven_error"


def test_missing_api_key(sanitiser: Sanitiser, upstream: FakeUpstream, db_path: Path) -> None:
    settings = Settings(_env_file=None, audit_db_path=str(db_path))
    app = create_app(settings=settings, sanitiser=sanitiser, transport=httpx.MockTransport(upstream.handler))
    with TestClient(app) as c:
        r = c.post("/v1/chat/completions", json={"model": "gpt-4o", "messages": []})
        assert r.status_code == 401
        # A key sent by the client is forwarded when Haven has none of its own.
        r = c.post(
            "/v1/chat/completions",
            headers={"authorization": "Bearer sk-client"},
            json={"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert r.status_code == 200
        assert upstream.requests[-1].headers["authorization"] == "Bearer sk-client"


def test_bad_json(client: TestClient) -> None:
    r = client.post("/v1/chat/completions", content=b"not json", headers={"content-type": "application/json"})
    assert r.status_code == 400


# --- Anthropic ---------------------------------------------------------------


def test_anthropic_scrubs_and_restores(client: TestClient, upstream: FakeUpstream) -> None:
    r = client.post(
        "/v1/messages",
        json={
            "model": "claude-sonnet-5-5",
            "max_tokens": 500,
            "system": [{"type": "text", "text": "Clinic manager is Aoife Byrne."}],
            "messages": [{"role": "user", "content": [{"type": "text", "text": NOTE}]}],
        },
    )
    assert r.status_code == 200
    sent = upstream.requests[-1]
    assert_scrubbed(sent.content.decode())
    assert "Aoife Byrne" not in sent.content.decode()
    assert sent.headers["x-api-key"] == "sk-ant-test"
    assert sent.headers["anthropic-version"] == "2023-06-01"

    content = r.json()["content"]
    assert "John Smith" in content[0]["text"]
    # The system prompt is scrubbed first, so [PERSON_1] is the clinic manager.
    assert content[1]["input"] == {"patient": "Aoife Byrne"}


def test_anthropic_tool_results_and_thinking(client: TestClient, upstream: FakeUpstream) -> None:
    thinking = {"type": "thinking", "thinking": "Consider [PERSON_1].", "signature": "sig"}
    client.post(
        "/v1/messages",
        json={
            "model": "claude-sonnet-5-5",
            "max_tokens": 500,
            "messages": [
                {"role": "user", "content": NOTE},
                {
                    "role": "assistant",
                    "content": [
                        thinking,
                        {"type": "tool_use", "id": "t1", "name": "lookup", "input": {"name": "John Smith"}},
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": "t1", "content": "MRN: 00482913"},
                        {"type": "text", "text": "Go on"},
                    ],
                },
            ],
        },
    )
    sent = upstream.last_body
    assert_scrubbed(json.dumps(sent["messages"][0]))
    assert "John Smith" not in json.dumps(sent)
    assert "00482913" not in json.dumps(sent)
    # Signed thinking blocks must go back exactly as the model wrote them.
    assert sent["messages"][1]["content"][0] == thinking


def test_anthropic_buffered_stream(client: TestClient) -> None:
    with client.stream(
        "POST",
        "/v1/messages",
        json={
            "model": "claude-sonnet-5-5",
            "max_tokens": 500,
            "stream": True,
            "messages": [{"role": "user", "content": NOTE}],
        },
    ) as r:
        assert r.status_code == 200
        raw = r.read().decode()

    events = []
    for block in raw.strip().split("\n\n"):
        name_line, data_line = block.split("\n")
        events.append((name_line.removeprefix("event: "), json.loads(data_line.removeprefix("data: "))))
    names = [n for n, _ in events]
    assert names[0] == "message_start"
    assert names[-2:] == ["message_delta", "message_stop"]
    text = "".join(d["delta"].get("text", "") for n, d in events if n == "content_block_delta")
    assert "John Smith" in text
    tool_json = "".join(d["delta"].get("partial_json", "") for n, d in events if n == "content_block_delta")
    assert json.loads(tool_json) == {"patient": "John Smith"}
    assert events[-2][1]["delta"]["stop_reason"] == "tool_use"


# --- Audit log ---------------------------------------------------------------


def test_audit_log_records_counts_but_no_content(client: TestClient, db_path: Path) -> None:
    client.post(
        "/v1/chat/completions", json={"model": "gpt-4o", "messages": [{"role": "user", "content": NOTE}]}
    )
    client.post(
        "/v1/messages",
        json={
            "model": "claude-sonnet-5-5",
            "max_tokens": 10,
            "stream": True,
            "messages": [{"role": "user", "content": NOTE}],
        },
    )

    rows = client.get("/haven/audit").json()
    assert len(rows) == 2
    anthropic_row, openai_row = rows
    assert openai_row["provider"] == "openai"
    assert openai_row["input_tokens"] == 50
    assert openai_row["entity_counts"]["PERSON"] == 1
    assert openai_row["entities_scrubbed"] >= 5
    assert anthropic_row["stream"] == 1
    assert anthropic_row["output_tokens"] == 15

    csv_text = client.get("/haven/audit.csv").text
    assert "1 PERSON" in csv_text

    # Check the raw database file, not just the API, for anything sensitive.
    raw = db_path.read_bytes()
    for secret in [*SECRETS, "[PERSON_1]", "sertraline"]:
        assert secret.encode() not in raw, f"{secret!r} found in audit database"
