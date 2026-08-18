#!/usr/bin/env python3
"""Streamlit shell for a personal AI Job Operator.

This first version lets you analyze one job against a candidate profile
and save the result to a local pipeline. Job-source integrations come later.

Run with:
  source .venv/bin/activate
  streamlit run operator_app.py
"""

import json
import os
import uuid
from datetime import date

import streamlit as st

from job_matcher import BLOCKER_SEVERITIES, RECOMMENDATIONS, analyze_with_ai

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
        "company": raw_job.get("company") or "",
        "title": raw_job.get("title") or "",
        "location": raw_job.get("location") or "",
        "source": raw_job.get("source") or "",
        "url": raw_job.get("url") or "",
        "description": raw_job.get("description") or "",
        "date_found": raw_job.get("date_found") or "",
        "match_score": raw_job.get("match_score", ""),
        "recommendation": raw_job.get("recommendation") or "",
        "blocker_severity": raw_job.get("blocker_severity") or "None",
        "blocker_summary": raw_job.get("blocker_summary") or "",
        "status": raw_job.get("status") or "Discovered",
        "date_applied": raw_job.get("date_applied") or "",
        "notes": raw_job.get("notes") or "",
    }
    if job["blocker_severity"] not in BLOCKER_SEVERITIES:
        job["blocker_severity"] = "None"
    if job["status"] not in STATUSES:
        job["status"] = "Discovered"

    required_keys = list(job.keys())
    filled = False
    for key in required_keys:
        if key not in raw_job or raw_job.get(key) is None:
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
            "status": "Discovered",
            "date_applied": "",
            "notes": "",
        }
    )
    return job


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

def show_analysis(result):
    """Display the AI analysis using Streamlit components."""
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
    interview_items = analysis_list(result, "interview_prep")
    if interview_items:
        for item in interview_items:
            st.write(f"- {item}")
    else:
        st.write("None")

    explanation = result.get("score_explanation", "")
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


def run_app():
    """Draw the Operator shell."""
    st.set_page_config(page_title="AI Job Operator", layout="wide")
    st.title("AI Job Operator")
    st.write(
        "A personal workspace to score jobs against your profile and track applications. "
        "This version uses pasted job text. Multi-source job intake comes later."
    )

    matcher_tab, pipeline_tab = st.tabs(["Job Matcher", "Job Pipeline"])
    with matcher_tab:
        render_job_matcher_tab()
    with pipeline_tab:
        render_job_pipeline_tab()


# Streamlit runs this file as __main__.
if __name__ == "__main__":
    run_app()
