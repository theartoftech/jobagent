from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from typing import Any


def _parse_override_date(value: object, *, field_name: str) -> dt.date:
    text = str(value).strip()
    try:
        return dt.date.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(
            f"Job override {field_name} must use YYYY-MM-DD; received {text!r}"
        ) from exc


def override_is_active(
    override: Mapping[str, object],
    *,
    as_of: dt.date,
) -> bool:
    expires_on = override.get("expires_on")
    if expires_on is not None and str(expires_on).strip():
        if as_of > _parse_override_date(expires_on, field_name="expires_on"):
            return False
    starts_on = override.get("starts_on")
    if starts_on is not None and str(starts_on).strip():
        if as_of < _parse_override_date(starts_on, field_name="starts_on"):
            return False
    return True


def _normalize_company_name(value: object) -> str:
    return " ".join(str(value).casefold().split())


def is_company_wide_skip_override(
    company_name: str,
    override: Mapping[str, object],
    *,
    as_of: dt.date,
) -> bool:
    if not override_is_active(override, as_of=as_of):
        return False
    if str(override.get("decision", "")).strip().casefold() != "skip":
        return False
    if any(
        str(override.get(field, "")).strip()
        for field in ("title_contains", "url_contains", "job_id")
    ):
        return False
    configured_company = _normalize_company_name(override.get("company", ""))
    return bool(configured_company) and configured_company == _normalize_company_name(company_name)


def filter_companies_by_overrides(
    companies: Sequence[dict[str, Any]],
    overrides: Sequence[Mapping[str, object]],
    *,
    as_of: dt.date,
) -> list[dict[str, Any]]:
    return [
        company
        for company in companies
        if not any(
            is_company_wide_skip_override(
                str(company.get("name", "")),
                override,
                as_of=as_of,
            )
            for override in overrides
        )
    ]
