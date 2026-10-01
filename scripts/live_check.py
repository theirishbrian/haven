"""Check Haven against your real OpenAI and Anthropic accounts.

Sends a made-up clinical note through Haven twice per provider:

1. with restoring switched off, so you see the reply exactly as the provider
   wrote it. If scrubbing works, it only ever saw tokens like [PERSON_1].
2. normally, so you see the reply with the real details put back.

Run it inside the Docker container (no Python needed on your machine):

    docker compose exec haven python scripts/live_check.py

or directly, with Haven running:

    python scripts/live_check.py --provider anthropic

Each run costs a fraction of a cent in API usage. The note is fictional.
"""

from __future__ import annotations

import argparse
import sys

import httpx

NOTE = (
    "Session note. Client: Siobhan Kavanagh, DOB 17/06/1979, MRN: 00731842. "
    "Lives at 22 Ranelagh Road, Dublin 6, D06 X2K7. Phone 086 555 0142, "
    "email siobhan.kavanagh@example.ie. PPSN 1234567T. "
    "Reports low mood for three weeks, sleeping badly. Sertraline 50mg once daily since 2021."
)
SECRETS = [
    "Siobhan",
    "Kavanagh",
    "17/06/1979",
    "00731842",
    "22 Ranelagh Road",
    "D06 X2K7",
    "086 555 0142",
    "siobhan.kavanagh@example.ie",
    "1234567T",
]
INSTRUCTION = (
    "Repeat the note below back to me exactly, word for word, between <note> and </note>. "
    "Then write one sentence summarising it.\n\n"
)


def ask(client: httpx.Client, provider: str, model: str, restore: bool) -> str:
    headers = {} if restore else {"x-haven-restore": "false"}
    prompt = INSTRUCTION + NOTE
    if provider == "openai":
        r = client.post(
            "/v1/chat/completions",
            headers=headers,
            json={"model": model, "messages": [{"role": "user", "content": prompt}]},
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]
    r = client.post(
        "/v1/messages",
        headers=headers,
        json={"model": model, "max_tokens": 600, "messages": [{"role": "user", "content": prompt}]},
    )
    r.raise_for_status()
    return "".join(b.get("text", "") for b in r.json()["content"] if b["type"] == "text")


def check(client: httpx.Client, provider: str, model: str) -> str:
    """Return "pass", "fail" or "skip"."""
    print(f"\n=== {provider} ({model}) ===")
    try:
        raw = ask(client, provider, model, restore=False)
        restored = ask(client, provider, model, restore=True)
    except httpx.HTTPStatusError as exc:
        print(f"SKIPPED: {exc.response.status_code} {exc.response.text[:300]}")
        return "skip"
    except httpx.HTTPError as exc:
        print(f"FAILED: could not reach Haven ({exc}). Is it running?")
        return "fail"

    print("\nWhat the provider saw and wrote back (restoring off):\n")
    print(raw)
    leaked = [s for s in SECRETS if s in raw]
    print("\nWhat your app receives (restoring on):\n")
    print(restored)
    restored_ok = "Kavanagh" in restored

    print()
    print("PASS  no patient details reached the provider" if not leaked else f"FAIL  leaked: {leaked}")
    print("PASS  details were put back in the reply" if restored_ok else "FAIL  name was not restored")
    return "pass" if not leaked and restored_ok else "fail"


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--url", default="http://127.0.0.1:8787", help="Where Haven is running")
    parser.add_argument("--provider", choices=["openai", "anthropic", "both"], default="both")
    parser.add_argument("--openai-model", default="gpt-4o-mini")
    parser.add_argument("--anthropic-model", default="claude-haiku-4-5-20251001")
    args = parser.parse_args()

    results: list[str] = []
    with httpx.Client(base_url=args.url, timeout=120) as client:
        if args.provider in {"openai", "both"}:
            results.append(check(client, "openai", args.openai_model))
        if args.provider in {"anthropic", "both"}:
            results.append(check(client, "anthropic", args.anthropic_model))
        try:
            rows = client.get("/haven/audit", params={"limit": 4}).json()
            print("\n=== Audit log (latest rows) ===")
            for row in rows:
                counts = ", ".join(f"{n} {k}" for k, n in row["entity_counts"].items())
                print(f"{row['created_at']}  {row['provider']:<9}  {row['status_code']}  {counts}")
        except httpx.HTTPError:
            pass

    if "fail" in results:
        print("\nSome checks failed. See above.")
        return 1
    if "pass" not in results:
        print("\nNothing was tested. Add an API key to .env and restart Haven.")
        return 1
    print("\nAll checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
