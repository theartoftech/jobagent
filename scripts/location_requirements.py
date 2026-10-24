from __future__ import annotations

import re
from collections.abc import Iterable


def hybrid_location_outside_allowed_metro(
    *,
    location: str,
    work_model: str,
    allowed_metro_tokens: Iterable[str],
) -> bool:
    """Return true when a hybrid or flex role is not in an allowed metro."""
    if re.search(r"\b(?:hybrid|flex)\b", work_model.casefold()) is None:
        return False
    normalized_location = " ".join(location.casefold().split())
    if re.search(r"\b(?:remote|virtual|work at home)\b", normalized_location):
        return False
    allowed_locations: list[str] = []
    for token in allowed_metro_tokens:
        normalized_token = " ".join(token.casefold().split())
        if not normalized_token:
            raise ValueError("Hybrid metro location tokens must be non-empty strings")
        allowed_locations.append(normalized_token)
    if not allowed_locations:
        return False
    return not any(token in normalized_location for token in allowed_locations)


def explicit_nonremote_location_outside_allowed_metro(
    *,
    location: str,
    work_model: str,
    allowed_metro_tokens: Iterable[str],
) -> bool:
    """Reject explicit physical locations when remote status was not established."""
    normalized_model = " ".join(work_model.casefold().split())
    if re.search(r"\b(?:remote|virtual|work at home|hybrid|flex)\b", normalized_model):
        return False

    normalized_location = " ".join(location.casefold().split())
    if not normalized_location or re.search(r"\b(?:remote|virtual|work at home)\b", normalized_location):
        return False
    if normalized_location in {
        "united states",
        "united states of america",
        "usa",
        "us",
        "u.s.",
    }:
        return False

    allowed_locations: list[str] = []
    for token in allowed_metro_tokens:
        normalized_token = " ".join(token.casefold().split())
        if not normalized_token:
            raise ValueError("Metro location tokens must be non-empty strings")
        allowed_locations.append(normalized_token)
    if not allowed_locations:
        return False
    return not any(token in normalized_location for token in allowed_locations)
