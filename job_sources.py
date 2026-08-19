#!/usr/bin/env python3
"""Simple job-source adapters.

Each source returns a list of dictionaries. The Operator then normalizes,
deduplicates, and prefilters those records. This version includes a local
JSON file source plus public Greenhouse and Lever adapters. It does not
scrape LinkedIn or other websites.
"""

import json
import os
import re
from datetime import date


DEFAULT_INCOMING_PATH = os.path.join("data", "incoming_jobs.json")


def clean_text(value):
    """Trim text and turn None into an empty string."""
    if value is None:
        return ""
    return str(value).strip()


def normalize_name(value):
    """Lowercase a company or source name and collapse extra spaces."""
    text = clean_text(value).lower()
    text = " ".join(text.split())
    return text


def normalize_url(value):
    """Lowercase a URL and drop a trailing slash and common tracking prefixes."""
    text = clean_text(value).lower()
    if text.endswith("/"):
        text = text[:-1]
    for prefix in ("https://", "http://"):
        if text.startswith(prefix):
            text = text[len(prefix):]
            break
    if text.startswith("www."):
        text = text[4:]
    return text


def normalize_location(value):
    """Lowercase a location and standardize a few Bay Area spellings."""
    text = clean_text(value).lower()
    text = text.replace(",", " ")
    text = " ".join(text.split())
    replacements = {
        "sf bay area": "bay area",
        "san francisco bay area": "bay area",
        "s.f.": "san francisco",
        "sf": "san francisco",
    }
    for old, new in replacements.items():
        if text == old:
            return new
    return text


def unique_sources(*groups):
    """Return unique source ids, keeping first-seen order."""
    seen = []
    for group in groups:
        if group is None or group == "":
            continue
        if isinstance(group, str):
            values = [group]
        elif isinstance(group, (list, tuple)):
            values = group
        else:
            continue
        for item in values:
            text = clean_text(item)
            if text and text not in seen:
                seen.append(text)
    return seen


SOURCE_DISPLAY_NAMES = {
    "greenhouse": "Greenhouse",
    "lever": "Lever",
    "local_json": "Local JSON",
    "all_live": "All Live Sources",
}


def format_source_label(job):
    """Readable source list for discovery tables, e.g. 'Greenhouse, Lever'."""
    if not isinstance(job, dict):
        return ""
    sources = unique_sources(job.get("discovery_sources"), job.get("source"))
    labels = []
    for source_id in sources:
        if source_id == "all_live":
            continue
        label = SOURCE_DISPLAY_NAMES.get(source_id, source_id)
        if label not in labels:
            labels.append(label)
    return ", ".join(labels)


def parse_salary_number(value):
    """Turn strings like 180000, $180,000, or 180k into an integer when possible."""
    if value is None or value == "":
        return ""
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).lower().replace(",", "").replace("$", "").strip()
    multiplier = 1
    if text.endswith("k"):
        multiplier = 1000
        text = text[:-1]
    match = re.search(r"(\d+(?:\.\d+)?)", text)
    if not match:
        return ""
    try:
        return int(float(match.group(1)) * multiplier)
    except ValueError:
        return ""


def infer_remote_type(location, remote_type, description):
    """Guess remote/hybrid/onsite from fields the source already provided."""
    explicit = clean_text(remote_type).lower()
    if explicit in ("remote", "hybrid", "onsite", "on-site", "in-office"):
        if explicit in ("onsite", "on-site", "in-office"):
            return "onsite"
        return explicit
    blob = " ".join([location or "", description or ""]).lower()
    if "remote" in blob and "hybrid" in blob:
        return "hybrid"
    if "hybrid" in blob:
        return "hybrid"
    if "remote" in blob:
        return "remote"
    if "on-site" in blob or "onsite" in blob or "in office" in blob:
        return "onsite"
    return clean_text(remote_type)


def empty_job_record():
    """Return a blank normalized job with every field the Operator uses."""
    return {
        "id": "",
        "external_id": "",
        "company": "",
        "title": "",
        "location": "",
        "remote_type": "",
        "employment_type": "",
        "salary_min": "",
        "salary_max": "",
        "salary_text": "",
        "source": "",
        "url": "",
        "description": "",
        "date_found": "",
        "date_posted": "",
        "raw_source_data": {},
        "pre_score": "",
        "prefilter_passed": False,
        "prefilter_reasons": [],
        "rejection_reason": "",
        "ai_eligible": False,
        "match_score": "",
        "recommendation": "",
        "blocker_severity": "None",
        "blocker_summary": "",
        "ai_analysis": {},
        "analysis_stale": False,
        "status": "Discovered",
        "date_applied": "",
        "notes": "",
        "discovery_sources": [],
    }


def normalize_incoming_job(raw_job, default_source=""):
    """Turn a source-specific dictionary into the Operator's common format."""
    if not isinstance(raw_job, dict):
        return None
    job = empty_job_record()
    job["external_id"] = clean_text(raw_job.get("external_id") or raw_job.get("id"))
    job["company"] = clean_text(raw_job.get("company") or raw_job.get("company_name"))
    job["title"] = clean_text(raw_job.get("title") or raw_job.get("job_title"))
    job["location"] = clean_text(raw_job.get("location"))
    job["employment_type"] = clean_text(raw_job.get("employment_type") or raw_job.get("type"))
    job["salary_text"] = clean_text(raw_job.get("salary_text") or raw_job.get("salary"))
    job["salary_min"] = parse_salary_number(raw_job.get("salary_min"))
    job["salary_max"] = parse_salary_number(raw_job.get("salary_max"))
    job["source"] = clean_text(raw_job.get("source") or default_source or "local_json")
    job["url"] = clean_text(raw_job.get("url") or raw_job.get("job_url"))
    job["description"] = clean_text(raw_job.get("description") or raw_job.get("job_description"))
    job["date_posted"] = clean_text(raw_job.get("date_posted") or raw_job.get("posted_at"))
    job["date_found"] = clean_text(raw_job.get("date_found")) or date.today().isoformat()
    provided_remote = raw_job.get("remote_type") or raw_job.get("work_mode")
    # Live ATS adapters already infer remote_type conservatively. Do not guess
    # from the job description for those sources.
    if job["source"] in ("greenhouse", "lever"):
        job["remote_type"] = clean_text(provided_remote)
    else:
        job["remote_type"] = infer_remote_type(
            job["location"],
            provided_remote,
            job["description"],
        )
    raw_source_data = raw_job.get("raw_source_data")
    if isinstance(raw_source_data, dict) and raw_source_data:
        job["raw_source_data"] = raw_source_data
    else:
        job["raw_source_data"] = {
            key: value for key, value in raw_job.items() if key != "raw_source_data"
        }
    job["discovery_sources"] = unique_sources(
        raw_job.get("discovery_sources"),
        job["source"],
    )
    return job


class JobSource:
    """Base adapter. Subclasses only need to implement fetch()."""

    name = "base"
    source_id = "base"

    def fetch(self):
        """Return (raw_jobs, error_message). error_message is None on success."""
        return [], "This source is not implemented yet."


class JsonFileJobSource(JobSource):
    """Read jobs from a local JSON file. This is the first test source."""

    name = "Local JSON file"
    source_id = "local_json"

    def __init__(self, path=DEFAULT_INCOMING_PATH):
        self.path = path

    def fetch(self):
        """Load a JSON list from disk. Missing files do not crash the Operator."""
        if not os.path.exists(self.path):
            return [], (
                f"I could not find {self.path}. "
                "Create that file with a JSON list of jobs to test ingestion."
            )
        try:
            with open(self.path, encoding="utf-8") as file:
                raw = file.read().strip()
        except OSError as error:
            return [], f"I could not read {self.path}: {error}"
        if raw == "":
            return [], f"{self.path} is empty. Add a JSON list of jobs, then fetch again."
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return [], f"{self.path} is not valid JSON. It should be a list of job objects."
        if isinstance(data, dict):
            data = data.get("jobs") or data.get("items") or []
        if not isinstance(data, list):
            return [], f"{self.path} must contain a JSON list of jobs."
        return data, None


def available_sources():
    """Return the source adapters this version supports."""
    from all_live_source import AllLiveJobSource
    from greenhouse_source import GreenhouseJobSource
    from lever_source import LeverJobSource

    return [
        AllLiveJobSource(),
        GreenhouseJobSource(),
        LeverJobSource(),
        JsonFileJobSource(),
    ]
