#!/usr/bin/env python3
"""Persistent employer universe for Source Discovery.

Collects potential employers, deduplicates them conservatively, and sends
selected names to existing Source Discovery validation. This module never
calls OpenAI, never scrapes LinkedIn, and never auto-approves ATS sources.
"""

import csv
import io
import json
import os
import re
import uuid

from company_universe_config import (
    PREFERRED_COMPANY_STAGES,
    PREFERRED_INDUSTRIES,
    PREFERRED_LOCATIONS,
)
from job_sources import clean_text, unique_sources
from source_registry import company_universe_path, data_dir, utc_now


PRIORITIES = ("High", "Medium", "Low", "Watch", "Ignore")
YES_NO_UNKNOWN = ("", "Yes", "No", "Unknown")
SOURCE_ORIGINS = (
    "Manual",
    "Job Pipeline",
    "Job Discovery",
    "Source Discovery",
    "Imported List",
    "Existing Config",
    "Approved Source",
    "Public Company Dataset",
    "Industry Directory",
    "Funding Dataset",
    "Search Provider",
    "Conference List",
    "Portfolio List",
)
ATS_STATUSES = (
    "Unknown",
    "Candidate",
    "Validated",
    "Invalid",
    "Error",
    "Already Configured",
    "Approved",
)
LEGAL_ENTITY_SUFFIXES = {
    "inc",
    "incorporated",
    "corp",
    "corporation",
    "llc",
    "ltd",
    "limited",
    "plc",
}
USER_EDITABLE_FIELDS = (
    "website",
    "industry",
    "sub_industry",
    "headquarters",
    "bay_area_presence",
    "remote_friendly",
    "company_stage",
    "public_private",
    "employee_size",
    "priority",
    "priority_reason",
    "notes",
    "active",
    "ignored",
)
LARGE_BATCH_THRESHOLD = 25


def _csv_value(row, field_map, key):
    column = field_map.get(key)
    if not column:
        return ""
    return clean_text(row.get(column))


def empty_company_record():
    """Return a blank company-universe record with safe defaults."""
    return {
        "id": "",
        "company_name": "",
        "normalized_name": "",
        "website": "",
        "industry": "",
        "sub_industry": "",
        "headquarters": "",
        "bay_area_presence": "",
        "remote_friendly": "",
        "company_stage": "",
        "public_private": "",
        "employee_size": "",
        "priority": "Medium",
        "priority_reason": "",
        "suggested_priority": "Medium",
        "suggested_priority_reasons": [],
        "source_origin": [],
        "ats_status": "Unknown",
        "ats_type": "",
        "ats_identifier": "",
        "ats_sources": [],
        "source_validation_status": "",
        "open_job_count": 0,
        "likely_relevant_job_count": 0,
        "last_source_validation": "",
        "last_job_fetch": "",
        "active": True,
        "ignored": False,
        "active_source": False,
        "notes": "",
        "date_added": "",
        "date_updated": "",
    }


def normalize_company_name(name):
    """Conservative identity key: lowercase, strip legal-entity suffixes only."""
    text = clean_text(name).lower()
    text = text.replace("&", " and ")
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    tokens = [token for token in text.split() if token]
    while tokens and tokens[-1] in LEGAL_ENTITY_SUFFIXES:
        tokens.pop()
    if tokens and tokens[0] == "the" and len(tokens) > 1:
        tokens = tokens[1:]
    return " ".join(tokens)


def normalize_domain(value):
    """Lowercase a website/domain and drop scheme, www, and trailing slash."""
    text = clean_text(value).lower()
    if not text:
        return ""
    for prefix in ("https://", "http://"):
        if text.startswith(prefix):
            text = text[len(prefix):]
            break
    if text.startswith("www."):
        text = text[4:]
    text = text.split("/", 1)[0]
    text = text.split("?", 1)[0]
    return text.strip(".")


def _int_value(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def normalize_company_record(raw):
    """Fill missing fields on a stored company. Unknown values stay blank."""
    if not isinstance(raw, dict):
        return None
    record = empty_company_record()
    record["id"] = clean_text(raw.get("id")) or str(uuid.uuid4())
    record["company_name"] = clean_text(raw.get("company_name") or raw.get("company") or raw.get("name"))
    record["normalized_name"] = clean_text(raw.get("normalized_name")) or normalize_company_name(
        record["company_name"]
    )
    if not record["company_name"] and not record["normalized_name"]:
        return None
    for key in (
        "website",
        "industry",
        "sub_industry",
        "headquarters",
        "company_stage",
        "public_private",
        "employee_size",
        "priority_reason",
        "ats_type",
        "ats_identifier",
        "source_validation_status",
        "last_source_validation",
        "last_job_fetch",
        "notes",
        "date_added",
        "date_updated",
        "suggested_priority",
    ):
        record[key] = clean_text(raw.get(key))
    presence = clean_text(raw.get("bay_area_presence"))
    record["bay_area_presence"] = presence if presence in ("Yes", "No", "Unknown") else presence
    remote = clean_text(raw.get("remote_friendly"))
    record["remote_friendly"] = remote if remote in ("Yes", "No", "Unknown") else remote
    priority = clean_text(raw.get("priority")) or "Medium"
    record["priority"] = priority if priority in PRIORITIES else "Medium"
    suggested = clean_text(raw.get("suggested_priority")) or "Medium"
    record["suggested_priority"] = suggested if suggested in PRIORITIES else "Medium"
    ats_status = clean_text(raw.get("ats_status")) or "Unknown"
    record["ats_status"] = ats_status if ats_status in ATS_STATUSES else "Unknown"
    origins = raw.get("source_origin")
    record["source_origin"] = unique_sources(origins)
    reasons = raw.get("suggested_priority_reasons")
    if isinstance(reasons, list):
        record["suggested_priority_reasons"] = [clean_text(item) for item in reasons if clean_text(item)]
    elif reasons:
        record["suggested_priority_reasons"] = [clean_text(reasons)]
    ats_sources = raw.get("ats_sources")
    record["ats_sources"] = []
    if isinstance(ats_sources, list):
        for item in ats_sources:
            if not isinstance(item, dict):
                continue
            ats_type = clean_text(item.get("ats_type")).lower()
            identifier = clean_text(item.get("identifier") or item.get("ats_identifier"))
            if not ats_type and not identifier:
                continue
            record["ats_sources"].append(
                {
                    "ats_type": ats_type,
                    "identifier": identifier,
                    "validation_status": clean_text(item.get("validation_status")),
                    "open_job_count": _int_value(item.get("open_job_count")),
                    "likely_relevant_job_count": _int_value(item.get("likely_relevant_job_count")),
                    "validated_at": clean_text(item.get("validated_at")),
                }
            )
    record["open_job_count"] = _int_value(raw.get("open_job_count"))
    record["likely_relevant_job_count"] = _int_value(raw.get("likely_relevant_job_count"))
    record["active"] = bool(raw.get("active", True))
    record["ignored"] = bool(raw.get("ignored", False))
    if record["ignored"]:
        record["active"] = False
        record["priority"] = "Ignore"
    record["active_source"] = bool(raw.get("active_source", False))
    if not record["date_added"]:
        record["date_added"] = utc_now()
    if not record["date_updated"]:
        record["date_updated"] = record["date_added"]
    return record


def load_company_universe():
    """Load company records. Missing or malformed files become []."""
    path = company_universe_path()
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
        data = data.get("companies") or data.get("items") or []
    if not isinstance(data, list):
        return []
    records = []
    for item in data:
        record = normalize_company_record(item)
        if record is not None:
            records.append(record)
    return refresh_active_source_flags(records, save=False)


def save_company_universe(records):
    """Write company records, creating the data folder if needed."""
    os.makedirs(data_dir(), exist_ok=True)
    payload = []
    for item in records or []:
        record = normalize_company_record(item)
        if record is not None:
            payload.append(record)
    with open(company_universe_path(), "w", encoding="utf-8") as file:
        json.dump(payload, file, indent=2)


def _live_source_maps():
    from greenhouse_source import configured_greenhouse_boards
    from lever_source import configured_lever_sites

    return {
        "greenhouse": configured_greenhouse_boards() or {},
        "lever": configured_lever_sites() or {},
    }


def _hardcoded_source_maps():
    from job_source_config import GREENHOUSE_BOARDS, LEVER_SITES

    return {
        "greenhouse": dict(GREENHOUSE_BOARDS or {}),
        "lever": dict(LEVER_SITES or {}),
    }


def _identifier_is_live(ats_type, identifier, live_maps=None):
    token = clean_text(identifier).lower()
    ats_type = clean_text(ats_type).lower()
    if not token:
        return False
    live_maps = live_maps or _live_source_maps()
    for value in (live_maps.get(ats_type) or {}).values():
        if clean_text(value).lower() == token:
            return True
    return False


def company_is_active_source(record, live_maps=None):
    """True when this company's ATS board/site is in Job Discovery."""
    live_maps = live_maps or _live_source_maps()
    if _identifier_is_live(record.get("ats_type"), record.get("ats_identifier"), live_maps):
        return True
    for item in record.get("ats_sources") or []:
        if _identifier_is_live(item.get("ats_type"), item.get("identifier"), live_maps):
            return True
    name = clean_text(record.get("company_name"))
    normalized = record.get("normalized_name") or normalize_company_name(name)
    for mapping in live_maps.values():
        for company_name in mapping:
            if normalize_company_name(company_name) == normalized:
                return True
    return False


def refresh_active_source_flags(records=None, save=True):
    """Recompute active_source from current live Greenhouse/Lever maps."""
    records = list(records if records is not None else load_company_universe())
    live_maps = _live_source_maps()
    changed = False
    for index, record in enumerate(records):
        active = company_is_active_source(record, live_maps=live_maps)
        if record.get("active_source") != active:
            record["active_source"] = active
            record["date_updated"] = utc_now()
            changed = True
        if active and record.get("ats_status") in ("Unknown", "Candidate", "Validated"):
            if record.get("source_validation_status") != "Approved":
                record["ats_status"] = "Already Configured"
        records[index] = suggest_company_priority(record)
    if save and changed:
        save_company_universe(records)
    return records


def find_company(records, incoming):
    """Find a conservative duplicate by name, domain, or ATS identifier."""
    incoming_id = clean_text(incoming.get("id"))
    incoming_name = incoming.get("normalized_name") or normalize_company_name(
        incoming.get("company_name")
    )
    incoming_domain = normalize_domain(incoming.get("website"))
    incoming_ats = clean_text(incoming.get("ats_type")).lower()
    incoming_identifier = clean_text(incoming.get("ats_identifier")).lower()
    if incoming_id:
        for record in records:
            if record.get("id") == incoming_id:
                return record
    if incoming_name:
        for record in records:
            if record.get("normalized_name") == incoming_name:
                return record
    if incoming_domain:
        for record in records:
            if normalize_domain(record.get("website")) == incoming_domain:
                return record
    if incoming_ats and incoming_identifier:
        for record in records:
            if (
                clean_text(record.get("ats_type")).lower() == incoming_ats
                and clean_text(record.get("ats_identifier")).lower() == incoming_identifier
            ):
                return record
            for item in record.get("ats_sources") or []:
                if (
                    clean_text(item.get("ats_type")).lower() == incoming_ats
                    and clean_text(item.get("identifier")).lower() == incoming_identifier
                ):
                    return record
    return None


def _merge_ats_sources(existing, incoming_item):
    sources = list(existing.get("ats_sources") or [])
    ats_type = clean_text(incoming_item.get("ats_type")).lower()
    identifier = clean_text(incoming_item.get("identifier") or incoming_item.get("ats_identifier"))
    if not ats_type and not identifier:
        return sources
    for index, item in enumerate(sources):
        same_type = clean_text(item.get("ats_type")).lower() == ats_type
        same_id = clean_text(item.get("identifier")).lower() == identifier.lower()
        if same_type and same_id:
            merged = dict(item)
            for key, value in incoming_item.items():
                if value not in ("", None, []):
                    merged[key] = value
            sources[index] = merged
            return sources
    sources.append(
        {
            "ats_type": ats_type,
            "identifier": identifier,
            "validation_status": clean_text(incoming_item.get("validation_status")),
            "open_job_count": _int_value(incoming_item.get("open_job_count")),
            "likely_relevant_job_count": _int_value(incoming_item.get("likely_relevant_job_count")),
            "validated_at": clean_text(incoming_item.get("validated_at")),
        }
    )
    return sources


def merge_company_records(existing, incoming):
    """Fill gaps from incoming. Do not invent facts or wipe user-entered metadata."""
    merged = dict(existing)
    incoming = normalize_company_record(incoming) or incoming
    if incoming.get("company_name") and (
        not merged.get("company_name")
        or len(incoming["company_name"]) < len(merged.get("company_name") or "")
    ):
        # Prefer the shorter display name when merging "Nurix Therapeutics, Inc."
        if normalize_company_name(incoming["company_name"]) == merged.get("normalized_name"):
            merged["company_name"] = incoming["company_name"]
            merged["normalized_name"] = incoming.get("normalized_name") or merged.get("normalized_name")
    for key in USER_EDITABLE_FIELDS:
        if key in ("priority", "active", "ignored"):
            continue
        if not merged.get(key) and incoming.get(key):
            merged[key] = incoming[key]
    if incoming.get("ignored"):
        merged["ignored"] = True
        merged["active"] = False
        merged["priority"] = "Ignore"
    merged["source_origin"] = unique_sources(merged.get("source_origin"), incoming.get("source_origin"))
    incoming_ats_items = list(incoming.get("ats_sources") or [])
    if not incoming_ats_items and (incoming.get("ats_type") or incoming.get("ats_identifier")):
        incoming_ats_items.append(
            {
                "ats_type": incoming.get("ats_type"),
                "identifier": incoming.get("ats_identifier"),
                "validation_status": incoming.get("source_validation_status")
                or incoming.get("validation_status")
                or incoming.get("ats_status"),
                "open_job_count": incoming.get("open_job_count"),
                "likely_relevant_job_count": incoming.get("likely_relevant_job_count"),
                "validated_at": incoming.get("last_source_validation") or incoming.get("validated_at"),
            }
        )
    for item in incoming_ats_items:
        merged["ats_sources"] = _merge_ats_sources(merged, item)
    if incoming.get("ats_type") or incoming.get("ats_identifier") or incoming_ats_items:
        incoming_relevant = _int_value(incoming.get("likely_relevant_job_count"))
        existing_relevant = _int_value(merged.get("likely_relevant_job_count"))
        incoming_status = clean_text(incoming.get("source_validation_status") or incoming.get("ats_status"))
        if incoming_status in ("Validated", "Approved", "Already Configured") and (
            not merged.get("ats_identifier")
            or incoming_relevant > existing_relevant
            or incoming_status == "Approved"
        ):
            if incoming.get("ats_type"):
                merged["ats_type"] = incoming.get("ats_type")
            if incoming.get("ats_identifier"):
                merged["ats_identifier"] = incoming.get("ats_identifier")
        for key in (
            "ats_status",
            "source_validation_status",
            "open_job_count",
            "likely_relevant_job_count",
            "last_source_validation",
            "last_job_fetch",
        ):
            if incoming.get(key) not in ("", None):
                merged[key] = incoming.get(key)
    if incoming.get("active_source"):
        merged["active_source"] = True
    merged["date_updated"] = utc_now()
    return suggest_company_priority(merged)


def suggest_company_priority(record):
    """Deterministic suggested priority. Does not overwrite user priority."""
    reasons = []
    score = 40
    industry = clean_text(record.get("industry")).lower()
    if industry:
        for preferred in PREFERRED_INDUSTRIES:
            if preferred.lower() in industry or industry in preferred.lower():
                score += 20
                reasons.append(f"Preferred industry ({preferred})")
                break
    headquarters = " ".join(
        [
            clean_text(record.get("headquarters")),
            clean_text(record.get("bay_area_presence")),
        ]
    ).lower()
    if record.get("bay_area_presence") == "Yes" or any(
        location.lower() in headquarters for location in PREFERRED_LOCATIONS
    ):
        score += 15
        reasons.append("Bay Area / preferred location signal")
    if record.get("remote_friendly") == "Yes":
        score += 10
        reasons.append("Remote-friendly")
    stage = clean_text(record.get("company_stage")).lower()
    if stage and any(preferred.lower() in stage for preferred in PREFERRED_COMPANY_STAGES):
        score += 8
        reasons.append("Preferred company stage")
    if record.get("active_source"):
        score += 15
        reasons.append("Already an active Job Discovery source")
    relevant = _int_value(record.get("likely_relevant_job_count"))
    if relevant >= 3:
        score += 20
        reasons.append(f"{relevant} likely relevant open jobs")
    elif relevant >= 1:
        score += 12
        reasons.append(f"{relevant} likely relevant open job(s)")
    if score >= 80:
        suggested = "High"
    elif score >= 50:
        suggested = "Medium"
    elif score >= 30:
        suggested = "Low"
    else:
        suggested = "Watch"
    record["suggested_priority"] = suggested
    record["suggested_priority_reasons"] = reasons
    return record


def add_or_merge_company(incoming, origin="", records=None, save=True):
    """Insert or merge one company. Returns (record, action)."""
    if records is None:
        records = load_company_universe()
    payload = normalize_company_record(incoming)
    if payload is None:
        return None, "invalid"
    if origin:
        payload["source_origin"] = unique_sources(payload.get("source_origin"), origin)
    existing = find_company(records, payload)
    if existing is None:
        if not payload.get("id"):
            payload["id"] = str(uuid.uuid4())
        now = utc_now()
        payload["date_added"] = payload.get("date_added") or now
        payload["date_updated"] = now
        payload = suggest_company_priority(payload)
        records.append(payload)
        if save:
            save_company_universe(records)
        return payload, "added"
    merged = merge_company_records(existing, payload)
    for index, current in enumerate(records):
        if current.get("id") == existing.get("id"):
            records[index] = merged
            break
    if save:
        save_company_universe(records)
    return merged, "merged"


def add_companies_from_names(names, origin="Manual", industry="", website="", priority="", notes=""):
    """Add pasted company names without calling external services."""
    stats = {"added": 0, "merged": 0, "invalid": 0}
    records = load_company_universe()
    added = []
    for name in names or []:
        payload = empty_company_record()
        payload["company_name"] = clean_text(name)
        payload["industry"] = clean_text(industry)
        payload["website"] = clean_text(website)
        if clean_text(priority) in PRIORITIES:
            payload["priority"] = clean_text(priority)
        payload["notes"] = clean_text(notes)
        company, action = add_or_merge_company(payload, origin=origin, records=records, save=False)
        if action == "invalid":
            stats["invalid"] += 1
            continue
        stats[action] += 1
        added.append(company)
    save_company_universe(refresh_active_source_flags(records, save=False))
    return added, stats


def import_companies_from_text(text, filename=""):
    """Import companies from CSV or TXT. Returns (records, stats)."""
    raw = text or ""
    stats = {"rows_read": 0, "added": 0, "merged": 0, "invalid": 0}
    lowered = (filename or "").lower()
    looks_csv = lowered.endswith(".csv") or ("," in raw.splitlines()[0] if raw.strip() else False)
    records = load_company_universe()
    imported = []
    if looks_csv:
        reader = csv.DictReader(io.StringIO(raw))
        if not reader.fieldnames:
            return [], stats
        field_map = {clean_text(name).lower(): name for name in reader.fieldnames}
        company_key = (
            field_map.get("company")
            or field_map.get("company_name")
            or field_map.get("name")
        )
        for row in reader:
            stats["rows_read"] += 1
            if not isinstance(row, dict):
                stats["invalid"] += 1
                continue
            name = clean_text(row.get(company_key) if company_key else "")
            if not name:
                stats["invalid"] += 1
                continue
            payload = empty_company_record()
            payload["company_name"] = name
            payload["website"] = _csv_value(row, field_map, "website")
            payload["industry"] = _csv_value(row, field_map, "industry")
            payload["headquarters"] = _csv_value(row, field_map, "headquarters")
            payload["notes"] = _csv_value(row, field_map, "notes")
            priority = _csv_value(row, field_map, "priority")
            if priority in PRIORITIES:
                payload["priority"] = priority
            company, action = add_or_merge_company(
                payload, origin="Imported List", records=records, save=False
            )
            if action == "invalid":
                stats["invalid"] += 1
                continue
            stats[action] += 1
            imported.append(company)
    else:
        from source_discovery import parse_company_names

        names = parse_company_names(raw)
        stats["rows_read"] = len(names)
        added, name_stats = add_companies_from_names(names, origin="Imported List")
        stats["added"] = name_stats["added"]
        stats["merged"] = name_stats["merged"]
        stats["invalid"] = name_stats["invalid"]
        return added, stats
    save_company_universe(refresh_active_source_flags(records, save=False))
    return imported, stats


def add_companies_from_jobs(jobs, origin="Job Pipeline"):
    """Add unique company names from job dicts."""
    names = []
    for job in jobs or []:
        if not isinstance(job, dict):
            continue
        name = clean_text(job.get("company"))
        if name and name not in names:
            names.append(name)
    return add_companies_from_names(names, origin=origin)


def add_companies_from_source_map(mapping, ats_type, origin):
    """Add companies from a {name: identifier} map."""
    stats = {"added": 0, "merged": 0, "invalid": 0}
    records = load_company_universe()
    added = []
    for company_name, identifier in (mapping or {}).items():
        payload = empty_company_record()
        payload["company_name"] = clean_text(company_name)
        payload["ats_type"] = clean_text(ats_type).lower()
        payload["ats_identifier"] = clean_text(identifier)
        payload["ats_status"] = "Already Configured" if origin in ("Existing Config", "Approved Source") else "Unknown"
        payload["source_validation_status"] = "Approved" if origin == "Approved Source" else ""
        payload["active_source"] = origin in ("Existing Config", "Approved Source")
        payload["ats_sources"] = [
            {
                "ats_type": payload["ats_type"],
                "identifier": payload["ats_identifier"],
                "validation_status": payload["source_validation_status"] or "Already Configured",
                "open_job_count": 0,
                "likely_relevant_job_count": 0,
                "validated_at": "",
            }
        ]
        company, action = add_or_merge_company(payload, origin=origin, records=records, save=False)
        if action == "invalid":
            stats["invalid"] += 1
            continue
        stats[action] += 1
        added.append(company)
    save_company_universe(refresh_active_source_flags(records, save=False))
    return added, stats


def initialize_from_operator_data(pipeline_jobs=None, discovery_jobs=None):
    """Seed or update the universe from data the Operator already knows.

    Existing company_universe.json records are merged, not replaced.
    """
    from source_registry import load_approved_maps

    totals = {"added": 0, "merged": 0, "invalid": 0}
    hardcoded = _hardcoded_source_maps()
    for ats_type, mapping in hardcoded.items():
        _added, stats = add_companies_from_source_map(mapping, ats_type, "Existing Config")
        for key in totals:
            totals[key] += stats[key]
    approved = load_approved_maps()
    for ats_type, mapping in approved.items():
        _added, stats = add_companies_from_source_map(mapping, ats_type, "Approved Source")
        for key in totals:
            totals[key] += stats[key]
    if pipeline_jobs:
        _added, stats = add_companies_from_jobs(pipeline_jobs, origin="Job Pipeline")
        for key in totals:
            totals[key] += stats[key]
    if discovery_jobs:
        _added, stats = add_companies_from_jobs(discovery_jobs, origin="Job Discovery")
        for key in totals:
            totals[key] += stats[key]
    return load_company_universe(), totals


def apply_source_discovery_results(run_records):
    """Copy Source Discovery validation results onto matching companies."""
    records = load_company_universe()
    live_maps = _live_source_maps()
    updated = []
    for source in run_records or []:
        if not isinstance(source, dict):
            continue
        payload = empty_company_record()
        payload["company_name"] = clean_text(source.get("company") or source.get("board_name"))
        payload["ats_type"] = clean_text(source.get("ats_type")).lower()
        payload["ats_identifier"] = clean_text(source.get("identifier"))
        status = clean_text(source.get("validation_status"))
        payload["source_validation_status"] = status
        if status == "Validated":
            payload["ats_status"] = "Validated"
        elif status == "Approved":
            payload["ats_status"] = "Approved"
        elif status == "Invalid":
            payload["ats_status"] = "Invalid"
        elif status == "Error":
            payload["ats_status"] = "Error"
        elif source.get("already_configured"):
            payload["ats_status"] = "Already Configured"
        payload["open_job_count"] = _int_value(source.get("open_job_count"))
        payload["likely_relevant_job_count"] = _int_value(source.get("likely_relevant_job_count"))
        payload["last_source_validation"] = clean_text(source.get("validated_at")) or utc_now()
        payload["active_source"] = bool(source.get("already_configured")) or _identifier_is_live(
            payload["ats_type"], payload["ats_identifier"], live_maps
        )
        if status == "Approved":
            payload["active"] = True
            payload["active_source"] = True
        payload["ats_sources"] = [
            {
                "ats_type": payload["ats_type"],
                "identifier": payload["ats_identifier"],
                "validation_status": status,
                "open_job_count": payload["open_job_count"],
                "likely_relevant_job_count": payload["likely_relevant_job_count"],
                "validated_at": payload["last_source_validation"],
            }
        ]
        company, _action = add_or_merge_company(
            payload, origin="Source Discovery", records=records, save=False
        )
        if company is not None:
            updated.append(company)
    save_company_universe(refresh_active_source_flags(records, save=False))
    return updated


def validate_universe_companies(companies, refresh=False, http_get=None):
    """Run existing Source Discovery validation for company names.

    Does not auto-approve sources. Respects the Source Discovery cache
    unless refresh=True. Large lists are processed in batches of 25.
    """
    from source_discovery import SOURCE_DISCOVERY_MAX_COMPANIES, discover_and_validate_sources

    names = []
    for company in companies or []:
        if isinstance(company, dict):
            name = clean_text(company.get("company_name"))
        else:
            name = clean_text(company)
        if name and name not in names:
            names.append(name)
    messages = []
    all_run_records = []
    chunk_size = SOURCE_DISCOVERY_MAX_COMPANIES
    for start in range(0, len(names), chunk_size):
        chunk = names[start:start + chunk_size]
        run_records, _saved, chunk_messages = discover_and_validate_sources(
            chunk,
            refresh=refresh,
            http_get=http_get,
        )
        all_run_records.extend(run_records or [])
        messages.extend(chunk_messages or [])
    apply_source_discovery_results(all_run_records)
    return all_run_records, messages


def companies_needing_ats_validation(records=None, high_priority_only=False):
    """Return companies whose ATS is still unknown."""
    records = records if records is not None else load_company_universe()
    selected = []
    for record in records:
        if record.get("ignored"):
            continue
        if high_priority_only and record.get("priority") != "High":
            continue
        unknown = (record.get("ats_status") or "Unknown") in ("Unknown", "Candidate", "")
        if unknown and not record.get("active_source"):
            selected.append(record)
    return selected


def update_company_fields(record_id, updates):
    """Apply user edits to one company. Returns (record, error)."""
    records = load_company_universe()
    target = None
    index = None
    for i, record in enumerate(records):
        if record.get("id") == record_id:
            target = record
            index = i
            break
    if target is None:
        return None, "I could not find that company."
    updates = updates or {}
    for key in USER_EDITABLE_FIELDS:
        if key not in updates:
            continue
        value = updates.get(key)
        if key == "priority":
            text = clean_text(value) or target.get("priority")
            target["priority"] = text if text in PRIORITIES else target.get("priority")
            target["ignored"] = target["priority"] == "Ignore"
            if target["ignored"]:
                target["active"] = False
            continue
        if key in ("active", "ignored"):
            target[key] = bool(value)
            if key == "ignored" and target["ignored"]:
                target["active"] = False
                target["priority"] = "Ignore"
            continue
        if key in ("bay_area_presence", "remote_friendly"):
            text = clean_text(value)
            target[key] = text if text in ("Yes", "No", "Unknown", "") else target.get(key)
            continue
        target[key] = clean_text(value)
    target["date_updated"] = utc_now()
    target = suggest_company_priority(target)
    records[index] = target
    save_company_universe(records)
    return target, None


def universe_summary(records=None):
    """Return headline counts for the Company Universe tab."""
    records = records if records is not None else load_company_universe()
    summary = {
        "total": len(records),
        "high_priority": 0,
        "ats_validated": 0,
        "active_sources": 0,
        "likely_relevant": 0,
        "ignored": 0,
    }
    for record in records:
        if record.get("priority") == "High":
            summary["high_priority"] += 1
        if record.get("ats_status") in ("Validated", "Approved", "Already Configured") or record.get(
            "source_validation_status"
        ) in ("Validated", "Approved"):
            summary["ats_validated"] += 1
        if record.get("active_source"):
            summary["active_sources"] += 1
        if _int_value(record.get("likely_relevant_job_count")) > 0:
            summary["likely_relevant"] += 1
        if record.get("ignored") or record.get("priority") == "Ignore":
            summary["ignored"] += 1
    return summary


def _priority_rank(value):
    order = {"High": 0, "Medium": 1, "Low": 2, "Watch": 3, "Ignore": 4}
    return order.get(value, 5)


def sort_companies(records):
    """Sort by priority, then likely relevant jobs, then name."""
    return sorted(
        records or [],
        key=lambda record: (
            _priority_rank(record.get("priority")),
            -_int_value(record.get("likely_relevant_job_count")),
            (record.get("company_name") or "").lower(),
        ),
    )


def filter_companies(
    records,
    priority="All",
    industry="All",
    ats_type="All",
    ats_status="All",
    bay_area="All",
    remote="All",
    active_state="All",
):
    """Apply Company Universe table filters."""
    filtered = []
    for record in records or []:
        if priority != "All" and record.get("priority") != priority:
            continue
        if industry != "All":
            current = clean_text(record.get("industry")) or "Unknown"
            if current != industry:
                continue
        if ats_type != "All":
            current_ats = clean_text(record.get("ats_type")) or "unknown"
            if current_ats.lower() != ats_type.lower():
                continue
        if ats_status != "All" and (record.get("ats_status") or "Unknown") != ats_status:
            continue
        if bay_area != "All" and (record.get("bay_area_presence") or "Unknown") != bay_area:
            continue
        if remote != "All" and (record.get("remote_friendly") or "Unknown") != remote:
            continue
        if active_state == "Active" and (record.get("ignored") or not record.get("active")):
            continue
        if active_state == "Ignored" and not (record.get("ignored") or record.get("priority") == "Ignore"):
            continue
        filtered.append(record)
    return filtered


def jobs_for_company(company, job_groups):
    """Lightweight job linkage by normalized company name."""
    target = company.get("normalized_name") or normalize_company_name(company.get("company_name"))
    matches = []
    for group in job_groups or []:
        for job in group or []:
            if not isinstance(job, dict):
                continue
            if normalize_company_name(job.get("company")) == target:
                matches.append(job)
    return matches
