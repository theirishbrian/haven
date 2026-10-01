"""Audit log: what was scrubbed and when, never what was said.

Each row records counts only, e.g. "2 PERSON, 1 DATE". No prompt text, reply
text, token map or original value is ever passed to this module, so none can be
written. tests/test_proxy.py checks the database file for leaks after real
requests.
"""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_events (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at        TEXT    NOT NULL,
    provider          TEXT    NOT NULL,
    endpoint          TEXT    NOT NULL,
    model             TEXT,
    stream            INTEGER NOT NULL,
    status_code       INTEGER NOT NULL,
    latency_ms        INTEGER NOT NULL,
    input_tokens      INTEGER,
    output_tokens     INTEGER,
    entities_scrubbed INTEGER NOT NULL,
    entity_counts     TEXT    NOT NULL
);
"""

CSV_COLUMNS = [
    "id",
    "created_at",
    "provider",
    "endpoint",
    "model",
    "stream",
    "status_code",
    "latency_ms",
    "input_tokens",
    "output_tokens",
    "entities_scrubbed",
    "entity_counts",
]


@dataclass
class AuditEvent:
    provider: str
    endpoint: str
    model: str | None
    stream: bool
    status_code: int
    latency_ms: int
    entity_counts: Mapping[str, int] = field(default_factory=dict)
    input_tokens: int | None = None
    output_tokens: int | None = None

    def __post_init__(self) -> None:
        # Labels are fixed uppercase names like PERSON or DATE. Anything else
        # suggests a value slipped in where a label should be, so refuse it.
        for label, count in self.entity_counts.items():
            if not label.replace("_", "").isalnum() or not label.isupper():
                raise ValueError("entity_counts keys must be entity labels")
            if not isinstance(count, int):
                raise ValueError("entity_counts values must be integers")


def summarise_counts(counts: Mapping[str, int]) -> str:
    """'2 PERSON, 1 DATE' style summary, largest first."""
    return ", ".join(f"{n} {label}" for label, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


class AuditLog:
    def __init__(self, path: str) -> None:
        self.path = path
        self._db: aiosqlite.Connection | None = None

    async def open(self) -> None:
        self._db = await aiosqlite.connect(self.path)
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(SCHEMA)
        await self._db.commit()

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    @property
    def db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("AuditLog is not open")
        return self._db

    async def record(self, event: AuditEvent) -> None:
        await self.db.execute(
            """
            INSERT INTO audit_events (
                created_at, provider, endpoint, model, stream, status_code, latency_ms,
                input_tokens, output_tokens, entities_scrubbed, entity_counts
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                datetime.now(UTC).isoformat(timespec="seconds"),
                event.provider,
                event.endpoint,
                event.model,
                int(event.stream),
                event.status_code,
                event.latency_ms,
                event.input_tokens,
                event.output_tokens,
                sum(event.entity_counts.values()),
                json.dumps(dict(sorted(event.entity_counts.items()))),
            ),
        )
        await self.db.commit()

    async def rows(self, limit: int | None = None) -> list[dict[str, object]]:
        sql = "SELECT * FROM audit_events ORDER BY id DESC"
        params: tuple[int, ...] = ()
        if limit is not None:
            sql += " LIMIT ?"
            params = (limit,)
        async with self.db.execute(sql, params) as cursor:
            return [dict(r) for r in await cursor.fetchall()]

    async def to_csv(self) -> str:
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for row in reversed(await self.rows()):
            row["entity_counts"] = summarise_counts(json.loads(str(row["entity_counts"])))
            writer.writerow(row)
        return buffer.getvalue()
