#!/usr/bin/env python3
"""
ATS-aware job discovery for the daily job search.

This script queries public ATS endpoints where available and can also use public
job boards as discovery hints after verifying hits against official company or
ATS postings. It does not apply to jobs or submit data.
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urljoin, urlparse
from urllib.request import Request, urlopen

try:
    import yaml
except ModuleNotFoundError:
    yaml = None


USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
)
SUBPROCESS_TIMEOUT_PADDING_SECONDS = 2

DEFAULT_MAX_AGE_DAYS = 60
MIN_APPLY_FIT_SCORE = 76
MAX_SEARCH_TERMS_PER_COMPANY = 24
MAX_DETAIL_FETCHES_PER_COMPANY = 25
MAX_INTUIT_SEARCH_TERMS_PER_COMPANY = 8
MAX_WORKDAY_NON_PRODUCTIVE_SEARCHES_PER_COMPANY = 6
MAX_WORKDAY_LOW_VALUE_SEARCHES_PER_COMPANY = 4
MAX_WORKDAY_VIABLE_JOBS_PER_COMPANY = 12
MAX_WORKDAY_REQUESTS_PER_COMPANY = 8
MAX_WORKDAY_PAGES_PER_TERM = 2
WORKDAY_COMPANY_TIME_BUDGET_SECONDS = 45
MAX_WORKDAY_FATAL_ERROR_RESPONSES_PER_COMPANY = 1
MAX_PUBLIC_BOARD_QUERIES_PER_COMPANY = 4
MAX_PUBLIC_BOARD_RESULTS_PER_QUERY = 4
MAX_PUBLIC_BOARD_VERIFICATIONS_PER_COMPANY = 4
MAX_PUBLIC_BOARD_TOTAL_QUERIES = 80
MAX_PUBLIC_BOARD_TOTAL_HITS = 120
MAX_RECRUITER_BRANDED_HITS_PER_RUN = 10
LIGHTWEIGHT_SENTINEL_ADAPTERS = {
    "adp",
    "workday",
    "greenhouse",
    "lever",
    "smartrecruiters",
    "ashby",
    "icims",
    "workable",
}
PUBLIC_BOARD_SEARCH_TIMEOUT_SECONDS = 6
PUBLIC_BOARD_TIME_BUDGET_SECONDS = 240
MAX_X_POST_SEARCH_QUERIES = 8
MAX_THREADS_POST_SEARCH_QUERIES = 8
MAX_X_POST_RESULTS_PER_QUERY = 5
MAX_X_POST_TOTAL_HITS = 50
X_POST_TIME_BUDGET_SECONDS = 300
MAX_X_POST_JOB_URLS_PER_POST = 3
MAX_SOCIAL_POST_FETCHES_PER_RUN = 12
MAX_SOCIAL_CONSECUTIVE_QUERY_FAILURES = 5
MAX_SOCIAL_TOTAL_QUERY_FAILURES = 15
MAX_COMPACT_DESCRIPTION_CHARS = 4000
MAX_SKIP_DESCRIPTION_CHARS = 240
MAX_PUBLIC_BOARD_QUERY_FILE_COMPANIES = 12
MAX_PUBLIC_BOARD_QUERY_FILE_TITLES = 3
MAX_PUBLIC_BOARD_QUERY_FILE_SOURCES = 4
USAJOBS_SEARCH_URL = "https://data.usajobs.gov/api/search"
USAJOBS_MAX_RESULTS_PER_QUERY = 50
USAJOBS_QUERY_TERMS = [
    "Java Spring Boot REST API SQL",
    "application software Java API",
    "IT Specialist APPSW Java",
    "solution architect application software",
]

DISCOVERY_TERMS = [
    "software engineer",
    "senior software engineer",
    "principal software engineer",
    "staff software engineer",
    "java",
    "spring boot",
    "backend",
    "platform",
    "api",
    "integration",
    "solution architect",
    "technical consultant",
    "senior technical consultant",
    "implementation architect",
    "professional services engineer",
    "integration consultant",
    "delivery architect",
    "forward deployed engineer",
    "forward deployed architect",
    "cpq",
]

SOCIAL_HIRING_SEARCH_PHRASES = [
    '"hiring java"',
    '"spring boot engineer"',
    '"java engineer"',
    '"java developer"',
    '"spring boot developer"',
    '"backend engineer" "spring boot"',
    '"software engineer" "spring boot"',
    '"we\'re hiring" java',
    '"know anyone" java',
    '"dm me" "spring boot"',
    '"send resume" java',
    '"send cv" java',
    '"help wanted" java',
    '"java" "spring boot" hiring',
    '"java backend" hiring',
    '"senior java engineer" hiring',
    '"backend engineer" "java" hiring',
    'recruiter "java" "spring boot"',
    'recruiting "java backend"',
    '"tech recruiter" "java" "spring boot"',
    '"engineering recruiter" backend java',
    '"hiring manager" "java engineer"',
    '"know anyone" "java developer"',
    '"dm me" "java engineer"',
    '"send your resume" "spring boot"',
    '"looking for someone" "java" "spring boot"',
    '"senior software engineer" java spring',
    '"principal software engineer" java',
    '"staff engineer" java backend',
    '"solution architect" java spring',
    '"technical consultant" java integration',
    '"integration engineer" java spring',
    '"professional services" java hiring',
    '"implementation consultant" java',
    '"technical consultant" "spring boot"',
    '"solution architect" "api" java',
    '"integration consultant" java',
    '"delivery architect" java',
    'fintech java spring hiring',
    'healthcare java spring hiring',
    'payments java backend hiring',
    'saas java spring engineer',
    '"integration platform" java hiring',
    '"claims platform" java hiring',
    'atlanta java hiring',
    '"remote" "java spring boot" hiring',
    '"hybrid" atlanta java engineer',
    'georgia "backend engineer" java',
    '"remote us" "spring boot engineer"',
]
SOCIAL_HIRING_EXPANDED_PHRASES = [
    '"backend engineer"',
    '"software engineer"',
    '"spring boot"',
    '"java backend"',
    '"senior java engineer"',
    '"solution architect"',
    '"technical consultant"',
]
SOCIAL_LOCATION_QUERY_CLAUSE = '("Atlanta" OR "Remote" OR "Remote US" OR "Georgia")'
SOCIAL_HIRING_QUERY_SUFFIX = '("Java" OR "Spring Boot" OR "backend" OR "software engineer")'
SOCIAL_HIRING_EXPANDED_QUERY_SUFFIX = '("Java" OR "Spring Boot") ("backend" OR "software engineer")'
X_SITE_CLAUSE = "site:x.com"
THREADS_SITE_CLAUSE = "(site:threads.net OR site:threads.com)"

PUBLIC_BOARD_SOURCES = [
    {
        "name": "LinkedIn Jobs",
        "url": "https://www.linkedin.com/jobs/search/",
        "params": {"keywords": "{query}", "location": "United States"},
    },
    {
        "name": "Indeed",
        "url": "https://www.indeed.com/jobs",
        "params": {"q": "{query}", "l": "United States", "fromage": "60"},
    },
    {
        "name": "Google Jobs",
        "url": "https://www.google.com/search",
        "params": {"q": "{query} jobs posted last 60 days"},
    },
    {
        "name": "Glassdoor",
        "url": "https://www.glassdoor.com/Job/jobs.htm",
        "params": {"sc.keyword": "{query}"},
    },
    {
        "name": "Dice",
        "url": "https://www.dice.com/jobs",
        "params": {"q": "{query}", "location": "Remote"},
    },
    {
        "name": "Built In",
        "url": "https://builtin.com/jobs",
        "params": {"search": "{query}"},
    },
    {
        "name": "RemoteOK",
        "url": "https://remoteok.com/remote-java-jobs",
        "params": {},
    },
    {
        "name": "Workable",
        "url": "https://jobs.workable.com/",
        "params": {},
    },
    {
        "name": "icims",
        "url": "https://careers.icims.com/careers-home/jobs",
        "params": {},
    },
    {
        "name": "Wellfound",
        "url": "https://wellfound.com/jobs",
        "params": {"q": "{query}"},
    },
]

LINKEDIN_PRIMARY_SEARCH_LANES: list[tuple[str, tuple[str, ...]]] = [
    (
        "Staff Software Engineer",
        ("Staff Software Engineer", "Principal Software Engineer", "Senior Principal Software Engineer"),
    ),
    ("Staff Java Engineer", ("Staff Java Engineer", "Principal Java Engineer")),
    ("Software Architect", ("Software Architect", "Cloud Architect", "Solutions Architect")),
    ("Forward Deployed Engineer", ("Forward Deployed Engineer", "Technical Product Manager")),
]

PUBLIC_BOARD_DOMAINS = {
    "linkedin.com": "LinkedIn Jobs",
    "www.linkedin.com": "LinkedIn Jobs",
    "indeed.com": "Indeed",
    "www.indeed.com": "Indeed",
    "glassdoor.com": "Glassdoor",
    "www.glassdoor.com": "Glassdoor",
    "dice.com": "Dice",
    "www.dice.com": "Dice",
    "builtin.com": "Built In",
    "www.builtin.com": "Built In",
    "remoteok.com": "RemoteOK",
    "wellfound.com": "Wellfound",
    "www.wellfound.com": "Wellfound",
    "hackajob.com": "Recruiter Marketplace",
    "www.hackajob.com": "Recruiter Marketplace",
}

RECRUITER_BRANDED_DOMAINS = {
    "hackajob.com",
    "www.hackajob.com",
}

ATS_DOMAIN_MARKERS = [
    "myworkdayjobs.com",
    "myjobs.adp.com",
    "boards.greenhouse.io",
    "job-boards.greenhouse.io",
    "jobs.lever.co",
    "jobs.workable.com",
    "apply.workable.com",
    "jobs.ashbyhq.com",
    "smartrecruiters.com",
    "icims.com/jobs",
    "careers.icims.com",
    "taleo.net",
    "oraclecloud.com",
    "jobs.insightglobal.com/find_a_job",
    "insightglobal.com/jobs/find_a_job",
    "atlassian.com/company/careers/details",
    "stripe.com/jobs/listing",
]

UNAVAILABLE_TEXT_MARKERS = [
    "job not found",
    "job is no longer available",
    "job has expired",
    "posting has expired",
    "no longer accepting applications",
    "this job is no longer accepting applications",
    "this position is no longer available",
    "404",
]


@dataclass
class DiscoveryError:
    company: str
    source: str
    message: str


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str = ""


@dataclass
class PublicBoardHit:
    company: str
    source: str
    query: str
    target_title: str
    title: str
    url: str
    snippet: str = ""


@dataclass(frozen=True)
class RecruiterEmployerResolution:
    source_company: str
    employer_company: str
    reason: str
    confidence: str
    preliminary_fit_score: int


@dataclass
class JobPosting:
    company: str
    title: str
    url: str
    location: str = ""
    work_model: str = "Unknown"
    posted_on: str = ""
    posted_age_days: int | None = None
    date_basis: str = ""
    description: str = ""
    source: str = ""
    verification_status: str = "official_live"
    job_id: str = ""
    fit_score: int = 0
    decision: str = "Skip"
    fit_reasons: list[str] = field(default_factory=list)
    risk_flags: list[str] = field(default_factory=list)
    source_company: str = ""
    employer_company: str = ""
    source_url: str = ""
    employer_resolution_reason: str = ""
    employer_resolution_confidence: str = ""


NEEDS_FRESHNESS_VERIFICATION_DECISION = "Needs Freshness Verification"
LIVE_OLD_POSTING_FRESHNESS_RISK = (
    "official posting is live but older than {max_age_days} days; needs freshness verification"
)


@dataclass(frozen=True)
class CompanyDiagnostic:
    company: str
    total_jobs: int
    apply_jobs: int
    maybe_jobs: int
    watchlist_jobs: int
    skip_jobs: int
    top_skip_reason: str


@dataclass
class XPostLead:
    post_url: str
    post_title: str
    author_handle: str
    post_text: str
    posted_on: str
    posted_age_hours: int | None
    query: str
    same_user_follow_up: str
    external_job_urls: list[str] = field(default_factory=list)


@dataclass
class SocialQueryOutcome:
    query: str
    results_found: int
    leads_collected: int


def truncate_text(value: str, max_chars: int) -> str:
    text = value or ""
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3].rstrip() + "..."


def build_discovered_jobs_payload(
    *,
    jobs: list[JobPosting],
    errors: list[DiscoveryError],
    generated_on: str,
    max_age_days: int,
) -> dict[str, Any]:
    serialized_jobs: list[dict[str, Any]] = []
    for job in jobs:
        job_dict = asdict(job)
        description = str(job_dict.get("description") or "")
        if job.decision in {"Apply", "Maybe"}:
            job_dict["description"] = truncate_text(description, MAX_COMPACT_DESCRIPTION_CHARS)
        else:
            job_dict["description"] = truncate_text(description, MAX_SKIP_DESCRIPTION_CHARS)
        serialized_jobs.append(job_dict)
    return {
        "generated_on": generated_on,
        "max_age_days": max_age_days,
        "jobs": serialized_jobs,
        "errors": [asdict(error) for error in errors],
    }


class TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        text = data.strip()
        if text:
            self.parts.append(text)

    def text(self) -> str:
        return re.sub(r"\s+", " ", " ".join(self.parts)).strip()


def load_yaml(path: Path) -> dict[str, Any] | list[Any]:
    if not path.exists():
        raise FileNotFoundError(f"Missing required file: {path}")

    raw_text = path.read_text(encoding="utf-8")
    if yaml is not None:
        data = yaml.safe_load(raw_text)
    else:
        data = load_yaml_via_ruby(path)

    if not isinstance(data, (dict, list)):
        raise ValueError(f"Expected YAML mapping or list in {path}")

    return data


def load_yaml_via_ruby(path: Path) -> dict[str, Any] | list[Any]:
    ruby = shutil.which("ruby")
    if not ruby:
        raise ModuleNotFoundError(
            "PyYAML is not installed and Ruby is not available for YAML fallback parsing."
        )

    result = subprocess.run(
        [
            ruby,
            "-ryaml",
            "-rjson",
            "-e",
            "print JSON.generate(YAML.load_file(ARGV[0]))",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def normalize_company_entries(companies_doc: dict[str, Any] | list[Any]) -> list[dict[str, Any]]:
    companies = companies_doc.get("companies") if isinstance(companies_doc, dict) else companies_doc
    if not isinstance(companies, list):
        raise ValueError("companies.yml must contain a top-level 'companies' list.")

    normalized: list[dict[str, Any]] = []
    for item in companies:
        if isinstance(item, dict) and item.get("name"):
            normalized.append(item)
        elif isinstance(item, str):
            normalized.append({"name": item})
        else:
            raise ValueError(f"Invalid company entry in companies.yml: {item!r}")
    return normalized


def profile_doc_to_profile(profile_doc: dict[str, Any]) -> dict[str, Any]:
    profile = profile_doc.get("target_profile")
    if not isinstance(profile, dict):
        raise ValueError("candidate_profile.yml must contain top-level 'target_profile' mapping.")
    return profile


def normalize_company_key(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().casefold())


def load_ats_registry(path: Path) -> dict[str, list[str]]:
    if not path.exists():
        return {}
    registry_doc = load_yaml(path)
    if not isinstance(registry_doc, dict):
        raise ValueError(f"{path} must contain a mapping.")

    companies = registry_doc.get("companies", {})
    if not isinstance(companies, dict):
        raise ValueError(f"{path} must contain a top-level 'companies' mapping.")

    registry: dict[str, list[str]] = {}
    for company_name, config in companies.items():
        urls: list[str] = []
        if isinstance(config, dict):
            ats_entries = config.get("ats", [])
            if isinstance(ats_entries, dict):
                ats_entries = [ats_entries]
            if isinstance(ats_entries, list):
                for entry in ats_entries:
                    if isinstance(entry, dict) and str(entry.get("url", "")).strip():
                        urls.append(str(entry["url"]).strip())
                    elif isinstance(entry, str) and entry.strip():
                        urls.append(entry.strip())
        elif isinstance(config, list):
            urls.extend(str(item).strip() for item in config if str(item).strip())
        elif isinstance(config, str) and config.strip():
            urls.append(config.strip())

        normalized_name = normalize_company_key(str(company_name))
        if urls:
            registry[normalized_name] = dedupe(urls)
    return registry


def ats_registry_urls_for_company(registry: dict[str, list[str]], company: dict[str, Any]) -> list[str]:
    company_name = normalize_company_key(str(company.get("name", "")))
    return list(registry.get(company_name, []))


def request_text(url: str, *, timeout: int = 10) -> str:
    if shutil.which("curl"):
        try:
            result = subprocess.run(
                [
                    "curl",
                    "-s",
                    "-L",
                    "--max-time",
                    str(timeout),
                    "-A",
                    USER_AGENT,
                    url,
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=timeout + SUBPROCESS_TIMEOUT_PADDING_SECONDS,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            raise URLError(str(exc)) from exc
        return result.stdout

    request = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    with urlopen(request, timeout=timeout) as response:
        encoding = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(encoding, errors="replace")


def request_text_with_status(url: str, *, timeout: int = 10) -> tuple[int, str, str]:
    if shutil.which("curl"):
        marker = "__CODEX_HTTP_STATUS__"
        try:
            result = subprocess.run(
                [
                    "curl",
                    "-s",
                    "-L",
                    "--max-time",
                    str(timeout),
                    "-A",
                    USER_AGENT,
                    "-w",
                    f"\n{marker}:%{{http_code}}:%{{url_effective}}",
                    url,
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=timeout + SUBPROCESS_TIMEOUT_PADDING_SECONDS,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            raise URLError(str(exc)) from exc

        body, _, status_line = result.stdout.rpartition(f"\n{marker}:")
        status_parts = status_line.split(":", 1)
        try:
            status = int(status_parts[0])
        except (ValueError, IndexError):
            status = 0
        effective_url = status_parts[1] if len(status_parts) > 1 else url
        return status, body, effective_url

    request = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            encoding = response.headers.get_content_charset() or "utf-8"
            return response.status, response.read().decode(encoding, errors="replace"), response.url
    except HTTPError as exc:
        encoding = exc.headers.get_content_charset() if exc.headers else None
        return exc.code, exc.read().decode(encoding or "utf-8", errors="replace"), url


def request_json(
    url: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    timeout: int = 12,
    headers: dict[str, str] | None = None,
) -> dict[str, Any] | list[Any]:
    request_headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json, text/plain, */*",
    }
    if headers:
        request_headers.update(headers)

    if shutil.which("curl"):
        command = [
            "curl",
            "-s",
            "-L",
            "--max-time",
            str(timeout),
        ]
        if "User-Agent" in request_headers:
            command.extend(["-A", request_headers["User-Agent"]])
        for key, value in request_headers.items():
            command.extend(["-H", f"{key}: {value}"])
        if method.upper() == "POST":
            command.extend(["-X", "POST", "-H", "Content-Type: application/json"])
        if payload is not None:
            command.extend(["--data", json.dumps(payload)])
        command.append(url)

        try:
            result = subprocess.run(
                command,
                check=True,
                capture_output=True,
                text=True,
                timeout=timeout + SUBPROCESS_TIMEOUT_PADDING_SECONDS,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            raise URLError(str(exc)) from exc
        if not result.stdout.strip():
            raise ValueError("empty JSON response")
        return json.loads(result.stdout)

    body = None
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        request_headers["Content-Type"] = "application/json"

    request = Request(url, data=body, headers=request_headers, method=method)
    with urlopen(request, timeout=timeout) as response:
        encoding = response.headers.get_content_charset() or "utf-8"
        return json.loads(response.read().decode(encoding, errors="replace"))


def html_to_text(value: str) -> str:
    parser = TextExtractor()
    parser.feed(html.unescape(value or ""))
    return parser.text()


def parse_iso_date(value: str) -> dt.date | None:
    if not value:
        return None
    match = re.search(r"\d{4}-\d{1,2}-\d{1,2}", value)
    if match:
        try:
            year, month, day = (int(part) for part in match.group(0).split("-"))
            return dt.date(year, month, day)
        except ValueError:
            return None
    return None


def parse_posted_age(value: str, today: dt.date) -> tuple[str, int | None, str]:
    if not value:
        return "", None, ""

    raw = value.strip()
    lowered = raw.lower()

    if "today" in lowered or "just posted" in lowered:
        return today.isoformat(), 0, raw

    if "yesterday" in lowered:
        posted = today - dt.timedelta(days=1)
        return posted.isoformat(), 1, raw

    match = re.search(r"(\d+)\s*\+?\s*day", lowered)
    if match and "+" not in raw:
        days = int(match.group(1))
        posted = today - dt.timedelta(days=days)
        return posted.isoformat(), days, raw

    match = re.search(r"(\d+)\s*\+?\s*day", lowered)
    if match and "+" in raw:
        days = int(match.group(1))
        return "", days + 1, raw

    parsed = parse_iso_date(raw)
    if parsed:
        return parsed.isoformat(), (today - parsed).days, raw

    month_date = parse_month_name_date(raw, today)
    if month_date:
        return month_date.isoformat(), (today - month_date).days, raw

    return "", None, raw


def parse_month_name_date(value: str, today: dt.date) -> dt.date | None:
    month_pattern = (
        r"\b("
        r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
        r"jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|"
        r"nov(?:ember)?|dec(?:ember)?"
        r")\.?\s+(\d{1,2})(?:,\s*(\d{4}))?\b"
    )
    match = re.search(month_pattern, value, flags=re.IGNORECASE)
    if not match:
        return None
    month_token = match.group(1).lower()[:3]
    month_numbers = {
        "jan": 1,
        "feb": 2,
        "mar": 3,
        "apr": 4,
        "may": 5,
        "jun": 6,
        "jul": 7,
        "aug": 8,
        "sep": 9,
        "oct": 10,
        "nov": 11,
        "dec": 12,
    }
    year = int(match.group(3)) if match.group(3) else today.year
    try:
        parsed = dt.date(year, month_numbers[month_token], int(match.group(2)))
    except (KeyError, ValueError):
        return None
    if parsed > today and not match.group(3):
        parsed = dt.date(year - 1, parsed.month, parsed.day)
    return parsed


def infer_work_model(location: str, description: str = "") -> str:
    haystack = f"{location} {description}".lower()
    if any(
        token in haystack
        for token in [
            "remote",
            "telecommute",
            "telecommuting",
            "home office",
            "virtual",
            "work from home",
        ]
    ):
        return "Remote"
    if "hybrid" in haystack or "flex" in haystack:
        return "Hybrid"
    if "onsite" in haystack or "on-site" in haystack:
        return "Onsite"
    return "Unknown"


def usajobs_credentials() -> tuple[str, str] | None:
    user_agent = os.environ.get("USAJOBS_USER_AGENT", "").strip()
    auth_key = os.environ.get("USAJOBS_AUTHORIZATION_KEY", "").strip()
    if not user_agent or not auth_key:
        return None
    return user_agent, auth_key


def build_usajobs_search_url(keyword: str, max_age_days: int) -> str:
    params = {
        "Keyword": keyword,
        "JobCategoryCode": "2210",
        "DatePosted": str(min(max_age_days, 60)),
        "WhoMayApply": "public",
        "Fields": "Full",
        "ResultsPerPage": str(USAJOBS_MAX_RESULTS_PER_QUERY),
        "SortField": "opendate",
        "SortDirection": "desc",
    }
    return f"{USAJOBS_SEARCH_URL}?{urlencode(params)}"


def flatten_usajobs_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return html_to_text(value)
    if isinstance(value, list):
        return " ".join(flatten_usajobs_text(item) for item in value if item)
    if isinstance(value, dict):
        return " ".join(flatten_usajobs_text(item) for item in value.values() if item)
    return str(value)


def usajobs_locations(descriptor: dict[str, Any]) -> str:
    locations = descriptor.get("PositionLocation")
    if isinstance(locations, list):
        names = [
            str(item.get("LocationName", "")).strip()
            for item in locations
            if isinstance(item, dict) and str(item.get("LocationName", "")).strip()
        ]
        return "; ".join(dedupe(names))
    if isinstance(locations, dict):
        return str(locations.get("LocationName", "")).strip()
    return ""


def usajobs_description(descriptor: dict[str, Any]) -> str:
    details = descriptor.get("UserArea", {}).get("Details", {})
    if not isinstance(details, dict):
        details = {}
    pieces = [
        descriptor.get("QualificationSummary"),
        details.get("JobSummary"),
        details.get("MajorDuties"),
        details.get("Qualifications"),
        details.get("Education"),
        details.get("Evaluations"),
        details.get("Requirements"),
        details.get("SecurityClearance"),
        details.get("WhoMayApply"),
    ]
    return re.sub(r"\s+", " ", " ".join(flatten_usajobs_text(piece) for piece in pieces if piece)).strip()


def usajobs_security_clearance(descriptor: dict[str, Any]) -> str:
    details = descriptor.get("UserArea", {}).get("Details", {})
    if isinstance(details, dict):
        return flatten_usajobs_text(details.get("SecurityClearance")).strip()
    return ""


def job_from_usajobs_descriptor(
    descriptor: dict[str, Any],
    *,
    profile: dict[str, Any],
    today: dt.date,
    max_age_days: int,
) -> JobPosting | None:
    title = str(descriptor.get("PositionTitle", "")).strip()
    url = str(descriptor.get("PositionURI", "")).strip()
    if not title or not url:
        return None

    company = str(descriptor.get("OrganizationName") or descriptor.get("DepartmentName") or "USAJOBS").strip()
    location = usajobs_locations(descriptor)
    description = usajobs_description(descriptor)
    posted_on, posted_age_days, date_basis = parse_posted_age(
        str(descriptor.get("PublicationStartDate", "")),
        today,
    )
    job = JobPosting(
        company=company,
        title=title,
        url=url,
        location=location,
        work_model=infer_work_model(location, description),
        posted_on=posted_on,
        posted_age_days=posted_age_days,
        date_basis=date_basis,
        description=description,
        source="usajobs",
        verification_status="official_live",
        job_id=str(descriptor.get("PositionID", "")).strip(),
    )
    clearance = usajobs_security_clearance(descriptor).casefold()
    if clearance and clearance not in {"not required", "none"}:
        job.risk_flags.append(f"security clearance required: {clearance}")
    score_job(job, profile, max_age_days, company={"name": company})
    if any("security clearance required:" in risk for risk in job.risk_flags):
        job.decision = "Skip"
        job.fit_score = min(job.fit_score, 45)
    return job


def discover_usajobs(
    *,
    profile: dict[str, Any],
    today: dt.date,
    max_age_days: int,
    errors: list[DiscoveryError],
) -> list[JobPosting]:
    credentials = usajobs_credentials()
    if credentials is None:
        errors.append(
            DiscoveryError(
                "USAJOBS",
                "usajobs",
                "skipped: missing USAJOBS_USER_AGENT or USAJOBS_AUTHORIZATION_KEY",
            )
        )
        return []

    user_agent, auth_key = credentials
    headers = {
        "Host": "data.usajobs.gov",
        "User-Agent": user_agent,
        "Authorization-Key": auth_key,
        "Accept": "application/json",
    }
    jobs: list[JobPosting] = []
    seen_ids: set[str] = set()
    for keyword in USAJOBS_QUERY_TERMS:
        try:
            response = request_json(
                build_usajobs_search_url(keyword, max_age_days),
                headers=headers,
                timeout=15,
            )
        except Exception as exc:
            errors.append(DiscoveryError("USAJOBS", "usajobs", f"{keyword}: {exc}"))
            continue
        if not isinstance(response, dict):
            continue
        result = response.get("SearchResult", {})
        if not isinstance(result, dict):
            continue
        items = result.get("SearchResultItems", [])
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            descriptor = item.get("MatchedObjectDescriptor", {})
            if not isinstance(descriptor, dict):
                continue
            identity = str(descriptor.get("PositionID") or descriptor.get("PositionURI") or "").strip()
            if identity and identity in seen_ids:
                continue
            job = job_from_usajobs_descriptor(
                descriptor,
                profile=profile,
                today=today,
                max_age_days=max_age_days,
            )
            if job is None:
                continue
            if identity:
                seen_ids.add(identity)
            jobs.append(job)
    return jobs


def extract_workday_config(url: str) -> tuple[str, str, str] | None:
    parsed = urlparse(url)
    if "myworkdayjobs.com" not in parsed.netloc:
        return None
    tenant = parsed.netloc.split(".")[0]
    path_parts = [part for part in parsed.path.split("/") if part]
    if not path_parts:
        return None
    site = path_parts[0]
    base = f"{parsed.scheme}://{parsed.netloc}"
    return tenant, site, base


def extract_greenhouse_token(url: str, page: str = "") -> str | None:
    parsed = urlparse(url)
    candidates = [url, page]
    if "boards.greenhouse.io" in parsed.netloc:
        parts = [part for part in parsed.path.split("/") if part]
        if parts:
            return parts[0]
    if "job-boards.greenhouse.io" in parsed.netloc:
        parts = [part for part in parsed.path.split("/") if part]
        if parts:
            return parts[0]

    for text in candidates:
        patterns = [
            r"boards\.greenhouse\.io/([A-Za-z0-9_-]+)",
            r"job-boards\.greenhouse\.io/([A-Za-z0-9_-]+)",
            r"boards-api\.greenhouse\.io/v1/boards/([A-Za-z0-9_-]+)",
            r"greenhouse\.io/embed/job_board\?for=([A-Za-z0-9_-]+)",
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                return match.group(1)
    return None


def extract_lever_slug(url: str, page: str = "") -> str | None:
    parsed = urlparse(url)
    if parsed.netloc == "jobs.lever.co":
        parts = [part for part in parsed.path.split("/") if part]
        if parts:
            return parts[0]
    for text in [url, page]:
        match = re.search(r"jobs\.lever\.co/([A-Za-z0-9_-]+)", text)
        if match:
            return match.group(1)
    return None


def extract_smartrecruiters_company(url: str, page: str = "") -> str | None:
    parsed = urlparse(url)
    parts = [part for part in parsed.path.split("/") if part]
    if "smartrecruiters.com" in parsed.netloc and parts:
        if parts[0] in {"careers", "company"} and len(parts) > 1:
            return parts[1]
        return parts[0]

    for text in [url, page]:
        match = re.search(r"smartrecruiters\.com/(?:careers/|company/)?([A-Za-z0-9_-]+)", text)
        if match:
            return match.group(1)
    return None


def extract_workable_slug(url: str, page: str = "") -> str | None:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if host in {"jobs.workable.com", "apply.workable.com"}:
        parts = [part for part in parsed.path.split("/") if part]
        if parts:
            return parts[0]
    for text in [url, page]:
        match = re.search(r"(?:jobs|apply)\.workable\.com/([A-Za-z0-9_-]+)", text)
        if match:
            return match.group(1)
    return None


def extract_ashby_slug(url: str, page: str = "") -> str | None:
    parsed = urlparse(url)
    if parsed.netloc == "jobs.ashbyhq.com":
        parts = [part for part in parsed.path.split("/") if part]
        if parts:
            return parts[0]
    for text in [url, page]:
        match = re.search(r"jobs\.ashbyhq\.com/([A-Za-z0-9_-]+)", text)
        if match:
            return match.group(1)
    return None


def looks_like_first_party_listing_url(url: str) -> bool:
    parsed = urlparse(url)
    host = parsed.netloc.lower().removeprefix("www.")
    normalized_path = parsed.path.rstrip("/")
    if host == "stripe.com" and normalized_path in {"/jobs", "/jobs/search"}:
        return True
    if host == "southerncompany.jobs" and normalized_path in {"", "/jobs"}:
        return True
    return host == "blackrock.com" and normalized_path in {
        "/corporate/careers/job-search",
        "/search-jobs",
    }


def is_first_party_job_detail_url(url: str) -> bool:
    parsed = urlparse(url)
    host = parsed.netloc.lower().removeprefix("www.")
    if host == "stripe.com":
        return "/jobs/listing/" in parsed.path
    if host == "southerncompany.jobs":
        return "/job/" in parsed.path
    return host == "blackrock.com" and "/job/" in parsed.path


def looks_like_intuit_search_url(url: str) -> bool:
    parsed = urlparse(url)
    return (
        parsed.netloc.lower().removeprefix("www.") == "jobs.intuit.com"
        and parsed.path.rstrip("/") == "/search-jobs"
    )


def is_intuit_job_detail_url(url: str) -> bool:
    parsed = urlparse(url)
    return (
        parsed.netloc.lower().removeprefix("www.") == "jobs.intuit.com"
        and "/job/" in parsed.path
    )


def looks_like_icims_url(url: str, page: str = "") -> bool:
    haystacks = [url, page]
    return any(
        marker in haystack
        for haystack in haystacks
        for marker in ["icims.com/jobs", "careers.icims.com", "/jobs/search", "/jobs/"]
    )


def looks_like_insight_global_url(url: str, page: str = "") -> bool:
    parsed = urlparse(url)
    host = parsed.netloc.lower().removeprefix("www.")
    if host == "jobs.insightglobal.com":
        return True
    haystacks = [url, page]
    return any(
        re.search(r"insightglobal\.com/(?:jobs/)?find_a_job/.+?/job-\d+", haystack, re.IGNORECASE)
        for haystack in haystacks
    )


def looks_like_atlassian_url(url: str, page: str = "") -> bool:
    haystacks = [url, page]
    return any(
        re.search(r"atlassian\.com/company/careers(?:/details/\d+)?", haystack, re.IGNORECASE)
        for haystack in haystacks
    )


def extract_atlassian_job_id(url: str, page: str = "") -> str:
    for haystack in [url, page]:
        match = re.search(r"/company/careers/details/(\d+)", haystack, flags=re.IGNORECASE)
        if match:
            return match.group(1)
        match = re.search(r"careers-americas\.icims\.com/jobs/(\d+)/", haystack, flags=re.IGNORECASE)
        if match:
            return match.group(1)
    return ""


def canonicalize_atlassian_url(url: str) -> str:
    job_id = extract_atlassian_job_id(url)
    if not job_id:
        return url
    return f"https://www.atlassian.com/company/careers/details/{job_id}"


def extract_insight_global_job_id(url: str, page: str = "") -> str:
    for haystack in [url, page]:
        match = re.search(r"\bjob-(\d+)\b", haystack, flags=re.IGNORECASE)
        if match:
            return match.group(1)
        match = re.search(r"\bjobid=(\d+)\b", haystack, flags=re.IGNORECASE)
        if match:
            return match.group(1)
    return ""


def canonicalize_insight_global_url(url: str) -> str:
    job_id = extract_insight_global_job_id(url)
    if not job_id:
        return url
    return f"https://jobs.insightglobal.com/find_a_job/georgia/job-{job_id}/"


def is_parseable_http_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def extract_urls(page: str, base_url: str) -> list[str]:
    urls: list[str] = []
    for match in re.finditer(r"""href=["']([^"']+)["']""", page):
        href = html.unescape(match.group(1))
        if href.startswith(("mailto:", "tel:", "#", "javascript:")):
            continue
        try:
            resolved_url = urljoin(base_url, href)
        except ValueError:
            continue
        if is_parseable_http_url(resolved_url):
            urls.append(resolved_url)
    for match in re.finditer(r"""https?://[^\s"'<>]+""", page):
        candidate_url = html.unescape(match.group(0)).rstrip(").,;")
        if is_parseable_http_url(candidate_url):
            urls.append(candidate_url)
    return dedupe(urls)


def discover_ats_links(company: dict[str, Any], errors: list[DiscoveryError]) -> list[str]:
    careers_url = company.get("careers_url", "")
    if not careers_url:
        return []

    try:
        page = request_text(careers_url)
    except (HTTPError, URLError, TimeoutError, ValueError) as exc:
        errors.append(DiscoveryError(company["name"], "html", f"career page fetch failed: {exc}"))
        return []

    urls = extract_urls(page, careers_url)
    ats_urls = [
        url for url in urls
        if any(
            marker in url
            for marker in [
                "myworkdayjobs.com",
                "boards.greenhouse.io",
                "job-boards.greenhouse.io",
                "jobs.lever.co",
                "jobs.workable.com",
                "apply.workable.com",
                "smartrecruiters.com",
                "icims.com/jobs",
                "careers.icims.com",
                "taleo.net",
                "oraclecloud.com",
                "jobs.insightglobal.com/find_a_job",
                "insightglobal.com/jobs/find_a_job",
                "atlassian.com/company/careers/details",
            ]
        )
    ]
    ats_urls.extend(discover_workday_board_urls(company, errors))
    return dedupe(ats_urls)


def discover_workday_board_urls(company: dict[str, Any], errors: list[DiscoveryError]) -> list[str]:
    company_name = str(company.get("name") or "").strip()
    if not company_name:
        return []
    queries = [
        f'"{company_name}" "myworkdayjobs.com"',
        f'site:myworkdayjobs.com "{company_name}" "Software Engineer"',
    ]
    urls: list[str] = []
    for query in queries:
        for result in search_public_web(query, errors, company_name)[:6]:
            if not extract_workday_config(result.url):
                continue
            if is_valid_workday_board(result.url):
                urls.append(result.url)
    return dedupe(urls)


def is_valid_workday_board(url: str) -> bool:
    config = extract_workday_config(url)
    if not config:
        return False
    tenant, site, base = config
    endpoint = f"{base}/wday/cxs/{tenant}/{site}/jobs"
    try:
        response = request_json(
            endpoint,
            method="POST",
            payload={"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": "software"},
            timeout=8,
        )
    except (HTTPError, URLError, TimeoutError, ValueError, json.JSONDecodeError):
        return False
    return isinstance(response, dict) and isinstance(response.get("jobPostings", []), list)


def dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            result.append(value)
            seen.add(value)
    return result


def dedupe_case_insensitive(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = value.strip().casefold()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(value.strip())
    return result


def title_specificity_sort_key(value: str) -> tuple[int, int, str]:
    normalized = value.casefold()
    seniority_weight = 0
    for index, token in enumerate(["principal", "staff", "lead", "senior"]):
        if token in normalized:
            seniority_weight = len(["principal", "staff", "lead", "senior"]) - index
            break
    word_count = len([part for part in re.split(r"\s+", value.strip()) if part])
    return (-seniority_weight, -word_count, normalized)


class Adapter:
    name = "base"

    def discover(
        self,
        company: dict[str, Any],
        profile: dict[str, Any],
        today: dt.date,
        max_age_days: int,
        errors: list[DiscoveryError],
    ) -> list[JobPosting]:
        raise NotImplementedError


def is_viable_discovery_candidate(job: JobPosting, max_age_days: int) -> bool:
    return (
        not (job.posted_age_days is not None and job.posted_age_days > max_age_days)
        and job.fit_score >= 20
    )


def is_workday_fatal_error_response(response: dict[str, Any]) -> bool:
    error_code = str(response.get("errorCode") or "").strip().upper()
    http_status = str(response.get("httpStatus") or "").strip()
    return bool(error_code and http_status in {"400", "403", "404"} and error_code in {"S21", "S12", "S13"})


class WorkdayAdapter(Adapter):
    name = "workday"

    def __init__(self, url: str) -> None:
        config = extract_workday_config(url)
        if not config:
            raise ValueError(f"Not a Workday URL: {url}")
        self.tenant, self.site, self.base = config

    def discover(
        self,
        company: dict[str, Any],
        profile: dict[str, Any],
        today: dt.date,
        max_age_days: int,
        errors: list[DiscoveryError],
    ) -> list[JobPosting]:
        jobs: dict[str, JobPosting] = {}
        endpoint = f"{self.base}/wday/cxs/{self.tenant}/{self.site}/jobs"
        search_terms = build_search_terms(profile, company)
        started_at = time.monotonic()
        requests_sent = 0
        nonproductive_searches = 0
        low_value_searches = 0
        viable_jobs_found = 0
        fatal_error_responses = 0
        budget_exhausted = False

        for term in search_terms[:MAX_SEARCH_TERMS_PER_COMPANY]:
            if requests_sent >= MAX_WORKDAY_REQUESTS_PER_COMPANY:
                break
            if time.monotonic() - started_at >= WORKDAY_COMPANY_TIME_BUDGET_SECONDS:
                break

            term_added_jobs = 0
            term_viable_jobs = 0
            limit = 20
            offset = 0
            term_pages_fetched = 0

            while True:
                if requests_sent >= MAX_WORKDAY_REQUESTS_PER_COMPANY:
                    budget_exhausted = True
                    break
                if time.monotonic() - started_at >= WORKDAY_COMPANY_TIME_BUDGET_SECONDS:
                    budget_exhausted = True
                    break

                payload = {"appliedFacets": {}, "limit": limit, "offset": offset, "searchText": term}
                requests_sent += 1
                term_pages_fetched += 1
                try:
                    response = request_json(endpoint, method="POST", payload=payload)
                except HTTPError as exc:
                    errors.append(DiscoveryError(company["name"], self.name, f"{term}: {exc}"))
                    if exc.code == 400:
                        budget_exhausted = True
                        break
                    continue
                except (URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
                    errors.append(DiscoveryError(company["name"], self.name, f"{term}: {exc}"))
                    break

                postings = response.get("jobPostings", []) if isinstance(response, dict) else []
                if isinstance(response, dict) and response.get("errorCode"):
                    errors.append(
                        DiscoveryError(
                            company["name"],
                            self.name,
                            f"{term}: {response.get('errorCode')} {response.get('httpStatus')}",
                        )
                    )
                    if is_workday_fatal_error_response(response):
                        fatal_error_responses += 1
                        if fatal_error_responses >= MAX_WORKDAY_FATAL_ERROR_RESPONSES_PER_COMPANY:
                            budget_exhausted = True
                            break
                    break
                for item in postings:
                    if not isinstance(item, dict):
                        continue
                    title = str(item.get("title", "")).strip()
                    external_path = str(item.get("externalPath", "")).strip()
                    if not title or not external_path:
                        continue
                    url = urljoin(self.base, f"/{self.site}{external_path}")
                    job_id = str((item.get("bulletFields") or [""])[0] or external_path)
                    posted_on, age, date_basis = parse_posted_age(str(item.get("postedOn", "")), today)
                    location = str(item.get("locationsText", "")).strip()
                    key = f"{company['name']}|{job_id}|{title}|{url}"
                    if key not in jobs:
                        jobs[key] = JobPosting(
                            company=company["name"],
                            title=title,
                            url=url,
                            location=location,
                            work_model=infer_work_model(location),
                            posted_on=posted_on,
                            posted_age_days=age,
                            date_basis=date_basis,
                            source=self.name,
                            job_id=job_id,
                        )
                        score_job(jobs[key], profile, max_age_days, company=company)
                        term_added_jobs += 1
                        if is_viable_discovery_candidate(jobs[key], max_age_days):
                            term_viable_jobs += 1

                    if len(jobs) >= 100:
                        break
                    if viable_jobs_found + term_viable_jobs >= MAX_WORKDAY_VIABLE_JOBS_PER_COMPANY:
                        break

                should_continue_paging = (
                    len(postings) >= limit
                    and term_pages_fetched < MAX_WORKDAY_PAGES_PER_TERM
                    and term_added_jobs > 0
                    and len(jobs) < 100
                    and viable_jobs_found + term_viable_jobs < MAX_WORKDAY_VIABLE_JOBS_PER_COMPANY
                )
                if not should_continue_paging:
                    break
                offset += limit

            if term_added_jobs == 0:
                nonproductive_searches += 1
                low_value_searches = 0
            elif term_viable_jobs == 0:
                nonproductive_searches = 0
                low_value_searches += 1
            else:
                nonproductive_searches = 0
                low_value_searches = 0
                viable_jobs_found += term_viable_jobs

            if budget_exhausted:
                break
            if len(jobs) >= 100:
                break
            if viable_jobs_found >= MAX_WORKDAY_VIABLE_JOBS_PER_COMPANY:
                break
            if nonproductive_searches >= MAX_WORKDAY_NON_PRODUCTIVE_SEARCHES_PER_COMPANY:
                break
            if low_value_searches >= MAX_WORKDAY_LOW_VALUE_SEARCHES_PER_COMPANY:
                break

        detailed: list[JobPosting] = []
        detail_candidates = sorted(
            [
                job for job in jobs.values()
                if is_viable_discovery_candidate(job, max_age_days)
            ],
            key=lambda item: (-item.fit_score, item.posted_age_days is None, item.posted_age_days or 9999),
        )
        detail_urls = {job.url for job in detail_candidates[:MAX_DETAIL_FETCHES_PER_COMPANY]}
        for job in jobs.values():
            if job.url in detail_urls:
                detail = self.fetch_detail(job, today, errors)
                score_job(detail, profile, max_age_days, company=company)
                detailed.append(detail)
            else:
                detailed.append(job)
        return detailed

    def fetch_detail(self, job: JobPosting, today: dt.date, errors: list[DiscoveryError]) -> JobPosting:
        parsed = urlparse(job.url)
        # Convert public URL back to CXS detail path.
        marker = f"/{self.site}/"
        if marker not in parsed.path:
            return job
        cxs_path = parsed.path.split(marker, 1)[1]
        detail_url = f"{self.base}/wday/cxs/{self.tenant}/{self.site}/{cxs_path}"
        try:
            response = request_json(detail_url)
        except (HTTPError, URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
            errors.append(DiscoveryError(job.company, self.name, f"detail failed {job.title}: {exc}"))
            return job

        info = response.get("jobPostingInfo", {}) if isinstance(response, dict) else {}
        if not isinstance(info, dict):
            return job

        job.description = html_to_text(str(info.get("jobDescription", "")))
        job.title = str(info.get("title") or job.title).strip()
        job.location = str(info.get("location") or job.location).strip()
        job.work_model = str(info.get("remoteType") or infer_work_model(job.location, job.description))
        job.url = str(info.get("externalUrl") or job.url)
        job.job_id = str(info.get("jobReqId") or job.job_id)

        start_date = str(info.get("startDate") or "")
        if start_date:
            posted = parse_iso_date(start_date)
            if posted:
                job.posted_on = posted.isoformat()
                job.posted_age_days = (today - posted).days
                job.date_basis = "startDate"
        elif info.get("postedOn"):
            job.posted_on, job.posted_age_days, job.date_basis = parse_posted_age(
                str(info.get("postedOn")), today
            )

        if info.get("posted") is False or info.get("canApply") is False:
            job.verification_status = "official_unavailable"
            job.risk_flags.append("official page says posting is not applyable")
        return job


class GreenhouseAdapter(Adapter):
    name = "greenhouse"

    def __init__(self, url: str, page: str = "") -> None:
        token = extract_greenhouse_token(url, page)
        if not token:
            raise ValueError(f"No Greenhouse board token found: {url}")
        self.token = token

    def discover(
        self,
        company: dict[str, Any],
        profile: dict[str, Any],
        today: dt.date,
        max_age_days: int,
        errors: list[DiscoveryError],
    ) -> list[JobPosting]:
        url = f"https://boards-api.greenhouse.io/v1/boards/{self.token}/jobs?content=true"
        try:
            response = request_json(url)
        except (HTTPError, URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
            errors.append(DiscoveryError(company["name"], self.name, str(exc)))
            return []

        postings = response.get("jobs", []) if isinstance(response, dict) else []
        jobs: list[JobPosting] = []
        for item in postings:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title", "")).strip()
            location = str((item.get("location") or {}).get("name", "")).strip()
            updated_at = str(item.get("updated_at", ""))
            posted_on, age, date_basis = parse_posted_age(updated_at, today)
            job = JobPosting(
                company=company["name"],
                title=title,
                url=str(item.get("absolute_url", "")),
                location=location,
                work_model=infer_work_model(location, str(item.get("content", ""))),
                posted_on=posted_on,
                posted_age_days=age,
                date_basis=f"updated_at: {date_basis}" if date_basis else "updated_at",
                description=html_to_text(str(item.get("content", ""))),
                source=self.name,
                job_id=str(item.get("id", "")),
            )
            score_job(job, profile, max_age_days, company=company)
            jobs.append(job)
        return jobs


class LeverAdapter(Adapter):
    name = "lever"

    def __init__(self, url: str, page: str = "") -> None:
        slug = extract_lever_slug(url, page)
        if not slug:
            raise ValueError(f"No Lever slug found: {url}")
        self.slug = slug

    def discover(
        self,
        company: dict[str, Any],
        profile: dict[str, Any],
        today: dt.date,
        max_age_days: int,
        errors: list[DiscoveryError],
    ) -> list[JobPosting]:
        url = f"https://api.lever.co/v0/postings/{self.slug}?mode=json"
        try:
            response = request_json(url)
        except (HTTPError, URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
            errors.append(DiscoveryError(company["name"], self.name, str(exc)))
            return []

        postings = response if isinstance(response, list) else []
        jobs: list[JobPosting] = []
        for item in postings:
            if not isinstance(item, dict):
                continue
            created_at = item.get("createdAt")
            posted_on = ""
            age = None
            if isinstance(created_at, int):
                posted = dt.datetime.fromtimestamp(created_at / 1000, tz=dt.timezone.utc).date()
                posted_on = posted.isoformat()
                age = (today - posted).days

            categories = item.get("categories") or {}
            location = str(categories.get("location") or "").strip()
            commitment = str(categories.get("commitment") or "").strip()
            team = str(categories.get("team") or "").strip()
            description = " ".join(
                [
                    html_to_text(str(item.get("descriptionPlain") or item.get("description") or "")),
                    html_to_text(str(item.get("additionalPlain") or item.get("additional") or "")),
                    team,
                    commitment,
                ]
            )
            job = JobPosting(
                company=company["name"],
                title=str(item.get("text", "")).strip(),
                url=str(item.get("hostedUrl") or item.get("applyUrl") or ""),
                location=location,
                work_model=infer_work_model(location, description),
                posted_on=posted_on,
                posted_age_days=age,
                date_basis="createdAt" if posted_on else "",
                description=description,
                source=self.name,
                job_id=str(item.get("id", "")),
            )
            score_job(job, profile, max_age_days, company=company)
            jobs.append(job)
        return jobs


class SmartRecruitersAdapter(Adapter):
    name = "smartrecruiters"

    def __init__(self, url: str, page: str = "") -> None:
        company = extract_smartrecruiters_company(url, page)
        if not company:
            raise ValueError(f"No SmartRecruiters company token found: {url}")
        self.company = company

    def discover(
        self,
        company: dict[str, Any],
        profile: dict[str, Any],
        today: dt.date,
        max_age_days: int,
        errors: list[DiscoveryError],
    ) -> list[JobPosting]:
        url = f"https://api.smartrecruiters.com/v1/companies/{self.company}/postings?limit=100"
        try:
            response = request_json(url)
        except (HTTPError, URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
            errors.append(DiscoveryError(company["name"], self.name, str(exc)))
            return []

        postings = response.get("content", []) if isinstance(response, dict) else []
        jobs: list[JobPosting] = []
        for item in postings:
            if not isinstance(item, dict):
                continue
            released = str(item.get("releasedDate", ""))
            posted_on, age, date_basis = parse_posted_age(released, today)
            location_obj = item.get("location") or {}
            location = ", ".join(
                str(location_obj.get(key, "")).strip()
                for key in ["city", "region", "country"]
                if location_obj.get(key)
            )
            job = JobPosting(
                company=company["name"],
                title=str(item.get("name", "")).strip(),
                url=str((item.get("ref") or "").replace("/api/", "/")),
                location=location,
                work_model=infer_work_model(location),
                posted_on=posted_on,
                posted_age_days=age,
                date_basis=date_basis,
                description=str(item.get("jobAd", {}).get("sections", "")),
                source=self.name,
                job_id=str(item.get("id", "")),
            )
            score_job(job, profile, max_age_days, company=company)
            jobs.append(job)
        return jobs


class WorkableAdapter(Adapter):
    name = "workable"

    def __init__(self, url: str, page: str = "") -> None:
        slug = extract_workable_slug(url, page)
        if not slug:
            raise ValueError(f"No Workable slug found: {url}")
        self.slug = slug
        self.board_url = f"https://jobs.workable.com/{self.slug}"

    def discover(
        self,
        company: dict[str, Any],
        profile: dict[str, Any],
        today: dt.date,
        max_age_days: int,
        errors: list[DiscoveryError],
    ) -> list[JobPosting]:
        try:
            page = request_text(self.board_url)
        except (HTTPError, URLError, TimeoutError, ValueError) as exc:
            errors.append(DiscoveryError(company["name"], self.name, str(exc)))
            return []

        candidate_urls = [
            url
            for url in extract_urls(page, self.board_url)
            if any(marker in url for marker in ["/j/", "/view/", "workable.com/"])
        ]

        jobs: list[JobPosting] = []
        seen: set[str] = set()
        for url in candidate_urls[:MAX_DETAIL_FETCHES_PER_COMPANY]:
            if url in seen:
                continue
            seen.add(url)
            jobs.extend(fetch_job_page_jobs(company, url, today, profile, max_age_days, errors, self.name))
        return dedupe_postings(jobs)


class AshbyAdapter(Adapter):
    name = "ashby"

    def __init__(self, url: str, page: str = "") -> None:
        slug = extract_ashby_slug(url, page)
        if not slug:
            raise ValueError(f"No Ashby slug found: {url}")
        self.slug = slug
        self.board_url = f"https://jobs.ashbyhq.com/{self.slug}"

    def discover(
        self,
        company: dict[str, Any],
        profile: dict[str, Any],
        today: dt.date,
        max_age_days: int,
        errors: list[DiscoveryError],
    ) -> list[JobPosting]:
        try:
            page = request_text(self.board_url)
        except (HTTPError, URLError, TimeoutError, ValueError) as exc:
            errors.append(DiscoveryError(company["name"], self.name, str(exc)))
            return []

        app_data = extract_ashby_app_data(page)
        job_board = app_data.get("jobBoard", {}) if isinstance(app_data, dict) else {}
        postings = job_board.get("jobPostings", []) if isinstance(job_board, dict) else []
        jobs: list[JobPosting] = []
        for item in postings:
            if not isinstance(item, dict) or item.get("isListed") is False:
                continue
            title = str(item.get("title", "")).strip()
            posting_id = str(item.get("id", "")).strip()
            if not title or not posting_id:
                continue
            published_date = str(item.get("publishedDate") or item.get("updatedAt") or "")
            posted_on, age, date_basis = parse_posted_age(published_date, today)
            location = format_ashby_location(item)
            description = " ".join(
                value
                for value in [
                    str(item.get("departmentName", "")).strip(),
                    str(item.get("teamName", "")).strip(),
                    str(item.get("employmentType", "")).strip(),
                    str(item.get("workplaceType", "")).strip(),
                ]
                if value
            )
            job = JobPosting(
                company=company["name"],
                title=title,
                url=f"{self.board_url}/{posting_id}",
                location=location,
                work_model=infer_work_model(location, description),
                posted_on=posted_on,
                posted_age_days=age,
                date_basis=date_basis,
                description=description,
                source=self.name,
                job_id=posting_id,
            )
            score_job(job, profile, max_age_days, company=company)
            jobs.append(job)
        return jobs


def extract_adp_career_site_domain(url: str) -> str | None:
    parsed = urlparse(url)
    if parsed.netloc.casefold() != "myjobs.adp.com":
        return None
    parts = [part for part in parsed.path.split("/") if part]
    if not parts or parts[0].casefold() in {"cx", "public", "workforcenow"}:
        return None
    return parts[0]


def format_adp_location(item: dict[str, Any]) -> str:
    locations = item.get("requisitionLocations", [])
    values: list[str] = []
    if isinstance(locations, list):
        for location in locations:
            if not isinstance(location, dict):
                continue
            address = location.get("address", {})
            if not isinstance(address, dict):
                continue
            subdivision = address.get("countrySubdivisionLevel1", {})
            country = address.get("country", {})
            pieces = [
                str(address.get("cityName", "")).strip(),
                str(subdivision.get("longName", "")).strip() if isinstance(subdivision, dict) else "",
                str(country.get("longName", "")).strip() if isinstance(country, dict) else "",
            ]
            value = ", ".join(piece for piece in pieces if piece)
            if value:
                values.append(value)
    return " / ".join(dedupe(values))


class ADPAdapter(Adapter):
    name = "adp"

    def __init__(self, url: str) -> None:
        domain = extract_adp_career_site_domain(url)
        if not domain:
            raise ValueError(f"No ADP career-site domain found: {url}")
        self.domain = domain
        self.board_url = f"https://myjobs.adp.com/{domain}/"

    def discover(
        self,
        company: dict[str, Any],
        profile: dict[str, Any],
        today: dt.date,
        max_age_days: int,
        errors: list[DiscoveryError],
    ) -> list[JobPosting]:
        metadata_url = f"https://myjobs.adp.com/public/staffing/v1/career-site/{self.domain}"
        try:
            metadata = request_json(metadata_url)
            if not isinstance(metadata, dict):
                raise ValueError("ADP career-site metadata was not an object")
            token = str(metadata.get("myJobsToken", "")).strip()
            myadp_url = str((metadata.get("properties") or {}).get("myadpUrl", "")).rstrip("/")
            if not token or not myadp_url:
                raise ValueError("ADP career-site metadata omitted its listing token or API URL")

            params = {
                "$orderby": "postingDate desc",
                "$select": (
                    "reqId,jobTitle,publishedJobTitle,type,jobDescription,jobQualifications,"
                    "workLocations,workLevelCode,clientRequisitionID,postingDate,requisitionLocations"
                ),
                "$top": "100",
                "tz": "America/New_York",
            }
            listing_url = (
                f"{myadp_url}/myadp_prefix/mycareer/public/staffing/v1/job-requisitions/"
                f"apply-custom-filters?{urlencode(params)}"
            )
            response = request_json(
                listing_url,
                headers={
                    "myJobsToken": token,
                    "rolecode": "manager",
                    "Accept-Language": "en-US",
                    "Referer": "https://myjobs.adp.com/",
                },
            )
        except (HTTPError, URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
            errors.append(DiscoveryError(company["name"], self.name, str(exc)))
            return []

        postings = response.get("jobRequisitions", []) if isinstance(response, dict) else []
        jobs: list[JobPosting] = []
        for item in postings:
            if not isinstance(item, dict):
                continue
            req_id = str(item.get("reqId", "")).strip()
            title = str(item.get("publishedJobTitle") or item.get("jobTitle") or "").strip()
            if not req_id or not title:
                continue
            description = html_to_text(
                " ".join(
                    [
                        str(item.get("jobDescription", "")),
                        str(item.get("jobQualifications", "")),
                    ]
                )
            )
            location = format_adp_location(item)
            posted_on, age, date_basis = parse_posted_age(str(item.get("postingDate") or ""), today)
            job = JobPosting(
                company=company["name"],
                title=title,
                url=f"https://myjobs.adp.com/{self.domain}/cx/job-details?reqId={req_id}",
                location=location,
                work_model=infer_work_model(location, f"{title} {description}"),
                posted_on=posted_on,
                posted_age_days=age,
                date_basis=date_basis,
                description=description,
                source=self.name,
                verification_status="official_live",
                job_id=str(item.get("clientRequisitionID") or req_id).strip(),
            )
            if age is None:
                job.risk_flags.append("official posting is live but posting date is unavailable; needs freshness verification")
            score_job(job, profile, max_age_days, company=company)
            jobs.append(job)
        return jobs


def extract_ashby_app_data(page: str) -> dict[str, Any]:
    marker = "window.__appData = "
    start = page.find(marker)
    if start < 0:
        return {}
    raw = page[start + len(marker):]
    try:
        value, _end = json.JSONDecoder().raw_decode(raw)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def format_ashby_location(item: dict[str, Any]) -> str:
    primary = str(item.get("locationName", "")).strip()
    secondary_locations = item.get("secondaryLocations", [])
    secondary_names = []
    if isinstance(secondary_locations, list):
        for location in secondary_locations:
            if isinstance(location, dict) and location.get("locationName"):
                secondary_names.append(str(location["locationName"]).strip())
    workplace = str(item.get("workplaceType", "")).strip()
    return " / ".join(dedupe([value for value in [primary, *secondary_names, workplace] if value]))


class FirstPartyListingAdapter(Adapter):
    name = "firstparty-listing"

    def __init__(self, url: str, page: str = "") -> None:
        if not looks_like_first_party_listing_url(url):
            raise ValueError(f"Not a supported first-party listing URL: {url}")
        parsed = urlparse(url)
        if parsed.netloc.lower().removeprefix("www.") == "stripe.com":
            self.url = "https://stripe.com/jobs/search"
        else:
            self.url = url

    def discover(
        self,
        company: dict[str, Any],
        profile: dict[str, Any],
        today: dt.date,
        max_age_days: int,
        errors: list[DiscoveryError],
    ) -> list[JobPosting]:
        try:
            page = request_text(self.url)
        except (HTTPError, URLError, TimeoutError, ValueError) as exc:
            errors.append(DiscoveryError(company["name"], self.name, str(exc)))
            return []

        candidate_urls = [
            url
            for url in extract_urls(page, self.url)
            if is_first_party_job_detail_url(url)
        ]
        jobs: list[JobPosting] = []
        seen: set[str] = set()
        for url in dedupe(candidate_urls)[:MAX_DETAIL_FETCHES_PER_COMPANY]:
            if url in seen:
                continue
            seen.add(url)
            jobs.extend(fetch_job_page_jobs(company, url, today, profile, max_age_days, errors, self.name))
        return dedupe_postings(jobs)


class IntuitAdapter(Adapter):
    name = "intuit"

    def __init__(self, url: str, page: str = "") -> None:
        del page
        if not looks_like_intuit_search_url(url):
            raise ValueError(f"Not an Intuit search URL: {url}")
        parsed = urlparse(url)
        self.search_url = f"{parsed.scheme}://{parsed.netloc}/search-jobs"

    def discover(
        self,
        company: dict[str, Any],
        profile: dict[str, Any],
        today: dt.date,
        max_age_days: int,
        errors: list[DiscoveryError],
    ) -> list[JobPosting]:
        candidate_urls: list[str] = []
        for term in build_search_terms(profile, company)[:MAX_INTUIT_SEARCH_TERMS_PER_COMPANY]:
            search_url = f"{self.search_url}?{urlencode({'k': term})}"
            try:
                page = request_text(search_url)
            except (HTTPError, URLError, TimeoutError, ValueError) as exc:
                errors.append(DiscoveryError(company["name"], self.name, f"{term}: {exc}"))
                continue

            candidate_urls.extend(
                url
                for url in extract_urls(page, search_url)
                if is_intuit_job_detail_url(url)
            )
            candidate_urls = dedupe(candidate_urls)
            if len(candidate_urls) >= MAX_DETAIL_FETCHES_PER_COMPANY:
                break

        jobs: list[JobPosting] = []
        for url in candidate_urls[:MAX_DETAIL_FETCHES_PER_COMPANY]:
            jobs.extend(fetch_job_page_jobs(company, url, today, profile, max_age_days, errors, self.name))
        return dedupe_postings(jobs)


class ICIMSAdapter(Adapter):
    name = "icims"

    def __init__(self, url: str, page: str = "") -> None:
        if not looks_like_icims_url(url, page):
            raise ValueError(f"Not an iCIMS URL: {url}")
        self.url = url

    def discover(
        self,
        company: dict[str, Any],
        profile: dict[str, Any],
        today: dt.date,
        max_age_days: int,
        errors: list[DiscoveryError],
    ) -> list[JobPosting]:
        try:
            page = request_text(self.url)
        except (HTTPError, URLError, TimeoutError, ValueError) as exc:
            errors.append(DiscoveryError(company["name"], self.name, str(exc)))
            return []

        candidate_urls = [
            url
            for url in extract_urls(page, self.url)
            if "/jobs/" in url and "icims.com" in url
        ]

        jobs: list[JobPosting] = []
        seen: set[str] = set()
        for url in candidate_urls[:MAX_DETAIL_FETCHES_PER_COMPANY]:
            if url in seen:
                continue
            seen.add(url)
            jobs.extend(fetch_job_page_jobs(company, url, today, profile, max_age_days, errors, self.name))
        return dedupe_postings(jobs)


class InsightGlobalAdapter(Adapter):
    name = "insightglobal"

    def __init__(self, url: str, page: str = "") -> None:
        if not looks_like_insight_global_url(url, page):
            raise ValueError(f"Not an Insight Global job URL: {url}")
        self.url = canonicalize_insight_global_url(url)

    def discover(
        self,
        company: dict[str, Any],
        profile: dict[str, Any],
        today: dt.date,
        max_age_days: int,
        errors: list[DiscoveryError],
    ) -> list[JobPosting]:
        if not extract_insight_global_job_id(self.url):
            try:
                page = request_text(self.url)
            except (HTTPError, URLError, TimeoutError, ValueError) as exc:
                errors.append(DiscoveryError(company["name"], self.name, str(exc)))
                return []
            candidate_urls = [
                canonicalize_insight_global_url(url)
                for url in extract_urls(page, self.url)
                if extract_insight_global_job_id(url)
            ]
            jobs: list[JobPosting] = []
            for url in dedupe(candidate_urls)[:MAX_DETAIL_FETCHES_PER_COMPANY]:
                job = self.fetch_detail_page(company, url, today, profile, max_age_days, errors)
                if job:
                    jobs.append(job)
            return dedupe_postings(jobs)

        job = self.fetch_detail_page(company, self.url, today, profile, max_age_days, errors)
        return [job] if job else []

    def fetch_detail_page(
        self,
        company: dict[str, Any],
        url: str,
        today: dt.date,
        profile: dict[str, Any],
        max_age_days: int,
        errors: list[DiscoveryError],
    ) -> JobPosting | None:
        try:
            page = request_text(url)
        except (HTTPError, URLError, TimeoutError, ValueError) as exc:
            errors.append(DiscoveryError(company["name"], self.name, f"{url}: {exc}"))
            return None

        if page_looks_unavailable(200, page):
            return None

        job = parse_insight_global_job_page(company, url, page, today, profile, max_age_days)
        return job


class AtlassianAdapter(Adapter):
    name = "atlassian"

    def __init__(self, url: str, page: str = "") -> None:
        if not looks_like_atlassian_url(url, page):
            raise ValueError(f"Not an Atlassian careers URL: {url}")
        self.url = canonicalize_atlassian_url(url)

    def discover(
        self,
        company: dict[str, Any],
        profile: dict[str, Any],
        today: dt.date,
        max_age_days: int,
        errors: list[DiscoveryError],
    ) -> list[JobPosting]:
        if not extract_atlassian_job_id(self.url):
            try:
                page = request_text(self.url)
            except (HTTPError, URLError, TimeoutError, ValueError) as exc:
                errors.append(DiscoveryError(company["name"], self.name, str(exc)))
                return []
            candidate_urls = [
                canonicalize_atlassian_url(url)
                for url in extract_urls(page, self.url)
                if extract_atlassian_job_id(url)
            ]
            jobs: list[JobPosting] = []
            for url in dedupe(candidate_urls)[:MAX_DETAIL_FETCHES_PER_COMPANY]:
                job = self.fetch_detail_page(company, url, today, profile, max_age_days, errors)
                if job:
                    jobs.append(job)
            return dedupe_postings(jobs)

        job = self.fetch_detail_page(company, self.url, today, profile, max_age_days, errors)
        return [job] if job else []

    def fetch_detail_page(
        self,
        company: dict[str, Any],
        url: str,
        today: dt.date,
        profile: dict[str, Any],
        max_age_days: int,
        errors: list[DiscoveryError],
    ) -> JobPosting | None:
        try:
            page = request_text(url)
        except (HTTPError, URLError, TimeoutError, ValueError) as exc:
            errors.append(DiscoveryError(company["name"], self.name, f"{url}: {exc}"))
            return None

        if page_looks_unavailable(200, page):
            return None

        return parse_atlassian_job_page(company, url, page, today, profile, max_age_days)


class HtmlFallbackAdapter(Adapter):
    name = "html"

    def discover(
        self,
        company: dict[str, Any],
        profile: dict[str, Any],
        today: dt.date,
        max_age_days: int,
        errors: list[DiscoveryError],
    ) -> list[JobPosting]:
        url = company.get("careers_url", "")
        if not url:
            return []
        try:
            page = request_text(url)
        except (HTTPError, URLError, TimeoutError, ValueError) as exc:
            errors.append(DiscoveryError(company["name"], self.name, str(exc)))
            return []

        jobs = extract_json_ld_jobs(company, page, url, today, profile, max_age_days)
        if jobs:
            return jobs
        return []


def extract_json_ld_jobs(
    company: dict[str, Any],
    page: str,
    base_url: str,
    today: dt.date,
    profile: dict[str, Any],
    max_age_days: int,
) -> list[JobPosting]:
    jobs: list[JobPosting] = []
    blocks = re.findall(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        page,
        flags=re.IGNORECASE | re.DOTALL,
    )
    for block in blocks:
        try:
            data = json.loads(html.unescape(block.strip()))
        except json.JSONDecodeError:
            continue
        candidates = flatten_json_ld(data)
        for item in candidates:
            if item.get("@type") != "JobPosting":
                continue
            title = str(item.get("title", "")).strip()
            if not title:
                continue
            date_value = str(item.get("datePosted", ""))
            posted_on, age, date_basis = parse_posted_age(date_value, today)
            location = extract_json_ld_location(item.get("jobLocation"))
            url = str(item.get("url") or base_url)
            job = JobPosting(
                company=company["name"],
                title=title,
                url=urljoin(base_url, url),
                location=location,
                work_model=infer_work_model(location, str(item.get("description", ""))),
                posted_on=posted_on,
                posted_age_days=age,
                date_basis=date_basis,
                description=html_to_text(str(item.get("description", ""))),
                source="json-ld",
                job_id=str(item.get("identifier", "")),
            )
            score_job(job, profile, max_age_days, company=company)
            jobs.append(job)
    return jobs


def fetch_job_page_jobs(
    company: dict[str, Any],
    url: str,
    today: dt.date,
    profile: dict[str, Any],
    max_age_days: int,
    errors: list[DiscoveryError],
    source: str,
) -> list[JobPosting]:
    try:
        page = request_text(url)
    except (HTTPError, URLError, TimeoutError, ValueError) as exc:
        errors.append(DiscoveryError(company["name"], source, f"{url}: {exc}"))
        return []

    jobs = extract_json_ld_jobs(company, page, url, today, profile, max_age_days)
    if jobs:
        for job in jobs:
            job.source = source
        return jobs

    title = extract_html_title(page)
    if not title:
        return []
    posted_on, age, date_basis = parse_posted_age(html_to_text(page[:15000]), today)
    location = extract_location_from_text(page)
    job = JobPosting(
        company=company["name"],
        title=title,
        url=url,
        location=location,
        work_model=infer_work_model(location, page),
        posted_on=posted_on,
        posted_age_days=age,
        date_basis=date_basis,
        description=html_to_text(page[:6000]),
        source=source,
        job_id=extract_requisition_hint(url),
    )
    score_job(job, profile, max_age_days, company=company)
    return [job]


def parse_insight_global_job_page(
    company: dict[str, Any],
    url: str,
    page: str,
    today: dt.date,
    profile: dict[str, Any],
    max_age_days: int,
) -> JobPosting:
    title = extract_html_title(page)
    title = re.sub(r"\s*\|\s*Insight Global Careers\s*$", "", title, flags=re.IGNORECASE).strip()
    title = re.sub(r"\s+job\s+in\s+.+$", "", title, flags=re.IGNORECASE).strip() or title

    date_text = extract_html_fragment_text(
        page,
        r'<div[^>]+itemprop=["\']datePosted["\'][^>]*>(.*?)</div>',
    )
    if not date_text:
        date_marker = re.search(r"itemprop=[\"']datePosted[\"']", page, flags=re.IGNORECASE)
        if date_marker:
            date_text = html_to_text(page[date_marker.start(): date_marker.start() + 500])
    posted_on, age, date_basis = parse_posted_age(date_text, today)

    location = extract_html_fragment_text(
        page,
        r'<div[^>]+itemprop=["\']jobLocation["\'][^>]*>(.*?)</div>\s*</div>',
    )
    if not location:
        location_parts = [
            extract_html_fragment_text(page, r"<span[^>]+itemprop=['\"]addressLocality['\"][^>]*>(.*?)</span>"),
            extract_html_fragment_text(page, r"<span[^>]+itemprop=['\"]addressRegion['\"][^>]*>(.*?)</span>"),
        ]
        location = ", ".join(part.strip(" ,") for part in location_parts if part.strip())

    description = extract_insight_global_description(page)
    job = JobPosting(
        company=company["name"],
        title=title or "Insight Global Job",
        url=canonicalize_insight_global_url(url),
        location=location,
        work_model=infer_work_model(location, f"{title} {description}"),
        posted_on=posted_on,
        posted_age_days=age,
        date_basis=date_basis,
        description=description,
        source="insightglobal",
        job_id=extract_insight_global_job_id(url, page),
        verification_status="official_live",
    )
    score_job(job, profile, max_age_days, company=company)
    return job


def parse_atlassian_job_page(
    company: dict[str, Any],
    url: str,
    page: str,
    today: dt.date,
    profile: dict[str, Any],
    max_age_days: int,
) -> JobPosting:
    title = extract_html_fragment_text(page, r"<h1[^>]*>(.*?)</h1>") or extract_html_title(page)
    title = re.sub(r"\s*\|\s*Atlassian Careers\s*$", "", title, flags=re.IGNORECASE).strip()
    canonical_url = canonicalize_atlassian_url(url)
    apply_url = extract_atlassian_apply_url(page, canonical_url)
    posted_on, age, date_basis = parse_posted_age(html_to_text(page[:15000]), today)
    if posted_on == "" and age is None:
        date_basis = ""
    description = extract_atlassian_description(page)
    if apply_url:
        description = f"{description}\n\nApply URL: {apply_url}".strip()

    job = JobPosting(
        company=company["name"],
        title=title or "Atlassian Job",
        url=canonical_url,
        location="",
        work_model=infer_work_model("", description),
        posted_on=posted_on,
        posted_age_days=age,
        date_basis=date_basis,
        description=description,
        source="atlassian",
        job_id=extract_atlassian_job_id(canonical_url, page),
        verification_status="official_live",
    )
    score_job(job, profile, max_age_days, company=company)
    return job


def extract_atlassian_apply_url(page: str, base_url: str) -> str:
    for url in extract_urls(page, base_url):
        if "careers-americas.icims.com/jobs/" in url:
            return url
    return ""


def extract_atlassian_description(page: str) -> str:
    marker = re.search(r">\s*Working at Atlassian\s*<|>\s*Technical Skills:\s*<", page, flags=re.IGNORECASE)
    if not marker:
        return html_to_text(page[:14000])
    end = re.search(
        r"(?:Join the Atlassian Talent Community|Privacy Policy|</footer\b)",
        page[marker.start():],
        flags=re.IGNORECASE,
    )
    stop = marker.start() + end.start() if end else min(len(page), marker.start() + 14000)
    return html_to_text(page[marker.start():stop])


def extract_html_fragment_text(page: str, pattern: str) -> str:
    match = re.search(pattern, page, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return ""
    return html_to_text(match.group(1))


def extract_insight_global_description(page: str) -> str:
    marker = re.search(r">\s*Job Description\s*<", page, flags=re.IGNORECASE)
    if not marker:
        return html_to_text(page[:12000])
    closing_heading_index = page.find(">", marker.end())
    start = closing_heading_index + 1 if closing_heading_index >= 0 else marker.end()
    end = re.search(
        r'(?:<a[^>]+id=["\'][^"\']*guestLogin|Apply as Guest|<footer\b)',
        page[start:],
        flags=re.IGNORECASE,
    )
    stop = start + end.start() if end else min(len(page), start + 12000)
    return html_to_text(page[start:stop])


def extract_location_from_text(page: str) -> str:
    text = html_to_text(page[:12000])
    patterns = [
        r"location[:\s]+([A-Za-z0-9, .-]{3,80})",
        r"job location[:\s]+([A-Za-z0-9, .-]{3,80})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return ""


def extract_requisition_hint(url: str) -> str:
    if atlassian_id := extract_atlassian_job_id(url):
        return atlassian_id
    if insight_global_id := extract_insight_global_job_id(url):
        return insight_global_id
    match = re.search(r"/jobs?/([0-9A-Za-z_-]{5,})", url)
    if match:
        return match.group(1)
    return url


def dedupe_postings(jobs: list[JobPosting]) -> list[JobPosting]:
    seen: set[str] = set()
    output: list[JobPosting] = []
    for job in jobs:
        key = job.url or f"{job.company}|{job.job_id}|{job.title}"
        if key in seen:
            continue
        seen.add(key)
        output.append(job)
    return output


def flatten_json_ld(value: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    if isinstance(value, dict):
        result.append(value)
        graph = value.get("@graph")
        if isinstance(graph, list):
            for item in graph:
                result.extend(flatten_json_ld(item))
    elif isinstance(value, list):
        for item in value:
            result.extend(flatten_json_ld(item))
    return result


def extract_json_ld_location(value: Any) -> str:
    if isinstance(value, list):
        return "; ".join(extract_json_ld_location(item) for item in value if item)
    if not isinstance(value, dict):
        return ""
    address = value.get("address")
    if isinstance(address, dict):
        parts = [
            address.get("addressLocality"),
            address.get("addressRegion"),
            address.get("addressCountry"),
        ]
        return ", ".join(str(part) for part in parts if part)
    return str(value.get("name", ""))


def profile_title_targets(profile: dict[str, Any]) -> list[str]:
    """Return direct targets followed by lower-priority worthwhile titles."""
    primary_titles = profile.get("title_targets", [])
    worth_applying_titles = profile.get("worth_applying_title_targets", [])
    if not isinstance(primary_titles, list):
        primary_titles = []
    if not isinstance(worth_applying_titles, list):
        worth_applying_titles = []
    return dedupe_case_insensitive(
        [
            *(str(title).strip() for title in primary_titles if str(title).strip()),
            *(str(title).strip() for title in worth_applying_titles if str(title).strip()),
        ]
    )


def build_search_terms(profile: dict[str, Any], company: dict[str, Any] | None = None) -> list[str]:
    normalized_titles = profile_title_targets(profile)
    prioritized_company_aliases: list[str] = []
    if company is not None:
        company_aliases = company.get("title_aliases", [])
        if isinstance(company_aliases, list):
            prioritized_company_aliases.extend(
                sorted(
                    (
                        str(alias).strip()
                        for alias in company_aliases
                        if str(alias).strip()
                    ),
                    key=title_specificity_sort_key,
                )
            )
    return dedupe_case_insensitive([*prioritized_company_aliases, *normalized_titles, *DISCOVERY_TERMS])


def build_social_post_searches(profile: dict[str, Any], site_clause: str) -> list[tuple[str, str]]:
    searches: list[tuple[str, str]] = []
    del profile
    for phrase in SOCIAL_HIRING_SEARCH_PHRASES:
        searches.append((phrase, f'{site_clause} {phrase} {SOCIAL_HIRING_QUERY_SUFFIX} {SOCIAL_LOCATION_QUERY_CLAUSE}'))
    for phrase in SOCIAL_HIRING_EXPANDED_PHRASES:
        searches.append(
            (
                f"{phrase} (expanded)",
                f'{site_clause} {phrase} {SOCIAL_HIRING_EXPANDED_QUERY_SUFFIX} {SOCIAL_LOCATION_QUERY_CLAUSE}',
            )
        )
    return searches[:50]


def build_x_post_searches(profile: dict[str, Any]) -> list[tuple[str, str]]:
    return build_social_post_searches(profile, X_SITE_CLAUSE)


def build_threads_post_searches(profile: dict[str, Any]) -> list[tuple[str, str]]:
    return build_social_post_searches(profile, THREADS_SITE_CLAUSE)


def social_post_matches_profile(
    *,
    post_title: str,
    post_text: str,
    profile: dict[str, Any],
) -> bool:
    haystack = f"{post_title} {post_text}".lower()
    title_targets = [item.lower() for item in profile_title_targets(profile)]
    strong_keywords = [str(item).lower() for item in profile.get("strong_keywords", [])]
    secondary_keywords = [
        str(item).lower() for item in profile.get("acceptable_secondary_keywords", [])
    ]
    exclusions = [str(item).lower() for item in profile.get("exclude_if_primary_focus", [])]
    baseline_signals = [
        "java",
        "spring",
        "spring boot",
        "backend",
        "software engineer",
        "solution architect",
        "technical consultant",
        "integration",
        "api",
        "professional services",
    ]
    hard_reject_tokens = [
        "intern",
        "wordpress",
        "shopify",
        "ui/ux",
        "ui ux",
        "frontend only",
        "front-end only",
        "front end only",
    ]

    title_hits = [target for target in title_targets if target and target in haystack]
    strong_hits = [keyword for keyword in strong_keywords if keyword and keyword in haystack]
    secondary_hits = [keyword for keyword in secondary_keywords if keyword and keyword in haystack]
    exclusion_hits = [item for item in exclusions if item and item in haystack]
    baseline_hits = [token for token in baseline_signals if token in haystack]

    if any(token in haystack for token in hard_reject_tokens):
        return False
    if exclusion_hits and not title_hits and len(strong_hits) < 2:
        return False
    if title_hits or len(strong_hits) >= 2:
        return True
    if strong_hits and secondary_hits and baseline_hits:
        return True
    return len(baseline_hits) >= 2 and (bool(strong_hits) or bool(secondary_hits))


def build_public_board_searches(company: dict[str, Any], profile: dict[str, Any]) -> list[tuple[str, str]]:
    title_targets = profile_title_targets(profile)
    preferred_titles = [
        "Forward Deployed Engineer",
        "Forward Deployed Software Engineer",
        "Senior Forward Deployed Engineer",
        "Principal Forward Deployed Engineer",
        "Forward Deployed Architect",
        "Forward Deployed Solutions Architect",
        "Technical Consultant",
        "Solution Architect",
        "Implementation Architect",
        "Professional Services Engineer",
        "Senior Software Engineer",
        "Senior Software Engineer Java",
        "Integration Consultant",
        "Principal Software Engineer",
        "Software Development Engineer",
        "Senior Application Engineer",
        "Solutions Consultant",
        "Implementation Consultant",
        "Technical Implementation Consultant",
        "Customer Engineer",
        "Member of Technical Staff",
        "Senior Member of Technical Staff",
        "Enterprise Architect",
        "Technical Delivery Manager"
    ]
    titles = dedupe(
        preferred_titles
        + [
            str(title)
            for title in title_targets
            if any(
                token in str(title).lower()
                for token in [
                    "consultant",
                    "architect",
                    "professional",
                    "senior",
                    "principal",
                    "staff",
                    "director",
                ]
            )
        ]
    )
    company_name = company["name"]
    linkedin_skills = (
        '("Java" OR "Spring Boot" OR Kubernetes OR microservices OR "distributed systems" '
        'OR "REST APIs" OR OCI OR "cloud architecture" OR "system design")'
    )
    location_config = profile.get("locations", {})
    preferred_locations = location_config.get("preferred", []) if isinstance(location_config, dict) else []
    location_terms = dedupe(
        [str(location).strip() for location in preferred_locations if str(location).strip()]
        + ["Remote"]
    )[:10]
    linkedin_locations = "(" + " OR ".join(f'"{term}"' for term in location_terms) + ")"
    searches: list[tuple[str, str]] = [
        (
            primary_title,
            f'site:linkedin.com/jobs "{company_name}" '
            f'({" OR ".join(f"\"{title}\"" for title in titles)}) '
            f'{linkedin_skills} {linkedin_locations}',
        )
        for primary_title, titles in LINKEDIN_PRIMARY_SEARCH_LANES
    ]
    for title in titles[:MAX_PUBLIC_BOARD_QUERIES_PER_COMPANY]:
        for domain in official_search_domains_for_company(company):
            searches.append(
                (
                    title,
                    f'site:{domain} "{title}" ("Java" OR "Spring Boot" OR backend OR API OR remote)',
                )
            )
        searches.extend(
            [
                (
                    title,
                    f'site:indeed.com "{company_name}" "{title}" "posted"',
                ),
                (
                    title,
                    f'site:linkedin.com/jobs "{company_name}" "{title}"',
                ),
            ]
        )
    return searches


def is_excluded_company(company_name: str, profile: dict[str, Any]) -> bool:
    excluded = profile.get("excluded_companies", [])
    if not isinstance(excluded, list):
        return False
    normalized_name = normalize_company_key(company_name)
    return normalized_name in {
        normalize_company_key(str(item))
        for item in excluded
        if str(item).strip()
    }


def official_search_domains_for_company(company: dict[str, Any]) -> list[str]:
    domains = company.get("official_search_domains", [])
    if not isinstance(domains, list):
        return []
    return dedupe_case_insensitive(
        [
            str(domain).strip().removeprefix("https://").removeprefix("http://").strip("/")
            for domain in domains
            if str(domain).strip()
        ]
    )


def search_public_web(
    query: str,
    errors: list[DiscoveryError],
    company_name: str,
    timeout: int = PUBLIC_BOARD_SEARCH_TIMEOUT_SECONDS,
) -> list[SearchResult]:
    url = "https://duckduckgo.com/html/?" + urlencode({"q": query})
    try:
        page = request_text(url, timeout=timeout)
    except (HTTPError, URLError, TimeoutError, ValueError) as exc:
        errors.append(DiscoveryError(company_name, "public-search", f"{query}: {exc}"))
        return []
    return parse_duckduckgo_results(page)


def search_social_web(
    query: str,
    errors: list[DiscoveryError],
    source_name: str,
    timeout: int = PUBLIC_BOARD_SEARCH_TIMEOUT_SECONDS,
) -> list[SearchResult]:
    url = "https://www.google.com/search?" + urlencode({"q": query, "hl": "en", "num": "10"})
    try:
        page = request_text(url, timeout=timeout)
    except (HTTPError, URLError, TimeoutError, ValueError) as exc:
        errors.append(DiscoveryError(source_name, "public-search", f"{query}: {exc}"))
        return []
    return parse_google_results(page)


def parse_duckduckgo_results(page: str) -> list[SearchResult]:
    results: list[SearchResult] = []
    pattern = re.compile(
        r'<a[^>]+class=["\'][^"\']*result__a[^"\']*["\'][^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
        flags=re.IGNORECASE | re.DOTALL,
    )
    for match in pattern.finditer(page):
        url = decode_search_redirect(html.unescape(match.group(1)))
        title = html_to_text(match.group(2))
        if not title or not url.startswith("http"):
            continue
        results.append(SearchResult(title=title, url=url))
    return dedupe_search_results(results)


def parse_google_results(page: str) -> list[SearchResult]:
    results: list[SearchResult] = []
    pattern = re.compile(
        r'<a[^>]+href=["\'](/url\?q=[^"\']+)["\'][^>]*>\s*(?:<div[^>]*>)?\s*(?:<h3[^>]*>(.*?)</h3>)?',
        flags=re.IGNORECASE | re.DOTALL,
    )
    for match in pattern.finditer(page):
        href = html.unescape(match.group(1))
        title = html_to_text(match.group(2) or "")
        decoded = decode_google_redirect(href)
        if not decoded.startswith("http"):
            continue
        results.append(SearchResult(title=title or decoded, url=decoded))
    return dedupe_search_results(results)


def decode_google_redirect(url: str) -> str:
    parsed = urlparse(url)
    params = parse_qs(parsed.query)
    if params.get("q"):
        return params["q"][0]
    return url


def decode_search_redirect(url: str) -> str:
    parsed = urlparse(url)
    if "duckduckgo.com" in parsed.netloc:
        params = parse_qs(parsed.query)
        if params.get("uddg"):
            return params["uddg"][0]
    return url


def dedupe_search_results(results: list[SearchResult]) -> list[SearchResult]:
    seen: set[str] = set()
    output: list[SearchResult] = []
    for result in results:
        if result.url in seen:
            continue
        seen.add(result.url)
        output.append(result)
    return output


def is_x_post_url(url: str) -> bool:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    if host not in {"x.com", "twitter.com"}:
        return False
    path = parsed.path.strip("/")
    return "/status/" in f"/{path}/"


def is_threads_post_url(url: str) -> bool:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    if host not in {"threads.net", "threads.com"}:
        return False
    path = parsed.path.strip("/")
    return "/post/" in f"/{path}/" or "/t/" in f"/{path}/"


def canonicalize_url(url: str) -> str:
    parsed = urlparse(url)
    return parsed._replace(fragment="").geturl()


def extract_meta_content(page: str, *, key: str, attr: str = "property") -> str:
    pattern = re.compile(
        rf'<meta[^>]+{attr}=["\']{re.escape(key)}["\'][^>]+content=["\']([^"\']+)["\']',
        flags=re.IGNORECASE,
    )
    match = pattern.search(page)
    if match:
        return html.unescape(match.group(1)).strip()
    return ""


def parse_datetime_value(value: str) -> dt.datetime | None:
    cleaned = value.strip()
    if not cleaned:
        return None
    if cleaned.endswith("Z"):
        cleaned = cleaned[:-1] + "+00:00"
    try:
        parsed = dt.datetime.fromisoformat(cleaned)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def extract_x_post_freshness(
    page: str,
    snippet: str,
    now: dt.datetime,
) -> tuple[str, int | None]:
    datetime_match = re.search(r'datetime=["\']([^"\']+)["\']', page, flags=re.IGNORECASE)
    if datetime_match:
        posted_at = parse_datetime_value(datetime_match.group(1))
        if posted_at:
            age_hours = int((now - posted_at).total_seconds() // 3600)
            return posted_at.date().isoformat(), max(age_hours, 0)

    combined = html_to_text(f"{snippet} {page[:4000]}").lower()
    hour_match = re.search(r"\b(\d+)\s*(?:h|hr|hrs|hour|hours)\b", combined)
    if hour_match:
        age_hours = int(hour_match.group(1))
        return now.date().isoformat(), age_hours

    minute_match = re.search(r"\b(\d+)\s*(?:m|min|mins|minute|minutes)\b", combined)
    if minute_match:
        return now.date().isoformat(), 0

    if "today" in combined:
        return now.date().isoformat(), 0

    return "", None


def extract_x_post_summary(page: str, result: SearchResult) -> str:
    for key, attr in [
        ("og:description", "property"),
        ("twitter:description", "name"),
        ("description", "name"),
    ]:
        content = extract_meta_content(page, key=key, attr=attr)
        if content:
            return content

    text = html_to_text(page[:4000])
    if text:
        return text[:280]
    return result.title


def infer_social_author_handle(post_url: str) -> str:
    parsed = urlparse(post_url)
    parts = [part for part in parsed.path.split("/") if part]
    if any(host in parsed.netloc.lower() for host in ["threads.net", "threads.com"]):
        for part in parts:
            if part.startswith("@"):
                return part
    if parts:
        return f"@{parts[0].lstrip('@')}"
    return "@unknown"


def extract_first_same_author_follow_up(page: str, post_url: str, author_handle: str) -> str:
    author = author_handle.lstrip("@")
    escaped_author = re.escape(author)
    normalized_page = html.unescape(page)

    patterns = [
        rf"https?://(?:www\.)?(?:x\.com|twitter\.com)/{escaped_author}/status/\d+",
        rf"https?://(?:www\.)?threads\.(?:net|com)/@{escaped_author}/(?:post|t)/[A-Za-z0-9_-]+",
    ]
    matches: list[re.Match[str]] = []
    for pattern in patterns:
        matches.extend(re.finditer(pattern, normalized_page, flags=re.IGNORECASE))
    if len(matches) < 2:
        return "No same-user follow-up found in first fetched page."

    second_match = sorted(matches, key=lambda item: item.start())[1]
    window = normalized_page[second_match.end(): second_match.end() + 280]
    snippet = html_to_text(window).strip()
    if not snippet:
        return "Same-user follow-up link found, but no extra text was visible in the first fetched page."
    return snippet[:240]


def looks_like_job_url(url: str) -> bool:
    lowered = url.lower()
    if is_public_board_url(url) or is_ats_url(url):
        return True
    return any(
        marker in lowered
        for marker in [
            "/job",
            "/jobs",
            "/careers",
            "/career",
            "openings",
            "positions",
            "requisition",
        ]
    )


def extract_social_post_job_urls(page: str, base_url: str) -> list[str]:
    urls: list[str] = []
    for url in extract_urls(page, base_url):
        cleaned = canonicalize_url(strip_tracking_fragment(url))
        if is_x_post_url(cleaned) or is_threads_post_url(cleaned):
            continue
        if not looks_like_job_url(cleaned):
            continue
        urls.append(cleaned)
    return dedupe(urls)[:MAX_X_POST_JOB_URLS_PER_POST]


def prettify_company_token(value: str) -> str:
    text = re.sub(r"[-_]+", " ", value).strip()
    return re.sub(r"\s+", " ", text).title()


def ats_identity_key(url: str, page: str = "") -> tuple[str, str] | None:
    if workday_config := extract_workday_config(url):
        tenant, site, _base = workday_config
        return ("workday", f"{tenant}/{site}")
    if token := extract_greenhouse_token(url, page):
        return ("greenhouse", token.casefold())
    if slug := extract_lever_slug(url, page):
        return ("lever", slug.casefold())
    if slug := extract_workable_slug(url, page):
        return ("workable", slug.casefold())
    if domain := extract_adp_career_site_domain(url):
        return ("adp", domain.casefold())
    if company := extract_smartrecruiters_company(url, page):
        return ("smartrecruiters", company.casefold())
    if looks_like_icims_url(url, page):
        parsed = urlparse(url)
        return ("icims", parsed.netloc.casefold())
    if job_id := extract_insight_global_job_id(url, page):
        return ("insightglobal", job_id)
    if job_id := extract_atlassian_job_id(url, page):
        return ("atlassian", job_id)
    return None


def match_company_for_url(url: str, companies: list[dict[str, Any]]) -> dict[str, Any] | None:
    target_key = ats_identity_key(url)
    for company in companies:
        careers_url = str(company.get("careers_url") or "")
        if careers_url and target_key and ats_identity_key(careers_url) == target_key:
            return company
        configured_ats_urls = company.get("ats_urls", [])
        if isinstance(configured_ats_urls, list) and target_key:
            for ats_url in configured_ats_urls:
                if ats_identity_key(str(ats_url)) == target_key:
                    return company
        if not target_key and careers_url and is_company_official_url(url, company):
            return company
    return None


def infer_company_name_from_url(url: str, page: str = "") -> str:
    if title := extract_html_title(page):
        cleaned = re.sub(r"\b(job|jobs|careers?)\b", "", title, flags=re.IGNORECASE).strip(" -|:")
        if cleaned:
            return cleaned

    for extractor in [
        extract_greenhouse_token,
        extract_lever_slug,
        extract_workable_slug,
        extract_smartrecruiters_company,
    ]:
        token = extractor(url, page)
        if token:
            return prettify_company_token(token)

    if workday_config := extract_workday_config(url):
        tenant, _site, _base = workday_config
        return prettify_company_token(tenant)

    host = urlparse(url).netloc.lower()
    host = host[4:] if host.startswith("www.") else host
    if host:
        return prettify_company_token(host.split(".")[0])
    return "Unknown Company"



def public_board_source_for_url(url: str) -> str | None:
    host = urlparse(url).netloc.lower()
    if host.startswith("m."):
        host = "www." + host[2:]
    return PUBLIC_BOARD_DOMAINS.get(host)


def is_public_board_url(url: str) -> bool:
    return public_board_source_for_url(url) is not None


def is_recruiter_branded_url(url: str) -> bool:
    return urlparse(url).netloc.casefold() in RECRUITER_BRANDED_DOMAINS


def compact_company_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def recruiter_hit_fit_score(hit: PublicBoardHit, profile: dict[str, Any]) -> int:
    title_text = f"{hit.target_title} {hit.title}".casefold()
    haystack = f"{title_text} {hit.snippet}".casefold()
    title_targets = [
        str(value).strip().casefold()
        for value in [
            *profile.get("title_targets", []),
            *profile.get("worth_applying_title_targets", []),
        ]
        if str(value).strip()
    ]
    score = 0
    if any(target in title_text or title_text in target for target in title_targets):
        score += 50
    strong_hits = {
        str(keyword).casefold()
        for keyword in profile.get("strong_keywords", [])
        if str(keyword).strip() and str(keyword).casefold() in haystack
    }
    secondary_hits = {
        str(keyword).casefold()
        for keyword in profile.get("acceptable_secondary_keywords", [])
        if str(keyword).strip() and str(keyword).casefold() in haystack
    }
    score += min(35, 7 * len(strong_hits))
    score += min(10, 3 * len(secondary_hits))
    return min(100, score)


def resolve_recruiter_branded_hit(
    hit: PublicBoardHit,
    *,
    companies: list[dict[str, Any]],
    profile: dict[str, Any],
) -> RecruiterEmployerResolution | None:
    if not is_recruiter_branded_url(hit.url):
        return None

    preliminary_fit_score = recruiter_hit_fit_score(hit, profile)
    if preliminary_fit_score <= 75:
        return None

    parsed_url = urlparse(hit.url)
    source_company = parsed_url.netloc.casefold().removeprefix("www.").split(".")[0]
    company_parameter = " ".join(parse_qs(parsed_url.query).get("company", []))
    evidence = f"{company_parameter} {hit.title} {hit.snippet} {hit.query}"
    compact_evidence = compact_company_name(evidence)
    compact_parameter = compact_company_name(company_parameter)

    for company in companies:
        company_name = str(company.get("name") or "").strip()
        if not company_name:
            continue
        compact_name = compact_company_name(company_name)
        parameter_match = bool(
            compact_parameter
            and (
                compact_name.startswith(compact_parameter)
                or compact_parameter.startswith(compact_name)
            )
        )
        evidence_match = compact_name in compact_evidence
        configured_target_match = (
            normalize_company_key(company_name) == normalize_company_key(hit.company)
            and compact_name[:10] in compact_evidence
        )
        if not (parameter_match or evidence_match or configured_target_match):
            continue
        if parameter_match:
            reason = "recruiter URL company parameter matched configured employer"
        elif evidence_match:
            reason = "recruiter listing text matched configured employer"
        else:
            reason = "recruiter listing matched the configured search employer"
        return RecruiterEmployerResolution(
            source_company=source_company,
            employer_company=company_name,
            reason=reason,
            confidence="high",
            preliminary_fit_score=preliminary_fit_score,
        )
    return None


def apply_recruiter_resolution(
    job: JobPosting,
    hit: PublicBoardHit,
    resolution: RecruiterEmployerResolution,
) -> None:
    job.company = resolution.employer_company
    job.source_company = resolution.source_company
    job.employer_company = resolution.employer_company
    job.source_url = hit.url
    job.employer_resolution_reason = resolution.reason
    job.employer_resolution_confidence = resolution.confidence
    job.risk_flags.append(
        f"recruiter-hosted listing resolved to {resolution.employer_company} "
        f"({resolution.confidence} confidence)"
    )
    job.risk_flags = dedupe(job.risk_flags)


def is_ats_url(url: str) -> bool:
    return any(marker in url for marker in ATS_DOMAIN_MARKERS)


def company_domain(company: dict[str, Any]) -> str:
    careers_url = str(company.get("careers_url") or "")
    host = urlparse(careers_url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def company_official_domains(company: dict[str, Any]) -> list[str]:
    domains: list[str] = []
    primary = company_domain(company)
    if primary:
        domains.append(primary)
    configured = company.get("official_domains", [])
    if isinstance(configured, list):
        domains.extend(str(item) for item in configured)
    return dedupe_case_insensitive(
        [
            domain.strip().lower().removeprefix("www.")
            for domain in domains
            if str(domain).strip()
        ]
    )


def is_company_official_url(url: str, company: dict[str, Any]) -> bool:
    if is_public_board_url(url):
        return False
    if is_ats_url(url):
        return True
    domains = company_official_domains(company)
    if not domains:
        return False
    host = urlparse(url).netloc.lower()
    host = host[4:] if host.startswith("www.") else host
    return any(host == domain or host.endswith(f".{domain}") for domain in domains)


def extract_official_candidate_urls(page: str, base_url: str, company: dict[str, Any]) -> list[str]:
    candidates = []
    for url in extract_urls(page, base_url):
        cleaned = strip_tracking_fragment(url)
        if is_company_official_url(cleaned, company):
            candidates.append(cleaned)
    return dedupe(candidates)


def strip_tracking_fragment(url: str) -> str:
    parsed = urlparse(url)
    if parsed.fragment and not parsed.fragment.startswith("job"):
        parsed = parsed._replace(fragment="")
    return parsed.geturl()


def page_looks_unavailable(status: int, page: str) -> bool:
    if status >= 400:
        return True
    lowered = html_to_text(page[:20000]).lower()
    return any(marker in lowered for marker in UNAVAILABLE_TEXT_MARKERS)


def important_title_tokens(title: str) -> set[str]:
    words = re.findall(r"[a-z0-9+#.]+", title.lower())
    ignored = {
        "job",
        "jobs",
        "career",
        "careers",
        "hiring",
        "at",
        "for",
        "and",
        "the",
        "remote",
        "hybrid",
        "posted",
        "linkedin",
        "indeed",
        "glassdoor",
    }
    return {word for word in words if len(word) > 2 and word not in ignored}


def title_similarity(expected: str, actual: str) -> float:
    expected_tokens = important_title_tokens(expected)
    actual_tokens = important_title_tokens(actual)
    if not expected_tokens or not actual_tokens:
        return 0.0
    return len(expected_tokens & actual_tokens) / len(expected_tokens)


def extract_html_title(page: str) -> str:
    match = re.search(r"<title[^>]*>(.*?)</title>", page, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return ""
    title = html_to_text(match.group(1))
    return re.split(r"\s+[-|]\s+", title)[0].strip()


def job_from_official_page(
    *,
    company: dict[str, Any],
    official_url: str,
    page: str,
    effective_url: str,
    target_title: str,
    hit: PublicBoardHit,
    profile: dict[str, Any],
    today: dt.date,
    max_age_days: int,
) -> JobPosting | None:
    json_ld_jobs = extract_json_ld_jobs(company, page, effective_url or official_url, today, profile, max_age_days)
    if json_ld_jobs:
        json_ld_jobs.sort(
            key=lambda job: title_similarity(target_title, job.title),
            reverse=True,
        )
        best = json_ld_jobs[0]
        if title_similarity(target_title, best.title) < 0.4:
            return None
        best.source = f"public-board:{hit.source}->json-ld"
        best.risk_flags.append(f"discovered via {hit.source}; verified on official page")
        score_job(best, profile, max_age_days, company=company)
        enforce_public_board_freshness(best, max_age_days)
        return best

    page_text = html_to_text(page)
    title = extract_html_title(page) or clean_public_result_title(hit.title, company["name"])
    if title_similarity(target_title, title) < 0.4 and target_title.lower() not in page_text.lower():
        return None

    posted_on, age, date_basis = parse_posted_age(page_text[:15000], today)
    job = JobPosting(
        company=company["name"],
        title=title or target_title,
        url=effective_url or official_url,
        location="",
        work_model=infer_work_model("", page_text[:20000]),
        posted_on=posted_on,
        posted_age_days=age,
        date_basis=date_basis,
        description=page_text[:6000],
        source=f"public-board:{hit.source}->official",
        verification_status="official_live",
        risk_flags=[f"discovered via {hit.source}; verified on official page"],
    )
    score_job(job, profile, max_age_days, company=company)
    enforce_public_board_freshness(job, max_age_days)
    return job


def clean_public_result_title(title: str, company_name: str) -> str:
    cleaned = html.unescape(title)
    for separator in [" - ", " | ", " at "]:
        if separator in cleaned:
            parts = [part.strip() for part in cleaned.split(separator) if part.strip()]
            if parts:
                cleaned = parts[0]
                break
    cleaned = re.sub(r"\b(job|jobs|careers?)\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = cleaned.replace(company_name, "").strip(" -|:")
    return re.sub(r"\s+", " ", cleaned).strip()


def infer_title_from_job_url(url: str) -> str:
    parsed = urlparse(url)
    parts = [part for part in parsed.path.split("/") if part]
    if not parts:
        return ""
    for part in reversed(parts):
        if re.search(r"[A-Za-z]", part) and not re.fullmatch(r"(job|jobs|careers?|details|\d+)", part, re.IGNORECASE):
            without_req = re.sub(r"[_-](?:REQ|JR|R)-?\d.*$", "", part, flags=re.IGNORECASE)
            without_numeric = re.sub(r"/?\d+$", "", without_req)
            title = re.sub(r"[-_]+", " ", without_numeric).strip()
            if title:
                return re.sub(r"\s+", " ", title).title()
    return ""


def enforce_public_board_freshness(job: JobPosting, max_age_days: int) -> None:
    if job.posted_age_days is not None and job.posted_age_days > max_age_days:
        if is_live_official_posting(job):
            risk = LIVE_OLD_POSTING_FRESHNESS_RISK.format(max_age_days=max_age_days)
            if risk not in job.risk_flags:
                job.risk_flags.append(risk)
            job.decision = NEEDS_FRESHNESS_VERIFICATION_DECISION
            return
        if f"older than {max_age_days} days" not in job.risk_flags:
            job.risk_flags.append(f"older than {max_age_days} days")
        job.decision = "Skip"


def is_live_official_posting(job: JobPosting) -> bool:
    """Return whether the discovered job was verified on an available official page."""
    return job.verification_status == "official_live"


def adapter_cache_key(company: dict[str, Any], official_url: str, adapter_name: str) -> tuple[str, str]:
    identity = ats_identity_key(official_url) or (adapter_name, official_url.casefold())
    return str(company.get("name") or "Unknown Company"), f"{identity[0]}:{identity[1]}"


def job_from_ats_url(
    *,
    company: dict[str, Any],
    official_url: str,
    target_title: str,
    hit: PublicBoardHit,
    profile: dict[str, Any],
    today: dt.date,
    max_age_days: int,
    errors: list[DiscoveryError],
    adapter_result_cache: dict[tuple[str, str], list[JobPosting]] | None = None,
) -> JobPosting | None:
    try:
        adapter = adapter_for_url(official_url)
    except ValueError as exc:
        errors.append(DiscoveryError(company["name"], "public-board-verify", str(exc)))
        return None
    if not adapter:
        return None

    if isinstance(adapter, WorkdayAdapter):
        shell = JobPosting(
            company=company["name"],
            title=target_title,
            url=official_url,
            source=f"public-board:{hit.source}->workday",
            verification_status="official_live",
            risk_flags=[f"discovered via {hit.source}; verified on Workday"],
        )
        detail = adapter.fetch_detail(shell, today, errors)
        if detail.verification_status != "official_live":
            return None
        if title_similarity(target_title, detail.title) < 0.4:
            return None
        score_job(detail, profile, max_age_days, company=company)
        enforce_public_board_freshness(detail, max_age_days)
        return detail

    cache_key = adapter_cache_key(company, official_url, adapter.name)
    if adapter_result_cache is not None and cache_key in adapter_result_cache:
        jobs = adapter_result_cache[cache_key]
    else:
        jobs = adapter.discover(company, profile, today, max_age_days, errors)
        if adapter_result_cache is not None:
            adapter_result_cache[cache_key] = jobs
    if not jobs:
        return None
    jobs.sort(key=lambda job: title_similarity(target_title, job.title), reverse=True)
    best = jobs[0]
    if title_similarity(target_title, best.title) < 0.4:
        return None
    best.source = f"public-board:{hit.source}->{adapter.name}"
    best.risk_flags.append(f"discovered via {hit.source}; verified on {adapter.name}")
    score_job(best, profile, max_age_days, company=company)
    enforce_public_board_freshness(best, max_age_days)
    return best


def verify_public_board_hit(
    hit: PublicBoardHit,
    company: dict[str, Any],
    profile: dict[str, Any],
    today: dt.date,
    max_age_days: int,
    errors: list[DiscoveryError],
    adapter_result_cache: dict[tuple[str, str], list[JobPosting]] | None = None,
    max_official_candidates: int = 4,
) -> JobPosting | None:
    if max_official_candidates <= 0:
        raise ValueError("max_official_candidates must be positive.")
    official_urls: list[str] = []
    if (
        hit.source == "manual-url"
        and not is_public_board_url(hit.url)
    ) or is_company_official_url(hit.url, company) or is_ats_url(hit.url):
        official_urls.append(hit.url)
    try:
        status, board_page, _ = request_text_with_status(hit.url, timeout=15)
        if status < 400:
            official_urls.extend(extract_official_candidate_urls(board_page, hit.url, company))
    except (HTTPError, URLError, TimeoutError, ValueError) as exc:
        errors.append(DiscoveryError(company["name"], "public-board", f"{hit.url}: {exc}"))

    if not official_urls:
        official_urls.extend(search_official_urls_for_hit(hit, company, errors))

    for official_url in dedupe(official_urls)[:max_official_candidates]:
        if is_ats_url(official_url):
            ats_job = job_from_ats_url(
                company=company,
                official_url=official_url,
                target_title=hit.target_title,
                hit=hit,
                profile=profile,
                today=today,
                max_age_days=max_age_days,
                errors=errors,
                adapter_result_cache=adapter_result_cache,
            )
            if ats_job:
                return ats_job

        try:
            status, page, effective_url = request_text_with_status(official_url, timeout=15)
        except (HTTPError, URLError, TimeoutError, ValueError) as exc:
            errors.append(DiscoveryError(company["name"], "official-verify", f"{official_url}: {exc}"))
            continue
        if page_looks_unavailable(status, page):
            continue
        job = job_from_official_page(
            company=company,
            official_url=official_url,
            page=page,
            effective_url=effective_url,
            target_title=hit.target_title,
            hit=hit,
            profile=profile,
            today=today,
            max_age_days=max_age_days,
        )
        if job:
            return job
    return None


def verify_x_job_url(
    url: str,
    *,
    title_hint: str,
    companies: list[dict[str, Any]],
    profile: dict[str, Any],
    today: dt.date,
    max_age_days: int,
    errors: list[DiscoveryError],
    discovery_source: str,
    adapter_result_cache: dict[tuple[str, str], list[JobPosting]] | None = None,
) -> JobPosting | None:
    company = match_company_for_url(url, companies) or {
        "name": infer_company_name_from_url(url),
        "careers_url": "",
    }
    target_title = clean_public_result_title(title_hint, company["name"]) or title_hint

    if is_public_board_url(url):
        source = public_board_source_for_url(url) or "public-board"
        hit = PublicBoardHit(
            company=company["name"],
            source=source,
            query="",
            target_title=target_title,
            title=title_hint,
            url=url,
        )
        job = verify_public_board_hit(hit, company, profile, today, max_age_days, errors)
        if job:
            job.source = f"{discovery_source}->{job.source}"
            job.risk_flags.append(f"discovered via {discovery_source}")
        return job

    if is_ats_url(url):
        job = job_from_ats_url(
            company=company,
            official_url=url,
            target_title=target_title,
            hit=PublicBoardHit(
                company=company["name"],
                source=discovery_source,
                query="",
                target_title=target_title,
                title=title_hint,
                url=url,
            ),
            profile=profile,
            today=today,
            max_age_days=max_age_days,
            errors=errors,
            adapter_result_cache=adapter_result_cache,
        )
        if job:
            job.source = f"{discovery_source}->{job.source}"
            job.risk_flags.append(f"discovered via {discovery_source}")
        return job

    try:
        status, page, effective_url = request_text_with_status(url, timeout=15)
    except (HTTPError, URLError, TimeoutError, ValueError) as exc:
        errors.append(DiscoveryError(company["name"], discovery_source, f"{url}: {exc}"))
        return None

    if page_looks_unavailable(status, page):
        return None

    resolved_company = match_company_for_url(effective_url, companies) or {
        "name": infer_company_name_from_url(effective_url, page),
        "careers_url": "",
    }
    job = job_from_official_page(
        company=resolved_company,
        official_url=url,
        page=page,
        effective_url=effective_url,
        target_title=target_title,
        hit=PublicBoardHit(
            company=resolved_company["name"],
            source=discovery_source,
            query="",
            target_title=target_title,
            title=title_hint,
            url=url,
        ),
        profile=profile,
        today=today,
        max_age_days=max_age_days,
    )
    if job:
        job.source = f"{discovery_source}->official"
        job.risk_flags.append(f"discovered via {discovery_source}")
        return job

    jobs = fetch_job_page_jobs(
        resolved_company,
        effective_url,
        today,
        profile,
        max_age_days,
        errors,
        f"{discovery_source}->html",
    )
    if not jobs:
        return None
    jobs.sort(key=lambda item: title_similarity(target_title, item.title), reverse=True)
    best = jobs[0]
    if title_similarity(target_title, best.title) < 0.4:
        return None
    enforce_public_board_freshness(best, max_age_days)
    best.risk_flags.append(f"discovered via {discovery_source}")
    return best


def load_manual_job_url_entries(manual_path: Path) -> list[dict[str, str]]:
    if not manual_path.exists():
        return []
    manual_doc = load_yaml(manual_path)
    raw_jobs = manual_doc.get("jobs", []) if isinstance(manual_doc, dict) else manual_doc
    if not isinstance(raw_jobs, list):
        raise ValueError(f"{manual_path} must contain a list or a top-level 'jobs' list.")

    entries: list[dict[str, str]] = []
    for item in raw_jobs:
        if isinstance(item, str):
            url = item.strip()
            if url:
                entries.append({"url": url})
            continue
        if not isinstance(item, dict):
            raise ValueError(f"Invalid manual job URL entry: {item!r}")
        url = str(item.get("url", "")).strip()
        if not url:
            raise ValueError(f"Manual job URL entry is missing 'url': {item!r}")
        entry = {str(key): str(value).strip() for key, value in item.items() if value is not None}
        entry["url"] = url
        entries.append(entry)
    return entries


def load_job_overrides(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    overrides_doc = load_yaml(path)
    raw_overrides = overrides_doc.get("overrides", []) if isinstance(overrides_doc, dict) else overrides_doc
    if not isinstance(raw_overrides, list):
        raise ValueError(f"{path} must contain a list or a top-level 'overrides' list.")
    overrides: list[dict[str, str]] = []
    for item in raw_overrides:
        if not isinstance(item, dict):
            raise ValueError(f"Invalid job override entry: {item!r}")
        normalized = {str(key): str(value) for key, value in item.items() if str(value).strip()}
        if normalized:
            overrides.append(normalized)
    return overrides


def apply_job_overrides(jobs: list[JobPosting], overrides: list[dict[str, str]]) -> None:
    for job in jobs:
        for override in overrides:
            url_contains = override.get("url_contains", "")
            job_id = override.get("job_id", "")
            if url_contains and url_contains not in job.url:
                continue
            if job_id and job_id != job.job_id and job_id not in job.url:
                continue
            if not url_contains and not job_id:
                continue
            if decision := override.get("decision"):
                job.decision = decision
            if fit_score := override.get("fit_score"):
                try:
                    job.fit_score = int(fit_score)
                except ValueError:
                    raise ValueError(f"Invalid fit_score in job override: {fit_score}") from None
            if reason := override.get("reason"):
                job.risk_flags.append(reason)
                job.risk_flags = dedupe(job.risk_flags)


def discover_manual_job_urls(
    *,
    manual_path: Path,
    companies: list[dict[str, Any]],
    profile: dict[str, Any],
    today: dt.date,
    max_age_days: int,
    errors: list[DiscoveryError],
) -> list[JobPosting]:
    jobs: list[JobPosting] = []
    adapter_result_cache: dict[tuple[str, str], list[JobPosting]] = {}
    for entry in load_manual_job_url_entries(manual_path):
        url = entry["url"]
        company = match_company_for_url(url, companies)
        if company is None:
            company_name = entry.get("company") or infer_company_name_from_url(url)
            company = {"name": company_name, "careers_url": ""}
        target_title = (
            entry.get("title")
            or clean_public_result_title(entry.get("title_hint", ""), company["name"])
            or infer_title_from_job_url(url)
            or "Manual Job URL"
        )
        hit = PublicBoardHit(
            company=company["name"],
            source="manual-url",
            query="manual_job_urls.yml",
            target_title=target_title,
            title=target_title,
            url=url,
        )
        job: JobPosting | None = None
        if is_ats_url(url):
            job = job_from_ats_url(
                company=company,
                official_url=url,
                target_title=target_title,
                hit=hit,
                profile=profile,
                today=today,
                max_age_days=max_age_days,
                errors=errors,
                adapter_result_cache=adapter_result_cache,
            )
        else:
            job = verify_public_board_hit(
                hit,
                company,
                profile,
                today,
                max_age_days,
                errors,
                adapter_result_cache=adapter_result_cache,
        )
        if job:
            if job.source:
                job.source = job.source.replace("public-board:manual-url", "manual-url")
            else:
                adapter = adapter_for_url(url)
                job.source = f"manual-url->{adapter.name if adapter else 'official'}"
            if not job.source.startswith("manual-url"):
                job.source = f"manual-url->{job.source}"
            job.risk_flags.append("manually surfaced by the candidate")
            jobs.append(job)
    return jobs


def dedupe_x_post_leads(leads: list[XPostLead]) -> list[XPostLead]:
    seen: set[str] = set()
    output: list[XPostLead] = []
    for lead in leads:
        key = canonicalize_url(lead.post_url)
        if key in seen:
            continue
        seen.add(key)
        output.append(lead)
    return output


def discover_social_posts(
    *,
    platform_name: str,
    search_label: str,
    discovery_source: str,
    build_searches: Any,
    is_post_url: Any,
    companies: list[dict[str, Any]],
    profile: dict[str, Any],
    today: dt.date,
    now: dt.datetime,
    max_age_days: int,
    errors: list[DiscoveryError],
    results_per_query: int,
    total_hit_limit: int,
    time_budget_seconds: int,
    max_queries: int,
    max_post_fetches: int,
    pause_seconds: float = 0.0,
) -> tuple[list[XPostLead], list[SocialQueryOutcome]]:
    leads: list[XPostLead] = []
    query_outcomes: list[SocialQueryOutcome] = []
    started_at = time.monotonic()
    returned_hits = 0
    post_fetches = 0
    seen_posts: set[str] = set()
    consecutive_query_failures = 0
    total_query_failures = 0

    for target_title, query in build_searches(profile)[:max_queries]:
        if returned_hits >= total_hit_limit:
            break
        if time.monotonic() - started_at >= time_budget_seconds:
            errors.append(
                DiscoveryError(
                    discovery_source,
                    "budget",
                    f"stopped after {time_budget_seconds}s {platform_name} time budget",
                )
            )
            break

        errors_before = len(errors)
        results = search_social_web(query, errors, search_label)[:results_per_query]
        query_leads_before = len(leads)
        query_failed = len(errors) > errors_before and not results
        if query_failed:
            consecutive_query_failures += 1
            total_query_failures += 1
        else:
            consecutive_query_failures = 0

        for result in results:
            if returned_hits >= total_hit_limit:
                break
            if post_fetches >= max_post_fetches:
                break
            post_url = canonicalize_url(result.url)
            if not is_post_url(post_url) or post_url in seen_posts:
                continue
            seen_posts.add(post_url)

            try:
                post_fetches += 1
                status, page, effective_url = request_text_with_status(post_url, timeout=12)
            except (HTTPError, URLError, TimeoutError, ValueError) as exc:
                errors.append(DiscoveryError(discovery_source, "fetch", f"{post_url}: {exc}"))
                continue
            if page_looks_unavailable(status, page):
                continue

            posted_on, age_hours = extract_x_post_freshness(page, result.snippet, now)
            if age_hours is None or age_hours > 24:
                continue
            author_handle = infer_social_author_handle(effective_url or post_url)
            post_text = extract_x_post_summary(page, result)
            same_user_follow_up = extract_first_same_author_follow_up(
                page,
                effective_url or post_url,
                author_handle,
            )
            if not social_post_matches_profile(
                post_title=result.title,
                post_text=f"{post_text} {same_user_follow_up}",
                profile=profile,
            ):
                continue

            leads.append(
                XPostLead(
                    post_url=effective_url or post_url,
                    post_title=result.title,
                    author_handle=author_handle,
                    post_text=post_text,
                    posted_on=posted_on,
                    posted_age_hours=age_hours,
                    query=query,
                    same_user_follow_up=same_user_follow_up,
                    external_job_urls=extract_social_post_job_urls(page, effective_url or post_url),
                )
            )
            returned_hits += 1
            if pause_seconds:
                time.sleep(pause_seconds)

        query_outcomes.append(
            SocialQueryOutcome(
                query=query,
                results_found=len(results),
                leads_collected=len(leads) - query_leads_before,
            )
        )

        if consecutive_query_failures >= MAX_SOCIAL_CONSECUTIVE_QUERY_FAILURES:
            errors.append(
                DiscoveryError(
                    discovery_source,
                    "fast-fail",
                    f"stopped after {consecutive_query_failures} consecutive failed social queries",
                )
            )
            break
        if total_query_failures >= MAX_SOCIAL_TOTAL_QUERY_FAILURES:
            errors.append(
                DiscoveryError(
                    discovery_source,
                    "fast-fail",
                    f"stopped after {total_query_failures} total failed social queries",
                )
            )
            break

    return dedupe_x_post_leads(leads), query_outcomes


def discover_x_posts(
    *,
    companies: list[dict[str, Any]],
    profile: dict[str, Any],
    today: dt.date,
    now: dt.datetime,
    max_age_days: int,
    errors: list[DiscoveryError],
    results_per_query: int,
    total_hit_limit: int,
    time_budget_seconds: int,
    pause_seconds: float = 0.0,
) -> tuple[list[XPostLead], list[SocialQueryOutcome]]:
    return discover_social_posts(
        platform_name="X.com post discovery",
        search_label="X.com",
        discovery_source="x-post",
        build_searches=build_x_post_searches,
        is_post_url=is_x_post_url,
        companies=companies,
        profile=profile,
        today=today,
        now=now,
        max_age_days=max_age_days,
        errors=errors,
        results_per_query=results_per_query,
        total_hit_limit=total_hit_limit,
        time_budget_seconds=time_budget_seconds,
        max_queries=MAX_X_POST_SEARCH_QUERIES,
        max_post_fetches=MAX_SOCIAL_POST_FETCHES_PER_RUN,
        pause_seconds=pause_seconds,
    )


def discover_threads_posts(
    *,
    companies: list[dict[str, Any]],
    profile: dict[str, Any],
    today: dt.date,
    now: dt.datetime,
    max_age_days: int,
    errors: list[DiscoveryError],
    results_per_query: int,
    total_hit_limit: int,
    time_budget_seconds: int,
    pause_seconds: float = 0.0,
) -> tuple[list[XPostLead], list[SocialQueryOutcome]]:
    return discover_social_posts(
        platform_name="Threads post discovery",
        search_label="Threads",
        discovery_source="threads-post",
        build_searches=build_threads_post_searches,
        is_post_url=is_threads_post_url,
        companies=companies,
        profile=profile,
        today=today,
        now=now,
        max_age_days=max_age_days,
        errors=errors,
        results_per_query=results_per_query,
        total_hit_limit=total_hit_limit,
        time_budget_seconds=time_budget_seconds,
        max_queries=MAX_THREADS_POST_SEARCH_QUERIES,
        max_post_fetches=MAX_SOCIAL_POST_FETCHES_PER_RUN,
        pause_seconds=pause_seconds,
    )


def search_official_urls_for_hit(
    hit: PublicBoardHit,
    company: dict[str, Any],
    errors: list[DiscoveryError],
) -> list[str]:
    domain = company_domain(company)
    domain_clause = f"site:{domain}" if domain else company["name"]
    queries = [
        f'{domain_clause} "{company["name"]}" "{hit.target_title}"',
        f'"{company["name"]}" "{hit.target_title}" "myworkdayjobs" OR "greenhouse" OR "lever"',
    ]
    urls: list[str] = []
    for query in queries:
        for result in search_public_web(query, errors, company["name"])[:6]:
            if is_company_official_url(result.url, company):
                urls.append(result.url)
    return dedupe(urls)


def build_social_platform_search_url(intro_label: str, query: str) -> str:
    normalized_label = intro_label.strip().lower()
    encoded_query = urlencode({"q": query})
    if normalized_label == "x.com":
        return f"https://x.com/search?{encoded_query}&src=typed_query&f=live"
    if normalized_label == "threads":
        return f"https://www.threads.com/search?{encoded_query}&filter=recent"
    raise ValueError(f"Unsupported social platform label: {intro_label}")


def discover_public_board_verified_jobs(
    *,
    companies: list[dict[str, Any]],
    profile: dict[str, Any],
    today: dt.date,
    max_age_days: int,
    errors: list[DiscoveryError],
    pause_seconds: float,
    max_companies: int | None,
    queries_per_company: int,
    results_per_query: int,
    total_query_limit: int,
    total_hit_limit: int,
    time_budget_seconds: int,
) -> list[JobPosting]:
    jobs: list[JobPosting] = []
    total_queries = 0
    total_hits = 0
    recruiter_branded_hits = 0
    started_at = time.monotonic()
    selected_companies = companies[:max_companies] if max_companies else companies
    adapter_result_cache: dict[tuple[str, str], list[JobPosting]] = {}

    for company in selected_companies:
        verified_for_company = 0
        searches = build_public_board_searches(company, profile)[:queries_per_company]
        for target_title, query in searches:
            if time.monotonic() - started_at >= time_budget_seconds:
                errors.append(
                    DiscoveryError(
                        "public-board",
                        "budget",
                        f"stopped after {time_budget_seconds}s public-board time budget",
                    )
                )
                return jobs
            if total_queries >= total_query_limit:
                return jobs
            if total_hits >= total_hit_limit:
                return jobs
            total_queries += 1
            results = search_public_web(query, errors, company["name"])[:results_per_query]
            for result in results:
                source = public_board_source_for_url(result.url)
                if not source and (is_company_official_url(result.url, company) or is_ats_url(result.url)):
                    source = "official-search"
                if not source:
                    continue
                hit = PublicBoardHit(
                    company=company["name"],
                    source=source,
                    query=query,
                    target_title=target_title,
                    title=result.title,
                    url=result.url,
                    snippet=result.snippet,
                )
                total_hits += 1
                resolution: RecruiterEmployerResolution | None = None
                verification_company = company
                official_candidate_limit = 4
                if is_recruiter_branded_url(hit.url):
                    if recruiter_branded_hits >= MAX_RECRUITER_BRANDED_HITS_PER_RUN:
                        continue
                    recruiter_branded_hits += 1
                    resolution = resolve_recruiter_branded_hit(
                        hit,
                        companies=companies,
                        profile=profile,
                    )
                    if resolution is None:
                        continue
                    verification_company = next(
                        (
                            candidate
                            for candidate in companies
                            if normalize_company_key(str(candidate.get("name") or ""))
                            == normalize_company_key(resolution.employer_company)
                        ),
                        company,
                    )
                    official_candidate_limit = 1
                job = verify_public_board_hit(
                    hit,
                    verification_company,
                    profile,
                    today,
                    max_age_days,
                    errors,
                    adapter_result_cache=adapter_result_cache,
                    max_official_candidates=official_candidate_limit,
                )
                if job:
                    if resolution is not None:
                        apply_recruiter_resolution(job, hit, resolution)
                    jobs.append(job)
                    verified_for_company += 1
                    if verified_for_company >= MAX_PUBLIC_BOARD_VERIFICATIONS_PER_COMPANY:
                        break
                if pause_seconds:
                    time.sleep(pause_seconds)
            if verified_for_company >= MAX_PUBLIC_BOARD_VERIFICATIONS_PER_COMPANY:
                break
    return jobs


def is_staffing_or_consulting_company(company: dict[str, Any] | None) -> bool:
    if not company:
        return False
    haystack = " ".join(
        [
            str(company.get("name", "")),
            str(company.get("notes", "")),
            str(company.get("careers_url", "")),
        ]
    ).casefold()
    return any(
        token in haystack
        for token in [
            "staffing",
            "consulting",
            "professional services",
            "talent",
            "solutions consultant",
            "implementation guidance",
            "client-facing technical delivery",
        ]
    )


def consulting_advisory_hits(haystack: str) -> list[str]:
    consulting_tokens = [
        "consultant",
        "consulting",
        "solution architect",
        "solutions consultant",
        "implementation",
        "customer engineer",
        "professional services",
        "technical advisory",
        "client-facing technical delivery",
        "technical lead",
        "delivery",
        "integration",
        "workshop",
        "design review",
        "mentoring",
    ]
    return [token for token in consulting_tokens if token in haystack]


def build_title_target_set(profile: dict[str, Any], company: dict[str, Any] | None) -> list[str]:
    title_targets = [item.lower() for item in profile_title_targets(profile)]
    if not company:
        return title_targets
    company_aliases = company.get("title_aliases", [])
    if not isinstance(company_aliases, list):
        return title_targets
    for alias in company_aliases:
        normalized = str(alias).strip().lower()
        if normalized and normalized not in title_targets:
            title_targets.append(normalized)
    return title_targets


def classify_exclusion_hits(haystack: str, title_text: str, description_text: str, exclusions: list[str]) -> tuple[list[str], list[str]]:
    dominant_hits: list[str] = []
    weak_hits: list[str] = []
    for item in exclusions:
        if not item:
            continue
        haystack_count = haystack.count(item)
        description_count = description_text.count(item)
        if item in title_text or description_count >= 2 or haystack_count >= 3:
            dominant_hits.append(item)
        elif haystack_count > 0:
            weak_hits.append(item)
    return dominant_hits, weak_hits


REQUIRED_EXPERIENCE_GAP_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("NodeJS", (r"\bnode\.?js\b",)),
    ("Python", (r"\bpython\b",)),
    ("Redis/cache", (r"\bredis\b", r"\bcache\b", r"\bcaching\b")),
    ("AWS/Azure", (r"\baws\b", r"\bazure\b")),
    ("Terraform", (r"\bterraform\b",)),
    ("Splunk", (r"\bsplunk\b",)),
    ("Datadog", (r"\bdatadog\b",)),
)


def explicit_required_experience_gaps(text: str) -> list[str]:
    lowered = text.lower()
    if not any(
        marker in lowered
        for marker in ["minimum requirement", "experience requirement", "basic qualification", "must have", "required"]
    ):
        return []

    year_pattern = r"(?:\b\d+\+?|\(\d+\)|one|two|three|four|five|six|seven|eight|nine|ten)\s+years?"
    gaps: list[str] = []
    for label, skill_patterns in REQUIRED_EXPERIENCE_GAP_PATTERNS:
        for skill_pattern in skill_patterns:
            skill_before_years = rf"{skill_pattern}.{{0,100}}{year_pattern}"
            years_before_skill = rf"{year_pattern}.{{0,140}}{skill_pattern}"
            if re.search(skill_before_years, lowered) or re.search(years_before_skill, lowered):
                gaps.append(label)
                break
    return dedupe(gaps)


def has_required_cpp_windows_gap(text: str) -> bool:
    lowered = text.lower()
    if not any(marker in lowered for marker in ["basic qualification", "minimum requirement", "required", "must have"]):
        return False
    year_pattern = r"(?:\b\d+\+?|\(\d+\)|one|two|three|four|five|six|seven|eight|nine|ten)\s+years?"
    cpp_pattern = r"(?:c\+\+|modern c\+\+)"
    windows_pattern = r"\bwindows\b"
    return bool(
        re.search(rf"{year_pattern}.{{0,180}}{cpp_pattern}.{{0,180}}{windows_pattern}", lowered)
        or re.search(rf"{cpp_pattern}.{{0,180}}{windows_pattern}.{{0,180}}{year_pattern}", lowered)
        or re.search(rf"{cpp_pattern}.{{0,80}}{year_pattern}.{{0,180}}{windows_pattern}", lowered)
    )


def existing_required_gap_count(risks: list[str]) -> int:
    for risk in risks:
        normalized = risk.casefold()
        if not normalized.startswith("required experience gaps:"):
            continue
        values = risk.split(":", 1)[1] if ":" in risk else ""
        return len([value for value in values.split(",") if value.strip()])
    return 0


def is_weather_science_ai_role(text: str) -> bool:
    lowered = text.lower()
    weather_tokens = [
        "weather science",
        "atmospheric model",
        "model output",
        "forecast product",
        "consumer forecast",
        "weather.com",
        "the weather channel",
    ]
    ai_python_tokens = [
        "python",
        "llm",
        "production llm",
        "ai-derived",
        "ai-generated",
        "machine learning",
        "aws",
    ]
    return (
        sum(1 for token in weather_tokens if token in lowered) >= 2
        and sum(1 for token in ai_python_tokens if token in lowered) >= 2
    )


def is_primary_sre_observability_aiops_role(title: str, description: str) -> bool:
    """Identify infrastructure reliability roles without rejecting app teams that monitor services."""
    title_text = title.casefold()
    description_text = description.casefold()
    title_markers = [
        r"\bsite reliability\b",
        r"\bsre\b",
        r"\bdevops\b",
        r"\baiops\b",
        r"\bobservability\b",
    ]
    primary_title = any(re.search(marker, title_text) for marker in title_markers)
    platform_signals = [
        "slo",
        "sli",
        "error budget",
        "opentelemetry",
        "otlp",
        "grafana",
        "prometheus",
        "loki",
        "tempo",
        "argo cd",
        "chaos engineering",
        "fault injection",
        "aiops",
        "llm observability",
        "observability platform",
    ]
    signal_count = sum(signal in description_text for signal in platform_signals)
    return (primary_title and signal_count >= 2) or signal_count >= 5


def is_primary_ruby_on_rails_role(description: str) -> bool:
    """Flag Rails-led product roles while allowing incidental legacy-Rails mentions."""
    text = description.casefold()
    rails_mentions = len(re.findall(r"\bruby on rails\b|\brails\b", text))
    if rails_mentions == 0:
        return False

    explicit_rails_requirement = bool(
        re.search(
            r"(?:required|must have|extensive|what you(?:'|’)ll need|application question|how many years)"
            r".{0,140}(?:ruby on rails|\brails\b)",
            text,
        )
    )
    rails_ecosystem_signals = ["react", "aws", "postgresql", "rds", "sqs", "graphql"]
    ecosystem_signal_count = sum(signal in text for signal in rails_ecosystem_signals)
    return explicit_rails_requirement or (rails_mentions >= 2 and ecosystem_signal_count >= 2)


def has_required_dotnet_stack(text: str) -> bool:
    lowered = text.lower()
    if not re.search(r"(?:^|[^a-z0-9])(?:\.net|dotnet|asp\.net)(?:[^a-z0-9]|$)", lowered):
        return False
    requirement_markers = [
        "qualification",
        "qualifications",
        "requirement",
        "requirements",
        "required",
        "must have",
        "must include",
        "experience with",
        "proficient with",
        "proficiency with",
        "technologies:",
        "languages:",
        "tech stack",
        "our stack",
    ]
    return any(marker in lowered for marker in requirement_markers)


def clustered_hard_stack_requirement_gaps(text: str) -> list[str]:
    lowered = text.lower()
    requirement_markers = [
        "application question",
        "application questions",
        "basic qualification",
        "minimum qualification",
        "qualification",
        "qualifications",
        "requirement",
        "requirements",
        "required",
        "required stack",
        "must have",
        "must include",
        "experience with",
        "hands-on",
        "proficient with",
        "proficiency with",
        "tech stack",
        "our stack",
    ]
    if not any(marker in lowered for marker in requirement_markers):
        return []

    gap_patterns = [
        ("React/TypeScript", [r"\breact\b", r"\btypescript\b", r"\bjavascript\b"]),
        ("MongoDB/NoSQL", [r"\bmongodb\b", r"\bnosql\b"]),
        ("Kafka/event streaming", [r"\bkafka\b", r"\bevent/message-driven\b", r"\bevent-driven\b"]),
        ("Groovy/Grails", [r"\bgroovy\b", r"\bgrails\b"]),
        ("AWS/cloud deployment", [r"\baws\b", r"\bazure\b", r"\bgcp\b", r"\bcloud deployment\b"]),
        ("Python primary stack", [r"\bpython\b"]),
    ]
    gaps: list[str] = []
    for label, patterns in gap_patterns:
        if any(re.search(pattern, lowered) for pattern in patterns):
            gaps.append(label)

    if "React/TypeScript" in gaps and re.search(r"(?:\b\d+\+?|\(\d+\)|one|two|three|four|five)\s+years?.{0,120}\breact\b", lowered):
        gaps.append("explicit React years screen")

    return dedupe(gaps)


def is_forward_deployed_title(title: str) -> bool:
    return "forward deployed" in title.casefold()


def is_customer_facing_engineering_title(title: str) -> bool:
    normalized = title.casefold()
    return is_forward_deployed_title(normalized) or any(
        marker in normalized
        for marker in ("solutions engineer", "solution engineer", "customer engineer")
    )


def has_substantial_customer_software_ownership(title: str, description: str) -> bool:
    text = f"{title} {description}".casefold()
    software_markers = (
        "write code",
        "java",
        "spring boot",
        "software development",
        "software engineering",
        "build production",
        "develop production",
        "backend services",
        "microservices",
        "rest api",
        "apis",
        "distributed systems",
        "enterprise integration",
        "enterprise integrations",
    )
    ownership_markers = (
        "architecture",
        "technical ownership",
        "own architecture",
        "implementation",
        "deployment",
        "deploy",
        "production operation",
        "production troubleshooting",
        "technical leadership",
        "technical direction",
        "integration delivery",
        "technical delivery",
    )
    customer_markers = (
        "customer",
        "client",
        "enterprise environment",
        "customer environment",
        "field engineering",
        "forward deployed",
    )
    return (
        any(marker in text for marker in software_markers)
        and any(marker in text for marker in ownership_markers)
        and any(marker in text for marker in customer_markers)
    )


def score_job(
    job: JobPosting,
    profile: dict[str, Any],
    max_age_days: int,
    *,
    company: dict[str, Any] | None = None,
) -> None:
    haystack = f"{job.title} {job.description} {job.location} {job.work_model}".lower()
    title_text = job.title.lower()
    description_text = job.description.lower()
    score = 0
    reasons: list[str] = []
    risks: list[str] = list(job.risk_flags)
    hard_reject = False

    title_targets = build_title_target_set(profile, company)
    strong_keywords = [str(item).lower() for item in profile.get("strong_keywords", [])]
    secondary_keywords = [
        str(item).lower() for item in profile.get("acceptable_secondary_keywords", [])
    ]
    exclusions = [str(item).lower() for item in profile.get("exclude_if_primary_focus", [])]

    if any(target in title_text for target in title_targets):
        score += 35
        reasons.append("target title match")

    strong_hits = [keyword for keyword in strong_keywords if keyword in haystack]
    if strong_hits:
        score += min(35, 5 * len(strong_hits))
        reasons.append("strong keywords: " + ", ".join(strong_hits[:8]))

    secondary_hits = [keyword for keyword in secondary_keywords if keyword in haystack]
    if secondary_hits:
        score += min(15, 3 * len(secondary_hits))
        reasons.append("secondary keywords: " + ", ".join(secondary_hits[:6]))

    customer_engineering_title = is_customer_facing_engineering_title(job.title)
    substantial_customer_software = has_substantial_customer_software_ownership(
        job.title,
        job.description,
    )
    if customer_engineering_title and substantial_customer_software:
        score += 12
        reasons.append("forward deployed engineering lane match")
    elif customer_engineering_title:
        score -= 30
        risks.append("customer-facing title lacks substantial software engineering ownership")

    location_text = f"{job.location} {job.work_model}".lower()
    foreign_location_tokens = [
        "india",
        "pune",
        "chennai",
        "hyderabad",
        "bangalore",
        "bengaluru",
        "israel",
        "tel aviv",
        "raanana",
        "germany",
        "munich",
        "brno",
        "czech republic",
        "paris",
        "ireland",
        "galway",
        "irl -",
        "dublin",
        "canada",
        "vancouver",
        "toronto",
        "london",
        "england",
        "united kingdom",
        "sumida",
        "tokyo",
        "japan",
        "auckland",
        "new zealand",
        "sydney",
        "australia",
        "bangkok",
        "thailand",
        "mexico",
        "shanghai",
        "china",
        "seoul",
        "korea",
        "copenhagen",
        "denmark",
        "courbevoie",
        "france",
        "sweden",
        "stockholm",
        "poland",
        "warsaw",
        "hong kong",
        "singapore",
        "sao paulo",
        "brazil",
        "milano",
        "italy",
        "bucharest",
        "romania",
    ]
    us_location_tokens = [
        "remote - united states",
        "remote us",
        "remote, us",
        "remote, united states",
        "us remote",
        "remote usa",
        "remote-usa",
        "usa",
        "united states",
        "u.s.",
        "home office usa",
        "home office us",
        "atlanta",
        "alpharetta",
        "georgia",
    ]

    local_or_remote_tokens = [
        "remote - united states",
        "remote us",
        "remote, us",
        "remote, united states",
        "us remote",
        "remote-usa",
        "telecommute",
        "home office",
        "virtual",
        "work from home",
        "atlanta",
        "alpharetta",
        "georgia",
     ]
    has_foreign_location = any(token in location_text for token in foreign_location_tokens)
    has_georgia_location = bool(re.search(r"\bga\b", location_text))
    has_us_location = any(token in location_text for token in us_location_tokens) or has_georgia_location
    nonlocal_us = any(token in location_text for token in ["usa", "united states", "u.s."])
    is_local_or_remote = any(token in location_text for token in local_or_remote_tokens) or has_georgia_location

    if job.work_model.lower() == "remote":
        if has_foreign_location:
            score -= 30
            risks.append("remote role appears outside target geography")
            hard_reject = True
        elif has_us_location or not location_text.strip():
            score += 15
            reasons.append("remote role appears compatible")
        else:
            score -= 10
            risks.append("remote geography is unclear")
    elif job.work_model.lower() == "onsite" and not is_local_or_remote:
        score -= 30
        risks.append("onsite location is not in the candidate's target geography")
        hard_reject = True
    elif is_local_or_remote:
        score += 15
        reasons.append("location/work model appears compatible")
    elif nonlocal_us and job.work_model.lower() in {"flex", "hybrid"}:
        score += 5
        risks.append("nonlocal Flex/Hybrid role needs office-attendance clarification")
    elif has_foreign_location:
        score -= 30
        risks.append("location likely incompatible")
        hard_reject = True
    elif job.work_model.lower() in {"remote", "hybrid"} and location_text.strip():
        score -= 10
        risks.append("role geography is unclear or non-target")
    elif job.work_model == "Unknown":
        risks.append("work model unclear")

    dominant_exclusion_hits, weak_exclusion_hits = classify_exclusion_hits(
        haystack,
        title_text,
        description_text,
        exclusions,
    )
    if dominant_exclusion_hits:
        score -= min(30, 10 * len(dominant_exclusion_hits))
        risks.append("possible exclusion: " + ", ".join(dominant_exclusion_hits[:5]))
    elif weak_exclusion_hits:
        reasons.append("weak exclusion mentions were ignored")

    ai_primary_tokens = [
        "agentic ai",
        "machine learning",
        "rag",
        "llm",
        "model orchestration",
        "ai/ml",
        "ai platform",
        "ml platform",
    ]
    ai_primary_hits = [token for token in ai_primary_tokens if token in haystack]
    if ai_primary_hits and len(ai_primary_hits) >= 3 and not substantial_customer_software:
        score -= 45
        risks.append("primary AI/ML platform role")
        hard_reject = True

    if is_weather_science_ai_role(haystack):
        score = min(score - 45, 45)
        risks.append("weather science / AI product role outside target lane")
        hard_reject = True

    if is_primary_sre_observability_aiops_role(job.title, job.description):
        score = min(score - 45, 45)
        risks.append("primary SRE/observability/AIOps platform role")
        hard_reject = True

    if is_primary_ruby_on_rails_role(job.description):
        score = min(score - 45, 45)
        risks.append("primary Ruby on Rails stack mismatch")
        hard_reject = True

    if has_required_dotnet_stack(haystack):
        score = min(score - 45, 45)
        risks.append("required .NET tech stack")
        hard_reject = True

    hard_stack_gaps = clustered_hard_stack_requirement_gaps(haystack)
    if (
        len(hard_stack_gaps) >= 3
        and not (
            is_forward_deployed_title(job.title)
            and substantial_customer_software
        )
    ):
        risks.append("hard-stack requirement gaps: " + ", ".join(hard_stack_gaps[:8]))
        score = min(score - 35, 45)
        hard_reject = True

    consulting_hits = consulting_advisory_hits(haystack)
    if consulting_hits:
        score += min(12, 4 * len(consulting_hits))
        reasons.append("consulting/advisory lane match")
        if is_staffing_or_consulting_company(company):
            reasons.append("staffing/consulting lane match")

    if job.posted_age_days is None:
        score += 10
        reasons.append("posting date unavailable; treated as freshest bucket")
    elif job.posted_age_days > max_age_days:
        if is_live_official_posting(job):
            risks.append(LIVE_OLD_POSTING_FRESHNESS_RISK.format(max_age_days=max_age_days))
            reasons.append("live official posting requires freshness verification")
        else:
            score -= 50
            risks.append(f"older than {max_age_days} days")
    elif job.posted_age_days <= 7:
        score += 10
        reasons.append("posted within 7 days")
    elif job.posted_age_days <= 21:
        score += 7
        reasons.append("posted within 21 days")
    elif job.posted_age_days <= max_age_days:
        score += 3
        reasons.append(f"posted within {max_age_days} days")

    if job.verification_status != "official_live":
        score -= 25
        risks.append(job.verification_status)

    required_gap_hits = explicit_required_experience_gaps(haystack)
    if required_gap_hits:
        risks.append("required experience gaps: " + ", ".join(required_gap_hits[:8]))
        score -= min(45, 10 * len(required_gap_hits))
        if len(required_gap_hits) >= 3:
            score = min(score, 45)
            hard_reject = True
    elif existing_required_gap_count(risks) >= 3:
        score = min(score, 45)
        hard_reject = True

    if has_required_cpp_windows_gap(haystack) or any(
        risk.casefold() == "required c++/windows experience gap" for risk in risks
    ):
        risks.append("required C++/Windows experience gap")
        score = min(score - 35, 45)
        hard_reject = True

    job.fit_score = max(0, min(100, score))
    job.fit_reasons = dedupe(reasons)
    job.risk_flags = dedupe(risks)

    if hard_reject:
        job.decision = "Skip"
    elif any("needs freshness verification" in risk for risk in job.risk_flags):
        job.decision = NEEDS_FRESHNESS_VERIFICATION_DECISION
    elif any("older than" in risk for risk in job.risk_flags):
        job.decision = "Skip"
    else:
        assign_decision_from_score(job)


def assign_decision_from_score(job: JobPosting) -> None:
    if job.fit_score >= MIN_APPLY_FIT_SCORE and not any(
        "nonlocal Flex/Hybrid" in risk for risk in job.risk_flags
    ):
        job.decision = "Apply"
    elif job.fit_score >= 50:
        job.decision = "Maybe"
    elif 35 < job.fit_score <= 49:
        job.decision = "Watchlist"
    else:
        job.decision = "Skip"


def adapter_for_url(url: str, page: str = "") -> Adapter | None:
    if extract_workday_config(url):
        return WorkdayAdapter(url)
    if extract_greenhouse_token(url, page):
        return GreenhouseAdapter(url, page)
    if extract_lever_slug(url, page):
        return LeverAdapter(url, page)
    if extract_workable_slug(url, page):
        return WorkableAdapter(url, page)
    if extract_ashby_slug(url, page):
        return AshbyAdapter(url, page)
    if extract_adp_career_site_domain(url):
        return ADPAdapter(url)
    if looks_like_intuit_search_url(url):
        return IntuitAdapter(url, page)
    if extract_smartrecruiters_company(url, page):
        return SmartRecruitersAdapter(url, page)
    if looks_like_first_party_listing_url(url):
        return FirstPartyListingAdapter(url, page)
    if looks_like_insight_global_url(url, page):
        return InsightGlobalAdapter(url, page)
    if looks_like_atlassian_url(url, page):
        return AtlassianAdapter(url, page)
    if looks_like_icims_url(url, page):
        return ICIMSAdapter(url, page)
    return None


def discover_ats_sentinel(
    *,
    company: dict[str, Any],
    board_url: str,
    profile: dict[str, Any],
    today: dt.date,
    max_age_days: int,
    errors: list[DiscoveryError],
) -> list[JobPosting]:
    """Enumerate an ATS once without running the company's deep term search."""
    try:
        adapter = adapter_for_url(board_url)
    except ValueError as exc:
        errors.append(DiscoveryError(str(company.get("name") or "Unknown Company"), "sentinel", str(exc)))
        return []
    if adapter is None or adapter.name not in LIGHTWEIGHT_SENTINEL_ADAPTERS:
        return []
    if isinstance(adapter, (ICIMSAdapter, WorkableAdapter)):
        listing_url = adapter.url if isinstance(adapter, ICIMSAdapter) else adapter.board_url
        try:
            page = request_text(listing_url)
        except (HTTPError, URLError, TimeoutError, ValueError) as exc:
            errors.append(
                DiscoveryError(
                    str(company.get("name") or "Unknown Company"),
                    f"{adapter.name}-sentinel",
                    str(exc),
                )
            )
            return []
        candidate_urls = [
            url
            for url in extract_urls(page, listing_url)
            if (
                isinstance(adapter, ICIMSAdapter)
                and "/jobs/" in url
                and "icims.com" in url
            )
            or (
                isinstance(adapter, WorkableAdapter)
                and any(marker in url for marker in ["/j/", "/view/", "workable.com/"])
            )
        ]
        jobs: list[JobPosting] = []
        for url in dedupe(candidate_urls):
            title = infer_title_from_job_url(url) or "ATS Job Posting"
            identifier_match = re.search(r"/jobs/(\d+)", url)
            job = JobPosting(
                company=str(company.get("name") or "Unknown Company"),
                title=title,
                url=url,
                source=f"{adapter.name}-sentinel",
                job_id=identifier_match.group(1) if identifier_match else url.rstrip("/").rsplit("/", 1)[-1],
            )
            score_job(job, profile, max_age_days, company=company)
            jobs.append(job)
        return jobs
    if not isinstance(adapter, WorkdayAdapter):
        return adapter.discover(company, profile, today, max_age_days, errors)

    endpoint = f"{adapter.base}/wday/cxs/{adapter.tenant}/{adapter.site}/jobs"
    payload = {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": ""}
    try:
        response = request_json(endpoint, method="POST", payload=payload)
    except (HTTPError, URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
        errors.append(DiscoveryError(str(company.get("name") or "Unknown Company"), "workday-sentinel", str(exc)))
        return []

    postings = response.get("jobPostings", []) if isinstance(response, dict) else []
    jobs: list[JobPosting] = []
    for item in postings:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title", "")).strip()
        external_path = str(item.get("externalPath", "")).strip()
        if not title or not external_path:
            continue
        location = str(item.get("locationsText", "")).strip()
        posted_on, age, date_basis = parse_posted_age(str(item.get("postedOn", "")), today)
        job = JobPosting(
            company=str(company.get("name") or "Unknown Company"),
            title=title,
            url=urljoin(adapter.base, f"/{adapter.site}{external_path}"),
            location=location,
            work_model=infer_work_model(location),
            posted_on=posted_on,
            posted_age_days=age,
            date_basis=date_basis,
            source="workday-sentinel",
            job_id=str((item.get("bulletFields") or [""])[0] or external_path),
        )
        score_job(job, profile, max_age_days, company=company)
        jobs.append(job)
    return jobs


def hydrate_ats_sentinel_job(
    *,
    board_url: str,
    job: JobPosting,
    profile: dict[str, Any],
    today: dt.date,
    max_age_days: int,
    company: dict[str, Any],
    errors: list[DiscoveryError],
) -> JobPosting:
    adapter = adapter_for_url(board_url)
    if isinstance(adapter, WorkdayAdapter):
        job = adapter.fetch_detail(job, today, errors)
        score_job(job, profile, max_age_days, company=company)
    elif isinstance(adapter, (ICIMSAdapter, WorkableAdapter)):
        detailed = fetch_job_page_jobs(
            company,
            job.url,
            today,
            profile,
            max_age_days,
            errors,
            f"{adapter.name}-sentinel",
        )
        if detailed:
            job = detailed[0]
    job.risk_flags.append("new job ID detected by daily ATS sentinel")
    job.risk_flags = dedupe(job.risk_flags)
    return job


def discover_company(
    company: dict[str, Any],
    profile: dict[str, Any],
    today: dt.date,
    max_age_days: int,
    errors: list[DiscoveryError],
    pause_seconds: float,
    ats_only: bool,
    ats_registry: dict[str, list[str]] | None = None,
) -> list[JobPosting]:
    careers_url = company.get("careers_url", "")
    configured_ats_urls = [
        str(url).strip()
        for url in company.get("ats_urls", [])
        if str(url).strip()
    ] if isinstance(company.get("ats_urls", []), list) else []
    known_ats_urls: list[str] = []
    if ats_registry is not None:
        known_ats_urls.extend(ats_registry_urls_for_company(ats_registry, company))
    known_ats_urls.extend(configured_ats_urls)
    urls = [*known_ats_urls]
    careers_adapter = adapter_for_url(careers_url) if careers_url else None
    if not ats_only and not careers_adapter and not known_ats_urls:
        urls.extend(discover_ats_links(company, errors))
    if careers_url:
        urls.append(careers_url)

    jobs: list[JobPosting] = []
    seen_sources: set[str] = set()
    for url in dedupe([value for value in urls if value]):
        try:
            adapter = adapter_for_url(url)
        except ValueError as exc:
            errors.append(DiscoveryError(company["name"], "adapter", str(exc)))
            continue
        if not adapter:
            continue
        identity = ats_identity_key(url)
        source_key = f"{adapter.name}:{identity[1]}" if identity else f"{adapter.name}:{url}"
        if source_key in seen_sources:
            continue
        seen_sources.add(source_key)
        jobs.extend(adapter.discover(company, profile, today, max_age_days, errors))
        if pause_seconds:
            time.sleep(pause_seconds)

    if not ats_only and not jobs and careers_url:
        jobs.extend(HtmlFallbackAdapter().discover(company, profile, today, max_age_days, errors))
    return jobs


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


def write_markdown_summary(
    path: Path,
    jobs: list[JobPosting],
    errors: list[DiscoveryError],
    today: dt.date,
) -> None:
    lines: list[str] = []
    lines.append(f"# ATS Discovery - {today.isoformat()}")
    lines.append("")
    lines.append("## Apply / Maybe")
    lines.append("")
    lines.append("| Decision | Company | Role | Posted | Location / Work Model | Fit | Source | URL |")
    lines.append("|---|---|---|---|---|---:|---|---|")
    for job in sorted(jobs, key=lambda item: (-item.fit_score, item.company, item.title)):
        if job.decision not in {"Apply", "Maybe"}:
            continue
        posted = job.posted_on or job.date_basis or "Unknown"
        lines.append(
            f"| {job.decision} | {job.company} | {escape_md(job.title)} | "
            f"{escape_md(posted)} | {escape_md(job.location)} / {job.work_model} | "
            f"{job.fit_score} | {job.source} | {job.url} |"
        )

    lines.append("")
    lines.append("## Reviewed / Skip")
    lines.append("")
    lines.append("| Company | Role | Posted | Fit | Reason | URL |")
    lines.append("|---|---|---|---:|---|---|")
    for job in sorted(jobs, key=lambda item: (item.company, item.title)):
        if job.decision != "Skip":
            continue
        reason = "; ".join(job.risk_flags[:3] or job.fit_reasons[:2] or ["low score"])
        posted = job.posted_on or job.date_basis or "Unknown"
        lines.append(
            f"| {job.company} | {escape_md(job.title)} | {escape_md(posted)} | "
            f"{job.fit_score} | {escape_md(reason)} | {job.url} |"
        )

    lines.append("")
    lines.append("## Company Diagnostics")
    lines.append("")
    lines.append("| Company | Total | Apply | Maybe | Watchlist | Skip | Top Skip Reason |")
    lines.append("|---|---:|---:|---:|---:|---:|---|")
    for diagnostic in build_company_diagnostics(jobs):
        lines.append(
            f"| {diagnostic.company} | {diagnostic.total_jobs} | {diagnostic.apply_jobs} | "
            f"{diagnostic.maybe_jobs} | {diagnostic.watchlist_jobs} | {diagnostic.skip_jobs} | "
            f"{escape_md(diagnostic.top_skip_reason or '-')} |"
        )

    lines.append("")
    lines.append("## Adapter Errors")
    lines.append("")
    if not errors:
        lines.append("- None")
    else:
        for error in errors[:100]:
            lines.append(f"- {error.company} [{error.source}]: {escape_md(error.message)}")
        if len(errors) > 100:
            lines.append(f"- ... {len(errors) - 100} more errors omitted")

    path.write_text("\n".join(lines), encoding="utf-8")


def build_company_diagnostics(jobs: list[JobPosting]) -> list[CompanyDiagnostic]:
    grouped: dict[str, list[JobPosting]] = {}
    for job in jobs:
        grouped.setdefault(job.company, []).append(job)

    diagnostics: list[CompanyDiagnostic] = []
    for company, company_jobs in sorted(grouped.items()):
        top_skip_reason = "-"
        skip_reason_counts: dict[str, int] = {}
        for job in company_jobs:
            if job.decision != "Skip":
                continue
            reason = job.risk_flags[0] if job.risk_flags else "low score"
            skip_reason_counts[reason] = skip_reason_counts.get(reason, 0) + 1
        if skip_reason_counts:
            top_skip_reason = sorted(skip_reason_counts.items(), key=lambda item: (-item[1], item[0]))[0][0]

        diagnostics.append(
            CompanyDiagnostic(
                company=company,
                total_jobs=len(company_jobs),
                apply_jobs=sum(1 for job in company_jobs if job.decision == "Apply"),
                maybe_jobs=sum(1 for job in company_jobs if job.decision == "Maybe"),
                watchlist_jobs=sum(1 for job in company_jobs if job.decision == "Watchlist"),
                skip_jobs=sum(1 for job in company_jobs if job.decision == "Skip"),
                top_skip_reason=top_skip_reason,
            )
        )
    return diagnostics


def write_public_verified_summary(path: Path, jobs: list[JobPosting], today: dt.date) -> None:
    public_jobs = [job for job in jobs if job.source.startswith("public-board:")]
    lines: list[str] = []
    lines.append(f"# Verified Public-Board Discoveries - {today.isoformat()}")
    lines.append("")
    lines.append(
        "These jobs were first found through public job-board search results, then "
        "resolved to an official company or ATS posting before being scored."
    )
    lines.append("")
    lines.append("## Apply / Maybe")
    lines.append("")
    lines.append("| Decision | Company | Role | Posted | Location / Work Model | Fit | Verified Source | URL |")
    lines.append("|---|---|---|---|---|---:|---|---|")
    for job in sorted(public_jobs, key=lambda item: (-item.fit_score, item.company, item.title)):
        if job.decision not in {"Apply", "Maybe"}:
            continue
        posted = job.posted_on or job.date_basis or "Unknown"
        lines.append(
            f"| {job.decision} | {job.company} | {escape_md(job.title)} | "
            f"{escape_md(posted)} | {escape_md(job.location)} / {job.work_model} | "
            f"{job.fit_score} | {escape_md(job.source)} | {job.url} |"
        )

    lines.append("")
    lines.append("## Verified But Skipped")
    lines.append("")
    lines.append("| Company | Role | Posted | Fit | Reason | URL |")
    lines.append("|---|---|---|---:|---|---|")
    for job in sorted(public_jobs, key=lambda item: (item.company, item.title)):
        if job.decision != "Skip":
            continue
        reason = "; ".join(job.risk_flags[:3] or job.fit_reasons[:2] or ["low score"])
        posted = job.posted_on or job.date_basis or "Unknown"
        lines.append(
            f"| {job.company} | {escape_md(job.title)} | {escape_md(posted)} | "
            f"{job.fit_score} | {escape_md(reason)} | {job.url} |"
        )

    lines.append("")
    lines.append("## Verification Gate")
    lines.append("")
    lines.append("- Raw LinkedIn, Indeed, Glassdoor, Dice, Built In, RemoteOK, and Wellfound URLs are not recommended directly.")
    lines.append("- A public-board hit must resolve to a live official company or ATS page.")
    lines.append("- Public-board discoveries without an official posting date are forced to Skip.")

    path.write_text("\n".join(lines), encoding="utf-8")


def write_x_post_summary(
    path: Path,
    leads: list[XPostLead],
    query_outcomes: list[SocialQueryOutcome],
    today: dt.date,
) -> None:
    write_social_post_summary(
        path=path,
        leads=leads,
        query_outcomes=query_outcomes,
        today=today,
        heading="X.com Job Leads",
        intro_label="X.com",
        review_section="X Posts to Review",
    )


def write_threads_post_summary(
    path: Path,
    leads: list[XPostLead],
    query_outcomes: list[SocialQueryOutcome],
    today: dt.date,
) -> None:
    write_social_post_summary(
        path=path,
        leads=leads,
        query_outcomes=query_outcomes,
        today=today,
        heading="Threads Job Leads",
        intro_label="Threads",
        review_section="Threads Posts to Review",
    )


def write_social_post_summary(
    *,
    path: Path,
    leads: list[XPostLead],
    query_outcomes: list[SocialQueryOutcome],
    today: dt.date,
    heading: str,
    intro_label: str,
    review_section: str,
) -> None:
    lines: list[str] = []
    lines.append(f"# {heading} - {today.isoformat()}")
    lines.append("")
    lines.append(
        f"These {intro_label} posts were filtered to posts dated within the last 24 hours at run time. "
        "This scan is intentionally shallow: it records the post, the user, the post text, and a best-effort check "
        "for the first same-user follow-up visible on the fetched page."
    )
    lines.append("")
    lines.append("## Search Coverage")
    lines.append("")
    lines.append("| Search Query | Manual Search URL | Raw Results | Leads Kept | Notes |")
    lines.append("|---|---|---:|---:|---|")
    for outcome in query_outcomes:
        note = "No results returned" if outcome.results_found == 0 else ""
        search_url = build_social_platform_search_url(intro_label, outcome.query)
        lines.append(
            f"| {escape_md(outcome.query)} | {search_url} | {outcome.results_found} | "
            f"{outcome.leads_collected} | {escape_md(note)} |"
        )

    lines.append("")
    lines.append(f"## {review_section}")
    lines.append("")
    lines.append("| Posted | Age (hours) | Search Query | User | Post Text | Same-User Follow-up | Post URL |")
    lines.append("|---|---:|---|---|---|---|---|")
    for lead in sorted(leads, key=lambda item: (item.posted_on, -(item.posted_age_hours or 0)), reverse=True):
        age = "" if lead.posted_age_hours is None else str(lead.posted_age_hours)
        lines.append(
            f"| {escape_md(lead.posted_on or 'Unknown')} | {age} | {escape_md(lead.query)} | "
            f"{escape_md(lead.author_handle)} | {escape_md(lead.post_text)} | "
            f"{escape_md(lead.same_user_follow_up)} | {lead.post_url} |"
        )

    lines.append("")
    lines.append("## Review Rules")
    lines.append("")
    lines.append("- Only the first page for each post is fetched.")
    lines.append("- Only the first same-user follow-up visible on that fetched page is noted.")
    lines.append("- No deeper thread crawling or job-page verification is performed in this social pass.")
    if not leads:
        lines.append("- No qualifying posts were captured in this run.")

    path.write_text("\n".join(lines), encoding="utf-8")


def write_public_board_queries(path: Path, companies: list[dict[str, Any]], profile: dict[str, Any]) -> None:
    titles = [
        "Senior Technical Consultant",
        "Solution Architect",
        "Implementation Architect",
        "Professional Services Engineer",
        "Senior Software Engineer Java",
        "Integration Consultant",
    ]
    lines: list[str] = []
    lines.append("# Public Job Board Discovery Queries")
    lines.append("")
    lines.append(
        "Use these as discovery links only. Verify any candidate against the official company job page. "
        "This file is intentionally compact and only includes a bounded sample of companies, titles, and sources."
    )
    lines.append("")
    for company in companies[:MAX_PUBLIC_BOARD_QUERY_FILE_COMPANIES]:
        name = company["name"]
        lines.append(f"## {name}")
        for title in titles[:MAX_PUBLIC_BOARD_QUERY_FILE_TITLES]:
            query = f'{name} "{title}" Java API integration remote Atlanta'
            lines.append(f"- {title}:")
            for source in PUBLIC_BOARD_SOURCES[:MAX_PUBLIC_BOARD_QUERY_FILE_SOURCES]:
                params = {
                    key: value.replace("{query}", query)
                    for key, value in source["params"].items()
                }
                url = source["url"]
                if params:
                    url = f"{url}?{urlencode(params)}"
                lines.append(f"  - [{source['name']}]({url})")
        lines.append("")
    if len(companies) > MAX_PUBLIC_BOARD_QUERY_FILE_COMPANIES:
        lines.append(
            f"- Additional companies omitted from this sample: {len(companies) - MAX_PUBLIC_BOARD_QUERY_FILE_COMPANIES}"
        )
    path.write_text("\n".join(lines), encoding="utf-8")


def escape_md(value: str) -> str:
    return (value or "").replace("|", "\\|").replace("\n", " ")


def run(
    *,
    companies_path: Path,
    profile_path: Path,
    out_dir: Path,
    max_age_days: int,
    ats_registry_path: Path | None,
    manual_job_urls_path: Path | None,
    job_overrides_path: Path | None,
    limit_companies: int | None,
    company_names: list[str] | None,
    pause_seconds: float,
    ats_only: bool,
    include_public_boards: bool,
    public_board_max_companies: int | None,
    public_board_queries_per_company: int,
    public_board_results_per_query: int,
    public_board_total_query_limit: int,
    public_board_total_hit_limit: int,
    public_board_time_budget_seconds: int,
    x_post_results_per_query: int,
    x_post_total_hit_limit: int,
    x_post_time_budget_seconds: int,
    threads_post_results_per_query: int,
    threads_post_total_hit_limit: int,
    threads_post_time_budget_seconds: int,
) -> tuple[list[JobPosting], list[DiscoveryError], Path, Path, Path, Path, Path, Path]:
    companies = normalize_company_entries(load_yaml(companies_path))
    profile = profile_doc_to_profile(load_yaml(profile_path))
    companies = [
        company for company in companies
        if not is_excluded_company(str(company.get("name", "")), profile)
    ]
    ats_registry = load_ats_registry(ats_registry_path) if ats_registry_path else {}
    job_overrides = load_job_overrides(job_overrides_path) if job_overrides_path else []
    if company_names:
        allowed = set(company_names)
        companies = [company for company in companies if company["name"] in allowed]
    if limit_companies:
        companies = companies[:limit_companies]

    today = dt.date.today()
    now = dt.datetime.now(dt.timezone.utc)
    out_dir.mkdir(parents=True, exist_ok=True)

    all_jobs: list[JobPosting] = []
    errors: list[DiscoveryError] = []
    for company in companies:
        all_jobs.extend(
            discover_company(
                company,
                profile,
                today,
                max_age_days,
                errors,
                pause_seconds,
                ats_only,
                ats_registry=ats_registry,
            )
        )

    if manual_job_urls_path:
        all_jobs.extend(
            discover_manual_job_urls(
                manual_path=manual_job_urls_path,
                companies=companies,
                profile=profile,
                today=today,
                max_age_days=max_age_days,
                errors=errors,
            )
        )

    all_jobs.extend(
        discover_usajobs(
            profile=profile,
            today=today,
            max_age_days=max_age_days,
            errors=errors,
        )
    )

    if include_public_boards:
        all_jobs.extend(
            discover_public_board_verified_jobs(
                companies=companies,
                profile=profile,
                today=today,
                max_age_days=max_age_days,
                errors=errors,
                pause_seconds=pause_seconds,
                max_companies=public_board_max_companies,
                queries_per_company=public_board_queries_per_company,
                results_per_query=public_board_results_per_query,
                total_query_limit=public_board_total_query_limit,
                total_hit_limit=public_board_total_hit_limit,
                time_budget_seconds=public_board_time_budget_seconds,
            )
        )

    x_post_leads, x_query_outcomes = discover_x_posts(
        companies=companies,
        profile=profile,
        today=today,
        now=now,
        max_age_days=max_age_days,
        errors=errors,
        results_per_query=x_post_results_per_query,
        total_hit_limit=x_post_total_hit_limit,
        time_budget_seconds=x_post_time_budget_seconds,
        pause_seconds=pause_seconds,
    )
    threads_post_leads, threads_query_outcomes = discover_threads_posts(
        companies=companies,
        profile=profile,
        today=today,
        now=now,
        max_age_days=max_age_days,
        errors=errors,
        results_per_query=threads_post_results_per_query,
        total_hit_limit=threads_post_total_hit_limit,
        time_budget_seconds=threads_post_time_budget_seconds,
        pause_seconds=pause_seconds,
    )

    all_jobs = [job for job in all_jobs if not is_excluded_company(job.company, profile)]
    deduped_jobs = dedupe_jobs(all_jobs)
    apply_job_overrides(deduped_jobs, job_overrides)
    json_path = out_dir / f"{today.isoformat()}-discovered-jobs.json"
    md_path = out_dir / f"{today.isoformat()}-ats-discovery.md"
    query_path = out_dir / f"{today.isoformat()}-public-job-board-queries.md"
    verified_public_path = out_dir / f"{today.isoformat()}-verified-public-board-jobs.md"
    x_post_path = out_dir / f"{today.isoformat()}-x-job-posts.md"
    threads_post_path = out_dir / f"{today.isoformat()}-threads-job-posts.md"

    write_json(
        json_path,
        build_discovered_jobs_payload(
            jobs=deduped_jobs,
            errors=errors,
            generated_on=today.isoformat(),
            max_age_days=max_age_days,
        ),
    )
    write_markdown_summary(md_path, deduped_jobs, errors, today)
    write_public_board_queries(query_path, companies, profile)
    write_public_verified_summary(verified_public_path, deduped_jobs, today)
    write_x_post_summary(x_post_path, x_post_leads, x_query_outcomes, today)
    write_threads_post_summary(threads_post_path, threads_post_leads, threads_query_outcomes, today)

    return deduped_jobs, errors, json_path, md_path, query_path, verified_public_path, x_post_path, threads_post_path


def dedupe_jobs(jobs: list[JobPosting]) -> list[JobPosting]:
    seen: set[str] = set()
    result: list[JobPosting] = []
    for job in jobs:
        key = job.url or f"{job.company}|{job.job_id}|{job.title}|{job.location}"
        if key in seen:
            continue
        seen.add(key)
        result.append(job)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Discover jobs from public ATS endpoints.")
    parser.add_argument("--companies", default="companies.yml")
    parser.add_argument("--profile", default="candidate_profile.yml")
    parser.add_argument("--out", default="outputs")
    parser.add_argument("--max-age-days", type=int, default=DEFAULT_MAX_AGE_DAYS)
    parser.add_argument("--ats-registry", default="ats_registry.yml")
    parser.add_argument("--manual-job-urls", default="manual_job_urls.yml")
    parser.add_argument("--job-overrides", default="job_overrides.yml")
    parser.add_argument("--limit-companies", type=int)
    parser.add_argument(
        "--company-name",
        action="append",
        help="Limit discovery to one or more exact company names. Can be passed multiple times.",
    )
    parser.add_argument("--pause-seconds", type=float, default=0.1)
    parser.add_argument(
        "--ats-only",
        action="store_true",
        help="Only query ATS URLs already present in companies.yml; skip marketing-page fallback crawling.",
    )
    parser.add_argument(
        "--include-public-boards",
        action="store_true",
        help="Search public job boards as discovery hints and verify hits against official company/ATS pages.",
    )
    parser.add_argument(
        "--public-board-max-companies",
        type=int,
        help="Limit public-board discovery to the first N configured companies.",
    )
    parser.add_argument(
        "--public-board-queries-per-company",
        type=int,
        default=MAX_PUBLIC_BOARD_QUERIES_PER_COMPANY,
        help="Maximum public-board search queries per company.",
    )
    parser.add_argument(
        "--public-board-results-per-query",
        type=int,
        default=MAX_PUBLIC_BOARD_RESULTS_PER_QUERY,
        help="Maximum public-board results to inspect per search query.",
    )
    parser.add_argument(
        "--public-board-total-query-limit",
        type=int,
        default=MAX_PUBLIC_BOARD_TOTAL_QUERIES,
        help="Maximum public-board search queries to run in one discovery pass.",
    )
    parser.add_argument(
        "--public-board-total-hit-limit",
        type=int,
        default=MAX_PUBLIC_BOARD_TOTAL_HITS,
        help="Maximum public-board hits to verify in one run.",
    )
    parser.add_argument(
        "--public-board-time-budget-seconds",
        type=int,
        default=PUBLIC_BOARD_TIME_BUDGET_SECONDS,
        help="Maximum wall-clock seconds to spend in public-board discovery.",
    )
    parser.add_argument(
        "--x-post-results-per-query",
        type=int,
        default=MAX_X_POST_RESULTS_PER_QUERY,
        help="Maximum X.com post results to inspect per search query.",
    )
    parser.add_argument(
        "--x-post-total-hit-limit",
        type=int,
        default=MAX_X_POST_TOTAL_HITS,
        help="Maximum combined verified X-job hits and manual-review X posts to keep in one run.",
    )
    parser.add_argument(
        "--x-post-time-budget-seconds",
        type=int,
        default=X_POST_TIME_BUDGET_SECONDS,
        help="Maximum wall-clock seconds to spend in X.com post discovery.",
    )
    parser.add_argument(
        "--threads-post-results-per-query",
        type=int,
        default=MAX_X_POST_RESULTS_PER_QUERY,
        help="Maximum Threads post results to inspect per search query.",
    )
    parser.add_argument(
        "--threads-post-total-hit-limit",
        type=int,
        default=MAX_X_POST_TOTAL_HITS,
        help="Maximum combined verified Threads-job hits and manual-review Threads posts to keep in one run.",
    )
    parser.add_argument(
        "--threads-post-time-budget-seconds",
        type=int,
        default=X_POST_TIME_BUDGET_SECONDS,
        help="Maximum wall-clock seconds to spend in Threads post discovery.",
    )
    args = parser.parse_args()

    try:
        jobs, errors, json_path, md_path, query_path, verified_public_path, x_post_path, threads_post_path = run(
            companies_path=Path(args.companies),
            profile_path=Path(args.profile),
            out_dir=Path(args.out),
            max_age_days=args.max_age_days,
            ats_registry_path=Path(args.ats_registry),
            manual_job_urls_path=Path(args.manual_job_urls),
            job_overrides_path=Path(args.job_overrides),
            limit_companies=args.limit_companies,
            company_names=args.company_name,
            pause_seconds=args.pause_seconds,
            ats_only=args.ats_only,
            include_public_boards=args.include_public_boards,
            public_board_max_companies=args.public_board_max_companies,
            public_board_queries_per_company=args.public_board_queries_per_company,
            public_board_results_per_query=args.public_board_results_per_query,
            public_board_total_query_limit=args.public_board_total_query_limit,
            public_board_total_hit_limit=args.public_board_total_hit_limit,
            public_board_time_budget_seconds=args.public_board_time_budget_seconds,
            x_post_results_per_query=args.x_post_results_per_query,
            x_post_total_hit_limit=args.x_post_total_hit_limit,
            x_post_time_budget_seconds=args.x_post_time_budget_seconds,
            threads_post_results_per_query=args.threads_post_results_per_query,
            threads_post_total_hit_limit=args.threads_post_total_hit_limit,
            threads_post_time_budget_seconds=args.threads_post_time_budget_seconds,
        )
    except Exception as exc:
        print(f"discover_jobs.py failed: {exc}", file=sys.stderr)
        raise

    apply_count = sum(1 for job in jobs if job.decision == "Apply")
    maybe_count = sum(1 for job in jobs if job.decision == "Maybe")
    skip_count = sum(1 for job in jobs if job.decision == "Skip")
    print(f"Wrote discovered jobs: {json_path}")
    print(f"Wrote ATS summary: {md_path}")
    print(f"Wrote public-board queries: {query_path}")
    print(f"Wrote verified public-board summary: {verified_public_path}")
    print(f"Wrote X.com post summary: {x_post_path}")
    print(f"Wrote Threads post summary: {threads_post_path}")
    print(
        f"Discovered {len(jobs)} official jobs "
        f"({apply_count} Apply, {maybe_count} Maybe, {skip_count} Skip); "
        f"{len(errors)} adapter errors"
    )


if __name__ == "__main__":
    main()
