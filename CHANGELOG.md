# Changelog

## 0.1.0 (pre-release)

The first version anyone can run. It works and is tested, but it hasn't been used on real clinical notes yet. Don't send real patient data through it.

### What it does

- Runs on your own computer and sits between your app and OpenAI or Anthropic. Patient and client details are swapped for tokens like `[PERSON_1]` before a prompt leaves your machine, and swapped back when the reply arrives.
- Works with the OpenAI format (`/v1/chat/completions`) and the Anthropic format (`/v1/messages`). Changing one setting, the base URL, is enough to switch an app over.
- Streaming requests work. Haven waits for the full reply, restores it, then sends it back in streaming format.
- Keeps an audit log of what was removed (for example "2 PERSON, 1 DATE"), never the text itself. Export it as CSV from `/haven/audit.csv`.

### What it catches

- Names, including Irish names with fadas and particles, names it has never seen before, and every later mention of a name already found.
- Dates of birth and appointment dates. Durations, frequencies and a year on its own are kept.
- Phone numbers, email addresses, street addresses, postcodes, Eircodes and ZIP codes.
- Medical record numbers, PPSNs, NHS numbers, Social Security numbers and IBANs.
- Irish counties, towns, Dublin suburbs and postal districts.

### Accuracy

On 660 fictional notes, Haven removed 100% of standard identifiers and left 99.9% of clinical phrases intact. Presidio on its own scored 80% and 72% on the same notes. The weak spot is names typed entirely in lower case. See [benchmark/RESULTS.md](benchmark/RESULTS.md).

### Tested against

- Anthropic's live API (Claude Haiku 4.5)
- A simulated OpenAI API. A live OpenAI test is still to do.
