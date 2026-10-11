"""Editorial rules for the real shipped resource catalog and the held-back list.

These run against cvmax/resources/catalog/*.json and cvmax/resources/held_back.json, not fixtures. The caps come from
docs/plan-resources-and-free-launch.md, Part E (principles 1 and 2) and the judge pass in docs/resources-review.md.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from cvmax.resources import catalog as catalog_mod
from cvmax.resources.catalog import load_catalog, load_skills, url_error, validate
from cvmax.resources.models import CLUSTERS, MAX_PRICE_NOTE, Resource

HELD_BACK_PATH = catalog_mod.RESOURCES_DIR / "held_back.json"
MAX_PER_SKILL = 3
MAX_PAID_PER_SKILL = 1
MIN_FETCHED_SHARE = 0.85
FIXTURES = Path(__file__).parent / "fixtures" / "resources"


@pytest.fixture(scope="module")
def shipped() -> list[Resource]:
    return load_catalog()


@pytest.fixture(scope="module")
def held_back_raw() -> list[dict]:
    data = json.loads(HELD_BACK_PATH.read_text(encoding="utf-8"))
    out: list[dict] = []
    for cluster, items in data["clusters"].items():
        assert cluster in CLUSTERS, f"held_back.json: unknown cluster {cluster!r}"
        out.extend(items)
    return out


@pytest.fixture(scope="module")
def held_back(held_back_raw) -> list[Resource]:
    return [Resource.from_dict(d) for d in held_back_raw]


def _ids(items) -> str:
    return ", ".join(sorted(items))


def test_catalog_is_not_empty(shipped):
    assert len(shipped) > 0


def test_every_shipped_entry_is_active(shipped):
    bad = [r.id for r in shipped if r.status != "active"]
    assert not bad, f"shipped entries that are not active: {_ids(bad)}"


def test_validate_passes_with_real_skills_and_today(shipped):
    errs = validate(shipped, load_skills(), today=date.today())
    assert not errs, "catalog problems:\n" + "\n".join(errs)


def test_at_most_three_entries_per_primary_skill(shipped):
    by_skill: dict[str, list[str]] = defaultdict(list)
    for r in shipped:
        by_skill[r.skills[0]].append(r.id)
    over = {s: ids for s, ids in by_skill.items() if len(ids) > MAX_PER_SKILL}
    assert not over, "skills over the cap of 3: " + "; ".join(f"{s}: {_ids(ids)}" for s, ids in over.items())


def test_at_most_one_paid_entry_per_primary_skill(shipped):
    by_skill: dict[str, list[str]] = defaultdict(list)
    for r in shipped:
        if r.cost == "paid":
            by_skill[r.skills[0]].append(r.id)
    over = {s: ids for s, ids in by_skill.items() if len(ids) > MAX_PAID_PER_SKILL}
    assert not over, "skills with more than one paid entry: " + "; ".join(
        f"{s}: {_ids(ids)}" for s, ids in over.items())


def test_ids_unique_across_shipped_and_held_back(shipped, held_back):
    counts = Counter([r.id for r in shipped] + [r.id for r in held_back])
    dupes = [i for i, n in counts.items() if n > 1]
    assert not dupes, f"ids listed more than once: {_ids(dupes)}"


def test_no_url_appears_twice(shipped, held_back):
    by_url: dict[str, list[str]] = defaultdict(list)
    for r in [*shipped, *held_back]:
        by_url[r.url.rstrip("/").lower()].append(r.id)
    dupes = {u: ids for u, ids in by_url.items() if len(ids) > 1}
    assert not dupes, "urls listed more than once: " + "; ".join(f"{u}: {_ids(ids)}" for u, ids in dupes.items())


def test_held_back_entries_have_a_reason_and_are_not_active(held_back_raw):
    no_reason = [d.get("id", "?") for d in held_back_raw
                 if not isinstance(d.get("held_back_reason"), str) or not d["held_back_reason"].strip()]
    active = [d.get("id", "?") for d in held_back_raw if d.get("status") == "active"]
    assert not no_reason, f"held-back entries without a reason: {_ids(no_reason)}"
    assert not active, f"held-back entries marked active: {_ids(active)}"


def test_held_back_entries_have_the_catalog_schema(held_back_raw):
    for d in held_back_raw:
        missing = [f for f in Resource.FIELDS if f not in d]
        extra = sorted(set(d) - set(Resource.FIELDS) - {"held_back_reason"})
        assert not missing and not extra, f"{d.get('id', '?')}: missing {missing}, unexpected {extra}"


def test_cluster_files_are_known_clusters():
    files = sorted(catalog_mod.CATALOG_DIR.glob("*.json"))
    assert files, "no catalog files found"
    unknown = [f.stem for f in files if f.stem not in CLUSTERS]
    assert not unknown, f"catalog files that are not in models.CLUSTERS: {_ids(unknown)}"
    wrong = [f.name for f in files if json.loads(f.read_text(encoding="utf-8")).get("cluster") != f.stem]
    assert not wrong, f"files whose 'cluster' field differs from the file name: {_ids(wrong)}"


def test_most_entries_were_fetched_not_snippets(shipped):
    snippets = [r.id for r in shipped if r.verification != "fetched"]
    share = 1 - len(snippets) / len(shipped)
    assert share >= MIN_FETCHED_SHARE, (
        f"only {share:.0%} of entries were verified by fetching the page; search-snippet only: {_ids(snippets)}")


def test_every_entry_has_evidence(shipped, held_back):
    bad = [r.id for r in [*shipped, *held_back] if not r.evidence]
    assert not bad, f"entries without evidence: {_ids(bad)}"


def test_paid_and_freemium_entries_name_the_billing_basis(shipped):
    # The app shows only a number next to "Paid", so why_picked must say what the number buys.
    words = ("a month", "a year", "per test", "per exam", "six months", "one-time", "for one year")
    bad = [r.id for r in shipped if r.cost in ("paid", "freemium")
           and not any(w in r.why_picked.lower() for w in words)]
    assert not bad, f"paid or freemium entries whose why_picked does not state the billing period: {_ids(bad)}"


def test_review_doc_lists_every_shipped_and_held_back_id(shipped, held_back):
    doc = Path(__file__).resolve().parent.parent / "docs" / "resources-review.md"
    text = doc.read_text(encoding="utf-8")
    missing = [r.id for r in [*shipped, *held_back] if r.id not in text]
    assert not missing, f"docs/resources-review.md does not mention: {_ids(missing)}"


# --- the billing basis of a price (price_note) ---

def test_price_note_is_required_for_paid_and_freemium_and_empty_for_free(shipped):
    bad_missing = [r.id for r in shipped if r.cost in ("paid", "freemium") and not r.price_note.strip()]
    bad_free = [r.id for r in shipped if r.cost in ("free", "free-audit") and r.price_note]
    too_long = [r.id for r in shipped if len(r.price_note) > MAX_PRICE_NOTE]
    assert not bad_missing, f"paid or freemium entries without a price_note: {_ids(bad_missing)}"
    assert not bad_free, f"free entries with a price_note: {_ids(bad_free)}"
    assert not too_long, f"price_note longer than {MAX_PRICE_NOTE} characters: {_ids(too_long)}"


def test_price_note_says_the_billing_period_in_words(shipped):
    words = ("per month", "per 6 months", "per exam", "per test", "one-time")
    bad = [r.id for r in shipped if r.cost in ("paid", "freemium") and not any(w in r.price_note for w in words)]
    assert not bad, f"price_note does not name a billing period: {_ids(bad)}"


def test_known_billing_bases_are_stated(shipped):
    notes = {r.id: r.price_note for r in shipped}
    assert notes["toastmasters-pathways"] == "per 6 months, plus a one-time $25 fee"
    assert notes["aws-certified-cloud-practitioner-exam"] == "per exam attempt"
    assert notes["macquarie-excel-skills-for-business-essentials"] == "per month with Coursera Plus"


def test_aws_static_website_is_freemium_with_aws_usage_as_the_basis(shipped):
    r = next(r for r in shipped if r.id == "aws-hands-on-host-a-static-website")
    assert r.cost == "freemium" and r.price_usd == 3
    assert r.price_note == "AWS usage per month after the free tier"


def fixture_skills():
    return load_skills(FIXTURES / "skills.json")


def fixture_resource(rid: str) -> Resource:
    return next(r for r in load_catalog(FIXTURES / "catalog") if r.id == rid)


def price_errors(r: Resource) -> list[str]:
    return [e for e in validate([r], fixture_skills(), today=date(2026, 10, 20)) if "price_note" in e]


def test_validate_requires_price_note_for_paid_and_freemium():
    paid = fixture_resource("brightpath-advanced-sql")
    freemium = fixture_resource("dataworks-power-bi-project")
    assert price_errors(paid) == [] and price_errors(freemium) == []
    for r in (paid, freemium):
        assert price_errors(replace(r, price_note=""))
        assert price_errors(replace(r, price_note="   "))
        assert price_errors(replace(r, price_note="x" * (MAX_PRICE_NOTE + 1)))
        assert price_errors(replace(r, price_note="x" * MAX_PRICE_NOTE)) == []
        assert price_errors(replace(r, price_note=5))


def test_validate_forbids_price_note_for_free_entries():
    free = fixture_resource("northfield-sql-basics")
    audit = fixture_resource("datalab-pandas-audit")
    for r in (free, audit):
        assert price_errors(r) == []
        assert price_errors(replace(r, price_note="per month"))


def test_price_note_round_trips_and_defaults_to_empty():
    r = fixture_resource("brightpath-advanced-sql")
    assert Resource.from_dict(r.to_dict()).price_note == r.price_note == "per month"
    d = fixture_resource("northfield-sql-basics").to_dict()
    del d["price_note"]
    assert Resource.from_dict(d).price_note == ""


# --- the pwc entry is held back ---

def test_pwc_presentations_course_is_held_back_not_shipped(shipped, held_back_raw):
    assert "pwc-effective-business-presentations-powerpoint" not in {r.id for r in shipped}
    entry = next(d for d in held_back_raw if d["id"] == "pwc-effective-business-presentations-powerpoint")
    assert entry["held_back_reason"] == "free-audit not confirmed on the page; rating margin thin"
    assert entry["status"] != "active"
    # The skill still has a shipped resource.
    assert any("powerpoint" in r.skills for r in shipped)


# --- hosts that must never be accepted ---

@pytest.mark.parametrize("host", [
    "127.1", "0177.0.0.1", "2130706433", "0x7f.1", "127.0.0.1", "10.0.0.5", "169.254.169.254",
    "localhost", "intranet", "metadata.google.internal", "printer.local", "nas.lan", "app.localhost",
    "[::1]", "[fe80::1]", "[2001:db8::1]",
])
def test_internal_and_numeric_hosts_are_rejected(host):
    assert url_error(f"https://{host}/") is not None


@pytest.mark.parametrize("host", [
    "learn.microsoft.com", "uk.react.dev", "www.coursera.org", "cs50.harvard.edu", "docs.aws.amazon.com",
    "osvita.diia.gov.ua", "pages.stern.nyu.edu",
])
def test_public_hosts_are_accepted(host):
    assert url_error(f"https://{host}/path") is None
