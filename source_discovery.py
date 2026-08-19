#!/usr/bin/env python3
"""Public ATS source discovery and validation.

Finds candidate Greenhouse boards and Lever sites from company names,
validates public endpoints, and estimates likely-relevant titles with
deterministic rules. This module never calls OpenAI and never scrapes
LinkedIn or arbitrary websites.
"""

import json
import os
import re
import uuid
from datetime import datetime, timezone, timedelta

import requests

from greenhouse_source import GREENHOUSE_API_ROOT, GREENHOUSE_USER_AGENT, normalize_board_token
from job_prefilter import functional_mismatch, seniority_alignment, title_relevance
from job_sources import clean_text
from lever_source import LEVER_API_EU, LEVER_API_US, LEVER_PAGE_SIZE, LEVER_USER_AGENT, parse_lever_site
from source_registry import (
    add_approved_source,
    data_dir,
    discovered_sources_path,
    identifier_is_configured,
    load_approved_maps,
    record_source_health,
    utc_now,
)


MAX_IDENTIFIERS_PER_COMPANY = 4
SOURCE_VALIDATION_CACHE_HOURS = 24
SOURCE_DISCOVERY_MAX_COMPANIES = 25
SOURCE_DISCOVERY_TIMEOUT = (5, 15)

STATUSES = (
    "Candidate",
    "Validated",
    "Invalid",
    "Approved",
    "Ignored",
    "Error",
)

LEGAL_SUFFIXES = {
    "inc",
    "llc",
    "ltd",
    "corp",
    "corporation",
    "company",
    "co",
    "plc",
    "limited",
    "holdings",
    "group",
    "therapeutics",
    "pharmaceuticals",
    "pharma",
    "laboratories",
    "labs",
    "technologies",
    "technology",
    "international",
    "incorporated",
    "the",
}


def empty_source_record():
    """Return a blank source-discovery record."""
    return {
        "id": "",
        "company": "",
        "ats_type": "",
        "identifier": "",
        "validation_status": "Candidate",
        "board_name": "",
        "open_job_count": 0,
        "likely_relevant_job_count": 0,
        "discovered_at": "",
        "validated_at": "",
        "approved": False,
        "notes": "",
        "already_configured": False,
        "error_message": "",
    }


def parse_company_names(text):
    """Split pasted company names, one per line, preserving first-seen order."""
    names = []
    for line in (text or "").splitlines():
        name = clean_text(line)
        if not name or name.startswith("#"):
            continue
        if name not in names:
            names.append(name)
    return names


def company_tokens(name):
    """Lowercase a company name into identifier tokens."""
    text = clean_text(name).lower()
    text = text.replace("&", " and ")
    text = re.sub(r"[^a-z0-9\s-]", " ", text)
    text = text.replace("-", " ")
    return [token for token in text.split() if token]


def identifiers_from_company(name, max_identifiers=MAX_IDENTIFIERS_PER_COMPANY):
    """Generate a short list of conservative public ATS identifiers."""
    max_identifiers = max(1, int(max_identifiers or MAX_IDENTIFIERS_PER_COMPANY))
    text = clean_text(name)
    if not text:
        return []

    lowered = text.lower()
    if "greenhouse.io" in lowered:
        token = normalize_board_token(text)
        return [token] if token else []
    if "lever.co" in lowered:
        slug, _region = parse_lever_site(text)
        return [slug] if slug else []

    if re.fullmatch(r"[A-Za-z0-9_-]+", text) and " " not in text:
        return [text.lower()][:max_identifiers]

    tokens = company_tokens(text)
    if not tokens:
        return []

    candidates = []

    def add(slug):
        slug = clean_text(slug).strip("-").lower()
        if not slug or slug in candidates:
            return
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", slug):
            return
        candidates.append(slug)

    add("-".join(tokens))
    add("".join(tokens))

    stripped = list(tokens)
    while len(stripped) > 1 and stripped[-1] in LEGAL_SUFFIXES:
        stripped.pop()
    while len(stripped) > 1 and stripped[0] in LEGAL_SUFFIXES:
        stripped.pop(0)
    if stripped != tokens:
        add("-".join(stripped))
        add("".join(stripped))

    if len(candidates) < max_identifiers and len(tokens) >= 3:
        initials = "".join(token[0] for token in tokens if token)
        if 2 <= len(initials) <= 6:
            add(initials)

    return candidates[:max_identifiers]


def title_looks_relevant(title):
    """Cheap title/function/seniority screen. Does not call OpenAI."""
    mismatch, _reason = functional_mismatch(title, "")
    if mismatch:
        return False
    _points, _reason, rejected, _reject = seniority_alignment(title)
    if rejected:
        return False
    title_points, _title_reason = title_relevance(title)
    return title_points >= 24


def count_relevant_titles(titles):
    """Return how many titles look like target senior technology roles."""
    count = 0
    for title in titles or []:
        if title_looks_relevant(title):
            count += 1
    return count


def load_discovered_sources():
    """Load source-discovery records. Missing or malformed files become []."""
    path = discovered_sources_path()
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as file:
            raw = file.read().strip()
        if raw == "":
            return []
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        return []
    if isinstance(data, dict):
        data = data.get("sources") or data.get("items") or []
    if not isinstance(data, list):
        return []
    records = []
    for item in data:
        record = normalize_source_record(item)
        if record is not None:
            records.append(record)
    return records


def save_discovered_sources(records):
    """Write source-discovery records, creating the data folder if needed."""
    os.makedirs(data_dir(), exist_ok=True)
    payload = []
    for item in records or []:
        record = normalize_source_record(item)
        if record is not None:
            payload.append(record)
    with open(discovered_sources_path(), "w", encoding="utf-8") as file:
        json.dump(payload, file, indent=2)


def normalize_source_record(raw):
    """Fill missing fields on a stored discovery record."""
    if not isinstance(raw, dict):
        return None
    record = empty_source_record()
    record["id"] = clean_text(raw.get("id")) or str(uuid.uuid4())
    record["company"] = clean_text(raw.get("company"))
    record["ats_type"] = clean_text(raw.get("ats_type")).lower()
    record["identifier"] = clean_text(raw.get("identifier"))
    status = clean_text(raw.get("validation_status")) or "Candidate"
    record["validation_status"] = status if status in STATUSES else "Candidate"
    record["board_name"] = clean_text(raw.get("board_name"))
    try:
        record["open_job_count"] = int(raw.get("open_job_count") or 0)
    except (TypeError, ValueError):
        record["open_job_count"] = 0
    try:
        record["likely_relevant_job_count"] = int(raw.get("likely_relevant_job_count") or 0)
    except (TypeError, ValueError):
        record["likely_relevant_job_count"] = 0
    record["discovered_at"] = clean_text(raw.get("discovered_at"))
    record["validated_at"] = clean_text(raw.get("validated_at"))
    record["approved"] = bool(raw.get("approved", False))
    record["notes"] = clean_text(raw.get("notes"))
    record["already_configured"] = bool(raw.get("already_configured", False))
    record["error_message"] = clean_text(raw.get("error_message"))
    if not record["company"] and not record["identifier"]:
        return None
    return record


def record_key(record):
    """Identity for duplicate company + ATS + identifier rows."""
    return "|".join(
        [
            clean_text(record.get("company")).lower(),
            clean_text(record.get("ats_type")).lower(),
            clean_text(record.get("identifier")).lower(),
        ]
    )


def upsert_source_record(records, incoming):
    """Insert or replace by company + ats_type + identifier. Preserve notes/approval."""
    incoming = normalize_source_record(incoming)
    if incoming is None:
        return records, None
    key = record_key(incoming)
    for index, existing in enumerate(records):
        if record_key(existing) != key:
            continue
        merged = dict(existing)
        for field in incoming:
            if field in ("id", "notes", "discovered_at"):
                continue
            merged[field] = incoming[field]
        if existing.get("notes") and not incoming.get("notes"):
            merged["notes"] = existing.get("notes")
        if existing.get("approved") and incoming.get("validation_status") != "Ignored":
            merged["approved"] = True
            if incoming.get("validation_status") == "Validated":
                merged["validation_status"] = "Approved"
        merged["id"] = existing.get("id") or incoming.get("id")
        merged["discovered_at"] = existing.get("discovered_at") or incoming.get("discovered_at")
        records[index] = merged
        return records, merged
    if not incoming.get("discovered_at"):
        incoming["discovered_at"] = utc_now()
    if not incoming.get("id"):
        incoming["id"] = str(uuid.uuid4())
    records.append(incoming)
    return records, incoming


def _parse_timestamp(value):
    text = clean_text(value)
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except ValueError:
        return None


def cached_record(records, ats_type, identifier, cache_hours=SOURCE_VALIDATION_CACHE_HOURS):
    """Return a recent Validated/Invalid record for this identifier, if any."""
    token = clean_text(identifier).lower()
    ats_type = clean_text(ats_type).lower()
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max(0, int(cache_hours)))
    newest = None
    for record in records:
        if clean_text(record.get("ats_type")).lower() != ats_type:
            continue
        if clean_text(record.get("identifier")).lower() != token:
            continue
        if record.get("validation_status") not in ("Validated", "Invalid", "Approved"):
            continue
        stamped = _parse_timestamp(record.get("validated_at"))
        if stamped is None or stamped < cutoff:
            continue
        if newest is None or (record.get("validated_at") or "") > (newest.get("validated_at") or ""):
            newest = record
    return newest


def _http_get(url, params=None, timeout=SOURCE_DISCOVERY_TIMEOUT, headers=None, http_get=None):
    getter = http_get or requests.get
    return getter(
        url,
        params=params or {},
        timeout=timeout,
        headers=headers or {},
    )


def _friendly_http_error(exc, label):
    text = str(exc)
    lowered = text.lower()
    if "timed out" in lowered or "timeout" in lowered:
        return f"The {label} request timed out."
    if "nameresolution" in lowered or "getaddrinfo" in lowered or "nodename" in lowered:
        return f"I could not resolve the {label} host."
    return f"I could not reach {label}: {exc}"


def extract_job_titles(jobs):
    titles = []
    for job in jobs or []:
        if not isinstance(job, dict):
            continue
        title = clean_text(job.get("title") or job.get("text"))
        if title:
            titles.append(title)
    return titles


def validate_greenhouse_identifier(identifier, company_name="", http_get=None):
    """Probe one public Greenhouse board. Never calls OpenAI."""
    token = normalize_board_token(identifier)
    display = clean_text(company_name) or token or "this company"
    result = {
        "ok": False,
        "status": "Invalid",
        "identifier": token,
        "board_name": "",
        "open_job_count": 0,
        "likely_relevant_job_count": 0,
        "error_message": "",
        "titles": [],
    }
    if not token:
        result["error_message"] = f"The Greenhouse identifier for {display} is invalid."
        return result

    url = f"{GREENHOUSE_API_ROOT}/{token}/jobs"
    headers = {"Accept": "application/json", "User-Agent": GREENHOUSE_USER_AGENT}
    try:
        response = _http_get(url, timeout=SOURCE_DISCOVERY_TIMEOUT, headers=headers, http_get=http_get)
    except requests.Timeout:
        result["status"] = "Error"
        result["error_message"] = f"The Greenhouse request for {display} timed out."
        return result
    except requests.RequestException as exc:
        result["status"] = "Error"
        result["error_message"] = _friendly_http_error(exc, f"Greenhouse for {display}")
        return result

    if response.status_code == 404:
        result["error_message"] = f"Greenhouse board '{token}' was not found."
        return result
    if response.status_code == 429:
        result["status"] = "Error"
        result["error_message"] = f"Greenhouse rate-limited the request for {display}."
        return result
    if not getattr(response, "ok", False):
        result["status"] = "Error"
        result["error_message"] = (
            f"Greenhouse returned HTTP {response.status_code} for {display}."
        )
        return result

    try:
        data = response.json()
    except (ValueError, TypeError, AttributeError):
        result["error_message"] = f"Greenhouse returned a response I could not parse for {display}."
        return result

    if isinstance(data, dict):
        if data.get("error") or data.get("errors"):
            result["error_message"] = f"Greenhouse did not return a valid board for {display}."
            return result
        jobs = data.get("jobs")
        if jobs is None:
            jobs = data.get("items")
    elif isinstance(data, list):
        jobs = data
    else:
        result["error_message"] = f"Greenhouse returned an unexpected response for {display}."
        return result
    if not isinstance(jobs, list):
        result["error_message"] = f"Greenhouse returned an unexpected job list for {display}."
        return result

    titles = extract_job_titles(jobs)
    board_name = ""
    meta_url = f"{GREENHOUSE_API_ROOT}/{token}"
    try:
        meta = _http_get(meta_url, timeout=SOURCE_DISCOVERY_TIMEOUT, headers=headers, http_get=http_get)
        if getattr(meta, "ok", False):
            meta_data = meta.json()
            if isinstance(meta_data, dict):
                board_name = clean_text(meta_data.get("name") or meta_data.get("company_name"))
    except (requests.RequestException, ValueError, TypeError, AttributeError):
        board_name = ""

    result["ok"] = True
    result["status"] = "Validated"
    result["board_name"] = board_name
    result["open_job_count"] = len(jobs)
    result["titles"] = titles
    result["likely_relevant_job_count"] = count_relevant_titles(titles)
    return result


def validate_lever_identifier(identifier, company_name="", http_get=None):
    """Probe one public Lever site. Never calls OpenAI."""
    site_slug, preferred_region = parse_lever_site(identifier)
    display = clean_text(company_name) or site_slug or "this company"
    result = {
        "ok": False,
        "status": "Invalid",
        "identifier": site_slug,
        "board_name": "",
        "open_job_count": 0,
        "likely_relevant_job_count": 0,
        "error_message": "",
        "titles": [],
    }
    if not site_slug:
        result["error_message"] = f"The Lever identifier for {display} is invalid."
        return result

    roots = [LEVER_API_US, LEVER_API_EU]
    if preferred_region == "eu":
        roots = [LEVER_API_EU, LEVER_API_US]
    headers = {"Accept": "application/json", "User-Agent": LEVER_USER_AGENT}
    last_error = ""
    found = False
    titles = []
    total = 0

    for api_root in roots:
        skip = 0
        site_found = False
        while skip <= 3000:
            url = f"{api_root}/{site_slug}"
            try:
                response = _http_get(
                    url,
                    params={"mode": "json", "skip": skip, "limit": LEVER_PAGE_SIZE},
                    timeout=SOURCE_DISCOVERY_TIMEOUT,
                    headers=headers,
                    http_get=http_get,
                )
            except requests.Timeout:
                result["status"] = "Error"
                result["error_message"] = f"The Lever request for {display} timed out."
                return result
            except requests.RequestException as exc:
                result["status"] = "Error"
                result["error_message"] = _friendly_http_error(exc, f"Lever for {display}")
                return result

            if response.status_code == 404:
                last_error = f"Lever site '{site_slug}' was not found."
                site_found = False
                break
            if response.status_code == 429:
                result["status"] = "Error"
                result["error_message"] = f"Lever rate-limited the request for {display}."
                return result
            if not getattr(response, "ok", False):
                result["status"] = "Error"
                result["error_message"] = (
                    f"Lever returned HTTP {response.status_code} for {display}."
                )
                return result
            try:
                data = response.json()
            except (ValueError, TypeError, AttributeError):
                result["status"] = "Error"
                result["error_message"] = f"Lever returned a response I could not parse for {display}."
                return result
            if isinstance(data, dict):
                jobs = data.get("data") or data.get("postings") or data.get("jobs") or []
            elif isinstance(data, list):
                jobs = data
            else:
                result["error_message"] = f"Lever returned an unexpected response for {display}."
                return result
            if not isinstance(jobs, list):
                result["error_message"] = f"Lever returned an unexpected job list for {display}."
                return result

            site_found = True
            found = True
            page_titles = extract_job_titles(jobs)
            titles.extend(page_titles)
            total += len(jobs)
            if len(jobs) < LEVER_PAGE_SIZE:
                break
            skip += LEVER_PAGE_SIZE
        if site_found:
            break

    if not found:
        result["error_message"] = last_error or f"Lever site '{site_slug}' was not found."
        return result

    result["ok"] = True
    result["status"] = "Validated"
    result["open_job_count"] = total
    result["titles"] = titles
    result["likely_relevant_job_count"] = count_relevant_titles(titles)
    return result


def _hardcoded_maps():
    from job_source_config import GREENHOUSE_BOARDS, LEVER_SITES

    return {
        "greenhouse": dict(GREENHOUSE_BOARDS or {}),
        "lever": dict(LEVER_SITES or {}),
    }


def already_configured_flag(ats_type, identifier):
    hardcoded = _hardcoded_maps()
    approved = load_approved_maps()
    return identifier_is_configured(
        ats_type,
        identifier,
        hardcoded.get(ats_type, {}),
        approved.get(ats_type, {}),
    )


def _result_to_record(company, ats_type, identifier, result, configured):
    record = empty_source_record()
    record["company"] = company
    record["ats_type"] = ats_type
    record["identifier"] = result.get("identifier") or identifier
    record["validation_status"] = result.get("status") or "Error"
    record["board_name"] = result.get("board_name") or ""
    record["open_job_count"] = int(result.get("open_job_count") or 0)
    record["likely_relevant_job_count"] = int(result.get("likely_relevant_job_count") or 0)
    record["validated_at"] = utc_now()
    record["discovered_at"] = utc_now()
    record["already_configured"] = configured
    record["error_message"] = result.get("error_message") or ""
    record["approved"] = False
    return record


def discover_and_validate_sources(
    company_names,
    refresh=False,
    max_identifiers=MAX_IDENTIFIERS_PER_COMPANY,
    cache_hours=SOURCE_VALIDATION_CACHE_HOURS,
    http_get=None,
):
    """Validate candidate companies against public Greenhouse and Lever endpoints.

    Returns (records_for_this_run, all_saved_records, messages).
    """
    names = []
    for name in company_names or []:
        text = clean_text(name)
        if text and text not in names:
            names.append(text)
    truncated = False
    if len(names) > SOURCE_DISCOVERY_MAX_COMPANIES:
        names = names[:SOURCE_DISCOVERY_MAX_COMPANIES]
        truncated = True

    saved = load_discovered_sources()
    run_records = []
    messages = []
    if truncated:
        messages.append(
            f"Only the first {SOURCE_DISCOVERY_MAX_COMPANIES} companies were validated in this run."
        )

    for company in names:
        identifiers = identifiers_from_company(company, max_identifiers=max_identifiers)
        if not identifiers:
            record = empty_source_record()
            record["company"] = company
            record["ats_type"] = "unknown"
            record["validation_status"] = "Invalid"
            record["error_message"] = "I could not build a public ATS identifier from that name."
            record["validated_at"] = utc_now()
            saved, stored = upsert_source_record(saved, record)
            run_records.append(stored)
            messages.append(f"{company}: no supported ATS identifier.")
            continue

        found = {"greenhouse": None, "lever": None}
        errors = []
        for identifier in identifiers:
            for ats_type, validator in (
                ("greenhouse", validate_greenhouse_identifier),
                ("lever", validate_lever_identifier),
            ):
                if found[ats_type] is not None:
                    continue
                if not refresh:
                    cached = cached_record(saved, ats_type, identifier, cache_hours=cache_hours)
                    if cached is not None:
                        reused = dict(cached)
                        reused["company"] = company
                        reused["already_configured"] = already_configured_flag(
                            ats_type, reused.get("identifier")
                        )
                        if reused.get("validation_status") in ("Validated", "Approved"):
                            found[ats_type] = reused
                        continue
                result = validator(identifier, company_name=company, http_get=http_get)
                if result.get("ok"):
                    configured = already_configured_flag(
                        ats_type, result.get("identifier") or identifier
                    )
                    found[ats_type] = _result_to_record(
                        company, ats_type, identifier, result, configured
                    )
                    try:
                        record_source_health(
                            ats_type,
                            found[ats_type]["identifier"],
                            "Empty" if found[ats_type]["open_job_count"] == 0 else "Active",
                            validated=True,
                        )
                    except OSError:
                        pass
                elif result.get("status") == "Error":
                    errors.append(f"{ats_type} {identifier}: {result.get('error_message')}")
            if found["greenhouse"] is not None and found["lever"] is not None:
                break

        matched = False
        for ats_type in ("greenhouse", "lever"):
            record = found[ats_type]
            if record is None:
                continue
            if record.get("validation_status") not in ("Validated", "Approved"):
                continue
            saved, stored = upsert_source_record(saved, record)
            run_records.append(stored)
            matched = True
            ats_label = "Greenhouse" if ats_type == "greenhouse" else "Lever"
            configured_label = " (already configured)" if stored.get("already_configured") else ""
            messages.append(
                f"{company}: validated {ats_label} '{stored.get('identifier')}' "
                f"with {stored.get('open_job_count', 0)} open jobs, "
                f"{stored.get('likely_relevant_job_count', 0)} likely relevant"
                f"{configured_label}."
            )

        if not matched:
            record = empty_source_record()
            record["company"] = company
            record["ats_type"] = "unknown"
            record["validation_status"] = "Error" if errors else "Invalid"
            record["error_message"] = (
                "; ".join(errors) if errors else "No supported Greenhouse or Lever source was found."
            )
            record["validated_at"] = utc_now()
            saved, stored = upsert_source_record(saved, record)
            run_records.append(stored)
            messages.append(f"{company}: no supported ATS source found.")

    save_discovered_sources(saved)
    return run_records, saved, messages


def companies_from_jobs(*job_groups):
    """Collect unique company names from pipeline or discovery job dicts."""
    names = []
    for group in job_groups:
        for job in group or []:
            if not isinstance(job, dict):
                continue
            name = clean_text(job.get("company"))
            if name and name not in names:
                names.append(name)
    return names


def set_record_notes(record_id, notes):
    records = load_discovered_sources()
    updated = None
    for index, record in enumerate(records):
        if record.get("id") != record_id:
            continue
        record["notes"] = clean_text(notes)
        records[index] = record
        updated = record
        break
    if updated is None:
        return None, "I could not find that source record."
    save_discovered_sources(records)
    return updated, None


def ignore_source(record_id):
    records = load_discovered_sources()
    updated = None
    for index, record in enumerate(records):
        if record.get("id") != record_id:
            continue
        record["approved"] = False
        record["validation_status"] = "Ignored"
        records[index] = record
        updated = record
        break
    if updated is None:
        return None, "I could not find that source record."
    save_discovered_sources(records)
    return updated, None


def approve_discovered_source(record_id):
    """Mark a validated source approved and add it to the live registry."""
    records = load_discovered_sources()
    target = None
    index = None
    for i, record in enumerate(records):
        if record.get("id") == record_id:
            target = record
            index = i
            break
    if target is None:
        return None, "I could not find that source record."
    if target.get("validation_status") == "Ignored":
        return target, "Ignored sources cannot be approved until they are validated again."
    if target.get("ats_type") not in ("greenhouse", "lever"):
        return target, "Only validated Greenhouse or Lever sources can be approved."
    if target.get("validation_status") not in ("Validated", "Approved"):
        return target, "Approve only after the public ATS endpoint has been validated."

    hardcoded = _hardcoded_maps().get(target["ats_type"], {})
    if identifier_is_configured(target["ats_type"], target["identifier"], hardcoded, {}):
        target["approved"] = True
        target["validation_status"] = "Approved"
        target["already_configured"] = True
        records[index] = target
        save_discovered_sources(records)
        return target, "This source is already in the hardcoded live configuration."

    added, message = add_approved_source(
        target["ats_type"],
        target["company"],
        target["identifier"],
    )
    target["approved"] = True
    target["validation_status"] = "Approved"
    target["already_configured"] = True
    records[index] = target
    save_discovered_sources(records)
    if added:
        return target, message
    return target, message
