# Review of tool.py

One page. Findings **ranked** — most harmful to a customer first.

For each finding: what is wrong, what it does to a customer (not to a linter),
and how you would verify it.

## 1.

The cache key has no date, so a stale rate gets labeled with the date the
customer actually asked for.

"""python
key = f"{base}-{target}"
if key in _cache:
    return _cache[key], str(on or date.today())
"""

What's wrong: the cache key only contains the currency pair (e.g.
`EUR-TRY`), never the date. Once a rate is cached for one date, a request
for a different date on the same pair still hits the cache, so the
upstream is never called again. The rate comes from whatever was cached
first, but the date label attached to it comes from the current request.
This affects `latest` queries too, not just dated ones: a rate cached on
Monday for "latest" is still returned on Thursday, now labeled with
Thursday's date.

What it does to a customer: a customer asking for the rate on a specific
date, or simply asking for today's rate, can silently get a rate from a
different day, labeled with the date they asked for. The response looks
entirely correct, so neither the customer nor the agent has an obvious
reason to question it.

How I'd verify it: code inspection alone establishes the bug — the key
is built from currency codes only, so it cannot distinguish between two
dates (or "latest" on two different days) for the same pair by
construction. I would still confirm it behaviorally: point the upstream
at a mock that counts calls, request the same pair twice — once with a
date, once as "latest" a few days apart — and check the call count stays
at 1 instead of 2.

## 2.

Query parameter names don't match the documented API.

"""python
async def convert(amount: float, from_: str = "EUR", to: str = "TRY",
                 on: date | None = None) -> dict:
"""

What's wrong: the brief's URL uses `from` and `date`
(`?amount=250&from=EUR&to=TRY&date=2026-08-28`), but the parameters here
are `from_` and `on`, with no alias set. FastAPI therefore does not map
`from` and `date` to these parameters.

What it does to a customer: since both parameters have defaults
(`from_="EUR"`, `on=None`), a correctly-formed request doesn't error — it
silently converts from EUR using today's rate, ignoring the currency and
date actually asked for, and still returns a normal 200. A different
question gets answered without any signal that it happened.

How I'd verify it: send the exact documented URL with a non-default
currency and a past date (e.g. `from=USD&date=2020-01-01`), then check
whether `from` and `rate_date` in the response reflect what was actually
asked for, or silently default to EUR and today.

## 3.

Any error returns a misleading zero rate as if the conversion succeeded.

"""python
except Exception as exc:
    return {"rate": 0.0, "result": 0.0, ...}
"""

What's wrong: one generic `except Exception` catches every kind of
failure (connection error, upstream 5xx, bad JSON) with no way to tell
them apart, and returns a normal-looking dict instead of raising an
error — so FastAPI answers 200 even when the conversion failed entirely.

What it does to a customer: the response presents `rate: 0.0` as a real
conversion result. It's somewhat less dangerous than #1 and #2 because a
zero value is implausible enough that a customer or agent is more likely
to question it — but it's still a wrong number shown as a successful
result.

How I'd verify it: point the upstream at a closed port, send a request,
and check whether the status is 200 (wrong) instead of a 4xx/5xx, with
`rate`/`result` at exactly 0.0.

## The one I would fix before shipping tonight

#1, the cache key ignoring the requested date. Both #1 and #2 are serious
silent correctness bugs, but #1 is worse in practice: once the cache is
filled, it keeps affecting every later request for that pair — both
dated and "latest" queries — rather than a single call. #2 breaks the
documented API contract, but the fix is narrow and local (adding an
alias); #1's bad data quietly persists and compounds until the process
restarts.

## Things that look suspicious but are fine

Being right about a non-issue is worth as much as finding a real defect.

The `/health` endpoint returns a static `{"ok": True}` without checking
the Frankfurter dependency. That looks incomplete, but it's reasonable
for a liveness check: its purpose is to show the process is running, not
that every dependency is healthy. A separate readiness check could cover
upstream availability if the deployment needed one — I wouldn't consider
this a defect on its own.
