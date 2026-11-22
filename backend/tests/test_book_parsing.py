"""Best bid/ask from a Polymarket book snapshot.

Polymarket sends levels worst-to-best: bids ascending, asks descending.
Taking the first level returned 0.001 / 0.999 (a 0.5 midpoint) for every
token. Shapes below are copied from a live `GET /book` response (2026-11-22).
"""

from server.clob_websocket import best_book_price

BIDS = [
    {"price": "0.001", "size": "1566.03"},
    {"price": "0.03", "size": "200"},
    {"price": "0.045", "size": "50"},
]
ASKS = [
    {"price": "0.999", "size": "25010.17"},
    {"price": "0.06", "size": "100"},
    {"price": "0.046", "size": "510"},
]


def test_best_bid_is_highest():
    assert best_book_price(BIDS, "bid") == 0.045


def test_best_ask_is_lowest():
    assert best_book_price(ASKS, "ask") == 0.046


def test_order_independent():
    assert best_book_price(list(reversed(BIDS)), "bid") == 0.045
    assert best_book_price(list(reversed(ASKS)), "ask") == 0.046


def test_list_levels():
    assert best_book_price([["0.2", "10"], ["0.4", "5"]], "bid") == 0.4


def test_empty_and_garbage():
    assert best_book_price([], "bid") is None
    assert best_book_price(None, "ask") is None
    assert best_book_price([{"size": "1"}, ["x"]], "bid") is None
