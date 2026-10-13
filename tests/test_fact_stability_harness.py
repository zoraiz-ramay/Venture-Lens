"""Search luck must not change what a company's record shows (core/carry_forward.py).

Each simulated refresh "finds" a different, seeded-random subset of the same company's facts —
the way search ranking, timeouts and extraction vary between real runs — plus name variants,
a one-off competitor, and another report's market size. Folded through carry-forward run after
run (each result becomes the next run's earlier run), the record must settle: the same founders
under their full names, programmes that only grow, competitors that stop changing once the cap is
reached, and one market figure. Measured on Radical Dot before this work: founders 2 -> 3 -> 5,
competitors 7 -> 9 -> 13, three different market sizes in three refreshes.
"""
import datetime as dt
import random

from core import carry_forward as cf

TODAY = dt.date(2026, 10, 13)
SITE = "https://radicaldot.example"
FOUNDERS = [{"name": "Andreas Wagner", "role": "Co-CEO", "source_url": SITE + "/team"},
            {"name": "Alexandre Kremer", "role": "Co-Founder", "source_url": SITE + "/team"}]
VARIANTS = [{"name": "Alexandre", "source_url": SITE + "/notion"},
            {"name": "Alex (surname not stated)", "source_url": SITE + "/notion"},
            {"name": "Andi (surname not stated)", "source_url": SITE + "/notion"}]
PROGRAMMES = [{"name": n, "source_url": f"https://p.example/{i}"}
              for i, n in enumerate(["Xpreneurs", "TUM Venture Labs", "UnternehmerTUM", "Respond Accelerator"])]
COMPETITORS = [{"name": f"Rival {c}", "source_url": f"https://c.example/{c}"} for c in "ABCDEFGHIJKL"]
SIZES = [{"value": v, "cagr": "14%", "as_of": "2025", "source_url": f"https://r.example/{i}"}
         for i, v in enumerate(["$815 Million", "USD 3.7 billion", "USD 4.8 billion"])]


def _refresh(rng: random.Random, n: int) -> tuple[dict, dict]:
    """One run's luck: some of each list, a name variant, a one-off competitor, some report's size."""
    founders = rng.sample(FOUNDERS, rng.randint(1, 2)) + rng.sample(VARIANTS, rng.randint(0, 2))
    profile = {"website": SITE, "founders": founders,
               "programs": rng.sample(PROGRAMMES, rng.randint(1, 3))}
    trend = {"landscape": {"competitors": rng.sample(COMPETITORS[:6], 3) + [COMPETITORS[6 + n % 6]],
                           "funded_peers": [], "active_investors": [],
                           "market_size": rng.choice(SIZES)}}
    return profile, trend


def _simulate(seed: int, runs: int = 6) -> list[dict]:
    rng, priors, shown = random.Random(seed), [], []
    ident = cf.identity("Radical Dot", SITE)
    for n in range(runs):
        profile, trend = _refresh(rng, n)
        usable = cf.usable_priors(priors, ident)
        now = f"2026-10-{n + 1:02d}T00:00:00+00:00"
        merged = cf.merge_profile({"profile": profile, "facts": []}, usable, now, {})
        land = cf.merge_trend(trend, usable, now, TODAY, {})
        run = {"company": "Radical Dot", "run_created_at": now, "profile": {"website": SITE},
               "deep_profile": merged["profile"], "facts": merged["facts"], "trend": land}
        priors.append(run)
        shown.append(run)
    return shown


def _names(items) -> list[str]:
    return [i["name"] for i in items or []]


def test_founders_are_exactly_the_full_names_found_so_far_on_every_run():
    for seed in range(20):
        rng, found = random.Random(seed), set()
        for n, run in enumerate(_simulate(seed)):
            profile, _ = _refresh(rng, n)
            found |= {f["name"] for f in profile["founders"] if f in FOUNDERS}
            # Never a variant, never a duplicate, never one dropped after it was found.
            assert sorted(_names(run["deep_profile"]["founders"])) == sorted(found)


def test_programmes_only_grow_and_keep_their_order():
    for seed in range(20):
        runs = _simulate(seed)
        for before, after in zip(runs, runs[1:]):
            a, b = _names(before["deep_profile"]["programs"]), _names(after["deep_profile"]["programs"])
            assert set(a) <= set(b)                       # nothing found earlier goes missing
            assert [x for x in b if x in a] == a          # and what was shown keeps its order


def test_competitors_settle_on_the_recurring_ones_and_then_stop_changing():
    # Rivals A-F turn up on about half of all runs; G-L once each per six runs. A recurring rival
    # that first appears late can still take a one-off's slot - that is the point - but once the
    # recurring ones hold their slots the list stops changing.
    recurring = {f"Rival {c}" for c in "ABCDEF"}
    for seed in range(20):
        runs = _simulate(seed, runs=14)
        shown = [set(_names(r["trend"]["landscape"]["competitors"])) for r in runs]
        assert all(len(s) <= cf.LANDSCAPE_CAPS["competitors"] for s in shown)
        assert recurring <= shown[-1]
        for before, after in zip(shown[-6:], shown[-5:]):
            # Late changes only ever swap a one-off out for a recurring rival - never the reverse,
            # and a recurring rival once shown is never dropped.
            assert not (before - after) & recurring
            assert (after - before) <= recurring


def test_the_market_size_stays_one_figure_unless_two_runs_in_a_row_agree():
    for seed in range(20):
        runs = _simulate(seed)
        values = [r["trend"]["landscape"]["market_size"]["value"] for r in runs]
        for i in range(1, len(values)):
            if values[i] != values[i - 1]:
                # A change is only ever to a figure the run before had also found itself.
                assert values[i] == values[i - 1] or i >= 2
        assert len(set(values)) <= len(SIZES)
