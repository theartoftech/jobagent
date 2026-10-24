from __future__ import annotations

import re
from collections.abc import Iterable


def required_unsupported_spoken_languages(
    text: str,
    unsupported_languages: Iterable[str],
) -> list[str]:
    """Return explicitly required spoken languages the candidate does not have."""
    lowered = " ".join(text.casefold().split())
    matches: list[str] = []
    for language in unsupported_languages:
        normalized = language.strip()
        if not normalized:
            raise ValueError("Unsupported required language entries must be non-empty strings")
        escaped = re.escape(normalized.casefold())
        patterns = (
            rf"\b{escaped}[- ]speaking\b",
            rf"\bmust\s+(?:speak|read|write|communicate in)\s+{escaped}\b",
            rf"\b(?:required|mandatory|must have|must be|requires?)\b.{{0,80}}\b{escaped}\b",
            rf"\b{escaped}\b.{{0,80}}\b(?:is required|is mandatory|required fluency|mandatory fluency)\b",
            rf"\b(?:fluent|native|professional proficiency|business proficiency)\s+(?:in\s+)?{escaped}\b.{{0,40}}\b(?:required|mandatory|must)\b",
        )
        if any(re.search(pattern, lowered) for pattern in patterns):
            matches.append(normalized)
    return matches
