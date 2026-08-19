#!/usr/bin/env python3
"""Greenhouse Job Board adapter.

Uses the public Greenhouse Job Board API only:

    GET https://boards-api.greenhouse.io/v1/boards/<board_token>/jobs?content=true

This module never calls OpenAI. Fetched jobs are mapped into the same
incoming-job dictionaries used by the local JSON source.
"""

import re
from html import unescape
from html.parser import HTMLParser

import requests

from job_source_config import GREENHOUSE_BOARDS
from job_sources import JobSource, clean_text, parse_salary_number


GREENHOUSE_API_ROOT = "https://boards-api.greenhouse.io/v1/boards"
GREENHOUSE_TIMEOUT = (5, 20)
GREENHOUSE_USER_AGENT = "AI-Job-Operator/1.0 (personal job search)"

REMOTE_METADATA_NAMES = (
    "remote",
    "workplace",
    "work type",
    "work arrangement",
    "location type",
    "office type",
    "work mode",
)
EMPLOYMENT_METADATA_NAMES = (
    "employment type",
    "job type",
    "type of employment",
    "employment",
)
SALARY_METADATA_NAMES = (
    "salary",
    "compensation",
    "pay range",
    "pay",
    "base pay",
    "annual salary",
)


class HTMLToTextParser(HTMLParser):
    """Turn HTML into readable plain text without dropping list items."""

    BLOCK_TAGS = {
        "p", "div", "br", "li", "ul", "ol", "tr", "table", "section",
        "article", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote",
    }
    SKIP_TAGS = {"script", "style"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.chunks = []
        self.skip_depth = 0

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in self.SKIP_TAGS:
            self.skip_depth += 1
            return
        if self.skip_depth:
            return
        if tag in self.BLOCK_TAGS:
            self.chunks.append("\n")
        if tag == "li":
            self.chunks.append("- ")

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in self.SKIP_TAGS and self.skip_depth:
            self.skip_depth -= 1
            return
        if self.skip_depth:
            return
        if tag in {"p", "div", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "tr"}:
            self.chunks.append("\n")

    def handle_data(self, data):
        if not self.skip_depth and data:
            self.chunks.append(data)


def decode_greenhouse_html(value):
    """Undo Greenhouse HTML entity encoding without guessing extra content."""
    text = str(value)
    for _ in range(2):
        decoded = unescape(text)
        if decoded == text:
            break
        text = decoded
    return text


def html_to_text(value):
    """Convert HTML job content into plain text the prefilter can read."""
    if value is None:
        return ""
    text = decode_greenhouse_html(value).strip()
    if not text:
        return ""
    if "<" not in text and ">" not in text:
        return collapse_plain_text(text)
    parser = HTMLToTextParser()
    try:
        parser.feed(text)
        parser.close()
        converted = "".join(parser.chunks)
    except Exception:
        converted = text
    return collapse_plain_text(converted)


def collapse_plain_text(text):
    """Keep paragraph and list breaks, but collapse extra whitespace."""
    lines = []
    previous_blank = False
    for raw_line in str(text).replace("\r", "\n").split("\n"):
        line = " ".join(raw_line.split())
        if not line:
            if not previous_blank and lines:
                lines.append("")
            previous_blank = True
            continue
        previous_blank = False
        lines.append(line)
    return "\n".join(lines).strip()


def normalize_board_token(value):
    """Accept a board slug, or extract it from a pasted Greenhouse URL."""
    text = clean_text(value)
    if not text:
        return ""
    lowered = text.lower()
    markers = (
        "https://boards-api.greenhouse.io/v1/boards/",
        "http://boards-api.greenhouse.io/v1/boards/",
        "https://boards.greenhouse.io/",
        "http://boards.greenhouse.io/",
        "boards.greenhouse.io/",
    )
    for marker in markers:
        if marker in lowered:
            remainder = lowered.split(marker, 1)[1]
            remainder = remainder.split("?", 1)[0].split("/", 1)[0]
            text = remainder
            break
    text = text.strip().strip("/")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", text):
        return ""
    return text


def configured_greenhouse_boards():
    """Return live Greenhouse boards from hardcoded config plus approved sources."""
    from source_registry import load_approved_maps, merge_source_maps

    hardcoded = {}
    raw_boards = GREENHOUSE_BOARDS or {}
    if isinstance(raw_boards, dict):
        for company_name, token in raw_boards.items():
            name = clean_text(company_name)
            board_token = normalize_board_token(token)
            if name and board_token:
                hardcoded[name] = board_token
    approved = {}
    for company_name, token in (load_approved_maps().get("greenhouse") or {}).items():
        name = clean_text(company_name)
        board_token = normalize_board_token(token)
        if name and board_token:
            approved[name] = board_token
    return merge_source_maps(hardcoded, approved)


def greenhouse_location_text(job):
    """Preserve the displayed location string from the Greenhouse payload."""
    location = job.get("location")
    if isinstance(location, dict):
        name = clean_text(location.get("name"))
        if name:
            return name
    if isinstance(location, str):
        name = clean_text(location)
        if name:
            return name
    offices = job.get("offices") or []
    names = []
    if isinstance(offices, list):
        for office in offices:
            if not isinstance(office, dict):
                continue
            office_location = clean_text(office.get("location") or office.get("name"))
            if office_location and office_location not in names:
                names.append(office_location)
    return "; ".join(names)


def _compact_label(text):
    """Lowercase a location/work-mode label and collapse punctuation."""
    cleaned = clean_text(text).lower()
    cleaned = cleaned.replace("&", " and ")
    cleaned = re.sub(r"[()/\\]", " ", cleaned)
    cleaned = re.sub(r"[\s,_-]+", " ", cleaned)
    return cleaned.strip()


def classify_work_label(text):
    """Classify one short label as remote, hybrid, onsite, or unknown."""
    compact = _compact_label(text)
    if not compact:
        return ""
    if re.search(r"\bnot remote\b|\bno remote\b", compact):
        return ""
    if re.search(r"\bhybrid\b", compact):
        return "hybrid"
    if re.search(r"\b(on site|onsite|in office|in-office)\b", compact):
        return "onsite"
    if re.search(r"\b(remote|work from home|wfh)\b", compact):
        return "remote"
    if compact in {"remote", "wfh"}:
        return "remote"
    return ""


def split_location_parts(text):
    """Split a location string into city/arrangement parts."""
    if not clean_text(text):
        return []
    return [part.strip() for part in re.split(r"\s*(?:,|;|\||\bor\b)\s*", text, flags=re.I) if part.strip()]


def infer_clear_remote_type(location_text, metadata_remote=""):
    """Infer remote/hybrid only when the job data clearly supports it."""
    explicit = classify_work_label(metadata_remote)
    if explicit:
        return explicit
    parts = split_location_parts(location_text)
    if not parts:
        return ""
    labels = [classify_work_label(part) for part in parts]
    if labels and all(label == "remote" for label in labels):
        return "remote"
    if labels and all(label == "hybrid" for label in labels):
        return "hybrid"
    if labels and all(label == "onsite" for label in labels):
        return "onsite"
    return ""


def metadata_items(job):
    """Return Greenhouse metadata dictionaries, ignoring malformed values."""
    metadata = job.get("metadata") or []
    if not isinstance(metadata, list):
        return []
    return [item for item in metadata if isinstance(item, dict)]


def metadata_value(job, name_keywords):
    """Return the first metadata value whose name matches a keyword."""
    for item in metadata_items(job):
        name = _compact_label(item.get("name"))
        if any(keyword in name for keyword in name_keywords):
            return item.get("value"), item
    return None, None


def format_salary_text(min_value, max_value, extra=""):
    """Build a short salary display string from parsed numbers."""
    pieces = []
    if min_value != "" and max_value != "":
        pieces.append(f"{min_value}-{max_value}")
    elif min_value != "":
        pieces.append(str(min_value))
    elif max_value != "":
        pieces.append(str(max_value))
    extra_text = clean_text(extra)
    if extra_text and extra_text not in pieces:
        pieces.append(extra_text)
    return " ".join(str(piece) for piece in pieces)


def parse_salary_value(value):
    """Extract salary_min, salary_max, and salary_text from a metadata value."""
    if value is None or value == "":
        return "", "", ""
    if isinstance(value, (int, float)):
        number = parse_salary_number(value)
        return number, "", str(number) if number != "" else ""
    if isinstance(value, dict):
        salary_min = parse_salary_number(
            value.get("min_value", value.get("min", value.get("from", "")))
        )
        salary_max = parse_salary_number(
            value.get("max_value", value.get("max", value.get("to", "")))
        )
        unit = clean_text(value.get("unit") or value.get("currency") or "")
        text = format_salary_text(salary_min, salary_max, unit)
        return salary_min, salary_max, text
    if isinstance(value, list):
        texts = []
        mins = []
        maxes = []
        for item in value:
            item_min, item_max, item_text = parse_salary_value(item)
            if item_min != "":
                mins.append(item_min)
            if item_max != "":
                maxes.append(item_max)
            if item_text:
                texts.append(item_text)
        salary_min = min(mins) if mins else ""
        salary_max = max(maxes) if maxes else ""
        return salary_min, salary_max, "; ".join(texts)
    text = clean_text(value)
    numbers = re.findall(r"\$?\d[\d,]*(?:\.\d+)?k?", text.lower().replace(" ", ""))
    parsed = [parse_salary_number(item) for item in numbers]
    parsed = [item for item in parsed if item != ""]
    salary_min = parsed[0] if parsed else ""
    salary_max = parsed[1] if len(parsed) > 1 else ""
    return salary_min, salary_max, text


def greenhouse_salary_fields(job):
    """Read salary only from explicit pay fields. Do not invent numbers."""
    value, _item = metadata_value(job, SALARY_METADATA_NAMES)
    if value not in (None, ""):
        return parse_salary_value(value)
    pay_ranges = job.get("pay_input_ranges") or job.get("pay_ranges") or []
    if pay_ranges:
        return parse_salary_value(pay_ranges)
    return "", "", ""


def greenhouse_employment_type(job):
    """Read employment type from metadata when the feed provides it."""
    value, _item = metadata_value(job, EMPLOYMENT_METADATA_NAMES)
    if isinstance(value, list):
        return ", ".join(clean_text(item) for item in value if clean_text(item))
    return clean_text(value)


def greenhouse_metadata_remote(job):
    """Read an explicit workplace-type metadata field when present."""
    value, _item = metadata_value(job, REMOTE_METADATA_NAMES)
    if isinstance(value, list):
        return " ".join(clean_text(item) for item in value if clean_text(item))
    return clean_text(value)


def greenhouse_date_posted(job):
    """Prefer first published date; fall back to updated_at."""
    for key in ("first_published", "updated_at", "created_at"):
        text = clean_text(job.get(key))
        if not text:
            continue
        if "T" in text:
            return text.split("T", 1)[0]
        return text
    return ""


def map_greenhouse_job(job, company_name, board_token):
    """Map one Greenhouse API job into the Operator's incoming-job shape."""
    if not isinstance(job, dict):
        return None
    location = greenhouse_location_text(job)
    salary_min, salary_max, salary_text = greenhouse_salary_fields(job)
    feed_company = clean_text(job.get("company_name") or job.get("company"))
    mapped = {
        "source": "greenhouse",
        "external_id": clean_text(job.get("id") or job.get("internal_job_id")),
        "company": feed_company or clean_text(company_name),
        "title": clean_text(job.get("title")),
        "location": location,
        "remote_type": infer_clear_remote_type(location, greenhouse_metadata_remote(job)),
        "employment_type": greenhouse_employment_type(job),
        "salary_min": salary_min,
        "salary_max": salary_max,
        "salary_text": salary_text,
        "url": clean_text(job.get("absolute_url") or job.get("url")),
        "description": html_to_text(job.get("content") or job.get("description")),
        "date_posted": greenhouse_date_posted(job),
        "raw_source_data": {
            "board_token": board_token,
            "configured_company": company_name,
            "job": job,
        },
    }
    if not mapped["title"] and not mapped["url"] and not mapped["external_id"]:
        return None
    return mapped


def _remember_greenhouse_health(token, job_count, error):
    """Update approved-source health after a Job Discovery fetch. Best-effort only."""
    try:
        from source_registry import record_source_health
    except Exception:
        return
    if error and ("was not found" in error or "is invalid" in error):
        status = "Invalid"
        fetched = False
    elif error:
        status = "Error"
        fetched = False
    elif job_count:
        status = "Active"
        fetched = True
    else:
        status = "Empty"
        fetched = True
    try:
        record_source_health("greenhouse", token, status, fetched=fetched)
    except OSError:
        return


def fetch_greenhouse_board(board_token, company_name, timeout=GREENHOUSE_TIMEOUT):
    """Fetch open jobs for one public board. Does not retry or call OpenAI."""
    token = normalize_board_token(board_token)
    display_name = clean_text(company_name) or token or "this company"
    if not token:
        error = f"The Greenhouse board identifier for {display_name} is invalid."
        _remember_greenhouse_health(token, 0, error)
        return [], error
    url = f"{GREENHOUSE_API_ROOT}/{token}/jobs"
    try:
        response = requests.get(
            url,
            params={"content": "true"},
            timeout=timeout,
            headers={
                "Accept": "application/json",
                "User-Agent": GREENHOUSE_USER_AGENT,
            },
        )
    except requests.Timeout:
        error = f"The Greenhouse request for {display_name} timed out."
        _remember_greenhouse_health(token, 0, error)
        return [], error
    except requests.RequestException as error:
        message = f"I could not reach Greenhouse for {display_name}: {error}"
        _remember_greenhouse_health(token, 0, message)
        return [], message

    if response.status_code == 404:
        error = (
            f"Greenhouse board '{token}' was not found for {display_name}. "
            "Check the board identifier."
        )
        _remember_greenhouse_health(token, 0, error)
        return [], error
    if response.status_code == 429:
        error = f"Greenhouse rate-limited the request for {display_name}. Try again later."
        _remember_greenhouse_health(token, 0, error)
        return [], error
    if not response.ok:
        error = f"Greenhouse returned HTTP {response.status_code} for {display_name}."
        _remember_greenhouse_health(token, 0, error)
        return [], error

    try:
        data = response.json()
    except ValueError:
        error = f"Greenhouse returned a response I could not parse for {display_name}."
        _remember_greenhouse_health(token, 0, error)
        return [], error

    if isinstance(data, dict):
        jobs = data.get("jobs")
        if jobs is None:
            jobs = data.get("items") or []
    elif isinstance(data, list):
        jobs = data
    else:
        error = f"Greenhouse returned an unexpected response for {display_name}."
        _remember_greenhouse_health(token, 0, error)
        return [], error

    if not isinstance(jobs, list):
        error = f"Greenhouse returned an unexpected job list for {display_name}."
        _remember_greenhouse_health(token, 0, error)
        return [], error

    mapped = []
    for job in jobs:
        incoming = map_greenhouse_job(job, display_name, token)
        if incoming is not None:
            mapped.append(incoming)
    _remember_greenhouse_health(token, len(mapped), None)
    return mapped, None


class GreenhouseJobSource(JobSource):
    """Fetch currently open jobs from one or more public Greenhouse boards."""

    name = "Greenhouse"
    source_id = "greenhouse"

    def __init__(self, boards=None):
        if boards is None:
            self.boards = configured_greenhouse_boards()
        else:
            self.boards = dict(boards)

    def fetch(self):
        """Return (incoming_jobs, error_message). error_message may be a warning."""
        boards = self.boards or configured_greenhouse_boards()
        if not boards:
            return [], (
                "No Greenhouse companies are configured. "
                "Add entries to GREENHOUSE_BOARDS in job_source_config.py "
                "or approve boards in Source Discovery."
            )
        all_jobs = []
        errors = []
        for company_name, board_token in boards.items():
            jobs, error = fetch_greenhouse_board(board_token, company_name)
            if error:
                errors.append(error)
            all_jobs.extend(jobs)
        if all_jobs:
            warning = " ".join(errors) if errors else None
            return all_jobs, warning
        if errors:
            return [], " ".join(errors)
        return [], None
