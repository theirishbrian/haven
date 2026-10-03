"""Generate fictional clinical notes with every identifier recorded.

Everything here is made up. Numbers that carry a checksum (PPSN, NHS, IBAN)
are generated to pass it, because a real recogniser would reject an invalid one
and the test would then be measuring the wrong thing.
"""

from __future__ import annotations

import random
import string
from dataclasses import dataclass, field

# --- Pools -------------------------------------------------------------------

FIRST_NAMES = [
    # Irish, with and without fadas
    "Siobhán",
    "Siobhan",
    "Seán",
    "Aoife",
    "Niamh",
    "Ciarán",
    "Oisín",
    "Pádraig",
    "Gráinne",
    "Eoin",
    "Caoimhe",
    "Róisín",
    "Cian",
    "Dervla",
    "Fionnuala",
    "Tadhg",
    # English and American
    "James",
    "Emily",
    "Michael",
    "Sarah",
    "David",
    "Jessica",
    "Thomas",
    "Hannah",
    "Daniel",
    "Laura",
    # Hispanic
    "María",
    "José",
    "Alejandro",
    "Lucía",
    "Carmen",
    # African American
    "Jamal",
    "Aaliyah",
    "DeShawn",
    "Imani",
    # South Asian
    "Priya",
    "Arjun",
    "Rohan",
    "Ananya",
    # East Asian
    "Wei",
    "Mei",
    "Hiroshi",
    "Yuki",
    "Min-jun",
    # West African
    "Chiamaka",
    "Oluwaseun",
    "Kwame",
    "Adaeze",
    # Polish and Lithuanian (large communities in Ireland)
    "Katarzyna",
    "Piotr",
    "Agnieszka",
    "Tomasz",
    "Rūta",
    # Arabic
    "Fatima",
    "Omar",
    "Yusuf",
    "Layla",
]
LAST_NAMES = [
    "O'Sullivan",
    "Murphy",
    "Kavanagh",
    "Ní Bhriain",
    "Mac Giolla Phádraig",
    "McCarthy",
    "Ó Súilleabháin",
    "Fitzgerald",
    "Doherty",
    "Byrne",
    "Walsh",
    "Quinlan",
    "Smith",
    "Johnson",
    "Williams",
    "Thompson",
    "Anderson",
    "Clarke",
    "García",
    "Hernández",
    "Rodríguez",
    "Washington",
    "Jefferson",
    "Patel",
    "Sharma",
    "Iyer",
    "Nguyen",
    "Kim",
    "Tanaka",
    "Chen",
    "Okafor",
    "Adeyemi",
    "Mensah",
    "Kowalski",
    "Nowak",
    "Wiśniewska",
    "Kazlauskas",
    "Haddad",
    "Al-Masri",
]
# Held out: none of these appear in Haven's name lists. They measure how Haven
# does on names it has never been told about, which is the honest number.
UNSEEN_FIRST_NAMES = [
    "Ailbhe",
    "Bláthnaid",
    "Clodagh",
    "Fiadh",
    "Sadhbh",
    "Ríona",
    "Tiernan",
    "Dara",
    "Zainab",
    "Thandiwe",
    "Bogdan",
    "Ilse",
    "Nnamdi",
    "Ayodele",
    "Ishaan",
    "Meera",
    "Haruto",
    "Sakura",
    "Mateo",
    "Ximena",
    "Kateryna",
    "Oleksandr",
    "Dmitri",
    "Freya",
    "Rhys",
    "Callum",
]
UNSEEN_LAST_NAMES = [
    "Breathnach",
    "Mulcahy",
    "Treacy",
    "Nwosu",
    "Abiodun",
    "Kovalenko",
    "Lindqvist",
    "Okonkwo",
    "Ferreira",
    "Gallagher",
    "Ahern",
    "Moriarty",
    "Ó Cearbhaill",
    "Mac an tSaoir",
    "Szabó",
    "Yamamoto",
    "Petrov",
]
# First names that are also ordinary words. These are hard for any model.
WORD_FIRST_NAMES = ["Grace", "Hope", "Rose", "Summer", "Will", "Faith", "Joy", "Rich", "Mark", "Iris"]
WORD_LAST_NAMES = ["Hill", "Baker", "Field", "Lane", "Bell", "Brown", "Stone", "Wood", "King", "Moss"]

CLINICIAN_TITLES = ["Dr", "Dr.", "Prof.", "Nurse", "Ms", "Mr"]

STREETS = [
    "Ranelagh Road",
    "Main Street",
    "Oak Avenue",
    "Church Lane",
    "Seaview Terrace",
    "Elm Drive",
    "Mill Court",
    "Castle Park",
    "Harbour View",
    "Maple Street",
    "Pine Crescent",
    "College Green",
]
IE_TOWNS = [
    "Swords",
    "Bray",
    "Navan",
    "Tralee",
    "Mullingar",
    "Ennis",
    "Cork",
    "Galway",
    "Tallaght",
    "Athlone",
]
US_PLACES = [("Springfield", "IL"), ("Austin", "TX"), ("Portland", "OR"), ("Albany", "NY"), ("Tucson", "AZ")]
UK_PLACES = ["Leeds", "Bristol", "Manchester", "Cardiff", "Glasgow"]

MONTHS = [
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
]

# Clinical content that must survive scrubbing.
KEEP_PHRASES = [
    "sertraline 50mg",
    "fluoxetine 20mg",
    "metformin 500mg twice daily",
    "Vitamin D3 1000 IU",
    "PHQ-9 score of 14",
    "GAD-7 of 11",
    "BMI 31.2",
    "HbA1c 58 mmol/mol",
    "for three weeks",
    "over the past six months",
    "twice daily",
    "at 8am",
    "since 2019",
    "low mood",
    "poor sleep",
    "panic attacks",
    "type 2 diabetes",
    "coeliac disease",
    "cognitive behavioural therapy",
    "flat affect",
    "weight loss of 4kg",
    "blood pressure 132/84",
]


# --- Checksummed numbers ------------------------------------------------------


def ppsn(rng: random.Random) -> str:
    digits = "".join(rng.choice(string.digits) for _ in range(7))
    total = sum(int(d) * w for d, w in zip(digits, range(8, 1, -1), strict=True))
    return digits + "WABCDEFGHIJKLMNOPQRSTUV"[total % 23]


def nhs_number(rng: random.Random) -> str:
    while True:
        digits = [rng.randint(0, 9) for _ in range(9)]
        check = 11 - sum(d * w for d, w in zip(digits, range(10, 1, -1), strict=True)) % 11
        if check == 11:
            check = 0
        if check != 10:
            s = "".join(map(str, digits)) + str(check)
            return f"{s[:3]} {s[3:6]} {s[6:]}"


def ie_iban(rng: random.Random) -> str:
    bban = "AIBK" + "".join(rng.choice(string.digits) for _ in range(14))
    rearranged = bban + "IE00"
    numeric = "".join(str(int(c, 36)) for c in rearranged)
    check = 98 - int(numeric) % 97
    iban = f"IE{check:02d}{bban}"
    return " ".join(iban[i : i + 4] for i in range(0, len(iban), 4))


def us_ssn(rng: random.Random) -> str:
    area = rng.choice([n for n in range(1, 900) if n != 666])
    return f"{area:03d}-{rng.randint(1, 99):02d}-{rng.randint(1, 9999):04d}"


def eircode(rng: random.Random) -> str:
    routing = rng.choice(["D06", "D04", "A94", "T12", "H91", "V92", "K67", "A98"])
    tail_chars = "0123456789ACDEFHKNPRTVWXY"
    return routing + " " + "".join(rng.choice(tail_chars) for _ in range(4))


# --- Notes --------------------------------------------------------------------


@dataclass
class Planted:
    kind: str  # PERSON, DATE, PHONE, ...
    value: str
    parts: list[str]  # substrings that count as a leak if they survive
    hard: bool = False  # deliberately difficult case


@dataclass
class Note:
    style: str
    text: str
    planted: list[Planted] = field(default_factory=list)
    keep: list[str] = field(default_factory=list)


class NoteBuilder:
    def __init__(self, rng: random.Random) -> None:
        self.rng = rng
        self.unseen = False

    # Values ---------------------------------------------------------------

    def person(self, hard: bool = False) -> Planted:
        if hard:
            first, last = self.rng.choice(WORD_FIRST_NAMES), self.rng.choice(WORD_LAST_NAMES)
        elif self.unseen:
            first, last = self.rng.choice(UNSEEN_FIRST_NAMES), self.rng.choice(UNSEEN_LAST_NAMES)
        else:
            first, last = self.rng.choice(FIRST_NAMES), self.rng.choice(LAST_NAMES)
        name = f"{first} {last}"
        return Planted("PERSON", name, [first, *last.split()], hard=hard)

    def clinician(self) -> Planted:
        title = self.rng.choice(CLINICIAN_TITLES)
        last = self.rng.choice(UNSEEN_LAST_NAMES if self.unseen else LAST_NAMES)
        return Planted("PERSON", f"{title} {last}", last.split())

    def dob(self) -> Planted:
        y, m, d = self.rng.randint(1940, 2008), self.rng.randint(1, 12), self.rng.randint(1, 28)
        fmt = self.rng.choice(["slash", "long", "iso", "us_long", "dots"])
        value = {
            "slash": f"{d:02d}/{m:02d}/{y}",
            "long": f"{d} {MONTHS[m - 1]} {y}",
            "iso": f"{y}-{m:02d}-{d:02d}",
            "us_long": f"{MONTHS[m - 1]} {d}, {y}",
            "dots": f"{d}.{m}.{y}",
        }[fmt]
        return Planted("DATE", value, [value])

    def appointment_date(self) -> Planted:
        d, m = self.rng.randint(1, 28), self.rng.randint(1, 12)
        value = self.rng.choice([f"{d} {MONTHS[m - 1]}", f"{MONTHS[m - 1]} {d}", f"{d:02d}/{m:02d}/2026"])
        return Planted("DATE", value, [value])

    def phone(self) -> Planted:
        value = self.rng.choice(
            [
                f"08{self.rng.choice('3567')} {self.rng.randint(100, 999)} {self.rng.randint(1000, 9999)}",
                f"+353 8{self.rng.choice('3567')} {self.rng.randint(100, 999)} "
                f"{self.rng.randint(1000, 9999)}",
                f"(01) {self.rng.randint(200, 999)} {self.rng.randint(1000, 9999)}",
                f"({self.rng.randint(201, 989)}) {self.rng.randint(200, 999)}-{self.rng.randint(1000, 9999)}",
                f"07700 900{self.rng.randint(100, 999)}",
            ]
        )
        return Planted("PHONE", value, [value])

    def email(self, person: Planted) -> Planted:
        first = person.parts[0].lower().translate(str.maketrans("áéíóúū", "aeiouu"))
        last = person.parts[-1].lower().replace("'", "").translate(str.maketrans("áéíóúś", "aeious"))
        domain = self.rng.choice(["gmail.com", "outlook.ie", "yahoo.com", "eircom.net", "icloud.com"])
        value = f"{first}.{last}@{domain}"
        return Planted("EMAIL", value, [value])

    def street_address(self) -> Planted:
        value = f"{self.rng.randint(1, 240)} {self.rng.choice(STREETS)}"
        return Planted("ADDRESS", value, [value])

    def ie_town(self) -> Planted:
        value = self.rng.choice(IE_TOWNS)
        return Planted("LOCATION", value, [value])

    def mrn(self) -> tuple[str, Planted]:
        label = self.rng.choice(["MRN:", "MRN", "Hospital No.", "Patient ID:", "Chart #"])
        number = self.rng.choice(
            [str(self.rng.randint(10_000_000, 99_999_999)), f"H{self.rng.randint(100000, 999999)}"]
        )
        return label, Planted("MRN", number, [number])

    def id_number(self, kind: str) -> Planted:
        value = {"PPSN": ppsn, "NHS": nhs_number, "IBAN": ie_iban, "SSN": us_ssn, "POSTCODE": eircode}[kind](
            self.rng
        )
        return Planted(kind, value, [value])

    def keep(self, n: int) -> list[str]:
        return self.rng.sample(KEEP_PHRASES, n)

    # Templates --------------------------------------------------------------

    def build(self, style: str) -> Note:
        return getattr(self, f"_{style}")()

    def _unseen_names(self) -> Note:
        """A standard note, but every person has a name Haven has never been told about."""
        self.unseen = True
        try:
            template = self.rng.choice(
                ["session_note", "referral_letter", "dietitian_consult", "billing", "email_to_colleague"]
            )
            note = self.build(template)
        finally:
            self.unseen = False
        for p in note.planted:
            if p.kind == "PERSON":
                p.kind = "PERSON_UNSEEN"
        note.style = "unseen_names"
        return note

    def _session_note(self) -> Note:
        p, dob, clin = self.person(), self.dob(), self.clinician()
        label, mrn = self.mrn()
        k = self.keep(3)
        text = (
            f"Session note. Client: {p.value}, DOB {dob.value}, {label} {mrn.value}. "
            f"Seen by {clin.value}. Reports {k[0]} {k[1]}. Plan: continue {k[2]}."
        )
        return Note("session_note", text, [p, dob, clin, mrn], k)

    def _intake_form(self) -> Note:
        p, dob, ph = self.person(), self.dob(), self.phone()
        em, addr, town, ec = self.email(p), self.street_address(), self.ie_town(), self.id_number("POSTCODE")
        pps = self.id_number("PPSN")
        k = self.keep(2)
        text = (
            f"Name: {p.value}\nDate of birth: {dob.value}\nAddress: {addr.value}, {town.value}, {ec.value}\n"
            f"Phone: {ph.value}\nEmail: {em.value}\nPPSN: {pps.value}\nPresenting issue: {k[0]}, {k[1]}."
        )
        return Note("intake_form", text, [p, dob, addr, town, ec, ph, em, pps], k)

    def _referral_letter(self) -> Note:
        p, dob, clin, town = self.person(), self.dob(), self.clinician(), self.ie_town()
        k = self.keep(3)
        text = (
            f"Dear Colleague,\n\nI would be grateful if you could see {p.value} (DOB {dob.value}) from "
            f"{town.value}, who has had {k[0]} {k[1]}. Current medication includes {k[2]}.\n\n"
            f"Kind regards,\n{clin.value}"
        )
        return Note("referral_letter", text, [p, dob, town, clin], k)

    def _dietitian_consult(self) -> Note:
        p, appt = self.person(), self.appointment_date()
        k = self.keep(3)
        first = Planted("PERSON", p.parts[0], [p.parts[0]])
        text = (
            f"Dietetic review for {p.value} on {appt.value}. {first.value} reports {k[0]}. "
            f"Noted {k[1]} and {k[2]}. Review in four weeks."
        )
        return Note("dietitian_consult", text, [p, appt, first], k)

    def _us_record(self) -> Note:
        p, dob, ssn, ph = self.person(), self.dob(), self.id_number("SSN"), self.phone()
        n = self.rng.randint(10, 9999)
        city, state = self.rng.choice(US_PLACES)
        zip_code = f"{self.rng.randint(10000, 99999)}"
        addr = Planted("ADDRESS", f"{n} {self.rng.choice(STREETS)}", [])
        addr.parts = [addr.value]
        city_p = Planted("LOCATION", city, [city])
        zip_p = Planted("POSTCODE", zip_code, [zip_code])
        k = self.keep(2)
        text = (
            f"Patient {p.value}, DOB {dob.value}, SSN {ssn.value}. Lives at {addr.value}, {city}, "
            f"{state} {zip_code}. Contact {ph.value}. History of {k[0]}; {k[1]}."
        )
        return Note("us_record", text, [p, dob, ssn, addr, city_p, zip_p, ph], k)

    def _uk_record(self) -> Note:
        p, dob, nhs = self.person(), self.dob(), self.id_number("NHS")
        city = Planted("LOCATION", self.rng.choice(UK_PLACES), [])
        city.parts = [city.value]
        k = self.keep(2)
        text = (
            f"{p.value} ({dob.value}), NHS number {nhs.value}, from {city.value}. "
            f"Presents with {k[0]}, {k[1]}."
        )
        return Note("uk_record", text, [p, dob, nhs, city], k)

    def _billing(self) -> Note:
        p, iban = self.person(), self.id_number("IBAN")
        k = self.keep(1)
        text = f"Invoice for {p.value}: 6 sessions of {k[0]}. Refund to IBAN {iban.value}."
        return Note("billing", text, [p, iban], k)

    def _email_to_colleague(self) -> Note:
        p, clin, em = self.person(), self.clinician(), None
        em = self.email(p)
        k = self.keep(2)
        text = (
            f"Hi {clin.value.split()[-1]},\n\nQuick one about {p.value} ({em.value}). "
            f"She mentioned {k[0]} again and {k[1]}. Can we discuss Thursday?\n\nThanks"
        )
        clin_last = Planted("PERSON", clin.value.split()[-1], clin.parts)
        return Note("email_to_colleague", text, [p, em, clin_last], k)

    def _word_name(self) -> Note:
        p, dob = self.person(hard=True), self.dob()
        k = self.keep(2)
        text = f"Client {p.value}, born {dob.value}, attended for {k[0]}. {p.value} described {k[1]}."
        return Note("word_name", text, [p, dob], k)

    def _informal_lowercase(self) -> Note:
        p = self.person()
        lower = Planted("PERSON", p.value.lower(), [part.lower() for part in p.parts], hard=True)
        k = self.keep(2)
        text = f"saw {lower.value} today, still {k[0]}. mentioned {k[1]}. will follow up next wk"
        return Note("informal_lowercase", text, [lower], k)


STYLES = [
    "session_note",
    "intake_form",
    "referral_letter",
    "dietitian_consult",
    "us_record",
    "uk_record",
    "billing",
    "email_to_colleague",
    "word_name",
    "informal_lowercase",
    "unseen_names",
]


def generate(n_per_style: int = 25, seed: int = 2026) -> list[Note]:
    rng = random.Random(seed)
    builder = NoteBuilder(rng)
    return [builder.build(style) for style in STYLES for _ in range(n_per_style)]
