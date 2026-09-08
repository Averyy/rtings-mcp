# Cache rules

> Moved verbatim out of `CLAUDE.md` on 2026-09-07 so the always-loaded file stays short.
> These are the project's hard-won rules: each one was forced by a measurement or a defect,
> and the date/`RECON.md` reference beside it says which. Read the file for the area you are
> touching before changing it; add new rules here, one bullet each, with the date and the
> evidence.

- **`cache_tier` keying is on the gated surfaces only.** `tests/`/`ratings/`/`reviews/`/
  `verdicts/` and — **since 2026-09-06 — `recs/<silo>/<list>`** are tier-keyed;
  `schema/`/`catalog/`/`graphs/`/`bench/` and `recs/<silo>/_lists.json` are **not** — they carry
  no gated fields (`RECON.md` §3, §4), so tiering them would store two identical copies. **A
  best-of page DOES carry gated fields**: each pick's `featured_test_results[]` and `ratings[]`
  have their own `unblurred` bit, and untiered it served a member a two-day-old anonymous copy
  with every featured score `tested_gated` and nothing suggesting a refresh (found by the
  2026-09-06 shopper round). Its demotion and anonymous-label guard reuse the `tests` predicates
  with the featured stub as the insider id. Scored rows are never overwritten by unscored ones;
  selection is on the **data** (`unblurred`), not the tier (ported).
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
- **`verdicts/` demotion keys on the usage SCORES, never on `user_has_access`** — still the rule,
  but the *reason* changed (measured 2026-09-06, `RECON.md` §13.5). The fear was that the flag might
  never flip for a member on a gated silo, which would demote every member write there, miss every
  read and refetch forever. **It does flip**: `true` on TV with a member cookie, against `false` on
  TV / `true` on mattress anonymously — so it tracks membership *and* enforcement, and the deadlock
  is retired. Keep keying on the scores anyway: all-null scores where the tier predicts otherwise is
  the same evidence and depends on nothing about what the flag means, so it stays correct if RTINGS
  changes the flag's semantics. Capture (p) is closed.
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
- **`articles/<silo>/<slug>.json` is untiered, like `recs/<silo>/_lists.json` (2026-09-08).** A
  `learn` page is prose: measured (`RECON.md` §12.19) it carries no `unblurred` bit, no test row
  and no `insider_only`, so there is nothing for a tier to distinguish and tiering it would store
  two identical copies. It shares `TTL_RECS` (7 days) with the best-of surfaces, which is the same
  kind of editorial content on the same refresh rhythm. Note the contrast with `recs/<silo>/<list>`
  — that one **is** tier-keyed, because a best-of page's featured rows do carry gated values.
