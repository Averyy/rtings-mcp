# HTTP rules

> Moved verbatim out of `CLAUDE.md` on 2026-09-07 so the always-loaded file stays short.
> These are the project's hard-won rules: each one was forced by a measurement or a defect,
> and the date/`RECON.md` reference beside it says which. Read the file for the area you are
> touching before changing it; add new rules here, one bullet each, with the date and the
> evidence.

- **ALWAYS use wafer** (`~/code/wafer`, see its `llms.txt`) — never `urllib`, `requests` or `httpx`.
  The `/api/v2/safe/` endpoints are clean JSON but sit behind CloudFront and carry repeated,
  authenticated member traffic.
- **ALWAYS pair `timeout=` with `attempt_timeout=`.** `timeout` is a TOTAL budget across retries.
  Unpaired, one hanging request eats the budget and retries never fire.
- **Rate limiting is a TOKEN BUCKET we own, not wafer's interval.** `wafer/_ratelimit.py` is a flat
  per-hostname sleeper — no burst, no lock, no concurrency control — and it cannot express a burst.
  Set `rate_limit=0.0` on both sessions and own the bucket: **capacity `RTINGS_RATE_BURST=5`, refill
  one token per `RTINGS_RATE_INTERVAL_S=2.0`s, with jitter.** Same sustained politeness as the old
  flat 2.0 s. **The burst buys interactive latency, not throughput** — a cold `rt_ratings` drops from
  ~8 s of sleep to ~0, but the 28-silo smoke test is ~270 s at burst 5 vs ~280 s flat, since sustained
  rate is the refill. Do not claim otherwise. `RTINGS_RATE_INTERVAL_S` is **seconds per token, not requests per
  second** — an "rps" value inverts the ceiling. Acquire the token **before** computing the
  per-request `timeout=`, or the wait eats the attempt budget.
- **IMPORTANT: honour `Retry-After` yourself — wafer will not under our settings.** It sleeps
  `max(retry_after, rotation_floor)` **only on the rotation path**, and we set `max_rotations=0`,
  under which a 429 is *returned immediately without sleeping*. Keep a per-host `cooldown_until`:
  on 429/503 or a `resp.challenge_type`, `max(resp.retry_after, 30s)`, doubling to a ~10 min cap,
  cleared on the next 200; persist it under the cache dir so a second process respects it. Split
  **`rate_limited`** (retryable, carries `retry_after`) from **`challenged`** (not) — collapsing them
  tells the agent "no" when the answer is "in 30 seconds".
- **Concurrency needs an `asyncio.Semaphore`** (`RTINGS_CONCURRENCY`, default 1). wafer's limiter
  records *after* the response and holds no lock, so two concurrent calls both fire at t=0.
  Single-flight dedupes only identical keys.
- **TWO sessions: `www.rtings.com` (credentialed) and `i.rtings.com` (never).** One session forces the
  origin's floor onto 2–361 KB static files and offers `_rtings_session` to the CDN for nothing
  (confirmed: `add_cookie` with an explicit `Domain` sets `host_only=False`, `wafer/_base.py:2542`).
  API session: `max_retries=0, max_rotations=0, max_failures=None` — `max_retries=0` also stops wafer
  retrying a 5xx or empty 200 **three times inside one of our bucket tokens** while never consulting
  `Retry-After` on that path. CDN session: **`max_rotations=0` here too** (otherwise a CDN
  403/challenge *raises* instead of returning and the "classify from the response" rule breaks on that
  session), own bucket (burst 10, 0.25 s), **`max_retries=0`** (not `1` — that reintroduces exactly
  what `max_retries=0` exists to prevent: wafer retrying a 5xx inside one of our tokens without
  consulting `Retry-After`), `max_response_size ≈ 1 MB` (**not 256 KB** — that was sized from
  TV's ~74 KB curves and made speaker's 361 KB one unfetchable; see the payload rule above).
- **Never pass wafer's `cache_dir=`** — it persists solver cookies to disk, a credential-shaped
  artifact this project writes nowhere.
- **Status precedence, first match wins:** `challenged` (any `resp.challenge_type`) → `rate_limited`
  (429, or 503 **carrying** `Retry-After`) → `fetch_failed` (any other non-200, transport error,
  `ResponseTooLarge`, or empty 200). A 503 with no `Retry-After` is `fetch_failed`.
- **Never hold two locks.** `cooldown_until` is cross-process like the key locks; read/write it
  lock-free via atomic replace and release anything it takes before acquiring a key lock, or two
  processes deadlock. `fcntl.flock` blocks the event loop — run it in `asyncio.to_thread`.
  **Qualified 2026-09-05:** the surface key locks (`tests`/`ratings`/`review`/`verdicts`) DO nest —
  they hold their lock across `catalog()`, `schema()` and `session_probe()`, each of which takes its
  own. That is deadlock-free only because those three are always **leaves** (they take no further
  cross-process lock) and always the **inner** lock. Nothing enforces that ordering and no test
  covers it, so adding a lock to any of those three, or acquiring `catalog`-before-`tests` anywhere,
  introduces a real cross-process deadlock.
- **The floor is politeness to origin, NOT camouflage.** `robots.txt` has no `Crawl-delay` and
  disallows only `/user_reviews/` and `/admin/`; no `X-RateLimit-*`/`Retry-After` observed. Rate
  rules count requests per window, not cadence — a metronomic interval is the *most* machine-like
  timing there is. Answer `RECON.md` §10 q6 from `telemetry/requests.jsonl` during real use (headers
  only — never bodies, never cookies), **never by inducing a limit**.
- **One shared `AsyncSession` per host family, process-wide** (`SyncSession` is not thread-safe). Set
  `max_response_size`. Single-flight on the fetch key so two calls never pull the same payload twice.
- **Send the real browser headers on every `/api/v2/safe/` call** — per-request `Origin`, `Referer`,
  `Sec-Fetch-Site: same-origin`, `Sec-Fetch-Mode: cors`, `Sec-Fetch-Dest: empty`. The cookie is the
  sole credential (no CSRF, JS-confirmed `RECON.md` §5); headers remove the one unprovable Phase-0
  risk (server-side origin validation).
- **Do NOT hand-set `sec-ch-ua*` or `User-Agent`, and never pass constructor `headers=`.** wafer's
  emulation applies a self-consistent header envelope; a constructor `headers=` **replaces** it
  wholesale and hand-written client hints contradict the emulated fingerprint — that *creates* the
  detection risk the browser headers are meant to remove. Per-request `headers=` merges, which is why
  the four above are safe to set that way.
- **Never send privileged *values*** — `is_admin:true`, `named_version:"admin"`. Note this is about
  the value, not the key: `is_admin:false` is part of the ordinary public body the logged-out front
  end sends on `products_list`, `test_results` and `search` (`RECON.md` §1), so **send
  `is_admin:false`**. Omitting the key produces a body the real client never sends.
- **Request shapes are per-query — do not generalize by prefix.** `table_tool__*` take
  `{"variables":{…}}`; `app/product_vue_page__page_body` takes
  `{"variables":{…}, share_token, url_path}`; **`app/search__search_results` takes a bare
  `{count, is_admin, query, type}` with NO `variables` wrapper** (`RECON.md` §1, re-confirmed
  2026-09-03). `named_version` is always `"public"`.
- **IMPORTANT: `errors[]` beside `data` is a PARTIAL-FIELD notice, not a failure.** RTINGS
  strips admin-only fields (`edit_url`, `methodology_url`, `review_notes_url`) and reports it
  in `errors[]` while returning a complete payload. Raise `api_error` only when `errors[]`
  comes back with **no `data`**. A genuinely bad request does not use `errors[]` at all — an
  unknown silo returns `{"data": {"silo": null}}`, which is `payload_missing`.
- **Never log or return a response body** — challenge pages carry tokens, member pages carry profile
  data. Status, `reason` and `<title>` only.
- Cache-first. Hit the network only on a miss or an explicit `refresh`.
