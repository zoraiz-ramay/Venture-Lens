"""An existing relationship with Siemens adds +1 to Collaborate (core/profile._siemens_relationship,
core/pillars.with_relationship_bonus).

What is pinned: detection reads one result at a time and needs the company, Siemens (a unit or a
product) and an engagement phrase together; a founder's Siemens CV and a comparison with Siemens are
not engagements; the startup's own site counts but says "claimed"; only a linked engagement earns
the point; and the point is Python's, added after the model without touching its criteria.
"""
import pandas as pd
import pytest

from core import assessment, carry_forward, pillar_match, pillars
from core import profile as P

ROW = pd.Series({"company_name": "Acme", "website": "https://acme.io"})


def found(body, key="news", href="https://news.example/a", row=ROW, prof=None):
    return P._siemens_relationship(prof or {}, "Acme", row, {key: [{"title": "", "href": href, "body": body}]})


# ----------------------------------------------------------------------------------- detection

@pytest.mark.parametrize("body, unit, kind", [
    ("Acme launches a pilot with Siemens Mobility in 2024 to monitor rail switches.", "Siemens Mobility", "pilot"),
    ("Acme raises $5M led by next47, the Siemens-backed venture firm.", "next47 (Siemens' venture firm)", "investment"),
    ("Siemens Smart Infrastructure is now a customer of Acme for building analytics.", "Siemens Smart Infrastructure", "customer"),
    ("Acme and Siemens announce a strategic partnership for factory AI.", "Siemens", "partner"),
    ("Acme was selected for the Siemens Xcelerator startup program cohort.", "Siemens Xcelerator", "programme"),
    ("Acme and Siemens Energy co-develop a turbine inspection system.", "Siemens Energy", "joint_project"),
    ("Acme becomes a supplier to Siemens Healthineers for imaging components.", "Siemens Healthineers", "supplier"),
])
def test_each_kind_of_engagement_is_read_from_a_third_party_result(body, unit, kind):
    (e,) = found(body)
    assert (e["unit"], e["type"], e["claimed"]) == (unit, kind, False)
    assert e["source_url"] == "https://news.example/a" and "Acme" in e["quote"]


def test_using_a_siemens_product_is_an_engagement_with_the_product_named():
    (e,) = found("Acme's inspection app integrates with Siemens Teamcenter for PLM data.")
    assert (e["type"], e["product"]) == ("uses_product", "Teamcenter")


def test_the_startups_own_site_counts_but_is_labelled_claimed():
    (e,) = found("Acme is certified for SIMATIC controllers.", key="__site__0", href="https://acme.io")
    assert (e["type"], e["product"], e["claimed"]) == ("uses_product", "SIMATIC", True)
    (e,) = found("Acme partners with Siemens.", href="https://www.acme.io/partners")
    assert e["claimed"] is True


@pytest.mark.parametrize("body", [
    "Acme CEO Jane Doe previously worked at Siemens for ten years.",        # a founder's CV
    "Acme is an alternative to Siemens Simcenter for small teams.",         # a comparison
    "Acme is built on NX and partners with local firms.",                   # "NX" without "Siemens"
    "Siemens Mobility pilots rail monitoring in Munich.",                   # Siemens, but not Acme
    "Acme serves smart infrastructure projects for cities.",                # a unit name as a phrase
    # Named in one list, not working together (a real false positive, from Wandelbots' results).
    "We're thrilled to be part of this new collaborative AI ecosystem, alongside SAP, Siemens, Acme and others.",
])
def test_what_is_not_an_engagement(body):
    assert found(body) == []


def test_an_application_only_mention_is_listed_without_a_link():
    row = pd.Series({"company_name": "Acme", "Your pitch": "Acme runs a pilot with Siemens Mobility."})
    (e,) = P._siemens_relationship({}, "Acme", row)
    assert e["claimed"] is True and e["source_url"] == ""


def test_grounded_investors_and_programmes_are_reused():
    prof = {"commercial": {"investors": [{"name": "next47", "source_url": "https://next47.com/acme"}]},
            "programs": [{"name": "Siemens Xcelerator", "source_url": "https://acme.io/news",
                          "confidence": "self_asserted"}]}
    out = {e["type"]: e for e in P._siemens_relationship(prof, "Acme", ROW)}
    assert out["investment"]["source_url"] == "https://next47.com/acme"
    assert out["programme"]["claimed"] is True


def test_one_engagement_per_kind_keeps_the_independent_source():
    out = P._siemens_relationship({}, "Acme", ROW, {
        "__site__0": [{"title": "", "href": "https://acme.io", "body": "Acme pilot with Siemens Mobility."}],
        "news": [{"title": "", "href": "https://news.example/a", "body": "Acme pilot with Siemens Mobility."}]})
    assert len(out) == 1 and out[0]["claimed"] is False


def test_the_profile_branch_records_engagements_and_their_facts(monkeypatch):
    def search(queries, **kw):
        return {k: ([{"title": "", "href": "https://news.example/a",
                      "body": "Acme launches a pilot with Siemens Mobility."}] if k == "sr_engage" else [])
                for k in queries}
    monkeypatch.setattr(P, "_ddg_many", search)

    class Offline:
        available = False
    out = P.research_profile(pd.Series({"company_name": "Acme"}), Offline(), do_web=True)
    (e,) = out["profile"]["siemens_engagements"]
    assert e["type"] == "pilot"
    facts = [f for f in out["facts"] if f.key == "siemens_relationship"]
    assert facts and facts[0].source_url == "https://news.example/a"


def test_a_sourced_engagement_is_carried_into_a_refresh_that_missed_it():
    e = {"unit": "Siemens Mobility", "type": "pilot", "product": "", "source_url": "https://news.example/a"}
    prior = {"deep_profile": {"siemens_engagements": [e]}, "facts": []}
    merged = carry_forward.merge_profile({"profile": {"siemens_engagements": []}, "facts": []}, [prior],
                                         "2026-10-07T00:00:00+00:00", {})
    assert merged["profile"]["siemens_engagements"][0]["unit"] == "Siemens Mobility"


# ------------------------------------------------------------------------------------ the bonus

def collaborate(total_scores, status="assessed"):
    if status != "assessed":
        return pillars.unassessed("Collaborate", "no_grounded_evidence", "x")
    ids = ("capability_fit", "need_fit", "actionability")
    rows = [{"id": i, "label": i, "score": s, "evidence": []} for i, s in zip(ids, total_scores)]
    total = sum(total_scores)
    return {"pillar": "Collaborate", "status": "assessed", "total": total,
            "band": pillars.band(total, total_scores[2]), "criteria": rows, "notes": [], "next_step": ""}


LINKED = [{"unit": "Siemens Mobility", "department": "mobility", "type": "pilot", "product": "",
           "year": "2024", "source_url": "https://news.example/a", "claimed": False}]


@pytest.mark.parametrize("scores, total", [((0, 0, 0), 1), ((2, 2, 1), 6), ((3, 3, 3), 9)])
def test_the_bonus_adds_one_point_up_to_nine_and_leaves_the_criteria_alone(scores, total):
    before = collaborate(scores)
    after = pillars.with_relationship_bonus(before, LINKED)
    assert after["total"] == total and after["model_total"] == sum(scores)
    assert after["relationship_bonus"] == 1 and after["siemens_relationship"] == LINKED
    assert [r["score"] for r in after["criteria"]] == list(scores)
    assert "Siemens Mobility: pilot (2024)" in after["notes"][-1]


def test_six_becomes_strong_only_with_actionability_at_two():
    assert pillars.with_relationship_bonus(collaborate((2, 2, 2)), LINKED)["band"] == "strong"
    assert pillars.with_relationship_bonus(collaborate((3, 2, 1)), LINKED)["band"] == "review"


def test_no_bonus_without_a_link_or_without_an_assessment():
    unlinked = [{**LINKED[0], "source_url": ""}]
    assert "relationship_bonus" not in pillars.with_relationship_bonus(collaborate((1, 1, 1)), unlinked)
    assert pillars.with_relationship_bonus(collaborate(None, "unassessed"), LINKED)["total"] is None
    connect = {**collaborate((1, 1, 1)), "pillar": "Connect"}
    assert "relationship_bonus" not in pillars.with_relationship_bonus(connect, LINKED)


def test_every_collaborate_passes_through_the_bonus_including_a_deterministic_zero(monkeypatch):
    monkeypatch.setattr(pillar_match, "_assess_pillar", lambda *a: collaborate((0, 0, 0)))
    run = {"deep_profile": {"siemens_engagements": LINKED}}
    out = pillar_match.assess_pillar("Collaborate", {}, run, None)
    assert out["total"] == 1 and out["model_total"] == 0


def test_the_route_says_a_point_was_added():
    out = pillars.recommend({"Collaborate": pillars.with_relationship_bonus(collaborate((2, 2, 2)), LINKED)})
    assert any("includes +1 for an existing Siemens relationship" in r for r in out["reasons"])


def test_a_tie_between_departments_goes_to_the_unit_the_startup_works_with():
    def entry(dep):
        c = pillars.with_relationship_bonus(collaborate((0, 0, 0)), LINKED)
        return {"department": {"id": dep}, "assessment": {"pillars": {"Collaborate": c}}}
    ranked = sorted(["di", "si", "mobility"], key=lambda d: assessment._collaborate_rank(entry(d), ["di", "si", "mobility"].index(d)))
    assert ranked[0] == "mobility"


def test_two_parties_joined_by_and_are_not_a_list():
    (e,) = found("Acme and Siemens announce a strategic partnership for factory AI.")
    assert e["type"] == "partner"


def test_the_year_is_the_engagements_not_the_founding_year():
    (e,) = found("Since its foundation in 2017, Acme has raised funding from investors such as next47.")
    assert e["type"] == "investment" and e["year"] == ""
    (e,) = found("In 2024 Acme started a pilot with Siemens Mobility.")
    assert e["year"] == "2024"
