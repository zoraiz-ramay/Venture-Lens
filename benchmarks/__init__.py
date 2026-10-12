"""Offline measurement of the decision layer against human labels.

`replay.py` re-scores stored runs with the current engine (no network, no model);
`routing_eval.py` compares the result with `labels.json`. Run with
`py -3 -m benchmarks.routing_eval` — see .claude/rules/benchmarks.md.
"""
