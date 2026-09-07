# rtings-mcp — product spec

An MCP server that exposes an RTINGS **member's own** subscription as structured tools, so an agent
can consult RTINGS test data the way it consults any other data source.

Status: **The anonymous server is built and working (2026-09-05).** All seven data tools run
against the live API, and `rt_sign_in` / `rt_auth_status` connect a membership from inside a
conversation (which is the only route Claude Desktop has: it offers no terminal for
`rtings-mcp auth`). 436 offline tests and 9 live anonymous tests pass, and the release-gate
re-scan reproduces the 12-enforcing / 16-open map exactly. **Published on PyPI as
`rtings-mcp` 0.2.3 (2026-09-07)** through GitHub-Actions trusted publishing; the install is
`uvx rtings-mcp`. **Member mode is ON by default**
(`RTINGS_MEMBER_MODE`) since Phase 0 settled q1 on 2026-09-06 (`RECON.md` §13.1). Facts the build
measured are in `RECON.md` §12, the member session in §13; the corrections they forced are marked
**(corrected)** below.
The anonymous surface is confirmed across all 28 silos: catalog, schema, search, review prose, ranked
recommendations, curve data for the tests that have a curve, and — on **16 of 28 silos**, while the
anonymous three-review preview budget is unspent, which an API client that never opens a review
page keeps it (`RECON.md` §14) — the full measurements and scores. **The member surface is verified** (2026-09-06, `RECON.md` §13.1): a
membership cookie returns the withheld scalars over the same API, 588/588 unblurred against 0/588
anonymous on tv bench 227. That was the one blocking unknown, and it is closed.

> **Read this first.** RTINGS is **not** Consumer Reports with the names changed. The plumbing is
> cleaner (a keyless parameterized JSON API) but the economics are inverted: CR's anonymous tier is
> useful; RTINGS' is **useful on 16 of 28 categories and thin on the other 12** (`RECON.md` §11).
> Three facts shape everything here and have no CR analogue: **(a)** the API response carries no auth
> marker — auth lives only in the HTML (`RECON.md` §1, §5), so §6 has to join two request families;
> **(b)** the paywall is enforced **per silo**, so what anonymous gets is a measured property of each
> category, never a constant (§5); **(c)** on the enforcing 12, anonymous gets the *raw measurement
> curve* for the few tests that have one, but not RTINGS' scalar *summary* of it (`RECON.md` §4).
> Where a rule ports from `consumer-reports-mcp`, this spec says "(ported)" and does not re-argue it.

---

## 1. Problem

RTINGS sells an "insider" membership, not access. No member API, no export, no MCP server. The
scores and measurements are blurred server-side and delivered through a Vue front end, so an agent
consuming them today must drive a logged-in browser and read rendered pages — slow, lossy, and it
drops the structure. Meanwhile RTINGS' own front end fetches every category from a keyless JSON API
(`RECON.md` §1). This closes that gap: one local server, the member's own cookie, structured tools.

## 2. Goals

- Ask RTINGS a question from an agent and get **structured** data back: 0–10 usage scores, scalar
  measurements with units, specs, and the curves behind them.
- Cache locally, so repeat questions cost zero requests and the data survives a lapsed membership.
- Be **structurally incapable of lying about the paywall** — an agent must never read a blurred
  value as "RTINGS did not test this" (§5). Core safety property, ported.
- **Anonymous by default, exposing the maximum RTINGS serves without a session** (§5). Measured
  2026-09-03: **16 of 28 silos serve their full measurements, scores and rankings anonymously**
  (corrected 2026-09-07: to a session whose three-review preview budget is unspent, which this
  server's always is — `RECON.md` §14); the other 12 (the flagship categories) gate them outright. So anonymous is not a consolation tier — for most of
  the catalog it serves the numbers outright. (Whether a membership adds *anything* on those 16 is
  unmeasured — usage ratings were not swept there, and no member session has ever been observed.) **All 28 silos, all-or-nothing** — full coverage, no launch
  subset (§12).

## 3. Non-goals

- **Not a hosted service.** Public, open source; each user runs it locally against their own
  membership. No shared server, no redistribution of any cache.
- **No circumvention of the gated scalars.** They are gated server-side (`RECON.md` §2). The server
  reads what the user's own session is entitled to. It never automates login, solves a CAPTCHA, or
  stores a password (§6). **It never populates `force_blur`/`unblur_product_ids` beyond what a
  logged-in front end sends** — a client-supplied unblur hint is precisely the circumvention this
  rules out (`RECON.md` §5, §10 q8).
- **Graph/curve data is the one deliberate exception (§5, §12), decided not accidental.** It is
  public via a sanctioned navbar tool (`RECON.md` §4). Its controls are concrete, not "it's
  sanctioned": local-only cache, no redistribution, on-demand per `(product,test)` with no bulk
  parameter, rate-limited, and `rt_graph` disable-able by config without breaking the other tools.
- No bulk mirroring. Silo-and-bench at a time, on demand.

## 4. Prior art

`RECON.md` §9. No RTINGS MCP server existed when this was designed (GitHub + MCP registries +
web checked); the PyPI name `rtings-mcp` was free on 2026-09-07 and now holds this project. All prior art is headphone-FR curve extraction via
`graph_tool__product_graph_data_url` plus one Firefox extension using the search endpoint. **Nobody
built the category-wide structured layer.** Greenfield.

## 5. The central decision — what the server is honest about

**Rewritten 2026-09-03 after a ~50,000-row anonymous sweep of all 28 silos (`RECON.md` §11).** The
earlier framing — "anonymous is thin on numbers by design" — was measured on TVs and is wrong for the
majority of the catalog.

**The complete blur model. Two independent gates, and nothing else:**

```
blurred  ⇔  product.published == false            (Early Access — an Insider perk; see §12.10)
         ∨  (test.insider_only == true  ∧  the SILO enforces the paywall)
```

**Enforcement is per-silo and binary: 12 of 28 enforce, 16 do not.** All 28 carry `has_paywall:true`,
so that flag is useless. The gated 12 are RTINGS' flagship categories — tv, headphones, monitor,
mouse, keyboard, soundbar, speaker, printer, laptop, robot-vacuum, projector, router (median 227
reviews, 5.5 benches, back to 2011). The open 16 are kitchen/home/long-tail (median 46.5 reviews,
**1** bench, none older than 2020-07): mattress, vacuum, air-purifier, air-fryer, refrigerator,
microwave, toaster, toaster-oven, air-conditioner, dehumidifier, humidifier, blender, vpn,
keyboard-switch, camera, running-shoes.

On those 16, `insider_only` tests come back **with values and scores** — real measurements (mattress
`Indent @ 50 kg` = `44.26`, score `5.4`), zero `Lock` markers in 5,194 rows checked.

| Surface | Anonymous, 12 gated silos | Anonymous, 16 metered silos, budget unspent (§14) | Member (verified 2026-09-06) |
|---|---|---|---|
| Catalog, schema, search, prose, recommendations | full | full | full |
| Graph / curve data | **full** | **full** | full |
| Spec fields + their 0–10 scores | 2–8 tests per silo | **full** | full |
| Scalar measurements | **null** | **populated** | populated? |
| Scores on `insider_only` tests | **null** | **populated** | populated? |
| 0–10 **usage** ratings (`table_tool__ratings`) | **null** | *unmeasured — assume gated* | populated? |

**So `insider_only` marks a test gate-*able*, never gated.** `scores_available` is derived from
**observed `unblurred` per (silo, bench)** — deriving it from the schema flag is wrong for 16 of 28
silos. Exclude `published:false` products when deriving it, or one unfinished review makes an open
silo look gated.

**IMPORTANT: this map is a snapshot, not a constant.** Enforcement correlates with category maturity,
so an open silo can be gated later as it grows. **Never hardcode the 12/16 split** — re-derive it per
fetch, and let never-downgrade (§8) absorb a silo flipping open → gated.

**Corrected 2026-09-07 (`RECON.md` §14): the 16 are METERED, not open.** An anonymous
session reads `access_level 2, access_limit 3` on those silos — a three-product review
preview budget, spent one unit per distinct product review page fetched as **HTML**, held
in the plain `product-previews` cookie. Past the third product the session drops to level 1
and `test_results` and `ratings` blur to exactly the previewed products (mattress 405 → 15,
camera 375 → 15). The 12 "enforcing" silos are the ones where anonymous has no budget at
all. Every scan so far ran on a fresh jar, i.e. unspent — and so does this server, by
construction: `rt_product` is the `page_body` POST, which does not count, and no code path
fetches a review page as HTML. So what the server observes is real for its own session, and
the honest description is "a preview budget the server never spends", never "open".
Never add a review-HTML fetch, and never strip, reset or persist that cookie.

**One sentence a reader needs:** anonymously you get the raw measurement (the curve) but not RTINGS'
number for it (the scalar). The server runs in two honest modes, and the envelope says which:

- **Anonymous on the 16 metered silos, budget unspent, is a full *data* mode** — values, scores, ranking and comparison,
  the numbers served outright. **Not verified to be identical to what a member sees** — no member
  session has been measured (`RECON.md` §10 q1), and usage ratings were not swept on these silos.
- **Anonymous on the 12 gated silos** is a *catalog, prose and curve* mode — search, spec routing,
  ranked editorial shortlists, full measurement curves, and the 2–8 public spec fields with their
  scores, but no `insider_only` measurement and no usage ratings, said structurally.
- **Member** is the *data* mode for those 12: the same tools, values filled in — **contingent on
  Phase 0**. It adds nothing to the other 16.

**The core safety property (ported, non-negotiable):** a gated value is `null`, **never absent**, and
the row is marked with the reason. So the normalizer emits **seven states**, never collapsing two of
them into one null (`RECON.md` §6, confirmed 2026-09-02 and 2026-09-03):

- **tested-visible** — `status:"tested"`, `unblurred:true` → `{value, gated:false}`
- **tested-gated** — `status:"tested"`, `unblurred:false` → `{value:null, gated:true}` ("measured,
  you can't see it")
- **not-applicable** — `status:"na"` → `not_applicable` ("this test does not apply to this product")
- **review-in-progress** — the product's catalog row is `published:false` → `review_unpublished`, a
  blur withheld from this session, which a membership lifts (`RECON.md` §12.10)
- **not-tested** — no row for that `(product, test)` within fetched, **non-stale** coverage → an
  explicit `not_tested`, never a gated null. Coverage that cannot be shown to cover this product
  (§8) yields `coverage_unknown`, never `not_tested`

**Branch on `status` before `unblurred`.** This is the ordering the safety property depends on, and
the 2026-09-03 sweep proves it necessary in **both** directions (`RECON.md` §11.4):

- an `na` row with `unblurred:false` is byte-identical to a gated row ⇒ read via `unblurred` alone it
  becomes "RTINGS measured this, buy a membership" about a test that never applied;
- **and 1,025 of 2,186 `na` rows are `unblurred:true`** (47%) ⇒ read via `unblurred` alone *those*
  become **`tested_visible` with a null value** — "RTINGS measured this and the answer is nothing."
  The earlier "all observed `na` rows are `unblurred:false`" was a TV-only artifact.

**`status` has a third value, `untested` (154 rows: mattress 75, mouse 79) — map it to
`not_tested`.** The table path emits **both** encodings of not-tested: an absent row *and* an explicit
`status:"untested"`. Mapping `untested` to `unknown_row_status` raises a false drift alarm on an
ordinary state and fails to report `not_tested`. 80 of those 154 are also `unblurred:true`.

**A visible row can be genuinely empty.** 2 rows came back `tested` + `unblurred:true` +
`value:null` + `rendered_value:null` (mattress `original_id 32186`), so `tested_visible` must
tolerate a null value rather than assume one exists. An unrecognised `status` ⇒ `unknown_row_status`
and a warning, never `tested_visible`.

**The two fetch paths encode the same facts differently, and both normalize to the same states:**

| | `table_tool__test_results` | `app/product_vue_page__page_body` |
|---|---|---|
| not-tested | **absent row** *or* `status:"untested"` (`RECON.md` §11.3) | n/a — every bench test has a row |
| not-applicable | `status:"na"` — 2,186 observed (`RECON.md` §11.4) | `status:"na"` |
| scalar field | `value` | `rendered_value` (HTML) + `score`, **no `value` key** |
| scope of one response | requested benches × tests, all products | one product, **its own bench only** |

The review body returns exactly one row per test in the product's **own** bench (54/54 on a v0.9 TV,
402/402 on a v2.2 TV) — so "absent ⇒ not_tested" is safe there **only against that bench**. Joining
review rows against the full silo schema would invent hundreds of false `not_tested`s.

The safe envelope:

- `unblurred` per value → **never dropped**; a blurred value is `null` with `gated:true`.
- **`scores_available` is an object of `available | gated | absent`, never a boolean**, and it is
  **derived from `insider_only` + observed `unblurred`** (`RECON.md` §6), never hardcoded (§7). This
  is grounded in a real schema flag CR never had.
- `auth_state` / `data_tier` / `session` are three structural fields (§7), and `data_tier` is read in
  the **safe direction only** (§6): a real `unblurred:true` on an `insider_only` test proves the row
  **was unblurred for us** — never that the caller is a member (a metered preview does it too);
  `all null ⇒ anonymous` is **forbidden** (it could be an expired session).
- `outputSchema` on every tool; a `data.notice` emitted only when the gated fields are entirely null,
  so an agent that ignores the envelope still cannot misread the payload (ported).

**The graph exception, plainly (§3).** Curve data is served anonymously because it is public. The
anonymous tier can therefore hand an agent a TV's *frequency-response curve* while withholding its
*peak-brightness number*. That asymmetry is RTINGS' own; the server exposes it rather than papering
over it, and §12 records it as a decision taken because it is the one place paywalled measurement
data reaches a non-member. **`rt_graph` returns a curve as shipped (resampled for size only, §7) and
never reduces it to a scalar** — restating CR's "never derive a score" positively, because here the
raw signal to derive *from* is present (`RECON.md` §2 correction, §4).

## 6. Auth design

Public and open source, so auth must work for any user on any platform — no macOS/Keychain/single-
browser assumption. The user logs in to RTINGS normally and hands the server the resulting **session
cookie**. No api-key, no bearer token, no password, no login flow. Ported from CR; only the specifics
below differ, and one is new.

### The seam — auth is HTML, data is JSON

The one genuinely new problem, and CR has no analogue. **The JSON API response carries no auth field**
(`RECON.md` §1, confirmed): no `session`, `access_state`, `subscriber` anywhere. Auth lives only in
the HTML `GLOBALS.session` (`RECON.md` §5). So a data fetch (JSON) and an auth reading (HTML) are
different requests, and the server must join them without ever inferring auth from null data:

- **`session` (credential health)** is read from `GLOBALS.session.current_user` on an **HTML probe** —
  run at `rtings-mcp auth` time and lazily once per process, **not** on every data call. Values:
  `member` / `free` / `anonymous` / `expired` (cookie configured but `current_user` null) /
  **`unknown`** (no probe yet, or the probe itself failed).
- **The probe page is pinned to a NON-review page** — `/{silo}/tools/table`, or `/` — and never a
  product review. The review path is the metered one (`RECON.md` §10 q2), so probing there would
  spend one of a free account's previews *every time the server checks whether it is logged in*, and
  `rt_product` re-probes after every call. Any page carries `GLOBALS.session`, so this costs nothing.
- **`session: unknown` demands `cache_tier: anonymous` and warns.** With a cookie configured and no
  probe yet, a tier-keyed *write* has no trustworthy label — so **a successful probe is a precondition
  for any tier-keyed write, and for `rt_product` whenever a cookie is configured.** Reads under
  `unknown` fall back to the `anonymous` demand tier (they cannot deadlock, and they cannot promote a
  file). Never write `member` off an absent or failed probe.
- **`probe/session.json` is persisted but TTL'd, and read under the key lock.** It survives restarts
  so `auth_state` and the preview budget are not amnesiac, but a second process reading a stale copy
  would let two processes each believe one preview remains. Give it a short TTL, re-probe past it,
  and read it **inside** the cross-process lock at budget-check time (§7, §8). It is stored at
  `probe/last_probe.json` — never `session.json`, which is the credential file's name at
  `~/.config/rtings-mcp/session.json` and must not be confusable with it.
- **`data_tier` (what the served bytes prove)** is computed **at read time from the rows actually
  served**, in the safe direction: any `unblurred:true` on an `insider_only` test ⇒ those rows came
  to us **unblurred**. Absence of any unblurred value proves nothing (could be anonymous, could be
  expired), so it is `unproven`, never `anonymous`. This is the only tier signal the data carries.
- **`data_tier` is NEVER a stored label, and never a cache key.** It is derived per response, from
  the rows in that response. Storing it and then requiring `data_tier ≥ configured` deadlocks: a
  slice for a **public** test (`insider_only:false` — Resolution `208`) is `unblurred:true` on every
  row *anonymously* yet contains no insider test, so it can only ever be labelled `unproven`. A
  member configured at `unblurred` would then miss on it forever, refetch, and re-derive the same
  label — every call, permanently. The same trap catches a **free** account on every table-path
  slice, and any slice that is entirely `status:"na"`. What the cache is keyed on is `cache_tier`
  (§8) — the *probe* tier at fetch time — which is a different question with a different answer.
- **`data_tier` values are `unblurred` / `unproven` — deliberately NOT `member`.** Unblurred data
  does not prove membership. The free/metered preview is resolved server-side on the review-page path
  (`RECON.md` §10 q2), which is exactly `rt_product`'s endpoint, so a **free** account produces
  `unblurred:true` on `insider_only` tests for the products it previewed. Calling that tier `member`
  would derive `auth_state: member` for a non-member — an entitlement claim from a metered preview.
  **Membership is decided by the HTML probe alone**; the data says only whether a row was unblurred.
- **`auth_state` is derived** from the two (ported from CR's current design): a cached unblurred row
  served after the session lapsed is unblurred *data* and a dead *session*; one enum cannot say both.
  The derivation is a table, not a vibe — and note that **`data_tier: unproven` is the normal case for
  a member** asking about public tests, so it must never read as a problem:

  | `session` ↓ / `data_tier` → | `unblurred` | `unproven` |
  |---|---|---|
  | `member` | `member` | `member` (nothing gated was requested — **not** a warning) |
  | `free` | `free` | `free` |
  | `anonymous` | `anonymous` | `anonymous` |
  | `expired` | `stale_member_data` (cached unblurred rows, dead session) | `expired` |
  | `unknown` | `unproven_session` | `unproven_session` |

  **(corrected 2026-09-04)** The `unblurred` column used to read `preview` for `free` and
  `anonymous` — "a metered unlock" / "a share or gift link". That was written when the model
  assumed every silo gates, and §5's own correction kills it: on **16 of 28 silos an
  anonymous caller gets `unblurred:true` on `insider_only` tests as ordinary behaviour**
  (measured live: mattress `Thickness` = 39.7 cm, `Normalized Stiffness @ Lumbar` = 42.98
  Pa/mm, no session at all). Reporting `preview` there invents a grant nobody made — the same
  class of error as calling a metered preview `member`, in the other direction. `auth_state`
  now never claims an entitlement; `data_tier` says what the bytes prove and nothing more.

  `auth_state` is a **summary for humans**; every rule in this spec keys on `session` or `data_tier`
  directly, never on `auth_state`.
- **The race is documented, not hidden:** the HTML `session` probe and the JSON data fetch can
  disagree (session lapses between them). The server prefers the data-derived `data_tier` for what a
  row *is*, and the last `session` probe for whether to warn the caller to re-auth. Neither is
  silently upgraded from the other.

### The cookie (`RECON.md` §5)

- **`_rtings_session` is the credential** — one Rails encrypted-session cookie, `.rtings.com`,
  30-day, **HttpOnly**.
- **HttpOnly ⇒ "Copy as cURL" is the only capture gesture BY HAND** (differs from CR):
  `document.cookie` cannot read it, so there is no console fallback. `rtings-mcp auth` parses the
  `Cookie:` header out of a pasted cURL and keeps `_rtings_session`.
- **The browser path reads the jar directly, so HttpOnly costs the user nothing.** Playwright's
  `context.cookies()` returns HttpOnly cookies, which is what `rt_sign_in` and
  `rtings-mcp auth --browser` use. The ready signal is `GLOBALS.session.current_user`, never the
  cookie's presence or a change in it: RTINGS mints one anonymously and rotates it on every
  response, so both would fire while still logged out (§*rt_sign_in* below).
- **Inject with explicit attributes** (ported): a pasted `name=value` carries none, so the jar gets
  `_rtings_session=…; Domain=.rtings.com; Path=/; Secure; HttpOnly`.
- **Persist `Set-Cookie` rotations from the jar**, read via `session.get_cookie(name, https_url)` —
  **not** `resp.cookies` (only the final response's headers).
- **The session SLIDES, and that makes write-back mandatory (measured 2026-09-04,
  `RECON.md` §12.15).** Every response re-issues the cookie with `expires = now + 30 days`,
  so the window is idle-based: used, it never expires; unused, it dies in 30 days. Freezing
  the stored value at the paste therefore *manufactures* a monthly re-paste. Write back the
  rotated value **only when the probe on that session returned `current_user` non-null** —
  proof, not presence, because anonymous requests are re-minted too.

### wafer behaviour for a single-credential site (ported, and it applies harder)

CR had seven cookies and a self-re-minting `hash`; **RTINGS has exactly one HttpOnly cookie and
nothing re-mints it**, so every CR mitigation applies more strongly:

- **Member fetches use `max_rotations=0, max_failures=None`.** A wafer rotation rebuilds with an
  **empty cookie jar** and does not raise — so one transient 403 would make RTINGS return a normal
  anonymous `200`, indistinguishable from an expired session.
- **Compare the jar's `_rtings_session` against the CONFIGURED value — presence is not enough.**
  A plain anonymous GET to RTINGS *sets* a fresh `_rtings_session` (`RECON.md` §5, confirmed), so a
  presence check is satisfied by RTINGS' own anonymous cookie. Two bugs follow from checking presence:
  the `session_expired` guard passes when the credential is already gone, and — worse — **persisting
  "rotations" from the jar overwrites the user's pasted credential with an anonymous one**, silently
  destroying it on disk. A credential that no longer matches is `identity_rotated` — a transport
  error, never cached — not an auth state.
- **Write a rotated cookie back only when the rotating response is proven logged-in** (HTML probe,
  `current_user` non-null). Never write back from a response that yielded `current_user: null`.
- Read rotations from the jar, not `resp.cookies` (above).
- **`identity_rotated` is unreachable under these settings and stays as a guard.** With
  `max_rotations=0` wafer never empties the jar, so nothing should ever trigger it; it exists so that
  a future settings change fails loudly instead of silently classifying a lost credential as expiry.
- **A jar/config mismatch triggers the PROBE, not an immediate error.** RTINGS may legitimately
  re-mint a *logged-in* session (`RECON.md` §10 q3, unmeasured), which changes the jar value while the
  caller is still authenticated. Classifying that from the value alone discards a perfectly good
  response and throws away the new credential. So on mismatch: run the HTML probe. `current_user`
  non-null ⇒ **adopt the rotated value and write it back**; `current_user` null ⇒ `session: expired`.
  `identity_rotated` is reserved for a mismatch the probe cannot explain.
- **Classify `challenged` from the response, not from an exception.** Under `max_rotations=0` wafer
  *returns* 403/429/challenge/empty-200 rather than raising, so an exception-based mapping never
  fires. Read status and `resp.challenge_type`. An **empty 200** is `fetch_failed`, not
  `payload_missing` — the drift alarm means "the shape changed", not "the transport failed".
- **One shared `AsyncSession` means one `max_rotations` for all traffic.** The anonymous surface
  inherits the member settings; that is intended (RTINGS re-mints anonymously, so rotation buys
  nothing) but must not be re-tuned per call site.

### Tiers, in precedence order

1. **Anonymous — default, zero config.** Full catalog, schema, search, prose, recommendations,
   curves, and the public tests with their scores; no `insider_only` value or score, no usage
   ratings (§5). Never an error.
2. **Stored session** — `~/.config/rtings-mcp/session.json`, `0600`, written by `rtings-mcp auth`.
3. **`RTINGS_SESSION_COOKIE`** — a `Cookie:` string containing `_rtings_session`. Container/CI path;
   precedence over the stored file. **Provisional on `RECON.md` §10 q3** — CR's read-only env-var was
   viable because `hash` does not rotate; a Rails session may be re-issued, so if the pasted value
   stops working after a server-side re-mint, this path needs the stored-file rotation write-back
   instead.

`rtings-mcp auth` **validates immediately** by fetching one **HTML** page and reading `GLOBALS.session`
(not the API — the API has no marker), reporting `member session active` / `free account (no
insider)` / `that cookie did not authenticate`. Paste-and-know.

### Not done, deliberately (ported)

No password ever requested, stored or transmitted. No CAPTCHA solving, no automated login. Cookies
never logged, never written to the cache, never echoed in tool output. If the 30-day session proves
a re-paste burden, an optional Playwright `login` extra is the additive follow-on, producing the same
cookie through the same interface — deferred until proven necessary (§13).

## 7. Tool surface

Designed around questions, not endpoints. **One tool set, 28 silos** — silo and bench are parameters,
never tool names. **Seven data tools** (down from a first-draft nine; the fold is justified
below), plus **two that connect a membership** rather than serve data.

| Tool | Returns | Anonymous |
|---|---|---|
| `rt_silos()` | the 28 silos with `url_part`, **observed** paywall enforcement + `data_completeness`, tool pages | full |
| `rt_schema(silo, bench?, group?)` | test/usage definitions: name, `kind`, unit, hierarchy, `insider_only` | full |
| `rt_ratings(silo, bench?, tests?, usages?, filters?, sort?, limit=10, offset=0)` | catalog + 0–10 usage scores (+ optional scalar-test projection) | catalog full; scores/values gated |
| `rt_product(product=url\|id, group?, include_prose, include_media, include_verdicts)` | one review: leaf test results by hierarchy; prose, media and verdicts opt-in | prose/specs/**verdicts** free; scalars gated |
| `rt_graph(product, test)` | one test's **curve**, resampled | **full** |
| `rt_search(query)` | model name/number → candidates across all silos | full |
| `rt_recommendations(silo, list?)` | the silo's best-of lists, or one ranked list with reasoning | ranking + prose free; scalars gated |

| Membership tool | Does | Anonymous |
|---|---|---|
| `rt_sign_in(force?)` | opens a browser window on RTINGS' login page; stores the cookie only if RTINGS confirms it signed in | n/a — this is how you stop being anonymous |
| `rt_auth_status(wait_s?)` | what credential is stored and how a sign-in in flight is going; long-polls up to 45 s | n/a |

These two carry their **own lean envelope**, not `BaseEnvelopeOut`: `data_tier`,
`scores_available` and `test_benches` describe served measurement rows, and a sign-in serves
none, so filling them in would be a claim about data nobody fetched. `session` is the one shared
field that means the same thing on both.

**Why seven data tools, not nine.** `rt_products` folded into `rt_ratings` — anonymously they are the same
catalog list, and CR did exactly this (catalog into `cr_ratings`). `rt_results` folded into
`rt_ratings(tests=[…])` for a silo-wide scalar projection and `rt_product` for the per-product full
set — the `ratings` vs `test_results` API split is a fetch detail, not a question an agent asks.
**Kept separate:** `rt_graph` (the curve tool — unique output, the anonymous tier's raw-signal
feature) and `rt_recommendations` (the second, page-extracted envelope, §below) — it is the real
`cr_reliability` analogue: ranked, prose, anonymous, not derivable, and it is per-silo ranking, not a
search `detail` mode. This fold survives either Phase 0 outcome: if q1 confirms, `rt_ratings` carries
the product and the numbers fill in; if q1 fails, `rt_ratings` still returns the catalog with
all-null gated scores (honestly), and no tool needs deleting.

### `rt_silos()` is the routing tool — it reports OBSERVED enforcement, never `has_paywall`

`has_paywall` is `true` on all 28 silos and therefore carries no information (`RECON.md` §11.1). What
an agent needs is whether *this* silo's numbers are answerable right now, so `rt_silos()` returns, per
silo, a `data_completeness` of `full` / `gated` / `unknown` derived from observed `unblurred` on the
last fetch — `unknown` until something has been fetched, never a guess from the schema flag.

**Observations carry a provenance, and only an anonymous one may say `full`** (added 2026-09-06).
`data_completeness` is a claim about what *anonymous* gets, so who fetched it is part of the fact.
A member's fetch of a gated silo sees 100% unblurred; recorded untagged, that silo would read `full`
for every later caller on that machine — and with a credential stored, no anonymous fetch would ever
correct it. That is the stale-map lie the release re-scan exists to catch, reached locally and
permanently. So `ObservationStore.record()` takes `provenance` as a **required** keyword and the
precedence rules are asymmetric: an anonymous observation is never replaced by a logged-in one; an
anonymous one replaces a logged-in one at any width; a logged-in observation reports `unknown`, or
`gated` if it saw nothing unblurred (a membership cannot *add* blur). Legacy untagged observations
are treated as unknown-provenance, so an existing cache may report `unknown` for one call before it
self-heals. The same `provenance: anonymous` reading is what the anonymous-label write guard (§8)
consults as its proof that a silo serves a surface in full.

This matters because **16 of 28 silos answer numeric questions anonymously (budget unspent, `RECON.md`
§14) and 12 do not**, and the
agent cannot tell from the outside. Without it, an agent asking "rank air purifiers by CADR" and an
agent asking "rank TVs by peak brightness" get the same-shaped call and wildly different usefulness,
with no way to know in advance which one it is. With it, the agent routes: ask the open silos
directly, and for the gated 12 either fall back to curves and recommendations or tell the user a
membership is what unlocks it.

`scores_available` (below) says the same thing per response; `rt_silos()` says it *before* the call.

### `silo` is an enum, not a free string

The 28 `url_part`s are small (~200 tokens) and change on the order of once a year, so they ship in
the `silo` parameter's **`description` and `examples`** — not as a JSON-Schema `enum`. The agent gets
the list without a `rt_silos()` round trip, and it still costs only a couple of hundred tokens.

**Not an `enum`, deliberately.** A JSON-Schema `enum` is enforced *client-side*, before the call ever
reaches the server, so a silo RTINGS adds mid-release becomes **unreachable** until a new release
ships — and it establishes a second allowlist that can disagree with the live `static.silos` the
server validates against. Both are the failure the "derive from `is_recent`, never hardcode
`[197,210,227]`" rule (§below) exists to prevent: a hardcode is allowed only where it cannot become
the sole source of truth. **Validation is server-side against the live silo list only**, and a value
outside the shipped hint list is fetched normally with a `silo_hint_drift` warning. Nothing else
from the schema goes into a tool description — `column_options` is 357 KB per silo and ~10 MB across
28, and a tool description is context paid on **every** session whether or not anyone asks about TVs.
That is what `rt_schema(silo, bench?, group?)` is for.

### Response envelope

Every tool returns (`outputSchema` declared):

```jsonc
{
  "auth_state": "member",          // derived (§6) from data_tier + session
  "data_tier": "unblurred",        // what the DATA proves: unblurred | unproven. Never "member" (§6).
                                   // DERIVED per response from the rows served — never a stored label (§8)
  "session": "member",             // credential health: member | free | anonymous | expired | unknown
  "scores_available": {            // object of available | gated | absent, DERIVED (§below); never boolean
    "usage_ratings": "gated",      // table_tool__ratings — the 0-10 usage scores
    "insider_tests": "gated",      // every test with insider_only:true (value AND score)
    "public_tests": "available"    // every test with insider_only:false (value AND score)
  },
  "rank_scope": "within_bench",    // comparisons are scoped to one bench (§below); never cross-bench-as-comparable
  "test_benches": [{"id": "227", "display_name": "v2.2"}],  // always a LIST (§below), even when length 1
  "sorted_by": {"field": "released_at", "gated": false},    // what the ordering actually used (§below)
  "fetched_at": "2026-09-02T14:02:11Z",
  "from_cache": true,
  "stale": false,                  // TRUE means past-TTL and nothing else
                                   // NB: superseded_at is PER ROW (§8), not an envelope field — it
                                   // rides on the row it describes, never summarized upward
  "previews_remaining": null,      // rt_product on a free session only: access_limit - previewed_products (§7)
  "source_url": "https://www.rtings.com/tv/tools/table",
  "warnings": [],
  "error": null,
  "data": { /* per-tool; may carry data.notice when gated fields are entirely null */ }
}
```

### `scores_available` — derived from OBSERVED `unblurred`, per (silo, bench) (§5)

****Compute it from the FRESHEST response's own rows, as a ratio — never "any `unblurred:true`", and
never over the merged set.** A single unblurred row does not make a bench available: a gift or preview
unblurs 1 product of 97 (`RECON.md` §5), and "any" would report `available` while 96 rows stay gated.
And computing it over the merged read (which retains rows from before a silo was gated) would keep
saying `available` forever after an open→gated flip — the exact stale-map lie the release re-scan
exists to catch (CLAUDE.md > Release). So: ratio over the freshest response, excluding
`published:false` rows, with a third value **`partial`** carrying the ratio. Never-downgrade retains
**rows**, never the boundary.

`insider_only` alone is wrong for 16 of 28 silos** (`RECON.md` §11.1): air-purifier,
mattress and vpn serve 100% of their `insider_only` rows unblurred, camera 98.7%. So the flag marks a
test *gate-able*; only an observed `unblurred` says whether it is gated here. Derive per (silo,
bench), never per silo and never from the schema flag alone. When computing the boundary, **exclude
rows whose product is `published:false`** — those are blurred for a different reason (§11.2) and
would make an open silo look gated.

> A test/usage is `available` if every observed row of that surface came back unblurred, `gated`
> if none did, `partial` (with a ratio) if some did. `absent` is reserved for a surface the silo
> does not carry at all, and **`unknown` for a surface this response did not query**.
>
> **(corrected 2026-09-04)** `unknown` is new. The rule used to be "with a population but
> nothing observed, report `gated`", justified as fail-safe. It is not fail-safe, it is
> wrong in the direction that matters: `rt_ratings("mattress")` with no `tests=` argument
> would report `insider_tests: gated` about a silo that serves those values outright, and an
> agent reading it would route *away* from the category that would have answered the
> question. This is the same distinction as `coverage_unknown` vs `not_tested` — "I did not
> look" is not "I looked and it was withheld".

**The three keys are defined against the schema, not invented categories:**

| key | population | source |
|---|---|---|
| `public_tests` | tests with `insider_only == false` | `column_options` test defs |
| `insider_tests` | tests with `insider_only == true` | `column_options` test defs |
| `usage_ratings` | the silo's usages | `table_tool__ratings` |

An earlier draft used `specs / measurements / usage_scores`; `specs` and `measurements` are not
schema concepts and the split would have had to be guessed per test. `insider_only` is the actual
axis, so the keys name it. Note `public_tests: "available"` covers the **value and the 0–10 score
together** — a public test ships both anonymously (`RECON.md` §2, confirmed).

`insider_only` is a real, session-independent schema flag (`RECON.md` §6), so this is grounded, not a
hardcoded boundary. It fails safe: the default reading of a gated field is "you can't see it", never
"available".

**Derive per `(silo, bench)`, not per silo.** The same silo gates differently across benches — TV
bench 227 exposes 6 public tests, TV bench 2 exposes 1 (`RECON.md` §2, confirmed 2026-09-03). A
per-silo derivation mislabels every legacy-bench product. The measured TV boundary stays in
`RECON.md` as documentation, never as code input.

**`usage_ratings` is unverified for `insider_only`.** `RECON.md` §6 records the flag on *test*
definitions; whether usage definitions carry it was not checked. Until it is, derive `usage_ratings`
from observed `unblurred` only, and do not assume the field exists on a usage def.

### Ranking / comparison — within a bench (`RECON.md` §8)

`test_bench` scopes every comparison (RTINGS' nearest analogue to CR's within-`_groupName` rule, but
it is a methodology axis, so the default is the **site's own recent-bench set**, not a single bench):

- `rt_ratings` / `rt_product` carry `test_bench` on every product and default to the **recent-bench
  set the site itself renders together**.
- **That set is derived, never hardcoded.** It is the benches flagged `is_recent:true` in the silo's
  page-embedded `GLOBALS` bench list, at `GLOBALS.static.silo` (`RECON.md` §8, §12.4).
  `[197,210,227]` is the TV answer, not the rule — the set size varies per silo (tv 3, headphones 4, mouse 2), and the two
  plausible shortcuts ("top 3", "same major version") were both tested and both fail. Bench **ids**
  come from the page; bench **definitions** from `column_options`, whose bench list is shorter (TV: 14
  vs 18) because it omits benches with no published schema.
- **IMPORTANT: `latest_test_bench_id` is NOT the current bench (corrected 2026-09-04).** On
  **5 of 28 silos** it names a bench that is in neither `column_options` nor the `is_recent`
  set — air-conditioner 258 (recent: 39), air-fryer 265, laptop 285, router 269,
  toaster-oven 266 (`RECON.md` §12.4). Reading it as the current bench costs real
  correctness: nothing ever matches it, so every slice on those silos falls to the 30-day
  legacy TTL instead of the 7-day current-bench one — which is the *only* mitigation for the
  open coverage hole (§8). Derive it as **the newest bench that the site renders and that has
  a published schema**.
- A caller widening beyond that set gets results **nested by bench, never flattened** — the same
  structural discouragement CR uses for display groups. `rank_scope: "within_bench"` always; there is
  no cross-bench "comparable" mode.
- **One response shape, always.** `data.groups: [{test_benches:[…], products:[…]}]` — the default is
  a single group spanning the recent set; widening **adds groups** rather than changing the shape.
  An earlier draft returned a flat list by default and a nested one when widened, which would have
  made `outputSchema` describe two different types and forced every caller to branch. The envelope
  field is `test_benches` (a list, length 1 when a caller pins one bench), for the same reason.
- **`rank_scope: "within_bench"` names the rule, not the population.** The default population is the
  recent *set* (3 benches for TVs), so the label means "never ranked across the bench boundary the
  site itself draws" — it does not claim the population is one bench.
- **This is a conservative default, not a measured incomparability** (`RECON.md` §8, §10 q7). If
  minor benches prove additive, the default population widens with no envelope change.

### Test/usage normalization (ported, `RECON.md` §6)

Each value → `{original_id, name, kind, value, raw_value, unit, gated, status, as_of}` where
`status ∈ {tested_visible, tested_gated, not_applicable, not_tested, review_unpublished,
coverage_unknown, unknown_row_status}` (§5). Resolution order, and the order matters:

0. **coverage check first (§8)** — is this `(product, bench, test)` inside a covered, fresh,
   non-stale slice? If the product is absent from, or on a different bench in, the catalog generation
   the slice was fetched against ⇒ a **miss**, not an answer: refetch. If the refetch cannot happen
   (offline, cooldown) ⇒ `{value:null, gated:null, status:"coverage_unknown"}` + warning.
   **Skipping this step turns every product newer than the cached slice into a false `not_tested`** —
   the exact lie §5 forbids, reached without a member session being involved.
1. row **absent** inside covered, non-stale scope → `{value:null, gated:null, status:"not_tested",
   as_of:<slice fetched_at>}`
2. row `status == "untested"` → **also `not_tested`** (`RECON.md` §11.3 — the table path emits both
   an absent row *and* an explicit `untested`; do **not** send this to step 5)
3. row `status == "na"` → `{value:null, gated:null, status:"not_applicable"}` — **regardless of
   `unblurred`**, which is `true` on 47% of them (`RECON.md` §11.4)
4. row `status == "tested"`:
   a. the product was `published:false` **in the same fetch that produced this row** → `{value:null,
      gated:null, status:"review_unpublished"}` — an Early Access review (`RECON.md` §12.10).
      **Checked AFTER `unblurred`**: a member's real Early Access values must not be discarded.
      **Check this before attributing any blur to the paywall.** Read it from
      `unpublished_product_ids`, embedded in the slice/review envelope **at fetch time** — never from
      the current catalog, which refreshes on its own 3-day clock: a review published on day 4 would
      otherwise make a day-1 blurred slice read as `tested_gated` ("buy a membership"), and a review
      later un-published would null a retained unblurred row, defeating never-downgrade. For
      `rt_product(url)` resolved via search — search hits carry no `published` (`RECON.md` §3) — fetch
      the product's bench catalog before normalizing.
   b. `unblurred == false` → `tested_gated`, `{value:null, gated:true}`
   c. `unblurred == true` → `tested_visible` with the coerced value — **which may legitimately be
      `null`** (2 measured rows, `RECON.md` §11.4). When it is, `gated` is **`null`, not `false`**:
      `{value:null, gated:false}` is the one pair the rule below forbids, and `gated = (unblurred ==
      false)` would produce exactly it. So `gated = null if value is null else false`.
5. anything else → `{value:null, gated:null, status:"unknown_row_status"}` + warning

`coverage_unknown` is an honest "I don't know", distinct from both `not_tested` ("RTINGS did not
measure this") and `tested_gated` ("measured, you can't see it"). It must appear in the
`outputSchema` `status` enum alongside the other five.

**`gated` is `null`, not `false`, whenever there is no value to gate.** A `not_tested` row written as
`{value:null, gated:false}` is indistinguishable from a *visible* value that happens to be null —
the multi-state distinction survives in `status` but is destroyed in the pair an agent is most likely
to read. Never emit `gated:false` alongside `value:null`.

Coverage is bench-specific, so which tests a product carries depends on its bench —
the projection must fill absent pairs with `not_tested` rather than dropping them, or an agent reads a
missing test as an unremarkable gap. Other traps port one-for-one: coerce by declared `kind` never by
value shape; never infer a unit (`number_display_unit`/`_precision` ship in the def); `original_id` is
the stable key, `name` is not; join definitions from `column_options` (keyed by `original_id`, and
`id` ≠ `original_id`), not the per-row `test:{id}` stub. A value that will not coerce keeps
`raw_value`, sets `value:null`, and warns — never guess.

### `rt_graph` — bounded, curve-only

`kind == "graph"` tests have a curve (`RECON.md` §4, §6); any other test returns a structural
`no_graph`, not an empty result. Output is resampled to ~200 points by default with `{header,
n_points, x_range, axis_bounds_of_served_points, points}`; `full=true` opts into the raw series.
`graphs/` is keyed `(product_id, test_original_id)`, no auth tier (curves are session-independent,
`RECON.md` §4).

**Resampling is not derivation, and the implementation is what keeps that true.** Resampling must
**select** points from the shipped series (decimation, or LTTB) — never interpolate, average or
smooth. An interpolated point is a number RTINGS never measured, which is the §5 prohibition wearing
a different hat.

**No headline scalar, including axis bounds.** An earlier draft returned `y_range`. On a
peak-luminance or EOTF curve `y_range.max` *is* the gated scalar in all but name, so bounds are
either omitted or returned under `axis_bounds_of_served_points` — a name that says it describes the
points we served, not a measurement of the product.

**Three outcomes, distinguished (`RECON.md` §4, confirmed 2026-09-03):**

| condition | result |
|---|---|
| `kind != "graph"` | `no_graph` — structural, this test never has a curve |
| `kind == "graph"` but the product's row has no `graph_data_url` | `graph_not_available` — this *product* has no curve for it |
| CDN returns non-200 | `fetch_failed`, with the CDN `source_url` |

The middle case is real and common: graph coverage is per-(product, bench), not schema-wide — 5 of
402 rows carry a `graph_data_url` on TV bench 227, and **0 of 54** on TV bench 2, where a legacy
product has no curves at all. Collapsing it into `no_graph` would tell an agent the test never has a
curve when it simply has none for that product.

**Fetch the CDN uncredentialed.** `i.rtings.com` needs no cookie (confirmed), and `_rtings_session`
is `Domain=.rtings.com`, so the default session would offer the credential to the CDN for nothing.

### `rt_schema` — bounded

The TV schema is 402 tests + hierarchy in 357 KB. `rt_schema` defaults to **leaf tests with
`has_score`, one `group` at a time** when `group` is given, and returns the group/category tree with
counts otherwise — never the raw 357 KB. Attribute *descriptions*/tooltips are category constants and
are served here, once, not per product (ported).

### `rt_product` — bounded, and metered

A current-bench review is **402 rows / 437 KB** (`RECON.md` §6), so this tool bounds like the others:

- **Default to leaf value kinds** (`number`, `word`), each row carrying a `hierarchy`
  breadcrumb. The list is **flat**; nesting it is deferred (`TODO.md`). Prose
  (`linked_description`) and media (`picture`, `video`, `dropdown_images` — 84 of 402 TV rows) are
  **opt-in**; media returns a URL, never an embedded asset.
- **`group` and `category` rows are structure, not results** (57 + 12 of 402). They carry no value and
  must never be given a `status` — a `group` marked `not_tested` is a category header reported as a
  missing measurement.
- **Scope the expected-test set to the product's own bench**, never the silo (§5).
- **IMPORTANT: this path has no machine `value` — parse `rendered_value`** (confirmed unblurred,
  `RECON.md` §5). A review row carries only `rendered_value` (a formatted string — `"1950 cd/m²"`,
  `"Yes"`) and `score`; there is **no `value` key**, blurred or unblurred. So `rt_product` recovers a
  numeric value by parsing `rendered_value` against the schema's `number_display_unit`/`_precision`
  (strip the unit, apply `kind`). This is the one place the "coerce from a raw `value`" rule cannot
  apply — the table tool (`rt_ratings`) is where a clean `value` exists. When the number is only
  needed numerically, prefer the table path; `rt_product` is for the full per-product picture.

**Corrected 2026-09-07 (`RECON.md` §14): `page_body` does NOT increment the meter, and a free
account has no preview budget at all** — the meter is three products on the 16 metered silos,
anonymous and free alike, spent by the review page's HTML GET and held in a plain cookie. The
budget control below stays as insurance and arms only when the probe reports a non-null
`access_limit`; with none, a `free` session behaves like `anonymous`. The original design:

**It consumes the user's metered previews — this needs a BUDGET, not a timer.** `rt_product` is
`app/product_vue_page__page_body`, the endpoint the server-side free/preview meter was believed
to count (`RECON.md` §10 q2). Spacing calls two seconds apart protects nothing — it just spends the user's
previews more slowly. Time is the wrong axis; **count** is the right one. `max_retries=0` on this
POST is necessary (wafer's default of 3 turns one call into three consumed previews) and not
sufficient. On a `session == free` run — anonymous has **zero** previews (`RECON.md` §5) and a member
has none of this problem — the full control is:

- **Check before spending.** Compute `remaining = access_limit - len(previewed_products)` from the
  last probe. If the product is not already in `previewed_products` and `remaining == 0`, return
  `preview_exhausted` **without fetching**.
- **Make the spend explicit.** A call that would consume a preview requires `consume_preview=true`;
  the envelope carries `previews_remaining` either way.
- **Never auto-refetch on TTL expiry.** A `reviews/` file past TTL on a free session is served
  `stale:true`, never silently re-bought. §7 already permits serving stale.
- **Hold the cross-process lock** (§8) across miss→fetch→write, so two MCP clients cannot
  double-spend the same preview.
- **Re-probe after the call** to refresh `previewed_products`, and never evict a preview-bought
  review (§8).

`RTINGS_MAX_PREVIEW_SPEND` (default `1`) caps distinct products per process on top of all this. It
is **1, not 0**: at `0` an explicit `consume_preview=true` could never succeed, which is a silently
broken parameter rather than a safe default. Set it to `0` to forbid spending entirely.

**The data→probe trigger must fire for `free` on `reviews/` too** (§8). Without it, a lapsed free
cookie yields a blurred review that is `free`-labelled, LRU-exempt and never auto-refetched — cached
nulls with no path back. Write-time demotion (§8) is what catches it.
**The meter's unit — per product, per session, per day — is still Phase 0 capture (i)**, so this is
the structure with one unknown constant, not a finished control.

### `rt_product(include_verdicts=true)` — RTINGS' words, which survive the paywall

`app/side_by_side__review` (`RECON.md` §12.16) returns, anonymously and on a **gated** silo,
what the measurements cannot: the per-usage verdict prose, the pros/cons blurbs, and the
formula composing each usage score. On the 12 gated silos this is the substantive answer —
"is it good for gaming?" is answerable there even though "how bright is it?" is not.

- **Opt-in, because it is one extra request.** `include_prose` costs nothing (that prose
  rides on the review body already fetched); silently doubling its request count would be a
  surprise, so verdicts get their own flag.
- **Its `test_results` are ignored, deliberately.** They are a **third** row shape carrying no
  `unblurred` key at all, so the normalizer has nothing to branch on. The table and review
  paths already answer that question properly; joining this one in would be the
  "never write one accessor that assumes both shapes" trap with a third shape added.
- **`user_has_access` is this path's blur signal** — the payload has no per-row flag.
  Measured anonymously: `false` on TV (gated), `true` on mattress (open), so it *appears* to
  track the **silo's enforcement** rather than membership. Two data points, both anonymous:
  treat it as a lead, not a settled fact, and confirm it in Phase 0.
- **Four states, and the fourth is deliberately NOT `not_tested`:**

  | score | context | status | `gated` |
  |---|---|---|---|
  | present | — | `tested_visible` | `false` |
  | absent | product is Early Access | `review_unpublished` | `null` |
  | absent | `user_has_access:false` | `tested_gated` | `true` |
  | absent | `user_has_access:true` | `tested_visible` (empty) | `null` |

  The last row matters. One review-wide boolean cannot support the claim "RTINGS did not
  measure this", and `not_tested` means exactly that (§7 step 1: an **absent** row). Here the
  row is present, with an `original_id`, `suitable` and prose. The measured precedent points
  the other way — a visible row can be genuinely empty (§5) — so it is `tested_visible` with
  a null score.
- **The verdicts notice gets its OWN key.** `data.notice` already carries why the
  measurements are null, including "this review is Early Access"; a `dict.update` that
  overwrote it would replace that with paywall framing — the exact conflation §5 forbids.
- **Failure here must not fail the call**, in both directions. An error fetching verdicts
  becomes a warning (the measurements are already in hand); and a spent preview budget
  degrades to a **verdicts-only** response rather than an error, because the verdicts are not
  the metered surface and must not be charged for one.

### `rt_recommendations` — the isolated second envelope

Page-extracted, **not** an API query (`RECON.md` §7) — the one page-extraction path, isolated
exactly as CR isolated `cr_reliability`: its own parser, its own drift alarm
(`recommendations_missing`, distinct from the API `payload_missing`), and it never touches the
table/graph code path. If an API query is later found (`RECON.md` §10 q10), it moves onto it with no
contract change.

**There are TWO page templates and both are legitimate** (measured 2026-09-05, `RECON.md` §12.17).
RTINGS is migrating best-of pages off the monolithic `RecommendationVuePage` — one `data-props` blob
holding `page_data.page.recommendation.product_recommendations[]` — onto a **server-rendered**
template whose only Vue parts are small islands. mattress and running-shoes have moved; the other 12
silos sampled have not, and it does not track silo age, so more will migrate silently. Supporting
only the props shape made every one of mattress's 20 lists fail: the tool advertised lists it could
not fetch.

So the parser tries props, then static, and both emit the **same payload shape**, which is what keeps
the pick mapper, the featured-row tier derivation and the cached envelope identical across templates.
`recommendations_missing` now means **neither** matched — that, not "the props are missing", is the
drift signal. The static template carries no API either (its whole bundle is ~3 KB with zero
`/api/v2/safe/` references), so extraction remains the only route on both.

**A silo has many best-of lists, and the URL slug is not derivable.** `/tv/reviews/best/tvs` does not
follow from `url_part: "tv"`, and a silo carries dozens of lists (best gaming TVs, best 65-inch, …).
So the signature is `rt_recommendations(silo, list?)`: with no `list`, return the silo's **available
lists** (discovered from the silo's `tool_pages` / review-index links) rather than guessing a slug;
with `list`, return that ranking. Never hardcode a slug table.

### `rt_sign_in` / `rt_auth_status` — connecting a membership from a conversation

`rtings-mcp auth` is a terminal command, and **Claude Desktop has no terminal**, so before these
two the credential path was reachable from Claude Code and nowhere else. They put the same
capability in the conversation. The CLI stays as the path for a machine with no MCP client, and
both front doors call one implementation.

**Two tools, not one blocking call.** Claude Desktop kills a local tool call at 60 s and progress
notifications do not extend it, while a human sign-in takes as long as it takes. So `rt_sign_in`
starts a background task and answers in ~2 s, and `rt_auth_status(wait_s<=45)` long-polls under
the cap. Elicitation is not an alternative: Desktop declares no elicitation capability to a local
stdio server.

**The window is Playwright, headed, throwaway.** An installed Chrome/Edge/Chromium is launched
(nothing is downloaded) with `AutomationControlled` disabled, on a fresh context with no
`storage_state` and no profile — so it starts signed out and the user's own browsing is untouched.
The server **never reads, fills or submits a form field** and never sees the password: it polls
one boolean and then reads one cookie. The `[browser]` extra is optional; without it the tool
refuses and names the paste path.

**IMPORTANT: the ready signal is `GLOBALS.session.current_user`, never the cookie.** Consumer
Reports polls for its `hash` cookie to *appear*, and that logic ported directly would be broken
here: RTINGS mints `_rtings_session` for anonymous requests (`RECON.md` §5) and re-issues it on
every response (§12.15), so within a second of opening the login page both "the cookie exists" and
"the cookie changed" are true while still logged out. A capture keyed on either would store an
anonymous session over a working credential and leave every later call quietly signed out. So the
browser watches the same field the server-side probe reads, and the captured value is **still**
validated against RTINGS before anything is written: `capture_session()` → `validate_cookie()` →
`store_credential()`.

**`free` counts as signed in** (a real difference from CR, where only `member` does). A free
account is what the metered preview budget exists for, so discarding one would throw away a
working credential.

**The guard, in order.** `RTINGS_SESSION_COOKIE` set ⇒ refuse, because the env var wins over
anything a sign-in stores and success would be a lie. A cached probe that says logged-in ⇒ refuse
without `force`, and without a network round-trip. Stored but unproven ⇒ **verify, do not refuse
blind**: `session` is `unknown` until a probe runs and a fresh Desktop process may never have run
one, so refusing there would refuse a dead cookie forever and the user would recover only by
discovering `force`. A verdict of `could_not_check` opens no window and changes nothing — a
transport failure is no verdict on the cookie.

**The envelope is lean, deliberately.** These two carry `session` / `warnings` / `error` / `data`
rather than `BaseEnvelopeOut`: `data_tier`, `scores_available` and `test_benches` describe served
measurement rows, and a sign-in serves none.

### Filtering and sorting must not silently use gated fields

**On the 16 open silos this guard mostly does not fire** — the values are present, so sorting and
filtering on real measurements is a first-class anonymous capability, not a degraded one. The guard
exists for the 12 gated silos and for `published:false` rows anywhere. Implement it as a check on the
**served rows**, never on the silo: "is the field I am sorting by actually populated for these rows?"
A silo-level rule would either block legitimate sorting on the open 16 or permit silent null-sorting
on the gated 12.

A filter on a gated scalar is the quiet version of the §5 failure. Anonymously every
`insider_only` value is `null`, so `rt_ratings(filters={peak_brightness: ">1000"})` matches **zero
products** — and "0 results" reads as *no TV is that bright*, not *you cannot see brightness*.

- A filter or sort whose field resolves to `tested_gated` for the population is **not applied**. The
  envelope carries a `filter_unavailable` warning naming the field and the reason.
- The default anonymous sort is a **public catalog field** (release date), never a gated score, and
  the envelope always reports `sorted_by: {field, gated}` so the ordering is never anonymous-looking
  but secretly arbitrary.

### `rt_search`

`app/search__search_results` is anonymous and real (`"LG C4"` → 1,749 hits, `RECON.md` §3). RTINGS has
a live cross-silo search, so `rt_search` uses it directly. Results labelled by `kind`
(`page`/`product`/…), carrying `product_id` + `url` for drilldown. A miss says `searched: "rtings
live index"` so an empty result is never read as "not tested".

### Error taxonomy — for a JSON API

Errors are **structured values in the envelope** (`error` field), not MCP protocol errors — an agent
must distinguish "RTINGS has no data" from "the fetch failed". `error` is null on success. What
"payload missing" means for a JSON API is defined here, not inherited:

| Code | Cause | Retryable |
|---|---|---|
| `unknown_silo` | silo not in `static.silos` | No |
| `unknown_product` | product id/url not found (search miss or 404) | No |
| `unknown_test` / `invalid_bench` | `original_id` / bench id not in the silo schema | No |
| `payload_missing` | HTTP 200 but the expected `data.<key>` is absent/unparseable | No — schema drift |
| `api_error` | the response carries `errors[]` **and no `data`** | No — schema drift |
| `recommendations_missing` | **neither** best-of template parsed (props *and* server-rendered) | No — schema drift (isolated, §above) |
| `no_graph` | test has no curve (`kind != "graph"`) | No — structural, not an error state |
| `graph_not_available` | `kind == "graph"` but this product has no `graph_data_url` (§above) | No — structural |
| `unknown_row_status` | a row `status` outside `{tested, na}` (§5) | No — surfaced as a warning, never coerced |
| `session_expired` | **`auth` validation only** — the pasted cookie did not authenticate. A *tool* fetch that comes back logged out is `session` in the envelope, `error:null` | No — re-run `auth` |
| `identity_rotated` | the jar's `_rtings_session` no longer matches the configured credential (§6) | **transport** — surfaced, never cached |
| `fetch_failed` | network error, non-200, `WaferTimeout`, **or an empty 200** | wafer only |
| `rate_limited` | 429/503 with no detected challenge. Carries `retry_after` seconds | **Yes, after `retry_after`** — the server said when |
| `challenged` | a bot challenge read from the **response** (`resp.challenge_type`), not an exception (§6) | **No** — surfaced |
| `cooldown_active` | a per-host `cooldown_until` from a previous `rate_limited`/`challenged` is still in force (§9); no request was made | Yes, at `retry_after` |
| `coverage_stale` | the product postdates, or is absent from, the catalog generation the cached slice was fetched against (§8) | Yes — refetch the slice |
| `preview_exhausted` | `rt_product` on a `free` session with no metered previews left, or past `RTINGS_MAX_PREVIEW_SPEND` (§below) | No — not a failure, a budget |
| `cache_miss_offline` | not cached and network unavailable | Yes |

**Status precedence is fixed, because the rows overlap.** Classify in this order and stop at the
first match: `challenged` (a `resp.challenge_type` is set, whatever the status) → `rate_limited`
(429, or 503 **carrying** `Retry-After`) → `fetch_failed` (every other non-200, a transport error,
`ResponseTooLarge`, or an empty 200). A 503 with **no** `Retry-After` is `fetch_failed`, not
`rate_limited` — the row's "the server said when" is only true when the server actually said. And
`challenged` being non-retryable is about *that call*; the cooldown it installs surfaces later as
`cooldown_active`, which is retryable at its `retry_after`. The two are not in conflict.

**`rate_limited` and `challenged` are split deliberately.** An earlier draft mapped 429 onto
`challenged` and marked it not-retryable, which tells an agent "no" when the truth is "in 30 seconds"
— and, because §6 disables wafer's rotation path, nothing was sleeping `Retry-After` either (§9). A
429 is a *schedule*, a challenge is a *refusal*; collapsing them loses the only actionable field the
server sent.

**`errors[]` beside `data` is a partial-field notice, not a failure (corrected 2026-09-04).**
RTINGS strips admin-only fields and *says so* in `errors[]` while returning a complete payload —
`distribution_tooltip__test` reports three such fields and still answers. Treating any `errors[]`
as fatal discards that data. And the case this row was originally documented for is not an
`errors[]` case at all: `column_options` with an unknown silo returns `{"data": {"silo": null}}`
and no errors, which `payload_missing` already covers.

`payload_missing` / `api_error` / `recommendations_missing` are the drift alarms — loud, never a
degraded empty result. **A cached row is served with `error:null` whenever one exists**, even past
TTL and even when the refetch failed (`stale:true`, original `fetched_at`, `refresh_failed` in
`warnings`); `error` is non-null only when there is nothing to return. **We own the retry budget** — the API session runs
`max_retries=0` (§9), so a 5xx or empty 200 is returned to us and re-attempted (if at all) by taking
a fresh bucket token, never by wafer looping inside one. `challenged` is surfaced, never retried
around (ported); `rate_limited` is retried only after its `retry_after`.

## 8. Storage

**Plain JSON files under `~/.cache/rtings-mcp/`, not a database.** **Store the extracted JSON, not
the page** (ported). Each file holds an ingest **envelope**, not a bare API body, so a cache read can
always serve a unit and derive a `data_tier` without a second fetch.

**Why files, and why the coverage problem largely dissolves.** The earlier SQLite design was ported
from `consumer-reports-mcp`, where the API returned forced category dumps and the cache had to shred
a response into rows. Shredding destroys the record of *what was asked for*, which is the entire
reason a separate `fetch_coverage` table had to exist. RTINGS inverts the premise: the API is
parameterized and `table_tool__test_results` has **no product filter** (`RECON.md` §1), so one
request is `(test_bench_ids[], original_ids[])` and one response covers **every product** on those
benches for those tests. Store the response as fetched — partitioned only by bench, never shredded
to rows — keyed by what was requested, and **the file path is the coverage record**. Nothing else in the design needs SQL: `rt_search` is API-backed so there is
no local index; catalog filtering is in-memory over a 111 KB payload; and cross-bench joins are
forbidden by §7 anyway.

```
~/.cache/rtings-mcp/
  meta.json                                       # cache format version
  silos.json                                      # GLOBALS.static.silos            (untiered)
  bench/{silo}.json                               # is_recent bench list + latest_test_bench_id
  probe/last_probe.json                           # last probe: session, previewed_products,
                                                  #   access_limit, probed_at — NEVER the cookie
  cooldown/{host}.json                            # per-host cooldown + doubling counter
  schema/{silo}.json                              # column_options, 357 KB          (untiered)
  catalog/{silo}/{bench_id}.{fetched_at}.json     # products_list, per bench        (untiered)
  tests/{bench_id}/{original_id}.{cache_tier}.{fetched_at}.json   # one test_results slice
  ratings/{bench_id}/{usage_id}.{cache_tier}.{fetched_at}.json    # ~21 KB per usage
  reviews/{product_id}.{cache_tier}.{fetched_at}.json.gz          # page_body, 442 KB raw
  graphs/{product_id}/{original_id}.json           # curve as shipped               (untiered)
  observed/{silo}.json                            # per-(silo,bench) unblurred ratios + the
                                                  #   PROVENANCE of each (anonymous | logged_in);
                                                  #   what rt_silos routes on and what the §8
                                                  #   write guard takes as proof a silo is open
                                                  #   (never a hardcoded map)
  locks/                                          # cross-process advisory locks
  recs/{silo}/_lists.json                         # discovered best-of slug index
  recs/{silo}/{list}.json
  telemetry/requests.jsonl                        # headers only, never bodies (§9)
```

Envelope: `{fetched_at, source_url, cache_tier, request, catalog_generation, product_ids,
`unpublished_product_ids`, outcome, payload}`. The last two are what make `not_tested` and
`review_unpublished` answerable later without re-consulting a catalog that has since moved.

**`catalog/` is partitioned by bench, not by silo.** `products_list` takes `test_bench_ids[]`
(`RECON.md` §1) and each product sits on exactly one bench, so a multi-bench response partitions
cleanly on `review.test_bench.id`. A single `catalog/{silo}.json` cannot represent a caller who
widened past the `is_recent` set without either overwriting the recent-set catalog or losing the
wider one — and the catalog is the join key that assigns `test_results` rows (which carry no bench
id) to a bench. A multi-bench response is partitioned against the catalog generation
it was fetched with. A row whose `product_id` is in **no** generation has no bench to file under, so
it goes to `tests/_unassigned/{original_id}.json` (bench-less by construction) with a warning, and is
**never silently dropped** — a dropped row becomes a false `not_tested` later. **An unassigned row is
a structural property of the API, NOT evidence the catalog is behind** (corrected 2026-09-03,
`RECON.md` §12.2: 9 TV product ids returned rows while appearing in no catalog across all 18 benches,
every row blurred), so it must not mark that silo's catalog stale. The rows are summarised as a
`coverage: uncatalogued` group of ids — rows only on `include_uncatalogued=true` or a `product_ids`
filter — because resolving four of them by id (2026-09-06) showed they are RTINGS' internal copies
and retests ("LG G5 OLED (Copy)", "Boring Mattress - TBF 1.0.1"), kept out of the listing on
purpose and never something to recommend. `rt_product(<id>, silo=…)` identifies one through the
compare tool's `product` block (`product_page__url`, `fullname`, `test_bench`). A `coverage_stale`
miss refreshes the **catalog first**, or the refetched slice is filed against the same stale
generation and the miss repeats forever.

### Coverage — the file path, plus a staleness rule

**`not_tested` is emitted from an absent row.** With responses stored whole, "never fetched" and
"fetched, confirmed absent" are already distinguishable: the file exists or it does not. So:

- **cache hit** ⇔ every requested `(bench, test)` has a file at `cache_tier ≥` the probe tier,
  within TTL, and not coverage-stale (below).
- an absent row **inside** a covered, fresh file ⇒ `not_tested` (a real answer).
- an absent row **outside** covered scope ⇒ a miss. Fetch it; never answer from it.

This is what "a query serves the superset" means — superset of **coverage**, not of rows. A request
for tests `[1,2,3]` is satisfied by cached files for `[1,2,3,4]`.

**Coverage has a time dimension, and without it the cache emits the exact lie §5 forbids.** A slice
for `(bench 227, test 11)` is fetched on day 1 covering products *{A,B,C}*. On day 5 a new TV ships
on bench 227. On day 6 the catalog refreshes to *{A,B,C,D}* — or `rt_search`, which is always live,
returns D directly. Joining D against the day-1 slice finds no row and reports **`not_tested`** about
a product RTINGS may well have measured. The old `fetch_coverage(surface, bench_id, original_id,
tier, fetched_at)` table had this hole too: it has no product dimension either. So coverage is
`(bench, test, fetched_at, catalog_generation)`, and on join:

- a product **present on that bench** in the catalog generation the slice was fetched against, with
  no row ⇒ `not_tested`. A real answer, stamped `as_of: <slice fetched_at>` so its age is visible.
- a product **absent** from that generation, or present but **on a different bench** in it (a retest
  moves a product between benches, `RECON.md` §6) ⇒ **`coverage_stale`** ⇒ a miss for that product.
  Refetch the slice; if offline, emit `status:"unknown"` with a warning. **Never `not_tested`.**

**The generation must be resolvable, so the slice envelope carries the product set, not a pointer.**
`catalog/` refreshes on a 3-day TTL, so a bare `catalog_generation` id is dangling by day 4 — the
generation's membership is gone and "was this product in it?" becomes unanswerable, silently
defaulting to whatever the reader assumes. So the slice envelope embeds the `product_ids` of the
bench it was fetched against (a few hundred integers), and catalog generations are **immutable**
(`{fetched_at}` in the name), pruned only when unreferenced.

**A hole that remains, and is labelled rather than papered over.** A product that *existed* on day 1,
was untested then, and is tested on day 5 is **not** distinguishable by the generation check — it was
present either way. The obvious candidate signal is dead: **`last_updated_at` was measured
(`RECON.md` §11.6) as a bulk re-index field** — 65 of 74 cameras share one minute — so it fires
constantly without a retest and is not shown to fire with one. It cannot close this in either
direction, and must not be used as a `coverage_stale` trigger. What ships instead: the `as_of` stamp
on every `not_tested`, and a shorter TTL for `tests/` on a **current** bench than a legacy one (below).
**The hole stays open and labelled** — do not claim the check is complete.

The same applies to `reviews/`: store the review's own `bench_id` in the envelope and scope the
expected-test set to *that* bench, warning `bench_mismatch` if the catalog later disagrees — a
product retested onto a newer bench (`retest_message`, `RECON.md` §6) otherwise invents a bench-sized
block of false `not_tested`s.

**Implemented 2026-09-05** (it was described here and missing from the code). A review body normally
carries every test on its own bench — 54/54 and 402/402 measured — so the absent set is empty in
practice. It stops being empty exactly when the schema and the cached review disagree, and the two
are fetched on independent clocks. So the absent set is only a real `not_tested` when the review can
be shown to be the newer document: if the review is **past its TTL** and the schema was fetched after
it, those rows are `coverage_unknown` plus a `bench_mismatch` warning, because asserting "RTINGS did
not measure this" by comparing two documents of different ages is the false-absence the safety
property forbids. Sub-second ordering inside one cold call is not drift and does not trigger it.

**A `_urls.json` graph-URL map was dropped as unnecessary.** An earlier draft cached the
`graph_data_url` map extracted from a review body. It buys nothing: the outcome is already
cached per `(product, test)` — including the negative — so the second call for a pair never
reaches the API either way, and a second index is a second thing to keep in sync.

**Negative results are files too, or "file present = fetched" fails open.** `graph_not_available` is
the common case, not the exception — 397/402 rows on TV bench 227 and **54/54** on bench 2 have no
curve (`RECON.md` §4). With nothing written, every `rt_graph` on such a pair costs a
`graph_tool__product_graph_data_url` POST forever. So write `{outcome:"graph_not_available",
payload:null}`. Likewise a `(bench, test)` slice that legitimately returns **zero rows** is a written
file with an empty payload — that is real `not_tested` for every product, and a reader must not treat
an empty payload as a miss. What is **never** cached: `fetch_failed`, `challenged`, `rate_limited`,
`identity_rotated`, `unknown_product` — transport and lookup failures, consistent with §7.

Two rules ported, both structural, both load-bearing:

1. **`cache_tier` is part of the cache key — but only on the gated surfaces.** `tests/`, `ratings/`,
   `reviews/`, `verdicts/` and — since 2026-09-06 — `recs/{silo}/{list}` are tier-keyed; `schema/`,
   `catalog/`, `graphs/`, `bench/` and `recs/{silo}/_lists.json` are **not** (they carry no gated
   fields — `RECON.md` §3, §4 — so tiering them would store two identical copies, the mistake CR
   made and reversed). A best-of page does carry gated fields (each pick's `featured_test_results`
   and `ratings` have their own `unblurred`), which is why it moved. RTINGS returns a normal `200` blurred page when logged
   out, so an untiered gated cache would overwrite scored rows with null ones on the next anonymous
   call.
2. **Never downgrade.** A row with `unblurred:true` is never overwritten by an all-blurred row for the
   same `(product, bench, test)`. Selection is on the **data** (`unblurred`), not the tier — a member
   fetch can still be all-blurred if nothing was unlocked. A retained scored row past a newer unscored
   one reports `superseded_at`, and `stale` stays reserved for past-TTL only.

**`fetched_at` is in the filename, because one slot per tier cannot hold the rules.** "Freshest
among those", "a row from an older file", "`refresh=true` appends, never promotes" and `superseded_at`
all presuppose more than one file per key. With `{key}.{tier}.json` a same-tier refetch is a
whole-file **overwrite** — so an all-blurred member response silently clobbers a good one, and
never-downgrade is unenforceable *within* a tier (it only ever worked across tiers). Pruning keeps,
per key: the newest file per tier, **plus** the newest file containing any `unblurred:true` on an
`insider_only` test.

**Reads merge across tier files, per row — a whole-file tier cannot express "selection on the data".**
A gift link or a metered preview unblurs **one product inside an otherwise-blurred response**
(`RECON.md` §5 — 1 of 97 rows). Neither whole-file label is right: call the file `member` and it
serves 96 blurred rows under a member claim; call it `anonymous` and never-downgrade throws away the
one real value. So `cache_tier` labels only *provenance*, and the read is a **per-row merge** over
every tier variant of a key: take the row with `unblurred:true` from any variant (freshest among
those), else the row from the freshest variant. `superseded_at` is set per row when the served row
comes from an older file than the freshest one. The envelope's `data_tier` is then computed from the
rows actually served — and since `gated` is already per row, a 1-of-97 response is `unproven` with
one visible row, never summarized as `unblurred`.

**Never prune the newest unblurred file for a key** — age-pruning would delete exactly what
never-downgrade preserves. `refresh=true` appends, never promotes (ported).

**TTL is per surface, not a uniform 30 days.** A single TTL means a new bench goes unnoticed for a
month while the server ranks on a stale `is_recent` set — a correctness bug, not a freshness
preference. Serving stale on refetch failure (§7) already makes a short TTL free when offline.

| Surface | TTL | Why |
|---|---|---|
| `silos.json`, `bench/` | 1 day | decides the default `is_recent` set and `rank_scope` |
| `catalog/` | 3 days | new products ship weekly; also the coverage generation key |
| `schema/` | 30 days | test definitions move per bench, not per week |
| `tests/`, `ratings/` — **current** bench | 7 days | the mitigation for the open `coverage_stale` hole above; new products land on the current bench |
| `tests/`, `ratings/` — legacy bench | 30 days | a closed bench gains no products |
| `reviews/` | 30 days | measurements do not change without a retest |
| `graphs/` (positive) | 180 days | content-addressed CDN paths |
| `graphs/` (`graph_not_available`) | 3 days | a negative is *not* content-addressed — a product that gains a curve on retest would otherwise stay negative for six months |
| `probe/session.json` | per process | credential health, never long-lived |

**Growth is bounded explicitly, and eviction respects the meter.** `reviews/` is the only unbounded
surface — 442 KB × 548 TVs is 242 MB for one silo. Gzip the payload (`.json.gz`, stdlib; expect a
large ratio on this highly repetitive JSON — **estimate, unmeasured**; verify on the first real
review) rather than stripping the repeated `test:{…}` stub, which would trade away "raw JSON cached,
so a parser fix needs no re-fetch" (§11). Then `RTINGS_CACHE_MAX_MB` (default 1024) with LRU
eviction — **run from `flush_lru`, the hook every write batch already ends with, throttled by
`SIZE_CHECK_INTERVAL_S`; until 2026-09-05 it was implemented and unit-tested but called from nowhere
in the serving path, so the ceiling had no effect at all** — subject to two exemptions: **never evict a `reviews/` file whose `cache_tier` is `free`**
(that one cost a metered preview and refetching spends another), and never the newest unblurred file
for a key. **`member` is deliberately NOT exempt**: a member refetch is free, and exempting it would
un-bound the cache for the primary user — 548 TVs × 28 silos of protected `.gz` files. Note also that
file mtime is set to `fetched_at` (below), so it is **not** a recency signal and `atime` is
unreliable; keep an explicit `_lru.json` index of last-read times.

**Write path.** `tempfile.mkstemp(dir=<target dir>, prefix=".tmp-")` — never `/tmp`, because
`os.replace` across filesystems is a copy, not atomic — then `fsync`, then `os.replace`. Sweep
orphaned `.tmp-*` at startup. A reader treats `JSONDecodeError` or truncation as a miss and unlinks.
Set each file's mtime to `fetched_at` so TTL and `stale` are a `stat`, not a 442 KB parse. Cache dir
`0700`: member payloads are data the user paid for.

**Cross-process lock — single-flight is not enough.** Single-flight (§9) is in-process, but Claude
Code, an IDE and a CLI all share `~/.cache/rtings-mcp`. Two processes miss the same key and both
fetch; on `reviews/` that **double-spends a metered preview**. So take an advisory lock around
miss→fetch→write (`fcntl.flock` on `<key>.lock`; `msvcrt.locking` or `portalocker` for the
any-platform promise), and re-check the cache after acquiring.

**Lock at the REQUEST grain, not the key grain.** One `test_results` response writes ~120
`(bench, test)` files, and taking 120 locks contradicts "never hold two locks" below. The lock key is
the request identity — silo + bench set + hash of the test set — so one fetch takes exactly one.

**Never hold two locks.** The per-host `cooldown_until` (§9) is also cross-process, and a process
holding a key lock while waiting on the cooldown lock — while another does the reverse — is a textbook
deadlock. So `cooldown_until` is read and written **lock-free via atomic replace** (it is a single
small file and a lost update costs one extra request, never correctness), and any lock it does take
is released before a key lock is acquired. **`fcntl.flock` blocks the event loop**, so it runs in
`asyncio.to_thread`, or as `LOCK_NB` with polling — never inline in a coroutine.

**Every path segment is validated before a path is built.** `silo` must be in `static.silos[].url_part`
(else `unknown_silo`) and lowercased first — that also makes APFS's case-insensitivity a non-issue.
`bench_id` / `original_id` / `product_id` / `usage_id` must match `^\d{1,10}$`; a recommendations
`list` must be in the discovered slug index and match `^[a-z0-9-]{1,64}$`; `cache_tier` is an enum.
`rt_product(url)` resolves to a numeric id via the catalog or search and **never** derives a path
from the URL. Final guard before any open: `resolved.is_relative_to(cache_root)`.

**`cache_tier` is the probe tier at fetch time — NOT a data-derived label.** This is the fix for the
deadlock described in §6. Two different questions were previously answered with one field:

| | `cache_tier` | `data_tier` |
|---|---|---|
| Question | what credential was in play when this was **fetched** | what the served **bytes** prove |
| Values | `anonymous < free < member` (`free` only on `reviews/`) | `unblurred` / `unproven` |
| Source | the last `session` probe, recorded at write time | the rows in *this* response, at read time |
| Stored? | **yes — in the filename** | **never** — recomputed per response |

`cache_tier` is written from the **last `session` probe**, not from "a cookie is set": a
configured-but-expired cookie must not demand a tier that can never arrive. `session:"expired"` ⇒
`cache_tier: anonymous`. Pre-Phase-0 it is always `anonymous`, which is exactly what "the tier ships
from day one carrying `anonymous`" (§10) means — enabling member mode adds values to an existing
axis, never a migration.

**IMPORTANT: the label is DEMOTED at write time when the response contradicts the probe.** The probe
and the fetch race (§6), so a naive "label it with the last probe" writes `11.member.json` holding
zero unblurred rows when the session lapsed mid-call — and the hit rule then serves those nulls to a
re-authenticated member for the file's whole TTL, which is precisely the "configure a cookie, get 30
days of cached nulls" failure the tier check exists to prevent. A cache *hit* never re-probes, so
nothing later corrects it. So, before writing a tier-keyed file:

- **Re-probe first, and never throttle the probe on this path** (the once-per-interval throttle is
  for the read path). Use the probe's answer, not the stale one.
- **Demote to `anonymous`** if the response contains `insider_only` rows with `status:"tested"`,
  none of them `unblurred:true`, **and the tier predicts unblurred** — i.e. `member` on any surface,
  or `free` on a `reviews/` fetch for a product already in `previewed_products`.
- **The predicate above is the TEST-path one, and it is per surface (corrected 2026-09-05).** A
  `table_tool__ratings` row has **no `status` field** and its `original_id` is a *usage* id, while
  `insider_ids` holds *test* ids — so on `ratings/` that predicate matched nothing, always returned
  `False`, and demotion was structurally **dead** on the surface `rt_ratings` uses by default. The
  same cross-namespace mistake in `envelope_notes_for` left `has_unblurred_insider` permanently
  `False` there, so the "keep the newest file holding unblurred data" pruning exemption could never
  fire and a later blurred refetch could delete a member's only copy of real scores. Ratings gate
  wholesale rather than per-flag, so the ratings predicate is `unblurred` alone across all
  non-Early-Access rows.
- **`verdicts/` demotes on the usage SCORES, not on `user_has_access`.** That flag is this payload's
  own blur signal, but anonymously it is `false` on TV and `true` on mattress, so it looks like it
  tracks silo enforcement rather than membership — a lead from two data points. If it never flips for
  a member on a gated silo, demoting on it would demote every member write there and miss every read
  forever. All-null usage scores under a tier that predicts otherwise is the same evidence without
  the dependency. Capture (p) in §10 settles what the flag means.
- **Exclude `published:false` products from that test.** An in-progress review is blurred for
  everyone (§5), so a member fetching one would demote to `anonymous`, then miss the hit rule
  (`cache_tier ≥ member`) on every subsequent call — reinstating the permanent refetch loop, for
  every unpublished product.
- **Re-probe once per RESPONSE, not per file** — one `test_results` POST can write ~120 files — and
  it still takes a bucket token.
- **Refuse to demote off a CloudFront-cached probe.** If Phase 0 capture (f) finds the probe page
  served from the anonymous cache to a member cookie, every write would demote and every read would
  miss, forever. Record the probe's `X-Cache`; on a cache hit, skip demotion and warn.
- The predicate is deliberately narrow: a public-test slice, an all-`na` slice and a `free` table
  fetch are all **vacuous** under it, so the §6 deadlock does not come back through the side door.

Demotion is a **write-time** rule. The read path stays probe-vs-probe and never inspects data to
decide a tier.

### The anonymous-label guard — when the honest answer is not to cache at all

Added 2026-09-06, alongside the `RTINGS_MEMBER_MODE` flip. Demotion answers "the tier claims more
than the bytes deliver". This answers the mirror case: **the bytes deliver more than the tier
claims.**

With the flag off (still supported, no longer the default) every tier-keyed write is stamped
`anonymous`, and a live credential still gets member data. `anonymous` is a *promise* — that a
signed-out session would have received these bytes — and a member's fetch of an enforcing silo
breaks it: measured, tv returns 588/588 `insider_only` rows unblurred for a member and 0/588
anonymously (`RECON.md` §13.1). A later cache hit would then serve member-only measurements to a
signed-out caller under an `anonymous` label. Relabelling is not available either, because writing
a tier above `anonymous` is precisely what the flag forbids. So the write is **refused**.

`auth.anonymous_write_refusal` requires all three:

1. **The session may have unblurred it** (`session_may_unblur`). No credential configured ⇒ `False`
   by construction — an inference from our own configuration, costing no probe — so the ordinary
   anonymous user never reaches the predicate, never reads an observation and never sees a warning.
   `expired` is also `False`: a dead session cannot resurrect, so the bytes are what anonymous gets.
2. **The response holds a row a signed-out session might not have seen** — an `insider_only` row
   with `status:"tested"` and `unblurred:true` (tests/reviews), or any unblurred usage row
   (ratings/verdicts, which gate wholesale). **Unblurred `na` rows and public rows are deliberately
   not evidence** — 47% of `na` rows are unblurred anonymously (§11.4 of `RECON.md`) — so all-`na`
   and public-only slices stay writable and the §6 deadlock does not return.
3. **No signed-out fetch has proven this (silo, bench) serves that surface in full**
   (`ObservationStore.anonymous_serves`). This is the load-bearing condition: **16 of 28 silos serve
   `insider_only` rows unblurred to anonymous** (with the preview budget unspent, which the server's
   own jar always is — `RECON.md` §14), so on mattress a member's bytes are the same bytes
   anonymous gets, the `anonymous` label is true, and the write must go through. Without this
   condition the guard would refuse every legitimate write on more than half the catalog.

Plus one unconditional refusal: an unblurred `tested` row for a `published:false` product. Early
Access is blurred for anonymous on **every** silo (§5), and a membership is exactly what lifts it,
so such a row is member-only even on an open category.

**A refusal never fails the call.** The rows are returned; only the write is dropped, with a
`not_cached` warning that names the real cause — flag off, demoted, or what the probe read — and
offers the matching remedy. It must not tell the user to enable a flag that is already enabled.

**Bootstrap.** On a fresh cache with a live session and the flag off there is no anonymous baseline,
and one response cannot distinguish the two cases. The write is refused, the rows served, the
warning raised, and the observation recorded as logged-in provenance. This is not the §6 deadlock:
reads are unaffected, it is loud on every call, and it names both exits (turn the flag on, or fetch
the category once signed out). The cost is real and should be understood — with the flag off a
signed-in user re-fetches on every call, measured at 2.3 s against 0.0 s cached on an open silo —
which is a large part of why the default is now on.

**`free` exists only on `reviews/`.** A free account unlocks nothing on the table path — nor, as
measured 2026-09-07, anywhere else (`RECON.md` §14.6); the tier is now a harmless label — so a `free` probe fetching `tests/` or `ratings/` would miss
every `anonymous` slice and write a byte-identical `free` copy, exactly the duplicate-rows mistake
rule 1 exists to avoid. So the **demand** tier on `tests/`/`ratings/` is `member` when the probe says
`member`, and `anonymous` otherwise; `reviews/` uses the full three-value order.

**The hit rule is probe-vs-probe.** A hit requires every requested `(bench, test)` covered by a file
at `cache_tier ≥` the current probe tier, within TTL, and not coverage-stale (below). Because both
sides come from the probe, a public test, a free account and an all-`na` slice all resolve normally
instead of missing forever. Never-downgrade (rule 2 above) stays on the **row's** `unblurred` bit,
where it was always correct.

**One sanctioned data → probe trigger, and it never sets auth.** If a response is entirely blurred on
`insider_only` tests while the last probe said `member`, that is the documented race (§6). The server
**re-runs the HTML probe** (rate-limited, at most once per process per interval) and uses *its*
answer. The data still never sets `session` — it only triggers an authoritative read. Without this,
`session` is fixed at first probe and a mid-session lapse is reported as member data forever;
with it, the "never infer auth from null data" rule is preserved exactly, because the inference is
"go ask", not "you are anonymous".

## 9. Stack

- **Python `>=3.12`, developed and run on 3.14.** 3.12 is supported to Oct 2028 and gives the widest
  install base for a locally-run tool; CI matrix 3.12 / 3.13 / 3.14.
- **`uv`** for everything (never `pip`).
- **HTTP via `wafer-py`** (`~/code/wafer`, `llms.txt`) — never `urllib`/`requests`/`httpx`. The
  `/api/v2/safe/` endpoints are clean JSON, but they sit behind CloudFront and the member traffic is
  repeated and authenticated, which is what wafer is for.
- **Always pair `timeout=` with `attempt_timeout=`** — `timeout` is a total budget across retries;
  unpaired, one hung request eats it and retries never fire.
- **IMPORTANT: `max_retries` is a CONSTRUCTOR kwarg, not a per-request one — so "set `max_retries=0`
  on the `page_body` POST specifically" was never implementable.** `AsyncSession.request()` pops
  exactly `headers, params, timeout, attempt_timeout, max_response_size`
  (`wafer/_async.py:1323-1328`) and forwards the rest to the underlying client; `max_retries` lives on
  `BaseSession.__init__` (`wafer/_base.py:660, 766`) and is read from `self` into `RetryState`
  (`_async.py:1419`). A per-request value is a `TypeError` at best and silently ignored at worst —
  either way `app/product_vue_page__page_body` would have run at the default of **3 retries = up to 3
  consumed previews**, the exact harm the rule exists to prevent. The control has to be a session
  property.
- **Two sessions, and the API session sets `max_retries=0`.** `www.rtings.com` carries the credential;
  `i.rtings.com` is an uncredentialed static-asset CDN (2–361 KB curve JSON — TV's ~74 KB is the
  small end, speaker's "Raw Frequency Response Graph" the large, `RECON.md` §4). One
  session would force a floor meant for the origin onto static files **and** offer `_rtings_session`
  (`Domain=.rtings.com`) to the CDN for nothing (confirmed: `add_cookie` with an explicit `Domain`
  records `host_only=False`, `wafer/_base.py:2542`).
  - **API session** — cookie injected. `max_retries=0, max_rotations=0, max_failures=None`,
    `rate_limit=0.0`, `max_response_size` set (below). `max_retries=0` both makes the preview control
    real and stops wafer retrying a 5xx or an empty 200 **three times inside one of our bucket
    tokens** (`_async.py:1866-1889`) — four origin hits per token, the semaphore held ~7 s, and
    `Retry-After` never consulted on the 5xx path, so our `cooldown_until` would only ever see the
    *fourth* failure.
  - **CDN session** — **a cookie is never injected**; the per-session jar gives the uncredentialed
    fetch for free. `max_rotations=0` here too, or a CDN 403/challenge *raises* `ChallengeDetected`
    instead of returning and the "classify from the response, not an exception" rule (§6) breaks on
    this session. `rate_limit=0.0`, own bucket (burst 10, 0.25 s), **`max_retries=0`** — `1` would
    reintroduce what the API session sets `0` to avoid (wafer retrying a 5xx inside one of our tokens
    without consulting `Retry-After`); curves are idempotent, so a retry is ours to make with a fresh
    token, `max_response_size ≈ 1 MB` (**not 256 KB**: that was sized from TV's ~74 KB curves,
    and speaker's 361 KB "Raw Frequency Response Graph" then came back `fetch_failed`).
  - **`max_response_size` on the API session is a real number, not "set it".** `test_results` "scales
    with rows" (`RECON.md` §1), so cap `original_ids[]` per request and set the ceiling to ~8 MB.
    `ResponseTooLarge` is an exception, not a status — map it to `fetch_failed` and shrink the
    request.
- **Rate limiting is a token bucket we own, not wafer's interval.** Read from wafer's source:
  `wafer/_ratelimit.py` is a fixed-interval per-hostname sleeper (`min_interval + uniform(0, jitter)`,
  a plain dict) — **no bucket, no lock, no concurrency control**, and `_async.py` waits *before* the
  attempt but records *after* the response. It cannot express a burst. A flat serial interval is also
  the wrong shape for a cache-first server: in steady state it never fires, and on cold start it is
  pure cost — first `rt_ratings("tv")` is ~5 requests ≈ 8 s of sleep, and the 28-silo smoke test
  (§10) is ~140 requests ≈ 5 minutes. Set `rate_limit=0.0` on both sessions and own ~40 lines:
  **capacity 5, refill 1 token / 2.0 s, with jitter** — identical sustained politeness to the old
  flat 2.0 s, without the cold-start penalty. Acquire the token **before** computing the
  per-request `timeout=`, or the wait eats the attempt budget.
- **`asyncio.Semaphore` for concurrency — wafer has none.** Its limiter records after the response
  and holds no lock, so two concurrent tool calls both read "no last request" and both fire at t=0.
  Single-flight dedupes only *identical* keys. `RTINGS_CONCURRENCY`, default `1` through Phase 0.
- **IMPORTANT: honour `Retry-After` ourselves — wafer will not, under our settings.** `_async.py`
  sleeps `max(retry_after, rotation_floor)` **only on the rotation path**, and §6 sets
  `max_rotations=0`, under which a 429 is *returned immediately without sleeping*. Combined with
  "retries happen in wafer and nowhere else", the server would hit a limit, ignore the server telling
  it how long to wait, and fire again one bucket token later — the one behaviour that turns a soft
  limit into a block. So keep a per-host `cooldown_until`: on a **429**, a **503 carrying
  `Retry-After`**, or a `resp.challenge_type`, set `min(max(resp.retry_after, 30 s), cap)` —
  **the cap bounds `Retry-After` itself, not only the doubling**, or a `Retry-After: 3600` freezes the
  server for an hour (wafer clamps to the request deadline; we have no such backstop).
  `resp.retry_after` is a public wafer property. Double on consecutive events to a ~10 min cap, clear
  on the next `200`. A bare 503 with no `Retry-After` is `fetch_failed` and installs **no** cooldown —
  we were not told to wait. During cooldown serve cache or return a structured error **without
  touching the network**. Persist it — **with the doubling counter**, or a second process restarts the
  ladder at 30 s — under the cache dir so another MCP process respects it (§8).
- **The floor is politeness to origin — not camouflage.** An earlier draft justified `2.0 s` as
  looking browser-like. It does not: CloudFront and openresty rate rules count requests per window and
  do not score cadence, a metronomic interval is the most machine-like timing there is, and our
  request sequence (bare API POSTs, no assets) is non-browser regardless. wafer already handles TLS
  and header coherence. `robots.txt` states **no `Crawl-delay`** and no rate-limit headers are served
  (`RECON.md` §1, measured 2026-09-03). So there is no stated ceiling being respected — tune the sustained rate for politeness and drop the camouflage
  claim from §11.
- **The bucket is per process; the cooldown is cross-process.** Two MCP clients each get their own
  burst of 5, so the real ceiling is `N × burst`. That is acceptable (N is 1–3 in practice, and the
  cache keeps steady-state volume near zero) but it must be stated rather than discovered — and it is
  why the *cooldown*, which is the safety-critical half, is shared on disk (§8).
- **Never pass wafer's `cache_dir=`.** It persists solver cookies to disk, which is a credential-shaped
  artifact this project does not want written anywhere (§6).
- **Measure passively; never induce a limit.** Append one line per response to
  `telemetry/requests.jsonl`: `ts, host, query, status, elapsed, limiter_wait, resp.retries,
  resp.rotations, resp.challenge_type` plus, where present, `retry-after`, `x-ratelimit-*`,
  `ratelimit-*`, `x-cache`, `age`, `x-amz-cf-pop`, `via`, `server`, `cache-control`. **Never bodies,
  never cookies, never `set-cookie`.** The 28-silo smoke test is the natural first sample, and
  `x-cache`/`age` on the **HTML probe GET** also answers Phase 0 capture (f) — whether a member cookie
  is served from CloudFront's anonymous cache. This is how `RECON.md` §10 q6 gets answered from real use.
- **One shared `AsyncSession` per host family, process-wide** (`SyncSession` is not thread-safe); set
  `max_response_size`; single-flight on the fetch key so two calls never pull the same payload twice.
- **Send the real browser headers on every `/api/v2/safe/` call, unconditionally** — as **per-request**
  headers: `Origin: https://www.rtings.com`, a matching `Referer`, `Sec-Fetch-Site: same-origin`,
  `Sec-Fetch-Mode: cors`, `Sec-Fetch-Dest: empty`. The cookie is the sole credential (no CSRF/nonce,
  JS-confirmed `RECON.md` §5), but the one unprovable Phase-0 risk is server-side
  `Origin`/`Referer`/`Sec-Fetch-*` validation. Cheap, and it removes the residual failure mode.
- **Do NOT set `sec-ch-ua*` or `User-Agent` by hand, and never pass constructor `headers=`.** wafer's
  emulation ships a self-consistent header envelope; a constructor `headers=` **replaces** it entirely
  and hand-written client hints contradict the emulated fingerprint — which *creates* the detection
  risk the other headers exist to remove. Per-request `headers=` merges, which is why the five above
  are safe that way. (An earlier draft called for setting the `sec-ch-ua*` trio; it was wrong.)
- **Never send privileged *values*** — `is_admin:true`, `named_version:"admin"`. This is about the
  value, not the key: `is_admin:false` is part of the ordinary public body the logged-out front end
  sends (`RECON.md` §1), so **send `is_admin:false`**; omitting the key produces a body the real
  client never sends.
- **Request shapes are per-query — do not generalize by prefix.** `table_tool__*` take
  `{"variables":{…}}`; `app/product_vue_page__page_body` takes `{"variables":{…}, share_token,
  url_path}`; **`app/search__search_results` takes a bare `{count, is_admin, query, type}` with no
  `variables` wrapper** (`RECON.md` §1). `named_version` is always `"public"`.
- **Never log or return a response body** — a WAF challenge page carries tokens and a member page
  carries profile data. Status, `reason` and `<title>` only.
- **The official `mcp` SDK (`mcp>=2.1.1`), NOT the standalone `fastmcp` package.** They are now
  separate, diverged projects — `mcp` is the canonical SDK from modelcontextprotocol.io, `fastmcp`
  (4.x, gofastmcp.com) is a third-party layer with more ergonomics and its own release cadence. For a
  public server whose value is protocol correctness, take the canonical one and the smaller dependency
  surface.
- **IMPORTANT: the class is `MCPServer`, not `FastMCP` (corrected 2026-09-04).**
  `from mcp.server.fastmcp import FastMCP` raises `ModuleNotFoundError` on `mcp==2.1.1` — 2.x
  renamed it: `from mcp.server.mcpserver import MCPServer`. Tool schema attributes are
  snake_case (`tool.input_schema` / `tool.output_schema`). `outputSchema` is derived from the
  tool function's **return annotation**, which is why every tool returns a declared Pydantic
  model: it is what carries the seven-state `status` enum into the client's schema, where an
  agent can see the distinction before it ever makes a call.
- **Pin floors, not exact versions** (this is a library-style app, and `uv.lock` handles
  reproducibility): `mcp>=2.1.1`, `wafer-py`, and dev extras `pytest>=9.1`, `pytest-asyncio>=1.4`,
  `ruff>=0.16`. Versions verified current 2026-09-03; re-check at the release re-scan (CLAUDE.md >
  Release).
- Logs to **stderr** (stdout is the protocol channel). No browser automation in v1.
  Cache-first: network only on a miss or explicit `refresh`.
- Registered by absolute path to the user's own checkout, e.g.
  `sh -lc uv --directory /path/to/rtings-mcp run rtings-mcp`.

### Config surface

| Variable | Default | Purpose |
|---|---|---|
| `RTINGS_SESSION_COOKIE` | unset | session cookie; precedence over the stored file (§6, provisional on q3) |
| `RTINGS_CACHE_DIR` | `~/.cache/rtings-mcp` | cache location |
| `RTINGS_CACHE_TTL_DAYS` | `30` | TTL for the measurement surfaces; the routing surfaces have their own, shorter (§8) |
| `RTINGS_CACHE_MAX_MB` | `1024` | LRU eviction ceiling; never evicts a preview-bought review (§8) |
| `RTINGS_RATE_INTERVAL_S` | `2.0` | token-bucket refill: seconds per token, **not** requests per second |
| `RTINGS_RATE_BURST` | `5` | bucket capacity — what absorbs cold start |
| `RTINGS_CONCURRENCY` | `1` | in-flight requests to `www.rtings.com` |
| `RTINGS_MIN_REQUEST_INTERVAL_S` | unset | **deprecated alias.** If set, pins `RTINGS_RATE_INTERVAL_S` and forces `RTINGS_RATE_BURST=1` — the old flat-interval behaviour, exactly |
| `RTINGS_ENABLE_GRAPH` | `true` | serve curve data; set `false` to disable `rt_graph` entirely (§3, §12) |
| `RTINGS_GRAPH_MAX_POINTS` | `200` | default curve resampling target |
| `RTINGS_MAX_PREVIEW_SPEND` | `1` | distinct products `rt_product` may fetch per process on a `free` session; `0` forbids spending entirely (§7) |
| `RTINGS_MEMBER_MODE` | `true` | **was the Phase-0 gate**, opened 2026-09-06 (`RECON.md` §13.1). On, the demand/write tier rules in §6/§8 take effect. Off, every tier-keyed write is `anonymous` and no read demands a tier — and a signed-in session's rows are then served but refused the cache, because they cannot honestly carry an `anonymous` label |
| `RTINGS_TELEMETRY` | `true` | append header-only request records to `telemetry/requests.jsonl` (§9) |
| `RTINGS_CDN_RATE_INTERVAL_S` / `RTINGS_CDN_RATE_BURST` | `0.25` / `10` | the CDN session's own bucket |

## 10. Build order

**Phase 0 does not come first — it runs in parallel, on top of Foundation.** It needs the wafer
session module and the real browser headers (§9), and the project bans raw `requests`/`httpx`, so it
cannot precede Foundation. More importantly, **nothing on the anonymous surface depends on it**:
silos, schema, catalog, search, graph, recommendations, the seven-state normalizer and the untiered
cache are all fully specified from measured facts. Gating them behind a membership purchase would
idle ~80% of the work for no reason.

**Foundation — DONE (2026-09-04).** skeleton (`pyproject.toml`, `uv`, `LICENSE`, `.gitignore`); wafer session module
with paired timeouts, per-request browser headers, the two-session split, the token bucket +
semaphore + `cooldown_until`, and the single-credential rules (§6, §9); the JSON-file cache per §8
**including coverage staleness and the `cache_tier` filename segment from day one** (value
`anonymous`), so enabling member mode is never a migration; `GLOBALS.static.silos` ingest powering `rt_silos`, and the
per-silo `is_recent` bench probe feeding `bench/{silo}.json` (§7, §8).

**Parsing — DONE (2026-09-04).** `column_options` schema parser (test typing, hierarchy, `insider_only`); the
**seven-state** row normalizer, branching `status` before `unblurred`, for **both** row shapes (table
`value`, review `rendered_value`); the two-source auth join (§6); **`scores_available` derived per
(silo, bench)** (§7), never hardcoded.

**Anonymous tools — DONE (2026-09-04).** the seven per §7, the local catalog-filter engine (with
the gated-field filter/sort guard) and response shaping. All seven verified against the live
API; the corrections the build forced are in `RECON.md` §12 and marked **(corrected)** above.

**Phase 0 — DONE 2026-09-06 (`RECON.md` §13).** q1 answered YES on the first attempt, plus
captures a, c, d, f, g and p; only the metered-preview questions remain and they need a **free**
account, not a member one. The prediction below held exactly. A live RTINGS-posted **gift link**
had confirmed 2026-09-03 (`RECON.md` §5)
that the safe API *does* return unblurred `value`/`score` when the server grants access — on **both**
`table_tool__test_results` (clean `value`) and `product_vue_page__page_body` (`rendered_value` +
`score`) — and that the unblurred row shapes match the design. So the API mechanism and the row
shapes are **already measured**; the residual is only that a **member cookie** unblurs at
**all-products** scope (a share token is per-product). It is **one logged-in session**, so capture
everything in that session; a missed item costs another membership month. Run one
`table_tool__test_results` call and confirm the cookie fills `value`/`score` across products. If it
comes back blurred, the ladder is: (a) add any missing browser headers; (b) try
`app/product_vue_page__page_body` with `url_path` (the **bare path**, no `?share_token=` — `RECON.md`
§5); only then (c) record q1 as failed. That is not a ship/no-ship call — the anonymous surface,
including full data on 16 silos, ships either way; only the member layer is affected.

**The capture list — rewritten 2026-09-04, and HALF OF IT IS FREE.**

The single biggest saving: **a free RTINGS account costs nothing, and it answers four of the
captures.** Create one, capture everything below marked FREE, *then* upgrade the same account
and capture the member half. Doing it the other way round spends a membership month on
questions a free login already answers.

**Before spending anything — already done, do not re-capture:**

| # | Question | Status |
|---|---|---|
| f | Is the HTML probe served from CloudFront's anonymous cache? | **Answered, favourable.** `/tv/tools/table` returns `Cache-Control: max-age=0, private, must-revalidate` and `X-Cache: Miss from cloudfront`. CloudFront does not cache a `private` response, so the demotion guard should never fire. **CONFIRMED with the cookie 2026-09-06** (`RECON.md` §13.4): still `X-Cache: Miss from cloudfront`, still `private`. Kept as a guard, not designed around |
| h | Unblurred row shapes | Done via the gift link (§5) |
| — | The member/free signal's *location* | Done: `var TRACKING_PROPS = {…}` carries `user_type` and `membership_type`, and a sibling literal carries `userIsInsider` — all plain, unescaped JS. `rtings-mcp auth --status` prints them |

**Stage 1 — FREE ACCOUNT (costs nothing):**

| # | Capture | How |
|---|---|---|
| a1 | The **free** shape of every auth signal | `rtings-mcp auth --status`. It prints `session`, `access_level`/`preview_level`, `access_limit`, `has_insider_access`, and `user_is_insider`/`membership_type`/`user_type` verbatim. Anonymously these read `1`/`2`, `null`, `false`, `false`, `"no plan"`, `"Visitor"` |
| c | ~~The exact `test_results` body a logged-in front end sends~~ **ANSWERED BY CONSTRUCTION 2026-09-06** | The body this project already sends returned fully unblurred member data (`RECON.md` §13.1), so there is nothing to adjust for the member case |
| o | Is `app/side_by_side__review` metered? **NEEDS A FREE ACCOUNT** | Check `previewed_products` before and after one `rt_product(include_verdicts=true)`. It is the public **compare** tool, not the review page, so it is believed unmetered — reasoning, not measurement, and `rt_product` degrades to verdicts-only when the budget is spent, which assumes this. **A membership cannot settle it**: a member has no meter at all (`access_limit: null`, `previewed_products: []` — `RECON.md` §13.2) |
| i | The metered preview's unit | `rt_product(consume_preview=true)` on one review, then `auth --status` again; compare `previewed_products`/`access_limit`. Repeat on a second review the next day to separate per-session from per-day |
| e | ~~Does the session slide?~~ **ANSWERED anonymously (`RECON.md` §12.15)** | It slides on every response with a fresh 30-day expiry, so a session lives indefinitely with use. Rotation write-back is implemented and gated on a logged-in probe. Only confirm the same holds when logged in, and that the browser stays signed in while the server uses the same blob |

**Stage 2 — MEMBERSHIP (the month you are paying for). Do q1 first; everything else works
whatever it says.**

| # | Capture | Why it gates code |
|---|---|---|
| q1 | One `table_tool__test_results` on a gated silo | **ANSWERED 2026-09-06 (`RECON.md` §13.1): 588/588 unblurred with values, against 0/588 anonymous.** It succeeded on the first rung — no header ladder, no `page_body` fallback — and `RTINGS_MEMBER_MODE` now defaults to `true` |
| a2 | ~~The **member** shape of the same signals~~ **ANSWERED 2026-09-06 (`RECON.md` §13.2)** | `current_user.is_insider: true` is the member/free field, and it is now the classifier's primary signal — `user_is_insider`, `has_insider_access` and `access_level 3 > preview_level 2` all agreed and remain as corroboration. `RTINGS_SESSION_OVERRIDE=member` stays as the escape hatch for the one-sided residual (a logged-in session with no positive signal still reads `free`) |
| j | `table_tool__ratings` with the cookie | Usage ratings on the gated 12 are `rt_ratings`' default path and 100% blurred anonymously — the most-used surface, and completely unmeasured for members |
| k | `products_list` with the cookie, TV recent benches | **Does it return 127 rather than 118?** TV's `reviews_in_progress_count` is 9 and exactly 9 product ids return results while appearing in no catalog (§12.2, §12.10). If a member sees them, `catalog/` becomes tier-dependent — and it is currently **untiered** |
| l | One `/early-access/` review with the cookie | RTINGS says Insiders see Early Access data (§12.10). Confirms `review_unpublished` is lifted by a membership, which the normalizer now assumes |
| m | One **open** silo (mattress) with the cookie | "A membership adds nothing on the 16" is asserted everywhere and measured nowhere. Check `table_tool__ratings` there too |
| p | ~~Does `user_has_access` flip for a member on a gated silo?~~ **YES, 2026-09-06 (`RECON.md` §13.5)** | `true` on TV with a member cookie, against `false` on TV / `true` on mattress anonymously — so it tracks membership *and* enforcement, and it is a second, independent confirmation of q1. `verdicts/` still deliberately keys on the usage scores rather than this flag: the permanent-miss deadlock that motivated the choice is retired, but keying on the scores depends on nothing about what the flag means |
| n | One **legacy** bench with the cookie | `RECON.md` §10 q16 — everything measured so far is current-bench only |
| d | ~~The same request **with and without** the browser headers~~ **ANSWERED 2026-09-06 (`RECON.md` §13.3)** | 98/98 unblurred **both ways** via `api_post(..., browser_headers=False)`. There is no server-side origin validation on `/api/v2/safe/`; the one "unprovable" risk is measured and absent. The headers stay because they cost nothing |
| g | ~~One curve with the cookie, diffed against the anonymous copy~~ **IDENTICAL, 2026-09-06 (`RECON.md` §13.6)** | Same CDN path, byte-identical JSON. `graphs/` stays untiered and the CDN still needs no cookie |

**Stage 3 — WHEN THE MONTH LAPSES (free, and easy to forget):** run `rtings-mcp auth --status`
once more. That is the *expired* shape, and it is the only way to see it without paying twice.

**Script it before you start.** A missed item costs a month. Two rules while capturing:

- **Use a scratch `RTINGS_CACHE_DIR`.** Running the tools by hand against the real cache with
  `RTINGS_MEMBER_MODE=0` *used* to write unblurred member data into `anonymous`-tier files, which
  never-downgrade and the eviction exemption then preserved indefinitely. The anonymous-label
  guard (§8) now refuses those writes instead — but a scratch dir is still the right habit for
  capture work, since it keeps experimental fetches out of the cache the server serves from.
- **`rtings-mcp scan` already refuses to use a configured cookie** — it clears the credential
  and says so — so the release baseline cannot be poisoned mid-capture.

**Member layer — BUILT AND ON (2026-09-06).** Tier-aware row selection, `member`/`free`
classification and write-time demotion were implemented and unit-tested while the gate was shut,
and Phase 0 then confirmed a cookie flips `unblurred` on the API (`RECON.md` §13.1), so
`RTINGS_MEMBER_MODE` now defaults to `true`. That is what "the tier ships from day one carrying
`anonymous`" bought: turning member mode on was a flag, never a migration.

Setting it to `0` still pins every tier to `anonymous`, and that path now has a cost worth knowing
— the anonymous-label guard (§8) refuses to cache rows it cannot honestly label, so a signed-in
user serves and re-fetches on every call. The flag remains the way to run a credential without
tiering the cache at all.

**Rotation write-back IS implemented** — this paragraph previously said the opposite, and was
written before §12.15 measured the session sliding. RTINGS re-issues `_rtings_session` on every
response with `expires = now + 30 days`, so the window is a sliding *idle* one: refusing to
persist the rotation froze the stored blob at the pasted value and expired it 30 days later
however much the server was used, causing the monthly re-paste the refusal was meant to prevent.

The destruction risk it guarded against is real and is handled by a **proof gate, not by
refusing to write**: RTINGS re-mints `_rtings_session` on any anonymous GET, so the rotated
value is persisted only when the HTML probe proves that same response was logged in
(`current_user` non-null — something an anonymous re-mint can never satisfy), and only for a
file-sourced credential (`RTINGS_SESSION_COOKIE` cannot be refreshed, and is warned about once).

The write is also a **compare-and-swap** (added 2026-09-05). Two processes sharing a config dir —
Claude Code, an IDE and a CLI is the documented case — each load the credential once, at startup, so
a process holding a stale baseline would otherwise overwrite a rotation another wrote seconds ago.
`CredentialState.stored_at` carries the baseline and `store_credential(..., not_newer_than=...)`
declines to overwrite a newer record, keeping its own value in memory instead. A user-driven paste
passes no baseline and always wins: it is the newest fact by definition.

**Tests — DONE (2026-09-07): 436 offline, 9 live.** `pytest` runs offline by default; live
tests are opt-in (`-m live`) and anonymous by construction. Fixtures are synthetic or from
**anonymous fetches only, never containing unblurred member values** (CLAUDE.md), and the one
`GLOBALS.session` fixture with a logged-in shape has every value replaced by a placeholder.
The originally planned coverage: Both envelope shapes, the auth-state matrix (member/free/anonymous/expired) built from
**redacted** `GLOBALS.session` fixtures (keep the shape, replace every value — `current_user` carries
the user's name and email), mixed-tier row selection, coverage-based `not_tested`, and an
**anonymous 28-silo smoke test**: `column_options` + default `products_list` + one `test_results` +
one graph per silo. "All 28, all-or-nothing" (§2) is only honest if all 28 are actually exercised.

**The smoke test doubles as the release-time enforcement re-scan.** It recomputes the per-silo
paywall map from observed `unblurred` and diffs it against `docs/enforcement-snapshot.json` (baseline
`scanned_at` 2026-09-04, re-verified 2026-09-06 with no diff: 12 enforcing / 16 open). **A diff is
a spec change, not a test failure** — enforcement
tracks category maturity and moves silently, so a stale map makes the server misdescribe what
anonymous gets, which is the product's central claim. It also re-checks the invariants the normalizer
rests on: blur is still exactly `published:false ∨ (insider_only ∧ enforcing)`, gating within a silo
is still per-product never per-test, `status` is still `{tested, na, untested}`, and usage defs still
carry no `insider_only`. See CLAUDE.md > Release.

## 11. Risks

| Risk | Mitigation |
|---|---|
| **Member cookie does not flip the blur on the API** (`RECON.md` §10 q1) | Phase 0 verifies before member-mode is built. A failure costs the numeric surface on the 12 enforcing silos only — the other 16 are full-data anonymously (§5) |
| **Auth marker (HTML) and data (JSON) come from different requests** and can race | `session` from an HTML probe, `data_tier` from the data in the safe direction, `auth_state` derived, race documented (§6) — never infer auth from null data |
| API query renamed / `variables` shape changed | `payload_missing`/`api_error` are loud (§7); raw JSON cached, so a parser fix needs no re-fetch |
| Session lapses; wafer rotation empties the jar → anonymous `200` | `max_rotations=0, max_failures=None`; compare the jar cookie against the **configured** value before `session_expired`; `identity_rotated` never cached (§6) |
| **RTINGS re-mints `_rtings_session` anonymously → rotation write-back overwrites the user's credential** | write back only when the rotating response is proven logged-in (`current_user` non-null); presence checks are not identity checks (§6) |
| **`rt_product` silently consumes the user's metered previews** | `max_retries=0` on `page_body`; surface `previewed_products`/`access_limit` in `auth`; warn when `session == free` (§7) |
| **A free account's preview is mistaken for membership** | `data_tier` is `unblurred`/`unproven`, never `member`; membership comes from the HTML probe alone (§6) |
| **`status:"na"` reported as gated** — "buy a membership" for an inapplicable test | seven-state normalizer, `status` branched before `unblurred` (§5) |
| **Cached `not_tested` invented for never-fetched pairs** | the response file *is* the coverage record; an absent row outside covered scope is a miss, never an answer (§8) |
| **Cached `not_tested` invented for a product newer than the coverage** | coverage carries `catalog_generation` + `fetched_at`; a product outside that generation is `coverage_stale`, never `not_tested` (§8) |
| **Tier hit-rule deadlock → refetch forever** | `cache_tier` (probe, stored) is separated from `data_tier` (data, derived); the hit rule is probe-vs-probe (§6, §8) |
| **429 ignored because wafer only sleeps `Retry-After` on the rotation path** | `max_rotations=0` means wafer *returns* the 429; we own a per-host `cooldown_until` and split `rate_limited` from `challenged` (§7, §9) |
| **Two MCP clients double-spend a metered preview** | cross-process advisory lock around miss→fetch→write; preview budget checked before the POST (§7, §8) |
| 30-day session is a re-paste burden | persist `Set-Cookie` rotations; optional Playwright `login` extra if proven painful (§6) |
| RTINGS objects to the volume or the graph surface | not a rights question — the curve JSON is served unauthenticated to anyone — but the controls are real and cheap: local-only cache, on-demand per `(product,test)` with no bulk parameter, rate-limited, and `RTINGS_ENABLE_GRAPH=false` kills `rt_graph` outright (§3). "No redistribution" is a **licence term we state, not a control we enforce** — do not count it as one |
| **RTINGS gates the currently-open 16 categories** | possible, and entirely their call — the finding is a property of what their public API returns, not a defect being exploited. The server derives enforcement per fetch and never hardcodes it, so it degrades to the gated path automatically (§5, §7) |
| **Automated access breaches the member's own ToS → account termination** | the risk lands on the *user's* account, not ours. Cache-first, a 1-token-per-2s sustained bucket, no bulk crawl, and `auth` documents that automation is at the member's discretion (`RECON.md` §4 note) |
| Cross-bench comparison corrupts an answer | `test_bench` scopes comparison; nested never flattened; default = the site's own `is_recent` set (§7) |
| Narrow parameterized fetch mistaken for complete | responses stored whole and keyed by request; serve supersets of **coverage**, not of rows (§8) |
| Anonymous review-page metering walls `rt_product` even logged out | `current-pageviews`/`page-history` cookies exist anonymously (`RECON.md` §5); treat a metered block as `fetch_failed`, never as "not tested" |
| WAF under member/volume traffic | untested (`RECON.md` §10 q6); wafer fingerprinting + a 2.0 s sustained refill + adaptive `cooldown_until`; cache-first keeps steady-state volume near zero. Answered from `telemetry/requests.jsonl` during real use, never by inducing a limit (§9) |
| **`reviews/` cache growth unbounded** | gzip + `RTINGS_CACHE_MAX_MB` LRU, exempting only preview-bought (`free`) reviews and the newest unblurred file (§8) |
| **A silo flips open → gated as it matures** | the 12/16 split is re-derived per fetch, never hardcoded; never-downgrade keeps the values already cached, and `scores_available` reports the change honestly (§5, §8) |
| **`published:false` reported as "buy a membership for this category"** | it yields `review_unpublished` — an Early Access review, a different reason and a different remedy. Checked *after* `unblurred`, so a member's Early Access values are kept (§5, §7, `RECON.md` §12.10) |
| Cross-silo claims rest on a small sample | the enforcement map now covers all 28 (`RECON.md` §11.1), but only each silo's **current bench** and its first 40–50 leaf tests; legacy benches, the review path and non-leaf kinds are unmeasured (`RECON.md` §10 q16). The 28-silo smoke test in §10 is what keeps "all 28" honest |
| **The enforcement map goes stale silently** | it is a dated snapshot re-scanned before every release and diffed against `docs/enforcement-snapshot.json`; `scores_available` is computed from the freshest response, never from the merged set, so an open→gated flip is visible immediately (§7, CLAUDE.md > Release) |

## 12. Decisions taken

- **Name: `rtings-mcp`**, matching the directory. Published on PyPI under that name as v0.2.1
  (2026-09-07); there is no npm package and none is planned.
- **Auth cookie-only in v1 (§6).** HttpOnly `_rtings_session` makes cURL the *only* capture gesture —
  no console fallback, unlike CR. Never a password/login/CAPTCHA.
- **Auth is a two-source join (§6)** because the API carries no marker (`RECON.md` §1). New vs CR;
  the most important structural decision in the auth design.
- **Serve graph/curve data anonymously — decided, not accidental (§3, §5).** The one place paywalled
  measurement data reaches a non-member; carried with concrete controls, and `rt_graph` never reduces
  a curve to a scalar.
- **`test_bench` is the comparison scope**, defaulting to the site's own `is_recent` set (derived per
  silo, never hardcoded); cross-bench nested, never comparable (§7, §8). Conservative pending
  `RECON.md` §10 q7.
- **The normalizer emits seven states, not three (§5).** `not_applicable` is a distinct state because
  `status:"na"` is otherwise indistinguishable from gated — added 2026-09-03 after it was measured.
- **Storage is plain JSON files, not SQLite (§8).** Reversed 2026-09-03. The SQLite design was
  ported from CR, where forced category dumps had to be shredded into rows — and shredding is
  precisely what destroys the record of what was requested, which is why a separate `fetch_coverage`
  table had to exist. RTINGS' API is parameterized with no product filter, so the response *is* the
  coverage record and the file path *is* the key. Nothing else needed SQL: search is API-backed,
  catalog filtering is in-memory over 111 KB, and cross-bench joins are forbidden by §7.
- **The cache stores coverage, not just rows (§8)** — and coverage carries a `catalog_generation`,
  because a product newer than the coverage would otherwise be reported `not_tested`.
- **`cache_tier` and `data_tier` are different fields answering different questions (§6, §8).**
  Added 2026-09-03. Storing a data-derived tier and requiring `data_tier ≥ configured` deadlocks
  permanently on public tests, free accounts and all-`na` slices.
- **Rate limiting is a token bucket we own, and `Retry-After` is honoured by us (§9).** Reversed
  2026-09-03. wafer's limiter is a flat per-host interval with no burst, no lock and no concurrency
  control, and under `max_rotations=0` it returns a 429 without sleeping. The flat `2.0 s` floor was
  also justified as camouflage, which is wrong — cadence is not what rate rules score.
- **The anonymous surface does not wait on Phase 0 (§10).** Only member-tier selection does.
- **Seven tools (§7)**, folding `rt_products`/`rt_results` into `rt_ratings`+`rt_product`; keeping
  `rt_graph` and the isolated page-extracted `rt_recommendations`.
- **The paywall model was corrected 2026-09-03 (§5, `RECON.md` §11).** Blur = `published:false` OR
  (`insider_only` AND the silo enforces). Enforcement is per-silo and binary — 12 of 28 enforce.
  `insider_only` marks a test gate-*able*, never gated; `has_paywall` is `true` on all 28 and is
  useless. The 12/16 split is **never hardcoded** — it tracks category maturity and will change.
- **`rt_silos()` is a routing tool, not a listing tool (§7).** It reports observed
  `data_completeness` per silo so an agent knows *before* calling whether numeric questions are
  answerable there.
- **Public and open source.** No macOS/Keychain/single-browser assumptions.
- **All 28 silos, one envelope.** No cars analogue.

## 13. Open questions

1. ~~If Phase 0 fails, does the server still ship?~~ **Not planned for.** 16 of 28 silos are
   full-data anonymously (`RECON.md` §11); a Phase-0 failure costs the numeric surface on 12 silos,
   not the product.
2. **Does a member cookie flip `unblurred`?** `RECON.md` §10 q1. **Blocking** for member mode only.
3. **Is a free/metered tier worth modelling?** Not optional any more: the meter sits on the
   `rt_product` endpoint (`RECON.md` §10 q2), so free-vs-member must be distinguished or a preview
   gets read as membership (§6). What remains open is the *unit* — per product, per session, per day.
4. **Does the 30-day session slide, and does a pasted value survive a server-side re-mint?**
   (`RECON.md` §10 q3) Decides the env-var path and whether Playwright is ever needed.
5. **Tool-surface sign-off** — seven (§7). Reopens once q2 is settled.
6. **Which field separates `member` from `free` in a logged-in `GLOBALS.session`?** Unmeasured — only
   the anonymous shape has been seen. The auth classifier cannot be written without it (§10 capture a).
7. ~~Do usage definitions carry `insider_only`?~~ **RESOLVED 2026-09-03** — they do **not** (checked
   tv/headphones/monitor/mouse; only *test* defs carry it). So `scores_available.usage_ratings`
   derives from observed `unblurred` on the ratings response, never from a schema flag.
8. **Minor-bench comparability** (`RECON.md` §8, §10 q7) — decides the default comparable population.
9. ~~How is a silo's set of best-of lists discovered?~~ **RESOLVED 2026-09-03** — the silo landing
   page (`/{silo}`) enumerates them as `/{silo}/reviews/best/<slug>` links (15 on TV: `by-size`,
   `by-usage`, `budget`, `ps5`, …). `rt_recommendations(silo)` extracts those hrefs; `list` selects
   one. Page-extraction, consistent with `rt_recommendations` being the one such path (§7).

*Resolved and removed:* per-silo paywall boundary (now per (silo, bench), `RECON.md` §2); the
"not tested" row shape (both halves + a fourth `na` state, `RECON.md` §6); the per-silo default bench
set (`is_recent`, `RECON.md` §8); usage `insider_only` (q7); best-of list discovery (q9).
