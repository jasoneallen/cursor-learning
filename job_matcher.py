#!/usr/bin/env python3
"""A simple command-line job matcher.

Ask for a candidate profile and a job description, then compare them
using keyword matching.
"""

# Words that are too common to be useful in a match score.
STOP_WORDS = ["the", "and", "a", "to", "of", "in", "for", "with"]


def get_multiline_input(prompt):
    """Ask the user to paste or type text. A blank line means they are done."""
    print(prompt)
    print("(Paste or type your text. Press Enter on an empty line when done.)")
    lines = []
    while True:
        line = input()
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


def get_keywords(text):
    """Turn text into a list of unique keywords, skipping common stop words."""
    words = clean_text(text).split()
    keywords = []
    for word in words:
        if word not in STOP_WORDS and word not in keywords:
            keywords.append(word)
    return keywords


def find_matched_and_missing(profile_keywords, job_keywords):
    """Compare job keywords to the profile and return two lists.

    matched: job keywords that also appear in the profile
    missing: job keywords that do not appear in the profile
    """
    matched = []
    missing = []
    for word in job_keywords:
        if word in profile_keywords:
            matched.append(word)
        else:
            missing.append(word)
    return matched, missing


def calculate_match_score(matched_keywords, job_keywords):
    """Return the percent of job keywords found in the profile.

    If the job description has no keywords, the score is 0.
    """
    if len(job_keywords) == 0:
        return 0
    return round(len(matched_keywords) / len(job_keywords) * 100)


def format_keyword_list(keywords):
    """Turn a list of keywords into a readable string."""
    if len(keywords) == 0:
        return "None"
    return ", ".join(keywords)


def display_results(score, matched_keywords, missing_keywords):
    """Print a formatted summary of the match results."""
    print()
    print("=" * 44)
    print("  Job Match Results")
    print("=" * 44)
    print(f"  Match score:       {score}%")
    print(f"  Matched keywords:  {format_keyword_list(matched_keywords)}")
    print(f"  Missing keywords:  {format_keyword_list(missing_keywords)}")
    print("=" * 44)


def main():
    """Run the job matcher from start to finish."""
    print("Simple Job Matcher")
    print("Compare a candidate profile to a job description.")
    print()

    profile_text = get_multiline_input("1. Enter the candidate profile:")
    print()
    job_text = get_multiline_input("2. Enter the job description:")

    profile_keywords = get_keywords(profile_text)
    job_keywords = get_keywords(job_text)
    matched, missing = find_matched_and_missing(profile_keywords, job_keywords)
    score = calculate_match_score(matched, job_keywords)

    display_results(score, matched, missing)


# This runs main() only when you start the file directly
# (for example: python3 job_matcher.py).
if __name__ == "__main__":
    main()
