"""PHI/PII scrubbing and restoring.

scrub() swaps identifiers for numbered tokens such as [PERSON_1] and records the
originals in a TokenMap. restore() puts the originals back into the LLM's reply.

The TokenMap lives in memory for one request only. It must never be logged,
written to disk or sent upstream.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field

from presidio_analyzer import AnalyzerEngine, RecognizerRegistry, RecognizerResult
from presidio_analyzer.nlp_engine import NlpEngineProvider
from presidio_analyzer.predefined_recognizers import PhoneRecognizer

from app.core.offline import force_offline
from app.core.recognizers import (
    IePpsnRecognizer,
    medical_record_recognizer,
    postcode_recognizer,
    street_address_recognizer,
    us_zip_recognizer,
)

# Presidio entity -> label used in the token the LLM sees.
TOKEN_LABELS: dict[str, str] = {
    "PERSON": "PERSON",
    "DATE_TIME": "DATE",
    "PHONE_NUMBER": "PHONE",
    "EMAIL_ADDRESS": "EMAIL",
    "LOCATION": "LOCATION",
    "URL": "URL",
    "IP_ADDRESS": "IP_ADDRESS",
    "CREDIT_CARD": "CARD_NUMBER",
    "MEDICAL_RECORD_NUMBER": "MRN",
    "STREET_ADDRESS": "ADDRESS",
    "POSTCODE": "POSTCODE",
    # US (HIPAA)
    "US_SSN": "SSN",
    "MEDICAL_LICENSE": "MEDICAL_LICENSE",
    "US_DRIVER_LICENSE": "DRIVER_LICENSE",
    "US_PASSPORT": "PASSPORT",
    "US_ITIN": "ITIN",
    "US_BANK_NUMBER": "BANK_ACCOUNT",
    # EU (GDPR, Ireland and UK)
    "IBAN_CODE": "IBAN",
    "UK_NHS": "NHS_NUMBER",
    "IE_PPSN": "PPSN",
}

COMMON_ENTITIES = [
    "PERSON",
    "DATE_TIME",
    "PHONE_NUMBER",
    "EMAIL_ADDRESS",
    "LOCATION",
    "URL",
    "IP_ADDRESS",
    "CREDIT_CARD",
    "MEDICAL_RECORD_NUMBER",
    "STREET_ADDRESS",
    "POSTCODE",
]
REGION_ENTITIES: dict[str, list[str]] = {
    "us": ["US_SSN", "MEDICAL_LICENSE", "US_DRIVER_LICENSE", "US_PASSPORT", "US_ITIN", "US_BANK_NUMBER"],
    "eu": ["IBAN_CODE", "UK_NHS", "IE_PPSN"],
}
# spaCy labels Haven has no use for. Ignoring them also silences Presidio's warnings.
SPACY_LABELS_TO_IGNORE = [
    "CARDINAL",
    "ORDINAL",
    "QUANTITY",
    "PERCENT",
    "MONEY",
    "PRODUCT",
    "EVENT",
    "WORK_OF_ART",
    "LAW",
    "LANGUAGE",
]
PHONE_REGIONS = ("US", "CA", "GB", "IE", "DE", "FR", "ES", "IT", "NL")

_MONTH = (
    r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?"
    r"|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?"
)
_SPECIFIC_DATE = re.compile(
    rf"\d{{1,4}}\s*[/.\-]\s*\d{{1,2}}(?:\s*[/.\-]\s*\d{{2,4}})?"  # 04/12/1982, 2024-03-01
    rf"|\b(?:{_MONTH})\b.*\d|\d.*\b(?:{_MONTH})\b",  # 3 March, March 2021
    re.IGNORECASE,
)


def is_identifying_date(text: str) -> bool:
    """True for dates that could identify a person.

    Follows the HIPAA Safe Harbor idea: a specific day or month is an identifier,
    a year on its own is not. Relative or clinical timings ("three weeks ago",
    "twice daily", "8am") are kept because the LLM needs them and they don't
    identify anyone.
    """
    return bool(_SPECIFIC_DATE.search(text))


@dataclass
class TokenMap:
    """Original values for each token, for one request only."""

    _by_value: dict[tuple[str, str], str] = field(default_factory=dict)
    _by_token: dict[str, str] = field(default_factory=dict)
    _counters: Counter[str] = field(default_factory=Counter)

    def token_for(self, label: str, value: str) -> str:
        key = (label, " ".join(value.split()).casefold())
        if key not in self._by_value:
            self._counters[label] += 1
            token = f"[{label}_{self._counters[label]}]"
            self._by_value[key] = token
            self._by_token[token] = value
        return self._by_value[key]

    def original(self, token: str) -> str | None:
        return self._by_token.get(token)

    @property
    def labels(self) -> set[str]:
        return set(self._counters)

    def __len__(self) -> int:
        return len(self._by_token)

    def __repr__(self) -> str:  # never print the originals by accident
        return f"TokenMap({len(self)} tokens)"


@dataclass
class ScrubResult:
    text: str
    entity_counts: Counter[str]


class Sanitiser:
    def __init__(
        self,
        regions: Iterable[str] = ("us", "eu"),
        spacy_model: str = "en_core_web_lg",
        score_threshold: float = 0.4,
        allow_list: Iterable[str] = (),
    ) -> None:
        self.regions = list(regions)
        self.score_threshold = score_threshold
        self.allow_list = list(allow_list)
        self.entities = COMMON_ENTITIES + [e for r in self.regions for e in REGION_ENTITIES[r]]
        self.analyzer = self._build_analyzer(spacy_model)

    @staticmethod
    def _build_analyzer(spacy_model: str) -> AnalyzerEngine:
        force_offline()
        nlp_engine = NlpEngineProvider(
            nlp_configuration={
                "nlp_engine_name": "spacy",
                "models": [{"lang_code": "en", "model_name": spacy_model}],
                "ner_model_configuration": {"labels_to_ignore": SPACY_LABELS_TO_IGNORE},
            }
        ).create_engine()
        registry = RecognizerRegistry()
        registry.load_predefined_recognizers(nlp_engine=nlp_engine)
        registry.remove_recognizer("PhoneRecognizer")
        registry.add_recognizer(PhoneRecognizer(supported_regions=PHONE_REGIONS))
        registry.add_recognizer(medical_record_recognizer())
        registry.add_recognizer(IePpsnRecognizer())
        registry.add_recognizer(street_address_recognizer())
        registry.add_recognizer(postcode_recognizer())
        registry.add_recognizer(us_zip_recognizer())
        return AnalyzerEngine(registry=registry, nlp_engine=nlp_engine)

    def find(self, text: str) -> list[RecognizerResult]:
        """Return non-overlapping matches, sorted by position."""
        results = self.analyzer.analyze(
            text=text,
            language="en",
            entities=self.entities,
            score_threshold=self.score_threshold,
            allow_list=self.allow_list or None,
        )
        results = [
            r for r in results if r.entity_type != "DATE_TIME" or is_identifying_date(text[r.start : r.end])
        ]
        # Prefer the most confident match, then the longest, when spans overlap.
        results.sort(key=lambda r: (-r.score, -(r.end - r.start)))
        kept: list[RecognizerResult] = []
        for r in results:
            if all(r.end <= k.start or r.start >= k.end for k in kept):
                kept.append(r)
        return sorted(kept, key=lambda r: r.start)

    def scrub(self, text: str, token_map: TokenMap) -> ScrubResult:
        """Replace identifiers in text with tokens, adding them to token_map.

        Pass the same TokenMap for every message in a request so the same person
        gets the same token throughout the conversation.
        """
        counts: Counter[str] = Counter()
        parts: list[str] = []
        cursor = 0
        for r in self.find(text):
            label = TOKEN_LABELS.get(r.entity_type, r.entity_type)
            parts.append(text[cursor : r.start])
            parts.append(token_map.token_for(label, text[r.start : r.end]))
            counts[label] += 1
            cursor = r.end
        parts.append(text[cursor:])
        return ScrubResult("".join(parts), counts)


def restore(text: str, token_map: TokenMap) -> str:
    """Put original values back in place of tokens.

    LLMs sometimes rewrite tokens, so this also accepts "[Person 1]",
    "[person-1]" and an unbracketed "PERSON_1".
    """
    if not token_map:
        return text
    labels = "|".join(
        re.escape(lbl).replace("_", r"[\s_]") for lbl in sorted(token_map.labels, key=len, reverse=True)
    )
    pattern = re.compile(
        rf"\[\s*({labels})[\s_\-]*(\d+)\s*\]|\b({labels})_(\d+)\b",
        re.IGNORECASE,
    )

    def _swap(m: re.Match[str]) -> str:
        label = re.sub(r"\s", "_", m.group(1) or m.group(3)).upper()
        number = m.group(2) or m.group(4)
        original = token_map.original(f"[{label}_{number}]")
        return original if original is not None else m.group(0)

    return pattern.sub(_swap, text)
