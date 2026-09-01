"""Tests for /tools/convert.

No real network call is ever made: httpx.MockTransport intercepts every
request main.client tries to send and answers with canned JSON that mimics
the real Frankfurter API's response shape.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

import main

# --- A tiny fake Frankfurter -------------------------------------------

call_count = {"n": 0}


def fake_frankfurter(request: httpx.Request) -> httpx.Response:
    call_count["n"] += 1
    path = request.url.path  # e.g. /v1/2026-08-28 or /v1/latest
    params = request.url.params
    to_code = params.get("symbols")

    if to_code == "ZZZ":
        # Simulate the provider rejecting an unknown/unsupported code.
        return httpx.Response(404, json={"error": "not found"})

    if to_code == "XXX":
        return httpx.Response(500)

    if to_code == "YYY":
        return httpx.Response(200, content=b"<html>not json</html>")

    if path == "/v1/2026-08-29":  # a Saturday -> ECB published nothing
        # Frankfurter's real behaviour: falls back to the last published
        # date and reports that date in "date".
        return httpx.Response(
            200, json={"amount": 1.0, "base": "EUR", "date": "2026-08-28", "rates": {"TRY": 47.5}}
        )

    # Default: pretend the requested date has real published rates.
    requested_date = path.split("/")[-1]
    date_in_response = requested_date if requested_date != "latest" else "2026-08-28"
    return httpx.Response(
        200,
        json={"amount": 1.0, "base": params.get("base"), "date": date_in_response, "rates": {to_code: 47.12}},
    )


@pytest.fixture(autouse=True)
def mock_upstream(monkeypatch):
    call_count["n"] = 0
    mocked_client = httpx.AsyncClient(
        base_url=main.UPSTREAM_BASE, transport=httpx.MockTransport(fake_frankfurter)
    )
    monkeypatch.setattr(main, "client", mocked_client)
    main._cache.clear()
    yield


@pytest.fixture
def client_app():
    return TestClient(main.app)


# --- Happy path ----------------------------------------------------------


def test_basic_conversion(client_app):
    r = client_app.get("/tools/convert", params={"amount": 250, "from": "EUR", "to": "TRY", "date": "2026-08-28"})
    assert r.status_code == 200
    body = r.json()
    assert body["rate"] == 47.12
    assert body["result"] == 250 * 47.12
    assert body["rate_date"] == "2026-08-28"
    assert body["asked_date"] == "2026-08-28"


def test_no_date_uses_latest(client_app):
    r = client_app.get("/tools/convert", params={"amount": 100, "from": "EUR", "to": "TRY"})
    assert r.status_code == 200
    assert r.json()["rate"] == 47.12


# --- The core requirement: never hide a rate_date/asked_date mismatch ----


def test_weekend_falls_back_and_says_so(client_app):
    r = client_app.get("/tools/convert", params={"amount": 100, "from": "EUR", "to": "TRY", "date": "2026-08-29"})
    assert r.status_code == 200
    body = r.json()
    assert body["asked_date"] == "2026-08-29"
    assert body["rate_date"] == "2026-08-28"
    assert body["rate_date"] != body["asked_date"]


# --- Same currency shortcut: never calls the upstream ---------------------


def test_same_currency_short_circuits(client_app):
    r = client_app.get("/tools/convert", params={"amount": 100, "from": "EUR", "to": "EUR"})
    assert r.status_code == 200
    body = r.json()
    assert body["rate"] == 1.0
    assert body["result"] == 100.0
    assert call_count["n"] == 0


# --- Validation errors happen before touching the upstream ----------------


def test_future_date_rejected(client_app):
    r = client_app.get("/tools/convert", params={"amount": 100, "from": "EUR", "to": "TRY", "date": "2099-01-01"})
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_date"
    assert call_count["n"] == 0


def test_date_before_series_start_rejected(client_app):
    r = client_app.get("/tools/convert", params={"amount": 100, "from": "EUR", "to": "TRY", "date": "1990-01-01"})
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_date"
    assert call_count["n"] == 0


def test_malformed_currency_code_rejected(client_app):
    r = client_app.get("/tools/convert", params={"amount": 100, "from": "EU", "to": "TRY"})
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_currency"
    assert call_count["n"] == 0


def test_zero_amount_rejected(client_app):
    r = client_app.get("/tools/convert", params={"amount": 0, "from": "EUR", "to": "TRY"})
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_amount"


def test_negative_amount_rejected(client_app):
    r = client_app.get("/tools/convert", params={"amount": -50, "from": "EUR", "to": "TRY"})
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_amount"


def test_too_many_decimals_rejected(client_app):
    r = client_app.get("/tools/convert", params={"amount": 250.1234567891, "from": "EUR", "to": "TRY"})
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_amount"


# --- Upstream failure handling: never fabricate a rate ---------------------


def test_upstream_500_returns_502_not_a_fake_rate(client_app):
    r = client_app.get("/tools/convert", params={"amount": 100, "from": "EUR", "to": "XXX"})
    assert r.status_code == 502
    assert r.json()["error"] == "upstream_unavailable"


def test_upstream_non_json_returns_502(client_app):
    r = client_app.get("/tools/convert", params={"amount": 100, "from": "EUR", "to": "YYY"})
    assert r.status_code == 502
    assert r.json()["error"] == "upstream_error"


def test_unknown_currency_from_upstream(client_app):
    r = client_app.get("/tools/convert", params={"amount": 100, "from": "EUR", "to": "ZZZ"})
    assert r.status_code in (400, 404)
    assert r.json()["error"] in ("invalid_currency", "rate_not_found")


# --- Caching: repeating the same question must not re-ask the upstream ----


def test_repeated_question_uses_cache(client_app):
    params = {"amount": 100, "from": "EUR", "to": "TRY", "date": "2026-08-28"}
    r1 = client_app.get("/tools/convert", params=params)
    r2 = client_app.get("/tools/convert", params={**params, "amount": 50})
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert call_count["n"] == 1  # only the first call touched the upstream
    assert r2.json()["result"] == 50 * 47.12