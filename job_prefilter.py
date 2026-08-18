#!/usr/bin/env python3
"""Cheap, deterministic job prefilter.

This module never calls OpenAI. It scores jobs with transparent rules so
only promising roles become eligible for a GPT-5 mini analysis later.
"""

from job_sources import normalize_name, normalize_url

# Jobs at or above this pre-score may be sent to GPT, but only if the user asks.
AI_ANALYSIS_THRESHOLD = 60

# How the 100-point pre-score is split.
TITLE_POINTS = 30
FUNCTION_POINTS = 25
SENIORITY_POINTS = 20
SKILL_POINTS = 15
LOCATION_POINTS = 5
INDUSTRY_POINTS = 5

PRIMARY_TARGET_TITLES = [
    "vp of it",
    "head of it",
    "senior director of it",
    "director of it operations",
    "senior director of infrastructure and operations",
    "director of cloud operations",
    "director of platform operations",
    "director of technology",
    "director of enterprise technology",
]

# Strong nearby titles. Matching is based on normalized phrases, not only this list.
ADJACENT_TARGET_TITLES = [
    "vice president of information technology",
    "vp information technology",
    "vp of information technology",
    "vp infrastructure",
    "vp infrastructure and operations",
    "vp technology operations",
    "vp enterprise infrastructure",
    "vp enterprise technology",
    "head of information technology",
    "head of technology",
    "head of technology operations",
    "head of infrastructure",
    "senior director global it",
    "senior director it operations",
    "senior director technology operations",
    "senior director cloud infrastructure",
    "senior director infrastructure",
    "senior director infrastructure and operations",
    "director infrastructure and operations",
    "director technology operations",
    "director cloud infrastructure",
    "director cloud and platform",
    "director enterprise infrastructure",
    "director enterprise applications and infrastructure",
    "director technology enablement",
]

PREFERRED_FUNCTIONS = [
    "information technology",
    "enterprise it",
    "it operations",
    "technology operations",
    "infrastructure and operations",
    "infrastructure",
    "cloud infrastructure",
    "cloud operations",
    "platform operations",
    "devops",
    "sre",
    "site reliability",
    "enterprise technology",
    "technology enablement",
    "enterprise architecture",
    "corporate technology",
    "digital infrastructure",
    "enterprise applications",
]

# Title phrases that usually mean the role *owns* an unrelated function.
MISMATCH_OWNER_PHRASES = [
    "human resources",
    "people operations",
    "people ops",
    "chief people",
    "vp of people",
    "head of people",
    "talent acquisition",
    "recruiting",
    "recruiter",
    "sales",
    "account executive",
    "marketing",
    "brand ",
    "general counsel",
    "attorney",
    "legal counsel",
    "cfo",
    "controller",
    "accountant",
    "accounting",
    "finance director",
    "vp of finance",
    "nurse",
    "physician",
    "clinician",
    "surgeon",
    "product manager",
    "product management",
    "product owner",
    "software engineer",
    "staff engineer",
    "principal engineer",
    "data scientist",
    "machine learning engineer",
    "ml engineer",
]

PREFERRED_SENIORITY = [
    "vice president",
    "vp",
    "head",
    "senior director",
    "director",
]

REJECT_SENIORITY = [
    "intern",
    "internship",
    "junior",
    "coordinator",
    "administrator",
    "analyst",
    "supervisor",
    "senior manager",
    "staff engineer",
    "principal engineer",
    "software engineer",
    "data scientist",
]

PREFERRED_LOCATIONS = [
    "remote",
    "san jose",
    "san francisco",
    "bay area",
    "peninsula",
    "palo alto",
    "mountain view",
    "sunnyvale",
    "santa clara",
    "redwood city",
    "hybrid",
]

INDUSTRY_SIGNALS = [
    "biotech",
    "biopharma",
    "pharma",
    "pharmaceutical",
    "healthcare",
    "medical device",
    "life sciences",
    "regulated",
    "sox",
    "hipaa",
    "pci",
    "soc 2",
    "soc2",
]

CANDIDATE_SIGNALS = [
    "aws",
    "azure",
    "cloud",
    "infrastructure",
    "devops",
    "sre",
    "kubernetes",
    "eks",
    "ecs",
    "terraform",
    "iac",
    "cybersecurity",
    "it operations",
    "itsm",
    "identity",
    "iam",
    "enterprise applications",
    "vendor management",
    "msp",
    "finops",
    "m&a",
    "compliance",
    "sox",
    "hipaa",
    "pci",
    "soc 2",
    "leadership",
    "managing managers",
    "global teams",
]


def normalize_title(title):
    """Lowercase a title and standardize common executive spellings."""
    text = (title or "").lower()
    text = text.replace("&", " and ")
    text = text.replace(",", " ")
    text = text.replace("-", " ")
    text = text.replace("/", " ")
    cleaned = ""
    for character in text:
        if character.isalnum() or character.isspace():
            cleaned += character
        else:
            cleaned += " "
    text = " ".join(cleaned.split())
    text = text.replace("vice president", "vp")
    text = text.replace("information technology", "it")
    text = text.replace("sr ", "senior ")
    text = text.replace("sr. ", "senior ")
    return text


def contains_phrase(text, phrase):
    """Return True when phrase appears as whole words inside text."""
    haystack = f" {text} "
    needle = f" {phrase} "
    return needle in haystack


def any_phrase(text, phrases):
    """Return the first matching phrase, or None."""
    for phrase in phrases:
        if contains_phrase(text, phrase):
            return phrase
    return None


def title_relevance(title):
    """Score how close the title is to the primary target roles (0-30)."""
    normalized = normalize_title(title)
    if not normalized:
        return 0, "No title provided"
    for phrase in PRIMARY_TARGET_TITLES:
        if contains_phrase(normalized, phrase):
            return TITLE_POINTS, f"Strong primary title match ({phrase})"
    for phrase in ADJACENT_TARGET_TITLES:
        if contains_phrase(normalized, phrase):
            return 27, f"Strong adjacent title match ({phrase})"
    has_function = any_phrase(normalized, PREFERRED_FUNCTIONS) or any_phrase(
        normalized,
        ["it", "technology", "infrastructure", "cloud", "platform", "devops"],
    )
    has_seniority = any_phrase(normalized, ["vp", "head", "senior director", "director"])
    if has_function and has_seniority:
        return 24, f"Title has preferred seniority and a technology-operations function"
    if has_seniority:
        return 12, "Title has preferred seniority but a weaker function match"
    return 4, "Title is not close to the target IT/operations leadership roles"


def seniority_alignment(title):
    """Score seniority (0-20) and optionally reject obvious IC/junior roles."""
    normalized = normalize_title(title)
    if any_phrase(normalized, ["intern", "internship", "junior"]):
        return 0, "Rejected: internship or junior seniority", True, "Internship or junior role"
    if any_phrase(normalized, ["staff engineer", "principal engineer", "software engineer", "data scientist", "ml engineer"]):
        if not any_phrase(normalized, ["vp", "head", "director"]):
            return 0, "Rejected: individual-contributor engineering role", True, "Individual-contributor engineering role"
    if contains_phrase(normalized, "senior manager") or (
        contains_phrase(normalized, "manager")
        and not any_phrase(normalized, ["vp", "head", "director"])
    ):
        return 0, "Rejected: manager-level seniority", True, "Manager-level role, not Director/Head/VP"
    if any_phrase(normalized, ["coordinator", "administrator", "supervisor", "analyst"]):
        if not any_phrase(normalized, ["vp", "head", "director"]):
            return 0, "Rejected: coordinator/administrator/analyst seniority", True, "Below Director-level seniority"
    if any_phrase(normalized, ["vp", "vice president"]):
        return SENIORITY_POINTS, "VP / Vice President seniority", False, ""
    if contains_phrase(normalized, "senior director") or contains_phrase(normalized, "head"):
        return 18, "Senior Director or Head seniority", False, ""
    if contains_phrase(normalized, "director"):
        return 16, "Director seniority", False, ""
    # "Lead" is ambiguous. Do not reject only because it appears.
    if contains_phrase(normalized, "lead"):
        if any_phrase(normalized, ["technology operations", "it operations", "infrastructure", "cloud", "platform"]):
            return 12, "Operations Lead title; seniority is plausible but not clearly Director/VP", False, ""
        return 6, "Lead title without a clear Director/VP signal", False, ""
    return 4, "Seniority is unclear", False, ""


def functional_mismatch(title, description):
    """Reject when the TITLE shows the role owns an unrelated function."""
    normalized_title = normalize_title(title)
    # Preferred tech-ops words in the title keep the job even if the JD
    # mentions HR, finance, or sales as partners.
    if any_phrase(normalized_title, PREFERRED_FUNCTIONS) or any_phrase(
        normalized_title,
        ["it", "infrastructure", "cloud", "platform", "devops", "sre", "technology operations"],
    ):
        return False, ""
    mismatch = any_phrase(normalized_title, MISMATCH_OWNER_PHRASES)
    if mismatch:
        return True, f"Role appears to own an unrelated function ({mismatch})"
    if contains_phrase(normalized_title, "people") and not any_phrase(normalized_title, ["it", "technology", "infrastructure"]):
        return True, "Role appears to own People/HR rather than technology operations"
    return False, ""


def functional_relevance(title, description):
    """Score how well the role's owned function matches IT/infra/cloud ops (0-25)."""
    title_text = normalize_title(title)
    body = f"{title_text} {(description or '').lower()}"
    title_hit = any_phrase(title_text, PREFERRED_FUNCTIONS) or any_phrase(
        title_text,
        ["it", "infrastructure", "cloud", "platform", "devops", "sre"],
    )
    body_hit = any_phrase(body, PREFERRED_FUNCTIONS)
    if title_hit and body_hit:
        return FUNCTION_POINTS, f"Strong function match in title and description ({title_hit})"
    if title_hit:
        return 22, f"Function match in the title ({title_hit})"
    if body_hit:
        return 14, f"Function match mainly in the description ({body_hit})"
    return 4, "Little IT/infrastructure/operations function signal"


def location_relevance(location, remote_type, description):
    """Score location/work arrangement (0-5). Does not auto-reject other cities."""
    blob = " ".join([location or "", remote_type or "", description or ""]).lower()
    for phrase in PREFERRED_LOCATIONS:
        if phrase in blob:
            return LOCATION_POINTS, f"Location/work arrangement fits preferences ({phrase})"
    if not (location or remote_type):
        return 2, "Location was not provided"
    return 1, "Location is outside the listed Bay Area/remote preferences"


def industry_relevance(description, profile_text):
    """Score cheap industry/domain overlap (0-5)."""
    blob = f"{description or ''} {profile_text or ''}".lower()
    hit = any_phrase(blob, INDUSTRY_SIGNALS)
    if hit:
        return INDUSTRY_POINTS, f"Industry/domain signal present ({hit})"
    return 2, "No strong listed industry signal"


def skill_overlap(description, title, profile_text):
    """Score cheap keyword overlap between the job and the candidate profile (0-15)."""
    job_blob = f"{title or ''} {description or ''}".lower()
    profile_blob = (profile_text or "").lower()
    overlap = []
    for signal in CANDIDATE_SIGNALS:
        if signal in job_blob and signal in profile_blob:
            overlap.append(signal)
        elif signal in job_blob and not profile_blob:
            overlap.append(signal)
    if not overlap:
        return 3, "Little keyword overlap with the candidate profile"
    ratio = min(len(overlap) / 6.0, 1.0)
    points = int(round(SKILL_POINTS * ratio))
    preview = ", ".join(overlap[:6])
    return points, f"Candidate skill overlap ({preview})"


def prefilter_job(job, profile_text=""):
    """Apply hard filters and a 0-100 pre-score. Never calls OpenAI."""
    title = job.get("title") or ""
    description = job.get("description") or ""
    location = job.get("location") or ""
    remote_type = job.get("remote_type") or ""
    reasons = []

    mismatch, mismatch_reason = functional_mismatch(title, description)
    if mismatch:
        job["pre_score"] = 0
        job["prefilter_passed"] = False
        job["prefilter_reasons"] = [mismatch_reason]
        job["rejection_reason"] = mismatch_reason
        job["ai_eligible"] = False
        return job

    seniority_points, seniority_reason, rejected, reject_reason = seniority_alignment(title)
    if rejected:
        job["pre_score"] = 0
        job["prefilter_passed"] = False
        job["prefilter_reasons"] = [reject_reason]
        job["rejection_reason"] = reject_reason
        job["ai_eligible"] = False
        return job

    title_points, title_reason = title_relevance(title)
    function_points, function_reason = functional_relevance(title, description)
    skill_points, skill_reason = skill_overlap(description, title, profile_text)
    location_points, location_reason = location_relevance(location, remote_type, description)
    industry_points, industry_reason = industry_relevance(description, profile_text)

    score = (
        title_points
        + function_points
        + seniority_points
        + skill_points
        + location_points
        + industry_points
    )
    score = max(0, min(100, score))
    reasons = [
        f"Title {title_points}/{TITLE_POINTS}: {title_reason}",
        f"Function {function_points}/{FUNCTION_POINTS}: {function_reason}",
        f"Seniority {seniority_points}/{SENIORITY_POINTS}: {seniority_reason}",
        f"Skills {skill_points}/{SKILL_POINTS}: {skill_reason}",
        f"Location {location_points}/{LOCATION_POINTS}: {location_reason}",
        f"Industry {industry_points}/{INDUSTRY_POINTS}: {industry_reason}",
    ]
    job["pre_score"] = score
    job["prefilter_passed"] = True
    job["prefilter_reasons"] = reasons
    job["rejection_reason"] = ""
    job["ai_eligible"] = score >= AI_ANALYSIS_THRESHOLD
    return job


def find_duplicate(existing_jobs, incoming_job):
    """Find a likely duplicate using URL, then source+external_id, then company+title+location."""
    incoming_url = normalize_url(incoming_job.get("url"))
    incoming_source = normalize_name(incoming_job.get("source"))
    incoming_external = normalize_name(incoming_job.get("external_id"))
    incoming_company = normalize_name(incoming_job.get("company"))
    incoming_title = normalize_title(incoming_job.get("title"))
    incoming_location = normalize_name(incoming_job.get("location"))

    if incoming_url:
        for job in existing_jobs:
            if incoming_url == normalize_url(job.get("url")):
                return job
    if incoming_source and incoming_external:
        for job in existing_jobs:
            if (
                incoming_source == normalize_name(job.get("source"))
                and incoming_external == normalize_name(job.get("external_id"))
            ):
                return job
    if incoming_company and incoming_title:
        for job in existing_jobs:
            same_company = incoming_company == normalize_name(job.get("company"))
            same_title = incoming_title == normalize_title(job.get("title"))
            existing_location = normalize_name(job.get("location"))
            if incoming_location and existing_location:
                same_location = incoming_location == existing_location
            else:
                same_location = True
            if same_company and same_title and same_location:
                return job
    return None


def has_ai_analysis(job):
    """Return True when a saved GPT analysis already exists."""
    if job.get("ai_analysis"):
        return True
    score = job.get("match_score")
    if score in ("", None):
        return False
    return True


def descriptions_match(left, right):
    """Compare two descriptions after simple whitespace normalization."""
    return " ".join((left or "").split()).lower() == " ".join((right or "").split()).lower()


def merge_duplicate(existing, incoming):
    """Fill missing metadata from a new source without wiping saved analysis or status."""
    merged = dict(existing)
    fillable = [
        "external_id",
        "company",
        "title",
        "location",
        "remote_type",
        "employment_type",
        "salary_min",
        "salary_max",
        "salary_text",
        "source",
        "url",
        "date_posted",
    ]
    for key in fillable:
        if not merged.get(key) and incoming.get(key):
            merged[key] = incoming[key]
    if incoming.get("raw_source_data") and not merged.get("raw_source_data"):
        merged["raw_source_data"] = incoming.get("raw_source_data")

    old_description = existing.get("description") or ""
    new_description = incoming.get("description") or ""
    if new_description and not old_description:
        merged["description"] = new_description
    elif new_description and old_description and not descriptions_match(old_description, new_description):
        merged["description"] = new_description
        if has_ai_analysis(existing):
            merged["analysis_stale"] = True

    # Keep cheap prefilter numbers current; never copy over GPT results.
    for key in ("pre_score", "prefilter_passed", "prefilter_reasons", "rejection_reason"):
        merged[key] = incoming.get(key, merged.get(key))
    if has_ai_analysis(merged) and not merged.get("analysis_stale"):
        merged["ai_eligible"] = False
    else:
        merged["ai_eligible"] = incoming.get("ai_eligible", False)
    return merged


def ingest_jobs(raw_jobs, existing_jobs, profile_text, default_source=""):
    """Normalize, prefilter, and deduplicate jobs. Does not call OpenAI."""
    from job_sources import normalize_incoming_job

    stats = {
        "fetched": 0,
        "duplicates": 0,
        "rejected": 0,
        "passed": 0,
        "ai_eligible": 0,
    }
    discovery = []
    pipeline_updates = list(existing_jobs)

    for raw_job in raw_jobs:
        stats["fetched"] += 1
        job = normalize_incoming_job(raw_job, default_source=default_source)
        if job is None:
            continue
        job = prefilter_job(job, profile_text)
        duplicate = find_duplicate(pipeline_updates, job)
        if duplicate is not None:
            stats["duplicates"] += 1
            merged = merge_duplicate(duplicate, job)
            for index, current in enumerate(pipeline_updates):
                if current.get("id") == duplicate.get("id"):
                    pipeline_updates[index] = merged
                    break
            discovery.append(merged)
            if merged.get("ai_eligible"):
                stats["ai_eligible"] += 1
            continue
        discovery.append(job)
        if job.get("prefilter_passed"):
            stats["passed"] += 1
        else:
            stats["rejected"] += 1
        if job.get("ai_eligible"):
            stats["ai_eligible"] += 1

    return discovery, pipeline_updates, stats
