"""Demo job postings for demo mode and AppTest (made up, on allowed domains)."""
from __future__ import annotations

import re
from datetime import date, timedelta

from cvmax.jobs.models import JobQuery, Vacancy
from cvmax.jobs.providers import deep_links
from cvmax.jobs.search import SearchResult


def demo_search_result(q: JobQuery, *, today: date | None = None) -> SearchResult:
    day = today or date.today()
    # The templates below add the level to the title, so we remove our own "Intern" or "Junior" from the role.
    p = re.sub(r"\b(intern(ship)?|junior|trainee|graduate|entry[- ]level)\b", "", q.primary, flags=re.I)
    p = " ".join(p.split()) or q.primary

    def ago(n: int) -> str:
        return (day - timedelta(days=n)).isoformat()

    rows = [
        ("https://jobs.dou.ua/companies/northwind/vacancies/100001/", f"{p} Intern", "Northwind", "Kyiv", "DOU", 1, False),
        ("https://remotive.com/remote-jobs/data/junior-data-analyst-100002", f"Junior {p}", "Brightwave", "Remote", "Remotive", 2, True),
        ("https://www.arbeitnow.com/jobs/companies/example/junior-analyst-100003", f"{p} Trainee", "Example GmbH", "Berlin", "Arbeitnow", 4, False),
        ("https://jobs.dou.ua/companies/contoso/vacancies/100004/", f"Graduate {p}", "Contoso", "Lviv", "DOU", 6, False),
        ("https://remotive.com/remote-jobs/data/entry-level-100005", f"{p} (Entry Level)", "Fabrikam", "Remote", "Remotive", 9, True),
        ("https://www.arbeitnow.com/jobs/companies/sample/analyst-intern-100006", f"{p} Working Student", "Sample AG", "Munich", "Arbeitnow", 15, False),
    ]
    vacancies = [
        Vacancy(url=u, title=t, company=c, location=loc, source=s, posted_at=ago(d), salary=None,
                snippet=f"Demo vacancy: work with data and reports as {p}.", remote=r)
        for u, t, c, loc, s, d, r in rows
    ]
    return SearchResult(vacancies=vacancies, sources_ok=["dou", "remotive", "arbeitnow"],
                        sources_failed=[], deep_links=deep_links(q))
