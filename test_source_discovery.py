#!/usr/bin/env python3
"""Deterministic source-discovery tests. Does not call OpenAI or live ATS APIs."""

import functools
import os
import tempfile

from greenhouse_source import configured_greenhouse_boards
from lever_source import configured_lever_sites
from source_discovery import (
    already_configured_flag,
    approve_discovered_source,
    discover_and_validate_sources,
    identifiers_from_company,
    parse_company_names,
    title_looks_relevant,
)
from source_registry import load_approved_maps


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.ok = 200 <= status_code < 300

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def assert_true(condition, message):
    if not condition:
        raise AssertionError(message)


def greenhouse_get(valid_token, titles, board_name="Example Board"):
    def http_get(url, params=None, timeout=None, headers=None):
        url = str(url)
        if "boards-api.greenhouse.io" in url:
            if "/jobs" in url:
                token = url.split("/boards/")[1].split("/")[0]
                if token == valid_token:
                    jobs = [{"id": index + 1, "title": title} for index, title in enumerate(titles)]
                    return FakeResponse(200, {"jobs": jobs})
                return FakeResponse(404, {})
            token = url.rstrip("/").split("/")[-1]
            if token == valid_token:
                return FakeResponse(200, {"name": board_name})
            return FakeResponse(404, {})
        return FakeResponse(404, {})

    return http_get


def lever_get(valid_slug, titles):
    def http_get(url, params=None, timeout=None, headers=None):
        url = str(url)
        if "api.lever.co" in url or "api.eu.lever.co" in url:
            slug = url.rstrip("/").split("/")[-1]
            if slug == valid_slug:
                jobs = [{"id": str(index + 1), "title": title, "text": title} for index, title in enumerate(titles)]
                return FakeResponse(200, jobs)
            return FakeResponse(404, {})
        return FakeResponse(404, {})

    return http_get


def combined_get(gh_token, gh_titles, lever_slug, lever_titles):
    gh = greenhouse_get(gh_token, gh_titles, board_name="Nurix Therapeutics")
    lever = lever_get(lever_slug, lever_titles)

    def http_get(url, params=None, timeout=None, headers=None):
        url = str(url)
        if "greenhouse" in url:
            return gh(url, params=params, timeout=timeout, headers=headers)
        return lever(url, params=params, timeout=timeout, headers=headers)

    return http_get


def with_temp_data_dir(fn):
    @functools.wraps(fn)
    def wrapped():
        previous = os.environ.get("OPERATOR_DATA_DIR")
        temp_dir = tempfile.mkdtemp(prefix="operator-source-")
        os.environ["OPERATOR_DATA_DIR"] = temp_dir
        try:
            fn()
        finally:
            if previous is None:
                os.environ.pop("OPERATOR_DATA_DIR", None)
            else:
                os.environ["OPERATOR_DATA_DIR"] = previous

    return wrapped


def test_identifier_generation():
    acme = identifiers_from_company("Acme Corporation")
    assert_true("acme" in acme, acme)
    assert_true("acme-corporation" in acme, acme)
    assert_true(len(acme) <= 4, acme)

    field = identifiers_from_company("Field AI")
    assert_true(field == ["field-ai", "fieldai"] or field[:2] == ["field-ai", "fieldai"], field)
    assert_true("fieldai" in field, field)
    assert_true(len(field) <= 4, field)

    nurix = identifiers_from_company("Nurix Therapeutics")
    assert_true("nurix" in nurix, nurix)
    assert_true(len(nurix) <= 4, nurix)


def test_parse_company_names():
    names = parse_company_names("Databricks\n\nGilead\n# comment\nStripe\nDatabricks")
    assert_true(names == ["Databricks", "Gilead", "Stripe"], names)


def test_title_relevance_screen():
    assert_true(title_looks_relevant("Director, IT Infrastructure & Operations"), "nurix title")
    assert_true(title_looks_relevant("Director of IT Operations"), "pathstone title")
    assert_true(not title_looks_relevant("Software Engineer"), "engineer")
    assert_true(not title_looks_relevant("Director of Finance"), "finance")


@with_temp_data_dir
def test_existing_configured_source_nurix():
    assert_true(already_configured_flag("greenhouse", "nurix"), "nurix should already be configured")
    http_get = combined_get(
        "nurix",
        ["Director, IT Infrastructure & Operations", "Software Engineer"],
        "not-a-real-lever-site",
        [],
    )
    run_records, _saved, messages = discover_and_validate_sources(
        ["Nurix Therapeutics"],
        refresh=True,
        http_get=http_get,
    )
    greenhouse = [record for record in run_records if record.get("ats_type") == "greenhouse"]
    assert_true(len(greenhouse) == 1, run_records)
    record = greenhouse[0]
    assert_true(record["validation_status"] == "Validated", record)
    assert_true(record["identifier"] == "nurix", record)
    assert_true(record["already_configured"] is True, record)
    assert_true(record["approved"] is False, record)
    assert_true(int(record["open_job_count"]) == 2, record)
    assert_true(int(record["likely_relevant_job_count"]) >= 1, record)
    assert_true(any("already configured" in message.lower() for message in messages), messages)


@with_temp_data_dir
def test_invalid_company_does_not_crash():
    def http_get(url, params=None, timeout=None, headers=None):
        return FakeResponse(404, {})

    run_records, _saved, messages = discover_and_validate_sources(
        ["Definitely Not A Real ATS Company XYZ"],
        refresh=True,
        http_get=http_get,
    )
    assert_true(len(run_records) >= 1, run_records)
    assert_true(run_records[0]["validation_status"] in ("Invalid", "Error"), run_records[0])
    assert_true(any("no supported ATS" in message for message in messages), messages)


@with_temp_data_dir
def test_valid_greenhouse_board():
    http_get = greenhouse_get(
        "examplecorp",
        ["Director of Cloud Operations", "Accountant"],
        board_name="Example Corp",
    )
    run_records, _saved, _messages = discover_and_validate_sources(
        ["Example Corp"],
        refresh=True,
        http_get=http_get,
    )
    greenhouse = [record for record in run_records if record.get("ats_type") == "greenhouse"]
    assert_true(len(greenhouse) == 1, run_records)
    record = greenhouse[0]
    assert_true(record["validation_status"] == "Validated", record)
    assert_true(record["open_job_count"] == 2, record)
    assert_true(record["board_name"] == "Example Corp", record)
    assert_true(record["likely_relevant_job_count"] >= 1, record)


@with_temp_data_dir
def test_valid_lever_site():
    http_get = lever_get("field-ai", ["Director of IT, Infrastructure & Security", "Recruiter"])
    run_records, _saved, _messages = discover_and_validate_sources(
        ["Field AI"],
        refresh=True,
        http_get=http_get,
    )
    lever = [record for record in run_records if record.get("ats_type") == "lever"]
    assert_true(len(lever) == 1, run_records)
    record = lever[0]
    assert_true(record["validation_status"] == "Validated", record)
    assert_true(record["identifier"] == "field-ai", record)
    assert_true(record["open_job_count"] == 2, record)
    assert_true(record["already_configured"] is True, record)


@with_temp_data_dir
def test_approved_source_appears_in_live_config():
    http_get = greenhouse_get(
        "newco",
        ["VP of Information Technology"],
        board_name="Newco",
    )
    run_records, _saved, _messages = discover_and_validate_sources(
        ["Newco"],
        refresh=True,
        http_get=http_get,
    )
    greenhouse = [record for record in run_records if record.get("ats_type") == "greenhouse"]
    assert_true(len(greenhouse) == 1, greenhouse)
    record = greenhouse[0]
    assert_true("newco" not in configured_greenhouse_boards().values(), configured_greenhouse_boards())
    updated, message = approve_discovered_source(record["id"])
    assert_true(updated["validation_status"] == "Approved", updated)
    assert_true(updated["approved"] is True, updated)
    boards = configured_greenhouse_boards()
    assert_true(boards.get("Newco") == "newco", boards)
    approved = load_approved_maps()
    assert_true(approved["greenhouse"].get("Newco") == "newco", approved)
    assert_true("Approved" in message or "approved" in message.lower(), message)
    # Hardcoded sources still present.
    assert_true(boards.get("Nurix Therapeutics") == "nurix", boards)
    assert_true(configured_lever_sites().get("Field AI") in ("field-ai", "field-ai"), configured_lever_sites())


def test_source_discovery_does_not_call_openai():
    import job_matcher

    def boom(*_args, **_kwargs):
        raise AssertionError("OpenAI should not be called during source discovery")

    original = job_matcher.analyze_with_ai
    job_matcher.analyze_with_ai = boom
    try:
        test_existing_configured_source_nurix()
        test_invalid_company_does_not_crash()
        test_valid_greenhouse_board()
        test_valid_lever_site()
        test_approved_source_appears_in_live_config()
    finally:
        job_matcher.analyze_with_ai = original


if __name__ == "__main__":
    tests = [
        test_identifier_generation,
        test_parse_company_names,
        test_title_relevance_screen,
        test_existing_configured_source_nurix,
        test_invalid_company_does_not_crash,
        test_valid_greenhouse_board,
        test_valid_lever_site,
        test_approved_source_appears_in_live_config,
        test_source_discovery_does_not_call_openai,
    ]
    for test in tests:
        test()
        print("ok", test.__name__)
    print("all tests passed")
