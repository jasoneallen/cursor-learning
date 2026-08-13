#!/usr/bin/env python3
"""A simple command-line job matcher.

Ask for a candidate profile and a job description, then compare them
using skill and keyword matching.
"""

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
    """Print a formatted summary of the match results."""
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


def main():
    """Run the job matcher from start to finish."""
    print("Simple Job Matcher")
    print("Compare a candidate profile to a job description.")
    print()

    profile_text = get_multiline_input("1. Enter the candidate profile:")
    print()
    job_text = get_multiline_input("2. Enter the job description:")

    # Skills are the actual job requirements used for the score.
    profile_skills = get_skills(profile_text)
    job_skills = get_skills(job_text)
    matched_skills, missing_skills = find_matched_and_missing(
        profile_skills,
        job_skills,
    )
    score = calculate_match_score(matched_skills, job_skills)
    matched_categories = get_matched_categories(matched_skills)

    # Leftover words are shown separately and do not affect the score.
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


# This runs main() only when you start the file directly
# (for example: python3 job_matcher.py).
if __name__ == "__main__":
    main()
