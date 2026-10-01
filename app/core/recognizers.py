"""Custom recognisers that Presidio does not ship out of the box.

Each recogniser here returns only the identifier itself, never the label in
front of it, so "MRN: 00482913" becomes "MRN: [MRN_1]".
"""

from __future__ import annotations

import re

from presidio_analyzer import EntityRecognizer, RecognizerResult
from presidio_analyzer.nlp_engine import NlpArtifacts


class LabelledIdRecognizer(EntityRecognizer):
    """Finds an identifier that follows a known label, such as "MRN:" or "Patient ID".

    The regex must contain one capture group. Only that group is reported, so the
    label stays readable in the scrubbed text.
    """

    def __init__(
        self,
        entity: str,
        pattern: str,
        score: float = 0.85,
        name: str | None = None,
        ignore_case: bool = True,
    ):
        self._regex = re.compile(pattern, re.IGNORECASE if ignore_case else 0)
        self._score = score
        super().__init__(supported_entities=[entity], name=name or f"{entity}Recognizer")

    def load(self) -> None:  # nothing to load
        pass

    def analyze(
        self, text: str, entities: list[str], nlp_artifacts: NlpArtifacts | None = None
    ) -> list[RecognizerResult]:
        entity = self.supported_entities[0]
        if entities and entity not in entities:
            return []
        return [
            RecognizerResult(entity, m.start(1), m.end(1), self._score) for m in self._regex.finditer(text)
        ]


def medical_record_recognizer() -> LabelledIdRecognizer:
    """Medical record numbers, hospital numbers and patient IDs.

    Formats vary by hospital, so this relies on the label rather than the shape.
    """
    labels = (
        r"MRN|medical\s+record\s+(?:number|no\.?|#)|hospital\s+(?:number|no\.?)"
        r"|patient\s+(?:id|number|no\.?)|chart\s+(?:number|no\.?|#)|HN"
    )
    pattern = rf"\b(?:{labels})\s*[:#]?\s*([A-Z]{{0,4}}[-\s]?\d[\d-]{{3,14}}[A-Z]?)\b"
    return LabelledIdRecognizer("MEDICAL_RECORD_NUMBER", pattern, name="MedicalRecordRecognizer")


# --- Irish PPSN -------------------------------------------------------------

_PPSN_RE = re.compile(r"\b(\d{7})([A-W])([A-IW]?)\b", re.IGNORECASE)
_PPSN_CHECK = "WABCDEFGHIJKLMNOPQRSTUV"


def ppsn_is_valid(digits: str, check: str, extra: str = "") -> bool:
    """Validate a PPSN check character (mod 23 over weighted digits)."""
    total = sum(int(d) * w for d, w in zip(digits, range(8, 1, -1), strict=True))
    extra = extra.upper()
    if extra and extra != "W":
        total += (ord(extra) - ord("A") + 1) * 9
    return _PPSN_CHECK[total % 23] == check.upper()


class IePpsnRecognizer(EntityRecognizer):
    """Irish Personal Public Service Numbers, validated by checksum."""

    def __init__(self) -> None:
        super().__init__(supported_entities=["IE_PPSN"], name="IePpsnRecognizer")

    def load(self) -> None:
        pass

    def analyze(
        self, text: str, entities: list[str], nlp_artifacts: NlpArtifacts | None = None
    ) -> list[RecognizerResult]:
        if entities and "IE_PPSN" not in entities:
            return []
        results = []
        for m in _PPSN_RE.finditer(text):
            if ppsn_is_valid(m.group(1), m.group(2), m.group(3)):
                results.append(RecognizerResult("IE_PPSN", m.start(), m.end(), 0.95))
        return results


# --- Street addresses and postcodes -----------------------------------------

_STREET_TYPES = (
    r"Road|Rd|Street|St|Avenue|Ave|Lane|Ln|Drive|Dr|Court|Ct|Close|Crescent|Cres"
    r"|Terrace|Tce|Place|Pl|Way|Boulevard|Blvd|Park|Grove|Square|Sq|Gardens|Row"
    r"|Hill|Green|Walk|View|Heights|Highway|Hwy|Parkway|Pkwy|Circle|Cir"
)


def street_address_recognizer() -> LabelledIdRecognizer:
    """House number plus a street name, e.g. "14 Rathmines Road" or "221B Baker St".

    HIPAA Safe Harbor treats any street address as an identifier.
    """
    pattern = (
        rf"(\b\d{{1,5}}[A-Za-z]?(?:\s*-\s*\d{{1,5}})?,?\s+"
        rf"(?:[A-Z][a-z'’]+\s+){{1,4}}(?:{_STREET_TYPES})\b\.?"
        rf"(?:,?\s+(?:Apt|Apartment|Unit|Suite|Flat)\.?\s*#?\w+)?)"
    )
    # Case-sensitive on purpose: street names are capitalised, which keeps
    # phrases like "3 times a day" from matching.
    return LabelledIdRecognizer(
        "STREET_ADDRESS", pattern, score=0.9, name="StreetAddressRecognizer", ignore_case=False
    )


def postcode_recognizer() -> LabelledIdRecognizer:
    """Irish Eircodes and UK postcodes, which identify a household or street."""
    eircode = r"[AC-FHKNPRTV-Y]\d{2}|D6W"
    uk = r"[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}"
    pattern = rf"\b((?:{eircode})\s?[0-9AC-FHKNPRTV-Y]{{4}}|{uk})\b"
    return LabelledIdRecognizer("POSTCODE", pattern, score=0.6, name="PostcodeRecognizer", ignore_case=False)


def us_zip_recognizer() -> LabelledIdRecognizer:
    """US ZIP codes, only when labelled or following a state abbreviation."""
    states = (
        r"AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|MT"
        r"|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY|DC"
    )
    pattern = rf"(?:\b(?i:zip(?:\s*code)?)\s*[:#]?\s*|\b(?:{states}),?\s+)(\d{{5}}(?:-\d{{4}})?)\b"
    return LabelledIdRecognizer("POSTCODE", pattern, score=0.7, name="UsZipRecognizer", ignore_case=False)
