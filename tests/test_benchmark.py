"""Fail the build if accuracy on the fictional benchmark drops.

The full benchmark is in benchmark/; this runs a smaller sample to keep CI quick.
"""

from __future__ import annotations

import pytest

from app.core.sanitizer import Sanitiser
from benchmark.notes import generate
from benchmark.run import run


@pytest.fixture(scope="module")
def results() -> dict:
    return run(generate(n_per_style=15), Sanitiser())


def rate(pair: list[int]) -> float:
    return pair[0] / pair[1]


def test_standard_identifiers(results: dict) -> None:
    assert rate(results["standard"]) >= 0.99


def test_names_haven_has_never_seen(results: dict) -> None:
    assert rate(results["by_kind"]["PERSON_UNSEEN"]) >= 0.95


def test_clinical_phrases_survive(results: dict) -> None:
    assert rate(results["keep"]) >= 0.99
