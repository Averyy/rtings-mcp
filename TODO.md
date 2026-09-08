# TODO — rtings-mcp

Tracking for open research and decisions. Confirmed items are recorded in `RECON.md`; this file is
the working checklist. Facts land in `RECON.md`, not here.

**Status 2026-09-08: built, working, signed in, published, and the backlog is closed.** Three
review rounds (15 defects, then 10, then 7 more found while testing the sign-in end to end — see
Built, below), the anonymous shopper round (40 scenarios), the member round (15 scenarios), and
on **2026-09-08 the whole open list was worked through**: 3 confirmed defects fixed, 6
discoverability gaps closed, `rt_article` added (8 data tools now), and **7 research questions
answered by measurement** — q6, q7, q15, q16, the usage-rating map, the orphan products and both
best-of template gaps. **483 offline + 9 live tests pass**, lint is clean, and both release gates
(`rtings-mcp scan`, `rtings-mcp drift`) reproduce their snapshots. What the build measured is in
`RECON.md` §12; the membership in §13; the preview meter in §14.

**Two findings from that round change what the docs may claim:**

- **The blur rule is an approximation, not an identity** (`RECON.md` §12.21). headphones legacy
  bench 4 serves test 287 `Transducer` unblurred on all 16 products with `insider_only: true` —
  **per test**, not per product. One counterexample in six bench-samples. Nothing in the server
  changed, because the boundary has always been read off the observed `unblurred` bit.
- **A product is on exactly ONE bench** (`RECON.md` §12.23): 6 bench pairs across 4 silos share
  zero products, so the recent-bench set is a strict partition and q7 is both answered and moot.

**Phase 0 is DONE** (`RECON.md` §13): q1 is YES, the member/free field is
`current_user.is_insider`, the browser headers turn out not to matter, `user_has_access` flips,
and member/anonymous curves are byte-identical. `RTINGS_MEMBER_MODE` defaults to **`true`** as of
2026-09-06. §13.8 closed the last member-only question on 2026-09-08.

## Handoff — read this before touching anything (2026-09-06)

### Where things stand

- **A real membership is signed in.** The credential lives at `~/.config/rtings-mcp/session.json`
  (`0600`), and it **rotates on use** — the session slides, so it stays alive as long as the server
  is used and dies 30 days after it stops. Observed rotating and being persisted correctly today.
- **`RTINGS_MEMBER_MODE` now defaults to `true`** (v0.2.0). Setting it to `0` still works and is
  what the anonymous-label guard exists for.
- **Committed 2026-09-06 as v0.2.0, then v0.2.1 after the shopper round** — the sign-in, the
  guards, the round's fixes and the docs are on `main` (through `a588f9a`). **v0.2.1 was
  released 2026-09-07**: the GitHub release ran `publish.yml` (lint, offline tests, tag-equals-
  version check, build, OIDC publish with attestations), PyPI holds the wheel and sdist, and a
  PyPI-installed server driven over stdio returned live member data. The project still
  forbids committing unasked.
- **The real cache now holds member data** — `tests/*/141.member.*` for tv, written 2026-09-06
  by a signed-in `rt_ratings` through this session's own MCP connection. Everything else in
  `~/.cache/rtings-mcp` is anonymous slices from 2026-09-04/05.
- **Any MCP server process you find running is STALE** — they predate today's code. Restart the
  client before testing through MCP, or you will be testing the old build.

### Already verified live — do not spend a session redoing these

Sign-in end to end (window → cookie → validate → store → in-process adopt, browser reaped, no PII
stored); Phase-0 q1 and captures a, c, d, f, g, p (`RECON.md` §13); the release scan's anonymity
guard with a real cookie on disk (12/16, zero drift); the write guard live on tv and mattress in
both flag states; member-tier writes and cache hits; **436 offline + 9 live tests** (483 as of
2026-09-08), the offline
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

Also confirmed: the credential rotates on disk with every run (`stored_at` advances). (`rt_auth_status`
answered `session: unknown` on a fresh process until 2026-09-07; it now probes.)

### The shopper round (2026-09-06) — 31 scenarios, all 28 categories

Fresh agents answered realistic shopping questions through a stdio CLI that spawns the server
from the working tree (`describe` first, then tool calls), then graded the experience. Each
FAIL below was a defect that is now fixed and covered by a test; the verification round re-ran
the failing categories with new questions.

| Batch | Category (scenario) | Verdict | What it found |
|---|---|---|---|
| 0 | tv (bright room), mattress, headphones (XM6 vs QC Ultra), robot-vacuum, monitor | tools worked; effort 2-3/5 | stale anonymous recs served to a member; `header: []` on graphs; 70-80 K responses dropped by the client; nameless "uncatalogued" rows on every call |
| 1 | tv (dark room) | FAIL | "Inf : 1" contrast parsed as 1.0 on the review path, null on the table path |
| 1 | monitor (1080p family desk) | FAIL | converted values labelled with the display unit (10.7 "inches" for 10.7 cm) |
| 1 | headphones (gym earbuds) | FAIL | canned cross-category examples in a warning; a `product_ids` entry on another bench vanished silently |
| 1 | tv (PS5), monitor (ultrawide), headphones (open-back), soundbar | PASS | — |
| 2 | projector | FAIL | `find` searched leaf names only: "input lag" reported as not measured |
| 2 | laptop | FAIL | the unit fix mislabelled rendered values (2.1 "kilograms" for 2.1 lbs); "no Size test" guidance wrong for laptop |
| 2 | mouse, keyboard, keyboard-switch, printer, speaker | PASS | duplicated test names, `find` synonyms, wrapper divs, truncation at 11 products |
| 3 | humidifier | FAIL (calls) | usage names ("Leftovers") unreachable by `find`; 18 calls |
| 3 | robot-vacuum (mop), vacuum, air-purifier, dehumidifier, air-conditioner, refrigerator | PASS | test/usage id collision (vacuum 35602); "pet" matched "carpet"; `scores_available` shape |
| 4 | toaster-oven | FAIL | "01:45" parsed as 1.0 on the review path; 105 labelled "mm:ss" on the table path |
| 4 | microwave, running-shoes, blender | FAIL (calls) | "countertop" three schema calls deep; zero-row response claimed `unblurred`; "2-4 benches" wrong (10 on running-shoes); `product_id` not documented as the join key |
| 4 | camera, air-fryer, toaster, router, mattress (couple), vpn | PASS | unscored specs carried `score: 0.0`; several `find` terms per call |

**Verification round (fresh questions on the categories that had failed):** tv 8 calls PASS,
toaster-oven 6 PASS, humidifier 7 PASS (was 18), headphones 7 PASS, projector 6 PASS (was 12),
microwave 8 PASS (was 14), blender 12 PASS, running-shoes 8 PASS (was 18), laptop 5 PASS (was
20, after a re-run), monitor: two more defects found and fixed — a numeric "Size" test hijacked
by the tested-variant alias, and slash-containing test names colliding with the `Group/Name`
syntax — with the fixes verified live (`5b7cb92` and the commit after it).

**Final confirmation round on the popular categories** (new questions, final build): tv 6 calls
PASS, headphones 7 PASS, monitor PASS. The monitor path found and fixed two last defects on the
way (a digit string against a word test parsed as a number; `rt_schema(group=<test id>)`
answering "no scored tests"), tv dropped `recommended_sku` (wrong on 2 of 3 picks of one list,
from RTINGS' own sku block) and picks now carry `tested_variant`/`test_bench`, and headphones
made an off-index best-of slug fetchable. 40 scenarios in all.

Correctness of the final answer scored 4-5/5 on every scenario; every cross-check between two
tools agreed. What changed is in `CLAUDE.md` (rules dated 2026-09-06) and the commits from
`055707d` onward.

**How to run another round.** `scratchpad/rtcli.py` in the session scratchpad is the harness
(`describe` / `call <tool> '<json>'`); it is not part of the repo. Give an agent a real
question, tell it to run `describe` first, and ask for the call log, dead ends, cross-checks
and a PASS/FAIL. Use a scratch `RTINGS_CACHE_DIR`.

### The member round (2026-09-07) — 15 scenarios, the real membership

`docs/member-scenarios.md` run as written: the real credential in its real config dir, a cold
scratch cache, one fresh agent per scenario driving a fresh stdio server per call, graded
against the invariant table. **14 PASS, 1 FAIL**; every finding below is fixed, covered by an
offline test, and re-verified live against the membership on the final build (v0.2.3).

| Scenario | Calls | Verdict | What it found |
|---|---|---|---|
| S1 tv 65" bright room | 7 | PASS | default recent set truncated to 8/10 with 2 tests + default usages; "current bench" needs `bench=["227"]` |
| S2 tv C5 vs S95H | 8 | PASS | tv's usage is "Gaming", not "Video Games"; `summary: null` without `include_prose` |
| S3 headphones treble RMS | 6 | PASS | bare name refused with the three qualified forms; a wrong `Group/Name` said only "no test named" → **fixed: lists the same-leaf candidates** |
| S4 monitor 27" 1440p 144 Hz | 3 | PASS | Size resolved to the numeric test, "1440" matched as text; **review rows for the response-time tests had no `unit`** (input unit only, no display unit) → fixed |
| S5 mouse lightest wireless | 6 | PASS | one recent-set group across 2 benches (per SPEC; the scenario doc said 2 groups, corrected); `score_direction` computed over the population while documented "in this response" → docs fixed |
| S6 printer cost per page | 3 | PASS | instructions rule 7 ("no prices") had no exception for the measured cost test → **fixed** |
| S7 robot-vacuum quiet + pet hair | 7 | PASS | test/usage ids distinct, usage-score filter works (undocumented); `scores_available.public_tests: available` on a review that served only insider rows (schema-wide predicate, not a violation) |
| S8 tv Bravia 9 curve + value | 9 | PASS | tv has no graph-kind brightness test; **`rt_graph` axes read `vAxis`/`scale` only — the PQ EOTF curve's `vAxes`/`scaleType` gave `y: null`** → fixed |
| S9 soundbar recommendations | 4 | PASS | no by-size list on soundbar (content fact); `[nolink:...]` shortcode in reasoning → **fixed** |
| S10 laptop 13–14" battery | 3 | PASS | range inclusive both ends, hours on both paths, no mislabel |
| S11 Early Access review | 2 | **FAIL** | member sees values, silo from the URL, notice accurate — but **`insider_tests: unknown` beside three served insider values** (unpublished product excluded from the fold) → fixed: a visible Early Access row counts |
| S12 mattress control | 6 | PASS | member and anonymous byte-identical except `session`/`auth_state`/`as_of`; `tests=["Firmness"]` hit the group with no pointer to its leaves → **fixed**; `find` hid `is_unscored` → fixed |
| S13 camera control | 5 | PASS | props template, real `original_id`s; `from_cache:false` on a call that only fetched a side-by-side to name an off-bench pick (by definition: "did this call touch the network") |
| S14 session health | 2 | PASS | credential rotated (mtime advanced, mode 0600, only file in the dir); **`rt_auth_status` said `unknown` on a fresh cache** → fixed: it probes |
| S15 cache reuse | 2 | PASS | second process: `from_cache:true`, same `fetched_at`, zero requests; telemetry lines had no pid → fixed |

Three graders lost a call to the instructions text `rt_product(url, ...)` — the parameter is
`product` (fixed in the instructions, README, SPEC, the scenario doc). Measured on the side: 129
requests from the round, all 200, 67 of them HTML session probes (the forced write-time probe,
`docs/rules/auth-and-session.md`). Not defects: `tests/_unassigned/` holds only uncatalogued
rows, not a duplicate slice; `observed/<silo>.json` carries `cache_tier: anonymous` as a fixed
label while the code reads each bench's `provenance`; the uncatalogued group's `matched` ignores
a usage filter its rows cannot carry.

### Still NOT tested

Nothing on the original list. All three closed 2026-09-08:

1. ~~The review path on a legacy bench for a member~~ — **measured** (`RECON.md` §13.8): 8/8
   `tested_visible`, `data_tier: unblurred`, on tv bench 1. The attempt found a real defect
   (a legacy-bench review URL was unresolvable); fixed and tested.
2. ~~`rt_sign_in` end to end on an expired cookie~~ — `validate_cookie` had **no test coverage at
   all** (every test injected `validate`). Five now cover it against the stub transport: expired,
   member, a crash, an unknown probe, and the guard that matters — it never writes to the real
   cache or the stored credential.
3. ~~A free account~~ — measured 2026-09-07 (`RECON.md` §14.6): identical to anonymous.

### Cautions

- **Use a scratch `RTINGS_CACHE_DIR` for experiments**, so probing does not fill the cache the
  server serves from.
- **Never commit a fixture containing unblurred member values**, and redact any `GLOBALS.session`
  capture — the real one carries the user's email, username and numeric user id.
- **Do not add a new scan/measurement path without the anonymity guard** that `rtings-mcp scan`
  uses. A member session measuring enforcement reports every gated silo as open.
- The delegated-review MCP (`codex-dobby`) had **expired auth** on 2026-09-06 and returned zero
  findings while reporting success. If you delegate a review, check it actually ran.
- **The two release gates are `rtings-mcp scan` and `rtings-mcp drift`** (added 2026-09-08), each
  with its own committed snapshot. `drift` exits non-zero on any silo that fails to extract.
- **`RECON.md`'s blur rule is an approximation** (§12.21). Do not re-derive it as an identity, and
  break a `partial` scan result down per test before calling it a paywall change.

```bash
.venv/bin/pytest tests/ -q                 # 483 offline, ~5 s
.venv/bin/pytest -m live -q                # 9 live, anonymous, ~4 min
.venv/bin/ruff check src/ tests/
.venv/bin/rtings-mcp auth --status         # what credential is stored, and its session
.venv/bin/rtings-mcp scan --silo tv        # gate 1, one silo (anonymous by construction)
.venv/bin/rtings-mcp drift --silo tv       # gate 2, best-of extraction
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

Nothing. q2 and capture (o) were answered anonymously on 2026-09-07 (`RECON.md` §14), and q7 was
answered on 2026-09-08 (`RECON.md` §12.23) — a product is on exactly one bench, so there is no
cross-bench comparison to make.

## CLOSED 2026-09-08 — the whole open list, worked through in one pass

Every item below carries where the evidence landed. Nothing here is outstanding.

### Defects fixed (3)

- [x] **`rt_schema find`: `terms_with_no_matches` was always `[]`.** `term in (h.get(...) or
      [term])` — an empty list is falsy, so one partial-only hit made every term read as matched.
      Now `h.get("matched_terms", [term])`; only the single-term shape defaults.
- [x] **`find` digit tokens drowned the real matches.** "hdmi 2.1" split into hdmi/2/1 and the
      bare digits filled the 60-row cap with noise. A lone digit now scores only beside a word of
      the same term — unless the term is nothing but that digit (`find="1440"`).
- [x] **`rt_ratings` failed the whole call on one off-bench usage.** Now a warning and a partial
      answer, matching `product_ids`; `tests=` too. A request where EVERY entry is off-bench still
      errors, because dropping them all would leave `usages=[]`, which reads as "no scores exist".

### Discoverability (6)

- [x] **The client cuts a tool description at exactly 2048 characters** (measured). `rt_ratings`
      was 4,229 with the filter vocabulary at ~1,540. Reordered by what a caller cannot work
      without; a test asserts each essential phrase is inside the window.
- [x] **`sold_in`** — the sizes a product is SOLD in, with operators (`{"sold_in": ">=83"}`).
- [x] **`variants[].model`** — the manufacturer model number per sku (`"XR-83A80L"`).
- [x] **`rt_schema(find=...)` answers catalog fields** in `catalog_fields` instead of nothing.
- [x] **`rt_search(silo=...)`** scopes the cross-silo index, reporting `scanned`/`silo_matches`.
- [x] **`rt_article`** reads a `/{silo}/learn/{slug}` page as prose, with `sections`/`section=`.

### Product (4)

- [x] **Flip the repository public** — already public (`Averyy/rtings-mcp`).
- [x] **Nest `data.results` by hierarchy** — done, and MEASURED as a shape win rather than a byte
      one: the breadcrumb is 26.6% of a real TV review but nesting recovers only 1.8%, because it
      adds two indent levels to 243 rows (`docs/rules/tools-and-responses.md`). Kept for the
      addressable `group_id`, which is what the new budget warning tells a caller to use.
- [x] **A bare `rt_ratings("tv")` now answers something numeric.** Measured: it was 33 usage
      scores with ZERO visible. When every score is withheld it projects the category's public
      tests (6 on tv, 4 on mattress); when scores are visible nothing changes.
- [x] **Template drift has an automated check** — `rtings-mcp drift`, diffed against
      `docs/recommendation-template-snapshot.json`. Baseline: 28/28 ok, 26 props / 2 static.

### Found while working, not on the list (2)

- [x] **`rt_product` never bounded its response.** A TV review is 77,407 characters against the
      40,000 budget, with no warning and no trimming, while `rt_ratings` has always bounded.
      Now drops whole sections from the tail and names them in `groups_omitted`.
- [x] **A legacy-bench review URL was unresolvable** (`RECON.md` §13.8) — the same product
      resolved by numeric id, under an error reading "no such product". Found while measuring the
      member legacy-bench path, which it was blocking.

### Research answered by measurement (7)

- [x] **q16** (`RECON.md` §12.21) — enforcement holds on legacy benches and is kind-agnostic,
      **with one per-test counterexample that falsifies the stated blur invariant**.
- [x] **q7** (§12.23) — a product is on exactly one bench; the recent set is a partition.
- [x] **q6** (§12.22) — a second passive sample: 193 requests, 0 non-200, no rate-limit headers
      of any kind, 13.7 req/min sustained.
- [x] **q15** — still not answerable from the API; it is a business decision, tracked so nobody
      re-derives the falsified "maturity" story. This is a conclusion, not a gap.
- [x] **Usage ratings on the other 15 metered silos** (§12.20) — 3,006 rows, perfectly bimodal:
      0% on the 12 that enforce, 100% on the 15 metered ones with usages. No separate map needed.
- [x] **The 9 orphan TV products** (§12.2b) — gone. 551 ids in `test_results`, 551 catalogued,
      0 orphans. A transient population, as inferred.
- [x] **Both best-of template gaps** (§12.17) — "Notable Mentions" IS extractable on the
      server-rendered template and is now surfaced as `mentions` on both; and the featured
      `target_id` join is by (`target_label`, `target_type`), not by id — 0 of 9 target_ids are
      schema ids, all 9 labels resolve.

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
