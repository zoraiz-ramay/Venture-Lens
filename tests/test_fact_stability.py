"""scripts/fact_stability.py reads stored runs the way a reviewer would compare them."""
import importlib.util
import json
import pathlib
import sqlite3

_spec = importlib.util.spec_from_file_location(
    "fact_stability", pathlib.Path(__file__).resolve().parent.parent / "scripts" / "fact_stability.py")
fs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fs)


def _run(founders, competitors, hq="Munich"):
    return {"profile": {"hq": hq},
            "deep_profile": {"founders": founders},
            "trend": {"landscape": {"competitors": competitors}}}


def test_found_and_shown_are_told_apart_and_duplicates_and_changes_reported(tmp_path):
    runs = [
        _run([{"name": "Andreas Wagner"}], [{"name": "Refinity"}]),
        _run([{"name": "Andreas Wagner"}, {"name": "Alexandre"}],
             [{"name": "Dow"}, {"name": "Refinity", "last_confirmed_at": "2026-10-12"}]),
        _run([{"name": "Alexandre Kremer"}, {"name": "Alexandre", "last_confirmed_at": "2026-10-12"}],
             [{"name": "Refinity"}], hq="Berlin"),
    ]
    db = tmp_path / "runs.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE runs (id INTEGER PRIMARY KEY, company TEXT, result_json TEXT)")
    con.executemany("INSERT INTO runs (company, result_json) VALUES (?, ?)",
                    [("Radical Dot", json.dumps(r)) for r in runs])
    con.commit(); con.close()

    rep = fs.analyse(fs.load(str(db))["Radical Dot"])
    competitors = rep["lists"]["competitors"]
    assert competitors["found_counts"] == [1, 1, 1]          # carried Refinity is not "found" in run 2
    assert competitors["found_jaccard"] == [0.0, 0.0]
    assert competitors["flicker"] == ["refinity"]            # found, missed, found again
    assert rep["lists"]["founders"]["shown_dropped"][1] == ["andreas wagner"]
    assert ("alexandre kremer", "alexandre") in rep["duplicates"]
    assert rep["scalars"]["hq"] == ["Munich", "Munich", "Berlin"]


def test_no_database_means_nothing_to_compare(tmp_path):
    assert fs.load(str(tmp_path / "missing.db")) == {}
