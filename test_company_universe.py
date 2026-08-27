#!/usr/bin/env python3
"""Deterministic Company Universe tests. Does not call OpenAI or live ATS APIs."""

import functools
import os
import tempfile

from company_universe import (
    add_companies_from_names,
    company_is_active_source,
    import_companies_from_text,
    initialize_from_operator_data,
    load_company_universe,
    normalize_company_name,
    validate_universe_companies,
)
from test_source_discovery import combined_get, greenhouse_get, lever_get


def assert_true(condition, message):
    if not condition:
        raise AssertionError(message)


def with_temp_data_dir(fn):
    @functools.wraps(fn)
    def wrapped():
        previous = os.environ.get("OPERATOR_DATA_DIR")
        temp_dir = tempfile.mkdtemp(prefix="operator-universe-")
        os.environ["OPERATOR_DATA_DIR"] = temp_dir
        try:
            fn()
        finally:
            if previous is None:
                os.environ.pop("OPERATOR_DATA_DIR", None)
            else:
                os.environ["OPERATOR_DATA_DIR"] = previous

    return wrapped


def test_normalize_legal_suffix_only():
    assert_true(
        normalize_company_name("Nurix Therapeutics")
        == normalize_company_name("Nurix Therapeutics, Inc."),
        "legal suffix should not create a second company",
    )
    assert_true(
        normalize_company_name("Stripe") != normalize_company_name("Stripe Press"),
        "Stripe and Stripe Press should stay separate",
    )


@with_temp_data_dir
def test_initialize_from_operator_data():
    companies, stats = initialize_from_operator_data()
    names = [record.get("normalized_name") for record in companies]
    assert_true("nurix therapeutics" in names, names)
    assert_true("field ai" in names, names)
    nurix = [record for record in companies if record.get("normalized_name") == "nurix therapeutics"]
    assert_true(len(nurix) == 1, nurix)
    record = nurix[0]
    assert_true(record.get("active_source") is True, record)
    assert_true(record.get("ats_type") == "greenhouse", record)
    assert_true(record.get("ats_identifier") == "nurix", record)
    assert_true("Existing Config" in (record.get("source_origin") or []), record)
    field = [item for item in companies if item.get("normalized_name") == "field ai"][0]
    assert_true(field.get("active_source") is True, field)
    assert_true(field.get("ats_type") == "lever", field)
    # Initialize again should merge, not duplicate.
    _again, second = initialize_from_operator_data()
    again = load_company_universe()
    nurix_again = [item for item in again if item.get("normalized_name") == "nurix therapeutics"]
    assert_true(len(nurix_again) == 1, nurix_again)
    assert_true(second["added"] == 0, second)


@with_temp_data_dir
def test_duplicate_company_names():
    add_companies_from_names(["Nurix Therapeutics"], origin="Manual")
    add_companies_from_names(["Nurix Therapeutics, Inc."], origin="Manual")
    companies = load_company_universe()
    matches = [item for item in companies if item.get("normalized_name") == "nurix therapeutics"]
    assert_true(len(matches) == 1, companies)
    assert_true(matches[0].get("ats_status") == "Unknown" or matches[0].get("active_source") in (True, False), matches[0])


@with_temp_data_dir
def test_manual_new_company_unknown_ats():
    added, stats = add_companies_from_names(["Example Technology Company"], origin="Manual")
    assert_true(stats["added"] == 1, stats)
    record = added[0]
    assert_true(record["ats_status"] == "Unknown", record)
    assert_true(record["priority"] == "Medium", record)
    assert_true(record["active_source"] is False, record)
    assert_true("Manual" in record["source_origin"], record)


@with_temp_data_dir
def test_source_validation_updates_universe():
    add_companies_from_names(["Example Corp"], origin="Manual")
    http_get = greenhouse_get(
        "example-corp",
        ["Director of Cloud Operations", "Accountant"],
        board_name="Example Corp",
    )
    run_records, messages = validate_universe_companies(
        ["Example Corp"],
        refresh=True,
        http_get=http_get,
    )
    greenhouse = [record for record in run_records if record.get("ats_type") == "greenhouse"]
    assert_true(len(greenhouse) == 1, run_records)
    companies = load_company_universe()
    example = [item for item in companies if item.get("normalized_name") == "example"]
    assert_true(len(example) == 1, companies)
    record = example[0]
    assert_true(record["ats_status"] == "Validated", record)
    assert_true(record["ats_type"] == "greenhouse", record)
    assert_true(int(record["open_job_count"]) == 2, record)
    assert_true(int(record["likely_relevant_job_count"]) >= 1, record)
    assert_true(record["last_source_validation"], record)
    assert_true(record["active_source"] is False, record)
    assert_true(any("validated" in message.lower() for message in messages), messages)


@with_temp_data_dir
def test_existing_active_source_without_approval():
    initialize_from_operator_data()
    companies = load_company_universe()
    nurix = [item for item in companies if item.get("normalized_name") == "nurix therapeutics"][0]
    assert_true(company_is_active_source(nurix) is True, nurix)
    assert_true(nurix.get("active_source") is True, nurix)
    assert_true(nurix.get("source_validation_status") != "Approved", nurix)
    assert_true("Approved Source" not in (nurix.get("source_origin") or []), nurix.get("source_origin"))


@with_temp_data_dir
def test_import_file_csv_and_txt():
    csv_text = (
        "company,website,industry,headquarters,priority,notes\n"
        "Acme Biotech,https://acme.example,Biotech,San Francisco,High,imported\n"
        "Nurix Therapeutics,https://nurix.example,Biotech,Bay Area,Medium,dup\n"
        ",,,,,\n"
    )
    _imported, stats = import_companies_from_text(csv_text, filename="companies.csv")
    assert_true(stats["rows_read"] == 3, stats)
    assert_true(stats["added"] == 2, stats)
    assert_true(stats["invalid"] == 1, stats)
    txt_text = "Acme Biotech\nStripe Press\n"
    _imported, txt_stats = import_companies_from_text(txt_text, filename="names.txt")
    assert_true(txt_stats["merged"] == 1, txt_stats)
    assert_true(txt_stats["added"] == 1, txt_stats)
    companies = load_company_universe()
    acme = [item for item in companies if item.get("normalized_name") == "acme biotech"]
    assert_true(len(acme) == 1, companies)
    assert_true(acme[0]["industry"] == "Biotech", acme[0])
    press = [item for item in companies if item.get("normalized_name") == "stripe press"]
    assert_true(len(press) == 1, press)


@with_temp_data_dir
def test_company_universe_does_not_call_openai():
    import job_matcher

    def boom(*_args, **_kwargs):
        raise AssertionError("OpenAI should not be called during Company Universe")

    original = job_matcher.analyze_with_ai
    job_matcher.analyze_with_ai = boom
    try:
        test_initialize_from_operator_data()
        test_duplicate_company_names()
        test_manual_new_company_unknown_ats()
        test_source_validation_updates_universe()
        test_existing_active_source_without_approval()
        test_import_file_csv_and_txt()
        validate_universe_companies(
            ["Field AI"],
            refresh=True,
            http_get=lever_get("field-ai", ["Director of IT Operations"]),
        )
        combined_get("nurix", ["Director of IT Operations"], "field-ai", [])
    finally:
        job_matcher.analyze_with_ai = original


if __name__ == "__main__":
    tests = [
        test_normalize_legal_suffix_only,
        test_initialize_from_operator_data,
        test_duplicate_company_names,
        test_manual_new_company_unknown_ats,
        test_source_validation_updates_universe,
        test_existing_active_source_without_approval,
        test_import_file_csv_and_txt,
        test_company_universe_does_not_call_openai,
    ]
    for test in tests:
        test()
        print("ok", test.__name__)
    print("all tests passed")
