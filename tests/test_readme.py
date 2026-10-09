"""Перевірка, що README не суперечить workflow щодо порожніх базових ліній evals."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _readme_baseline_line() -> str:
    # рядок з твердженням про total = 0 у базових лініях
    for line in (ROOT / "README.md").read_text(encoding="utf-8").splitlines():
        if "Поки `total` = 0" in line:
            return line
    raise AssertionError("У README немає речення про total = 0")


def test_readme_empty_synth_baseline_blocks_only_weekly_lessons():
    line = _readme_baseline_line()
    # хибне твердження «обидва workflow зупиняє будь-яка базова лінія» прибрано
    assert "в будь-якій базовій лінії" not in line
    assert "`evals/baseline.json`" in line
    assert "зупиняє лише «Weekly lessons»" in line


def test_market_workflow_does_not_read_synth_baseline():
    # саме тому порожня synth_baseline не блокує market.yml
    market = (ROOT / ".github" / "workflows" / "market.yml").read_text(encoding="utf-8")
    learn = (ROOT / ".github" / "workflows" / "learn.yml").read_text(encoding="utf-8")
    assert "synth_baseline" not in market
    assert "synth_baseline" in learn


def test_evals_readme_describes_the_synthetic_evals():
    # Усе, що додав план: каталог вад, синтетичні кейси, --raw, таблиця за вадами, нові види перевірок.
    text = (ROOT / "evals" / "README.md").read_text(encoding="utf-8")
    for needle in ("evals/flaws.json", "evals/synth/", "--raw", "by_flaw", '"length"', '"mentions"',
                   "_no_invented_numbers", "evals/synth_baseline.json", "generate_synthetic.py"):
        assert needle in text, needle
    run_evals = (ROOT / "evals" / "run_evals.py").read_text(encoding="utf-8")
    assert '"--raw"' in run_evals and "by_flaw" in run_evals  # README не описує того, чого в скрипті немає
