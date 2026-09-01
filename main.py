"""Currency conversion tool for the agent runtime.

Wraps the public ECB feed at frankfurter.dev so an agent can answer questions
like "how much is 250 EUR in TRY".

Run:  ./run.sh   (or: uvicorn main:app --reload)
"""

from __future__ import annotations

import os
from datetime import date

import httpx
from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse

UPSTREAM_BASE = os.environ.get("FX_UPSTREAM_BASE", "https://api.frankfurter.dev")
PORT = int(os.environ.get("PORT", "8080"))

# Frankfurter's ECB series starts on this date; anything earlier has no data.
SERIES_START = date(1999, 1, 4)

app = FastAPI(title="fx-tool", version="1.0")

client = httpx.AsyncClient(base_url=UPSTREAM_BASE, timeout=5.0)

# Simple in-process cache: same (from, to, date) question is answered from
# memory instead of hitting the upstream API again.
_cache: dict[tuple[str, str, str], dict] = {}


def error(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": code, "message": message})


def is_valid_amount(amount: float) -> bool:
    if amount <= 0:
        return False
    cents = round(amount * 100)
    return abs(amount * 100 - cents) < 1e-6


def is_valid_currency_code(code: str) -> bool:
    return len(code) == 3 and code.isalpha() and code.isupper()


@app.get("/tools/convert")
async def convert(
    amount: float = Query(...),
    from_: str = Query(..., alias="from"),
    to: str = Query(...),
    date_param: date | None = Query(None, alias="date"),
):
    asked_date = date_param
    from_code = from_.upper()
    to_code = to.upper()

    if not is_valid_amount(amount):
        return error(
            400,
            "invalid_amount",
            "Amount must be a positive number with at most 2 decimal places.",
        )

    if not is_valid_currency_code(from_code) or not is_valid_currency_code(to_code):
        return error(
            400,
            "invalid_currency",
            "Currency codes must be 3-letter ISO codes, e.g. EUR, TRY.",
        )

    today = date.today()
    if asked_date is not None:
        if asked_date > today:
            return error(400, "invalid_date", "The date cannot be in the future.")
        if asked_date < SERIES_START:
            return error(
                400,
                "invalid_date",
                f"No rates are published before {SERIES_START.isoformat()}.",
            )

    # Same-currency shortcut: no need to ask the upstream at all.
    if from_code == to_code:
        result_date = (asked_date or today).isoformat()
        return {
            "amount": amount,
            "from": from_code,
            "to": to_code,
            "rate": 1.0,
            "result": round(amount, 2),
            "rate_date": result_date,
            "asked_date": result_date,
            "source": "ECB via frankfurter.dev",
        }

    cache_key = (from_code, to_code, asked_date.isoformat() if asked_date else "latest")
    if cache_key in _cache:
        cached = _cache[cache_key]
        return {**cached, "amount": amount, "result": round(amount * cached["rate"], 2)}

    path = asked_date.isoformat() if asked_date else "latest"
    try:
        response = await client.get(f"/v1/{path}", params={"base": from_code, "symbols": to_code})
    except httpx.RequestError:
        return error(
            502,
            "upstream_unavailable",
            "Could not reach the exchange rate provider. Please try again.",
        )

    if response.status_code >= 500:
        return error(
            502,
            "upstream_unavailable",
            "The exchange rate provider is currently unavailable.",
        )
    if response.status_code >= 400:
        return error(
            400,
            "invalid_currency",
            "One of the currency codes is not recognized by the rate provider.",
        )

    try:
        payload = response.json()
    except ValueError:
        return error(
            502,
            "upstream_error",
            "The exchange rate provider returned an unreadable response.",
        )

    rates = payload.get("rates", {})
    if to_code not in rates:
        return error(
            404,
            "rate_not_found",
            f"No rate is available for {from_code} to {to_code}.",
        )

    rate = rates[to_code]
    actual_rate_date = payload.get("date", (asked_date or today).isoformat())

    result_payload = {
        "from": from_code,
        "to": to_code,
        "rate": rate,
        "rate_date": actual_rate_date,
        "asked_date": (asked_date or today).isoformat(),
        "source": "ECB via frankfurter.dev",
    }
    _cache[cache_key] = result_payload

    return {
        **result_payload,
        "amount": amount,
        "result": round(amount * rate, 2),
    }


@app.get("/health")
async def health() -> dict:
    return {"ok": True}