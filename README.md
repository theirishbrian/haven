# Haven

Haven is a small proxy that runs on your own machine and strips patient and client details out of AI prompts before they reach OpenAI or Anthropic.

A therapist types "Summarise today's session with John Smith, DOB 04/12/1982". The AI provider receives "Summarise today's session with [PERSON_1], DOB [DATE_1]". When the reply comes back, Haven puts John Smith's name back in. The original details stay in memory on your machine for that one request and are never written to disk.

> **Status: early development.** The scrubbing engine works and is tested. The proxy endpoints, audit log and Docker image are being built now. Don't use this with real patient data yet.

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

## Roadmap

- [x] Scrubbing engine with US and EU packs
- [ ] `/v1/chat/completions` (OpenAI format) and `/v1/messages` (Anthropic format)
- [ ] Streaming support (buffered first, word by word later)
- [ ] Audit log of counts only, never content
- [ ] Docker image
- [ ] Published accuracy figures on a synthetic test set

## Licence

MIT. See [LICENSE](LICENSE).
