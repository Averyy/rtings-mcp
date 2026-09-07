# Member scenarios — proving every tool with the real membership

A checklist of realistic shopper questions, run against the **real, signed-in membership**, that
together touch every tool and every honesty rule the server makes. Anonymous scenarios were run
40 times across all 28 categories during the 2026-09-06 shopper round; this is the signed-in
half, and it is the acceptance test for "the tools work for a paying member".

## Ground rules

- **The credential is the real one** at `~/.config/rtings-mcp/session.json` (mode `0600`). It
  rotates on use and the rotation is written back, so **use the real config dir** — a copy
  would freeze the cookie and expire in 30 days. Never print, log, or paste it; never commit a
  fixture captured from these runs (they carry unblurred member values).
- **Use a scratch cache** so runs start cold and the real cache is not polluted:
  `RTINGS_CACHE_DIR=/tmp/rtings-member-scenarios` (delete between rounds). Add
  `RTINGS_RATE_BURST=2 RTINGS_RATE_INTERVAL_S=3` when several agents run at once.
- **Never call `rt_sign_in`** — the session is already live. `rt_auth_status()` is the check.
- Drive the tools over stdio with a fresh server per run (`scratchpad/rtcli.py call <tool>
  '<json>'` or the installed `rtings-mcp` as an MCP server in a client). An MCP server already
  running in a client is the build it started with — restart it first.
- One scenario per agent, graded **PASS / FAIL**. A scenario FAILS on wrong data, on a
  tool-caused dead end, on any of the invariants below, or on more than 12 tool calls.

## Invariants every signed-in response must satisfy

| Field | Expected for a member | Why it matters |
|---|---|---|
| `session` | `member` | probe read `current_user.is_insider: true` |
| `auth_state` | `member` | derived from `session`; never `preview` |
| `data_tier` | `unblurred` on any response that served an `insider_only` row on a gated silo; `unproven` only when the served rows had no insider test to prove it | derived from the rows served, not the slice fetched |
| `scores_available` | `available` for surfaces the call queried; `unknown` for the rest, never `gated` | a member sees everything |
| row `status` | never `tested_gated`; `review_unpublished` only for Early Access products (`published:false`) — and a member **should** see those values, so expect `tested_visible` there too | the seven states |
| `previews_remaining` | `null` | a member has no meter |
| `warnings` | no `not_cached`, no `demoted`, no `refresh_failed` on a cold run | write guard and demotion must stay silent for a genuine member |
| cache files | `tests/<silo>/*.member.*.json`, `ratings/...member...`, `reviews/...member...`, `recs/<silo>/<list>.member.*` after the call | tier-keyed writes happen at the probe tier |
| credential file | mtime advances during the round, mode stays `0600`, still classifies `member` afterwards | rotation write-back |

## Scenarios

Categories are chosen so that the gated 12 (where membership changes the answer) dominate,
with two metered ones as controls (where a member and anonymous should agree byte for byte).

### S1 — "Best 65-inch TV for a bright living room under the current bench" (tv, gated)
Tools: `rt_silos` → `rt_schema(find="peak brightness")` → `rt_ratings(silo="tv", tests=[SDR
peak, HDR peak], filters={"variant": "65"}, sort=-<peak>, limit=10)`.
Pass: real brightness values with units (cd/m²), ranked descending, `data_tier: unblurred`,
`sorted_by.gated: false`, `tested_variant` shows 65".

### S2 — "Is the LG C5 better than the Samsung S95H for gaming?" (tv, head-to-head)
Tools: `rt_search` for both → `rt_ratings(filters={"product_ids": [...]}, usages=["Video
Games"], tests=[input lag 1080p@120, VRR])` → `rt_product(url, include_verdicts=true)` on one.
Pass: both rows present, usage scores populated, verdict prose returned, no `tested_gated`.

### S3 — "Headphones with the flattest treble for mixing" (headphones, gated, repeated names)
Tools: `rt_schema(find="RMS deviation")` → must be told the three qualified names →
`rt_ratings(tests=["Treble/RMS Deviation From Target"], sort=+..., limit=10)`.
Pass: the treble band (not bass) is ranked; a bare `"RMS Deviation From Target"` is refused with
the qualified forms listed.

### S4 — "27-inch 1440p monitor, at least 144 Hz, best response time" (monitor, gated)
Tools: `rt_ratings(filters={"Size": "26..28", "Native Resolution": "1440", "Max Refresh
Rate": ">=144"}, sort=+<response time>)`.
Pass: `Size` resolves to the numeric test (not the variant alias), the word filter matches
"2560 x 1440" as text, response time is a real number in ms.

### S5 — "Lightest wireless mouse with a good sensor" (mouse, gated, two recent benches)
Tools: `rt_ratings(tests=["Default Weight", "Click Latency"], filters={"Connectivity": "Wireless"},
sort=+"Default Weight")`.
Pass: results nested by bench (2 groups), no cross-bench merge, weight in grams, latency in ms.

### S6 — "Which printer is cheapest per page for black text?" (printer, gated)
Tools: `rt_schema(find="cost per print")` → `rt_ratings(sort=+<black cost>)`.
Pass: the cost test is found (it is not named "cost per page"), values are populated; the
"no prices" rule is *not* triggered because this is a measured test, not a retail price.

### S7 — "Quietest robot vacuum that still picks up pet hair" (robot-vacuum, gated)
Tools: `rt_schema(find="noise")`, `rt_schema(find="pet hair")` → `rt_ratings(tests=[noise], usages=["Pet Hair"], sort=+noise)`.
Pass: the usage and the test are distinguished (no id collision error where none exists),
noise in dB, usage score 0–10.

### S8 — "Show me the peak-brightness curve and the number for the Sony Bravia 9" (tv, graph + value)
Tools: `rt_search` → `rt_product(url, tests=[HDR peak brightness])` → `rt_graph(product, test)`.
Pass: the scalar is a real value (member), the curve is the same as anonymous (RECON §13.6),
`axis_bounds_of_served_points` labelled, ≤ ~200 points unless `full=true`.

### S9 — "What does RTINGS recommend for a soundbar under a 55-inch TV, and why?" (soundbar, gated recs)
Tools: `rt_recommendations(silo="soundbar")` → pick a list → `rt_recommendations(silo, list=…, include_reasoning=true)`.
Pass: picks carry `featured_results` with **real scores** (the recs cache is tier-keyed since
2026-09-06); `recs/soundbar/<list>.member.*` exists; the anonymous copy is not served.

### S10 — "Best laptop between 13 and 14 inches for battery life" (laptop, gated, ranges)
Tools: `rt_ratings(filters={"Size": "13..14"}, tests=["Battery Life Web Browsing"], sort=-...)`.
Pass: one call, the range works, battery life in hours (input unit), no unit mislabel.

### S11 — "An Early Access review" (any gated silo with `published:false` products)
Tools: `rt_ratings(silo="tv", limit=25)` → find a `review_unpublished` row anonymously would
show; as a member → `rt_product(url)` on it.
Pass: the member sees real values (Early Access is an Insider perk, RECON §12.10), status
`tested_visible`, the URL's second path segment is used as the silo.

### S12 — Control: "Firmest mattress for side sleepers" (mattress, metered)
Tools: `rt_ratings(silo="mattress", tests=["Firmness"], usages=["Side Sleepers"], sort=-Firmness)`.
Pass: identical values to an anonymous run of the same call; `data_tier: unblurred` both times;
the member write is **refused** with a `not_cached` warning **only if** the anonymous
observation is missing — with a cold scratch cache expect the write to go through as `member`
(no anonymous proof yet) and no warning.

### S13 — Control: "Camera with the best autofocus for video" (camera, metered, second template)
Tools: `rt_recommendations(silo="camera")` → `rt_ratings(usages=["Video"], limit=5)`.
Pass: the best-of page parses (whichever template), usage scores populated.

### S14 — Session health, before and after
Tools: `rt_auth_status()` first and last.
Pass: `session: member` both times, `credential.source: file`, the stored cookie's mtime
advanced, mode `0600`, and `~/.config/rtings-mcp/session.json` is the only file written there.

### S15 — Cache reuse across processes
Run S1 twice in two fresh processes against the same scratch cache.
Pass: the second run reports `from_cache: true`, the same `fetched_at`, no network fetch of
`tests/` (the telemetry log shows only the probe), and still `session: member`.

## Running it

```bash
export RTINGS_CACHE_DIR=/tmp/rtings-member-scenarios RTINGS_RATE_BURST=2 RTINGS_RATE_INTERVAL_S=3
rm -rf "$RTINGS_CACHE_DIR"
.venv/bin/rtings-mcp auth --status            # must print session: member before starting
# one agent per scenario, e.g.
python scratchpad/rtcli.py call rt_auth_status '{}'
python scratchpad/rtcli.py call rt_ratings '{"silo":"tv","tests":["141"],"filters":{"variant":"65"},"sort":"-141","limit":10}'
ls "$RTINGS_CACHE_DIR"/tests/tv/                 # expect *.member.*.json
.venv/bin/rtings-mcp auth --status            # still member; file mtime advanced
```

Grade each scenario PASS/FAIL with the call count, the invariant table above, and the first
wrong thing seen. A FAIL is a bug: fix it, add the offline test, re-run the scenario, and only
then move on. Record the round in `TODO.md` the way the anonymous round is recorded.
