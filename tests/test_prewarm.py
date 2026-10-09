"""scripts/prewarm.py decides which startups need evaluating; it never re-evaluates a current one."""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from scripts.prewarm import plan  # noqa: E402


def test_current_runs_are_skipped_and_names_are_deduplicated_in_order():
    todo, fresh = plan(["Phena", " bliro ", "PHENA", "", "Celonis", "Bliro"],
                       is_current=lambda n: n == "Celonis")
    assert todo == ["Phena", "bliro"]
    assert fresh == ["Celonis"]


def test_report_row_counts_uncached_calls_and_times_the_researched_profile():
    from scripts.prewarm import report_row
    call = lambda stage, kind, ms, cached=False, think=0: {  # noqa: E731
        "stage": stage, "kind": kind, "cached": cached, "duration_ms": ms, "reasoning_tokens": think}
    result = {"found": True, "engine": "openai:gpt-5.4 · 3/18 web queries timed out",
              "profile": {"founded_year": "2019", "funding": "", "website": "https://x.io"},
              "degraded": [{"stage": "trend", "reason": "timeout", "calls": 1}],
              "token_usage": {"log": [call("profile", "completion", 4000, think=50),
                                      call("profile", "completion", 2000, cached=True),
                                      call("pillars", "web_search", 9000)]}}
    row = report_row("X", result, 61.23, {"identity": [1.0], "profile": [3.0, 42.5], "score": [55.0]})

    assert row["wall_s"] == 61.2
    assert row["profile_s"] == 42.5          # the researched emission, not the row-only one
    assert list(row["first_seen_s"]) == ["identity", "profile", "score"]
    assert row["calls"] == {"completion": 1, "web_search": 1}
    assert row["cached_calls"] == 1 and row["reasoning_tokens"] == 50
    assert list(row["by_stage"]) == ["pillars", "profile"]   # slowest stage first
    assert row["by_stage"]["profile"] == {"calls": 2, "cached": 1, "seconds": 6.0, "reasoning_tokens": 50}
    assert row["timed_out"] == 3
    assert row["missing"] == ["funding", "employees_count", "hq", "linkedin_url"]
