"""Shared test fixtures for the demo platform."""

import pytest


@pytest.fixture
def sample_book():
    """Sample order book for execution tests."""
    return {
        "asks": [
            {"p": "0.50", "s": "200"},
            {"p": "0.51", "s": "150"},
            {"p": "0.52", "s": "100"},
            {"p": "0.53", "s": "50"},
        ],
        "bids": [
            {"p": "0.49", "s": "200"},
            {"p": "0.48", "s": "150"},
            {"p": "0.47", "s": "100"},
        ],
    }


@pytest.fixture
def starting_bankroll():
    return 900.0
