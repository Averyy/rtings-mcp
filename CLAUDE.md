# rtings-mcp

MCP server exposing an RTINGS member's own subscription as structured test-data and ratings tools.
Public, open source. **Python `>=3.12`, developed on 3.14.** Built on the **official `mcp` SDK
(`mcp>=2.1.1`)** — **`from mcp.server.mcpserver import MCPServer`** — **not** the standalone
`fastmcp` 4.x package; they are separate diverged projects.

**IMPORTANT: the class is `MCPServer`, not `FastMCP`.** `mcp` 2.x renamed it;
`mcp.server.fastmcp` raises `ModuleNotFoundError` on 2.1.1. Tool schema attributes are
snake_case (`tool.input_schema` / `tool.output_schema`), and `outputSchema` comes from the
tool function's **return annotation** — which is why every tool returns a declared Pydantic
model: that is what carries the seven-state `status` enum into the client's schema.

**Status: the anonymous server is built and working** (2026-09-05). `RECON.md` §12 holds the
facts the build measured; the rules below marked "(corrected 2026-09-04)" are the ones it
forced, and those marked **2026-09-05** come from a seven-lens review round that found 15
defects — several of them reachable anonymously, on silos where nothing is gated. Member mode
is built but gated off behind `RTINGS_MEMBER_MODE`.

- `SPEC.md` — design of record. Read before proposing anything structural.
- `RECON.md` — measured facts about RTINGS' API, paywall and auth. Cite it; don't re-derive it.
  Everything in it is **anonymous-only**; the member side is unverified (`RECON.md` §10 q1).

**This is not Consumer Reports with the names changed.** The plumbing is cleaner (a keyless
parameterized JSON API). **Anonymous is a full data mode for 16 of the 28 silos** (measured
2026-09-03, `RECON.md` §11) — values, scores, ranking — and a catalog/prose/curve mode for the 12
flagship silos that enforce the paywall. An earlier "thin on numbers by design" framing was measured
on TVs and is wrong for most of the catalog. Rules below marked "(ported)" come from `consumer-reports-mcp` unchanged;
the rest are RTINGS-specific. **Only confirmed facts appear here** — anything unverified lives in
`RECON.md` §10, never as a rule.

## Architecture

**There is a real keyless JSON API — use it, do not scrape pages.** RTINGS is Rails + Vue.
Everything of value comes from `POST https://www.rtings.com/api/v2/safe/<query>` — confirmed
anonymous, no api-key/CSRF/cookie (`RECON.md` §1). The one page-extraction exception is
`rt_recommendations` (`RECON.md` §7), isolated with its own parser and drift alarm.

- **Discovery:** inline `var GLOBALS.static.silos` on any page lists all **28 silos** in one fetch,
  each with its **current** bench (`test_bench.{id,name,replaces_id}`). No crawl, no A-Z page. The
  per-silo `is_recent` bench set is *not* in `static.silos` — it costs one page fetch per silo
  (`RECON.md` §8).
- **The API is parameterized.** Request exactly the tests/usages/products you want; there is no
  forced category dump. `column_options` (the schema) is the one fixed ~357 KB payload, once per silo.
- **The API response carries no auth field** (`RECON.md` §1, confirmed) — true of the seven
  table/review queries, and **the one exception is `app/side_by_side__review`, which carries
  `user_has_access`** (`RECON.md` §12.16). Auth otherwise lives only in the HTML
  `GLOBALS.session`. See the auth-seam rule below.

## Critical rules

- **Anonymous is a first-class mode, never an error** (ported) — but be honest what it is: a
  *catalog, prose and curve* mode. It returns the full catalog, schema, search, review prose, ranked
  recommendations and **every curve RTINGS publishes** — but note curves are sparse: only `kind:
  "graph"` tests have one, 5 of 402 on TV bench 227 and **0 of 54** on bench 2 (`RECON.md` §4). On the
  **12 enforcing** silos it withholds the value *and* the score of every `insider_only` test; on the
  other **16 it withholds nothing** (`RECON.md` §11.1). Do not describe it as CR's anonymous tier, and
  do not say "all measurement curves" — say "every curve RTINGS publishes", which is far fewer.
- **IMPORTANT: the gate is `insider_only` AND per-silo enforcement — the flag alone is NOT the gate.**
  Corrected 2026-09-03 by a 41,944-row anonymous sweep (`RECON.md` §11.1). **tv, headphones, monitor,
  mouse, printer, laptop gate 100% of `insider_only` rows; air-purifier, mattress and vpn serve 100%
  of them UNBLURRED** — real measurements with scores, no `Lock` marker. All 28 silos are
  `has_paywall:true`, so that flag does not explain it. **Derive the boundary from OBSERVED
  `unblurred` per (silo, bench); `insider_only` marks a test gate-*able*, never gated.** A **public**
  test ships its value *and* a real 0–10 `score` anonymously. Never write "anonymous gets no scores":
  100%-gated is true only of *usage* ratings (`table_tool__ratings`), a different surface.
- **IMPORTANT: `published:false` is a SECOND, orthogonal blur — it is EARLY ACCESS, and a
  membership DOES lift it (corrected 2026-09-04, `RECON.md` §12.10).** RTINGS: "the writer
  publishes it for Early Access so that Insiders who support us can see the data without any
  text", and those products sit at `/early-access/{silo}/reviews/...`. So **check `unblurred`
  BEFORE the `published` flag** — branching on the flag first returns `review_unpublished,
  value:null` for a member's real Early Access values and throws them away. And the silo is
  the SECOND path segment on those URLs; deriving it from the first sent a lookup into search,
  which answered with a nine-year-old TV. An
  in-progress review is blurred on every test regardless of silo enforcement (`RECON.md` §11.2:
  camera's one blurred product is blurred on all 50 tests, `published:false`, while 74 others are
  unblurred on all 50). Calling that `tested_gated` tells the user to buy a membership for a review
  RTINGS has not finished. `published` ships in `products_list` — **check it before attributing any
  blur to the paywall**, and exclude those rows when deriving the (silo, bench) boundary.
- **The coverage check is STEP 0 of the normalizer, before the absent-row test.** Skipping it turns
  every product newer than the cached slice into a false `not_tested`. A product that cannot be shown
  covered yields **`coverage_unknown`** — an honest "I don't know", distinct from both `not_tested`
  and `tested_gated`, and it belongs in the `outputSchema` `status` enum.
- **IMPORTANT: an agent must never read "no member session" as "RTINGS did not test this."** The
  normalizer emits **seven** states — `tested_visible` / `tested_gated` / `not_applicable` /
  `not_tested` / `review_unpublished` / `coverage_unknown` / `unknown_row_status` — and never
  collapses two into one null. Most dangerous failure mode in the project (ported, and it covers
  far more of the data here). **It was four when first ported**; `review_unpublished` (Early
  Access), `coverage_unknown` (the Step-0 check) and `unknown_row_status` (the drift alarm) were
  each forced by a measurement, and each is documented in its own rule above. The core four
  (`RECON.md` §6, confirmed):
  - `status:"na"` ⇒ **`not_applicable`**. **Branch on `status` BEFORE `unblurred`** — proven
    necessary in BOTH directions (`RECON.md` §11.4): an `na` row with `unblurred:false` is identical
    to a gated row (⇒ false "buy a membership"), **and 1,025 of 2,186 `na` rows are `unblurred:true`**
    (47%) which via `unblurred` alone become **`tested_visible` with a null value** — "measured, and
    the answer is nothing." The old "all observed `na` are `unblurred:false`" was a TV-only artifact.
  - **`status:"untested"` is a REAL third value ⇒ `not_tested`** (154 rows: mattress 75, mouse 79).
    The table path emits **both** encodings — an absent row *and* an explicit `untested`. Mapping it
    to `unknown_row_status` raises a false drift alarm and fails to report `not_tested`.
  - **A visible row can be genuinely empty** — 2 rows came back `tested` + `unblurred:true` +
    `value:null` + `rendered_value:null`. `tested_visible` must tolerate a null value.
    An unrecognised `status` ⇒ `unknown_row_status` + warning, never `tested_visible`.
  - present row, `unblurred:false` ⇒ **`tested_gated`** → `value:null, gated:true`.
  - present row, `unblurred:true` ⇒ **`tested_visible`**.
  - **absent** `(product, test)` ⇒ **`not_tested`** — but only within a scope you have actually
    fetched, and only against the product's **own bench** (next rule).
- **The two fetch paths encode not-tested differently — normalize both to the same state.** On
  `table_tool__test_results` a not-tested pair is an **absent row**. On the review body **no row in
  the product's bench is ever absent** (54/54 and 402/402 observed); not-applicable appears as
  `status:"na"` instead. Row counts match the product's **own bench**, never the silo — so joining
  review rows against the full silo schema invents hundreds of false `not_tested`s (`RECON.md` §6).
- **The two paths return different row shapes.** The table row carries `value`; the review row has
  **no `value` key** — it carries `rendered_value` (an HTML string) plus `score`. Parse each shape
  explicitly; never write one accessor that assumes both (`RECON.md` §6).
- **The auth marker is HTML-only; the data is JSON-only — join them, never infer auth from null
  data.** `session` (credential health) comes from an HTML `GLOBALS.session` probe run at `auth` time
  and lazily per process. `data_tier` (a served row's tier) comes from the **data in the safe
  direction only**: an `unblurred:true` on an `insider_only` test proves the row was **unblurred for
  us**; absence proves nothing (`unproven`, never `anonymous`). `auth_state` is derived. The two can
  race; document it, never silently upgrade one from the other (`SPEC.md` §6).
- **`data_tier: unblurred` means "this row came through unblurred", NOT "the user is a member."**
  Unblurred data does not prove membership. **Phase-0 hypothesis, not a measured fact** (`RECON.md`
  §10 q2 is open): the free/metered preview is believed to unblur a *specific review* on the
  `rt_product` path, so a free account would yield `unblurred:true` on an `insider_only` test. The
  rule is conservative either way — if the hypothesis is wrong nothing breaks — but do not cite it as
  confirmed. Naming that tier `member` promotes a non-member to `auth_state: member` off a
  metered preview. Member-vs-free is decided by the **HTML probe only**, never by the data.
- **IMPORTANT: `max_retries` is a CONSTRUCTOR kwarg — "set it on that POST specifically" does not
  work.** `AsyncSession.request()` pops only `headers, params, timeout, attempt_timeout,
  max_response_size` (`wafer/_async.py:1323-1328`); `max_retries` lives on `BaseSession.__init__`
  (`_base.py:660, 766`) and is read from `self` (`_async.py:1419`). A per-request value is a
  `TypeError` at best, ignored at worst — and `page_body` would then run at the default 3 retries =
  **3 consumed previews**. So `max_retries=0` goes on the **API session**.
- **`rt_product` spends the user's metered previews — that needs a BUDGET, not a timer.** It *is*
  `app/product_vue_page__page_body`, the endpoint the server-side meter counts (`RECON.md` §10 q2).
  Spacing calls apart just spends previews more slowly; **count** is the right axis. On
  `session == free`: compute `remaining = access_limit - len(previewed_products)` from the probe and
  return `preview_exhausted` **without fetching** at zero; require an explicit `consume_preview=true`
  for a call that would spend one; never auto-refetch a past-TTL review (serve `stale:true`); hold the
  cross-process lock so two clients cannot double-spend; re-probe after. `RTINGS_MAX_PREVIEW_SPEND`
  defaults to `1` — **not `0`**, or `consume_preview=true` could never succeed. The meter's *unit* is
  still Phase 0 capture (i).
- **`data_tier` and `session` are separate envelope fields; `auth_state` is derived** (ported). A
  cached member row served after the session lapsed is member data *and* a dead session.
- **IMPORTANT: `auth_state` never claims `preview` (corrected 2026-09-04).** The derivation
  table used to map `(anonymous|free, unblurred)` to `preview` — "a share or gift link". That
  assumed every silo gates. It does not: on 16 of 28, unblurred `insider_only` values are
  what anonymous simply gets. `preview` there invents a grant nobody made, which is the same
  error as calling a metered preview `member`, pointing the other way. `(anonymous, *)` ⇒
  `anonymous`; `(free, *)` ⇒ `free`. `stale_member_data` survives — it says something the
  data alone cannot.
- **`scores_available` has a fifth value, `unknown` (added 2026-09-04)** — "this response did
  not query that surface". Reporting `gated` for a surface nobody asked about tells an agent
  it cannot see values that are, on most silos, served outright, and routes it away from the
  category that would have answered. Same distinction as `coverage_unknown` vs `not_tested`.
- **Best-of discovery is `silo_layout.best` in the landing page's `data-props`, not an href
  scan** — 20 lists on TV vs 5, because most list paths are **two segments**
  (`by-size/65-inch`). A single-segment slug pattern silently rejects the majority. A slug is
  validated then **flattened** into one filename component, never used as a path segment.
- **Review-path prose sits on `group` rows and survives the blur.** All 53 rows carrying a
  `linked_description` on the X90L are `kind:"group"`, all `unblurred:false`. A normalizer
  that (correctly) skips structure rows discards *all* of a review's prose unless it collects
  it separately — as commentary, **never** as a result with a `status`.
- **`has_insider_access` lives inside an HTML-escaped `data-props` attribute.** A raw-text
  regex never matches it and silently reports "the page does not carry this signal".
- **The blur is server-side and per value.** `scores_available` is an **object** of `available |
  gated | absent`, never a boolean, **derived from `insider_only` + observed `unblurred`** (`RECON.md`
  §6), never hardcoded. `insider_only` is a real session-independent schema flag. Derive the boundary
  per **(silo, bench)**, never per silo: the same silo gates differently across benches — TV bench
  227 exposes 6 public tests, TV bench 2 exposes 1 (`RECON.md` §2, confirmed). Deriving once per silo
  mislabels every legacy-bench product.
- **Never derive or estimate a gated value.** And note the raw signal *is* present: the gated scalar
  is a summary of a **public curve** (`RECON.md` §4). So the rule is positive — `rt_graph` returns a
  curve as shipped (resampled for size only), never a number that could read as an RTINGS measurement.
- **IMPORTANT: rank and compare WITHIN a `test_bench`, never flatten across** (RTINGS' methodology
  axis). Default to the **recent-bench set the site itself renders together**; a caller widening
  beyond it gets results **nested by bench**. `rank_scope: "within_bench"` always; no cross-bench
  "comparable" mode. This is a conservative default, **not** a measured incomparability
  (`RECON.md` §10 q7).
- **IMPORTANT: `latest_test_bench_id` is NOT the current bench (corrected 2026-09-04).** On
  **5 of 28 silos** it names a bench absent from `column_options` *and* absent from the
  `is_recent` set — air-conditioner 258 (recent: 39), air-fryer 265, laptop 285, router 269,
  toaster-oven 266 (`RECON.md` §12.4) — apparently benches under development. Reading it as
  "current" means nothing ever matches it, so every slice on those silos falls to the 30-day
  legacy TTL instead of the 7-day current-bench one, defeating the *only* mitigation for the
  open coverage hole. **Derive the current bench as the newest bench the site renders that
  also has a published schema.** The per-page bench list lives at `GLOBALS.static.silo`.
- **Derive the recent-bench set from `is_recent`, never hardcode `[197,210,227]`.** That set is
  TV-specific and the size varies per silo — tv 3, headphones 4, mouse 2 (`RECON.md` §8, confirmed).
  The flag lives on the **page-embedded `GLOBALS` bench list**, not in `column_options`; "top 3 in
  list order" and "same major version" were both tested and both fail. Note the page's bench list is
  **longer** than `column_options`' (TV: 18 vs 14) — bench *ids* come from the page, bench
  *definitions* from the schema; never assume the two lists match. `GLOBALS.static.silos[]` gives the
  **current** bench for all 28 silos in one fetch (with `replaces_id` chaining backwards), but the
  `is_recent` set costs one page fetch per silo.
- **Detect `session` from HTML `GLOBALS.session.current_user`** (`null` = anonymous, object = logged
  in), corroborated by `access_state.access_level` and page-prop `has_insider_access`. **Never** from
  "a cookie is set", **never** from "scores are null", **never** from a UI string (ported).
- **The session cookie is `_rtings_session`** — one Rails encrypted-session cookie, `.rtings.com`,
  30-day, **HttpOnly**. HttpOnly ⇒ **"Copy as cURL" is the only capture gesture** (no console
  fallback, differs from CR). Inject with explicit attributes: `_rtings_session=…;
  Domain=.rtings.com; Path=/; Secure; HttpOnly`. Persist rotations via `session.get_cookie(name,
  https_url)`, **not** `resp.cookies`.
- **IMPORTANT: member fetches use `max_rotations=0, max_failures=None`.** A wafer rotation rebuilds
  with an **empty jar** and does not raise — one transient 403 then makes RTINGS return a normal
  anonymous `200` that looks exactly like expiry. A missing credential is `identity_rotated` (a
  transport error, never cached), not an auth state (ported; applies harder — one cookie, no re-mint).
- **IMPORTANT: the session SLIDES, so ROTATION WRITE-BACK IS REQUIRED (measured
  2026-09-04, `RECON.md` §12.15).** RTINGS re-issues `_rtings_session` on **every** response,
  HTML and API alike, with a new value and `expires = now + 30 days`. So the 30 days is a
  sliding *idle* window: a session lives indefinitely while used and dies 30 days after it
  stops, and `/login` has no "remember me" because it needs none.
  **Refusing to persist rotations therefore causes the harm it was meant to prevent** — the
  stored blob freezes at the pasted value and expires 30 days later however much the server
  is used, so the user re-pastes monthly because of us. Persist the rotated value, gated on
  **proof**: only a jar value that just produced a response with `current_user` non-null,
  which an anonymous session can never satisfy. `RTINGS_SESSION_COOKIE` cannot be refreshed,
  so the stored credential is the durable path and the env var is warned about once.
- **IMPORTANT: track credential IDENTITY, not cookie presence — RTINGS re-mints anonymously.** A
  plain anonymous GET *sets* a fresh `_rtings_session` (`RECON.md` §5, confirmed). So "assert the
  cookie is present in the jar" is satisfied by RTINGS' own anonymous cookie, and two bugs follow:
  the presence check passes when the credential is long gone, and **persisting "rotations" from the
  jar overwrites the user's pasted cookie with an anonymous one**, destroying the credential on disk.
  Compare the jar value against the **configured** value, and write a rotated value back **only** when
  the rotating response is proven logged-in (HTML probe, `current_user` non-null). Never write back
  from a response that yielded `current_user: null`.
- **Read `challenged` from the response, not from an exception.** Under `max_rotations=0` wafer
  *returns* 403/429/challenge/empty-200 rather than raising, so an exception-based mapping never
  fires. Classify from status and `resp.challenge_type`. An **empty 200** is `fetch_failed`, not the
  `payload_missing` drift alarm — that alarm means "the shape changed", not "the transport failed".
- **The server never populates `force_blur` / `unblur_product_ids` beyond what a logged-in front end
  sends.** They are client-controlled request params (`RECON.md` §5); a client unblur hint is
  circumvention. Send `unblur_product_ids: []`, `force_blur: false`.
- **Never store credentials.** Cookie only, user-supplied. Never ask for a password, automate login,
  solve a CAPTCHA, log a cookie, or write one to the cache (ported).
- **`cache_tier` keying is on the gated surfaces only.** `tests/`/`ratings/`/`reviews/` are
  tier-keyed; `schema/`/`catalog/`/`graphs/`/`bench/`/`recs/` are **not** — they carry no gated
  fields (`RECON.md` §3, §4), so tiering them would store two identical copies. Scored rows are never
  overwritten by unscored ones; selection is on the **data** (`unblurred`), not the tier (ported).
- **A cache hit also requires `cache_tier ≥` the probe tier** (ported) — otherwise configuring a
  cookie serves 30 days of cached nulls honestly labelled `anonymous`. Both sides of that comparison
  come from the **probe**, never from the data (see the `cache_tier`/`data_tier` rule below).
- **Storage is plain JSON files, NOT SQLite** (`SPEC.md` §8, reversed 2026-09-03). The SQLite design
  came from CR, where forced category dumps had to be shredded into rows — and shredding is what
  destroys the record of *what was requested*, which is the only reason a `fetch_coverage` table ever
  had to exist. RTINGS' API is parameterized and `test_results` has **no product filter**
  (`RECON.md` §1), so **the response IS the coverage record and the file path IS the key.** Nothing
  else needed SQL: `rt_search` is API-backed, catalog filtering is in-memory over 111 KB, and
  cross-bench joins are forbidden anyway.
- **Store the response whole; a query serves the superset of COVERAGE — not of rows.** A request for
  tests `[1,2,3]` must be satisfiable from a cached `[1,2,3,4]`. Cache hit ⇔ every requested
  `(bench, test)` has a file at `cache_tier ≥` the probe tier, within TTL, and not coverage-stale.
  An absent row **inside** a covered fresh file is `not_tested`; an absent row **outside** covered
  scope is a miss, never an answer.
- **IMPORTANT: coverage carries a TIME dimension, or the cache emits the forbidden `not_tested`.**
  A slice fetched day 1 covers the products that existed on day 1. A TV that ships on day 5 has no
  row in it — and reporting `not_tested` for a product RTINGS may have measured is the exact lie the
  safety property forbids. So coverage is `(bench, test, fetched_at, catalog_generation)`: a product
  present in that catalog generation with no row is `not_tested`; a product **absent** from it, or
  present but **on a different bench** in it, is **`coverage_stale`** — a miss. **Do NOT trigger on
  `last_updated_at`**: `RECON.md` §11.6 measured it as a bulk re-index field (65 of 74 cameras share
  one minute), so it fires constantly without a retest and is not shown to fire with one. The old
  `fetch_coverage(bench_id, original_id, tier, fetched_at)` had this hole too; it has no product
  dimension either. **Embed the generation's `product_ids` in the slice envelope** — a bare generation
  id dangles once the 3-day catalog TTL overwrites it, and "was this product in it?" silently defaults
  to whatever the reader assumes. Catalog generations are immutable
  (`catalog/{silo}/{bench}.{fetched_at}.json`). The check is per **(product, bench)**: a retest moves
  a product between benches, and "present in the generation" on the *wrong* bench is still a false
  `not_tested`. **Known remaining hole:** a product that existed on day 1, untested, and was tested on
  day 5 is not distinguishable. The obvious signal is dead — `last_updated_at` is a bulk re-index
  field (§11.6), not a retest marker. So: stamp every `not_tested` with `as_of:<slice fetched_at>`,
  and give **current**-bench `tests/`/`ratings/` a 7-day TTL vs 30 for legacy. The hole stays open and
  labelled.
- **IMPORTANT: write-time demotion is PER SURFACE, because the row shapes differ (added 2026-09-05).**
  The test-path predicate needs `status:"tested"` and an `insider_only` id. **A `ratings/` row has
  NEITHER** — no `status` at all, and its `original_id` is a *usage* id while `insider_ids` holds
  *test* ids, so the loop skipped every row and demotion was structurally **dead** on the surface
  `rt_ratings` uses by default. `envelope_notes_for` had the same cross-namespace bug, which made the
  "keep the newest file holding unblurred data" pruning exemption unable to fire there — a later
  blurred refetch could delete a member's only copy of real scores. Ratings key on `unblurred` alone
  (every usage row is gate-relevant); `surface` is a REQUIRED argument to `envelope_notes_for`, not a
  defaulted one, because getting it wrong is silent.
- **IMPORTANT: `verdicts/` demotion keys on the usage SCORES, never on `user_has_access`.** That flag
  is the payload's own blur signal, but it *appears* to track silo enforcement rather than membership
  (`false` on TV, `true` on mattress, both anonymous — two data points, a lead not a fact). If it
  never flips for a member on a gated silo, demoting on it would demote every member write there,
  miss every read and refetch forever — the deadlock the `cache_tier`/`data_tier` rule exists to
  prevent. All-null scores where the tier predicts otherwise is the same evidence, without depending
  on what the flag means. `SPEC.md` §10 capture (p) settles it.
- **IMPORTANT: DEMOTE `cache_tier` at write time when the response contradicts the probe.** The probe
  and the fetch race, so labelling a file with the last probe writes `member` over a response that
  came back fully blurred after the session lapsed — and the hit rule then serves those nulls to a
  re-authenticated member for the whole TTL, since a cache *hit* never re-probes. Before any
  tier-keyed write: re-probe (**never throttled on this path**), and **demote to `anonymous`** if the
  response has `insider_only` rows with `status:"tested"`, none `unblurred:true`, **and** the tier
  predicts unblurred (`member` on any surface, or `free` on a `reviews/` fetch for a product already
  in `previewed_products`). Public-only, all-`na` and `free`-on-table slices are vacuous under that
  predicate, so the deadlock does not return. **Demotion is write-time; the read path stays
  probe-vs-probe and never inspects data to pick a tier.**
- **A successful probe is a PRECONDITION for any tier-keyed write** and for `rt_product` whenever a
  cookie is configured. `session` gains an **`unknown`** value (no probe yet, or the probe failed) ⇒
  demand `anonymous` and warn. Never write `member` off an absent or failed probe. **Pin the probe to
  a non-review page** (`/{silo}/tools/table` or `/`) — the review path is the metered one, so probing
  there spends a free account's preview every time the server checks whether it is logged in.
- **`free` as a `cache_tier` exists only on `reviews/`.** A free account unlocks nothing on the table
  path, so a `free` probe fetching `tests/`/`ratings/` would miss every `anonymous` slice and write a
  byte-identical `free` copy. Demand tier there is `member` if the probe says `member`, else
  `anonymous`.
- **IMPORTANT: `cache_tier` (stored) and `data_tier` (derived) are DIFFERENT fields.** `cache_tier` is
  the **probe** tier at fetch time (`anonymous < free < member`) and lives in the filename;
  `data_tier` is what the served bytes prove (`unblurred`/`unproven`) and is recomputed per response,
  **never stored and never a cache key**. Storing a data-derived tier and requiring
  `data_tier ≥ configured` deadlocks forever: a slice for a **public** test is `unblurred:true` on
  every row anonymously yet contains no `insider_only` test, so it can only be labelled `unproven`
  and a member misses on it on every call, permanently. Same trap for a free account on every
  table-path slice and for any all-`na` slice. **The hit rule is probe-vs-probe.**
- **`fetched_at` is in the filename** (`{key}.{cache_tier}.{fetched_at}.json`). One slot per tier
  cannot express "freshest among those", "`refresh=true` appends, never promotes", or `superseded_at`
  — a same-tier refetch would be a whole-file overwrite, letting an all-blurred response clobber a
  good one. Prune to: newest file per tier, **plus** the newest file holding any `unblurred:true` on
  an `insider_only` test.
- **Reads MERGE across tier files, per row.** A gift or metered preview unblurs one product inside an
  otherwise-blurred response (1 of 97 observed), so no whole-file label is correct. Take the row with
  `unblurred:true` from any tier variant (freshest among those), else the row from the freshest
  variant. Never-downgrade stays on the row's `unblurred` bit.
- **Negative results are FILES too.** 397/402 rows on TV bench 227 have no curve, so without a written
  `{outcome:"graph_not_available"}` every `rt_graph` on those costs a POST forever. A legitimately
  empty slice is a written file with an empty payload — a reader must never read empty as a miss.
  **Never** cache `fetch_failed`/`challenged`/`rate_limited`/`identity_rotated`/`unknown_product`.
- **The size limit must be CALLED, not merely implemented.** `enforce_size_limit` (with both its
  exemptions) existed, was unit-tested, and was invoked from nowhere in the serving path — so
  `RTINGS_CACHE_MAX_MB` had no effect and `reviews/` grew unbounded (442 KB x 548 TVs is 242 MB for
  one silo). `prune_variants` bounds variants per key, never total size. It now runs from
  `flush_lru`, throttled by `SIZE_CHECK_INTERVAL_S`.
- **TTL is per surface.** Silos/bench 1 day, catalog 3, schema 30, results 30, graphs 180. A uniform
  30 days means a new bench goes unnoticed for a month while ranking on a stale `is_recent` set.
- **Write temp-in-target-dir + `fsync` + `os.replace`** (never `/tmp` — cross-filesystem replace is a
  copy, not atomic), mtime = `fetched_at`, cache dir `0700`. Take a **cross-process** advisory lock
  around miss→fetch→write: single-flight is in-process, and two MCP clients sharing the cache dir
  double-spend a metered preview on `reviews/`.
- **Validate every caller-influenced path segment before building a path** — `silo` against
  `static.silos[].url_part` (lowercased), ids `^\d{1,10}$`, and a final
  `resolved.is_relative_to(cache_root)`.
- **`stale` means past-TTL and nothing else** (ported). A scored row retained over a newer unscored
  one is reported by `superseded_at`, per row. Never prune the newest unblurred file for a key, and
  never evict a `reviews/` file bought with a metered preview.
- **IMPORTANT: a test's 0-10 SCORE is not a stand-in for its value.** A usage rating has
  only a score; a test has a value *and* a score. Falling back to the score when a test's
  value is null makes `peak_brightness < 10` match a TV whose value is unknown because its
  *score* is 8.5 — confidently wrong rows with no signal, which is worse than the empty
  result the filter guard exists for. One `_comparable(entry, kind)` helper decides what a
  filter or sort may compare, and every call site goes through it: `value` for a test,
  `score` for a usage, `None` otherwise (including a `tested_visible` row that is genuinely
  empty). It also drives the "is this field populated?" census, or a null-valued visible row
  makes the guard think the field is usable.
- **Paginate WITHIN a group, never across benches.** A group is one comparable bench
  population, so a global sort-then-slice can return zero rows from a widened bench that
  matched — and it ranks across the very boundary `rank_scope: within_bench` exists to keep.
  `limit`/`offset` apply per group; each group reports its own `matched`.
- **IMPORTANT: reserve a metered preview BEFORE the awaited fetch, under a process-wide
  lock.** The per-product file lock cannot bound a per-*process* budget: two calls for two
  different products take different locks, both read `previews_spent == 0`, and both spend
  against a limit of 1. Increment first, release on failure. The file lock still does its own
  job — stopping two processes double-spending the *same* product.
- **A failed refresh serves the cache, it does not fail the call.** A cached row is served
  with `error:null` whenever one exists, even past TTL and even when the refetch failed:
  `stale:true`, the original `fetched_at`, and a `refresh_failed` warning. `error` is
  non-null only when there is nothing to return. This applies to `schema`, `catalog`,
  `silos`, `bench`, the `tests`/`ratings` slices and `recs` alike — a transient 503 must not
  turn a perfectly good 8-day-old slice into a hard failure.
- **The envelope must report the DATA's age, not the call's.** `fetched_at`/`from_cache`/
  `stale` come from the cached object actually served. A 29-day-old schema reported as
  "fetched just now, not from cache, not stale" is the quiet dishonesty the envelope exists
  to prevent — which means a repository method returning a parsed object must also expose
  that object's provenance.
- **Warnings are scoped to ONE call.** The repository is a process-wide singleton, so a
  plain instance list attaches an early call's warning to every later response and grows
  without bound. A re-entrant `ContextVar` scope is opened by both the server wrapper (so the
  error path keeps the warnings that explain the failure) and each service body (so a direct
  call still collects).
- **IMPORTANT: a field a filter or sort NAMES must be FETCHED (added 2026-09-05).** Predicates run
  over the rows actually served, so a field nobody projected is absent from every row and the
  filter quietly does nothing. Measured live on **mattress — an OPEN silo, nothing gated**:
  `filters={"Thickness": ">1"}` returned all 69 products with "no value is populated", when the
  truth was that the test had never been requested. That is the project's core failure mode wearing
  a different hat, reachable where the paywall is not involved at all. `_fields_to_fetch` unions
  filter/sort fields into the projection (strip the `+`/`-` sort prefix first). And **a name works
  wherever an `original_id` works** — `filters`/`sort` always took either, so `tests=` rejecting a
  name made the documented remedy fail with `unknown_test` on the string the filter had just
  accepted.
- **"Not applied" has THREE reasons and they must not be collapsed:** *gated* (buy a membership),
  *absent from the bench* (call `rt_schema`), *genuinely empty* (RTINGS published nothing). Reporting
  the middle one as "no value is populated" sends the caller to buy a membership they do not need.
- **IMPORTANT: a row the server cannot compare sorts LAST in both directions.** `reverse=True` flips
  the whole sort key, so a fixed "missing" rank put gated and untested rows at the TOP of every
  descending sort — "the brightest TVs" led by TVs whose brightness is unknown. Pre-flip the presence
  flag; do not rely on the tuple ordering alone.
- **IMPORTANT: Early-Access status comes from the ROW's own slice envelope, never today's catalog.**
  The catalog refreshes on its own 3-day clock, so a review published on day 4 makes a day-1 blurred
  slice read as `tested_gated` — "buy a membership" for a review RTINGS had not finished. `rt_product`
  always did this; `rt_ratings` did not until 2026-09-05. Same rule for `_verdicts_only`, which
  defaulted `unpublished=False` and so reported an Early-Access verdict as paywalled.
- **A `kind:"graph"` test has no scalar on EITHER path.** The review path said so; the table path did
  not, so a curve test requested through `rt_ratings` came back `tested_visible, value:null` —
  "measured, and the answer is nothing", when the answer is a curve. `rt_schema` lists graph tests
  beside real ones, so this needs no unusual input to reach.
- **IMPORTANT: a negative is a well-formed EMPTY payload; an unrecognised shape is DRIFT.** Measured
  2026-09-04: a genuine "no curve" is `{"data":{"product":{"review":{"test_results":[]}}}}`. A blanket
  `except RtingsError: return None` around `_dig` therefore turned any transient shape change into a
  written `graph_not_available` file — a confident structural claim, cached three days, from a
  transport blip. Negatives get cached, so only an explicit empty may produce one.
- **Filtering and sorting must not silently use gated fields.** Anonymously every gated
  value is `null`, so a filter on one matches **zero** products — and "0 results" reads as
  *no product qualifies* rather than *you cannot see it*. Check the **served rows**, never
  the silo: a field that resolves to `tested_gated` for the population is **not applied**,
  and the envelope names it. The default sort is a public catalog field, never a gated score.
- **Never return the raw payload.** `column_options` is ~357 KB, a full review ~442 KB, a
  graph up to ~361 KB (measured 2026-09-05 across silos: speaker "Raw Frequency Response
  Graph" 361 KB, soundbar 335 KB, headphones 190 KB — TV's ~74 KB is the SMALL end, and a
  256 KB CDN cap sized from it turned real published curves into `fetch_failed`).
  `rt_schema` bounds by group; `rt_graph` resamples to ~200 points
  (`full=true` opts in); `rt_ratings` defaults `limit=10` per group. `rt_product` is the
  per-product drilldown and **is itself bounded** — a current-bench review is 402 rows /
  437 KB, so default to leaf value kinds (`number`/`word`), each row carrying a `hierarchy`
  breadcrumb (a flat list, NOT nested — see `TODO.md`), with prose
  (`linked_description`), media and verdicts opt-in. `group`/`category` rows are **structure,
  not results** — they never get a `status` or a value.
- **IMPORTANT: measure the payload ON THE WIRE, not the dict you built.** The services return
  lean dicts, but the output model re-adds every declared field as `null` when it serializes —
  one `rt_product` response was 55,857 bytes of content and **113,406** delivered, and a
  single gated row 424 bytes of which 256 were nulls. Row models drop their null optionals;
  **`value`, `gated` and `status` are exempt** because "a gated value is null, never absent"
  — and **`score` is exempt on `RatingOut`/`VerdictOut` only**, where a usage rating has *only*
  a score so it plays `value`'s role; it stays droppable on `ValueOut`, where it is secondary
  and null on most rows. The exemption is a per-model `ALWAYS_PRESENT`, not one global set,
  is the whole promise, and envelope models are exempt entirely so `out["error"] is None`
  cannot become a `KeyError`.
- **Resample by SELECTING shipped points, never by interpolating.** Decimation or LTTB over the
  `(x,y)` pairs as shipped; never average, smooth, or synthesize a point. An interpolated value is a
  number RTINGS did not measure, which is the derivation prohibition by another name. For the same
  reason do not return a curve's `y_range`/max as a headline figure — a peak-luminance maximum reads
  exactly like the gated scalar it summarizes; if bounds are returned at all, label them as
  `axis_bounds_of_served_points`.
- **Fetch curve JSON UNCREDENTIALED.** The CDN needs no cookie (`RECON.md` §4, confirmed), and
  `_rtings_session` is `Domain=.rtings.com`, so a default session would offer the credential to
  `i.rtings.com` for nothing.
- **Store the extracted JSON, not the HTML page** — parse on ingest. Store an ingest **envelope**
  (with `cache_tier`, `fetched_at`, `source_url`, `catalog_generation` + its `product_ids`), not a
  bare API body. **`data_tier` is never stored** — it is derived per response (see the
  `cache_tier`/`data_tier` rule).
- **The 28 silos ship as a `description`/`examples` hint on the `silo` parameter, NEVER a JSON-Schema
  `enum`.** An `enum` is enforced client-side, so a silo RTINGS adds mid-release becomes unreachable
  until a release ships, and it creates a second allowlist that can disagree with live `static.silos`.
  Validate **server-side against the live list only**; a value outside the hint fetches normally with
  a `silo_hint_drift` warning. Same reasoning as never hardcoding `[197,210,227]`.
- **IMPORTANT: `test_benches[].tests[]` is a REFERENCE list — `{original_id}` only.** Definitions
  live in `silo.test_bench.tests[]` (current bench) + `silo.legacy_tests[]`. Bench→test *membership*
  comes from `test_benches[]`; *definitions* from the other two. Joining the wrong one yields an
  empty schema **silently** (`RECON.md` §11.5).
- **IMPORTANT: `table_tool__ratings` rows carry NO `status` field** (measured 2026-09-04):
  `{original_id, product_id, score, suitable, unblurred, usage}`. "Branch `status` before
  `unblurred`" has nothing to branch on there, so the usage normalizer is a **separate
  function**, not a parameterization of the test one. And usage ratings are **not** uniformly
  gated on the open silos — mattress `Side Sleeping` came back `score: 7.7, unblurred: true`
  anonymously, falsifying the old "assume gated there" caveat for at least that silo.
- **IMPORTANT: `test_results` has a WIDER product population than `products_list`.** 9 TV
  product ids returned rows while appearing in no catalog across all 18 benches, every row
  blurred (`RECON.md` §12.2). So a row for a product the catalog does not carry is
  **structural, not evidence the catalog is stale** — do not warn that it is. File those rows
  under `_unassigned` rather than dropping them (a dropped row becomes a false `not_tested`
  if the catalog later catches up) and do not report them as results.
- **Public tests are not all `word`** — `number` publics exist (laptop/monitor `Size`, mouse `Default
  Weight`, vpn `Data Limit`). And the `kind` domain is wider than TV's: also `audio`, `3d_model`,
  `download`.
- **IMPORTANT: the category→group hierarchy is POSITIONAL — the API states none.** All 69 of
  TV's `category`/`group` rows carry `parent_original_id: null` (`RECON.md` §12.13); only the
  **order** of `test_bench.tests[]` links a group to the category above it. Reading the field
  alone makes `rt_schema` a flat scramble of 57 groups plus 12 categories reporting zero
  leaves. Attach each top-level group to the most recent preceding `category`, and keep it in
  a **separate derived field** — the API's own value really is null and overwriting it hides
  that.
- **The tested size is in the CATALOG, not in a test.** Most silos have no "Size" test;
  `variant_skus[reviewed_sku_id].variation` is the SKU RTINGS actually reviewed
  (`RECON.md` §12.14). Without exposing it, "which 65-inch TV is brightest?" is unanswerable —
  and it matters for correctness, because the results describe *that* SKU.
- **Bound the DEFAULT response, not just the maximum.** 25 products x every usage is ~15K
  tokens of near-identical rows on a gated silo and ~26K on an open one. Default `limit` is
  10, usages default to the headline set (not sub-usages), and `usages=[]` skips the surface.
  Per-row honesty is never traded for size — what changes is how many rows are served.
- **`original_id` is the stable test key; `name` is not** (ported — names repeat across sub-groups).
- **Join test definitions from `column_options`, NOT the per-row `test:{id}` stub.** The row
  references a test by `original_id`; the schema is the dictionary, keyed by `original_id`. Note
  **`id` ≠ `original_id`**.
- **Coerce by declared `kind`, never by value shape** (ported). `word` tests hold `"Yes"`/`"No"` and
  numeric-looking strings; only `number` tests are numbers. Never coerce a `word` value. A value that
  will not coerce keeps `raw_value`, sets `value:null`, and warns.
- **Never infer a unit** — `number_display_unit` / `number_display_precision` ship in the definition.
- **`app/side_by_side__review` is RTINGS' WORDS, and they survive the paywall.** Bare
  `{product_id}`. On a gated silo it returns, anonymously, per-usage verdict prose, pros/cons
  blurbs (`priority` below 0 is a con) and the score formula — the substantive answer where the
  numbers are null. **Ignore its `test_results`**: a third row shape with no `unblurred` key,
  so the normalizer has nothing to branch on. `user_has_access` is this path's only blur
  signal; it *appears* to track silo enforcement rather than membership (`false` on TV, `true`
  on mattress, both anonymous) — two data points, so a lead, not a fact.
  **A missing score under `user_has_access:true` is NOT `not_tested`.** One review-wide
  boolean cannot support "RTINGS did not measure this", and the row is present with prose and
  a `suitable` flag — it is `tested_visible` with a null score. Early Access rows are
  `review_unpublished`, never `tested_gated`.
  **Give the verdicts their own notice key**: `data.notice` already explains why the
  measurements are null, and overwriting it with verdicts framing erases the Early Access
  distinction. Metering is unverified — it is the public compare tool, not the review page —
  so a spent preview budget degrades to verdicts-only rather than an error.
- **Do not repeat a response-constant on every row.** `rt_product` carried `product_id` and
  `as_of` on all 243 rows: identical every time, already in `data.product` and the envelope,
  and 13 KB of a 69 KB response. They stay per-row in `rt_ratings`, where they genuinely vary.
- **`rt_recommendations` is the one page-extraction path** (`RECON.md` §7) — isolate it: own parser,
  own `recommendations_missing` drift alarm, never on the table/graph code path.
- **IMPORTANT: there are TWO best-of templates and BOTH are legitimate (added 2026-09-05,
  `RECON.md` §12.17).** RTINGS is migrating best-of pages off the monolithic
  `RecommendationVuePage` (one `data-props` blob) onto a **server-rendered** template whose only
  Vue parts are islands. **mattress and running-shoes have moved; the other 12 silos sampled have
  not**, and it does *not* track silo age — refrigerator is the newest silo and still on the old
  one, so more will migrate silently. Supporting only the props shape made `rt_recommendations`
  fail on **all 20** mattress lists — the tool advertised lists it could not fetch. So try props,
  then static, and fire `recommendations_missing` only when **neither** matches; that, not "the
  props are missing", is the drift signal. There is still **no API** (the new page's whole bundle
  is 3 KB with zero `/api/v2/safe/` references), so extraction remains the only route.
  The static template's `DistributionTooltip` gives `target_label`/`target_type` but its
  **`target_id` is NOT the schema `original_id`** (Side Sleeping: 38309 vs 36553) — emit a null id
  with a real name, never the wrong join key. Both migrated silos are open, so no blurred sample
  exists: a featured item rendering neither score nor value is `unknown_row_status`, never
  `tested_gated`.
- **`RTINGS_MEMBER_MODE` (default `false`) is how Phase 0 is enforced in code.** With it off,
  `probe_tier()` returns `anonymous` unconditionally, so every tier-keyed write is `anonymous`
  and no read demands a tier — while the whole mechanism (the filename segment, demand/write
  rules, write-time demotion, the preview budget) is built, unit-tested and running. Enabling
  member mode is a flag, never a migration. **Rotation write-back IS implemented** (superseding
  an earlier "stays unimplemented" note here, which predated the sliding-session measurement in
  `RECON.md` §12.15): the rotated cookie is written to disk, but **only** from a response the
  HTML probe proved logged-in (`current_user` non-null) and only for a file-sourced credential.
  Refusing to persist was what caused the monthly re-paste it was meant to prevent.
- **Phase 0 gates member-tier SELECTION and the member claim — not the cache schema, and not the
  anonymous surface** (`SPEC.md` §10). Do not implement tier-aware row selection or
  `member`/`free` classification, and do not claim member support, until a bought membership
  confirms a cookie flips `unblurred` on the API. **Do** build everything else now: the `cache_tier`
  filename segment ships from day one carrying `anonymous`, so enabling member mode is never a
  migration. The entire
  anonymous surface (silos, schema, catalog, search, graph, recommendations, the seven-state
  normalizer, the untiered cache) is Phase-0-independent — do not gate it.

## HTTP

- **ALWAYS use wafer** (`~/code/wafer`, see its `llms.txt`) — never `urllib`, `requests` or `httpx`.
  The `/api/v2/safe/` endpoints are clean JSON but sit behind CloudFront and carry repeated,
  authenticated member traffic.
- **ALWAYS pair `timeout=` with `attempt_timeout=`.** `timeout` is a TOTAL budget across retries.
  Unpaired, one hanging request eats the budget and retries never fire.
- **Rate limiting is a TOKEN BUCKET we own, not wafer's interval.** `wafer/_ratelimit.py` is a flat
  per-hostname sleeper — no burst, no lock, no concurrency control — and it cannot express a burst.
  Set `rate_limit=0.0` on both sessions and own the bucket: **capacity `RTINGS_RATE_BURST=5`, refill
  one token per `RTINGS_RATE_INTERVAL_S=2.0`s, with jitter.** Same sustained politeness as the old
  flat 2.0 s. **The burst buys interactive latency, not throughput** — a cold `rt_ratings` drops from
  ~8 s of sleep to ~0, but the 28-silo smoke test is ~270 s at burst 5 vs ~280 s flat, since sustained
  rate is the refill. Do not claim otherwise. `RTINGS_RATE_INTERVAL_S` is **seconds per token, not requests per
  second** — an "rps" value inverts the ceiling. Acquire the token **before** computing the
  per-request `timeout=`, or the wait eats the attempt budget.
- **IMPORTANT: honour `Retry-After` yourself — wafer will not under our settings.** It sleeps
  `max(retry_after, rotation_floor)` **only on the rotation path**, and we set `max_rotations=0`,
  under which a 429 is *returned immediately without sleeping*. Keep a per-host `cooldown_until`:
  on 429/503 or a `resp.challenge_type`, `max(resp.retry_after, 30s)`, doubling to a ~10 min cap,
  cleared on the next 200; persist it under the cache dir so a second process respects it. Split
  **`rate_limited`** (retryable, carries `retry_after`) from **`challenged`** (not) — collapsing them
  tells the agent "no" when the answer is "in 30 seconds".
- **Concurrency needs an `asyncio.Semaphore`** (`RTINGS_CONCURRENCY`, default 1). wafer's limiter
  records *after* the response and holds no lock, so two concurrent calls both fire at t=0.
  Single-flight dedupes only identical keys.
- **TWO sessions: `www.rtings.com` (credentialed) and `i.rtings.com` (never).** One session forces the
  origin's floor onto 2–361 KB static files and offers `_rtings_session` to the CDN for nothing
  (confirmed: `add_cookie` with an explicit `Domain` sets `host_only=False`, `wafer/_base.py:2542`).
  API session: `max_retries=0, max_rotations=0, max_failures=None` — `max_retries=0` also stops wafer
  retrying a 5xx or empty 200 **three times inside one of our bucket tokens** while never consulting
  `Retry-After` on that path. CDN session: **`max_rotations=0` here too** (otherwise a CDN
  403/challenge *raises* instead of returning and the "classify from the response" rule breaks on that
  session), own bucket (burst 10, 0.25 s), **`max_retries=0`** (not `1` — that reintroduces exactly
  what `max_retries=0` exists to prevent: wafer retrying a 5xx inside one of our tokens without
  consulting `Retry-After`), `max_response_size ≈ 1 MB` (**not 256 KB** — that was sized from
  TV's ~74 KB curves and made speaker's 361 KB one unfetchable; see the payload rule above).
- **Never pass wafer's `cache_dir=`** — it persists solver cookies to disk, a credential-shaped
  artifact this project writes nowhere.
- **Status precedence, first match wins:** `challenged` (any `resp.challenge_type`) → `rate_limited`
  (429, or 503 **carrying** `Retry-After`) → `fetch_failed` (any other non-200, transport error,
  `ResponseTooLarge`, or empty 200). A 503 with no `Retry-After` is `fetch_failed`.
- **Never hold two locks.** `cooldown_until` is cross-process like the key locks; read/write it
  lock-free via atomic replace and release anything it takes before acquiring a key lock, or two
  processes deadlock. `fcntl.flock` blocks the event loop — run it in `asyncio.to_thread`.
  **Qualified 2026-09-05:** the surface key locks (`tests`/`ratings`/`review`/`verdicts`) DO nest —
  they hold their lock across `catalog()`, `schema()` and `session_probe()`, each of which takes its
  own. That is deadlock-free only because those three are always **leaves** (they take no further
  cross-process lock) and always the **inner** lock. Nothing enforces that ordering and no test
  covers it, so adding a lock to any of those three, or acquiring `catalog`-before-`tests` anywhere,
  introduces a real cross-process deadlock.
- **The floor is politeness to origin, NOT camouflage.** `robots.txt` has no `Crawl-delay` and
  disallows only `/user_reviews/` and `/admin/`; no `X-RateLimit-*`/`Retry-After` observed. Rate
  rules count requests per window, not cadence — a metronomic interval is the *most* machine-like
  timing there is. Answer `RECON.md` §10 q6 from `telemetry/requests.jsonl` during real use (headers
  only — never bodies, never cookies), **never by inducing a limit**.
- **One shared `AsyncSession` per host family, process-wide** (`SyncSession` is not thread-safe). Set
  `max_response_size`. Single-flight on the fetch key so two calls never pull the same payload twice.
- **Send the real browser headers on every `/api/v2/safe/` call** — per-request `Origin`, `Referer`,
  `Sec-Fetch-Site: same-origin`, `Sec-Fetch-Mode: cors`, `Sec-Fetch-Dest: empty`. The cookie is the
  sole credential (no CSRF, JS-confirmed `RECON.md` §5); headers remove the one unprovable Phase-0
  risk (server-side origin validation).
- **Do NOT hand-set `sec-ch-ua*` or `User-Agent`, and never pass constructor `headers=`.** wafer's
  emulation applies a self-consistent header envelope; a constructor `headers=` **replaces** it
  wholesale and hand-written client hints contradict the emulated fingerprint — that *creates* the
  detection risk the browser headers are meant to remove. Per-request `headers=` merges, which is why
  the four above are safe to set that way.
- **Never send privileged *values*** — `is_admin:true`, `named_version:"admin"`. Note this is about
  the value, not the key: `is_admin:false` is part of the ordinary public body the logged-out front
  end sends on `products_list`, `test_results` and `search` (`RECON.md` §1), so **send
  `is_admin:false`**. Omitting the key produces a body the real client never sends.
- **Request shapes are per-query — do not generalize by prefix.** `table_tool__*` take
  `{"variables":{…}}`; `app/product_vue_page__page_body` takes
  `{"variables":{…}, share_token, url_path}`; **`app/search__search_results` takes a bare
  `{count, is_admin, query, type}` with NO `variables` wrapper** (`RECON.md` §1, re-confirmed
  2026-09-03). `named_version` is always `"public"`.
- **IMPORTANT: `errors[]` beside `data` is a PARTIAL-FIELD notice, not a failure.** RTINGS
  strips admin-only fields (`edit_url`, `methodology_url`, `review_notes_url`) and reports it
  in `errors[]` while returning a complete payload. Raise `api_error` only when `errors[]`
  comes back with **no `data`**. A genuinely bad request does not use `errors[]` at all — an
  unknown silo returns `{"data": {"silo": null}}`, which is `payload_missing`.
- **Never log or return a response body** — challenge pages carry tokens, member pages carry profile
  data. Status, `reason` and `<title>` only.
- Cache-first. Hit the network only on a miss or an explicit `refresh`.

## Development

```bash
uv venv && uv pip install -e ".[dev]"
.venv/bin/pytest tests/ -q                 # offline tests (the default; no network)
.venv/bin/pytest -m live -q                # live, anonymous, against the real API
.venv/bin/pytest -m "live and slow" -q -s  # + the 28-silo enforcement re-scan (~4 min)
.venv/bin/ruff check src/ tests/           # lint (fix with --fix)
.venv/bin/rtings-mcp scan --out docs/enforcement-snapshot.json   # the release gate, by hand
```

Run lint and the offline tests before every commit; run the live suite and the enforcement
re-scan before every version bump.

- **Pin dependency FLOORS, not exact versions** — `uv.lock` provides reproducibility. Verified
  current 2026-09-03: `mcp>=2.1.1`, `pytest>=9.1`, `pytest-asyncio>=1.4`, `ruff>=0.16`. Re-check at
  each release re-scan (below).
- **Fixtures come from anonymous fetches. Never commit a fixture containing unblurred member values.**
- **Redact every `GLOBALS.session` fixture before committing.** The member/free auth-state fixtures
  are captured from a real logged-in session, so `current_user` carries the user's own name, email
  and subscription details. Keep the **shape** (which fields are present, and `current_user`
  non-null), replace every value with a placeholder, and never commit the cookie.
- **IMPORTANT: `StubTransport` overrides `api_post`/`api_get_html`/`cdn_get_json` WITHOUT calling
  `super()`, so the real `Transport._perform`/`_classify` and the `errors[]` rule are dead code under
  the offline suite.** Mutation-tested 2026-09-04: deleting the challenge-precedence branch, and
  making any `errors[]` fatal, each left the whole suite green. **`tests/test_http.py` is where the
  shipped transport is actually exercised** — it injects a fake wafer session at `_api_session` so
  `_perform`, the cooldown and the JSON handling all run. A contract the stub *mirrors* is not a
  contract the suite *tests*; if you change transport behaviour, the test belongs there.
- **Mutation-test the safety-critical branches rather than trusting the count.** The three gaps found
  this way (status precedence, `errors[]`, `should_demote`'s free/reviews branch) were all invisible
  to line coverage — the lines ran, nothing asserted on them.
- **MCP tools in Claude Code connect to the installed server, not your working tree.** Edits need an
  MCP restart; test inline with `.venv/bin/python -c "..."` first.

## Release — RE-SCAN BEFORE EVERY VERSION BUMP

**IMPORTANT: the paywall map is a snapshot of RTINGS' business decisions, not a fact about the API.
It WILL change, and it changes silently.** The 12/16 enforcement split (`RECON.md` §11.1) correlates
with category maturity, so a silo that serves full measurements today gets gated as it grows. Nothing
in the API announces this — the schema flag `insider_only` does not move, `has_paywall` is `true` on
all 28 regardless, and the only signal is the served `unblurred` value changing. **A stale map means
the server tells users a silo is fully answerable when it is now paywalled, or hides a silo that has
since opened up.**

So, as a release gate, before every version bump:

1. **Re-run the anonymous 28-silo scan** — the enforcement half of the smoke test (`SPEC.md` §10):
   per silo, `column_options` + `products_list` + one `test_results` over the current bench's leaf
   tests, anonymous, no cookie.
2. **Diff against `docs/enforcement-snapshot.json`** (the committed baseline, last scanned
   2026-09-03: 12 enforcing / 16 open).
3. **A diff is a SPEC CHANGE, not a test failure.** Do not "fix" the test to match. Update the
   snapshot, `RECON.md` §11.1, and the framing in `SPEC.md` §5 / this file's preamble, and say so in
   the release notes — the honest description of what anonymous gets is the product's main claim.
   **Also update the gated-category list inside `mcp.instructions` in `server.py`** — it is prose
   read by the calling LLM as the authoritative routing signal, it has no runtime fallback, and it
   was missing from this checklist. `test_the_instructions_gated_list_matches_the_enforcement_snapshot`
   fails if you forget. Same for the category tables in `README.md`.
   **Do not change `config.KNOWN_SILOS` for this** — that hint is about which silos *exist*, not
   which gate, and a silo outside it already warns `silo_hint_drift` rather than failing.
4. **Re-check dependency currency** — `mcp`, `wafer-py`, `ruff`, `pytest`, `pytest-asyncio`, and the
   Python floor against what is actually current. Floors were set 2026-09-03; a floor that has
   drifted two majors is a bug waiting to surface.
5. **Re-check the invariants too, not just the split** — they are what the normalizer is built on:
   - blur is still exactly `published:false ∨ (insider_only ∧ silo enforces)` — no third mechanism;
   - gating within a silo is still **per-product, never per-test**;
   - `status` domain is still `{tested, na, untested}`;
   - usage definitions still carry no `insider_only`.
   - **which best-of template each silo serves** (`RECON.md` §12.17) — mattress and
     running-shoes are server-rendered, the rest are `RecommendationVuePage`, and RTINGS is
     migrating. This drifts silently exactly like the paywall map: the symptom is
     `recommendations_missing`, and the fix is a parser, never a "that silo has no lists".
     One `rt_recommendations(silo, list=<first>)` per silo is the check.
6. **Never let the runtime read the snapshot.** It is a release-time diff baseline and documentation
   only. The server derives the boundary from observed `unblurred` per (silo, bench) on every fetch
   (`SPEC.md` §5) — a hardcoded map is exactly the bug this rule exists to catch.

The same applies to anything else in `RECON.md` measured once: it is dated, and a re-scan is cheap
compared to shipping a confident lie.

## Git

- **NEVER commit without explicit permission** — only when the user actually asks.
- **NEVER add Claude attribution.**
- **Always bump version** in `pyproject.toml` (patch by default; ask before minor/major).
- **Run the release re-scan above before any version bump.**
