# rtings-mcp

> ### ⚠️ This is not a paywall bypass
> RTINGS gates some test values and scores server-side. This server reads only what your own
> session is already entitled to. On the categories that enforce the paywall, gated values come
> back `null` without a membership — the tool cannot unlock them, and it never estimates,
> interpolates or infers them. If you want those numbers, you still have to pay RTINGS and sign in.

An MCP server that exposes [RTINGS.com](https://www.rtings.com) test data as structured tools, so an
agent can consult it the way it consults any other data source — 0–10 scores, scalar measurements
with units, specs, and the measurement curves behind them. You run it locally; nothing is hosted,
nothing is shared.

**It's built for members and non-members alike.** RTINGS' JSON API is keyless and public; what it
returns anonymously is a lot, and what it withholds is withheld server-side. Sign in and you get
more on the categories that gate; don't, and you still get everything RTINGS serves openly.

> **Status: the anonymous server works.** All seven tools run against the live API.
> **Member mode is built but switched off** — whether a membership cookie actually unblurs the
> JSON API is still unverified, so `RTINGS_MEMBER_MODE` defaults to `false` and every cached
> file is written at the `anonymous` tier until a bought membership settles it.
> [`SPEC.md`](SPEC.md) is the design of record and [`RECON.md`](RECON.md) the measured evidence.

## What you get without paying

**Anonymous is a first-class mode — and for most categories it's the whole product.** An anonymous
sweep of all 28 categories ([`RECON.md` §11](RECON.md)) found the rule:

```
blurred  ⇔  product.published == false          (Early Access — an Insider perk, not the category paywall)
         ∨  (test.insider_only  ∧  the CATEGORY enforces the paywall)
```

**Enforcement is per-category and binary — 12 of 28 enforce, 16 do not.**

| | Categories | Anonymous gets |
|---|---|---|
| **Open (16)** | mattress, vacuum, air-purifier, air-fryer, refrigerator, microwave, toaster, toaster-oven, air-conditioner, dehumidifier, humidifier, blender, vpn, keyboard-switch, camera, running-shoes | test values, scores, ranking and comparison — the numbers are simply served |
| **Gated (12)** | tv, headphones, monitor, mouse, keyboard, soundbar, speaker, printer, laptop, robot-vacuum, projector, router | catalog, schema, search, **RTINGS' written verdicts, pros and cons**, review prose, ranked best-of lists, published curve data, and a handful of public spec fields with their scores |

Every category reports `has_paywall: true`, so that flag tells you nothing — the split is only visible
by observing what comes back. **It is a dated snapshot**: what decides it is unknown, it will change,
and the server never hardcodes it. `rt_silos()` reports what your machine actually observed, and the
map is re-scanned before each release against
[`docs/enforcement-snapshot.json`](docs/enforcement-snapshot.json) (baseline scanned 2026-09-03,
re-verified 2026-09-04: 12/16, unchanged).

## Honesty guarantees

Gated values come back `null`, never missing, with a typed output schema and structural `auth_state`
/ `data_tier` / `scores_available` fields. Every row says *why* it has no value — measured but
hidden, not applicable to this product, not tested, published only to Insiders as Early Access, or
outside what the server can show it has fetched. **An agent can never mistake "no member session"
for "RTINGS did not test this."**

## Install

```bash
git clone https://github.com/Averyy/rtings-mcp && cd rtings-mcp
uv venv && uv pip install -e .
```

Register it with your MCP client by absolute path, e.g. for Claude Code:

```bash
claude mcp add rtings -- uv --directory /absolute/path/to/rtings-mcp run rtings-mcp
```

No configuration is needed to start: anonymous is the default and never an error.

## Tools

| Tool | Returns |
|---|---|
| `rt_silos()` | the 28 categories with **observed** paywall enforcement |
| `rt_schema(silo, bench?, group?)` | test/usage definitions: name, kind, unit, hierarchy, `insider_only` |
| `rt_ratings(silo, tests?, usages?, filters?, sort?, limit?)` | catalog + 0–10 usage scores, with an optional scalar projection |
| `rt_product(product)` | one review: test results, plus RTINGS' verdicts and pros/cons with `include_verdicts` |
| `rt_graph(product, test)` | one test's measurement curve, resampled by selecting shipped points |
| `rt_search(query)` | model name/number → candidates across all categories |
| `rt_recommendations(silo, list?)` | the category's best-of lists, or one ranked list with reasoning |

Results are compared within a **test bench** — RTINGS versions its methodology, so cross-bench
results are nested rather than flattened into one ranking, and `limit` applies within each.
`filters` accepts a test's id or name plus `brand`, `name_contains`, `published` and
`variant` — the size RTINGS tested, which is how you ask for 65-inch TVs (most categories
have no "Size" test). A filter or sort on a field that is gated
for the rows in hand is **not applied**, and the response says so: an empty result there would read
as "no product qualifies" when the truth is "you cannot see it".

**On a gated category, ask for the verdicts.** `rt_product(url, include_verdicts=true)`
returns RTINGS' per-usage judgement in their own words — *"The Sony X90L is decent for gaming.
It has good enough black levels that it looks good in a dark room…"* — plus their pros and
cons and how each usage score is composed. Those are served even where every measurement
comes back null, so on the 12 gated categories they are the substantive answer.

⚠️ **`rt_product` can cost you something.** On a free account it hits the endpoint RTINGS meters, so
each new review spends one of your limited previews. It refuses by default and requires an explicit
`consume_preview=true`, reports how many remain, and never silently re-fetches a cached review.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `RTINGS_SESSION_COOKIE` | unset | your session cookie (see below) |
| `RTINGS_CACHE_DIR` | `~/.cache/rtings-mcp` | cache location |
| `RTINGS_CACHE_MAX_MB` | `1024` | cache ceiling, LRU-evicted |
| `RTINGS_ENABLE_GRAPH` | `true` | set `false` to disable `rt_graph` entirely |
| `RTINGS_RATE_INTERVAL_S` | `2.0` | seconds per request to rtings.com (a refill rate, not a floor) |
| `RTINGS_RATE_BURST` | `5` | requests available immediately after an idle period |
| `RTINGS_MAX_PREVIEW_SPEND` | `1` | metered previews `rt_product` may spend per run; `0` forbids it |
| `RTINGS_CONFIG_DIR` | `~/.config/rtings-mcp` | where the credential is stored |
| `RTINGS_CACHE_TTL_DAYS` | `30` | TTL for measurements and reviews; routing data has its own shorter clocks |
| `RTINGS_CONCURRENCY` | `1` | in-flight requests to rtings.com |
| `RTINGS_GRAPH_MAX_POINTS` | `200` | default curve resampling target |
| `RTINGS_CDN_RATE_INTERVAL_S` / `RTINGS_CDN_RATE_BURST` | `0.25` / `10` | the asset CDN's own budget |
| `RTINGS_TELEMETRY` | `true` | append header-only request records to `telemetry/requests.jsonl` |
| `RTINGS_MEMBER_MODE` | `false` | enables member-tier caching once a membership has been verified |
| `RTINGS_SESSION_OVERRIDE` | unset | `member`/`free`/`anonymous` — assert your own tier if the probe reads it wrong |

## Signing in (optional — only adds anything on the 12 gated categories)

You supply your own RTINGS session cookie. **No password is ever requested and login is never
automated.**

`_rtings_session` is **HttpOnly**, so `document.cookie` cannot read it and **"Copy as cURL" is the
only way to capture it**: open rtings.com logged in → DevTools → Network → right-click any request →
Copy → Copy as cURL, then:

```bash
.venv/bin/rtings-mcp auth     # paste, press Ctrl-D; it validates and tells you what you have
```

What the server does with it: stores it `0600` in your config dir, sends it only to `www.rtings.com`
(never the asset CDN), never writes it to the cache, never logs it, never returns it in tool output.
It is a long-lived credential granting access to your account — treat the file accordingly, and
revoke by logging out of RTINGS. `rtings-mcp auth --status` shows the current session;
`--clear` removes the stored copy.

**You should not have to do this again.** RTINGS re-issues the session cookie on every
response with a fresh 30-day expiry, so it is a sliding idle window rather than a deadline
from login. The server persists each re-issued value — but only one that came back with your
account actually signed in, never one minted for an anonymous request. In practice the
credential lasts as long as you keep using it, and lapses 30 days after you stop. (The
`RTINGS_SESSION_COOKIE` env var cannot be refreshed this way, so prefer `rtings-mcp auth` for
anything long-running.)

Running an automated client against your own logged-in account is your call and carries whatever
obligations your account does. The server is cache-first, rate-limited, and does no bulk crawling.

## Development

```bash
uv pip install -e ".[dev]"
.venv/bin/pytest tests/ -q                 # offline (the default; no network)
.venv/bin/pytest -m live -q                # live, anonymous, against the real API
.venv/bin/pytest -m "live and slow" -q -s  # + the 28-category enforcement re-scan
.venv/bin/ruff check src/ tests/
```

## Legal

MIT licensed ([LICENSE](LICENSE)). Not affiliated with or endorsed by RTINGS.com.

This is a **client**, not a scraper or a bypass. It calls the same keyless public JSON API that
rtings.com's own front end calls, reads only what that API returns to the caller, and never attempts
to reveal anything the server withholds. All test data and measurements are RTINGS' own, subject to
their [Terms of Use](https://www.rtings.com/company/terms-of-use). Cache locally, don't redistribute
it.
