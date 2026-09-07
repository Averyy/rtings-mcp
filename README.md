# rtings-mcp

> ### ⚠️ Not a paywall bypass
> RTINGS gates some test values and scores server-side. This server reads what your own session
> is already entitled to. On the categories that enforce the paywall, gated values come back
> `null` without a membership. The tool can't unlock them, and it never estimates, interpolates
> or infers them. For those numbers you have to pay RTINGS and sign in.

An MCP server that exposes [RTINGS.com](https://www.rtings.com) test data as structured tools:
0–10 scores, scalar measurements with units, specs, and the measurement curves behind them. It
runs locally. Nothing is hosted and nothing is shared.

RTINGS' JSON API is keyless and public, and a lot comes back anonymously. Signing in adds data on
the 12 categories that gate.

> **Status: working, anonymous and signed in.** All seven data tools run against the live API, and
> two more connect a membership.
> **A membership cookie does unblur the JSON API** — measured 2026-09-06 on a bought membership:
> 588 of 588 withheld rows came back with real values where anonymous got none
> ([`RECON.md`](RECON.md) §13). Member mode is on by default as of that measurement, so a
> signed-in session caches its rows under their own tier; `RTINGS_MEMBER_MODE=0` pins everything
> back to `anonymous`.
> [`SPEC.md`](SPEC.md) is the design of record and [`RECON.md`](RECON.md) the measured evidence.

## What anonymous gets

An anonymous sweep of all 28 categories ([`RECON.md` §11](RECON.md)) found the rule:

```
blurred  ⇔  product.published == false          (Early Access, an Insider perk separate from the category paywall)
         ∨  (test.insider_only  ∧  the CATEGORY enforces the paywall)
```

Enforcement is per-category. 12 of 28 gate outright. The other 16 give a session without a
membership a **three-review preview budget** ([`RECON.md` §14](RECON.md)): while it is unspent the
numbers are served, and after the third product review page the same session is gated like the
12. The budget is spent only by loading a review page as HTML, which this server never does (its
product drilldown is a JSON call that does not count), so for the server the 16 behave as full.

| | Categories | Anonymous gets |
|---|---|---|
| **Metered (16)** | mattress, vacuum, air-purifier, air-fryer, refrigerator, microwave, toaster, toaster-oven, air-conditioner, dehumidifier, humidifier, blender, vpn, keyboard-switch, camera, running-shoes | test values, scores, ranking and comparison. The numbers are served while the preview budget is unspent, which it always is for this server. |
| **Gated (12)** | tv, headphones, monitor, mouse, keyboard, soundbar, speaker, printer, laptop, robot-vacuum, projector, router | catalog, schema, search, **RTINGS' written verdicts, pros and cons**, review prose, ranked best-of lists, published curve data, and a handful of public spec fields with their scores |

Every category reports `has_paywall: true`, so that flag carries no information. Only the served
data shows the split (the per-category `access_level` on the landing page agrees with it). It will change, so the server never hardcodes
it; `rt_silos()` reports what your machine observed. The map is re-scanned before each release
against [`docs/enforcement-snapshot.json`](docs/enforcement-snapshot.json) (baseline 2026-09-03,
re-verified 2026-09-06: 12/16, unchanged — and re-verified *with* a membership stored, which the
scan ignores by construction, so the map stays an anonymous measurement).

## Honest nulls

Gated values come back `null` with a typed output schema and structural `auth_state` /
`data_tier` / `scores_available` fields. Every row says why it has no value: measured but hidden,
not applicable, not tested, Early Access, or outside what the server can show it has fetched. An
agent can't read "no member session" as "RTINGS did not test this."

## Install

From PyPI, with nothing to clone (`uvx` fetches and caches the package on first run):

```bash
claude mcp add rtings --scope user -- uvx rtings-mcp
```

For the in-conversation browser sign-in (`rt_sign_in`) install the `browser` extra instead:

```bash
claude mcp add rtings --scope user -- uvx --from "rtings-mcp[browser]" rtings-mcp
```

Or from a checkout, if you want to work on it:

```bash
git clone https://github.com/Averyy/rtings-mcp && cd rtings-mcp
uv venv && uv pip install -e .
claude mcp add rtings --scope user -- uv --directory /absolute/path/to/rtings-mcp run rtings-mcp
```

`--scope user` makes it available in every project. Drop it to scope the server to the current
directory. Verify with:

```bash
claude mcp get rtings      # expect: Status: ✔ Connected
```

A connected server keeps running the code it started with, so restart the session after editing
the source. `/reload-plugins` won't do it; it only handles servers bundled inside plugins.

No configuration is needed to start. Anonymous is the default and never an error.

## Tools

| Tool | Returns |
|---|---|
| `rt_silos(silos?)` | the 28 categories with **observed** paywall enforcement |
| `rt_schema(silo, bench?, group?, find?)` | test/usage definitions: name, kind, unit, hierarchy, `insider_only`; `find` searches the bench by name |
| `rt_ratings(silo, tests?, usages?, filters?, sort?, limit?)` | catalog + 0–10 usage scores, with an optional scalar projection; `filters.product_ids` for a head-to-head |
| `rt_product(product, group?, tests?)` | one review: test results (bounded by `group` or `tests`), plus RTINGS' verdicts and pros/cons with `include_verdicts` (`include_results=false` for the words alone) |
| `rt_graph(product, test)` | one test's measurement curve, resampled by selecting shipped points |
| `rt_search(query)` | model name/number → candidates across all categories |
| `rt_recommendations(silo, list?, limit?)` | the category's best-of lists, or one ranked list with reasoning |
| `rt_sign_in(force?)` | opens a browser window on RTINGS' sign-in page and stores the resulting cookie |
| `rt_auth_status(wait_s?)` | what credential is stored, and how a sign-in in progress is going |

Results are compared within a **test bench**, RTINGS' version of its methodology. Cross-bench
results come back nested in separate groups and `limit` applies within each.

`filters` and `sort` take a test's id or its name, plus `brand`, `name_contains`, `published` and
`variant`. `variant` is the size RTINGS tested, which is how you ask for 65-inch TVs, since most
categories have no "Size" test. A field you filter or sort on is fetched for you. When one can't be
compared the predicate is **not applied** and the response names why: gated for this session,
absent from the bench queried, or measured-but-empty. Otherwise an empty result reads as "no
product qualifies" when the truth is "you can't see it". Rows with no comparable value sort last in
both directions.

**On a gated category, ask for the verdicts.** `rt_product(url, include_verdicts=true)` returns
RTINGS' per-usage judgement in their own words, their pros and cons, and how each usage score is
composed. Those come through even where every measurement is null.

**A free RTINGS account adds nothing over anonymous** — measured ([`RECON.md` §14.6](RECON.md)):
it has no preview budget on the gated categories and the same three-review meter on the others.
`rt_product` keeps a budget guard in case RTINGS ever meters the API for free accounts (it arms
only when the site reports a limit, and then requires an explicit `consume_preview=true`), but
today nothing it calls is metered.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `RTINGS_SESSION_COOKIE` | unset | your session cookie (see below) |
| `RTINGS_CACHE_DIR` | `~/.cache/rtings-mcp` | cache location |
| `RTINGS_CACHE_MAX_MB` | `1024` | cache ceiling, LRU-evicted |
| `RTINGS_ENABLE_GRAPH` | `true` | set `false` to disable `rt_graph` entirely |
| `RTINGS_RATE_INTERVAL_S` | `2.0` | seconds per token refilled into the rtings.com request budget |
| `RTINGS_MIN_REQUEST_INTERVAL_S` | — | **deprecated**; pins `RTINGS_RATE_INTERVAL_S` and forces burst 1, i.e. the old flat interval. Warns when set |
| `RTINGS_RATE_BURST` | `5` | requests available immediately after an idle period |
| `RTINGS_MAX_PREVIEW_SPEND` | `1` | metered previews `rt_product` may spend per run; `0` forbids it |
| `RTINGS_CONFIG_DIR` | `~/.config/rtings-mcp` | where the credential is stored |
| `RTINGS_CACHE_TTL_DAYS` | `30` | TTL for measurements and reviews; routing data has its own shorter clocks |
| `RTINGS_CONCURRENCY` | `1` | in-flight requests to rtings.com |
| `RTINGS_GRAPH_MAX_POINTS` | `200` | default curve resampling target |
| `RTINGS_MAX_RESPONSE_CHARS` | `40000` | `rt_ratings` trims each group's page to fit this many characters on the wire and says where to page from |
| `RTINGS_CDN_RATE_INTERVAL_S` / `RTINGS_CDN_RATE_BURST` | `0.25` / `10` | the asset CDN's own budget |
| `RTINGS_TELEMETRY` | `true` | append header-only request records to `telemetry/requests.jsonl` |
| `RTINGS_MEMBER_MODE` | `true` | member-tier caching. Set `0` to pin every cached file to the `anonymous` tier — a signed-in session then serves its rows but cannot cache them |
| `RTINGS_SESSION_OVERRIDE` | unset | `member`/`free`/`anonymous` — assert your own tier if the probe reads it wrong |

## Signing in (optional, and only adds anything on the 12 gated categories)

You supply your own RTINGS session. **No password is ever requested and login is never automated** —
you type it into RTINGS' own page, and the server keeps the resulting cookie.

**In a conversation** (works in Claude Desktop and Claude Code):

> "sign me in to RTINGS"

That calls `rt_sign_in`, which opens a browser window on the sign-in page and answers straight
away; `rt_auth_status(wait_s=45)` waits for you to finish. It needs the browser extra:

```bash
uv sync --extra browser        # or: pip install "rtings-mcp[browser]"
```

**In a terminal**, the same capture without an MCP client:

```bash
.venv/bin/rtings-mcp auth --browser
```

**Without a browser at all**, paste one instead. `_rtings_session` is **HttpOnly**, so
`document.cookie` can't read it and "Copy as cURL" is the only way to get it by hand: open
rtings.com logged in → DevTools → Network → right-click any request → Copy → Copy as cURL, then:

```bash
.venv/bin/rtings-mcp auth     # paste, press Ctrl-D; it validates and tells you what you have
```

Every path validates the cookie against RTINGS before storing it. A session that doesn't come back
signed in is discarded, which matters more than it sounds: RTINGS hands anonymous visitors a
`_rtings_session` too, so "a cookie appeared" proves nothing on its own.

The cookie is stored `0600` in your config dir, sent only to `www.rtings.com`, never written to
the cache, never logged, never returned in tool output. It grants access to your account, so treat
the file accordingly and revoke it by logging out of RTINGS. `--status` shows the current session
and `--clear` removes the stored copy.

You shouldn't have to do this again. RTINGS re-issues the cookie on every response with a fresh
30-day expiry, so it's a sliding idle window rather than a deadline from login. The server persists
each re-issued value that came back with your account signed in, and discards the one an anonymous
request mints. `RTINGS_SESSION_COOKIE` can't be refreshed this way, so prefer `rtings-mcp auth` for
anything long-running.

Running an automated client against your own account is your call and carries whatever obligations
your account does. The server is cache-first, rate-limited, and does no bulk crawling.

## Development

```bash
uv pip install -e ".[dev]"
uv pip install -e ".[browser]"             # only to run the browser sign-in itself
.venv/bin/pytest tests/ -q                 # offline (the default; no network)
.venv/bin/pytest -m live -q                # live, anonymous, against the real API
.venv/bin/pytest -m "live and slow" -q -s  # + the 28-category enforcement re-scan
.venv/bin/ruff check src/ tests/
```

## Legal

MIT licensed ([LICENSE](LICENSE)). Not affiliated with or endorsed by RTINGS.com.

The server calls the same keyless public JSON API that rtings.com's own front end calls, reads
only what that API returns to the caller, and never attempts to reveal anything the server
withholds. All test data and measurements are RTINGS' own, subject to their
[Terms of Use](https://www.rtings.com/company/terms-of-use). Cache locally, don't redistribute it.
