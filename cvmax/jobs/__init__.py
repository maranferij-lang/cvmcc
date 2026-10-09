"""Job search: models, HTTP layer, providers, routing and LLM ranking."""
from __future__ import annotations

from cvmax.jobs.demo import demo_search_result
from cvmax.jobs.models import JobQuery, Vacancy
from cvmax.jobs.providers import deep_links
from cvmax.jobs.rank import rank_jobs
from cvmax.jobs.search import SearchResult, search_jobs

__all__ = ["JobQuery", "Vacancy", "SearchResult", "search_jobs", "rank_jobs",
           "demo_search_result", "deep_links"]
