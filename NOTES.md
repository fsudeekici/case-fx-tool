# Notes
## Decisions
Frankfurter automatically falls back to the most recent published rate when
ECB has nothing for the asked date. I kept asked_date and rate_date as two
separate fields instead of merging them so the caller always knows which
date the rate actually belongs to. This was the part I wanted to get right
the most.

If from and to are the same currency I don't call the upstream at all and
I don't treat it as an error. It's a trivial valid request so I just return
rate 1.0 directly. This way we don't bother the upstream and we don't show
the customer an unnecessary error.

For dates I reject anything in the future or before 1999-01-04 (where
Frankfurter's series starts) before touching the upstream.

Every upstream failure is caught separately instead of one generic except.
A broken connection, a 5xx from the provider, a response that isn't valid
JSON and a response missing the requested rate all get their own error
code. None of them produce a fabricated number. The rule I followed
throughout was: no rate is better than a wrong rate.

I added an in-process cache keyed by from, to and date. Asking the same
question twice doesn't hit the upstream again.

For amount I reject anything zero or negative or with more than 2 decimal
places. A currency amount doesn't need more precision than that and it
protects against garbage input.

## With another day
I would rank these by whether it shows the customer a wrong answer
first, then whether it breaks the service in a visible way, and last
whether it's just cleanup that doesn't hurt anyone.

1. The cache never refreshes on "latest" queries. If no date is given,
the cache key is just "latest", so a rate cached on Monday keeps being
returned days later even though the real current rate has already
changed. The customer asks for "today's rate" but silently gets an old
one, with no way to tell. This is a data correctness problem, and in a
currency API correctness comes first. I'd fix it by including today's
date in the cache key, so "latest" becomes "latest:2026-09-02" and
refreshes automatically every day.

2. No logging or monitoring. This doesn't hit the customer immediately,
but it hurts them indirectly: without logs you can't tell how often the
upstream is failing, how many invalid currency requests are coming in,
or whether latency is creeping up. If you can't see a problem, you
can't operate the service reliably, and other real issues go unnoticed
longer.

3. Currency code validation only checks format, not a real list. My
first idea was calling Frankfurter's /v1/currencies endpoint per
request, but that adds latency and another point of failure for every
single call. A better approach: fetch the supported currency list once
at startup, cache it locally, refresh it periodically, and validate
against that cache. That way an invalid code like XYZ gets a clean 400
without ever touching the upstream.

4. No upper limit on amount. Lowest priority. A large amount can be a
completely legitimate request, so I wouldn't hardcode an arbitrary
limit like 10 million without a real business requirement. Until that
requirement exists, I'd still add a configurable MAX_AMOUNT (read from
an env variable, defaulting to something very high) mainly to guard
against floating-point overflow on extreme inputs, not to police normal
usage. amount > MAX_AMOUNT → 400, and the actual number stays a
business decision.

## AI tools
I used Claude for most of the coding — writing main.py step by step, one
concern at a time (skeleton, then validation, then date handling, then
upstream error handling, then caching), reviewing and testing each piece
before moving to the next rather than accepting a full implementation at
once. I used ChatGPT separately to double check some Frankfurter API
details (the date field behaviour, the series start date).

I didn't just accept what was suggested. When it came up, I decided
against returning an error for from==to — I wanted that to be a valid,
trivial request instead of something the customer sees as a failure.

For the ranking in "With another day", the first draft mixed all four
issues at the same level. I reordered them myself based on what actually
reaches the customer versus what's just an operational or cleanup
concern, and pushed logging/monitoring higher than the first suggestion
because an invisible problem in production still hurts the customer
indirectly.

## One thing the AI got wrong
I didn't catch the AI making a clear mistake in the code it helped me
write — I reviewed each piece (validation, date handling, error codes,
caching) as it was added and tested it before moving to the next part,
so mistakes got caught during that process rather than surfacing later
as bugs.

What I verified with tests rather than just assuming: that every failure
path (connection error, 5xx, bad JSON, missing rate) returns the fixed
error shape with no "rate" or "result" field at all — I wrote a separate
test for each path asserting on the error code, not just the status. I
also wrote a dedicated test for the weekend fallback case, asserting
asked_date and rate_date come back as two different values instead of
being silently merged.