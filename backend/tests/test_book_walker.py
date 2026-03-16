"""Tests for the order book walker."""

from app.execution.book_walker import walk_book


def test_full_fill_from_single_level():
    book = {
        "asks": [{"p": "0.52", "s": "100"}],
        "bids": [{"p": "0.48", "s": "100"}],
    }
    result = walk_book(book, "BUY", 50.0)
    assert result.filled_size == 50.0
    assert result.unfilled_size == 0.0
    assert abs(result.wavg_fill_price - 0.52) < 0.001
    assert not result.is_partial


def test_partial_fill_insufficient_depth():
    book = {
        "asks": [{"p": "0.52", "s": "30"}, {"p": "0.53", "s": "10"}],
        "bids": [],
    }
    result = walk_book(book, "BUY", 100.0)
    assert result.is_partial
    assert result.filled_size == 40.0
    assert result.unfilled_size == 60.0


def test_multi_level_wavg():
    book = {
        "asks": [{"p": "0.50", "s": "50"}, {"p": "0.55", "s": "50"}],
        "bids": [],
    }
    result = walk_book(book, "BUY", 100.0)
    assert abs(result.wavg_fill_price - 0.525) < 0.01
    assert result.levels_consumed == 2


def test_staleness_haircut():
    book = {"asks": [{"p": "0.50", "s": "100"}], "bids": []}
    result = walk_book(book, "BUY", 80.0, staleness_haircut=0.3)
    assert result.filled_size == 70.0  # 100 * 0.7 = 70
    assert result.unfilled_size == 10.0


def test_empty_book():
    result = walk_book({"asks": [], "bids": []}, "BUY", 50.0)
    assert result.filled_size == 0
    assert result.is_partial
