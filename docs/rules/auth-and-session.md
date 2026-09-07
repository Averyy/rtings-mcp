# Auth, session and credential rules

> Moved verbatim out of `CLAUDE.md` on 2026-09-07 so the always-loaded file stays short.
> These are the project's hard-won rules: each one was forced by a measurement or a defect,
> and the date/`RECON.md` reference beside it says which. Read the file for the area you are
> touching before changing it; add new rules here, one bullet each, with the date and the
> evidence.

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
- **Detect `session` from HTML `GLOBALS.session.current_user`** (`null` = anonymous, object = logged
  in). **Never** from "a cookie is set", **never** from "scores are null", **never** from a UI
  string (ported).
- **IMPORTANT: member-vs-free is `current_user.is_insider` — MEASURED, no longer a guess
  (2026-09-06, `RECON.md` §13.2).** A literal boolean inside the object the probe already parses.
  It **leads**; the three signals that stood in for it — the `user_is_insider` analytics marker,
  page-prop `has_insider_access`, and `access_level > preview_level` — all agreed with it on the
  measured session and stay as corroboration, because each comes from a different part of the page
  and any one can be absent. Read it **strictly** (`is True`, never truthiness): the field could
  become a string or an enum, and `"false"` is truthy, which would promote every free account.
  Without it, a member whose page carries `access_level 1` and no marker classified as `free` —
  and a member read as `free` is quietly crippled (cached anonymous nulls for up to 7 days, and
  `rt_product` refused without `consume_preview`). The residual caveat was retired 2026-09-07:
  a real free account reads `is_insider: false` and `access_level 1` on the probe page, so a
  logged-in session with no positive signal really is `free` (`RECON.md` §14.6).
- **A FREE account is anonymous with a username (measured 2026-09-07, `RECON.md` §14.6).** No
  preview budget on the 12 gated silos (`access_limit: null`, 0/490 unblurred on tv), the same
  three-product cookie meter as anonymous on the 16 metered ones, and `page_body` never
  increments it. `preview_would_spend` therefore arms only when the probe reports a non-null
  `access_limit`; with none, `free` behaves like `anonymous` on `rt_product`. The gate stays
  as insurance and re-arms by itself if RTINGS ever meters the API.
- **The session cookie is `_rtings_session`** — one Rails encrypted-session cookie, `.rtings.com`,
  30-day, **HttpOnly**. HttpOnly ⇒ **"Copy as cURL" is the only capture gesture BY HAND** (no
  console fallback, differs from CR) — but not the only one full stop: Playwright's
  `context.cookies()` reads the jar itself, HttpOnly included, which is what makes the browser
  sign-in possible at all (see the sign-in rule below). Inject with explicit attributes:
  `_rtings_session=…;
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
  solve a CAPTCHA, log a cookie, or write one to the cache (ported). The browser sign-in does not
  breach this: it opens a window on RTINGS' own login page and **the human types**. The server
  never reads, fills or submits a form field, never sees the password, and never touches a
  CAPTCHA — it polls one boolean and then reads one cookie.
- **IMPORTANT: the sign-in's ready signal is `GLOBALS.session.current_user`, NEVER "a cookie
  appeared" (added 2026-09-05).** RTINGS mints `_rtings_session` for anonymous requests
  (`RECON.md` §5) and re-issues it on every response (§12.15), so within a second of opening the
  login page both "the cookie exists" and "the cookie changed" are true while still logged out.
  A capture keyed on either would store an anonymous session over a working credential and leave
  every later call quietly signed out. So the browser watches the same field the server-side probe
  reads, and the captured cookie is **still** validated against RTINGS before anything is written:
  `capture_session()` → `validate_cookie()` → `store_credential()`, and a value that does not come
  back `member` or `free` is discarded. `free` counts as signed in here — it is what the metered
  preview budget exists for — which is a real difference from CR, where only `member` does.
- **The sign-in is a background task plus a status poll, because Claude Desktop kills a tool call
  at 60 s** and a human takes longer. `rt_sign_in` answers in ~2 s; `rt_auth_status(wait_s<=45)`
  long-polls. Desktop declares no elicitation capability to a local stdio server, so this shape is
  the only one available. The CLI (`rtings-mcp auth [--browser]`) stays as the path for a machine
  with no MCP client, and both front doors call one implementation.

## Member mode, the write guard and observation provenance

- **`RTINGS_MEMBER_MODE` (default `true` since 2026-09-06) was how Phase 0 was enforced in code.** With it off,
  `probe_tier()` returns `anonymous` unconditionally, so every tier-keyed write is `anonymous`
  and no read demands a tier — while the whole mechanism (the filename segment, demand/write
  rules, write-time demotion, the preview budget) is built, unit-tested and running. Enabling
  member mode is a flag, never a migration. **Rotation write-back IS implemented** (superseding
  an earlier "stays unimplemented" note here, which predated the sliding-session measurement in
  `RECON.md` §12.15): the rotated cookie is written to disk, but **only** from a response the
  HTML probe proved logged-in (`current_user` non-null) and only for a file-sourced credential.
  Refusing to persist was what caused the monthly re-paste it was meant to prevent.
- **Phase 0's evidence bar is MET (2026-09-06); the flag is now a DECISION, not a blocker.** The
  condition this gate named — "until a bought membership confirms a cookie flips `unblurred` on the
  API" — is satisfied by `RECON.md` §13.1, and `member`/`free` classification is measured rather
  than guessed (§13.2), so the flag was flipped to default `true` on 2026-09-06. What Phase 0 always *excluded*
  is unchanged: the `cache_tier` filename segment ships carrying `anonymous`, so enabling member
  mode is never a migration, and the entire anonymous surface (silos, schema, catalog, search,
  graph, recommendations, the seven-state normalizer, the untiered cache) is Phase-0-independent —
  do not gate it.
- **IMPORTANT: a live cookie with `member_mode` OFF is a real state (no longer the default, still supported), and it mislabels things.** The
  credential is still sent and member data still comes back, but `probe_tier()` is pinned to
  `anonymous`, so member-only measurements get stamped `cache_tier: anonymous` and a member's
  fetch of a gated silo would record `completeness: "full"` in `observed/` — the stale-map lie the
  release re-scan exists to catch, reached locally and permanently (with a credential stored, no
  anonymous fetch ever overwrites it). Anything that derives a durable, published fact from a
  fetch must therefore check the probe, not just the data. Note the naive predicate is WRONG:
  **16 of 28 silos serve `insider_only` rows unblurred to anonymous**, so "unblurred insider rows"
  alone does not mean "only a member could see this".
- **IMPORTANT: a write that would be labelled `anonymous` but could not have been fetched
  anonymously is REFUSED, not relabelled** (`auth.anonymous_write_refusal`, added 2026-09-06).
  The `anonymous` label is a promise that a signed-out session would have received these bytes,
  and writing a tier above `anonymous` is exactly what `member_mode=0` forbids — so the only
  honest third option is not to cache. Three conditions, all required: the session **may** have
  unblurred it (`session_may_unblur`: no credential ⇒ false by construction, so the ordinary
  anonymous user never reaches the predicate, costs no probe and sees no warning); the response
  holds a row a signed-out session might not have seen (an `insider_only` `tested` row that is
  `unblurred`, or on `ratings`/`verdicts` any unblurred usage row — **`na` and public rows are
  deliberately not evidence**, so all-`na` and public-only slices stay writable and the tier
  deadlock does not return); and **no signed-out fetch has proven this (silo, bench) serves that
  surface in full**. Plus one unconditional refusal: an unblurred `tested` row for a
  `published:false` product, since Early Access is blurred for anonymous on **every** silo
  (`RECON.md` §12.10), so it is member-only even on an open one. **The rows are still served** —
  the call returns them with a `not_cached` warning naming the real cause; only the write is
  dropped. The warning must name that cause (flag off / demoted / what the probe read) and must
  not tell the user to enable a flag that is already on.
- **Observations carry a PROVENANCE, and only an anonymous one may prove a silo open.**
  `ObservationStore.record()` takes `provenance` as a **required** keyword, because the value
  `rt_silos()` publishes as `data_completeness` is a claim about what *anonymous* gets. A member's
  fetch of a gated silo sees 100% unblurred; recorded untagged, that silo reads `full` forever on
  that machine, and with a credential stored no anonymous fetch ever corrects it. So: an anonymous
  observation is never replaced by a logged-in one; an anonymous one replaces a logged-in one at
  any width; a logged-in observation reports `unknown` (or `gated` — a membership cannot *add*
  blur); and legacy untagged observations are treated as unknown-provenance, which is why an
  existing cache may show `unknown` for one call before it self-heals.

- **`rt_auth_status` resolves the session instead of answering `unknown` (member round S14,
  2026-09-07).** On a fresh cache nothing had probed, so the first call said `unknown` to "is my
  membership live?" while the CLI's `auth --status` probed. The tool now runs
  `ensure_session()` when no sign-in is in progress: no credential → `anonymous` with no
  request; a credential → the HTML probe of `/tv/tools/table` (never a review page, spends
  nothing), throttled by `TTL_PROBE`. During a sign-in the long-poll path is unchanged.
- **Measured 2026-09-07 (fifteen cold processes sharing one scratch cache): 67 of 129 requests
  were the HTML session probe**, most of them the forced write-time probe that precedes every
  tier-keyed write. All 129 returned 200 from CloudFront; no limit was hit. The forcing rule
  stands (a write must never be labelled from a stale answer); the cost is now a number.
