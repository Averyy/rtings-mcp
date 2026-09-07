# Testing and fixture rules

> Moved verbatim out of `CLAUDE.md` on 2026-09-07 so the always-loaded file stays short.
> These are the project's hard-won rules: each one was forced by a measurement or a defect,
> and the date/`RECON.md` reference beside it says which. Read the file for the area you are
> touching before changing it; add new rules here, one bullet each, with the date and the
> evidence.

- **Pin dependency FLOORS, not exact versions** — `uv.lock` provides reproducibility. Verified
  current 2026-09-03: `mcp>=2.1.1`, `pytest>=9.1`, `pytest-asyncio>=1.4`, `ruff>=0.16`, and
  `playwright>=1.40` in the optional `[browser]` extra (added 2026-09-06). Re-check at
  each release re-scan (below).
- **Fixtures come from anonymous fetches. Never commit a fixture containing unblurred member values.**
- **Redact every `GLOBALS.session` fixture before committing.** The member/free auth-state fixtures
  are captured from a real logged-in session, so `current_user` carries the user's own name, email
  and subscription details. Keep the **shape** (which fields are present, and `current_user`
  non-null), replace every value with a placeholder, and never commit the cookie.
- **IMPORTANT: `StubTransport` overrides `api_post`/`api_get_html`/`cdn_get_json` WITHOUT calling
  `super()`, so the real `Transport._perform`/`_classify` and the `errors[]` rule are dead code under
  the offline suite.** Mutation-tested 2026-09-04: deleting the challenge-precedence branch, and
  making any `errors[]` fatal, each left the whole suite green. **`tests/test_http.py` is where the
  shipped transport is actually exercised** — it injects a fake wafer session at `_api_session` so
  `_perform`, the cooldown and the JSON handling all run. A contract the stub *mirrors* is not a
  contract the suite *tests*; if you change transport behaviour, the test belongs there.
- **Mutation-test the safety-critical branches rather than trusting the count.** The three gaps found
  this way (status precedence, `errors[]`, `should_demote`'s free/reviews branch) were all invisible
  to line coverage — the lines ran, nothing asserted on them.
- **MCP tools in Claude Code connect to the installed server, not your working tree.** Edits need an
  MCP restart; test inline with `.venv/bin/python -c "..."` first.
