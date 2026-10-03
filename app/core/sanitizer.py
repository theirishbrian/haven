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
    cued_name_recognizer,
    date_recognizer,
    first_name_recognizer,
    greeting_recognizer,
    ie_district_recognizer,
    ie_place_recognizer,
    irish_surname_recognizer,
    lenient_phone_recognizer,
    load_place_list,
    medical_record_recognizer,
    name_parts,
    postcode_recognizer,
    street_address_recognizer,
    titled_name_recognizer,
    unit_number_recognizer,
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


_WORD_NAMES = set(load_place_list("first_names_ambiguous.txt"))


def _is_word_used_as_word(text: str, r: RecognizerResult) -> bool:
    """True for "Mark the chart" or "Will review": a word-like name alone, followed by a lower-case word."""
    if r.entity_type != "PERSON" or text[r.start : r.end] not in _WORD_NAMES:
        return False
    following = text[r.end : r.end + 20].lstrip(" \t")
    return bool(following) and following[0].islower()


def merge_overlaps(results: list[RecognizerResult]) -> list[RecognizerResult]:
    """Combine overlapping matches into one span covering all of them.

    Dropping the weaker of two overlapping matches can leak text: if "Ennis"
    (a town) beat "Mary Ennis" (a person), "Mary" would get through. Merging
    removes the union instead. The label comes from the most confident match,
    then the longest.
    """
    merged: list[RecognizerResult] = []
    for r in sorted(results, key=lambda r: (r.start, -r.end)):
        if merged and r.start < merged[-1].end:
            current = merged[-1]
            best = max((current, r), key=lambda x: (x.score, x.end - x.start))
            merged[-1] = RecognizerResult(
                best.entity_type, current.start, max(current.end, r.end), max(current.score, r.score)
            )
        else:
            merged.append(r)
    return merged


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

    def person_token(self, value: str) -> str:
        """Token for a person, shared with a fuller name already seen.

        "Mark" after "Mark Byrne" gets Mark Byrne's token, so the AI knows it is
        the same person. If the short name fits more than one known person
        (two Byrnes), it gets its own token rather than a guess.
        """
        key = ("PERSON", " ".join(value.split()).casefold())
        if key in self._by_value:
            return self._by_value[key]
        parts = set(name_parts(value))
        if parts:
            matches = {
                token
                for token, original in self._by_token.items()
                if token.startswith("[PERSON_") and parts < set(name_parts(original))
            }
            if len(matches) == 1:
                token = matches.pop()
                self._by_value[key] = token
                return token
        return self.token_for("PERSON", value)

    def values(self, label: str) -> list[str]:
        """Original values for one label, e.g. every PERSON seen so far."""
        return [v for t, v in self._by_token.items() if t.startswith(f"[{label}_")]

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
        self.allow_list = [*load_place_list("clinical_terms.txt"), *allow_list]
        self.entities = COMMON_ENTITIES + [e for r in self.regions for e in REGION_ENTITIES[r]]
        self.analyzer = self._build_analyzer(spacy_model, self.regions)

    @staticmethod
    def _build_analyzer(spacy_model: str, regions: list[str]) -> AnalyzerEngine:
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
        registry.add_recognizer(unit_number_recognizer())
        registry.add_recognizer(first_name_recognizer())
        registry.add_recognizer(titled_name_recognizer())
        registry.add_recognizer(irish_surname_recognizer())
        registry.add_recognizer(greeting_recognizer())
        registry.add_recognizer(cued_name_recognizer())
        registry.add_recognizer(date_recognizer())
        registry.add_recognizer(lenient_phone_recognizer())
        if "eu" in regions:
            registry.add_recognizer(ie_place_recognizer())
            registry.add_recognizer(ie_district_recognizer())
        return AnalyzerEngine(registry=registry, nlp_engine=nlp_engine)

    def find(self, text: str, known_names: Iterable[str] = ()) -> list[RecognizerResult]:
        """Return non-overlapping matches, sorted by position.

        known_names are people already found earlier in the same request. Any
        word from their names is removed wherever else it appears.
        """
        results = self.analyzer.analyze(
            text=text,
            language="en",
            entities=self.entities,
            score_threshold=self.score_threshold,
            allow_list=self.allow_list or None,
        )
        results = [
            r
            for r in results
            if (r.entity_type != "DATE_TIME" or is_identifying_date(text[r.start : r.end]))
            and not _is_word_used_as_word(text, r)
        ]
        results = merge_overlaps(results)
        return merge_overlaps(results + self._other_mentions(text, results, known_names))

    @staticmethod
    def _other_mentions(
        text: str, results: list[RecognizerResult], known_names: Iterable[str]
    ) -> list[RecognizerResult]:
        """Find every other mention of a name already detected.

        A note might give "Siobhán Kavanagh" once and then just "Kavanagh" or
        "Siobhán". The model may only catch the first, so search for the parts.
        """
        names = [text[r.start : r.end] for r in results if r.entity_type == "PERSON"]
        parts = {part for name in [*names, *known_names] for part in name_parts(name)}
        if not parts:
            return []
        alternation = "|".join(re.escape(p) for p in sorted(parts, key=len, reverse=True))
        pattern = re.compile(rf"(?<![\w'’-])(?:{alternation})(?![\w'’-])")
        return [RecognizerResult("PERSON", m.start(), m.end(), 0.6) for m in pattern.finditer(text)]

    def scrub(self, text: str, token_map: TokenMap) -> ScrubResult:
        """Replace identifiers in text with tokens, adding them to token_map.

        Pass the same TokenMap for every message in a request so the same person
        gets the same token throughout the conversation.
        """
        counts: Counter[str] = Counter()
        parts: list[str] = []
        cursor = 0
        found = self.find(text, known_names=token_map.values("PERSON"))
        # Register full names before short ones so "Mark" can share "Mark Byrne"'s token.
        people = list(dict.fromkeys(text[r.start : r.end] for r in found if r.entity_type == "PERSON"))
        for name in sorted(people, key=lambda n: -len(name_parts(n))):  # stable: ties keep text order
            token_map.person_token(name)
        for r in found:
            label = TOKEN_LABELS.get(r.entity_type, r.entity_type)
            value = text[r.start : r.end]
            parts.append(text[cursor : r.start])
            parts.append(
                token_map.person_token(value) if label == "PERSON" else token_map.token_for(label, value)
            )
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
