"""Tests for CSV import parsing."""
from app.csv_import import parse_csv


def test_fidelity_style_csv():
    text = """Symbol,Quantity,Cost
AAPL,10,1500
VOO,5,2000"""
    result = parse_csv(text)
    assert ("AAPL", 10.0) in result
    assert ("VOO", 5.0) in result


def test_vanguard_style():
    text = """Ticker,Shares
BND,100.5
GLD,20"""
    result = parse_csv(text)
    assert ("BND", 100.5) in result


def test_handles_commas_in_quantity():
    text = """Symbol,Quantity
AAPL,"1,000" """
    result = parse_csv(text.strip())
    assert ("AAPL", 1000.0) in result


def test_skips_blank_and_invalid_rows():
    text = """Symbol,Quantity
AAPL,10

BAD_LINE_NO_COMMA
VOO,abc
MSFT,5"""
    result = parse_csv(text)
    tickers = [r[0] for r in result]
    assert "AAPL" in tickers and "MSFT" in tickers
    assert "VOO" not in tickers  # invalid quantity


def test_no_header_falls_back_to_first_two_columns():
    text = "AAPL,10\nMSFT,5"
    result = parse_csv(text)
    assert ("AAPL", 10.0) in result
    assert ("MSFT", 5.0) in result
