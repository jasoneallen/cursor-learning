#!/usr/bin/env python3
"""Approved live-source registry.

Stores user-approved Greenhouse boards and Lever sites in JSON so the
Operator can grow its source list without editing Python config. This
module never calls OpenAI.
"""

import json
import os
from datetime import datetime, timezone

from job_sources import clean_text


DEFAULT_DATA_DIR = "data"
APPROVED_FILENAME = "approved_sources.json"
DISCOVERED_FILENAME = "discovered_sources.json"


def data_dir():
    """Return the local data folder. Tests may override OPERATOR_DATA_DIR."""
    return os.environ.get("OPERATOR_DATA_DIR") or DEFAULT_DATA_DIR


def approved_sources_path():
    return os.path.join(data_dir(), APPROVED_FILENAME)


def discovered_sources_path():
    return os.path.join(data_dir(), DISCOVERED_FILENAME)


def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def empty_approved_registry():
    return {
        "greenhouse": {},
        "lever": {},
        "health": {},
    }


def _normalize_company_map(raw_map):
    """Return {company_name: identifier} from a JSON object."""
    mapping = {}
    if not isinstance(raw_map, dict):
        return mapping
    for company_name, identifier in raw_map.items():
        name = clean_text(company_name)
        token = clean_text(identifier)
        if name and token:
            mapping[name] = token
    return mapping


def load_approved_registry():
    """Load approved sources. Missing or malformed files become empty maps."""
    registry = empty_approved_registry()
    path = approved_sources_path()
    if not os.path.exists(path):
        return registry
    try:
        with open(path, encoding="utf-8") as file:
            raw = file.read().strip()
        if raw == "":
            return registry
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        return registry
    if not isinstance(data, dict):
        return registry
    registry["greenhouse"] = _normalize_company_map(data.get("greenhouse"))
    registry["lever"] = _normalize_company_map(data.get("lever"))
    health = data.get("health")
    registry["health"] = health if isinstance(health, dict) else {}
    return registry


def save_approved_registry(registry):
    """Write approved sources, creating the data folder if needed."""
    payload = empty_approved_registry()
    payload["greenhouse"] = _normalize_company_map((registry or {}).get("greenhouse"))
    payload["lever"] = _normalize_company_map((registry or {}).get("lever"))
    health = (registry or {}).get("health")
    payload["health"] = health if isinstance(health, dict) else {}
    os.makedirs(data_dir(), exist_ok=True)
    with open(approved_sources_path(), "w", encoding="utf-8") as file:
        json.dump(payload, file, indent=2)


def load_approved_maps():
    """Return greenhouse and lever company maps only."""
    registry = load_approved_registry()
    return {
        "greenhouse": dict(registry.get("greenhouse") or {}),
        "lever": dict(registry.get("lever") or {}),
    }


def health_key(ats_type, identifier):
    return f"{clean_text(ats_type).lower()}|{clean_text(identifier).lower()}"


def merge_source_maps(hardcoded, approved):
    """Keep hardcoded entries first. Skip duplicate names or identifiers."""
    merged = {}
    seen_identifiers = set()
    for company_name, identifier in (hardcoded or {}).items():
        name = clean_text(company_name)
        token = clean_text(identifier)
        if not name or not token:
            continue
        merged[name] = token
        seen_identifiers.add(token.lower())
    for company_name, identifier in (approved or {}).items():
        name = clean_text(company_name)
        token = clean_text(identifier)
        if not name or not token:
            continue
        if name in merged:
            continue
        if token.lower() in seen_identifiers:
            continue
        merged[name] = token
        seen_identifiers.add(token.lower())
    return merged


def identifier_is_configured(ats_type, identifier, hardcoded, approved=None):
    """Return True when this ATS identifier is already live-configured."""
    token = clean_text(identifier).lower()
    if not token:
        return False
    approved = approved if approved is not None else load_approved_maps().get(ats_type, {})
    for mapping in (hardcoded or {}, approved or {}):
        for value in mapping.values():
            if clean_text(value).lower() == token:
                return True
    return False


def add_approved_source(ats_type, company_name, identifier):
    """Add one approved source. Returns (added, message)."""
    ats_type = clean_text(ats_type).lower()
    name = clean_text(company_name)
    token = clean_text(identifier)
    if ats_type not in ("greenhouse", "lever"):
        return False, "Only Greenhouse and Lever sources can be approved."
    if not name or not token:
        return False, "A company name and identifier are required to approve a source."
    registry = load_approved_registry()
    existing = registry.get(ats_type) or {}
    for existing_name, existing_token in existing.items():
        if existing_name.lower() == name.lower():
            return False, f"{name} is already approved."
        if clean_text(existing_token).lower() == token.lower():
            return False, f"{existing_name} is already approved as {token}."
    existing[name] = token
    registry[ats_type] = existing
    key = health_key(ats_type, token)
    health = registry.get("health") or {}
    entry = dict(health.get(key) or {})
    entry.setdefault("last_successful_fetch", "")
    entry["last_validation"] = utc_now()
    entry["current_status"] = entry.get("current_status") or "Active"
    health[key] = entry
    registry["health"] = health
    save_approved_registry(registry)
    return True, f"Approved {name} ({ats_type}: {token})."


def record_source_health(ats_type, identifier, current_status, fetched=False, validated=False):
    """Update health metadata for an approved source. Unknown sources are ignored."""
    ats_type = clean_text(ats_type).lower()
    token = clean_text(identifier)
    if not ats_type or not token:
        return
    if current_status not in ("Active", "Empty", "Invalid", "Error"):
        return
    registry = load_approved_registry()
    approved = registry.get(ats_type) or {}
    if not any(clean_text(value).lower() == token.lower() for value in approved.values()):
        return
    key = health_key(ats_type, token)
    health = registry.get("health") or {}
    entry = dict(health.get(key) or {})
    now = utc_now()
    entry["current_status"] = current_status
    if validated or not entry.get("last_validation"):
        entry["last_validation"] = now
    if fetched and current_status in ("Active", "Empty"):
        entry["last_successful_fetch"] = now
    health[key] = entry
    registry["health"] = health
    save_approved_registry(registry)


def source_health(ats_type, identifier):
    """Return health metadata for one approved source, or empty defaults."""
    registry = load_approved_registry()
    entry = (registry.get("health") or {}).get(health_key(ats_type, identifier))
    if not isinstance(entry, dict):
        return {
            "last_successful_fetch": "",
            "last_validation": "",
            "current_status": "",
        }
    return {
        "last_successful_fetch": clean_text(entry.get("last_successful_fetch")),
        "last_validation": clean_text(entry.get("last_validation")),
        "current_status": clean_text(entry.get("current_status")),
    }
