"""Sanitiser tests. Every name, date and number here is made up."""

from __future__ import annotations

import socket

import pytest

from app.core.recognizers import ppsn_is_valid
from app.core.sanitizer import Sanitiser, TokenMap, is_identifying_date, restore


@pytest.fixture(scope="module")
def sanitiser() -> Sanitiser:
    return Sanitiser(regions=["us", "eu"])


def scrub(s: Sanitiser, text: str) -> tuple[str, TokenMap]:
    tm = TokenMap()
    return s.scrub(text, tm).text, tm


# --- Identifiers that must be removed ---------------------------------------

LEAKS = [
    ("John Smith attended today.", "John Smith"),
    ("DOB: 04/12/1982", "04/12/1982"),
    ("Next review on 14 March 2025.", "14 March 2025"),
    ("MRN: 00482913", "00482913"),
    ("Patient ID: AB-778812", "AB-778812"),
    ("Call her on 087 123 4567.", "087 123 4567"),
    ("Call him on (415) 555-0132.", "555-0132"),
    ("Email john.smith@gmail.com", "john.smith@gmail.com"),
    ("SSN 536-22-1457", "536-22-1457"),
    ("PPSN 1234567T", "1234567T"),
    ("NHS number 943 476 5919", "943 476 5919"),
    ("IBAN IE29 AIBK 9311 5212 3456 78", "IE29 AIBK 9311 5212 3456 78"),
    ("Lives at 14 Rathmines Road.", "14 Rathmines Road"),
    ("Address: 221B Baker St, London", "221B Baker St"),
    ("Eircode D06 F2X3", "D06 F2X3"),
    ("Postcode NW1 6XE", "NW1 6XE"),
    ("Springfield, IL 62704", "62704"),
    ("She moved to Galway last year.", "Galway"),
]


@pytest.mark.parametrize(("text", "secret"), LEAKS)
def test_identifier_is_removed(sanitiser: Sanitiser, text: str, secret: str) -> None:
    scrubbed, _ = scrub(sanitiser, text)
    assert secret not in scrubbed, scrubbed


# --- Clinical context that must survive -------------------------------------

KEEP = [
    "three weeks",
    "twice daily",
    "sertraline 50mg",
    "2019",
    "8am",
    "Take 3 tablets a day",
    "PHQ-9 score of 14",
]


def test_clinical_context_is_kept(sanitiser: Sanitiser) -> None:
    note = (
        "Mary O'Brien reports low mood for three weeks. Started sertraline 50mg twice daily "
        "in 2019, taken at 8am. Take 3 tablets a day. PHQ-9 score of 14."
    )
    scrubbed, _ = scrub(sanitiser, note)
    assert "Mary O'Brien" not in scrubbed
    for phrase in KEEP:
        assert phrase in scrubbed, f"{phrase!r} was scrubbed: {scrubbed}"


@pytest.mark.parametrize(
    ("text", "identifying"),
    [
        ("04/12/1982", True),
        ("2024-03-01", True),
        ("14 March 2025", True),
        ("March 2021", True),
        ("2019", False),
        ("three weeks ago", False),
        ("Monday", False),
        ("twice daily", False),
    ],
)
def test_date_filter(text: str, identifying: bool) -> None:
    assert is_identifying_date(text) is identifying


# --- Tokens -----------------------------------------------------------------


def test_same_person_gets_same_token_across_messages(sanitiser: Sanitiser) -> None:
    tm = TokenMap()
    first = sanitiser.scrub("John Smith is anxious.", tm).text
    second = sanitiser.scrub("Ask John Smith about sleep. Also see Aoife Byrne.", tm).text
    assert first.startswith("[PERSON_1]")
    assert "[PERSON_1]" in second
    assert "[PERSON_2]" in second


def test_entity_counts(sanitiser: Sanitiser) -> None:
    result = sanitiser.scrub("John Smith, DOB 04/12/1982, email js@example.com", TokenMap())
    assert result.entity_counts["PERSON"] == 1
    assert result.entity_counts["DATE"] == 1
    assert result.entity_counts["EMAIL"] == 1


def test_token_map_repr_hides_values() -> None:
    tm = TokenMap()
    tm.token_for("PERSON", "John Smith")
    assert "John" not in repr(tm)
    assert "John" not in str(tm)


# --- Restoring --------------------------------------------------------------


def test_round_trip(sanitiser: Sanitiser) -> None:
    note = "John Smith, DOB 04/12/1982, MRN: 00482913, lives at 14 Rathmines Road."
    scrubbed, tm = scrub(sanitiser, note)
    assert restore(scrubbed, tm) == note


@pytest.mark.parametrize("variant", ["[PERSON_1]", "[Person 1]", "[person-1]", "PERSON_1", "[ PERSON_1 ]"])
def test_restore_handles_rewritten_tokens(variant: str) -> None:
    tm = TokenMap()
    tm.token_for("PERSON", "John Smith")
    assert restore(f"Dear {variant},", tm) == "Dear John Smith,"


def test_restore_handles_multi_word_labels() -> None:
    tm = TokenMap()
    tm.token_for("MEDICAL_LICENSE", "BS1234567")
    assert restore("Licence: [Medical License 1]", tm) == "Licence: BS1234567"


def test_restore_leaves_unknown_tokens_alone() -> None:
    tm = TokenMap()
    tm.token_for("PERSON", "John Smith")
    assert restore("[PERSON_2] and [DATE_1]", tm) == "[PERSON_2] and [DATE_1]"


def test_restore_ignores_ordinary_words() -> None:
    tm = TokenMap()
    tm.token_for("PERSON", "John Smith")
    assert restore("Person 1 of 3 in the group", tm) == "Person 1 of 3 in the group"


# --- Regions ----------------------------------------------------------------


def test_us_only_skips_eu_identifiers() -> None:
    us_only = Sanitiser(regions=["us"])
    scrubbed, _ = scrub(us_only, "PPSN 1234567T")
    assert "1234567T" in scrubbed


def test_ppsn_checksum() -> None:
    assert ppsn_is_valid("1234567", "T")
    assert not ppsn_is_valid("1234567", "A")


# --- No network -------------------------------------------------------------


def test_scrubbing_makes_no_network_calls(sanitiser: Sanitiser, monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("Haven tried to open a network connection while scrubbing")

    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)
    scrubbed, _ = scrub(sanitiser, "Email jane.doe@hospital.ie or visit https://example.org/patient/123")
    assert "jane.doe@hospital.ie" not in scrubbed


# --- Irish places, names and repeat mentions --------------------------------


@pytest.mark.parametrize(
    ("text", "secret"),
    [
        ("Lives in Dublin 6, near the park.", "Dublin 6"),
        ("Moved from Co. Kerry last year.", "Kerry"),
        ("Originally from Cork, now in Swords.", "Cork"),
        ("Originally from Cork, now in Swords.", "Swords"),
        ("Address: Apartment 4, Bray.", "Apartment 4"),
        ("Seen with Siobhán Ní Bhriain today.", "Bhriain"),
        ("Referred by Nurse Ó Súilleabháin.", "Súilleabháin"),
        ("Letter from Prof. Mac an tSaoir.", "tSaoir"),
        ("Hi Walsh,\nThanks for the update.", "Walsh"),
        ("Dietetic review for Ailbhe Moriarty on Tuesday.", "Moriarty"),
        ("Call (793) 329-6832 after 5pm.", "329-6832"),
        ("DOB 7 June 1954.", "7 June 1954"),
    ],
)
def test_irish_and_contextual_identifiers(sanitiser: Sanitiser, text: str, secret: str) -> None:
    scrubbed, _ = scrub(sanitiser, text)
    assert secret not in scrubbed, scrubbed


def test_later_mentions_of_a_name_are_removed(sanitiser: Sanitiser) -> None:
    scrubbed, _ = scrub(sanitiser, "Client: Siobhán Kavanagh. Later Kavanagh said Siobhán slept badly.")
    assert "Kavanagh" not in scrubbed
    assert "Siobhán" not in scrubbed


def test_short_mention_shares_the_full_names_token(sanitiser: Sanitiser) -> None:
    scrubbed, tm = scrub(sanitiser, "Mark Byrne attended. Mark reported poor sleep.")
    assert scrubbed == "[PERSON_1] attended. [PERSON_1] reported poor sleep."
    assert restore(scrubbed, tm) == "Mark Byrne attended. Mark Byrne reported poor sleep."


def test_shared_surname_is_not_guessed(sanitiser: Sanitiser) -> None:
    scrubbed, _ = scrub(sanitiser, "Mark Byrne and Aoife Byrne attended. Byrne family history noted.")
    assert "[PERSON_3] family history" in scrubbed


def test_names_known_from_earlier_messages(sanitiser: Sanitiser) -> None:
    tm = TokenMap()
    sanitiser.scrub("Client: Ailbhe Moriarty, first session.", tm)
    second = sanitiser.scrub("Ailbhe says sleep is better.", tm).text
    assert second == "[PERSON_1] says sleep is better."


def test_overlapping_matches_are_merged_not_dropped(sanitiser: Sanitiser) -> None:
    # "Ennis" is a town and a surname. Neither half of the name may survive.
    scrubbed, _ = scrub(sanitiser, "Seen with Mary Ennis today.")
    assert "Mary" not in scrubbed
    assert "Ennis" not in scrubbed


@pytest.mark.parametrize(
    "text",
    [
        "Will review in two weeks. Mark the chart for follow up.",
        "Presents with low mood, GAD-7 of 11 and PHQ-9 score of 14.",
        "Started Vitamin D3 1000 IU daily.",
        "Referred for Cognitive Behavioural Therapy.",
        "Grace period for the appointment is ten minutes.",
        "Patient reports a flare of Crohn's Disease since last Monday.",
    ],
)
def test_clinical_language_is_left_alone(sanitiser: Sanitiser, text: str) -> None:
    scrubbed, _ = scrub(sanitiser, text)
    assert scrubbed == text
