# Security

Haven exists to stop patient and client data leaking to AI providers. If you find a way that data gets past it, please tell us privately before posting it anywhere.

## Reporting a leak or vulnerability

Use GitHub's private reporting: go to the Security tab of this repo and choose "Report a vulnerability".

Please include:

- the text that got through (made-up data only, never a real patient's details)
- what Haven sent upstream, if you captured it
- your Haven version and settings (`HAVEN_REGIONS`, spaCy model)

We aim to reply within five working days.

## Never send real patient data

Not in issues, pull requests, test fixtures or bug reports. Make up names, dates and numbers that have the same shape as the real thing.

## What counts as a security issue

- PHI or PII reaching the upstream provider unscrubbed
- original values written to disk, logs or the audit database
- Haven making network calls other than to the provider you configured
- tokens restored into the wrong place (one patient's name in another's note)
