import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


import pytest


@pytest.fixture(autouse=True)
def _fresh_streamlit_resources():
    """Every test starts with fresh cached resources (the in-memory database and its daily counters).

    Without this, reviews made by one test use up the shared daily limit of the next one, so the result
    depends on the order in which test files run.
    """
    import streamlit as st

    st.cache_resource.clear()
    yield
