"""One person, one entry: core/text.py::same_person, the rule every people list is deduplicated by.

Matching on the name key alone listed Phena's co-founder twice ("KD Kutadgu Gokalp Demirci" and
"Kutadgu Gokalp Demirci"). Matching too loosely would be worse — it would delete a real person —
so the cases that must stay apart are pinned as firmly as the ones that must merge.
"""
import pytest

from core.text import clean_person_name, dedupe_people, founders_first, resolve_short_name, same_person


def p(name, **kw):
    return {"name": name, **kw}


@pytest.mark.parametrize("a, b", [
    (p("Andreas Wagner"), p("Dr. Andreas Wagner")),                          # honorific
    (p("Alexandre Kremer"), p("Alexandre Krémer (CTO)")),                    # accent, role
    (p("KD Kutadgu Gokalp Demirci"), p("Kutadgu Gokalp Demirci")),           # leading initials
    (p("Jane A. Doe"), p("Jane Doe")),                                       # middle initial
    (p("Andy Wagner", linkedin="https://www.linkedin.com/in/wagner-andreas/"),
     p("Andreas Wagner", source_url="https://linkedin.com/in/wagner-andreas")),  # same profile, shared surname
])
def test_the_same_person_spelled_differently_is_one_person(a, b):
    assert same_person(a, b) and same_person(b, a)


@pytest.mark.parametrize("a, b", [
    (p("Christian Piechnick"), p("Maria Piechnick")),                        # siblings share a surname
    # Research attached Anna Winiwarter's LinkedIn to "Florian Zahn": a shared link alone is not enough.
    (p("Florian Zahn", source_url="https://www.linkedin.com/in/anna-winiwarter"),
     p("Anna Winiwarter", linkedin="https://www.linkedin.com/in/anna-winiwarter")),
    (p("Jack Ma"), p("Jack")),                                               # one name left is not a full name
    (p("Li Wei"), p("Wei Li")),
    # A team page names everyone on it, so a shared non-LinkedIn source says nothing.
    (p("Anna Roe", source_url="https://acme.example/team"), p("Anna Smith", source_url="https://acme.example/team")),
])
def test_different_people_stay_apart(a, b):
    assert not same_person(a, b)


def test_dedupe_keeps_the_first_entry_and_fills_its_blanks_from_the_duplicate():
    out = dedupe_people([p("KD Kutadgu Gokalp Demirci", role=""), p("Kutadgu Gokalp Demirci", role="Co-Founder")])
    assert out == [{"name": "KD Kutadgu Gokalp Demirci", "role": "Co-Founder"}]


def test_a_founder_wins_over_the_same_person_in_key_team_or_advisors():
    prof = {"founders": [p("Trevor Amanya")], "key_team": [p("Dr. Trevor Amanya"), p("Ann Lee")],
            "advisors": [p("Trevor Amanya"), p("Ann Lee"), p("Bo Chen")]}
    founders_first(prof)
    assert [x["name"] for x in prof["key_team"]] == ["Ann Lee"]
    assert [x["name"] for x in prof["advisors"]] == ["Bo Chen"]


# ------------------------------------------------------------------ first names and placeholders
# Radical Dot has two founders, Andreas Wagner and Alexandre Kremer. Three refreshes stored 2, 3 and
# then 5, because the model wrote a different form of each name every run.
RADICAL_DOT = [
    p("Andreas Wagner", source_url="https://fundraisingfox.example/radicaldot"),
    p("Alex (surname not stated)", background="HEC Paris", source_url="https://radicaldot.notion.example"),
    p("Andi (surname not stated)", source_url="https://radicaldot.notion.example"),
    p("Alexandre Kremer", source_url="https://openregister.example/radical-dot"),
    p("Alexandre", source_url="https://radicaldot.notion.example"),
]


def test_radical_dots_name_variants_are_its_two_founders_with_their_full_names():
    out = dedupe_people(RADICAL_DOT)
    assert [x["name"] for x in out] == ["Andreas Wagner", "Alexandre Kremer"]
    assert out[1]["background"] == "HEC Paris"                       # a blank filled from "Alex"


@pytest.mark.parametrize("raw, name", [
    ("Alex (surname not stated)", "Alex"), ("Jane Doe (CEO)", "Jane Doe"), ("Alex, last name unknown", "Alex"),
    ("Founder (name not given)", ""), ("Unknown", ""), ("Not stated", ""), ("CEO", ""), ("王伟", "王伟"),
])
def test_a_placeholder_is_never_part_of_a_name(raw, name):
    assert clean_person_name(raw) == name


def test_a_placeholder_entry_is_never_stored():
    assert dedupe_people([p("Unknown", role="CTO"), p("Co-founder (name not stated)")]) == []
    assert all("stated" not in x["name"] for x in dedupe_people(RADICAL_DOT))


def test_a_first_name_with_no_full_name_to_join_is_dropped():
    assert dedupe_people([p("Alex", role="CTO")]) == []


def test_a_first_name_that_could_be_two_people_merges_with_neither():
    out = dedupe_people([p("Alex", background="HEC"), p("Alexandre Kremer"), p("Alexander Ochs")])
    assert [x["name"] for x in out] == ["Alexandre Kremer", "Alexander Ochs"]
    assert not any(x.get("background") for x in out)


@pytest.mark.parametrize("short, full, match", [
    ("Alexandre", "Alexandre Kremer", True),        # the first name itself
    ("Alex", "Alexandre Kremer", True),             # a prefix of three letters or more
    ("Andi", "Andreas Wagner", True),               # a common short form
    ("Tom", "Thomas Müller", True),
    ("Al", "Alexandre Kremer", False),              # too short to be a prefix
    ("Kremer", "Alexandre Kremer", False),          # a surname alone is not a given name
    ("Andi", "Alexandre Kremer", False),
])
def test_which_first_names_can_name_a_full_name(short, full, match):
    assert (resolve_short_name(p(short), [p(full)]) is not None) is match


def test_a_founder_named_by_first_name_only_in_key_team_is_dropped_there():
    prof = {"founders": [p("Andreas Wagner")], "key_team": [p("Andi"), p("Ann Lee")]}
    founders_first(prof)
    assert [x["name"] for x in prof["key_team"]] == ["Ann Lee"]


def test_the_team_refresh_merge_applies_the_same_identity_rules():
    from core.profile import merge_team
    run = {"founders": [p("Andreas Wagner", source_url="https://a.example")]}
    out = merge_team(run, {"founders": [p("Andi (surname not stated)", background="PhD, Cambridge"),
                                         p("Alex"), p("Unknown")]})
    assert [x["name"] for x in out["founders"]] == ["Andreas Wagner"]
    assert out["founders"][0]["background"] == "PhD, Cambridge"


def test_a_person_is_kept_only_where_one_result_names_them_with_the_company():
    from core import profile as P
    results = {"founders": [{"title": "Andreas Wagner - Co-Founder - Radical Dot | LinkedIn", "body": "",
                             "href": "https://linkedin.com/in/aw"},
                            {"title": "Jane Doe joins Other Startup", "body": "", "href": "https://news.example"}],
               "__site__0": [{"title": "Radical Dot — /team", "body": "Radical Dot Alexandre Krémer, CTO",
                              "href": "https://radicaldot.com"}]}
    people = [{"name": "Andreas Wagner"}, {"name": "Alexandre Kremer"}, {"name": "Jane Doe"}]
    kept = [p["name"] for p in P._ground_people(people, "Radical Dot", results)]
    assert kept == ["Andreas Wagner", "Alexandre Kremer"]          # accent folded; Jane never with the company
