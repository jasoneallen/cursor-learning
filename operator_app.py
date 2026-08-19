#!/usr/bin/env python3
"""Streamlit shell for a personal AI Job Operator.

This app lets you analyze jobs against a candidate profile, track a local
pipeline, discover jobs from public Greenhouse and Lever boards, find
additional ATS sources, and maintain a company universe. GPT-5 mini runs
only when you ask.

Run with:
  source .venv/bin/activate
  streamlit run operator_app.py
"""

import json
import os
import uuid
from datetime import date

import streamlit as st

from job_matcher import BLOCKER_SEVERITIES, FIT_SCORE_FIELDS, RECOMMENDATIONS, analyze_with_ai
from greenhouse_source import GreenhouseJobSource, configured_greenhouse_boards
from job_prefilter import (
    AI_ANALYSIS_THRESHOLD,
    find_duplicate,
    has_ai_analysis,
    ingest_jobs,
    needs_ai_analysis,
)
from job_sources import available_sources, format_source_label, unique_sources
from lever_source import LeverJobSource, configured_lever_sites
from source_discovery import (
    approve_discovered_source,
    companies_from_jobs,
    discover_and_validate_sources,
    ignore_source,
    load_discovered_sources,
    parse_company_names,
    set_record_notes,
)
from source_registry import load_approved_maps, source_health
from company_universe import (
    LARGE_BATCH_THRESHOLD,
    PRIORITIES,
    add_companies_from_jobs,
    add_companies_from_names,
    add_companies_from_source_map,
    apply_source_discovery_results,
    companies_needing_ats_validation,
    filter_companies,
    import_companies_from_text,
    initialize_from_operator_data,
    jobs_for_company,
    load_company_universe,
    sort_companies,
    universe_summary,
    update_company_fields,
    validate_universe_companies,
)
from company_universe_config import PREFERRED_INDUSTRIES

# Local JSON store for pipeline jobs. Created on first save.
DATA_DIR = "data"
JOBS_FILE = os.path.join(DATA_DIR, "jobs.json")
DEFAULT_PROFILE_PATH = "private/profile.txt"

# Allowed pipeline statuses for this version.
STATUSES = [
    "Discovered",
    "Review",
    "Apply",
    "Applied",
    "Recruiter",
    "Interview",
    "Offer",
    "Closed",
]


# ---------------------------------------------------------------------------
# Local data helpers
# ---------------------------------------------------------------------------

def load_profile_text():
    """Load private/profile.txt when it exists. Return "" if it does not."""
    if not os.path.exists(DEFAULT_PROFILE_PATH):
        return ""
    try:
        with open(DEFAULT_PROFILE_PATH, encoding="utf-8") as file:
            return file.read()
    except OSError:
        return ""


def load_jobs():
    """Read jobs from data/jobs.json.

    Missing, empty, or malformed files become an empty list plus a warning.
    Older records get safe defaults for new fields.
    """
    if not os.path.exists(JOBS_FILE):
        return [], None
    try:
        with open(JOBS_FILE, encoding="utf-8") as file:
            raw = file.read().strip()
        if raw == "":
            return [], "The jobs file is empty, so the pipeline starts blank."
        jobs = json.loads(raw)
        if not isinstance(jobs, list):
            return [], "The jobs file is not a list, so the pipeline starts blank."
    except (OSError, json.JSONDecodeError):
        return [], "The jobs file could not be read, so the pipeline starts blank."

    normalized = []
    changed = False
    for raw_job in jobs:
        job, filled = normalize_job(raw_job)
        if job is None:
            changed = True
            continue
        if filled:
            changed = True
        normalized.append(job)
    if changed:
        save_jobs(normalized)
    return normalized, None


def save_jobs(jobs):
    """Write the job list to data/jobs.json, creating the folder if needed."""
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(JOBS_FILE, "w", encoding="utf-8") as file:
        json.dump(jobs, file, indent=2)


def normalize_job(raw_job):
    """Fill missing fields on an older saved job.

    Returns (job_dict, filled_missing_fields) or (None, True) when the
    record cannot be used.
    """
    if not isinstance(raw_job, dict):
        return None, True

    job = {
        "id": raw_job.get("id") or str(uuid.uuid4()),
        "external_id": raw_job.get("external_id") or "",
        "company": raw_job.get("company") or "",
        "title": raw_job.get("title") or "",
        "location": raw_job.get("location") or "",
        "remote_type": raw_job.get("remote_type") or "",
        "employment_type": raw_job.get("employment_type") or "",
        "salary_min": raw_job.get("salary_min", ""),
        "salary_max": raw_job.get("salary_max", ""),
        "salary_text": raw_job.get("salary_text") or "",
        "source": raw_job.get("source") or "",
        "url": raw_job.get("url") or "",
        "description": raw_job.get("description") or "",
        "date_found": raw_job.get("date_found") or "",
        "date_posted": raw_job.get("date_posted") or "",
        "raw_source_data": raw_job.get("raw_source_data") or {},
        "pre_score": raw_job.get("pre_score", ""),
        "prefilter_passed": bool(raw_job.get("prefilter_passed", False)),
        "prefilter_reasons": raw_job.get("prefilter_reasons") or [],
        "rejection_reason": raw_job.get("rejection_reason") or "",
        "ai_eligible": bool(raw_job.get("ai_eligible", False)),
        "match_score": raw_job.get("match_score", ""),
        "recommendation": raw_job.get("recommendation") or "",
        "blocker_severity": raw_job.get("blocker_severity") or "None",
        "blocker_summary": raw_job.get("blocker_summary") or "",
        "ai_analysis": raw_job.get("ai_analysis") or {},
        "analysis_stale": bool(raw_job.get("analysis_stale", False)),
        "experience_score": raw_job.get("experience_score", ""),
        "experience_score_explanation": raw_job.get("experience_score_explanation") or "",
        "industry_score": raw_job.get("industry_score", ""),
        "industry_score_explanation": raw_job.get("industry_score_explanation") or "",
        "leadership_score": raw_job.get("leadership_score", ""),
        "leadership_score_explanation": raw_job.get("leadership_score_explanation") or "",
        "responsibilities_score": raw_job.get("responsibilities_score", ""),
        "responsibilities_score_explanation": raw_job.get("responsibilities_score_explanation") or "",
        "technical_score": raw_job.get("technical_score", ""),
        "technical_score_explanation": raw_job.get("technical_score_explanation") or "",
        "company_stage_score": raw_job.get("company_stage_score", ""),
        "company_stage_score_explanation": raw_job.get("company_stage_score_explanation") or "",
        "compensation_score": raw_job.get("compensation_score", ""),
        "compensation_score_explanation": raw_job.get("compensation_score_explanation") or "",
        "location_score": raw_job.get("location_score", ""),
        "location_score_explanation": raw_job.get("location_score_explanation") or "",
        "overall_match_score": raw_job.get("overall_match_score", ""),
        "overall_match_score_explanation": raw_job.get("overall_match_score_explanation") or "",
        "status": raw_job.get("status") or "Discovered",
        "date_applied": raw_job.get("date_applied") or "",
        "notes": raw_job.get("notes") or "",
        "discovery_sources": unique_sources(
            raw_job.get("discovery_sources"),
            raw_job.get("source"),
        ),
    }
    if job["blocker_severity"] not in BLOCKER_SEVERITIES:
        job["blocker_severity"] = "None"
    if job["status"] not in STATUSES:
        job["status"] = "Discovered"

    # Null is a real stored value for these scores, not a missing field.
    nullable_score_keys = {"compensation_score", "location_score"}
    required_keys = list(job.keys())
    filled = False
    for key in required_keys:
        if key not in raw_job:
            filled = True
            break
        if raw_job.get(key) is None and key not in nullable_score_keys:
            filled = True
            break
    return job, filled


def normalize(value):
    """Lowercase and trim text for duplicate checks."""
    return (value or "").strip().lower()


def is_duplicate(jobs, company, title, url):
    """Return True when company+title or URL already exists in the pipeline."""
    company_key = normalize(company)
    title_key = normalize(title)
    url_key = normalize(url)
    for job in jobs:
        existing_url = normalize(job.get("url"))
        if url_key and existing_url and url_key == existing_url:
            return True
        same_company = company_key and company_key == normalize(job.get("company"))
        same_title = title_key and title_key == normalize(job.get("title"))
        if same_company and same_title:
            return True
    return False


def make_job(company, title, location, source, url, description, result):
    """Build one job record from form fields plus AI analysis."""
    job, _filled = normalize_job(
        {
            "id": str(uuid.uuid4()),
            "company": company.strip(),
            "title": title.strip(),
            "location": location.strip(),
            "source": source.strip(),
            "url": url.strip(),
            "description": description.strip(),
            "date_found": date.today().isoformat(),
            "match_score": result.get("match_score", ""),
            "recommendation": result.get("recommendation", ""),
            "blocker_severity": result.get("blocker_severity", "None"),
            "blocker_summary": result.get("blocker_summary", ""),
            "ai_analysis": result,
            "analysis_stale": False,
            "status": "Discovered",
            "date_applied": "",
            "notes": "",
        }
    )
    return copy_fit_scores(job, result)


def copy_fit_scores(target, result):
    """Copy dimension scores from an AI result onto a job record."""
    overall = result.get("overall_match_score", result.get("match_score", ""))
    target["overall_match_score"] = overall
    target["match_score"] = overall if overall != "" else result.get("match_score", "")
    for score_key, explanation_key in FIT_SCORE_FIELDS:
        if score_key == "overall_match_score":
            target[explanation_key] = (
                result.get(explanation_key) or result.get("score_explanation") or ""
            )
            continue
        target[score_key] = result.get(score_key, "")
        target[explanation_key] = result.get(explanation_key) or ""
    return target


def apply_ai_result(job, result):
    """Copy GPT results onto a job without dropping pipeline fields."""
    updated = dict(job)
    if not updated.get("id"):
        updated["id"] = str(uuid.uuid4())
    updated["ai_analysis"] = result
    updated["recommendation"] = result.get("recommendation", "")
    updated["blocker_severity"] = result.get("blocker_severity", "None")
    updated["blocker_summary"] = result.get("blocker_summary", "")
    updated["analysis_stale"] = False
    copy_fit_scores(updated, result)
    if not updated.get("status"):
        updated["status"] = "Discovered"
    if not updated.get("date_found"):
        updated["date_found"] = date.today().isoformat()
    normalized, _filled = normalize_job(updated)
    return normalized


def save_analyzed_job(job):
    """Insert or update one analyzed job in the pipeline."""
    jobs, warning = load_jobs()
    duplicate = None
    if job.get("id"):
        for existing in jobs:
            if existing.get("id") == job.get("id"):
                duplicate = existing
                break
    if duplicate is None:
        duplicate = find_duplicate(jobs, job)
    if duplicate is not None:
        job["id"] = duplicate.get("id")
        job["status"] = duplicate.get("status") or job.get("status") or "Discovered"
        job["notes"] = duplicate.get("notes") or job.get("notes") or ""
        job["date_applied"] = duplicate.get("date_applied") or job.get("date_applied") or ""
        jobs = replace_job(jobs, job)
    else:
        if not job.get("id"):
            job["id"] = str(uuid.uuid4())
        if not job.get("status"):
            job["status"] = "Discovered"
        jobs.append(job)
        save_jobs(jobs)
    return warning


def job_needs_ai_call(job):
    """Return True only when a GPT call would add new analysis."""
    return needs_ai_analysis(job)


def analysis_list(result, *keys):
    """Return the first matching list field from an AI result."""
    for key in keys:
        value = result.get(key)
        if isinstance(value, list):
            return value
    return []


# ---------------------------------------------------------------------------
# UI sections
# ---------------------------------------------------------------------------

def format_fit_score(value):
    """Show a 0-100 score, or Not available when the value is missing/null."""
    if value is None or value == "":
        return "Not available"
    return str(value)


def has_fit_scores(data):
    """Return True when a result or job has the new dimension scores."""
    if not isinstance(data, dict):
        return False
    for key in ("overall_match_score", "experience_score", "leadership_score"):
        value = data.get(key)
        if value not in (None, ""):
            return True
    return False


def fit_score_source(result=None, job=None):
    """Prefer AI result scores, then saved job fields."""
    if has_fit_scores(result):
        return result
    if has_fit_scores(job):
        return job
    if isinstance(job, dict) and has_fit_scores(job.get("ai_analysis")):
        return job.get("ai_analysis")
    return None


def show_fit_scoring(data):
    """Show the compact Fit Scoring block used in Matcher and Pipeline."""
    if not has_fit_scores(data):
        return
    labels = [
        ("Overall Match", "overall_match_score", "overall_match_score_explanation"),
        ("Experience", "experience_score", "experience_score_explanation"),
        ("Industry", "industry_score", "industry_score_explanation"),
        ("Leadership", "leadership_score", "leadership_score_explanation"),
        ("Responsibilities", "responsibilities_score", "responsibilities_score_explanation"),
        ("Technical", "technical_score", "technical_score_explanation"),
        ("Company Stage", "company_stage_score", "company_stage_score_explanation"),
        ("Compensation", "compensation_score", "compensation_score_explanation"),
        ("Location", "location_score", "location_score_explanation"),
    ]
    st.subheader("Fit Scoring")
    rows = []
    for label, score_key, explanation_key in labels:
        rows.append(
            {
                "Dimension": label,
                "Score": format_fit_score(data.get(score_key)),
                "Why": data.get(explanation_key) or "",
            }
        )
    st.dataframe(rows, use_container_width=True, hide_index=True)


def show_analysis(result):
    """Display the AI analysis using Streamlit components."""
    score = result.get("overall_match_score")
    if score is None or score == "":
        score = result.get("match_score", "—")
    recommendation = result.get("recommendation", "—")
    st.subheader("Analysis")
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Match score", f"{score}%")
    with col2:
        st.metric("Recommendation", recommendation)
    with col3:
        st.metric("Blocker severity", result.get("blocker_severity", "None"))

    show_fit_scoring(result)

    st.markdown("**Leadership alignment**")
    st.write(result.get("leadership_alignment", ""))
    st.markdown("**Technical alignment**")
    st.write(result.get("technical_alignment", ""))
    st.markdown("**Industry/domain alignment**")
    st.write(result.get("industry_alignment", ""))
    st.markdown("**Blocker summary**")
    st.write(result.get("blocker_summary") or "None")

    st.markdown("**Strongest qualifications**")
    strengths = analysis_list(result, "demonstrated_strengths", "strongest_qualifications")
    if strengths:
        for item in strengths:
            st.write(f"- {item}")
    else:
        st.write("None")

    st.markdown("**Important gaps**")
    gaps = analysis_list(result, "likely_true_gaps", "important_gaps")
    if gaps:
        for item in gaps:
            st.write(f"- {item}")
    else:
        st.write("None")

    st.markdown("**Not demonstrated in profile**")
    missing = analysis_list(result, "not_demonstrated", "not_mentioned")
    if missing:
        for item in missing:
            st.write(f"- {item}")
    else:
        st.write("None")

    st.markdown("**Resume positioning**")
    resume_items = analysis_list(result, "resume_positioning")
    if resume_items:
        for item in resume_items:
            st.write(f"- {item}")
    else:
        st.write("None")

    st.markdown("**Interview preparation topics**")
    interview_items = analysis_list(result, "interview_prep", "interview_preparation")
    if interview_items:
        for item in interview_items:
            st.write(f"- {item}")
    else:
        st.write("None")

    explanation = result.get("overall_match_score_explanation") or result.get("score_explanation", "")
    if explanation:
        st.markdown("**Why this score**")
        st.write(explanation)


def render_job_matcher_tab():
    """Tab 1: paste a job, run AI analysis, optionally save it."""
    st.caption("Analyze one job against your candidate profile.")

    if "profile_text" not in st.session_state:
        st.session_state.profile_text = load_profile_text()

    profile_text = st.text_area(
        "Candidate profile",
        height=220,
        help="Loaded from private/profile.txt when that file exists. You can edit it here.",
        key="profile_text",
    )

    st.markdown("**Job details**")
    col1, col2 = st.columns(2)
    with col1:
        company = st.text_input("Company")
        location = st.text_input("Location")
        job_url = st.text_input("Job URL")
    with col2:
        job_title = st.text_input("Job title")
        source = st.text_input("Source", placeholder="Manual paste, LinkedIn, company site, ...")

    job_description = st.text_area("Job description", height=240)

    if st.button("Analyze Job", type="primary"):
        if profile_text.strip() == "" or job_description.strip() == "":
            st.error("Please provide both a candidate profile and a job description.")
            st.session_state.last_analysis = None
        else:
            job_text = job_description
            extra_lines = []
            if job_title.strip():
                extra_lines.append(f"Job title: {job_title.strip()}")
            if company.strip():
                extra_lines.append(f"Company: {company.strip()}")
            if location.strip():
                extra_lines.append(f"Location: {location.strip()}")
            if extra_lines:
                job_text = "\n".join(extra_lines) + "\n\n" + job_description

            with st.spinner("Analyzing job with OpenAI..."):
                result, error = analyze_with_ai(profile_text, job_text)
            if error:
                st.error(error)
                st.session_state.last_analysis = None
            else:
                st.session_state.last_analysis = {
                    "result": result,
                    "company": company,
                    "title": job_title,
                    "location": location,
                    "source": source,
                    "url": job_url,
                    "description": job_description,
                }

    last_analysis = st.session_state.get("last_analysis")
    if not last_analysis:
        return

    show_analysis(last_analysis["result"])

    if st.button("Save to Pipeline"):
        jobs, warning = load_jobs()
        if warning:
            st.warning(warning)
        if is_duplicate(
            jobs,
            last_analysis["company"],
            last_analysis["title"],
            last_analysis["url"],
        ):
            st.warning("This job already looks like it is in the pipeline (same company + title, or same URL).")
        else:
            jobs.append(
                make_job(
                    last_analysis["company"],
                    last_analysis["title"],
                    last_analysis["location"],
                    last_analysis["source"],
                    last_analysis["url"],
                    last_analysis["description"],
                    last_analysis["result"],
                )
            )
            save_jobs(jobs)
            st.success("Saved to the pipeline with status Discovered.")


def score_sort_value(job):
    """Turn match_score into a number for ranking. Missing scores sort last."""
    score = job.get("match_score")
    try:
        return int(score)
    except (TypeError, ValueError):
        return -1


def apply_filters(jobs, recommendation, status, blocker_severity):
    """Return jobs that match the selected pipeline filters."""
    filtered = []
    for job in jobs:
        if recommendation != "All" and job.get("recommendation") != recommendation:
            continue
        if status != "All" and job.get("status") != status:
            continue
        if blocker_severity != "All" and job.get("blocker_severity") != blocker_severity:
            continue
        filtered.append(job)
    return filtered


def replace_job(jobs, updated_job):
    """Replace one job in the list by id and save the file."""
    updated = []
    for job in jobs:
        if job.get("id") == updated_job.get("id"):
            updated.append(updated_job)
        else:
            updated.append(job)
    save_jobs(updated)
    return updated


def render_job_pipeline_tab():
    """Tab 2: rank, filter, and manage locally stored jobs."""
    st.info(
        "This table is your local application pipeline. "
        "Jobs are ranked by match score. "
        "Automated job discovery from LinkedIn, company sites, and other sources will be added later."
    )

    jobs, warning = load_jobs()
    if warning:
        st.warning(warning)

    if not jobs:
        st.write("No jobs in the pipeline yet. Analyze a job in the Job Matcher tab, then save it.")
        return

    jobs = sorted(jobs, key=score_sort_value, reverse=True)

    st.markdown("**Filters**")
    filter_col1, filter_col2, filter_col3 = st.columns(3)
    with filter_col1:
        recommendation_filter = st.selectbox(
            "Recommendation",
            ["All"] + RECOMMENDATIONS,
        )
    with filter_col2:
        status_filter = st.selectbox("Status", ["All"] + STATUSES)
    with filter_col3:
        blocker_filter = st.selectbox(
            "Blocker Severity",
            ["All"] + BLOCKER_SEVERITIES,
        )

    visible_jobs = apply_filters(
        jobs,
        recommendation_filter,
        status_filter,
        blocker_filter,
    )
    if not visible_jobs:
        st.write("No jobs match these filters.")
        return

    rows = []
    for job in visible_jobs:
        rows.append(
            {
                "Score": job.get("match_score", ""),
                "Recommendation": job.get("recommendation", ""),
                "Blocker Severity": job.get("blocker_severity", "None"),
                "Company": job.get("company", ""),
                "Title": job.get("title", ""),
                "Location": job.get("location", ""),
                "Source": job.get("source", ""),
                "Status": job.get("status", ""),
                "Date Found": job.get("date_found", ""),
            }
        )
    st.dataframe(rows, use_container_width=True, hide_index=True)

    st.markdown("**Job detail**")
    labels = [
        f"{job.get('match_score', '—')} | {job.get('company', '(no company)')} — {job.get('title', '(no title)')}"
        for job in visible_jobs
    ]
    selected_index = st.selectbox(
        "Select a job",
        range(len(visible_jobs)),
        format_func=lambda index: labels[index],
    )
    selected_job = dict(visible_jobs[selected_index])

    st.write(f"**Company:** {selected_job.get('company') or '—'}")
    st.write(f"**Title:** {selected_job.get('title') or '—'}")
    st.write(f"**Location:** {selected_job.get('location') or '—'}")
    st.write(f"**Source:** {selected_job.get('source') or '—'}")
    st.write(f"**URL:** {selected_job.get('url') or '—'}")
    st.write(f"**Date found:** {selected_job.get('date_found') or '—'}")
    st.write(f"**Date applied:** {selected_job.get('date_applied') or '—'}")
    st.write(f"**Match score:** {selected_job.get('match_score', '—')}")
    st.write(f"**Recommendation:** {selected_job.get('recommendation') or '—'}")
    st.write(f"**Blocker severity:** {selected_job.get('blocker_severity') or 'None'}")
    st.write(f"**Blocker summary:** {selected_job.get('blocker_summary') or 'None'}")
    show_fit_scoring(fit_score_source(job=selected_job))
    st.markdown("**Job description**")
    st.write(selected_job.get("description") or "No description saved.")

    current_status = selected_job.get("status", "Discovered")
    status_index = STATUSES.index(current_status) if current_status in STATUSES else 0
    job_id = selected_job.get("id", "")
    new_status = st.selectbox(
        "Current status",
        STATUSES,
        index=status_index,
        key=f"status_{job_id}",
    )
    notes_key = f"notes_{job_id}"
    if notes_key not in st.session_state:
        st.session_state[notes_key] = selected_job.get("notes", "")
    new_notes = st.text_area("Notes", height=140, key=notes_key)

    if st.button("Save job updates"):
        selected_job["status"] = new_status
        selected_job["notes"] = new_notes
        if new_status == "Applied" and not selected_job.get("date_applied"):
            selected_job["date_applied"] = date.today().isoformat()
        replace_job(jobs, selected_job)
        st.success("Job updated.")
        st.rerun()


def pre_score_sort_value(job):
    """Sort discovery rows by pre-score. Missing scores go last."""
    score = job.get("pre_score")
    try:
        return int(score)
    except (TypeError, ValueError):
        return -1


def analyze_discovery_job(job, profile_text):
    """Run GPT-5 mini on one job, or skip if analysis is already current."""
    if has_ai_analysis(job) and not job.get("analysis_stale"):
        return job, None, "skipped"
    job_text = job.get("description") or ""
    extra_lines = []
    if job.get("title"):
        extra_lines.append(f"Job title: {job.get('title')}")
    if job.get("company"):
        extra_lines.append(f"Company: {job.get('company')}")
    if job.get("location"):
        extra_lines.append(f"Location: {job.get('location')}")
    if extra_lines:
        job_text = "\n".join(extra_lines) + "\n\n" + job_text
    result, error = analyze_with_ai(profile_text, job_text)
    if error:
        return job, error, "error"
    updated = apply_ai_result(job, result)
    save_analyzed_job(updated)
    return updated, None, "analyzed"


def render_job_discovery_tab():
    """Tab 3: fetch jobs from a source, cheaply prefilter, then optionally call GPT."""
    st.caption(
        "Fetch jobs, filter them with a cheap local pre-score, and only then "
        "choose which ones to send to GPT-5 mini. Fetching never calls OpenAI."
    )
    st.write(
        f"AI analysis threshold is **{AI_ANALYSIS_THRESHOLD}**. "
        "Eligible jobs are not analyzed until you click a button."
    )

    sources = available_sources()
    source_names = [source.name for source in sources]
    selected_name = st.selectbox("Job source", source_names)
    selected_source = sources[source_names.index(selected_name)]
    fetch_ready = True

    if selected_source.source_id == "greenhouse":
        boards = configured_greenhouse_boards()
        if not boards:
            st.info(
                "No Greenhouse companies are configured. "
                "Add entries to GREENHOUSE_BOARDS in job_source_config.py, "
                "or approve a board in Source Discovery."
            )
            fetch_ready = False
        else:
            st.markdown("**Configured Greenhouse companies**")
            st.dataframe(
                [
                    {"Company": name, "Board identifier": token}
                    for name, token in boards.items()
                ],
                use_container_width=True,
                hide_index=True,
            )
            company_choices = ["All configured companies"] + list(boards.keys())
            company_choice = st.selectbox(
                "Fetch jobs for",
                company_choices,
                key="greenhouse_company",
            )
            if company_choice == "All configured companies":
                selected_source = GreenhouseJobSource(boards=boards)
            else:
                selected_source = GreenhouseJobSource(
                    boards={company_choice: boards[company_choice]}
                )

    elif selected_source.source_id == "lever":
        sites = configured_lever_sites()
        if not sites:
            st.info(
                "No Lever companies are configured. "
                "Add entries to LEVER_SITES in job_source_config.py, "
                "or approve a site in Source Discovery."
            )
            fetch_ready = False
        else:
            st.markdown("**Configured Lever companies**")
            st.dataframe(
                [
                    {"Company": name, "Site identifier": identifier}
                    for name, identifier in sites.items()
                ],
                use_container_width=True,
                hide_index=True,
            )
            company_choices = ["All configured companies"] + list(sites.keys())
            company_choice = st.selectbox(
                "Fetch jobs for",
                company_choices,
                key="lever_company",
            )
            if company_choice == "All configured companies":
                selected_source = LeverJobSource(sites=sites)
            else:
                selected_source = LeverJobSource(
                    sites={company_choice: sites[company_choice]}
                )

    elif selected_source.source_id == "all_live":
        boards = configured_greenhouse_boards()
        sites = configured_lever_sites()
        st.markdown("**Configured live sources**")
        st.write(f"**Greenhouse:** {len(boards)} configured companies")
        if boards:
            for name in boards:
                st.write(f"- {name}")
        st.write(f"**Lever:** {len(sites)} configured companies")
        if sites:
            for name in sites:
                st.write(f"- {name}")
        if not boards and not sites:
            st.info(
                "No live sources are configured. "
                "Add Greenhouse boards or Lever sites in job_source_config.py, "
                "or approve them in Source Discovery."
            )
            fetch_ready = False

    if st.button("Fetch Jobs", disabled=not fetch_ready):
        raw_jobs, fetch_error = selected_source.fetch()
        if fetch_error and not raw_jobs:
            st.warning(fetch_error)
            st.session_state.discovery_jobs = []
            st.session_state.discovery_stats = None
            st.session_state.discovery_source_counts = None
            st.session_state.discovery_partial = False
            st.session_state.discovery_fetch_mode = selected_source.source_id
        else:
            if fetch_error:
                st.warning(fetch_error)
            pipeline_jobs, _warning = load_jobs()
            profile_text = st.session_state.get("profile_text") or load_profile_text()
            default_source = selected_source.source_id
            if default_source == "all_live":
                # Combined fetches keep each job's original source (greenhouse/lever).
                default_source = ""
            discovery, pipeline_updates, stats = ingest_jobs(
                raw_jobs,
                pipeline_jobs,
                profile_text,
                default_source=default_source,
            )
            # Save only metadata fills on existing pipeline jobs. New jobs wait
            # until the user runs AI analysis (or they can stay in discovery).
            if pipeline_updates != pipeline_jobs:
                save_jobs(pipeline_updates)
            st.session_state.discovery_jobs = discovery
            st.session_state.discovery_stats = stats
            st.session_state.discovery_source_counts = getattr(
                selected_source, "last_source_counts", None
            )
            st.session_state.discovery_partial = bool(
                getattr(selected_source, "last_partial", False)
            )
            st.session_state.discovery_fetch_mode = selected_source.source_id
            if st.session_state.discovery_partial and not fetch_error:
                st.warning(
                    "Discovery results are partial because one source or company failed."
                )
            st.success(f"Fetched {stats['fetched']} job(s) from {selected_source.name}.")

    stats = st.session_state.get("discovery_stats")
    discovery_jobs = list(st.session_state.get("discovery_jobs") or [])
    source_counts = st.session_state.get("discovery_source_counts")
    if source_counts and st.session_state.get("discovery_fetch_mode") == "all_live":
        count1, count2, count3 = st.columns(3)
        count1.metric("Greenhouse fetched", source_counts.get("greenhouse", 0))
        count2.metric("Lever fetched", source_counts.get("lever", 0))
        count3.metric("Total fetched", (stats or {}).get("fetched", 0))
    if stats:
        metric1, metric2, metric3, metric4, metric5 = st.columns(5)
        metric1.metric("Fetched", stats.get("fetched", 0))
        metric2.metric("Duplicates", stats.get("duplicates", 0))
        metric3.metric("Rejected", stats.get("rejected", 0))
        metric4.metric("Passed hard filter", stats.get("passed", 0))
        metric5.metric("AI eligible", stats.get("ai_eligible", 0))

    if not discovery_jobs:
        if selected_source.source_id == "local_json":
            st.write("No discovered jobs yet. Add data/incoming_jobs.json and click Fetch Jobs.")
        elif selected_source.source_id == "greenhouse":
            st.write("No discovered jobs yet. Select a Greenhouse company and click Fetch Jobs.")
        elif selected_source.source_id == "lever":
            st.write("No discovered jobs yet. Select a Lever company and click Fetch Jobs.")
        elif selected_source.source_id == "all_live":
            st.write("No discovered jobs yet. Click Fetch Jobs to search all configured live sources.")
        else:
            st.write("No discovered jobs yet. Choose a source and click Fetch Jobs.")
        return

    discovery_jobs = sorted(discovery_jobs, key=pre_score_sort_value, reverse=True)
    rows = []
    for job in discovery_jobs:
        status_label = "Passed" if job.get("prefilter_passed") else "Rejected"
        if job.get("analysis_stale"):
            status_label = "Stale analysis"
        rows.append(
            {
                "Pre-score": job.get("pre_score", ""),
                "AI Eligible": "Yes" if job.get("ai_eligible") else "No",
                "Company": job.get("company", ""),
                "Title": job.get("title", ""),
                "Location": job.get("location", ""),
                "Remote Type": job.get("remote_type", ""),
                "Source / Sources": format_source_label(job),
                "Date Posted": job.get("date_posted", ""),
                "Prefilter Status": status_label,
                "Rejection Reason": job.get("rejection_reason", ""),
            }
        )
    st.dataframe(rows, use_container_width=True, hide_index=True)

    st.markdown("**Prefilter reasons for a selected job**")
    labels = [
        f"{job.get('pre_score', '—')} | {job.get('company', '(no company)')} — {job.get('title', '(no title)')}"
        for job in discovery_jobs
    ]
    selected_index = st.selectbox(
        "Select a discovered job",
        range(len(discovery_jobs)),
        format_func=lambda index: labels[index],
        key="discovery_select",
    )
    selected_job = dict(discovery_jobs[selected_index])
    reasons = selected_job.get("prefilter_reasons") or []
    if reasons:
        for reason in reasons:
            st.write(f"- {reason}")
    else:
        st.write("No prefilter reasons stored.")
    if selected_job.get("analysis_stale"):
        st.warning("The job description changed since the last AI analysis. Re-run analysis only if you want another API call.")

    profile_text = st.session_state.get("profile_text") or load_profile_text()
    if st.button("Analyze selected job"):
        if not job_needs_ai_call(selected_job) and has_ai_analysis(selected_job):
            st.info("This job already has current AI analysis. No API call was made.")
        elif not selected_job.get("ai_eligible") and not selected_job.get("analysis_stale"):
            st.warning("This job is not eligible for AI analysis (hard filter failed or pre-score is below the threshold).")
        else:
            with st.spinner("Analyzing selected job with OpenAI..."):
                updated, error, status = analyze_discovery_job(selected_job, profile_text)
            if error:
                st.error(error)
            elif status == "skipped":
                st.info("Skipped because current AI analysis already exists.")
            else:
                discovery_jobs[selected_index] = updated
                st.session_state.discovery_jobs = discovery_jobs
                st.success("Analysis saved to the pipeline.")
                show_analysis(updated.get("ai_analysis") or {})

    eligible_jobs = [job for job in discovery_jobs if job_needs_ai_call(job)]
    st.markdown("**Batch AI analysis**")
    st.write(
        f"{len(eligible_jobs)} jobs still need AI analysis. "
        f"This action will make up to {len(eligible_jobs)} OpenAI API calls."
    )
    confirmed = st.checkbox("I understand this will call OpenAI for each eligible job")
    if st.button("Analyze eligible jobs"):
        if not confirmed:
            st.warning("Check the confirmation box before running batch analysis.")
        elif not eligible_jobs:
            st.info("There are no eligible jobs that still need an AI call.")
        else:
            analyzed = 0
            skipped = 0
            failed = 0
            progress = st.progress(0)
            for index, job in enumerate(discovery_jobs):
                if not job_needs_ai_call(job):
                    continue
                updated, error, status = analyze_discovery_job(job, profile_text)
                discovery_jobs[index] = updated
                if error:
                    failed += 1
                    st.error(f"{job.get('title')}: {error}")
                elif status == "skipped":
                    skipped += 1
                else:
                    analyzed += 1
                progress.progress((index + 1) / max(len(eligible_jobs), 1))
            st.session_state.discovery_jobs = discovery_jobs
            st.success(f"Analyzed {analyzed} job(s). Skipped {skipped}. Failed {failed}.")


def _source_discovery_sort_key(record):
    """Validated sources with likely relevant jobs come first."""
    status = record.get("validation_status")
    if status in ("Validated", "Approved"):
        status_rank = 0
    elif status == "Error":
        status_rank = 1
    else:
        status_rank = 2
    try:
        relevant = -int(record.get("likely_relevant_job_count") or 0)
    except (TypeError, ValueError):
        relevant = 0
    try:
        opened = -int(record.get("open_job_count") or 0)
    except (TypeError, ValueError):
        opened = 0
    return (status_rank, relevant, opened, record.get("company") or "")


def render_source_discovery_tab():
    """Tab 4: find and approve additional public Greenhouse and Lever sources."""
    st.caption(
        "Paste company names to check public Greenhouse and Lever job boards. "
        "Validated sources are not added to Job Discovery until you approve them. "
        "This tab never calls OpenAI."
    )

    if st.session_state.get("_fill_source_names"):
        st.session_state.source_discovery_names = st.session_state._fill_source_names
        st.session_state._fill_source_names = ""

    names_text = st.text_area(
        "Company names (one per line)",
        height=160,
        placeholder="Databricks\nGilead\nStripe\nAnthropic\nSnowflake",
        key="source_discovery_names",
    )
    fill_col, refresh_col = st.columns(2)
    with fill_col:
        if st.button("Use companies from pipeline and last fetch"):
            pipeline_jobs, _warning = load_jobs()
            discovery_jobs = st.session_state.get("discovery_jobs") or []
            names = companies_from_jobs(pipeline_jobs, discovery_jobs)
            st.session_state._fill_source_names = "\n".join(names)
            st.rerun()
    with refresh_col:
        refresh = st.checkbox(
            "Refresh cached validations",
            value=False,
            help="By default, a source validated in the last 24 hours is not probed again.",
        )

    if st.button("Discover / Validate Sources"):
        names = parse_company_names(names_text)
        if not names:
            st.warning("Paste at least one company name.")
        else:
            with st.spinner("Validating public Greenhouse and Lever endpoints..."):
                _run_records, _saved, messages = discover_and_validate_sources(
                    names,
                    refresh=refresh,
                )
            st.session_state.source_discovery_messages = messages
            apply_source_discovery_results(_run_records)
            st.success(f"Finished validating {len(names)} company name(s).")

    for message in st.session_state.get("source_discovery_messages") or []:
        st.write(f"- {message}")

    records = sorted(load_discovered_sources(), key=_source_discovery_sort_key)
    if not records:
        st.write("No discovered sources yet. Paste company names and click Discover / Validate Sources.")
        return

    rows = []
    for record in records:
        rows.append(
            {
                "Company": record.get("company") or "",
                "ATS": (record.get("ats_type") or "unknown").title()
                if record.get("ats_type") != "unknown"
                else "Unknown",
                "Identifier": record.get("identifier") or "—",
                "Validation Status": record.get("validation_status") or "",
                "Open Jobs": record.get("open_job_count", 0),
                "Likely Relevant Jobs": record.get("likely_relevant_job_count", 0),
                "Already Configured": "Yes" if record.get("already_configured") else "No",
                "Approved": "Yes" if record.get("approved") else "No",
            }
        )
    st.dataframe(rows, use_container_width=True, hide_index=True)

    st.markdown("**Review a source**")
    labels = [
        f"{record.get('company') or '(no company)'} | {record.get('ats_type') or 'unknown'} | "
        f"{record.get('identifier') or '—'} | {record.get('validation_status')}"
        for record in records
    ]
    selected_index = st.selectbox(
        "Select a discovered source",
        range(len(records)),
        format_func=lambda index: labels[index],
        key="source_discovery_select",
    )
    selected = records[selected_index]
    st.write(f"**Board name:** {selected.get('board_name') or '—'}")
    if selected.get("error_message"):
        st.write(f"**Detail:** {selected.get('error_message')}")
    health = source_health(selected.get("ats_type"), selected.get("identifier"))
    if health.get("current_status"):
        st.write(
            f"**Source health:** {health.get('current_status')} "
            f"(last validation {health.get('last_validation') or '—'}; "
            f"last successful fetch {health.get('last_successful_fetch') or '—'})"
        )

    note_text = st.text_input("Note", value=selected.get("notes") or "", key="source_discovery_note")
    note_col, approve_col, ignore_col = st.columns(3)
    with note_col:
        if st.button("Save note"):
            _updated, error = set_record_notes(selected.get("id"), note_text)
            if error:
                st.warning(error)
            else:
                st.success("Note saved.")
                st.rerun()
    with approve_col:
        if st.button("Approve"):
            _updated, message = approve_discovered_source(selected.get("id"))
            if _updated is None:
                st.warning(message)
            else:
                apply_source_discovery_results([_updated])
                st.success(message)
                st.rerun()
    with ignore_col:
        if st.button("Ignore"):
            _updated, error = ignore_source(selected.get("id"))
            if error:
                st.warning(error)
            else:
                st.success("Source ignored. It was not added to Job Discovery.")
                st.rerun()

    approved = load_approved_maps()
    st.markdown("**Approved source registry**")
    st.write(
        "These sources are stored in data/approved_sources.json and are included "
        "in Job Discovery / All Live Sources without editing Python config."
    )
    st.write(f"Greenhouse approved: {len(approved.get('greenhouse') or {})}")
    for name, token in (approved.get("greenhouse") or {}).items():
        st.write(f"- {name} (`{token}`)")
    st.write(f"Lever approved: {len(approved.get('lever') or {})}")
    for name, token in (approved.get("lever") or {}).items():
        st.write(f"- {name} (`{token}`)")


def render_company_universe_tab():
    """Tab 5: collect potential employers and send them to Source Discovery."""
    st.caption(
        "Company Universe stores potential employers and sends selected names "
        "to existing Source Discovery validation. It never calls OpenAI and "
        "never auto-approves ATS boards."
    )

    records = sort_companies(load_company_universe())
    summary = universe_summary(records)
    metric1, metric2, metric3, metric4, metric5, metric6 = st.columns(6)
    metric1.metric("Total companies", summary["total"])
    metric2.metric("High priority", summary["high_priority"])
    metric3.metric("ATS validated", summary["ats_validated"])
    metric4.metric("Active job sources", summary["active_sources"])
    metric5.metric("Likely relevant jobs", summary["likely_relevant"])
    metric6.metric("Ignored", summary["ignored"])

    st.markdown("**Initialize and import**")
    init_col, pipeline_col, discovery_col = st.columns(3)
    with init_col:
        if st.button("Initialize from existing Operator data"):
            pipeline_jobs, _warning = load_jobs()
            discovery_jobs = st.session_state.get("discovery_jobs") or []
            _companies, stats = initialize_from_operator_data(
                pipeline_jobs=pipeline_jobs,
                discovery_jobs=discovery_jobs,
            )
            st.success(
                f"Added {stats['added']}, merged {stats['merged']}, "
                f"invalid {stats['invalid']}."
            )
            st.rerun()
    with pipeline_col:
        if st.button("Add companies from Job Pipeline"):
            pipeline_jobs, _warning = load_jobs()
            _added, stats = add_companies_from_jobs(pipeline_jobs, origin="Job Pipeline")
            st.success(f"Added {stats['added']}, merged {stats['merged']}.")
            st.rerun()
    with discovery_col:
        if st.button("Add companies from last Job Discovery"):
            discovery_jobs = st.session_state.get("discovery_jobs") or []
            _added, stats = add_companies_from_jobs(discovery_jobs, origin="Job Discovery")
            st.success(f"Added {stats['added']}, merged {stats['merged']}.")
            st.rerun()

    config_col, approved_col = st.columns(2)
    with config_col:
        if st.button("Add companies from configured Greenhouse/Lever sources"):
            from job_source_config import GREENHOUSE_BOARDS, LEVER_SITES

            _added, gh_stats = add_companies_from_source_map(
                GREENHOUSE_BOARDS, "greenhouse", "Existing Config"
            )
            _added, lever_stats = add_companies_from_source_map(
                LEVER_SITES, "lever", "Existing Config"
            )
            st.success(
                "Configured sources: "
                f"added {gh_stats['added'] + lever_stats['added']}, "
                f"merged {gh_stats['merged'] + lever_stats['merged']}."
            )
            st.rerun()
    with approved_col:
        if st.button("Add companies from approved sources"):
            approved = load_approved_maps()
            _added, gh_stats = add_companies_from_source_map(
                approved.get("greenhouse") or {}, "greenhouse", "Approved Source"
            )
            _added, lever_stats = add_companies_from_source_map(
                approved.get("lever") or {}, "lever", "Approved Source"
            )
            st.success(
                "Approved sources: "
                f"added {gh_stats['added'] + lever_stats['added']}, "
                f"merged {gh_stats['merged'] + lever_stats['merged']}."
            )
            st.rerun()

    st.markdown("**Add companies manually**")
    names_text = st.text_area(
        "Company names (one per line)",
        height=120,
        placeholder="Databricks\nGilead\nStripe",
        key="universe_manual_names",
    )
    man1, man2, man3, man4 = st.columns(4)
    with man1:
        manual_industry = st.text_input("Industry (optional)", key="universe_manual_industry")
    with man2:
        manual_website = st.text_input("Website (optional)", key="universe_manual_website")
    with man3:
        manual_priority = st.selectbox(
            "Priority",
            PRIORITIES,
            index=PRIORITIES.index("Medium"),
            key="universe_manual_priority",
        )
    with man4:
        manual_notes = st.text_input("Notes (optional)", key="universe_manual_notes")
    if st.button("Add companies"):
        from source_discovery import parse_company_names

        names = parse_company_names(names_text)
        if not names:
            st.warning("Paste at least one company name.")
        else:
            _added, stats = add_companies_from_names(
                names,
                origin="Manual",
                industry=manual_industry,
                website=manual_website,
                priority=manual_priority,
                notes=manual_notes,
            )
            st.success(f"Added {stats['added']}, merged {stats['merged']}.")
            st.rerun()

    uploaded = st.file_uploader("Import CSV or TXT company list", type=["csv", "txt"])
    if uploaded is not None and st.button("Import file"):
        text = uploaded.getvalue().decode("utf-8", errors="replace")
        _imported, stats = import_companies_from_text(text, filename=uploaded.name)
        st.success(
            f"Rows read {stats['rows_read']}. Added {stats['added']}, "
            f"merged {stats['merged']}, invalid {stats['invalid']}."
        )
        st.rerun()

    if not records:
        st.write(
            "No companies yet. Initialize from existing Operator data or paste names."
        )
        return

    st.markdown("**Filters**")
    industries = ["All"] + sorted(
        {
            record.get("industry") or "Unknown"
            for record in records
        }
    )
    ats_types = ["All"] + sorted(
        {
            (record.get("ats_type") or "unknown")
            for record in records
        }
    )
    ats_statuses = ["All", "Unknown", "Validated", "Invalid", "Error", "Already Configured", "Approved"]
    f1, f2, f3, f4 = st.columns(4)
    with f1:
        filter_priority = st.selectbox("Priority", ["All"] + list(PRIORITIES), key="universe_filter_priority")
    with f2:
        filter_industry = st.selectbox("Industry", industries, key="universe_filter_industry")
    with f3:
        filter_ats = st.selectbox("ATS type", ats_types, key="universe_filter_ats")
    with f4:
        filter_status = st.selectbox("ATS status", ats_statuses, key="universe_filter_status")
    f5, f6, f7 = st.columns(3)
    with f5:
        filter_bay = st.selectbox(
            "Bay Area presence",
            ["All", "Yes", "No", "Unknown"],
            key="universe_filter_bay",
        )
    with f6:
        filter_remote = st.selectbox(
            "Remote friendly",
            ["All", "Yes", "No", "Unknown"],
            key="universe_filter_remote",
        )
    with f7:
        filter_active = st.selectbox(
            "Active / ignored",
            ["All", "Active", "Ignored"],
            key="universe_filter_active",
        )

    visible = sort_companies(
        filter_companies(
            records,
            priority=filter_priority,
            industry=filter_industry,
            ats_type=filter_ats,
            ats_status=filter_status,
            bay_area=filter_bay,
            remote=filter_remote,
            active_state=filter_active,
        )
    )
    rows = []
    for record in visible:
        rows.append(
            {
                "Priority": record.get("priority") or "",
                "Company": record.get("company_name") or "",
                "Industry": record.get("industry") or "",
                "Headquarters": record.get("headquarters") or "",
                "Bay Area Presence": record.get("bay_area_presence") or "",
                "Remote Friendly": record.get("remote_friendly") or "",
                "Company Stage": record.get("company_stage") or "",
                "ATS": (record.get("ats_type") or "unknown").title()
                if record.get("ats_type")
                else "Unknown",
                "ATS Status": record.get("ats_status") or "Unknown",
                "Open Jobs": record.get("open_job_count", 0),
                "Likely Relevant Jobs": record.get("likely_relevant_job_count", 0),
                "Last Validated": record.get("last_source_validation") or "",
                "Active Source": "Yes" if record.get("active_source") else "No",
            }
        )
    st.dataframe(rows, use_container_width=True, hide_index=True)

    st.markdown("**Validate with Source Discovery**")
    name_options = [record.get("company_name") for record in visible if record.get("company_name")]
    selected_names = st.multiselect(
        "Selected companies",
        name_options,
        key="universe_selected_names",
    )
    refresh = st.checkbox(
        "Refresh cached validations",
        value=False,
        key="universe_refresh_cache",
        help="By default, a source validated in the last 24 hours is not probed again.",
    )
    high_unknown = companies_needing_ats_validation(records, high_priority_only=True)
    unknown = companies_needing_ats_validation(records, high_priority_only=False)
    st.write(
        f"{len(selected_names)} selected, {len(high_unknown)} high-priority unknown ATS, "
        f"and {len(unknown)} total unknown ATS companies would be checked against "
        "supported public ATS sources."
    )
    confirm_selected = True
    if len(selected_names) > LARGE_BATCH_THRESHOLD:
        confirm_selected = st.checkbox(
            f"I understand {len(selected_names)} selected companies will be checked.",
            key="universe_confirm_selected",
        )
    confirm_high = True
    if len(high_unknown) > LARGE_BATCH_THRESHOLD:
        confirm_high = st.checkbox(
            f"I understand {len(high_unknown)} high-priority companies will be checked.",
            key="universe_confirm_high",
        )
    confirm_unknown = True
    if len(unknown) > LARGE_BATCH_THRESHOLD:
        confirm_unknown = st.checkbox(
            f"I understand {len(unknown)} companies will be checked.",
            key="universe_confirm_unknown",
        )

    val1, val2, val3 = st.columns(3)
    with val1:
        if st.button("Validate selected companies"):
            if not selected_names:
                st.warning("Select at least one company.")
            elif not confirm_selected:
                st.warning("Confirm the large batch before validating.")
            else:
                with st.spinner("Validating public Greenhouse and Lever endpoints..."):
                    _run_records, messages = validate_universe_companies(
                        selected_names, refresh=refresh
                    )
                st.session_state.universe_validation_messages = messages
                st.success(f"Finished validating {len(selected_names)} company name(s).")
                st.rerun()
    with val2:
        if st.button("Validate all High priority companies with unknown ATS"):
            if not high_unknown:
                st.info("No high-priority companies still have an unknown ATS.")
            elif not confirm_high:
                st.warning("Confirm the large batch before validating.")
            else:
                with st.spinner("Validating public Greenhouse and Lever endpoints..."):
                    _run_records, messages = validate_universe_companies(
                        high_unknown, refresh=refresh
                    )
                st.session_state.universe_validation_messages = messages
                st.success(f"Finished validating {len(high_unknown)} company name(s).")
                st.rerun()
    with val3:
        if st.button("Validate all companies with unknown ATS"):
            if not unknown:
                st.info("No companies still have an unknown ATS.")
            elif not confirm_unknown:
                st.warning("Confirm the large batch before validating.")
            else:
                with st.spinner("Validating public Greenhouse and Lever endpoints..."):
                    _run_records, messages = validate_universe_companies(
                        unknown, refresh=refresh
                    )
                st.session_state.universe_validation_messages = messages
                st.success(f"Finished validating {len(unknown)} company name(s).")
                st.rerun()

    for message in st.session_state.get("universe_validation_messages") or []:
        st.write(f"- {message}")

    if not visible:
        st.write("No companies match the current filters.")
        return

    st.markdown("**Company detail**")
    labels = [
        f"{record.get('priority') or '—'} | {record.get('company_name') or '(no name)'} | "
        f"{record.get('ats_status') or 'Unknown'}"
        for record in visible
    ]
    selected_index = st.selectbox(
        "Select a company",
        range(len(visible)),
        format_func=lambda index: labels[index],
        key="universe_select",
    )
    selected = visible[selected_index]
    st.write(f"**Company name:** {selected.get('company_name') or '—'}")
    st.write(f"**Website:** {selected.get('website') or '—'}")
    st.write(f"**Industry:** {selected.get('industry') or '—'}")
    st.write(f"**Headquarters:** {selected.get('headquarters') or '—'}")
    st.write(f"**Priority:** {selected.get('priority') or '—'}")
    st.write(f"**Notes:** {selected.get('notes') or '—'}")
    st.write(f"**ATS type:** {selected.get('ats_type') or 'unknown'}")
    st.write(f"**ATS identifier:** {selected.get('ats_identifier') or '—'}")
    st.write(f"**Source status:** {selected.get('ats_status') or 'Unknown'}")
    st.write(f"**Source validation:** {selected.get('source_validation_status') or '—'}")
    st.write(f"**Open jobs:** {selected.get('open_job_count', 0)}")
    st.write(f"**Likely relevant jobs:** {selected.get('likely_relevant_job_count', 0)}")
    st.write(f"**Last validation:** {selected.get('last_source_validation') or '—'}")
    st.write(f"**Active source:** {'Yes' if selected.get('active_source') else 'No'}")
    st.write(f"**Origins:** {', '.join(selected.get('source_origin') or []) or '—'}")
    suggested = selected.get("suggested_priority") or "—"
    reasons = selected.get("suggested_priority_reasons") or []
    st.write(f"**Suggested priority:** {suggested}")
    for reason in reasons:
        st.write(f"- {reason}")

    pipeline_jobs, _warning = load_jobs()
    discovery_jobs = st.session_state.get("discovery_jobs") or []
    linked = jobs_for_company(selected, [pipeline_jobs, discovery_jobs])
    if linked:
        st.write(f"**Linked jobs:** {len(linked)}")
        for job in linked[:8]:
            eligible = "AI eligible" if job.get("ai_eligible") else "not AI eligible"
            st.write(
                f"- {job.get('title') or '(no title)'} | {job.get('status') or '—'} | {eligible}"
            )

    st.markdown("**Edit company**")
    edit1, edit2, edit3 = st.columns(3)
    with edit1:
        new_priority = st.selectbox(
            "Priority",
            PRIORITIES,
            index=PRIORITIES.index(selected.get("priority")) if selected.get("priority") in PRIORITIES else 1,
            key=f"universe_edit_priority_{selected.get('id')}",
        )
        new_industry = st.text_input(
            "Industry",
            value=selected.get("industry") or "",
            key=f"universe_edit_industry_{selected.get('id')}",
        )
        new_website = st.text_input(
            "Website",
            value=selected.get("website") or "",
            key=f"universe_edit_website_{selected.get('id')}",
        )
    with edit2:
        new_hq = st.text_input(
            "Headquarters",
            value=selected.get("headquarters") or "",
            key=f"universe_edit_hq_{selected.get('id')}",
        )
        bay_options = ["", "Yes", "No", "Unknown"]
        bay_value = selected.get("bay_area_presence") or ""
        new_bay = st.selectbox(
            "Bay Area presence",
            bay_options,
            index=bay_options.index(bay_value) if bay_value in bay_options else 0,
            key=f"universe_edit_bay_{selected.get('id')}",
        )
        remote_options = ["", "Yes", "No", "Unknown"]
        remote_value = selected.get("remote_friendly") or ""
        new_remote = st.selectbox(
            "Remote friendly",
            remote_options,
            index=remote_options.index(remote_value) if remote_value in remote_options else 0,
            key=f"universe_edit_remote_{selected.get('id')}",
        )
    with edit3:
        new_stage = st.text_input(
            "Company stage",
            value=selected.get("company_stage") or "",
            key=f"universe_edit_stage_{selected.get('id')}",
        )
        new_notes = st.text_area(
            "Notes",
            value=selected.get("notes") or "",
            key=f"universe_edit_notes_{selected.get('id')}",
        )
    if st.button("Save company updates"):
        _updated, error = update_company_fields(
            selected.get("id"),
            {
                "priority": new_priority,
                "industry": new_industry,
                "website": new_website,
                "headquarters": new_hq,
                "bay_area_presence": new_bay,
                "remote_friendly": new_remote,
                "company_stage": new_stage,
                "notes": new_notes,
            },
        )
        if error:
            st.warning(error)
        else:
            st.success("Company updated.")
            st.rerun()

    st.caption(
        "Preferred industries for suggested priority: "
        + ", ".join(PREFERRED_INDUSTRIES)
        + ". Suggested priority never overwrites the priority you set."
    )


def run_app():
    """Draw the Operator shell."""
    st.set_page_config(page_title="AI Job Operator", layout="wide")
    st.title("AI Job Operator")
    st.write(
        "A personal workspace to score jobs against your profile and track applications. "
        "Discovery uses a cheap local prefilter; GPT-5 mini runs only when you ask."
    )

    matcher_tab, pipeline_tab, discovery_tab, source_tab, universe_tab = st.tabs(
        ["Job Matcher", "Job Pipeline", "Job Discovery", "Source Discovery", "Company Universe"]
    )
    with matcher_tab:
        render_job_matcher_tab()
    with pipeline_tab:
        render_job_pipeline_tab()
    with discovery_tab:
        render_job_discovery_tab()
    with source_tab:
        render_source_discovery_tab()
    with universe_tab:
        render_company_universe_tab()


# Streamlit runs this file as __main__.
if __name__ == "__main__":
    run_app()
