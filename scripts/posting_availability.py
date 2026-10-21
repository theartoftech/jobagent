"""Availability gates shared by discovery and strict review."""
from __future__ import annotations

import datetime as dt
import re


def posting_availability_blockers(
    *, verification_status: str, description: str, as_of: dt.date,
) -> list[str]:
    blockers: list[str] = []
    if verification_status and verification_status != "official_live":
        blockers.append(f"official posting availability not verified: {verification_status}")
    match = re.search(r"\b(?:anticipated\s+)?end date\s*:\s*(\d{4}-\d{2}-\d{2})\b", description, re.IGNORECASE)
    if match:
        try:
            end_date = dt.date.fromisoformat(match.group(1))
        except ValueError:
            # Invalid source text is not evidence that a posting has closed.
            return blockers
        day_before_cutoff = "day before the listed end date" in description.casefold()
        if end_date < as_of or (day_before_cutoff and end_date == as_of):
            blockers.append(f"official posting end date has passed: {end_date.isoformat()}")
    return blockers
