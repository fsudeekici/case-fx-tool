# fx-tool

A small HTTP service that converts an amount between two currencies using the
public [Frankfurter API](https://frankfurter.dev) (ECB reference rates), meant
to be called by an AI agent as a tool.

## Running it

```bash
pip install -r requirements.txt   # fastapi, uvicorn, httpx
./run.sh                          # listens on $PORT, default 8080
```

## Running the tests

```bash
pip install pytest
./test.sh
```

Tests never touch the network: `httpx.AsyncClient`'s transport is swapped for
a `httpx.MockTransport` in every test, so `./test.sh` passes even with
`FX_UPSTREAM_BASE` pointing at a closed port.

## Endpoint
GET /tools/convert?amount=250&from=EUR&to=TRY&date=2026-08-28


`date` is optional; omitting it uses the latest published rates.

### Success — 200

```json
{
  "amount": 250,
  "from": "EUR",
  "to": "TRY",
  "rate": 47.12,
  "result": 11780.0,
  "rate_date": "2026-08-28",
  "asked_date": "2026-08-28",
  "source": "ECB via frankfurter.dev"
}
```

`asked_date` is what was requested. `rate_date` is the date the rate actually
belongs to. **They can differ** — see "Weekends and holidays" below — and the
response always shows both rather than silently substituting one for the
other.

### Failure — non-2xx

```json
{ "error": "<short_machine_code>", "message": "<a sentence a person could read>" }
```

| `error` code | When | HTTP status |
|---|---|---|
| `invalid_amount` | `amount` is missing, 0, negative, or has more than 2 decimal places | 400 |
| `invalid_currency` | a currency code isn't a 3-letter code, or the provider doesn't recognize it | 400 |
| `invalid_date` | date is in the future, or before 1999-01-04 (series start) | 400 |
| `upstream_unavailable` | couldn't reach the provider, or it returned a 5xx | 502 |
| `upstream_error` | the provider's response wasn't valid JSON | 502 |
| `rate_not_found` | the provider answered but has no rate for that pair | 404 |

The service never invents a rate. Any failure to get a real, dated rate from
the upstream returns an error instead of a number.

## How each edge case is handled

- **Weekends and holidays** (no rate published for the asked date): Frankfurter
  itself falls back to the most recent published date and reports that date.
  We pass that date through as `rate_date`, unchanged, while `asked_date`
  keeps the original request — so a caller can never mistake a Friday's rate
  for a Saturday's.
- **Future date / before 1999-01-04**: rejected before any upstream call, with
  `invalid_date`.
- **Unknown currency code / `from` == `to`**: a malformed code (not 3 letters)
  is rejected immediately. A code the provider itself doesn't recognize is
  translated into `invalid_currency` from its response. `from == to` is
  treated as a valid, trivial request — we answer `rate: 1.0` directly without
  calling the upstream at all.
- **Upstream slow / 500 / non-JSON**: a connection error or 5xx becomes
  `upstream_unavailable`; an unparsable body becomes `upstream_error`. Both
  are 502s and never contain a fabricated rate.
- **Bad `amount`**: missing, ≤ 0, or more precise than 2 decimal places is
  rejected as `invalid_amount`.

## Caching

Repeating the same `(from, to, date)` question is answered from an in-process
dict instead of re-asking the upstream. The cache is per-process and
unbounded — fine for this exercise, not for a long-running production
service (see `NOTES.md`).