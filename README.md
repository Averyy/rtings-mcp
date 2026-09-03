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

> **Status: design complete, no code yet.** Nothing here is installable.
> [`SPEC.md`](SPEC.md) is the design of record and [`RECON.md`](RECON.md) the measured evidence.
> **Member mode is unverified** — whether a membership cookie actually unblurs the JSON API is an
> open question, settled in Phase 0 before any member code ships.

## What you get without paying

**Anonymous is a first-class mode — and for most categories it's the whole product.** An anonymous
sweep of all 28 categories on 2026-09-03 ([`RECON.md` §11](RECON.md)) found the rule:

```
blurred  ⇔  product.published == false          (review in progress — not a paywall gate)
         ∨  (test.insider_only  ∧  the CATEGORY enforces the paywall)
```

**Enforcement is per-category and binary — 12 of 28 enforce, 16 do not.**

| | Categories | Anonymous gets |
|---|---|---|
| **Open (16)** | mattress, vacuum, air-purifier, air-fryer, refrigerator, microwave, toaster, toaster-oven, air-conditioner, dehumidifier, humidifier, blender, vpn, keyboard-switch, camera, running-shoes | test values, scores, ranking and comparison — the numbers are simply served |
| **Gated (12)** | tv, headphones, monitor, mouse, keyboard, soundbar, speaker, printer, laptop, robot-vacuum, projector, router | catalog, schema, search, review prose, ranked best-of lists, published curve data, and a handful of public spec fields with their scores |

Every category reports `has_paywall: true`, so that flag tells you nothing — the split is only visible
by observing what comes back. **It is a dated snapshot**: what decides it is unknown, it will change,
and the server never hardcodes it. It's re-scanned before each release against
[`docs/enforcement-snapshot.json`](docs/enforcement-snapshot.json).

Scope, so you can judge it: measured on each category's **current** test bench and its first 40–50
numeric/word tests. Older benches and the per-review path are unmeasured. Usage ratings
(`table_tool__ratings`) were not swept on the open categories — assume gated there.

## Honesty guarantees

Gated values come back `null`, never missing, with a typed output schema and structural `auth_state`
/ `data_tier` / `scores_available` fields. Every row says *why* it has no value — measured but
hidden, not applicable to this product, not tested, review not yet published, or outside what was
fetched. **An agent can never mistake "no member session" for "RTINGS did not test this."**

## Tools

| Tool | Returns |
|---|---|
| `rt_silos()` | the 28 categories with observed paywall enforcement |
| `rt_schema(silo, bench?, group?)` | test/usage definitions: name, kind, unit, hierarchy, `insider_only` |
| `rt_ratings(silo, bench?, tests?, usages?, filters?)` | catalog + 0–10 usage scores, with an optional scalar projection |
| `rt_product(url\|id)` | one review: leaf test results by hierarchy, prose and media opt-in |
| `rt_graph(product, test)` | one test's measurement curve, resampled by selecting shipped points |
| `rt_search(query)` | model name/number → candidates across all categories |
| `rt_recommendations(silo, list?)` | the category's best-of lists, or one ranked list with reasoning |

Results are compared within a **test bench** — RTINGS versions its methodology, so cross-bench
results are nested rather than flattened into one ranking.

⚠️ **`rt_product` can cost you something.** On a free account it hits the endpoint RTINGS meters, so
each new review spends one of your limited previews. It refuses by default and requires an explicit
`consume_preview=true`, reports how many remain, and never silently re-fetches a cached review.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `RTINGS_SESSION_COOKIE` | unset | your session cookie (see below) |
| `RTINGS_CACHE_DIR` | `~/.cache/rtings-mcp` | cache location |
| `RTINGS_ENABLE_GRAPH` | `true` | set `false` to disable `rt_graph` entirely |
| `RTINGS_RATE_INTERVAL_S` | `2.0` | seconds per request to rtings.com |
| `RTINGS_MAX_PREVIEW_SPEND` | `1` | metered previews `rt_product` may spend per run; `0` forbids it |

## Signing in (optional — only adds anything on the 12 gated categories)

You supply your own RTINGS session cookie. **No password is ever requested and login is never
automated.**

`_rtings_session` is **HttpOnly**, so `document.cookie` cannot read it and **"Copy as cURL" is the
only way to capture it**: open rtings.com logged in → DevTools → Network → right-click any request →
Copy → Copy as cURL, and hand that to `rtings-mcp auth`, which keeps only the one cookie.

What the server does with it: stores it `0600` in your config dir, sends it only to `www.rtings.com`
(never the asset CDN), never writes it to the cache, never logs it, never returns it in tool output.
It is a long-lived credential granting access to your account — treat the file accordingly, and
revoke by logging out of RTINGS.

Running an automated client against your own logged-in account is your call and carries whatever
obligations your account does. The server is cache-first, rate-limited, and does no bulk crawling.

## Legal

MIT licensed ([LICENSE](LICENSE)). Not affiliated with or endorsed by RTINGS.com.

This is a **client**, not a scraper or a bypass. It calls the same keyless public JSON API that
rtings.com's own front end calls, reads only what that API returns to the caller, and never attempts
to reveal anything the server withholds. All test data and measurements are RTINGS' own, subject to
their [Terms of Use](https://www.rtings.com/company/terms-of-use). Cache locally, don't redistribute
it.
