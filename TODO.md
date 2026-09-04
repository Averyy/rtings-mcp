# TODO — rtings-mcp

Tracking for open research and decisions. Confirmed items are recorded in `RECON.md`; this file is
the working checklist. Facts land in `RECON.md`, not here.

**Status 2026-09-04: the anonymous server is built and working.** All seven tools run against the
live API, 206 offline + 9 live tests pass, and the release-gate re-scan reproduces the 12-enforcing /
16-open map with zero drift. What the build measured is in `RECON.md` §12. Everything still open is
below.

## Built

- [x] **Foundation** — two wafer sessions (credentialed origin / uncredentialed CDN), token bucket +
      semaphore + cross-process `cooldown_until`, JSON file cache with the `cache_tier` filename
      segment, coverage generations, atomic writes and cross-process locks.
- [x] **Parsing** — `column_options` schema parser, the seven-state normalizer for **three** row
      shapes (table `value`, review `rendered_value`, ratings — which carries no `status` at all,
      `RECON.md` §12.3), the two-source auth join, `scores_available` derived per (silo, bench).
- [x] **Anonymous tools** — all seven, plus the catalog filter/sort engine with the gated-field
      guard, and `rt_silos()` routing on **observed** enforcement.
- [x] **Member layer, gated off** — tier selection, write-time demotion and the preview budget are
      implemented and unit-tested behind `RTINGS_MEMBER_MODE` (default `false`). Rotation
      write-back is deliberately **not** implemented (`SPEC.md` §10).
- [x] **Release gate** — `rtings-mcp scan` and `pytest -m "live and slow"` both re-derive the
      per-silo map and diff it against `docs/enforcement-snapshot.json`.

## Research — needs a bought membership (Phase 0, capture all in one logged-in session)

Ordered by how much they gate. `SPEC.md` §10 holds the **full capture list (a–i)** — it is one
session, so a missed item costs another membership month. Highlights:

- [ ] **q1 — does a member/insider cookie flip `unblurred:true` on the API?** BLOCKING for member
      mode, but **~90% expected to succeed** (`RECON.md` §5). Run one `table_tool__test_results` call
      with a logged-in `_rtings_session` **+ real browser headers** (`SPEC.md` §9). Fallback ladder if
      blurred: add headers → try `app/product_vue_page__page_body` with `url_path` → then failed.
      **On success: set `RTINGS_MEMBER_MODE=true` and the whole tier mechanism is already live.**
- [ ] **capture a — a logged-in `GLOBALS.session` in full.** Which field separates `member` from
      `free` is **unknown**; only the anonymous shape has ever been measured. The classifier
      currently calls a logged-in session `member` only on positive evidence
      (`has_insider_access`, or `access_level > preview_level`) and `free` otherwise, and says in
      the envelope's `note` that the boundary is provisional. Redact before committing as a fixture.
- [ ] **capture f — is the HTML probe served from CloudFront's anonymous cache to a member cookie?**
      Check `X-Cache`/`Vary`. The code already records the probe's `X-Cache` and refuses to demote
      on a cache hit; this measurement is what tells us whether that guard ever fires.
- [ ] **capture c — the exact `test_results` body a logged-in front end sends.**
- [ ] **NEW — is `app/side_by_side__review` metered?** It is the public compare tool rather
      than the review page, and a tool that spent a preview per comparison would be unusable
      — but that is reasoning, not measurement. Check `previewed_products` before and after
      an `include_verdicts` call on the free account. Cheap, and it is in Stage 1.
- [ ] **NEW — does `user_has_access` flip for a member on a gated silo?** It is `false` on TV
      and `true` on mattress anonymously, so it tracks silo enforcement. If a membership
      flips it on TV, it is a second, independent confirmation of q1 — and it is an auth
      marker *inside* the API, which §1 says does not exist.
- [ ] **capture d — the same request with and without the browser headers.** Turns the one
      "unprovable" origin-validation risk into a measured one.
- [ ] **capture g — one curve fetched with the cookie, diffed against the anonymous copy.**
      `graphs/` is untiered on the assumption they are identical.
- [ ] **q2 — the metered preview's unit** (per product, per session, per day). The budget control is
      built and enforced; only the constant is unknown. Capture
      `previewed_products`/`access_limit` before and after one `rt_product` call.
- [x] ~~**q3 / capture e — does the session slide?**~~ **ANSWERED 2026-09-04, anonymously,
      no membership needed** (`RECON.md` §12.15). It slides on *every* response, HTML and API
      alike, with a fresh 30-day expiry each time — so a session lives indefinitely with use
      and dies 30 days after it stops. Rotation write-back is now implemented, gated on a
      probe proving `current_user` non-null. What remains for Phase 0 is only to confirm the
      same holds for a logged-in session (it is the same Rails mechanism).
- [ ] **q7 — minor-bench comparability** (are v2.0.1/v2.1/v2.2 rescored or additive?). Needs member
      scores to compare. Decides whether the default comparable population is one bench or the set.

## Research — answerable anonymously

- [ ] **q16 — does enforcement hold on LEGACY benches, the review path, and non-leaf kinds?** Both
      sweeps covered only each silo's **current** bench and its first 40–50 `number`/`word` leaf
      tests. Non-blocking — the server derives the boundary per fetch anyway — but it bounds what
      `RECON.md` §11 may be cited for. (`RECON.md` §10 q16)
- [ ] **usage ratings on the other 15 open silos.** `RECON.md` §12.3 falsified "assume gated there"
      for mattress (`Side Sleeping` = 7.7, unblurred, anonymous). Whether that generalizes is
      unmeasured. Cheap to check: one `table_tool__ratings` call per open silo.
- [ ] **the 9 orphan TV products** (`RECON.md` §12.2) — ids that return `test_results` rows while
      appearing in no `products_list` across all 18 benches. Almost certainly reviews in progress
      that the catalog filters out entirely, but that is inference, not measurement.
- [ ] **q15 — what decides whether a silo enforces?** Probably NOT answerable from the API; it is a
      business decision. Tracked so nobody re-derives the falsified "maturity" story.
      (`RECON.md` §10 q15)
- [ ] **q6 — API behaviour under sustained volume.** Answered **passively** from
      `telemetry/requests.jsonl` during real use — the file now accumulates
      `retry-after`, `x-ratelimit-*`, `x-cache`, `age` per response. The 28-silo re-scan
      (~140 requests over ~3.5 min at the default rate) is the first sample and hit no limit.

## Product

- [ ] Flip the repository public.
- [x] ~~Null fields survive the output model.~~ **DONE 2026-09-04.** Measured on the wire,
      not the raw dict: the model was re-adding every declared field as `null`, so one
      `rt_product` response went from 55,857 bytes of content to **113,406** delivered, and a
      gated row from 168 to 424 bytes. Row models now omit their null optionals
      (`rt_product` −51%, ~36% across the six most common responses). `value`, `gated` and
      `status` are exempt, and envelopes are untouched, so `out["error"] is None` still
      works — with four tests pinning exactly that.
- [ ] **Nest `data.results` by hierarchy** (SPEC §7 says "grouped by hierarchy"; the code
      returns a flat list repeating `hierarchy` on every row, ~23% of a 56 KB response).
      Recommended by review, deliberately deferred as its own change.
- [ ] Confirm `rtings-mcp` is free on PyPI before publishing.
- [ ] Decide whether `rt_ratings` should default to projecting the category's public tests, so a
      bare `rt_ratings("tv")` returns something numeric rather than catalog-plus-gated-scores.

## Decisions (human judgment — not measurable)

- [x] **Scope: all 28 silos, all-or-nothing.** (`SPEC.md` §2, §12)
- [x] **Posture: anonymous by default, max exposure; logged-in member is the primary use case.**
- [x] **Graph/curve data served anonymously, with concrete controls** (local-only cache, on-demand,
      rate-limited, `RTINGS_ENABLE_GRAPH=false`). "No redistribution" is a stated licence term,
      **not** an enforced control.
- [x] **Build order: the anonymous surface did NOT wait on Phase 0**, and did not need to.

## Resolved during the build (moved to `RECON.md` §12)

- [x] `app/product_vue_page__page_body` returns at **`data.page`**, review at
      `data.page.product.review`.
- [x] `table_tool__ratings` rows carry **no `status`** — a third row shape, not a variant.
- [x] `test_results` has a **wider product population** than `products_list`.
- [x] **`latest_test_bench_id` can name a bench that has no schema and is not `is_recent`** — 5 of
      28 silos. Deriving "current bench" from it defeats the current-bench TTL.
- [x] Best-of discovery is **`silo_layout.best`** (20 lists on TV vs 5 by href scan); slugs are
      frequently **two segments**.
- [x] Recommendation `featured_test_results[].test` carries **no `original_id`** — unjoinable.
- [x] Review prose lives on **`group` rows** and survives the blur.
- [x] `has_insider_access` is inside an **HTML-escaped** `data-props` attribute.
- [x] The SDK class is **`MCPServer`**, not `FastMCP`.

## Resolved before the build (in `RECON.md` §11)

- [x] **q10 — there is NO recommendations API.** Page extraction is the only path. (§11.7)
- [x] **`status` has a third value, `untested`** (154 rows). (§11.3)
- [x] **`status:"na"` IS frequently `unblurred:true`** — 1,025 of 2,186 (47%). (§11.4)
- [x] **`last_updated_at` is a bulk-job field, NOT a retest signal.** (§11.6)
- [x] **MAJOR — `insider_only` is not the gate.** Enforcement is per silo; 12 of 28. (§11.1)
- [x] **MAJOR — `published:false` is a second, orthogonal blur.** (§11.2) **Corrected 2026-09-04:
      it is Early Access, and a membership DOES lift it** — RECON §12.10.
- [x] **Usage defs carry no `insider_only`**; `test_benches[].tests[]` is a reference list. (§11.5)
- [x] **q5 / q8 / q9 / q11** — per-(silo, bench) boundary; client `unblur_product_ids` ignored;
      not-tested row shapes; `is_recent` as the default bench set.
- [x] **Storage is JSON files, not SQLite**; **rate limiting is a token bucket we own**; the
      **`cache_tier` / `data_tier` split**; **coverage carries a time dimension**.
