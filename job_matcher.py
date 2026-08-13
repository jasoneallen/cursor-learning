#!/usr/bin/env python3
"""A simple command-line job matcher.

Ask for a candidate profile and a job description, then compare them
using skill matching and optional AI analysis.

Setup for AI mode:
  python3 -m venv .venv
  source .venv/bin/activate
  pip install openai python-dotenv
  Add OPENAI_API_KEY to a local .env file (never commit that file).
"""

import json
import os

# Words that are too common or are generic recruiting language.
# These should not count as job requirements.
STOP_WORDS = [
    "a", "an", "and", "are", "as", "at", "be", "been", "being", "by",
    "can", "candidate", "candidates", "description", "director",
    "experience", "experienced", "for", "from", "have", "has", "had",
    "in", "including", "into", "is", "job", "join", "looking", "must",
    "of", "on", "or", "our", "please", "plus", "position", "preferred",
    "prefer", "proven", "qualification", "qualifications", "related",
    "require", "required", "requirements", "responsibilities", "role",
    "seek", "seeking", "should", "strong", "such", "summary", "team",
    "teams", "than", "that", "the", "their", "they", "this", "to",
    "understanding", "us", "use", "using", "was", "we", "well", "were",
    "which", "who", "will", "with", "work", "year", "years", "you",
    "your", "ability", "able", "about", "across", "all", "also", "any",
    "based", "both", "company", "etc", "knowledge", "like",
    "manager", "may", "more", "new", "other", "over", "through",
    "within",
]

# Multi-word technical terms. Longer phrases are matched first so
# "cloud infrastructure" stays together instead of becoming two words.
MULTI_WORD_SKILLS = [
    "artificial intelligence",
    "cloud infrastructure",
    "enterprise applications",
    "enterprise technology",
    "engineering teams",
    "google cloud",
    "it operations",
    "machine learning",
    "service management",
    "service now",
    "technology operations",
    "ci cd",
]

# Different ways of writing the same skill.
SKILL_ALIASES = {
    "ai": "artificial intelligence",
    "enterprise applications": "enterprise technology",
    "ml": "machine learning",
    "service now": "servicenow",
    "technology operations": "it operations",
}

# Skills that count toward the match score.
# The value is a category name, or None when the skill has no category.
SKILL_TO_CATEGORY = {
    # Cloud
    "aws": "Cloud",
    "azure": "Cloud",
    "gcp": "Cloud",
    "google cloud": "Cloud",
    "cloud": "Cloud",
    "cloud infrastructure": "Cloud",
    "finops": "Cloud",
    # Infrastructure
    "kubernetes": "Infrastructure",
    "infrastructure": "Infrastructure",
    "linux": "Infrastructure",
    "networking": "Infrastructure",
    # DevOps
    "devops": "DevOps",
    "terraform": "DevOps",
    "docker": "DevOps",
    "ansible": "DevOps",
    "jenkins": "DevOps",
    "ci cd": "DevOps",
    "cicd": "DevOps",
    # Security
    "cybersecurity": "Security",
    "security": "Security",
    "infosec": "Security",
    # IT Operations
    "it operations": "IT Operations",
    "servicenow": "IT Operations",
    "service management": "IT Operations",
    "enterprise technology": "IT Operations",
    "itil": "IT Operations",
    # Leadership
    "leadership": "Leadership",
    "leading": "Leadership",
    "leader": "Leadership",
    "executive": "Leadership",
    "engineering teams": "Leadership",
    # Known skills with no category
    "machine learning": None,
    "artificial intelligence": None,
}

# Display categories in this order.
CATEGORIES = [
    "Cloud",
    "Infrastructure",
    "DevOps",
    "Security",
    "IT Operations",
    "Leadership",
]

# Model used for AI analysis. Change this if you want a different OpenAI model.
AI_MODEL = "gpt-5-mini"

# Allowed recommendation labels from the AI.
RECOMMENDATIONS = [
    "Strong Apply",
    "Apply",
    "Possible Fit",
    "Weak Fit",
]

# JSON shape we ask the model to return. extra fields are not allowed.
AI_RESPONSE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "match_score": {
            "type": "integer",
            "description": "Overall match from 0 to 100.",
        },
        "recommendation": {
            "type": "string",
            "enum": RECOMMENDATIONS,
        },
        "leadership_alignment": {
            "type": "string",
            "description": "How well the candidate's leadership scope fits the role.",
        },
        "technical_alignment": {
            "type": "string",
            "description": "How well the candidate's technical background fits the role.",
        },
        "industry_alignment": {
            "type": "string",
            "description": "How well the candidate's industry or domain fits the role.",
        },
        "strongest_qualifications": {
            "type": "array",
            "items": {"type": "string"},
        },
        "important_gaps": {
            "type": "array",
            "items": {"type": "string"},
            "description": "True experience gaps, not things merely left unsaid.",
        },
        "not_mentioned": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Job items the profile does not mention. These are unknown, not proven gaps.",
        },
        "resume_positioning": {
            "type": "array",
            "items": {"type": "string"},
        },
        "interview_prep": {
            "type": "array",
            "items": {"type": "string"},
        },
        "score_explanation": {
            "type": "string",
            "description": "Short explanation of why this score was assigned.",
        },
    },
    "required": [
        "match_score",
        "recommendation",
        "leadership_alignment",
        "technical_alignment",
        "industry_alignment",
        "strongest_qualifications",
        "important_gaps",
        "not_mentioned",
        "resume_positioning",
        "interview_prep",
        "score_explanation",
    ],
}

# Instructions that tell the AI how to judge the match.
AI_INSTRUCTIONS = """
You are a careful job-match analyst for technology leaders.

Compare only the supplied candidate profile against the supplied job description.

Scoring rules:
- Return an overall match_score from 0 to 100.
- Recommendation must be exactly one of: Strong Apply, Apply, Possible Fit, Weak Fit.
- Do not assume the candidate has experience that is not in the profile.
- If the job asks for something the profile never mentions, put it in not_mentioned.
  Do not treat silence as proof the candidate lacks that skill.
- Put something in important_gaps only when the profile clearly shows a missing
  capability, a conflicting background, or a core requirement the profile cannot support.
- Do not penalize senior technology leaders too heavily for missing one or two
  individual tools. Closely related technologies can still show alignment.
- Give meaningful weight to leadership scope, organizational responsibility,
  transformation experience, and related platforms.
- Weight a missing niche tool much less than a missing leadership or domain fit.
- Be specific and practical in resume_positioning and interview_prep.
- Keep each written field concise.
""".strip()


def get_multiline_input(prompt):
    """Ask the user to paste or type text. A blank line means they are done."""
    print(prompt)
    print("(Paste or type your text. Press Enter on an empty line when done.)")
    lines = []
    while True:
        try:
            line = input()
        except EOFError:
            break
        if line.strip() == "":
            break
        lines.append(line)
    return " ".join(lines)


def clean_text(text):
    """Make text lowercase and replace punctuation with spaces."""
    text = text.lower()
    cleaned = ""
    for character in text:
        # Treat letters, numbers, and spaces as useful. Everything else
        # (commas, periods, hyphens, and so on) becomes a space.
        if character.isalnum() or character.isspace():
            cleaned += character
        else:
            cleaned += " "
    return cleaned


def phrase_sort_key(phrase):
    """Help sort phrases so longer ones are checked first."""
    words_in_phrase = phrase.split()
    return (len(words_in_phrase), len(phrase))


def get_phrase_list():
    """Return multi-word phrases, longest first, so matching stays accurate."""
    phrases = []
    for phrase in MULTI_WORD_SKILLS:
        phrases.append(phrase)
    for phrase in SKILL_ALIASES:
        if phrase not in phrases:
            phrases.append(phrase)

    # Sort by number of words, then by length, so longer phrases win.
    phrases.sort(key=phrase_sort_key, reverse=True)
    return phrases


def tokenize(text):
    """Split text into tokens, keeping multi-word skills together."""
    words = clean_text(text).split()
    phrases = get_phrase_list()
    tokens = []
    index = 0
    while index < len(words):
        matched_phrase = None
        for phrase in phrases:
            phrase_words = phrase.split()
            size = len(phrase_words)
            if words[index:index + size] == phrase_words:
                matched_phrase = phrase
                break
        if matched_phrase is not None:
            tokens.append(matched_phrase)
            index += len(matched_phrase.split())
        else:
            tokens.append(words[index])
            index += 1
    return tokens


def canonical_name(token):
    """Convert an alias to the standard skill name when one exists."""
    if token in SKILL_ALIASES:
        return SKILL_ALIASES[token]
    return token


def unique_in_order(items):
    """Return a new list with duplicates removed, keeping first appearances."""
    unique_items = []
    for item in items:
        if item not in unique_items:
            unique_items.append(item)
    return unique_items


def get_skills(text):
    """Return unique known skills found in the text."""
    skills = []
    for token in tokenize(text):
        skill = canonical_name(token)
        if skill in SKILL_TO_CATEGORY:
            skills.append(skill)
    return unique_in_order(skills)


def get_generic_keywords(text):
    """Return leftover words that are not stop words and not known skills."""
    keywords = []
    for token in tokenize(text):
        skill = canonical_name(token)
        if skill in SKILL_TO_CATEGORY:
            continue
        if skill in STOP_WORDS:
            continue
        keywords.append(skill)
    return unique_in_order(keywords)


def find_matched_and_missing(profile_items, job_items):
    """Compare job items to the profile and return two lists.

    matched: job items that also appear in the profile
    missing: job items that do not appear in the profile
    """
    matched = []
    missing = []
    for item in job_items:
        if item in profile_items:
            matched.append(item)
        else:
            missing.append(item)
    return matched, missing


def calculate_match_score(matched_skills, job_skills):
    """Return the percent of job skills found in the profile.

    The score uses known skills only, not generic recruiting language.
    If the job description has no skills, the score is 0.
    """
    if len(job_skills) == 0:
        return 0
    return round(len(matched_skills) / len(job_skills) * 100)


def get_matched_categories(matched_skills):
    """Return skill categories that have at least one matched skill."""
    matched_categories = []
    for category in CATEGORIES:
        for skill in matched_skills:
            if SKILL_TO_CATEGORY.get(skill) == category:
                if category not in matched_categories:
                    matched_categories.append(category)
                break
    return matched_categories


def format_keyword_list(items):
    """Turn a list of words or skills into a readable string."""
    if len(items) == 0:
        return "None"
    return ", ".join(items)


def display_results(
    score,
    matched_categories,
    matched_skills,
    missing_skills,
    missing_generic_keywords,
):
    """Print a formatted summary of the rules-based match results."""
    print()
    print("=" * 52)
    print("  Job Match Results")
    print("=" * 52)
    print(f"  Match score:          {score}%")
    print(f"  Matched categories:   {format_keyword_list(matched_categories)}")
    print(f"  Matched skills:       {format_keyword_list(matched_skills)}")
    print(f"  Missing skills:       {format_keyword_list(missing_skills)}")
    print(f"  Other missing words:  {format_keyword_list(missing_generic_keywords)}")
    print("=" * 52)


def run_rules_based_match(profile_text, job_text):
    """Run the original keyword matcher and print the results."""
    profile_skills = get_skills(profile_text)
    job_skills = get_skills(job_text)
    matched_skills, missing_skills = find_matched_and_missing(
        profile_skills,
        job_skills,
    )
    score = calculate_match_score(matched_skills, job_skills)
    matched_categories = get_matched_categories(matched_skills)

    profile_generic = get_generic_keywords(profile_text)
    job_generic = get_generic_keywords(job_text)
    _, missing_generic_keywords = find_matched_and_missing(
        profile_generic,
        job_generic,
    )

    display_results(
        score,
        matched_categories,
        matched_skills,
        missing_skills,
        missing_generic_keywords,
    )


def choose_mode():
    """Ask which matcher to run. Empty input or EOF keeps the rules-based mode."""
    print()
    print("3. Choose a matching mode:")
    print("   1 = Rules-based (keyword skills)")
    print("   2 = AI analysis (OpenAI)")
    print("   3 = Both")
    try:
        choice = input("Enter 1, 2, or 3 [1]: ").strip()
    except EOFError:
        choice = ""
    if choice not in ("1", "2", "3"):
        choice = "1"
    return choice


def get_openai_client():
    """Create an OpenAI client using OPENAI_API_KEY from the local .env file."""
    try:
        from dotenv import load_dotenv
        from openai import OpenAI
    except ImportError:
        print()
        print("AI mode needs these packages:")
        print("  pip install openai python-dotenv")
        return None

    # load_dotenv reads key=value pairs from .env in the current folder.
    load_dotenv()
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print()
        print("AI mode needs OPENAI_API_KEY in a local .env file.")
        print("Example line: OPENAI_API_KEY=sk-your-key")
        return None

    return OpenAI(api_key=api_key)


def parse_ai_json(text):
    """Turn the model text into a Python dictionary."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        lines = [line for line in lines if not line.startswith("```")]
        cleaned = "\n".join(lines)
    return json.loads(cleaned)


def request_ai_analysis(client, profile_text, job_text):
    """Call the OpenAI Responses API and return the parsed JSON result."""
    user_input = (
        "Candidate profile:\n"
        f"{profile_text}\n\n"
        "Job description:\n"
        f"{job_text}"
    )
    response = client.responses.create(
        model=AI_MODEL,
        instructions=AI_INSTRUCTIONS,
        input=user_input,
        text={
            "format": {
                "type": "json_schema",
                "name": "job_match_analysis",
                "schema": AI_RESPONSE_SCHEMA,
                "strict": True,
            }
        },
    )
    return parse_ai_json(response.output_text)


def print_ai_list(title, items):
    """Print a titled list, or None when the list is empty."""
    print(f"  {title}")
    if not items:
        print("    None")
        return
    for item in items:
        print(f"    - {item}")


def display_ai_results(result):
    """Print a formatted summary of the AI analysis."""
    score = result.get("match_score", 0)
    recommendation = result.get("recommendation", "Possible Fit")
    print()
    print("=" * 52)
    print("  AI Job Match Analysis")
    print("=" * 52)
    print(f"  Match score:        {score}%")
    print(f"  Recommendation:     {recommendation}")
    print()
    print(f"  Leadership:         {result.get('leadership_alignment', '')}")
    print(f"  Technical:          {result.get('technical_alignment', '')}")
    print(f"  Industry/domain:    {result.get('industry_alignment', '')}")
    print()
    print_ai_list("Strongest qualifications:", result.get("strongest_qualifications"))
    print_ai_list("Important gaps:", result.get("important_gaps"))
    print_ai_list("Not mentioned in profile:", result.get("not_mentioned"))
    print_ai_list("Resume positioning:", result.get("resume_positioning"))
    print_ai_list("Interview prep:", result.get("interview_prep"))
    print()
    print("  Why this score:")
    print(f"    {result.get('score_explanation', '')}")
    print("=" * 52)


def run_ai_match(profile_text, job_text):
    """Run the AI matcher and print the results."""
    client = get_openai_client()
    if client is None:
        return

    print()
    print("Asking OpenAI for an AI analysis...")
    try:
        result = request_ai_analysis(client, profile_text, job_text)
    except Exception as error:
        print()
        print("The OpenAI request failed.")
        print(f"  {error}")
        return

    display_ai_results(result)


def main():
    """Run the job matcher from start to finish."""
    print("Simple Job Matcher")
    print("Compare a candidate profile to a job description.")
    print()

    profile_text = get_multiline_input("1. Enter the candidate profile:")
    print()
    job_text = get_multiline_input("2. Enter the job description:")
    mode = choose_mode()

    if mode in ("1", "3"):
        run_rules_based_match(profile_text, job_text)
    if mode in ("2", "3"):
        run_ai_match(profile_text, job_text)


# This runs main() only when you start the file directly
# (for example: python3 job_matcher.py).
if __name__ == "__main__":
    main()
