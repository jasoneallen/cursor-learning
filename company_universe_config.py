"""Search preferences for Company Universe prioritization.

These values help sort and suggest company priority. They do not reject
companies and they are not job-fit rules. This module never calls OpenAI.
"""

PREFERRED_INDUSTRIES = [
    "Biotech",
    "Pharmaceutical",
    "Healthcare",
    "Healthtech",
    "Fintech",
    "SaaS",
    "Enterprise Software",
    "AI",
    "Technology",
    "Cybersecurity",
]

PREFERRED_LOCATIONS = [
    "San Jose",
    "Bay Area",
    "San Francisco",
    "Peninsula",
    "Remote US",
]

PREFERRED_COMPANY_STAGES = [
    "Growth",
    "Late-stage startup",
    "Public company",
    "Mid-size enterprise",
]
