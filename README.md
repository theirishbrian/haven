# Haven

Haven is a small proxy that runs on your own machine and strips patient and client details out of AI prompts before they reach OpenAI or Anthropic.

A therapist types "Summarise today's session with John Smith, DOB 04/12/1982". The AI provider receives "Summarise today's session with [PERSON_1], DOB [DATE_1]". When the reply comes back, Haven puts John Smith's name back in. The original details stay in memory on your machine for that one request and are never written to disk.

> **Status: early development.** The scrubbing engine, both proxy endpoints and the audit log work and are tested against a simulated provider. They have passed a live test against Anthropic's API but not yet OpenAI's. Don't use this with real patient data yet.

## What it catches

Haven uses [Microsoft Presidio](https://github.com/microsoft/presidio) and the spaCy `en_core_web_lg` model, plus its own recognisers for things Presidio misses.

| Everywhere | US pack (HIPAA) | EU pack (GDPR) |
|---|---|---|
| Names | Social Security numbers | Irish PPSNs (checksum validated) |
| Specific dates (DOB, appointment dates) | Medical licence (DEA) numbers | IBANs |
| Phone numbers (US, UK, IE and others) | Driver's licence and passport numbers | UK NHS numbers |
| Email addresses, URLs, IP addresses | ITINs and bank account numbers | Eircodes and UK postcodes |
| Street addresses and places | ZIP codes | |
| Medical record and patient ID numbers | | |

Turn packs on or off with `HAVEN_REGIONS=us,eu`.

## What it deliberately leaves alone

The AI still needs clinical context to be useful, so Haven keeps:

- durations and frequencies ("three weeks", "twice daily", "8am")
- a year on its own ("diagnosed in 2019"), which HIPAA Safe Harbor allows
- medication names, doses and scores ("sertraline 50mg", "PHQ-9 of 14")

## What it can't promise

No automated scrubber catches everything. Names that are also ordinary words, unusual spellings and identifiers in formats we haven't seen will sometimes get through. Haven lowers your risk. It doesn't remove your obligations under HIPAA or GDPR, and it isn't a substitute for a business associate agreement or data processing agreement with your AI provider. Read what you send.

If you find something that gets through, please report it privately (see [SECURITY.md](SECURITY.md)).

## Running it with Docker

This is the easiest way, and you don't need Python installed.

1. Install [Docker Desktop](https://www.docker.com/products/docker-desktop/) and start it.
2. Download Haven and add your keys:

   ```bash
   git clone https://github.com/theirishbrian/haven
   cd haven
   cp .env.example .env
   ```

   On Windows PowerShell, use `copy .env.example .env` for the last line. Open `.env` in any text editor and paste in your OpenAI key, your Anthropic key, or both.

3. Start Haven:

   ```bash
   docker compose up -d
   ```

   The first build downloads about 1 GB (mostly the language model) and takes a few minutes. After that it starts in seconds.

4. Check it works against your real accounts:

   ```bash
   docker compose exec haven python scripts/live_check.py
   ```

   This sends a made-up clinical note through Haven, shows you exactly what OpenAI and Anthropic received (tokens only), then shows the reply with the details put back. It costs a fraction of a cent.

Haven is only reachable from your own computer. Your audit log is kept in a Docker volume, so it survives restarts. To stop Haven, run `docker compose down`.

## Running it (development)

Python 3.11 or later.

```bash
git clone https://github.com/theirishbrian/haven
cd haven
pip install -r requirements-dev.txt
python -m spacy download en_core_web_lg
cp .env.example .env   # add your API keys
pytest
```

## Pointing your app at Haven

Start Haven with `python -m app`. It listens on `http://127.0.0.1:8787` and only accepts connections from your own machine.

Anything that talks to OpenAI or Anthropic can use Haven by changing one setting, the base URL.

```python
# OpenAI
from openai import OpenAI
client = OpenAI(base_url="http://127.0.0.1:8787/v1")

# Anthropic
from anthropic import Anthropic
client = Anthropic(base_url="http://127.0.0.1:8787")
```

Put your API keys in Haven's `.env` file, or keep sending them from your app as usual. Haven forwards them.

To see a reply exactly as the provider wrote it, tokens and all, send the header `x-haven-restore: false`.

### Streaming

If your app asks for a streamed reply, Haven waits for the whole answer, restores the real names, then sends it back in streaming format. The reply arrives in one go rather than word by word. This is deliberate: it means a token like `[PERSON_1]` can never be split across two pieces and slip through unrestored.

## The audit log

Every request adds one row to a local SQLite file (`haven_audit.db`): the time, provider, model, token counts and how many of each kind of identifier were removed, e.g. "2 PERSON, 1 DATE". It never stores prompt text, replies or the original values. The test suite reads the raw database file and fails if any patient detail is in it.

- `GET /haven/audit` shows the latest rows as JSON
- `GET /haven/audit.csv` downloads the whole log

## Roadmap

- [x] Scrubbing engine with US and EU packs
- [x] `/v1/chat/completions` (OpenAI format) and `/v1/messages` (Anthropic format)
- [x] Buffered streaming
- [x] Audit log of counts only, never content
- [x] Tested against a live Anthropic account
- [ ] Tested against a live OpenAI account
- [ ] Word-by-word streaming
- [x] Docker image
- [ ] Published accuracy figures on a synthetic test set

## Licence

MIT. See [LICENSE](LICENSE).
