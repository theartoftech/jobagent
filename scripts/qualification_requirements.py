"""Qualification sections and explicitly supported alternatives."""
from __future__ import annotations

import html
import re
from collections.abc import Mapping, Sequence
from typing import Any


REQUIRED_HEADING = re.compile(
    r'\b(?:requirements\s*(?:&|and)\s*qualifications|required qualifications|'
    r'minimum qualifications|basic qualifications|qualifications and requirements|requirements\s*:|'
    r"experience you['’]ll need(?: to have)?)", re.IGNORECASE,
)
SECTION_END = re.compile(
    r'\b(?:preferred qualifications|preferred experience(?=\s*[:\n])|nice to have|bonus qualifications|'
    r'reporting relationship|skills\s*:|benefits\s*:|our purpose|about us|job posting end date|'
    r'experience that would be great to have)', re.IGNORECASE,
)


def qualification_text(value: str) -> str:
    text = re.sub(r'</?(?:p|div|li|ul|ol|h[1-6]|br)\b[^>]*>', '\n', html.unescape(value), flags=re.IGNORECASE)
    text = html.unescape(re.sub(r'<[^>]+>', ' ', text))
    return '\n'.join(' '.join(line.split()) for line in text.splitlines() if line.strip())


def term_pattern(term: str) -> str:
    # Normalize typographic hyphens without changing technology word boundaries.
    normalized = re.sub(r'[‐‑–—]', '-', term.casefold())
    return rf'(?<![a-z0-9]){re.escape(normalized)}(?![a-z0-9])'


def contains_required_qualification(text: str, term: str) -> bool:
    normalized = re.sub(r'[‐‑–—]', '-', qualification_text(text).casefold())
    for heading in REQUIRED_HEADING.finditer(normalized):
        end = SECTION_END.search(normalized, heading.end())
        section = normalized[heading.end():end.start() if end else len(normalized)]
        for line in section.splitlines():
            term_match = re.search(term_pattern(term), line)
            if term_match:
                # Optional individual bullets do not change the surrounding section.
                local = line[max(0, term_match.start() - 30):term_match.end() + 35]
                if re.search(r'\b(?:preferred|optional|nice to have|a plus|not required)\b', local):
                    continue
                return True
    return False


def has_confirmed_alternative(text: str, required_skill: str, confirmed_strengths: Sequence[str]) -> bool:
    terms = {required_skill.casefold(), *(strength.casefold() for strength in confirmed_strengths),
             'rust', 'c++', 'c#', 'python', 'gosu', 'kotlin', 'aws', 'azure', 'gcp', 'sap cloud',
             'microsoft azure', 'google cloud', 'google cloud platform', 'oci', 'oracle cloud'}
    atom = '(?:' + '|'.join(term_pattern(term) for term in sorted(terms, key=len, reverse=True)) + ')'
    separator = r'\s*(?:,\s*(?:or\s+)?|/\s*|\bor\s+)'
    choices = re.compile(atom + '(?:' + separator + atom + ')+', re.IGNORECASE)
    for match in choices.finditer(text):
        group = match.group()
        if not re.search(r'\bor\b', group, re.IGNORECASE) or not re.search(term_pattern(required_skill), group, re.IGNORECASE):
            continue
        if any(strength.casefold() != required_skill.casefold() and re.search(term_pattern(strength), group, re.IGNORECASE)
               for strength in confirmed_strengths):
            return True
    return False


def unconfirmed_required_capabilities(
    *, title: str, description: str, rules: Sequence[Mapping[str, Any]],
) -> list[str]:
    if isinstance(rules, (str, bytes)):
        raise ValueError('Required capability evidence must be a list of rules')
    gaps: list[str] = []
    for rule in rules:
        if not isinstance(rule, Mapping):
            raise ValueError('Capability evidence rule must be an object')
        label, terms, pattern, status = (rule.get(key) for key in ('label', 'terms', 'title_pattern', 'evidence_status'))
        if (not isinstance(label, str) or not label.strip() or not isinstance(terms, list) or not terms
                or any(not isinstance(term, str) or not term.strip() for term in terms)
                or not isinstance(pattern, str) or not pattern.strip()
                or not isinstance(status, str) or status not in {'unconfirmed', 'professional_evidence'}):
            raise ValueError('Capability evidence rule requires label, terms, title_pattern and a valid evidence_status')
        try:
            title_match = re.search(pattern, title, re.IGNORECASE)
        except re.error as exc:
            raise ValueError(f'Invalid capability title pattern: {pattern}') from exc
        if status == 'unconfirmed' and title_match and any(contains_required_qualification(description, term) for term in terms):
            if label not in gaps:
                gaps.append(label)
    return gaps
