"""Local configuration for live job sources.

Add Greenhouse companies here by mapping a display name to the public
board identifier. That identifier is the slug in a Greenhouse board URL:

    https://boards.greenhouse.io/<board_identifier>

Example:

    GREENHOUSE_BOARDS = {
        "Example Corp": "examplecorp",
    }

Leave the dictionary empty until you choose real companies. The Operator
will show a friendly message instead of fetching.
"""

GREENHOUSE_BOARDS = {}
