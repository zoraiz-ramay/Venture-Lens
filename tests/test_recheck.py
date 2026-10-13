"""Carried facts are re-read from their own source and flagged, never removed (core/recheck.py)."""
from core import recheck

DP = {"founders": [{"name": "Andreas Wagner", "source_url": "https://radicaldot.example/team",
                    "last_confirmed_at": "2026-10-01"},
                   {"name": "Fresh Person", "source_url": "https://radicaldot.example/team"}],      # not carried
      "advisors": [{"name": "Grace Hopper", "source_url": "https://linkedin.com/in/grace",
                    "last_confirmed_at": "2026-10-01"}],                                          # unreadable host
      "programs": [{"name": "Xpreneurs", "source_url": "https://unternehmertum.example/xpreneurs",
                    "last_confirmed_at": "2026-10-01"}],
      "commercial": {"investors": [{"name": "UVC Partners", "source_url": "https://press.example/round",
                                    "last_confirmed_at": "2026-10-01"}]}}
PAGES = {"https://radicaldot.example/team": "Radical Dot team: Andreas Wagner, co-founder",
         "https://unternehmertum.example/xpreneurs": "XPRENEURS batch 15 includes Radical Dot",
         "https://press.example/round": "Radical Dot announces its seed round"}           # UVC gone


def test_only_carried_readable_facts_are_rechecked_and_a_first_miss_is_only_unsupported():
    out = recheck.recheck(DP, "Radical Dot", fetch=PAGES.get)
    status = {(o["kind"], o["name"]): o["status"] for o in out}
    assert status == {("founder", "Andreas Wagner"): "supported", ("program", "Xpreneurs"): "supported",
                      ("investor", "UVC Partners"): "unsupported"}


def test_two_fetched_misses_in_a_row_make_a_fact_unconfirmed_and_an_unreadable_page_never_counts():
    first = recheck.recheck(DP, "Radical Dot", fetch=PAGES.get)
    second = recheck.recheck(DP, "Radical Dot", previous=first, fetch=PAGES.get)
    assert next(o for o in second if o["name"] == "UVC Partners")["status"] == "unconfirmed"
    gone = recheck.recheck(DP, "Radical Dot", previous=second, fetch=lambda url: "")
    assert {o["status"] for o in gone} == {"unreachable"}
    assert next(o for o in gone if o["name"] == "UVC Partners")["misses"] == 2      # kept, not reset


def test_a_slow_page_is_unreachable_within_the_budget():
    import time
    out = recheck.recheck(DP, "Radical Dot", budget_s=0.2, fetch=lambda url: time.sleep(1) or PAGES.get(url))
    assert {o["status"] for o in out} == {"unreachable"}
