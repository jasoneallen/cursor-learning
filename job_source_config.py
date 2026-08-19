"""Local configuration for live job sources.

Add Greenhouse companies here by mapping a display name to the public
board identifier. That identifier is the slug in a Greenhouse board URL:

    https://boards.greenhouse.io/<board_identifier>

Example:

    GREENHOUSE_BOARDS = {
        "Example Corp": "examplecorp",
    }

Add or remove companies here. The Operator only fetches when you click
Fetch Jobs in Job Discovery.
"""

GREENHOUSE_BOARDS = {
    "Nurix Therapeutics": "nurix",
    "Xaira Therapeutics": "xairatherapeutics",
    "Tenstorrent": "tenstorrent",
    "Fictiv": "fictiv",
    "Pathstone": "pathstone",
}
