#!/usr/bin/env python3
"""AI eligibility vs needs-analysis regression tests. Does not call OpenAI."""

from job_prefilter import (
    AI_ANALYSIS_THRESHOLD,
    ingest_jobs,
    is_ai_eligible,
    merge_duplicate,
    needs_ai_analysis,
)


def assert_true(condition, message):
    if not condition:
        raise AssertionError(message)


def scored_job(pre_score, analyzed=False, **overrides):
    job = {
        "id": "job-1",
        "company": "Nurix",
        "title": "Director, IT Infrastructure & Operations",
        "location": "San Francisco, CA",
        "source": "greenhouse",
        "url": "https://boards.greenhouse.io/nurix/jobs/1",
        "external_id": "1",
        "description": "Lead IT infrastructure and operations.",
        "pre_score": pre_score,
        "prefilter_passed": True,
        "rejection_reason": "",
        "ai_eligible": False,
        "status": "Review",
        "notes": "keep me",
        "ai_analysis": {},
        "match_score": "",
    }
    if analyzed:
        job["ai_analysis"] = {"summary": "saved analysis"}
        job["match_score"] = 82
        job["ai_eligible"] = False
    job.update(overrides)
    return job


def test_threshold_unchanged():
    assert_true(AI_ANALYSIS_THRESHOLD == 60, AI_ANALYSIS_THRESHOLD)


def test_analyzed_high_score_stays_eligible_without_needing_analysis():
    existing = scored_job(96, analyzed=True)
    incoming = scored_job(96, analyzed=False, ai_eligible=True, source="lever")
    merged = merge_duplicate(existing, incoming)
    assert_true(merged["pre_score"] == 96, merged["pre_score"])
    assert_true(merged["prefilter_passed"] is True, merged)
    assert_true(merged["ai_eligible"] is True, merged["ai_eligible"])
    assert_true(is_ai_eligible(merged) is True, "should remain AI eligible")
    assert_true(needs_ai_analysis(merged) is False, "should not need another GPT call")
    assert_true(merged["ai_analysis"] == {"summary": "saved analysis"}, merged["ai_analysis"])
    assert_true(merged["status"] == "Review", merged["status"])
    assert_true(merged["notes"] == "keep me", merged["notes"])


def test_new_unanalyzed_high_score_needs_analysis():
    job = scored_job(96, analyzed=False, ai_eligible=True)
    assert_true(is_ai_eligible(job) is True, job)
    assert_true(needs_ai_analysis(job) is True, job)


def test_below_threshold_needs_no_analysis():
    job = scored_job(55, analyzed=False, ai_eligible=True)
    assert_true(is_ai_eligible(job) is False, job)
    assert_true(needs_ai_analysis(job) is False, job)


def test_ingest_does_not_clear_eligibility_on_saved_analysis():
    description = (
        "Lead enterprise IT infrastructure and operations including AWS, Azure, "
        "identity, endpoint management, networking, vendor management, SOX, "
        "and a global team supporting hybrid cloud and cybersecurity. "
        * 4
    )
    existing = scored_job(96, analyzed=True, description=description)
    raw = {
        "company": "Nurix",
        "title": "Director, IT Infrastructure & Operations",
        "location": "San Francisco, CA",
        "source": "greenhouse",
        "url": "https://boards.greenhouse.io/nurix/jobs/1",
        "external_id": "1",
        "description": description,
    }
    discovery, pipeline, stats = ingest_jobs([raw], [existing], "", default_source="greenhouse")
    assert_true(stats["duplicates"] == 1, stats)
    assert_true(len(discovery) == 1, discovery)
    job = discovery[0]
    assert_true(job["prefilter_passed"] is True, job)
    assert_true(int(job["pre_score"]) >= AI_ANALYSIS_THRESHOLD, job["pre_score"])
    assert_true(job["ai_eligible"] is True, job["ai_eligible"])
    assert_true(needs_ai_analysis(job) is False, "saved analysis should skip OpenAI")
    assert_true(pipeline[0]["ai_analysis"] == {"summary": "saved analysis"}, pipeline[0])
    assert_true(stats["ai_eligible"] == 1, stats)


def test_ingest_new_high_score_needs_analysis():
    raw = {
        "company": "Pathstone",
        "title": "Director of IT Operations",
        "location": "New York, NY",
        "source": "greenhouse",
        "url": "https://boards.greenhouse.io/pathstone/jobs/2",
        "external_id": "2",
        "description": (
            "Lead IT operations, infrastructure, cloud, identity, and a team of "
            "managers supporting enterprise technology. AWS Azure Kubernetes SOX. "
            * 4
        ),
    }
    discovery, _pipeline, stats = ingest_jobs([raw], [], "", default_source="greenhouse")
    job = discovery[0]
    assert_true(job["prefilter_passed"] is True, job)
    assert_true(int(job["pre_score"]) >= AI_ANALYSIS_THRESHOLD, job["pre_score"])
    assert_true(job["ai_eligible"] is True, job["ai_eligible"])
    assert_true(needs_ai_analysis(job) is True, job)
    assert_true(stats["ai_eligible"] == 1, stats)


def test_apply_ai_result_keeps_eligibility():
    from operator_app import apply_ai_result, job_needs_ai_call

    job = scored_job(96, analyzed=False, ai_eligible=True)
    updated = apply_ai_result(
        job,
        {
            "overall_match_score": 80,
            "recommendation": "Pursue",
            "blocker_severity": "None",
            "blocker_summary": "",
            "summary": "new analysis",
        },
    )
    assert_true(updated["ai_eligible"] is True, updated["ai_eligible"])
    assert_true(updated["ai_analysis"]["overall_match_score"] == 80, updated["ai_analysis"])
    assert_true(job_needs_ai_call(updated) is False, "analyzed job should not need another call")


def test_ingest_does_not_call_openai():
    import job_matcher

    def boom(*_args, **_kwargs):
        raise AssertionError("OpenAI should not be called")

    original = job_matcher.analyze_with_ai
    job_matcher.analyze_with_ai = boom
    try:
        test_ingest_does_not_clear_eligibility_on_saved_analysis()
        test_ingest_new_high_score_needs_analysis()
    finally:
        job_matcher.analyze_with_ai = original


if __name__ == "__main__":
    tests = [
        test_threshold_unchanged,
        test_analyzed_high_score_stays_eligible_without_needing_analysis,
        test_new_unanalyzed_high_score_needs_analysis,
        test_below_threshold_needs_no_analysis,
        test_ingest_does_not_clear_eligibility_on_saved_analysis,
        test_ingest_new_high_score_needs_analysis,
        test_apply_ai_result_keeps_eligibility,
        test_ingest_does_not_call_openai,
    ]
    for test in tests:
        test()
        print("ok", test.__name__)
    print("all tests passed")
