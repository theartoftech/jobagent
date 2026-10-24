"""Candidate-owned requirements shared by discovery and strict review."""
from __future__ import annotations

import re
from typing import Any


def required_term(description: str, term: str) -> bool:
    escaped = re.escape(term.casefold())
    return bool(re.search(rf"\b(?:required|must have|requires)\b[^.\n]{{0,80}}(?<!\w){escaped}(?!\w)|(?<!\w){escaped}(?!\w)[^.\n]{{0,40}}\b(?:required|mandatory)\b", description.casefold()))


COUNTRY_ALIASES: tuple[frozenset[str], ...] = (
    frozenset({"us", "usa", "u.s.", "u.s.a.", "united states", "united states of america"}),
    frozenset({"uk", "u.k.", "united kingdom", "great britain"}),
)
GLOBAL_LOCATIONS = ("worldwide", "anywhere", "global")


def place_matches(text: str, place: str) -> bool:
    return bool(re.search(rf"(?<!\w){re.escape(place)}(?!\w)", text, re.IGNORECASE))


def country_names(countries: list[str]) -> set[str]:
    names = {country.strip().casefold() for country in countries}
    for aliases in COUNTRY_ALIASES:
        if names & aliases:
            names.update(aliases)
    return names


def candidate_location_checks(location: str, work_model: str, profile: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Separate explicit mismatches from unknown eligibility without assuming a country."""
    blockers: list[str] = []
    warnings: list[str] = []
    model = work_model.strip().casefold()
    if model == "flex":
        model = "hybrid"
    allowed = profile.get("work_models", [])
    known_model = model in {"remote", "hybrid", "onsite"}
    if allowed and known_model and model not in allowed:
        blockers.append("work model outside candidate preferences")
    if allowed and not known_model:
        warnings.append("work model requires confirmation")
    text = " ".join(location.casefold().split())
    metro = profile.get("hybrid_allowed_metro_tokens", [])
    countries = country_names(profile.get("countries", []))
    # A country alone cannot prove that an office is within a particular commute.
    broad_locations = countries | set(GLOBAL_LOCATIONS) | set().union(*COUNTRY_ALIASES)
    if model != "remote" and metro:
        if not text or text in broad_locations:
            warnings.append("physical location requires confirmation")
        elif not any(place_matches(text, str(token).strip()) for token in metro):
            blockers.append("physical location outside candidate commute area")
        elif not known_model:
            warnings.append("physical location requires confirmation")
    if model == "remote" and countries:
        if not any(place_matches(text, country) for country in countries) and not any(place_matches(text, term) for term in GLOBAL_LOCATIONS):
            warnings.append("remote country eligibility requires confirmation")
    return blockers, warnings
