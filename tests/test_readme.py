"""Checks that the README does not contradict the workflows about empty evals baselines."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _readme_baseline_line() -> str:
    # the line that states what happens while total = 0 in the baselines
    for line in (ROOT / "README.md").read_text(encoding="utf-8").splitlines():
        if "While `total` = 0" in line:
            return line
    raise AssertionError("README has no sentence about total = 0")


def test_readme_empty_synth_baseline_blocks_only_weekly_lessons():
    line = _readme_baseline_line()
    # the false claim "any baseline stops both workflows" is gone
    assert "in any baseline" not in line
    assert "`evals/baseline.json`" in line
    assert 'stops only "Weekly lessons"' in line


def test_market_workflow_does_not_read_synth_baseline():
    # this is why an empty synth_baseline does not block market.yml
    market = (ROOT / ".github" / "workflows" / "market.yml").read_text(encoding="utf-8")
    learn = (ROOT / ".github" / "workflows" / "learn.yml").read_text(encoding="utf-8")
    assert "synth_baseline" not in market
    assert "synth_baseline" in learn


def test_evals_readme_describes_the_synthetic_evals():
    # Everything the plan added: the flaw catalog, synthetic cases, --raw, the "By flaw" table, the new check kinds.
    text = (ROOT / "evals" / "README.md").read_text(encoding="utf-8")
    for needle in ("evals/flaws.json", "evals/synth/", "--raw", "by_flaw", '"length"', '"mentions"',
                   "_no_invented_numbers", "evals/synth_baseline.json", "generate_synthetic.py"):
        assert needle in text, needle
    run_evals = (ROOT / "evals" / "run_evals.py").read_text(encoding="utf-8")
    assert '"--raw"' in run_evals and "by_flaw" in run_evals  # the README does not describe what the script lacks
