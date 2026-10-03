"""Custom recognisers that Presidio does not ship out of the box.

Each recogniser here returns only the identifier itself, never the label in
front of it, so "MRN: 00482913" becomes "MRN: [MRN_1]".
"""

from __future__ import annotations

import re
from pathlib import Path

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


# --- Irish places -----------------------------------------------------------

_DATA_DIR = Path(__file__).parent / "data"
_IE_COUNTIES = (
    "Antrim|Armagh|Carlow|Cavan|Clare|Cork|Derry|Donegal|Down|Dublin|Fermanagh|Galway|Kerry|Kildare"
    "|Kilkenny|Laois|Leitrim|Limerick|Londonderry|Longford|Louth|Mayo|Meath|Monaghan|Offaly|Roscommon"
    "|Sligo|Tipperary|Tyrone|Waterford|Westmeath|Wexford|Wicklow"
)


class PlaceListRecognizer(EntityRecognizer):
    """Whole-word, case-sensitive match against a list of place names."""

    def __init__(self, places: list[str], entity: str = "LOCATION", score: float = 0.75, name: str = ""):
        alternation = "|".join(re.escape(p) for p in sorted(places, key=len, reverse=True))
        self._regex = re.compile(rf"(?<![\w'’-])(?:{alternation})(?![\w'’-])")
        self._score = score
        super().__init__(supported_entities=[entity], name=name or "PlaceListRecognizer")

    def load(self) -> None:
        pass

    def analyze(
        self, text: str, entities: list[str], nlp_artifacts: NlpArtifacts | None = None
    ) -> list[RecognizerResult]:
        entity = self.supported_entities[0]
        if entities and entity not in entities:
            return []
        return [RecognizerResult(entity, m.start(), m.end(), self._score) for m in self._regex.finditer(text)]


def load_place_list(filename: str) -> list[str]:
    lines = (_DATA_DIR / filename).read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines if line.strip() and not line.lstrip().startswith("#")]


def ie_place_recognizer() -> PlaceListRecognizer:
    """Irish counties, towns and Dublin suburbs, which spaCy often misses or mislabels."""
    return PlaceListRecognizer(load_place_list("ie_uk_places.txt"), name="IePlaceRecognizer")


def ie_district_recognizer() -> LabelledIdRecognizer:
    """Dublin postal districts ("Dublin 6", "Dublin 6W") and "Co. Kerry" / "County Kerry".

    The short form "D6" is left out on purpose: it collides with "Vitamin D3" and
    with the routing key at the start of an Eircode.
    """
    pattern = (
        rf"(\bDublin\s+(?:6W|1[0-8]|2[0-2]|24|[1-9])\b"
        rf"|\b(?:Co\.?|County)\s+(?:{_IE_COUNTIES})\b)"
    )
    return LabelledIdRecognizer(
        "LOCATION", pattern, score=0.9, name="IeDistrictRecognizer", ignore_case=False
    )


def unit_number_recognizer() -> LabelledIdRecognizer:
    """Apartment, flat and unit numbers that appear without a street number."""
    pattern = r"(\b(?:Apartment|Apt\.?|Flat|Unit|Suite)\s*#?\s*\d{1,4}[A-Z]?\b)"
    return LabelledIdRecognizer(
        "STREET_ADDRESS", pattern, score=0.8, name="UnitNumberRecognizer", ignore_case=False
    )


# --- People -----------------------------------------------------------------

# Upper-case letters including Irish fadas and common European accents.
_UP = "A-ZÁÉÍÓÚÀÈÌÒÙÂÊÎÔÛÄËÏÖÜÇÑŁŚŻŹĆŃĘĄŠŽČŪĖĮ"
# Irish eclipsis and t-prefix give surnames like "tSaoir" or "bhFearraigh", so allow a short
# lower-case prefix before the capital.
_CAP_WORD = rf"(?:[a-z]{{1,2}})?[{_UP}][\w'’-]+"
# Surname particles: Irish (Ní, Nic, Mac, Ó, Uí), Arabic, Dutch, German, Spanish, Italian.
NAME_PARTICLES = (
    "Ní",
    "Nic",
    "Mac",
    "Mc",
    "Ó",
    "Uí",
    "Ui",
    "Bean Uí",
    "de",
    "De",
    "del",
    "della",
    "di",
    "Di",
    "da",
    "van",
    "Van",
    "von",
    "der",
    "den",
    "la",
    "La",
    "le",
    "Le",
    "bin",
    "ibn",
    "al",
    "Al",
    "el",
    "El",
)
_PARTICLE = "|".join(re.escape(p) for p in sorted(NAME_PARTICLES, key=len, reverse=True))
# One surname: optional particle, then a capitalised word, optionally a second capitalised word.
_SP = r"[ \t]+"  # names never cross a line break
SURNAME = rf"(?:(?:{_PARTICLE}){_SP})?{_CAP_WORD}(?:{_SP}{_CAP_WORD})?"
TITLES = r"Dr\.?|Doctor|Prof\.?|Professor|Nurse|Mr\.?|Mrs\.?|Ms\.?|Miss|Mx\.?|Fr\.?|Sr\.?|Sister|Rev\.?"
# Words that follow "Dear"/"Hi" but are not names.
_NOT_NAMES = {
    "All",
    "Colleague",
    "Colleagues",
    "Team",
    "Everyone",
    "Sir",
    "Madam",
    "Sir/Madam",
    "Doctor",
    "There",
    "Folks",
    "Both",
    "Again",
    "Friends",
}
# Words that look like name parts but must not be spread to other mentions.
_NOT_NAME_PARTS = {
    "Street",
    "Road",
    "Avenue",
    "Lane",
    "Hospital",
    "Clinic",
    "Centre",
    "Center",
    "Practice",
    "Health",
    "Ireland",
    "Dublin",
    "The",
    "And",
    "Of",
    "St",
    "Saint",
    "Mr",
    "Mrs",
    "Ms",
    "Dr",
    "Prof",
    "Nurse",
}


class _RegexRecognizer(EntityRecognizer):
    """Reports group 1 of each match (or the whole match), optionally filtered."""

    def __init__(self, entity: str, pattern: str, score: float, name: str) -> None:
        self._regex = re.compile(pattern)
        self._score = score
        super().__init__(supported_entities=[entity], name=name)

    def load(self) -> None:
        pass

    def keep(self, text: str) -> bool:
        return True

    def analyze(
        self, text: str, entities: list[str], nlp_artifacts: NlpArtifacts | None = None
    ) -> list[RecognizerResult]:
        entity = self.supported_entities[0]
        if entities and entity not in entities:
            return []
        results = []
        for m in self._regex.finditer(text):
            group = 1 if m.re.groups else 0
            if m.group(group) and self.keep(m.group(group)):
                results.append(RecognizerResult(entity, m.start(group), m.end(group), self._score))
        return results


def first_name_recognizer() -> _RegexRecognizer:
    """Known first names, plus the surname that follows. Catches names spaCy misses."""
    names = load_place_list("first_names.txt")
    ambiguous = load_place_list("first_names_ambiguous.txt")
    plain = "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))
    word_like = "|".join(re.escape(n) for n in sorted(ambiguous, key=len, reverse=True))
    pattern = (
        rf"(?<![\w'’-])((?:{plain})(?:{_SP}{SURNAME})?"  # Siobhán, Siobhán Ní Bhriain
        rf"|(?:{word_like}){_SP}{SURNAME})(?![\w'’-])"  # Grace Hill, but not "grace" or "Grace" alone
    )
    return _RegexRecognizer("PERSON", pattern, score=0.7, name="FirstNameRecognizer")


def titled_name_recognizer() -> _RegexRecognizer:
    """A title followed by a name: "Dr Byrne", "Nurse Ó Súilleabháin", "Ms Rodríguez"."""
    pattern = rf"\b(?:{TITLES}){_SP}((?:{_CAP_WORD}{_SP})?{SURNAME})(?![\w'’-])"
    return _RegexRecognizer("PERSON", pattern, score=0.8, name="TitledNameRecognizer")


def irish_surname_recognizer() -> _RegexRecognizer:
    """Irish particle surnames ("Ní Bhriain", "Mac Giolla Phádraig") with any first name before them."""
    particles = r"Mac an|Nic an|Mhic an|Ní|Nic|Mac|Ó|Uí|Bean Uí"
    pattern = rf"((?:{_CAP_WORD}{_SP})?(?:{particles}){_SP}{_CAP_WORD}(?:{_SP}{_CAP_WORD})?)(?![\w'’-])"
    return _RegexRecognizer("PERSON", pattern, score=0.75, name="IrishSurnameRecognizer")


class _GreetingRecognizer(_RegexRecognizer):
    def keep(self, text: str) -> bool:
        return text.split()[0] not in _NOT_NAMES


def greeting_recognizer() -> _RegexRecognizer:
    """The name after "Dear", "Hi" or "Hello" at the start of a message: "Hi Walsh,"."""
    greetings = r"Dear|Hi|Hello|Hey|Morning|Afternoon"
    pattern = rf"\b(?:{greetings}){_SP}((?:{_CAP_WORD}{_SP})?{SURNAME})(?=[ \t]*[,!\n:]|\s*$)"
    return _GreetingRecognizer("PERSON", pattern, score=0.75, name="GreetingRecognizer")


def lenient_phone_recognizer() -> _RegexRecognizer:
    """Phone-shaped numbers that Presidio's strict validator rejects (e.g. unassigned US area codes)."""
    pattern = (
        r"(?<![\w-])(\(\d{3}\)\s?\d{3}[-\s]\d{4}"  # (415) 555-0132
        r"|\d{3}[-.]\d{3}[-.]\d{4}"  # 415-555-0132
        r"|\+\d{1,3}[\s-]?\(?\d{1,4}\)?(?:[\s-]?\d{2,4}){2,4})(?![\w-])"  # +353 87 123 4567
    )
    return _RegexRecognizer("PHONE_NUMBER", pattern, score=0.65, name="LenientPhoneRecognizer")


def name_parts(name: str) -> list[str]:
    """Words in a detected name that are worth searching for elsewhere in the text."""
    particles = set(NAME_PARTICLES)
    parts = []
    for word in re.split(r"\s+", name.strip()):
        word = word.strip(".,;:()")
        if len(word) < 2 or word in particles or word in _NOT_NAME_PARTS or not word[0].isupper():
            continue
        parts.append(word)
    return parts


# Words and phrases that are almost always followed by a person's name in clinical writing.
_NAME_CUES = (
    r"Client|Patient|Pt|Name|Service user|Resident|Re|Regarding|Attn|Invoice for|Receipt for"
    r"|review for|appointment for|referral for|letter for|session with|consult with|spoke with|spoke to"
    r"|met with|seen with|about|saw|see|called|phoned|emailed|texted|told|asked|thanked"
)
# Capitalised phrases after a cue that are clinical, not people.
_NOT_PERSON_PHRASES = {
    "Type",
    "Crohn's",
    "Crohn",
    "Parkinson's",
    "Alzheimer's",
    "Huntington's",
    "Hodgkin's",
    "Down",
    "Asperger's",
    "Cognitive",
    "Dialectical",
    "Acceptance",
    "Eye",
    "Emotionally",
    "Mental",
    "Social",
    "Child",
    "Adult",
    "Speech",
    "Occupational",
    "Physical",
    "General",
    "Primary",
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
    "January",
    "February",
    "March",
    "April",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
    "The",
    "This",
    "That",
    "Her",
    "His",
    "Their",
    "Our",
}


class _CuedNameRecognizer(_RegexRecognizer):
    def keep(self, text: str) -> bool:
        return text.split()[0] not in _NOT_PERSON_PHRASES


def cued_name_recognizer() -> _RegexRecognizer:
    """Two capitalised words straight after a cue such as "Client:" or "review for".

    Works for names Haven has never seen, because it relies on where the name
    sits, not on the name itself.
    """
    pattern = rf"\b(?:{_NAME_CUES})[ \t]*:?{_SP}({_CAP_WORD}{_SP}{SURNAME})(?![\w'’-])"
    return _CuedNameRecognizer("PERSON", pattern, score=0.7, name="CuedNameRecognizer")


# --- Dates ------------------------------------------------------------------

_MONTH_NAMES = (
    r"Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?"
    r"|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?"
)


def date_recognizer() -> _RegexRecognizer:
    """Written-out and numeric dates that spaCy sometimes misses ("7 June 1954", "March 20, 1974").

    Bare years and durations are deliberately not matched, and is_identifying_date
    in the sanitiser still makes the final call.
    """
    day = r"(?:[12]\d|3[01]|0?[1-9])(?:st|nd|rd|th)?"
    year = r"(?:19|20)\d{2}"
    pattern = (
        rf"(?<![\w/.-])("
        rf"{day}(?:{_SP}of)?{_SP}(?:{_MONTH_NAMES})\.?(?:,?{_SP}{year})?"  # 7 June 1954, 3rd of May
        rf"|(?:{_MONTH_NAMES})\.?{_SP}{day}(?:,?{_SP}{year})?"  # March 20, 1974
        rf"|(?:{_MONTH_NAMES})\.?{_SP}{year}"  # March 2021
        rf"|\d{{1,2}}[/.-]\d{{1,2}}[/.-](?:\d{{4}}|\d{{2}})"  # 04/12/1982, 4.12.82
        rf"|{year}-\d{{2}}-\d{{2}}"  # 1982-12-04
        rf")(?![\w/-])"
    )
    return _RegexRecognizer("DATE_TIME", pattern, score=0.8, name="HavenDateRecognizer")
