# rtings-mcp

MCP server exposing an RTINGS member's own subscription as structured test-data tools. Public,
on PyPI as `rtings-mcp` (`uvx rtings-mcp`), so a fix reaches nobody until it is released.
Python `>=3.12`, developed on 3.14, `uv` only.

**IMPORTANT: the official `mcp` SDK 2.x, not the `fastmcp` package.** The class is
`from mcp.server.mcpserver import MCPServer`; `mcp.server.fastmcp` does not exist on 2.1.1.
Tool schema attributes are snake_case, and `outputSchema` comes from the tool's **return
annotation** — every tool returns a declared Pydantic model for that reason.

## Where things are written down

- `SPEC.md` — design of record. Read before proposing anything structural.
- `RECON.md` — measured facts about RTINGS' API, paywall and auth. Cite it; never re-derive it.
  §1–§12 anonymous, §13 the member session (2026-09-06), **§14 the anonymous preview meter
  (2026-09-07)**.
- `docs/rules/` — the full rule ledger, one dated bullet per measurement or defect. **Read the
  file for the area before editing it**, and add new rules there, not here:
  - `paywall-and-normalizer.md` — the gate, the seven states, row shapes, benches, schema,
    units, values (`normalize.py`, `schema.py`, `envelope.py`)
  - `auth-and-session.md` — probe, tiers, cookie, rotation, sign-in, member mode, the write
    guard, observation provenance (`auth.py`, `htmlprobe.py`, `browser_auth.py`, `observations.py`)
  - `cache.md` — keys, tiers, coverage, TTLs, pruning, locks (`cache.py`, `repository.py`)
  - `tools-and-responses.md` — filters, sort, `find`, budgets, recommendations, legends
    (`services.py`, `server.py`, `models.py`)
  - `http.md` — wafer, rate limiting, headers, request shapes (`http.py`, `api.py`)
  - `testing.md` and `release.md`
- `TODO.md` — open questions and the handoff. Facts go in `RECON.md`, not there.

## Status

Built, working, signed in, published (v0.3.1). Member mode is on by default; a member cookie
unblurs the API (`RECON.md` §13.1). **The paywall model was corrected 2026-09-07 (`RECON.md`
§14):** the 16 silos the docs called "open" give an anonymous session a **three-product review
preview budget**, and while it is unspent the table tool serves everything. The budget is
spent only by fetching a product review page as **HTML**, which this server never does, so the
"full" it observes is real for its own session — describe it as metered, never as open. **A
free account is anonymous with a username** (§14.6): no budget on the gated 12, the same
meter on the 16; `rt_product`'s preview gate arms only on a reported `access_limit`.

## Non-negotiables

Each of these has a longer entry, with the evidence, in `docs/rules/`.

- **An agent must never read "no member session" as "RTINGS did not test this."** The normalizer
  emits seven states (`tested_visible`, `tested_gated`, `not_applicable`, `not_tested`,
  `review_unpublished`, `coverage_unknown`, `unknown_row_status`) and never collapses two into
  one null. Branch on `status` before `unblurred`; check `unblurred` before `published`.
- **The gate is `insider_only` AND per-silo enforcement, derived from OBSERVED `unblurred` per
  (silo, bench)** — never from the flag, never from a hardcoded map, never from the snapshot.
- **Never derive, estimate, interpolate or summarise a gated value.** Curves are served as
  shipped, resampled by selecting points only.
- **Never engineer around the preview meter.** No review-HTML fetch path (it spends the budget
  and the fourth GET blurs the whole silo for the process); never strip, reset or persist the
  `product-previews` cookie; never send `unblur_product_ids`/`force_blur` beyond what the
  logged-out front end sends.
- **Never store credentials, automate a login, or touch a CAPTCHA.** One user-supplied cookie;
  the browser sign-in opens RTINGS' own login page and the human types. Never log or return a
  response body or a cookie.
- **`session` comes from the HTML probe (`GLOBALS.session.current_user`, `is_insider` read
  strictly); `data_tier` from the data; `auth_state` is derived.** Never infer auth from null
  data or from a cookie's presence, and never claim `preview`.
- **`cache_tier` (probe tier, in the filename) and `data_tier` (derived, never stored) are
  different fields; the hit rule is probe-vs-probe; demotion is write-time and per surface.**
  A write that would be labelled `anonymous` but could not have been fetched anonymously is
  refused, not relabelled. Only an anonymous-provenance observation may prove a silo open.
- **Rotation write-back only from a response proven logged-in.** RTINGS mints and re-issues
  `_rtings_session` anonymously on every response; the session slides.
- **Rank and compare within a `test_bench`; paginate within a group.** The recent-bench set
  comes from `is_recent` on the page, the current bench is the newest rendered bench with a
  published schema — `latest_test_bench_id` is not it.
- **Coverage has a time dimension.** `not_tested` only inside a fetched, fresh slice whose
  catalog generation contained the product on that bench; otherwise `coverage_unknown`.
- **A field a filter or sort names must be fetched; a gated or absent field is "not applied"
  and the envelope says which of the three reasons.** Uncomparable rows sort last both ways.
- **Coerce by declared `kind`; label with `number_input_unit`; "Inf" is a value; a clock unit
  means seconds.** A repeated test name is an error, never "the first one".
- **Bound every response on the wire** (`RTINGS_MAX_RESPONSE_CHARS`, indent=2 form). Rows
  carry answers, legends carry definitions, nothing repeats a response constant per row.
- **A failed refresh serves the cache with `stale:true`; the envelope reports the data's age.**
  Negative results are files; transport failures are never cached.
- **wafer only, two sessions, own token bucket, `max_retries=0`, `max_rotations=0`.** Classify
  `challenged` / `rate_limited` / `fetch_failed` from the response; honour `Retry-After`
  yourself; never pass constructor `headers=` or `cache_dir=`.
- **`errors[]` beside `data` is a partial-field notice, not a failure.** `payload_missing` means
  the shape changed; an empty 200 is `fetch_failed`.
- **The 28 silos are a description hint, never a JSON-Schema `enum`.** Validate against live
  `static.silos` only.
- **A tool description is cut at 2048 characters by the client (measured 2026-09-08).** Order it
  by what a caller cannot work without — vocabulary before rationale. A test asserts the
  essentials are inside the window.
- **Two page-extraction paths now, and both are shape-guarded against the meter.** A best-of
  brand page is tried only for a single-segment slug; `rt_article` accepts a fixed
  `learn|tests` branch alternation and nothing else. Neither can name
  `/{silo}/reviews/{brand}/{model}` (`RECON.md` §14.7, §14.8). A `/tests/` page's prose may
  live entirely in `introduction` with `text` empty, and the two branches never share a cache
  key.

## Development

```bash
uv venv && uv pip install -e ".[dev]"
uv pip install -e ".[browser]"              # only for the browser sign-in (`rt_sign_in`)
.venv/bin/pytest tests/ -q                 # offline (the default; no network)
.venv/bin/pytest -m live -q                # live, anonymous, against the real API
.venv/bin/pytest -m "live and slow" -q -s  # + the 28-silo enforcement re-scan (~4 min)
.venv/bin/ruff check src/ tests/           # lint (fix with --fix)
.venv/bin/rtings-mcp scan --out docs/enforcement-snapshot.json   # release gate 1: the paywall map
.venv/bin/rtings-mcp drift --out docs/recommendation-template-snapshot.json  # gate 2: best-of extraction
```

- Run lint and the offline tests before every commit — with the real exit code, never through
  `| tail`.
- `StubTransport` bypasses the real transport; `tests/test_http.py` is where `_perform`, the
  cooldown and the `errors[]` rule are actually exercised. Mutation-test safety branches.
- Fixtures come from anonymous fetches; redact every `GLOBALS.session` fixture; never commit
  unblurred member values.
- MCP tools in a running client connect to the server it started with — restart after edits;
  test inline with `.venv/bin/python -c "..."` first. Experiments use a scratch
  `RTINGS_CACHE_DIR`, never the real cache or credential.

## Release gate — before every version bump

The paywall map is a snapshot of RTINGS' business decisions and changes silently. Details and
the invariant list are in `docs/rules/release.md`.

1. Re-run the anonymous 28-silo scan and diff against `docs/enforcement-snapshot.json`, and
   `rtings-mcp drift` against `docs/recommendation-template-snapshot.json`.
2. A diff is a **spec change**: update the snapshot, `RECON.md` §11.1/§14, `SPEC.md` §5, the
   gated list in `server.py`'s `instructions`, and the `README.md` tables. Never
   `config.KNOWN_SILOS`, and never let the runtime read the snapshot.
3. Re-check the invariants (blur rule, per-product gating, `status` domain, best-of templates)
   and dependency floors (`mcp`, `wafer-py`, `ruff`, `pytest`, `pytest-asyncio`, `playwright`).

## Git

- **NEVER commit without explicit permission.**
- **NEVER add Claude attribution.**
- Bump `__version__` in `src/rtings_mcp/__init__.py` (patch by default; ask before minor/major),
  run the release gate first.
- Publishing is a GitHub release, never a manual upload: bump, commit, push, then
  `gh release create vX.Y.Z --generate-notes`. `publish.yml` refuses a tag that does not match
  `__version__` and publishes through PyPI trusted publishing (environment `pypi`).
- **IMPORTANT: after every push, release or workflow dispatch, WATCH the run to completion and
  verify the deploy** — `gh run watch <id> --exit-status`, then for a release confirm PyPI
  lists the version (`curl -s https://pypi.org/pypi/rtings-mcp/json | jq .info.version`) and
  that `uvx rtings-mcp --help` works from a fresh `UV_CACHE_DIR`. Never dispatch a workflow
  you have not verified locally first (action tags exist, lint and tests pass): every failed
  run emails the owner.
