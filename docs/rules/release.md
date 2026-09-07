# Release gate — re-scan before every version bump

> Moved verbatim out of `CLAUDE.md` on 2026-09-07 so the always-loaded file stays short.
> These are the project's hard-won rules: each one was forced by a measurement or a defect,
> and the date/`RECON.md` reference beside it says which. Read the file for the area you are
> touching before changing it; add new rules here, one bullet each, with the date and the
> evidence.

**IMPORTANT: the paywall map is a snapshot of RTINGS' business decisions, not a fact about the API.
It WILL change, and it changes silently.** The 12/16 enforcement split (`RECON.md` §11.1) correlates
with category maturity, so a silo that serves full measurements today gets gated as it grows. Nothing
in the API announces this — the schema flag `insider_only` does not move, `has_paywall` is `true` on
all 28 regardless, and the only signal is the served `unblurred` value changing. **A stale map means
the server tells users a silo is fully answerable when it is now paywalled, or hides a silo that has
since opened up.**

So, as a release gate, before every version bump:

1. **Re-run the anonymous 28-silo scan** — the enforcement half of the smoke test (`SPEC.md` §10):
   per silo, `column_options` + `products_list` + one `test_results` over the current bench's leaf
   tests, anonymous, no cookie.
2. **Diff against `docs/enforcement-snapshot.json`** (the committed baseline: `scanned_at`
   2026-09-04, re-verified 2026-09-06 with no diff — 12 enforcing / 16 open).
3. **A diff is a SPEC CHANGE, not a test failure.** Do not "fix" the test to match. Update the
   snapshot, `RECON.md` §11.1, and the framing in `SPEC.md` §5 / this file's preamble, and say so in
   the release notes — the honest description of what anonymous gets is the product's main claim.
   **Also update the gated-category list inside `mcp.instructions` in `server.py`** — it is prose
   read by the calling LLM as the authoritative routing signal, it has no runtime fallback, and it
   was missing from this checklist. `test_the_instructions_gated_list_matches_the_enforcement_snapshot`
   fails if you forget. Same for the category tables in `README.md`.
   **Do not change `config.KNOWN_SILOS` for this** — that hint is about which silos *exist*, not
   which gate, and a silo outside it already warns `silo_hint_drift` rather than failing.
4. **Re-check dependency currency** — `mcp`, `wafer-py`, `ruff`, `pytest`, `pytest-asyncio`,
   `playwright` (the `[browser]` extra), and the Python floor against what is actually current. Floors were set 2026-09-03; a floor that has
   drifted two majors is a bug waiting to surface.
5. **Re-check the invariants too, not just the split** — they are what the normalizer is built on:
   - blur is still exactly `published:false ∨ (insider_only ∧ silo enforces)` — no third mechanism;
   - gating within a silo is still **per-product, never per-test**;
   - `status` domain is still `{tested, na, untested}`;
   - usage definitions still carry no `insider_only`.
   - **which best-of template each silo serves** (`RECON.md` §12.17) — mattress and
     running-shoes are server-rendered, the rest are `RecommendationVuePage`, and RTINGS is
     migrating. This drifts silently exactly like the paywall map: the symptom is
     `recommendations_missing`, and the fix is a parser, never a "that silo has no lists".
     One `rt_recommendations(silo, list=<first>)` per silo is the check.
6. **Never let the runtime read the snapshot.** It is a release-time diff baseline and documentation
   only. The server derives the boundary from observed `unblurred` per (silo, bench) on every fetch
   (`SPEC.md` §5) — a hardcoded map is exactly the bug this rule exists to catch.

The same applies to anything else in `RECON.md` measured once: it is dated, and a re-scan is cheap
compared to shipping a confident lie.
