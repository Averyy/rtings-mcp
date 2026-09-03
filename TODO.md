# TODO — rtings-mcp

Tracking for open research and decisions. Confirmed items are recorded in `RECON.md`; this file is
the working checklist. Facts land in `RECON.md`, not here.

## Decisions (human judgment — not measurable)

- [x] **Scope: all 28 silos, all-or-nothing.** Full coverage, no launch subset. (`SPEC.md` §2, §12)
- [x] **Posture: anonymous by default, max exposure; logged-in member is the primary use case.**
      (`SPEC.md` §2, §5)
- [x] **Graph/curve data served anonymously, with concrete controls** (local-only cache, on-demand,
      rate-limited, `RTINGS_ENABLE_GRAPH=false`). Note "no redistribution" is a stated licence term,
      **not** an enforced control — don't count it as one. (`SPEC.md` §3, §12)
- [x] **Build order: the anonymous surface does NOT wait on Phase 0.** Phase 0 needs Foundation
      (wafer session + headers) so it cannot come first, and nothing anonymous depends on it. Ship
      the `cache_tier` filename segment and coverage records from day one so member mode is never a
      migration. (`SPEC.md` §10)
- [ ] **If Phase 0 fails (member cookie does NOT flip the blur over the API): does a graph+catalog-
      only server still ship?** Now low-stakes: the JS investigation puts Phase 0 at **~90% likely to
      succeed** (`RECON.md` §5), so this contingency probably never triggers. Keep as a fallback
      decision, no longer a gate on buying the membership. (`SPEC.md` §13 q1)

## Research — needs a bought membership (Phase 0, capture all in one logged-in session)

Ordered by how much they gate. `SPEC.md` §10 holds the **full capture list (a–i)** — it is one
session, so a missed item costs another membership month. Highlights:

- [ ] **q1 — does a member/insider cookie flip `unblurred:true` on the API?** BLOCKING for member
      mode, but **~90% expected to succeed** (`RECON.md` §5). Run one `table_tool__test_results` call
      with a logged-in `_rtings_session` **+ real browser headers** (`SPEC.md` §9). Fallback ladder if
      blurred: add headers → try `app/product_vue_page__page_body` with `url_path` → then failed.
- [ ] **NEW — capture a logged-in `GLOBALS.session` in full.** Which field separates `member` from
      `free` is **unknown**; only the anonymous shape has ever been measured. The auth classifier
      cannot be written without it. Redact before committing as a fixture (name/email live in
      `current_user`). (`SPEC.md` §10 capture a, §13 q6)
- [ ] **NEW — is the HTML probe served from CloudFront's anonymous cache when a member cookie is
      sent?** Check `X-Cache`/`Vary`. If it is, a live member is permanently classified `expired`.
      (`SPEC.md` §10 capture f)
- [ ] **NEW — capture the exact `test_results` body a logged-in front end sends.** `SPEC.md` §7
      reasons about "beyond what a logged-in front end sends" without anyone having seen one.
- [ ] **NEW — run the same request with and without the browser headers.** Turns the one
      "unprovable" origin-validation risk into a measured one, in the only session that can do it.
- [ ] **NEW — fetch one curve with the cookie and diff against the anonymous copy.** `graphs/` is
      untiered on the assumption they are identical. (`SPEC.md` §10 capture g)
- [ ] **q2 — confirm the free/metered-unlock lead.** No longer optional: the meter sits on
      `rt_product`'s endpoint, so free-vs-member must be distinguished or a preview reads as
      membership. Capture `previewed_products`/`access_limit` before and after one call to learn the
      meter's unit. (`RECON.md` §10 q2, `SPEC.md` §13 q3)
- [ ] **q3 — does the 30-day `_rtings_session` slide on use, and does a pasted value survive a
      server-side re-mint?** Gates the env-var path and whether a Playwright `login` extra is needed.
      (Starts a clock — note the capture date.)
- [ ] **q7 — minor-bench comparability** (are v2.0.1/v2.1/v2.2 rescored or additive?). Needs member
      scores to compare. Decides whether the default comparable population is one bench or the set.

## Research — answerable anonymously

- [ ] **q16 — does enforcement hold on LEGACY benches, the review path, and non-leaf kinds?** Both
      sweeps covered only each silo's **current** bench and its first 40–50 `number`/`word` leaf
      tests. Non-blocking — the server derives the boundary per fetch anyway — but it bounds what
      `RECON.md` §11 may be cited for. (`RECON.md` §10 q16)
- [ ] **q15 — what decides whether a silo enforces?** Probably NOT answerable from the API; it is a
      business decision. Tracked so nobody re-derives the falsified "maturity" story.
      (`RECON.md` §10 q15)
- [ ] **q6 — API behaviour under sustained / authenticated volume.** Answered **passively** from
      `telemetry/requests.jsonl` (`SPEC.md` §9) during real use — record `retry-after`,
      `x-ratelimit-*`, `x-cache`, `age`; never induce a limit. Confirmed 2026-09-03 that `robots.txt`
      has **no `Crawl-delay`** and no rate-limit headers are served, so the bucket defaults rest on
      politeness, not on a stated ceiling. The 28-silo smoke test is the first sample.

## Resolved (moved to RECON.md)

**Anonymous sweep 2026-09-03 — 41,944 rows, 10 silos (`RECON.md` §11):**

- [x] **q10 — NO recommendations API exists.** A full browser network capture of
      `/tv/reviews/best/tvs` fires exactly two `/api/v2/safe/` POSTs — `comment_list__comments` and
      `price_box__batch_prices`, neither carrying recommendation content. Page extraction is the only
      path. Also: best-of slugs **redirect** (`/best/tvs` → `/best/tvs-on-the-market`). (§11.7)
- [x] **`status` has a THIRD value — `untested`** (154 rows). The table path emits *both* an absent
      row and an explicit `untested`; map it to `not_tested`, never `unknown_row_status`. (§11.3)
- [x] **`status:"na"` IS frequently `unblurred:true`** — 1,025 of 2,186 (47%). The old "all 4 observed
      were `unblurred:false`" was a TV artifact. Proves "branch `status` before `unblurred`" necessary
      in both directions. (§11.4)
- [x] **`last_updated_at` is a bulk-job field, NOT a retest signal** — 65 of 74 cameras share one
      minute, 60 of 110 mice share another. It cannot close the `coverage_stale` hole in either
      direction; the `as_of` stamp + shorter current-bench TTL stands as the mitigation. (§11.6)
- [x] **MAJOR — `insider_only` is not the gate.** Enforcement is **per silo**: tv/headphones/monitor/
      mouse/printer/laptop gate 100%; air-purifier/mattress/vpn serve 100% of `insider_only` rows
      unblurred, with real measurements and scores. All 28 are `has_paywall:true`. Derive from
      observed `unblurred` per (silo, bench). (§11.1)
- [x] **MAJOR — `published:false` is a second, orthogonal blur** that no membership lifts. Detectable
      anonymously from `products_list`. Must be checked before calling any blur `tested_gated`. (§11.2)
- [x] **Usage defs carry no `insider_only`** — re-confirmed across 10 silos (was 4). (§11.5)
- [x] **`test_benches[].tests[]` is a reference list** (`{original_id}` only); definitions live in
      `test_bench.tests` + `legacy_tests`. Joining the wrong one yields an empty schema silently.
      (§11.5)

- [x] **Best-of list discovery.** The silo landing page (`/{silo}`) enumerates them as
      `/{silo}/reviews/best/<slug>` links (15 on TV). `rt_recommendations(silo)` extracts those hrefs.
      (`SPEC.md` §13 q9, resolved 2026-09-03)
- [x] **Usage definitions do NOT carry `insider_only`** (tv/headphones/monitor/mouse checked; only
      *test* defs do). `scores_available.usage_ratings` derives from observed `unblurred`.
      (`SPEC.md` §13 q7, resolved 2026-09-03)
- [x] **Storage is JSON files, not SQLite** (`SPEC.md` §8, §12, 2026-09-03). The response *is* the
      coverage record; the file path *is* the key.
- [x] **Rate limiting redesigned** (`SPEC.md` §9, 2026-09-03) — token bucket we own, `asyncio`
      semaphore, per-host `cooldown_until` honouring `Retry-After` (wafer does not, under
      `max_rotations=0`), separate uncredentialed CDN session. The flat `2.0 s` floor stays only as
      the sustained refill rate; its "looks like a browser" justification was wrong and is dropped.
- [x] **Two design defects found and fixed 2026-09-03** — the `cache_tier`/`data_tier` conflation
      (permanent refetch loop on public tests, free accounts and all-`na` slices) and coverage with
      no time dimension (false `not_tested` for any product newer than the cached slice). Both would
      have shipped under SQLite too.

- [x] **q5 — per-silo paywall boundary generalizes.** Each silo gates all but a few `word` spec
      tests (TV 6, headphones 8, mattress 4). **Amended 2026-09-03:** the boundary is per-**(silo,
      bench)** — TV bench 227 exposes 6 public tests, TV bench 2 exposes 1. Derive per (silo, bench).
      (`RECON.md` §2)
- [x] **q8 — client `unblur_product_ids` is ignored anonymously.** Server decides blur by session,
      not request params. (`RECON.md` §5)
- [x] **q9 — not-tested = an ABSENT row.** **Amended 2026-09-03:** "every present row is
      `status:"tested"`" holds for the *table* tool only. The review body also emits `status:"na"`
      (not applicable), which is byte-identical to a gated row on `unblurred` alone → **four**-state
      normalizer, `status` branched before `unblurred`. (`RECON.md` §6, `SPEC.md` §5/§7)
- [x] **q11 — per-silo default bench set is `is_recent:true`** on the page-embedded `GLOBALS` bench
      list. Set size varies (tv 3, headphones 4, mouse 2); `[197,210,227]` is the TV answer, not the
      rule. "Top 3" and "same major version" both tested and both fail. (`RECON.md` §8)
- [x] **Review-path row shape.** One row per test in the product's **own bench** (54/54 on v0.9,
      402/402 on v2.2), never the whole silo; and the row has **no `value` key** — `rendered_value`
      (HTML) + `score` instead. Two row shapes to parse. (`RECON.md` §6)
- [x] **Public tests ship their 0–10 score anonymously** (Resolution `"4k"` → `10.0`). "Anonymous
      gets no scores" was wrong; `insider_only` gates value and score together, and 100%-gated is
      true only of *usage* ratings. (`RECON.md` §2)
- [x] **Curves confirmed public on a blurred row** — the gated scalar and its open-CDN curve sit one
      field apart in the same response. Graph coverage is sparse and bench-dependent (5/402 on bench
      227, **0/54** on bench 2), so `rt_graph` needs a `graph_not_available` outcome distinct from
      `no_graph`. CDN needs no cookie — fetch it uncredentialed. (`RECON.md` §4)
- [x] **Auth marker location — the JSON API carries no auth field.** Auth is HTML-`GLOBALS.session`
      only; the two-source join is the design. (`RECON.md` §1, `SPEC.md` §6)
- [x] **JS-bundle investigation — member data flows through the same API.** Client displays a
      server-set `unblurred` flag verbatim; no client-side unblur, no CSRF, cookie is sole credential;
      member CSV export serializes these same rows → Phase 0 ~90%. `share_token` = member gift-links
      (q4 largely mapped); free meter is server-side via `url_path` (q2 lead). (`RECON.md` §5, §10)
