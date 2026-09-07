# Paywall, normalizer and schema rules

> Moved verbatim out of `CLAUDE.md` on 2026-09-07 so the always-loaded file stays short.
> These are the project's hard-won rules: each one was forced by a measurement or a defect,
> and the date/`RECON.md` reference beside it says which. Read the file for the area you are
> touching before changing it; add new rules here, one bullet each, with the date and the
> evidence.

## Architecture facts

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

## The gate, the seven states, the two row shapes

- **IMPORTANT: the 16 "open" silos are METERED (measured 2026-09-07, `RECON.md` §14).**
  Anonymous reads `access_level 2, access_limit 3` on them: three product review pages, then
  the session drops to level 1 and `test_results`/`ratings` blur to exactly the previewed
  products (mattress 405 → 15 unblurred, camera 375 → 15). The unit is one PRODUCT, spent by
  the review page's **HTML GET** only — `page_body` and `side_by_side` do not count — and the
  state is the plain `product-previews` cookie. The 12 "enforcing" silos are where anonymous
  has no budget (`access_limit: null`, level 1). Every scan ran on a fresh jar, and so does the
  server: it never fetches a review as HTML. Consequences: (1) never add a review-HTML fetch
  path; (2) never strip, reset or persist `product-previews`; (3) say "metered, budget the
  server never spends", never "open"; (4) the blur rule for a fresh session is unchanged, so
  the normalizer, the observation store and the scan are all still correct for the server's
  own session — the correction is to the DESCRIPTION, and to what must never be built.
- **A silo's anonymous `access_level` on `/{silo}` is a per-silo enforcement signal** — level 1
  on exactly the 12 enforcing silos, 2 on the 16 (28/28 agree with the row sweep, `RECON.md`
  §14.1). One GET per silo; a candidate cross-check for the release scan, never a runtime map.
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

## Envelope semantics

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

## Benches

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

## Schema, catalog, units and values

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
  if the catalog later catches up) and serve them as a `coverage: uncatalogued` **summary
  of ids** (`SPEC.md` §8), rows only with `include_uncatalogued=true` or a `product_ids`
  filter. **They are RTINGS' internal copies and retests (identified 2026-09-06 by resolving
  four by id: "LG G5 OLED (Copy)", "Samsung QN90F (Copy)", "Boring Mattress - TBF 1.0.1",
  "…Pure Green Organic - TBF 1.0.1"), kept out of the listing on purpose** — 41 of
  mattress's 110, 23 of headphones', 9 of tv's. Ranking them by default handed shoppers a
  "(Copy)" as a pick and doubled every payload; the earlier "a third of mattress is
  unreachable" reading was wrong. `rt_product(<id>, silo=…)` resolves one through the
  compare tool's `product` block, which carries the review URL.
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
- **Never infer a unit** — but read the RIGHT one (corrected 2026-09-06, shopper round). A
  converted test ships **three** unit fields: `number_input_unit` (the unit of the machine
  `value` — monitor Height Adjustment stores centimeters), `number_display_unit` (what the site
  shows — inches) and `number_custom_unit` (" Hz" when `display_unit` is null). Labelling
  `value: 10.7` with the display unit reported eleven inches of monitor travel, 3 of 3 tests
  checked, plausible enough to ship. `unit` on every row is `TestDef.value_unit` (input unit,
  else display, else custom) and `display_unit` is added only when it differs. The cacheable
  schema carries a `cacheable_version`; a cached parse from before these fields is a MISS,
  not a hit, or the mislabel survives its 30-day TTL. **The review path is the other way
  round:** its number is parsed out of the DISPLAY string ("2.1 lbs (1.0 kg)" → 2.1), so a
  rendered value is labelled with the display unit — the first fix labelled rt_product's 2.1
  as kilograms, caught by the laptop scenario the same day. **Then the parenthesised figure
  wins:** RTINGS shows the stored unit beside the display one ("4.0 lbs (1.8 kg)"), and
  `_parenthesised_number` serves 1.8 kilograms with `display_unit: pounds`, so both paths
  label the same test the same way; a display with one figure keeps the display unit.
- **IMPORTANT: "Inf" is a VALUE RTINGS publishes, never a null and never a 1 (2026-09-06).**
  An OLED's contrast is `value: "Inf"`, `rendered_value: "Inf : 1"`. `float("Inf")` is
  accepted silently and then serialised as null, so the two best contrast readings on the
  bench read as empty rows; and the review path's number regex found the "1" in the unit text
  and served **1.0 — the worst possible contrast** — with no warning. Both parsers recognise
  `inf`/`infinity`/`∞` explicitly and carry a real `math.inf`, so sorting and filtering rank it
  above every finite value (`_comparable` maps it back); the wire says `value: null,
  is_infinite: true` plus `display`, because JSON has no infinity token.
- **Review path: a test with no display unit is shown in its input unit (member round S4,
  2026-09-07).** monitor `Total Response Time` declares `number_input_unit: milliseconds` and no
  display unit; RTINGS renders "0.2 ms". Read from the display unit alone the review row
  carried no `unit` while `rt_ratings` said milliseconds for the same test. `unit` is now the
  display unit, else the input unit; the parenthesised-value swap only runs when a display
  unit actually exists.
- **A VISIBLE Early Access row proves the surface; only a withheld one is excluded (S11).**
  `observe_test_rows` skipped every row of a `published:false` product, so a member's
  `rt_product` on `/early-access/tv/reviews/lg/b6e-oled` reported `insider_tests: unknown`
  beside three served insider values (and `usage_ratings: available` from the verdict loop,
  which already counted the visible verdicts). Excluding the blurred row is still right — it
  is withheld for the Early Access reason, not the gate — but a value that came through says
  the surface is served to this session whatever the publication state. An anonymous session
  sees those rows blurred, so the observation store is unchanged for it.
