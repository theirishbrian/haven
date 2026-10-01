"""Stop third-party libraries from reaching the internet.

Presidio's email recogniser uses tldextract, which downloads the Public Suffix
List on first use. A privacy proxy must not make outbound calls its user
didn't ask for, so we switch tldextract to its bundled snapshot.
"""

from __future__ import annotations

import tldextract
import tldextract.tldextract as _tld


def force_offline() -> None:
    _tld.TLD_EXTRACTOR = tldextract.TLDExtract(suffix_list_urls=(), cache_dir=None)
