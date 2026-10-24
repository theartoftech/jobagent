from __future__ import annotations

import re


LINKEDIN_CONNECTION_NOTE_MAX_CHARS = 299


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def shorten_text(value: str, max_chars: int) -> str:
    if len(value) <= max_chars:
        return value
    if max_chars < 4:
        raise ValueError("max_chars must allow an ellipsis")
    clipped = value[: max_chars - 3].rstrip()
    word_boundary = clipped.rfind(" ")
    if word_boundary >= max_chars // 2:
        clipped = clipped[:word_boundary].rstrip()
    return f"{clipped}..."


def build_linkedin_connection_note(
    *,
    company: str,
    role: str,
    contact_name: str = "",
) -> str:
    clean_company = normalize_text(company)
    clean_role = normalize_text(role)
    if not clean_company:
        raise ValueError("company is required for a LinkedIn connection note")
    if not clean_role:
        raise ValueError("role is required for a LinkedIn connection note")

    clean_contact = normalize_text(contact_name)
    first_name = shorten_text(clean_contact.split()[0], 40) if clean_contact else ""
    greeting = f"Hi {first_name}," if first_name else "Hi,"

    candidates = [
        f"{greeting} I'm interested in the {clean_role} role at {clean_company}. I'd appreciate connecting and learning more about the team.",
        f"{greeting} I'm interested in the {shorten_text(clean_role, 90)} role at {shorten_text(clean_company, 60)}. I'd appreciate connecting.",
    ]
    for note in candidates:
        if len(note) <= LINKEDIN_CONNECTION_NOTE_MAX_CHARS:
            return note
    raise ValueError("Unable to build a LinkedIn connection note under 300 characters")
