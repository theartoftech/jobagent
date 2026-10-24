from __future__ import annotations

import html
import re
from collections.abc import Sequence


PLATFORM_ALIASES: dict[str, tuple[str, ...]] = {
    "guidewire": ("guidewire", "billingcenter", "policycenter", "claimcenter", "gosu"),
}
DELIVERY_ACTION = re.compile(
    r"\b(?:configur\w*|customiz\w*|develop\w*|extend\w*|extension|implement\w*|"
    r"upgrad\w*|maintain\w*|support\w*|design\w*|build\w*|own\w*)\b"
)
OPTIONAL_CONTEXT = re.compile(
    r"\b(?:optional|preferred|familiarity|exposure|bonus|nice to have|a plus|not required|if needed)\b"
)
EXPLICIT_TASK = re.compile(
    r"^(?:you will |responsible for )?(?:configur\w*|customiz\w*|develop\w*|"
    r"extend\w*|implement\w*|upgrad\w*|maintain\w*|support\w*|design\w*|build\w*|own\w*)\b"
)


def unsupported_primary_platforms(
    *, title: str, description: str, unsupported_platforms: Sequence[str]
) -> list[str]:
    """Identify a configured platform that defines the role's delivery work."""
    if isinstance(unsupported_platforms, (str, bytes)):
        raise ValueError("Unsupported primary platforms must be a sequence of platform names")
    normalized_title = " ".join(title.casefold().split())
    body = html.unescape(re.sub(r"<[^>]+>", "\n", description)).casefold()
    clauses = [" ".join(part.split()) for part in re.split(r"[.!?;\n]+", body)]
    blockers: list[str] = []
    for platform in unsupported_platforms:
        if not isinstance(platform, str) or not platform.strip():
            raise ValueError("Unsupported primary platforms must be nonempty strings")
        key = " ".join(platform.casefold().split())
        aliases = PLATFORM_ALIASES.get(key, (key,))
        pattern = re.compile(
            r"(?<![a-z0-9])(?:"
            + "|".join(re.escape(alias) for alias in aliases)
            + r")(?![a-z0-9])"
        )
        platform_clauses = [clause for clause in clauses if pattern.search(clause)]
        delivery_clauses = [
            clause for clause in platform_clauses
            if DELIVERY_ACTION.search(clause) and not OPTIONAL_CONTEXT.search(clause)
        ]
        if pattern.search(normalized_title) or any(
            EXPLICIT_TASK.search(clause) or len(platform_clauses) >= 2
            for clause in delivery_clauses
        ):
            if platform not in blockers:
                blockers.append(platform)
    return blockers
