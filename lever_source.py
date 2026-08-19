#!/usr/bin/env python3
"""Lever Postings API adapter.

Uses the public Lever Postings API only:

    GET https://api.lever.co/v0/postings/<site>?mode=json
    GET https://api.eu.lever.co/v0/postings/<site>?mode=json

This module never calls OpenAI. Fetched jobs are mapped into the same
incoming-job dictionaries used by the local JSON and Greenhouse sources.
"""

import re
from datetime import datetime, timezone

import requests

from job_source_config import LEVER_SITES
from job_sources import JobSource, clean_text, parse_salary_number


LEVER_API_US = "https://api.lever.co/v0/postings"
LEVER_API_EU = "https://api.eu.lever.co/v0/postings"
LEVER_TIMEOUT = (5, 20)
LEVER_USER_AGENT = "AI-Job-Operator/1.0 (personal job search)"
LEVER_PAGE_SIZE = 100
LEVER_MAX_SKIP = 3000


def parse_lever_site(value):
    """Return (site_slug, preferred_region) from a slug or pasted Lever URL."""
    text = clean_text(value)
    if not text:
        return "", "us"
    lowered = text.lower()
    region = "eu" if ("jobs.eu.lever.co" in lowered or "api.eu.lever.co" in lowered) else "us"
    markers = (
        "https://api.eu.lever.co/v0/postings/",
        "http://api.eu.lever.co/v0/postings/",
        "https://api.lever.co/v0/postings/",
        "http://api.lever.co/v0/postings/",
        "https://jobs.eu.lever.co/",
        "http://jobs.eu.lever.co/",
        "https://jobs.lever.co/",
        "http://jobs.lever.co/",
        "jobs.eu.lever.co/",
        "jobs.lever.co/",
    )
    for marker in markers:
        if marker in lowered:
            remainder = lowered.split(marker, 1)[1]
            remainder = remainder.split("?", 1)[0].split("/", 1)[0]
            text = remainder
            break
    text = text.strip().strip("/")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", text):
        return "", region
    return text, region


def configured_lever_sites():
    """Return valid {company_name: site_slug} entries from config."""
    sites = {}
    raw_sites = LEVER_SITES or {}
    if not isinstance(raw_sites, dict):
        return sites
    for company_name, identifier in raw_sites.items():
        name = clean_text(company_name)
        slug, _region = parse_lever_site(identifier)
        if name and slug:
            sites[name] = clean_text(identifier) or slug
    return sites


def lever_api_roots(preferred_region):
    """Return API roots to try, preferred region first. One fallback only."""
    if preferred_region == "eu":
        return [LEVER_API_EU, LEVER_API_US]
    return [LEVER_API_US, LEVER_API_EU]


def lever_location_text(job):
    """Preserve displayed location from Lever categories."""
    categories = job.get("categories") or {}
    if not isinstance(categories, dict):
        categories = {}
    primary = clean_text(categories.get("location"))
    all_locations = categories.get("allLocations") or []
    names = []
    if isinstance(all_locations, list):
        for item in all_locations:
            text = clean_text(item)
            if text and text not in names:
                names.append(text)
    if primary and primary not in names:
        names.insert(0, primary)
    return "; ".join(names)


def lever_employment_type(job):
    """Read commitment/employment type when Lever provides it."""
    categories = job.get("categories") or {}
    if isinstance(categories, dict):
        return clean_text(categories.get("commitment") or categories.get("employmentType"))
    return ""


def lever_salary_fields(job):
    """Read salary only from explicit Lever pay fields. Do not invent numbers."""
    salary_range = job.get("salaryRange") or {}
    salary_min = ""
    salary_max = ""
    extra = ""
    if isinstance(salary_range, dict) and salary_range:
        salary_min = parse_salary_number(salary_range.get("min"))
        salary_max = parse_salary_number(salary_range.get("max"))
        extra = " ".join(
            part
            for part in (
                clean_text(salary_range.get("currency")),
                clean_text(salary_range.get("interval")),
            )
            if part
        )
    salary_text = clean_text(job.get("salaryDescriptionPlain") or "")
    if not salary_text and (salary_min != "" or salary_max != ""):
        pieces = []
        if salary_min != "" and salary_max != "":
            pieces.append(f"{salary_min}-{salary_max}")
        elif salary_min != "":
            pieces.append(str(salary_min))
        elif salary_max != "":
            pieces.append(str(salary_max))
        if extra:
            pieces.append(extra)
        salary_text = " ".join(pieces)
    elif extra and extra not in salary_text:
        salary_text = f"{salary_text} {extra}".strip()
    return salary_min, salary_max, salary_text


def lever_date_posted(job):
    """Convert Lever createdAt milliseconds to YYYY-MM-DD when possible."""
    created = job.get("createdAt")
    if isinstance(created, (int, float)) and created > 0:
        seconds = created / 1000.0 if created > 10_000_000_000 else created
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc).date().isoformat()
        except (OSError, OverflowError, ValueError):
            return ""
    return clean_text(created)


def lever_remote_type(job, location_text):
    """Use Lever workplaceType or a clearly remote/hybrid location. Do not guess."""
    from greenhouse_source import infer_clear_remote_type

    workplace = clean_text(job.get("workplaceType")).replace("_", " ")
    if workplace.lower() == "unspecified":
        workplace = ""
    return infer_clear_remote_type(location_text, workplace)


def lever_description_text(job):
    """Build readable plain text from Lever description fields and lists."""
    from greenhouse_source import html_to_text

    parts = []
    plain = clean_text(job.get("descriptionPlain") or job.get("openingPlain"))
    if plain:
        parts.append(plain)
    elif job.get("description") or job.get("opening"):
        converted = html_to_text(job.get("description") or job.get("opening"))
        if converted:
            parts.append(converted)
    lists = job.get("lists") or []
    if isinstance(lists, list):
        for item in lists:
            if not isinstance(item, dict):
                continue
            heading = clean_text(item.get("text"))
            content = html_to_text(item.get("content") or "")
            if heading:
                parts.append(heading)
            if content:
                parts.append(content)
    additional = clean_text(job.get("additionalPlain"))
    if additional:
        parts.append(additional)
    return "\n\n".join(part for part in parts if part).strip()


def map_lever_job(job, company_name, site_slug):
    """Map one Lever posting into the Operator's incoming-job shape."""
    if not isinstance(job, dict):
        return None
    location = lever_location_text(job)
    salary_min, salary_max, salary_text = lever_salary_fields(job)
    mapped = {
        "source": "lever",
        "external_id": clean_text(job.get("id")),
        "company": clean_text(company_name),
        "title": clean_text(job.get("text") or job.get("title")),
        "location": location,
        "remote_type": lever_remote_type(job, location),
        "employment_type": lever_employment_type(job),
        "salary_min": salary_min,
        "salary_max": salary_max,
        "salary_text": salary_text,
        "url": clean_text(job.get("hostedUrl") or job.get("applyUrl") or job.get("url")),
        "description": lever_description_text(job),
        "date_posted": lever_date_posted(job),
        "raw_source_data": {
            "site_slug": site_slug,
            "configured_company": company_name,
            "job": job,
        },
    }
    if not mapped["title"] and not mapped["url"] and not mapped["external_id"]:
        return None
    return mapped


def _request_lever_page(api_root, site_slug, skip, timeout):
    """Fetch one Lever JSON page. Returns (jobs_or_none, error, http_status)."""
    url = f"{api_root}/{site_slug}"
    try:
        response = requests.get(
            url,
            params={"mode": "json", "skip": skip, "limit": LEVER_PAGE_SIZE},
            timeout=timeout,
            headers={
                "Accept": "application/json",
                "User-Agent": LEVER_USER_AGENT,
            },
        )
    except requests.Timeout:
        return None, f"The Lever request for {site_slug} timed out.", None
    except requests.RequestException as error:
        return None, f"I could not reach Lever for {site_slug}: {error}", None
    if response.status_code == 404:
        return None, None, 404
    if response.status_code == 429:
        return None, f"Lever rate-limited the request for {site_slug}. Try again later.", response.status_code
    if not response.ok:
        return None, f"Lever returned HTTP {response.status_code} for {site_slug}.", response.status_code
    try:
        data = response.json()
    except ValueError:
        return None, f"Lever returned a response I could not parse for {site_slug}.", response.status_code
    if isinstance(data, dict):
        jobs = data.get("data") or data.get("postings") or data.get("jobs") or []
    elif isinstance(data, list):
        jobs = data
    else:
        return None, f"Lever returned an unexpected response for {site_slug}.", response.status_code
    if not isinstance(jobs, list):
        return None, f"Lever returned an unexpected job list for {site_slug}.", response.status_code
    return jobs, None, response.status_code


def fetch_lever_site(site_identifier, company_name, timeout=LEVER_TIMEOUT):
    """Fetch open jobs for one public Lever site. Does not retry or call OpenAI."""
    site_slug, preferred_region = parse_lever_site(site_identifier)
    display_name = clean_text(company_name) or site_slug or "this company"
    if not site_slug:
        return [], f"The Lever site identifier for {display_name} is invalid."

    last_error = None
    for index, api_root in enumerate(lever_api_roots(preferred_region)):
        skip = 0
        seen_ids = set()
        collected = []
        page_error = None
        found_site = False
        while skip <= LEVER_MAX_SKIP:
            jobs, error, status = _request_lever_page(api_root, site_slug, skip, timeout)
            if status == 404:
                page_error = (
                    f"Lever site '{site_slug}' was not found for {display_name}. "
                    "Check the site identifier."
                )
                found_site = False
                break
            if error:
                return [], error.replace(site_slug, display_name) if display_name != site_slug else error
            found_site = True
            new_count = 0
            for job in jobs:
                if not isinstance(job, dict):
                    continue
                job_id = clean_text(job.get("id"))
                if job_id and job_id in seen_ids:
                    continue
                if job_id:
                    seen_ids.add(job_id)
                incoming = map_lever_job(job, display_name, site_slug)
                if incoming is not None:
                    collected.append(incoming)
                    new_count += 1
            if len(jobs) < LEVER_PAGE_SIZE or new_count == 0:
                break
            skip += LEVER_PAGE_SIZE
        if found_site:
            return collected, None
        last_error = page_error
        # One region fallback only, and only after a 404.
        if index == 0 and page_error:
            continue
        break
    return [], last_error or f"Lever site '{site_slug}' was not found for {display_name}."


class LeverJobSource(JobSource):
    """Fetch currently open jobs from one or more public Lever career sites."""

    name = "Lever"
    source_id = "lever"

    def __init__(self, sites=None):
        if sites is None:
            self.sites = configured_lever_sites()
        else:
            self.sites = dict(sites)

    def fetch(self):
        """Return (incoming_jobs, error_message). error_message may be a warning."""
        sites = self.sites or configured_lever_sites()
        if not sites:
            return [], (
                "No Lever companies are configured. "
                "Add entries to LEVER_SITES in job_source_config.py."
            )
        all_jobs = []
        errors = []
        for company_name, site_identifier in sites.items():
            jobs, error = fetch_lever_site(site_identifier, company_name)
            if error:
                errors.append(error)
            all_jobs.extend(jobs)
        if all_jobs:
            warning = " ".join(errors) if errors else None
            return all_jobs, warning
        if errors:
            return [], " ".join(errors)
        return [], None
