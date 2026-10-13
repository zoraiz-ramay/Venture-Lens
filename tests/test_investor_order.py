"""The investors a run keeps do not depend on the order the model listed them in."""
import json

import pandas as pd

from core import profile as P


class _Model:
    available = True

    def __init__(self, investors):
        self.reply = json.dumps({"investors": investors})

    def complete(self, *a, **k):
        return self.reply


def _investors(listed):
    prof = {}
    web = {"investors_web": [{"title": "Radical Dot raises", "body": "investors", "href": "https://press.example/a"}]}
    P._extract_commercial_posture(prof, "Radical Dot", pd.Series({"company_name": "Radical Dot"}), {}, web,
                                  _Model(listed))
    return [i["name"] for i in prof.get("commercial", {}).get("investors", [])]


def test_the_same_investors_listed_in_another_order_come_out_the_same():
    named = [{"name": f"Fund {c}", "source_url": f"https://press.example/{c}"} for c in "KJIHGFEDCBA"]
    unsourced = [{"name": "Angel Zed", "source_url": ""}]
    first = _investors(named + unsourced)
    second = _investors(list(reversed(named)) + unsourced)
    assert first == second
    assert len(first) == 8 and "Angel Zed" not in first        # sourced ones win the cap
