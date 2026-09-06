# TODO — rtings-mcp

Tracking for open research and decisions. Confirmed items are recorded in `RECON.md`; this file is
the working checklist. Facts land in `RECON.md`, not here.

**Status 2026-09-06: built, working, and signed in.** Three review rounds (15 defects, then 10,
then 7 more found while testing the sign-in end to end — see Built, below). All seven data tools
run against the live API, **382 offline + 9 live tests pass**, and the release-gate re-scan
reproduces the 12-enforcing / 16-open map with zero drift — re-run today *with* a membership
stored, which the scan ignores by construction. What the build measured is in `RECON.md` §12; what
the membership measured is in §13.

**Phase 0 is DONE for everything a membership can answer** (`RECON.md` §13): q1 is YES, the
member/free field is `current_user.is_insider`, the browser headers turn out not to matter,
`user_has_access` flips, and member/anonymous curves are byte-identical. `RTINGS_MEMBER_MODE`
therefore defaults to **`true`** as of 2026-09-06. The only questions left need a **free**
account — see below.

## Handoff — read this before touching anything (2026-09-06)

### Where things stand

- **A real membership is signed in.** The credential lives at `~/.config/rtings-mcp/session.json`
  (`0600`), and it **rotates on use** — the session slides, so it stays alive as long as the server
  is used and dies 30 days after it stops. Observed rotating and being persisted correctly today.
- **`RTINGS_MEMBER_MODE` now defaults to `true`** (v0.2.0). Setting it to `0` still works and is
  what the anonymous-label guard exists for.
- **Committed 2026-09-06 as v0.2.0** — the sign-in, the guards and the docs are on `main`.
  The project still forbids committing unasked.
- **The real cache now holds member data** — `tests/*/141.member.*` for tv, written 2026-09-06
  by a signed-in `rt_ratings` through this session's own MCP connection. Everything else in
  `~/.cache/rtings-mcp` is anonymous slices from 2026-09-04/05.
- **Any MCP server process you find running is STALE** — they predate today's code. Restart the
  client before testing through MCP, or you will be testing the old build.

### Already verified live — do not spend a session redoing these

Sign-in end to end (window → cookie → validate → store → in-process adopt, browser reaped, no PII
stored); Phase-0 q1 and captures a, c, d, f, g, p (`RECON.md` §13); the release scan's anonymity
guard with a real cookie on disk (12/16, zero drift); the write guard live on tv and mattress in
both flag states; member-tier writes and cache hits; **382 offline + 9 live tests**, the offline
suite hermetic even under a hostile environment.

### Verified 2026-09-06 over the MCP wire (a fresh stdio server per run, scratch cache)

Driven with a stdio `ClientSession` against `.venv/bin/rtings-mcp`, so `server.py`'s wrappers
and the Pydantic output models serialized every response. Then repeated once through this
session's own installed MCP connection.

1. **Member wire shape** — `rt_ratings("tv", tests=["141","461"])`: `value: 5658.0, gated:
   false, insider_only: true, score: 9.7, unit, display, as_of` all present; `data_tier:
   unblurred`; `scores_available.insider_tests: available`. Usage scores on the ratings surface
   come back `tested_visible` with a real score, including on an Early Access product, which is
   correct for a member. Files land as `tests/<bench>/<test>.member.<ts>.json`.
2. **`rt_silos()` after a member fetch** — tv reports `unknown`, `observed/tv.json` carries
   `provenance: logged_in` on every bench. After an anonymous-provenance observation (the
   expired-cookie run below) tv reports `gated`. Provenance has not regressed.
3. **`rt_product` as a member** — X90L, LG C5, TCL QM8K: 14/14 rows `tested_visible` with
   values, `previews_remaining: null`, no `consume_preview` demanded, `reviews/<id>.member.*`
   written. An Early Access review (`/early-access/tv/reviews/lg/b6e-oled`) serves values with
   the Early Access notice saying 14 came through; the same call on a dead cookie serves
   `review_unpublished, gated: null`, never `tested_gated`.
4. **`rt_graph`, `rt_recommendations`, `rt_search`, `rt_schema` signed in** — all fine; graphs
   134/22 points unresampled, the 65-inch best-of list gives 6 picks with reasoning.
5. **Expired session** (garbage cookie in a scratch `RTINGS_CONFIG_DIR`) — probe classifies
   `expired`; `rt_auth_status` says so; every write is labelled `anonymous`; the garbage cookie
   is left on disk untouched (no anonymous re-mint written back); `rt_product` serves the gated
   shape with no preview demand. `rt_sign_in`'s guard (read, not run — it opens a window)
   refuses only when the cached probe says `logged_in`, so a rejected cookie renews without
   `force`.
6. **`RTINGS_MEMBER_MODE=0` with the live cookie** — rows served unblurred, nothing written,
   two `not_cached` warnings naming the real cause, `rt_silos` tv stays `unknown`.
7. **Legacy bench / review path for a member (q16, partial)** — `rt_ratings("tv",
   bench=["197"], tests=["141"])` serves bench-197 values unblurred; every review-path product
   tried is on 227, so the review path on a legacy bench is still unobserved.
8. **Two clients, one cache dir** — two stdio servers issuing the same uncached `rt_ratings` +
   `rt_product` simultaneously: both succeed, exactly one file per key, the second served
   `from_cache: true`. The `uncatalogued` warning fires only on the fetching call; the group
   itself is in both responses.

Also confirmed: the credential rotates on disk with every run (`stored_at` advances), and the
`rt_auth_status` `session: unknown` on a fresh process is by design (no probe yet) — the first
data tool fills it in.

### Still NOT tested

1. **q2 and capture (o) — need a FREE account.** A membership cannot answer them; see below.
2. **The review path on a legacy bench for a member** (the other half of q16).
3. **`rt_sign_in` end to end on an expired cookie** — the guard was read, not run.

### Cautions

- **Use a scratch `RTINGS_CACHE_DIR` for experiments**, so probing does not fill the cache the
  server serves from.
- **Never commit a fixture containing unblurred member values**, and redact any `GLOBALS.session`
  capture — the real one carries the user's email, username and numeric user id.
- **Do not add a new scan/measurement path without the anonymity guard** that `rtings-mcp scan`
  uses. A member session measuring enforcement reports every gated silo as open.
- The delegated-review MCP (`codex-dobby`) had **expired auth** on 2026-09-06 and returned zero
  findings while reporting success. If you delegate a review, check it actually ran.

```bash
.venv/bin/pytest tests/ -q                 # 382 offline, ~4 s
.venv/bin/pytest -m live -q                # 9 live, anonymous, ~4 min
.venv/bin/ruff check src/ tests/
.venv/bin/rtings-mcp auth --status         # what credential is stored, and its session
.venv/bin/rtings-mcp scan --silo tv        # release gate, one silo (anonymous by construction)
```

## Built

- [x] **Foundation** — two wafer sessions (credentialed origin / uncredentialed CDN), token bucket +
      semaphore + cross-process `cooldown_until`, JSON file cache with the `cache_tier` filename
      segment, coverage generations, atomic writes and cross-process locks.
- [x] **Parsing** — `column_options` schema parser, the seven-state normalizer for **three** row
      shapes (table `value`, review `rendered_value`, ratings — which carries no `status` at all,
      `RECON.md` §12.3), the two-source auth join, `scores_available` derived per (silo, bench).
- [x] **Anonymous tools** — all seven, plus the catalog filter/sort engine with the gated-field
      guard, and `rt_silos()` routing on **observed** enforcement.
- [x] **In-conversation sign-in (2026-09-06)** — `rt_sign_in` / `rt_auth_status`, ported in shape
      from `consumer-reports-mcp`: a background task plus a 45 s long-poll, because Claude Desktop
      kills a tool call at 60 s and offers no terminal for `rtings-mcp auth`. A headed Playwright
      window (optional `[browser]` extra) opens on RTINGS' login page; the human types and the
      server reads one boolean, then one cookie. Also `rtings-mcp auth --browser` for a terminal.
      **The ready signal is `GLOBALS.session.current_user`, never the cookie's presence** — RTINGS
      mints `_rtings_session` anonymously and rotates it on every response, so a CR-style
      "the token appeared" poll would capture an anonymous session and overwrite a working
      credential. Validated against RTINGS before anything is stored; `free` counts as signed in.
- [x] **Member layer, now ON** — tier selection, write-time demotion and the preview budget are
      implemented and unit-tested behind `RTINGS_MEMBER_MODE`, **on by default since
      2026-09-06** now that `RECON.md` §13.1 measured the gate opening.
- [x] **Rotation write-back IS implemented** (corrected here 2026-09-06; this file still said
      "deliberately not implemented", which `RECON.md` §12.15 reversed and the code had already
      followed). The session slides on use, so refusing to persist a rotation caused the monthly
      re-paste it was meant to prevent. Gated on proof: only a jar value from a response the HTML
      probe saw logged in, and only for a file-sourced credential.
- [x] **Release gate** — `rtings-mcp scan` and `pytest -m "live and slow"` both re-derive the
      per-silo map and diff it against `docs/enforcement-snapshot.json`. Verified 2026-09-06 that
      the scan's anonymity guard holds with a real member cookie on disk: all 12 enforcing silos
      still scanned `unblurred 0`. That guard had never been exercised against a live credential.
- [x] **The anonymous-label write guard (2026-09-06)** — `SPEC.md` §8. With `RTINGS_MEMBER_MODE=0`
      a live credential still returns member data, which cannot honestly be stamped `anonymous`
      and cannot be stamped higher either, so the write is **refused**, the rows are still served,
      and a `not_cached` warning names the real cause. The load-bearing condition is proof that a
      signed-out fetch sees the same thing: **16 of 28 silos serve `insider_only` rows unblurred
      to anonymous**, so the naive "unblurred insider rows ⇒ member-only" predicate would refuse
      every legitimate write on more than half the catalog.
- [x] **Observation provenance (2026-09-06)** — `SPEC.md` §7. `data_completeness` is a claim about
      what *anonymous* gets, so `record()` now requires a `provenance` and only an anonymous
      observation may say `full`. Without it a member's fetch of a gated silo made `rt_silos()`
      report it `full` permanently on that machine — the stale-map lie the release re-scan exists
      to catch, reached locally.
- [x] **The offline suite is hermetic (2026-09-06)** — an autouse fixture in `tests/conftest.py`
      redirects the cache and config dirs and clears the auth env vars. `test_server.py` reaches a
      tool through the process-wide `get_context()`, so before this a signed-in developer's real
      `~/.cache` made it fail. Whether the suite passes must not depend on who is signed in.

## Research — Phase 0, MEASURED 2026-09-06

**The membership was bought and signed in on 2026-09-06, and every question a member account
can answer is now answered** — the facts are in `RECON.md` §13, which is the citable record;
this list is only the checklist. The two that remain need a **free** account, not a member one.

- [x] ~~**q1 — does a member/insider cookie flip `unblurred:true` on the API?**~~ **YES**
      (`RECON.md` §13.1). tv bench 227, 6 `insider_only` tests x 98 products: member
      **588/588 unblurred with values**, anonymous **0/588**, identical call. It succeeded on
      the first rung — no header ladder, no `page_body` fallback. tv is an *enforcing* silo, so
      this is the gate opening rather than an open silo answering. The tier mechanism is now
      justified by measurement; enabling it is a flag, not a migration.
- [x] ~~**capture a — a logged-in `GLOBALS.session` in full.**~~ **ANSWERED** (`RECON.md` §13.2).
      **`current_user.is_insider` is the member/free field** — a literal boolean inside the
      object the probe already parses, so the boundary stops being provisional. `access_level: 3`,
      `preview_level: 2`, `access_limit: null`, `previewed_products: []`. The three older signals
      (`user_is_insider`, `has_insider_access`, `access_level > preview_level`) all agreed and
      stay as corroboration.
- [x] ~~**capture f — CONFIRM ONLY.**~~ **CONFIRMED** (`RECON.md` §13.4). With the cookie,
      `/tv/tools/table` returns `x-cache: Miss from cloudfront` and
      `cache-control: max-age=0, private, must-revalidate`. The CloudFront demotion guard should
      never fire; it stays a guard, not a design assumption.
- [x] ~~**capture c — the exact `test_results` body a logged-in front end sends.**~~ **ANSWERED
      BY CONSTRUCTION** — the body this project already sends returned fully unblurred member
      data in §13.1, so there is nothing to adjust for the member case.
- [x] ~~**capture p — does `user_has_access` flip for a member on a gated silo?**~~ **YES**
      (`RECON.md` §13.5). `true` on tv with the cookie (anonymously `false` on tv, `true` on
      mattress), 11/11 usage score sets scored. So it tracks membership *and* enforcement, and it
      is a real auth marker inside the API. It does **not** change the rule that `verdicts/`
      demotion keys on the usage scores — the deadlock that rule guards against is simply retired.
- [x] ~~**capture d — the same request with and without the browser headers.**~~ **ANSWERED**
      (`RECON.md` §13.3): 98/98 unblurred **both ways**. There is no server-side origin validation
      on `/api/v2/safe/`; the one "unprovable" risk is measured and absent. The headers stay
      because they cost nothing, but nothing depends on them.
- [x] ~~**capture g — one curve fetched with the cookie, diffed against the anonymous copy.**~~
      **IDENTICAL** (`RECON.md` §13.6): same CDN path, byte-identical JSON. `graphs/` stays
      untiered, and the CDN still needs no cookie.

### Still open — these need a FREE account, not a member one

A member has no meter (`access_limit: null`, `previewed_products: []`), so no amount of member
session time can measure the preview budget. The control is built and enforced; only the constant
is unknown.

- [ ] **q2 — the metered preview's unit** (per product, per session, per day). Capture
      `previewed_products`/`access_limit` before and after one `rt_product` call **on a free
      account**.
- [ ] **capture o — is `app/side_by_side__review` metered?** Check `previewed_products` before and
      after an `include_verdicts` call **on a free account**. Reasoning says no (it is the public
      compare tool, not the review page), but that is reasoning, not measurement.

- [x] ~~**q3 / capture e — does the session slide?**~~ **ANSWERED 2026-09-04, anonymously,
      no membership needed** (`RECON.md` §12.15). It slides on *every* response, HTML and API
      alike, with a fresh 30-day expiry each time — so a session lives indefinitely with use
      and dies 30 days after it stops. Rotation write-back is now implemented, gated on a
      probe proving `current_user` non-null. What remains for Phase 0 is only to confirm the
      same holds for a logged-in session (it is the same Rails mechanism).
- [ ] **q7 — minor-bench comparability** (are v2.0.1/v2.1/v2.2 rescored or additive?). Needs member
      scores to compare. Decides whether the default comparable population is one bench or the set.

## Product — from the second best-of template (2026-09-05)

- [ ] **"Notable Mentions" is not extracted on the server-rendered template.**
      `recommendation_mentions` comes back `[]` there while the props template populates it, and
      the section is plainly on the page. Nothing surfaces mentions today, so this is latent
      rather than broken.
- [ ] **The static template's featured `target_id` cannot be joined to the schema.** Its
      `DistributionTooltip` gives `target_label` / `target_type` and an id in a different
      namespace — Side Sleeping is `38309` there and `36553` in the schema — so those rows carry
      a null `original_id` and a real name. Whether a mapping exists is unmeasured; a wrong join
      key would be worse than none.
- [ ] **Template drift has no automated check.** `RECON.md` §12.17 is a dated snapshot (mattress
      and running-shoes migrated, 12 others not), and the release checklist covers it as a MANUAL
      step. One `rt_recommendations(silo, list=<first>)` per silo would catch it; the symptom is
      `recommendations_missing` on a silo that used to work.

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
- [ ] **Nest `data.results` by hierarchy** — the code returns a flat list repeating
      `hierarchy` on every row (~23% of a 56 KB response). Recommended by review, deliberately
      deferred as its own change. **The docs no longer claim otherwise** (2026-09-04): SPEC §7,
      CLAUDE.md and the `rt_product` docstring said "grouped by hierarchy", which the response
      shape did not support; they now describe the breadcrumb the rows actually carry.
- [x] **Seven-agent review round (2026-09-04) — all findings fixed.** In brief: `filters`/`sort`
      silently no-opped unless the field was also in `tests=` (live-confirmed on mattress, an OPEN
      silo — nothing was gated, the test had simply never been fetched); descending sorts put rows
      with no comparable value FIRST; `rt_ratings` read Early-Access status from the freshly
      refetched catalog instead of the row's own slice envelope, so a stale slice reported
      `tested_gated` for an unpublished review; `graph_data_url` mapped ANY `payload_missing` to
      "no curve" and cached that for 3 days (the real negative is a well-formed `test_results: []`,
      measured); `rt_product`'s Early-Access notice asserted "a membership does not lift it", the
      exact claim §12.10 reversed; write-time demotion was structurally dead on `ratings/` (the
      predicate needs `status` and an `insider_only` id, and a ratings row has neither), which also
      made the pruning exemption never fire there; `verdicts/` had no demotion at all; a graph-kind
      test on the table path collapsed to "measured, and the answer is nothing"; `RTINGS_CACHE_MAX_MB`
      was documented as the growth bound but `enforce_size_limit` was called from nowhere;
      `silo_hint_drift` was promised in three docs and emitted nowhere. Plus a `test_http.py` for the
      real transport — `StubTransport` overrides every method without `super()`, so status precedence
      and the `errors[]` rule were dead code under the suite and both survived mutation.

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
      Re-confirmed 2026-09-05 on RTINGS' *new* server-rendered best-of template: its entire
      bundle is 3,103 bytes with zero `/api/v2/safe/` references, so the picks are not fetched
      client-side there either. Two templates now, still no API. (§12.17)
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
