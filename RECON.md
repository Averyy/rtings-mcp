# RECON — measured facts about RTINGS

What we know about RTINGS' API, paywall and auth, and **how we know it**. `SPEC.md` decides what
to build; this file is the evidence those decisions rest on.

Everything here is measured, not inferred. Where a claim is inferred or unverified it says so, and
it lives in §10, never in the body as if it were fact.

- **Recon pass 1** (anonymous) — 2026-09-02
- **Recon pass 2** (anonymous) — 2026-09-03 — bench-default derivation (§8), the review-path row
  shape and the `status:"na"` fourth state (§6), per-bench public-test boundary (§2)

Reference silo throughout: **TVs** (`url_part: "tv"`, silo id `1`), 548 tested products, 402 tests,
11 usages on the current bench (v2.2).

> **Anonymous only.** Every measurement here was taken with no account. This file draws the paywall
> boundary in **one direction** — what a logged-out caller sees. What a member sees is unmeasured;
> the whole member side is §10 q1, and a membership will be bought to settle it before member-mode
> code is written.

---

## 1. The data source — a keyless JSON API

RTINGS is a **Rails** backend with a **Vue** front end. Data comes from a JSON API, not from a blob
embedded in the page (that is the Consumer Reports shape, and RTINGS is not it):

```
POST https://www.rtings.com/api/v2/safe/<query_name>
content-type: application/json
{"variables": { ... }}
```

**Confirmed anonymous:** a plain `curl` with only a browser `User-Agent` returns `200` on every
query below. No api-key, no CSRF token, no cookie. `Server: openresty`, fronted by CloudFront
(`X-Cache: … from cloudfront`). No bot challenge and no rate limit was hit across the ~40 requests
this recon made — **volume behaviour is untested** (§10 q6). `robots.txt` disallows only
`/user_reviews/` and `/admin/`.

**No stated rate ceiling exists (measured 2026-09-03).** `https://www.rtings.com/robots.txt` carries
two `Sitemap:` lines and `User-Agent: *` with those two `Disallow:` rules — and **no `Crawl-delay`**.
A response carries `Server: openresty/1.21.4.3`, `Via: … cloudfront`, `X-Cache: Miss from cloudfront`
and **no `X-RateLimit-*`, no `RateLimit-*`, no `Retry-After`**. So any request interval this project
picks is a politeness choice of ours, not a published limit being honoured; `SPEC.md` §9 sizes the
token bucket on that basis and answers §10 q6 passively from response headers during real use rather
than by inducing a limit.

**The API carries no auth field.** Every JSON response above was searched for `session`,
`current_user`, `access_state`, `access_level`, `subscriber`, `auth` — **none appear** in any of
`products_list`, `ratings`, `test_results`, `column_options` or the review body. The only auth-ish
token anywhere is `insider_only` on *test definitions* (a schema flag, §6), not a caller state.
**This is load-bearing:** the caller's tier is not in the data response, so it must be read either
from the data itself (an `unblurred:true` value proves **the row was unblurred for us**, never that
the caller is a member — a gift link or metered preview does it too) or from a separate HTML probe
(§5). `SPEC.md` §6 resolves the seam.

### Confirmed queries and request shapes (2026-09-02)

| Query (`POST /api/v2/safe/…`) | Body shape tested | Returns | Bytes (TV) |
|---|---|---|---|
| `table_tool__column_options` | `{"variables":{silo_url_part,named_version:"public"}}` | the silo **schema** | 357 KB |
| `table_tool__products_list` | `{"variables":{test_bench_ids[],named_version,is_admin}}` | product **catalog** | 111 KB |
| `table_tool__test_results` | `{"variables":{test_bench_ids[],original_ids[],named_version,is_admin,force_blur,unblur_product_ids[]}}` | scalar **measurements** | scales with rows |
| `table_tool__ratings` | `{"variables":{test_bench_ids[],original_ids[],…}}` | 0–10 **usage scores** | 63 KB |
| `graph_tool__product_graph_data_url` | **form** `named_version=…&product_id=…&test_original_id=…` **and** `{"variables":{…}}` | a **curve JSON** CDN url | tiny |
| `app/search__search_results` | `{count,is_admin,query,type:"full"}` (no `variables` wrapper) | cross-silo **search** | small |
| `app/product_vue_page__page_body` | `{"variables":{url,named_version,version_id},share_token,url_path}` | one full **review** | 442 KB |

The request shape is **per-query and confirmed only where noted** — `graph_tool__…` accepted both
form and JSON; `search__…` takes a bare object with no `variables` wrapper; the others take
`{"variables":{…}}`. Do not assume a shape that was not tested. `named_version:"public"` was the
only value used; whether others exist is unknown (§10).

**`is_admin` is part of the ordinary public body, not a privileged flag.** It appears in the
confirmed `products_list`, `test_results` and `search` bodies as `false` — that is what the logged-out
front end sends, and it is what we send. The thing never to send is a **privileged value**
(`is_admin:true`, `named_version:"admin"`). Re-confirmed 2026-09-03: the bare `search` shape
`{"count":5,"is_admin":false,"query":"x90l","type":"full"}` returns `200` with `total_count:99`.
Note the distinction is *value*, not *key* — a rule phrased "never send `is_admin`" would produce a
body the front end never sends, which is the opposite of blending in.

**The API is parameterized.** You request exactly the tests/usages/products you want — there is no
forced full-category dump. `column_options` is the one fixed ~357 KB payload (the schema, fetched
once per silo).

### Discovery — inline `var GLOBALS`, no crawl

Every HTML page carries an inline `var GLOBALS = {session, static, ads}` (132 KB on the table page).
Two parts matter, both confirmed present:

- **`static.silos`** — all **28 silos** in one fetch: `{name, url_part, has_paywall,
  first_published_at, tool_pages[], …}`. The category index; no A-Z page exists.
- **`session`** — the auth state (§5): `current_user` (`null` anonymous), `access_state`.

The table and graph tool pages ship almost no data inline — they fetch it from the API after load —
so **the API is the source, not the HTML**, except that `GLOBALS` (discovery + auth) is HTML-only,
and the review page (`ProductVuePage`) additionally inlines its full 442 KB in a `data-props`
attribute.

### The 28 silos — every one paywalled

`static.silos[].has_paywall` is **`true` for all 28** (confirmed):

```
tv  headphones  monitor  soundbar  mouse  keyboard  printer  robot-vacuum  vacuum
dehumidifier  projector  toaster-oven  keyboard-switch  air-purifier  running-shoes
humidifier  refrigerator  mattress  air-conditioner  microwave  blender  air-fryer
toaster  vpn  router  speaker  camera  laptop
```

Same API shape across silos (schema confirmed on `tv`; other silos share the query set but their
payloads were not each fetched — §10 q5). RTINGS reviews no cars; no out-of-scope carve-out needed.

---

## 2. The paywall boundary — server-side, anonymous direction only

> **SUPERSEDED IN PART by §11 (2026-09-03).** This section measured TVs and generalized from a
> 3-silo sample. The 28-silo sweep found enforcement is **per-silo**: 16 of 28 serve their
> `insider_only` values anonymously. Read §11.1 first; the mechanics here (what `insider_only` means,
> how the blur is expressed) still hold.

The load-bearing measurement. Where CR gates two things and leaves a usable free tier, RTINGS gates
nearly every number. **Measured on TVs, anonymous, 2026-09-02.**

**`table_tool__test_results`** — 60 tests × 125 TVs = **7,500 rows**:

| | count |
|---|---|
| rows returned | 7,500 |
| `unblurred: true` | **0** |
| `unblurred: false` | **7,500** |
| non-null `value` | **0** |
| non-null `score` | **0** |

Every blurred row carries the **unit and a lock marker, never the number**:

```json
{"original_id":"11","score":null,"value":null,
 "rendered_value":"<span class=\"e-blurred\">Lock</span> cd/m²","unblurred":false,
 "status":"tested"}
```

`rendered_value` patterns: `…Lock</span> : 1`, `…Lock</span> cd/m²`, `…Lock</span>%`, `…Lock`,
`…Locked`. The trailing `: 1` is a display-precision hint (`number_display_precision`), not a
leaked value. **Nothing reconstructs the scalar — it is not in the bytes.**

**`table_tool__ratings`** (0–10 usage scores) — **348/348** `unblurred:false`, `score:null`.

**Review page** (`product_vue_page__page_body`, Samsung S90D) — 402 test results, **6 unblurred /
396 blurred**, matching the table.

### The 6 public tests (TVs) — pure spec fields

Of the TV bench's 402 tests, **6 are `insider_only:false`** and return real values anonymously:

| `original_id` | name | kind | example |
|---|---|---|---|
| 208 | Resolution | word | `4k` |
| 219 | Native Refresh Rate | word | `144Hz` |
| 53 | Screen Finish | word | `Glossy` |
| 217 | Panel Type | word | `OLED` |
| 216 | Sub-Type | word | `QD-OLED` |
| 517 | Dolby Vision | word | `No` |

All six are `kind:"word"` spec fields, not measurements. **396 of 402 tests, and 100% of the 0–10
usage scores, are gated.**

#### Public tests carry a real `score` anonymously (confirmed 2026-09-03)

Re-measured on the Sony X90L/X90CL (`product_id:39008`, bench 227) via the review body. The 6 public
rows return **both** a value and a non-null 0–10 `score`:

```
53  Screen Finish         word  "Glossy"  score 10.0
217 Panel Type            word  "LCD"     score 10.0
216 Sub-Type              word  "VA"      score 10.0
219 Native Refresh Rate   word  "120Hz"   score  9.5
208 Resolution            word  "4k"      score 10.0
517 Dolby Vision          word  "Yes"     score 10.0
```

So **"anonymous gets no 0–10 scores" is wrong as an absolute** — it is *`insider_only` tests* whose
scores are withheld. A public test ships its score. The 100%-gated figure above is for **usage**
scores (`table_tool__ratings`), which is a different surface. State the rule as *`insider_only`
gates the value and the score together*, never as "no scores anonymously."

#### The public set is per-(silo, **bench**), not per-silo (confirmed 2026-09-03)

The same silo gates differently on different benches. TVs:

| bench | tests | `insider_only:false` |
|---|---|---|
| 227 (v2.2, current) | 402 | **6** |
| 2 (v0.9, 2014) | 54 | **1** (208 Resolution only) |

Measured on the Sony W600B (`product_id:154`, bench 2): 53 of 54 rows `insider_only:true`. So the
"6 public TV tests" figure is a property of **bench 227**, not of the TV silo. `scores_available`
must be derived per **(silo, bench)** — deriving once per silo mislabels every legacy-bench product.

---

**The pattern generalizes across silos (confirmed 2026-09-02).** Pulled `column_options` for two
non-TV silos and counted `insider_only`: **headphones 8 public / 234 tests, mattress 4 public / 142
tests** — every public test a `word` spec field (headphones: Bass/Treble Amount, Sound Signature,
Type, Enclosure, Wireless, Noise Cancelling, Mic; mattress: Mattress Type, Bed-In-A-Box, Firmness
Level, Upper Comfort Foam). So each silo exposes a small, silo-specific set of spec `word` tests and
gates everything measured — which is exactly why `scores_available` is derived from `insider_only`
per **(silo, bench)**, never hardcoded to the TV list (`SPEC.md` §7). Those two counts were taken on
each silo's **current** bench; per the amendment below, a legacy bench in the same silo will differ.

### The blur is server-side

`value:null` and `score:null` in every gated row — withheld before the bytes leave the server, not
hidden with CSS. A real paywall.

**Confirmed in a real browser (Playwright, anonymous, 2026-09-03).** Loaded the Sony X90L review in
Chromium and inspected the live DOM, not just the API:

- **788** `.e-blurred` elements, each with computed style `filter: blur(5px)` — so a genuine CSS blur
  *is* applied. The question is what sits underneath it.
- Underneath is **placeholder text, not data.** Only **23 distinct strings** across all 788 elements:
  `Locked` (416), `Lock` (160), `0.0` (99), `Lock cd/m²` (46), `Lock%`, `Lock ms`, `Lock Hz`, `Lock°`,
  `Lock K`, `Lock dB`, `Lock : 1`, … The only non-placeholder strings that appear are `4k`, `120Hz`,
  `Glossy`, `LCD` — the **public** spec fields, which are supposed to show.
- **Force-removing every blur filter reveals `0.0`, not a real score.** Injected
  `.e-blurred{filter:none!important}`, screenshotted: all **61** `.score_box-value` nodes read `0.0`
  (`distinct_score_values: ["0.0"]`, `all_are_zero: true`). No real usage score is present.
- **No cached real value anywhere in the DOM** — no `[data-unblurred="true"]`, no `[data-real-value]`,
  no `::before`/`::after` pseudo-content, no `title` attribute holding a number. The measured scalars
  are simply absent from the delivered page.

So the CSS blur is cosmetic theatre over placeholders; the paywall is enforced server-side, exactly
as the API rows show. Stripping the blur client-side yields nothing — there is no number to reveal.

> **Correction (supersedes an earlier in-conversation claim).** An earlier pass said there is "no
> partial numeric signal to regress against, unlike CR." **That is false** — see §4: the *curves*
> the scalars are computed from are public. The raw signal is available; only RTINGS' scalar
> *summary* of it is gated. So the derivation temptation is larger here, not absent, and `SPEC.md`
> §5 states the rule positively: `rt_graph` returns a curve as shipped and never reduces it to a
> number that could read as an RTINGS measurement.

---

## 3. What anonymous does get — catalog, schema, search, prose

> **SUPERSEDED IN PART by §11 (2026-09-03).** "Thin on numbers" is true of the 12 enforcing silos
> only. See §11.2.

Thin on numbers, but enough to route and to describe. All confirmed anonymous:

- **`table_tool__products_list`** — full catalog. Per product: `id`, `fullname`, `full_url_part`,
  `url_part`, `brand_name`, `published`, `reviewed_sku_id`, `approximate_released_at`,
  `last_updated_at`, `review.test_bench.{id,display_name}`, `image`, `variant_skus[]`, `page`,
  `published_product_recommendation_count`, `status_discussion`. **No gated fields** — the catalog
  is entirely public. 116 products across the three recent TV benches.
- **`table_tool__column_options`** — the schema (§6), public in full including the definitions of
  gated tests. You can tell an agent exactly what RTINGS measures; only the numbers are withheld.
- **`app/search__search_results`** — `{count,is_admin,query,type:"full"}` → ranked hits across all
  silos. `"LG C4"` → `total_count:1749`, first hit `{title:"LG C4 OLED TV Review",
  url:"/tv/reviews/lg/c4-oled", product_id:"49541", kind:"page", highlighted_title, …}`. The only
  cross-silo surface RTINGS exposes.
- **`linked_description`** — on the review page, the per-test qualitative prose survives the blur.
  Words free, numbers not.
- **Recommendation pages** (§7) — ranked best-of lists with reasoning, anonymous.

---

## 4. The graph tool — measurements as curves, fully anonymous

The most important asymmetry. RTINGS ships a navbar **Graph Tool** (`/tv/graph`, `/headphones/graph`,
…). The scalar "Peak 100% Window: 🔒 cd/m²" is gated, but the **curve it is measured from is on an
open CDN with no auth**. Two steps, both confirmed anonymous 2026-09-02:

```
POST /api/v2/safe/graph_tool__product_graph_data_url
  named_version=public&product_id=49703&test_original_id=13907
→ {"data":{"product":{"review":{"test_results":[{"graph_data_url":"/assets/products/79xUyeNz/graph-pqeotf.json"}]}}}}

GET https://i.rtings.com/assets/products/79xUyeNz/graph-pqeotf.json
→ 200, {"header":[…], "data":[[x,y,…], …]}   # real curve, thousands of points
```

**Every graph tested returned data — 6 across 2 products.** For the Samsung S90D
(`product_id:49703`), all `kind:"graph"` tests:

| `original_id` | kind | graph | CDN bytes |
|---|---|---|---|
| 13907 | graph | PQ EOTF | 4,056 |
| 29485 | graph | Bright Room Black Level Raise | 2,535 |
| 29486 | graph | Bright Room Color Volume | 2,211 |
| 920 | graph | Frequency Response | 9,362 |
| 30855 | graph | Direct Reflections | 79,658 |

Plus a headphone (Dyson OnTrac, raw L-channel FR, `test_original_id:4011`): 94,112 bytes.

**Which tests have a graph is derivable from the schema: `kind == "graph"`.** All five TV graphs
pulled were `kind:"graph"` tests; the table `test_results` rows carry `graph_tool_url: null`
(0/7,500), so the graph URL comes only from `graph_tool__product_graph_data_url`. A `graphs/`
entry is keyed by `(product_id, test_original_id)`; a non-`graph` test has no graph (structural
`no_graph`, `SPEC.md` §7).

**Curves are public; whether they differ for members is unmeasured** (no reason they would — they
carry no `unblurred` flag — but it is not confirmed, §10). Output is large (79 KB ≈ 25k tokens; FR
94 KB on TV — but **361 KB on speaker and 335 KB on soundbar**, measured 2026-09-05; audio
curves are the large end and the CDN response cap must clear them), so `rt_graph` resamples
(`SPEC.md` §7). This is the seam AutoEq and others have used for
years (§9).

**Re-confirmed 2026-09-03, and the asymmetry is explicit in one payload.** The review body carries
`graph_data_url` inline on graph rows. On the Sony X90L, all 5 such rows are `unblurred:false` — yet
the CDN serves the curve to an anonymous `GET` at full precision:

```
row: {original_id:13907, name:"PQ EOTF Graph", kind:"graph", unblurred:false,
      graph_data_url:"/assets/products/zmn5qIi9/graph-pqeotf.json"}
GET https://i.rtings.com/assets/products/zmn5qIi9/graph-pqeotf.json → 200, 4,157 bytes
{"header":["Input Stimulus","PQ EOTF Target","600 nit mastering",…],
 "data":[[0,0,0.03973670413,…],[0.05,0.05,0.06439094777,…], …]}
```

So the gated scalar and its public curve sit **in the same response object**, one field apart. This
is the strongest form of the §2 correction, and exactly why `SPEC.md` §5 states the rule positively.

Two operational facts for `rt_graph`:

- **Graph coverage is sparse and bench-dependent.** X90L (bench 227): **5 of 402** rows carry a
  `graph_data_url`. W600B (bench 2): **0 of 54** — a legacy-bench product has no curves at all. So
  "this test has a graph" is a per-(product, bench) fact, not a schema-wide one, and `rt_graph` needs
  a first-class *no curve for this product* outcome, not just a non-`graph` `kind` check
  (`SPEC.md` §7).
- **The CDN needs no credential.** A plain `GET` with only a `User-Agent` returns `200`. The curve
  fetch must therefore go out on an **uncredentialed** path — `_rtings_session` is scoped
  `.rtings.com` and would otherwise be offered to `i.rtings.com` for no reason.

> **Design note, not a measurement.** Serving graph data means serving paywalled measurements to
> non-members. It is public via a sanctioned navbar tool with established precedent, but "public"
> ≠ "permitted for automated use." `SPEC.md` §3/§12 own this decision and carry the concrete
> controls (local-only cache, no redistribution, on-demand, rate-limited, disable-able).

---

## 5. Auth — one cookie, HTML-only markers

### The session cookie (confirmed)

A plain anonymous GET sets, among others:

| Cookie | HttpOnly | Domain | Expiry | Role |
|---|---|---|---|---|
| `_rtings_session` | **yes** | `.rtings.com` | **30 days** | the Rails session — the credential |
| `exp_user_id` | no | default | 1 year | tracking id only, not auth |
| `current-pageviews`, `page-history` | no | `.rtings.com` | 1 day | paywall metering counters |

`_rtings_session` is **HttpOnly**, so `document.cookie` cannot read it — capture is **"Copy as cURL"
only**, never a console one-liner. (Opposite of CR, whose durable cookie was not HttpOnly.)

### The auth markers — HTML `GLOBALS.session` only (confirmed)

Server-rendered inline in the HTML, seen by a plain GET with no JavaScript:

```
GLOBALS.session.current_user               null ⇒ anonymous; an object ⇒ logged in
GLOBALS.session.access_state.access_level  1 anonymous
GLOBALS.session.access_state.preview_level 2
GLOBALS.session.access_state.access_limit  null anonymous
```

Page-level Vue props also carry `has_insider_access:false` anonymously. **These live only in the
HTML — the JSON API responses carry no equivalent (§1).** So session health is an HTML fact and data
is a JSON fact; `SPEC.md` §6 joins them and records the race.

### Tiers — inferred from the front-end bundle, behaviour unverified

Read out of `application-setup-g-LhtWbK.js` (the bundle text is confirmed; the runtime *behaviour*
is not — this whole subsection is evidence for a hypothesis, not a measured tier boundary):

```js
O = GLOBALS.session.access_state.access_level >= GLOBALS.session.access_state.preview_level
// logged in: `You have ${k}/${f} free reviews left` / "You've used all your free reviews"
```

| Tier | `current_user` | Hypothesised access |
|---|---|---|
| Anonymous | `null` | catalog, schema, search, prose, rankings, **all curves**, and the public (`insider_only:false`) tests **with their scores**; no `insider_only` value or score, no usage ratings (§2 — **measured on TV only**; SUPERSEDED IN PART by §12.3, which measured mattress `Side Sleeping` at `score: 7.7, unblurred: true` anonymously). **Zero free previews — see below** |
| Free account | object | above + N metered full-review unlocks in `access_state.previewed_products`, capped at `access_limit` (**inferred from the bundle**) |
| Member (insider) | object, `has_insider_access` | everything `unblurred` (**unverified — §10 q1**) |

### Anonymous gets ZERO free previews — the threshold is in the client (confirmed 2026-09-03)

An anonymous `access_state`, read from a fresh-jar review-page load:

```json
{"access_limit": null, "previewed_products": [],
 "paywall_test_access_limit": null, "paywall_test_previewed_products": [],
 "access_level": 1, "preview_level": 2}
```

`application-setup-DDVkdcu5.js` gates the whole free-review budget on one expression:

```js
O = GLOBALS.session.access_state.access_level >= GLOBALS.session.access_state.preview_level
//  anonymous: 1 >= 2  ->  FALSE
k = (access_limit != null) ? Math.max(access_limit - previewed_products.length, 0) : 0
//  anonymous: access_limit is null -> 0
```

So **anonymous is one level BELOW the preview threshold.** It is not a metered tier that has been
exhausted; it never had a budget. The ladder the code implies:

| `access_level` | who | previews |
|---|---|---|
| 1 | anonymous | **none** — below `preview_level` |
| 2 | logged-in free account | budget of `access_limit` (meets the threshold) |
| higher | insider/member | everything unblurred |

**Measured corroboration (all anonymous, 2026-09-03):**

- Fresh jar → review page: `previewed_products: []`, `access_limit: null`, **0 `e-blurred` markers**
  in the HTML (the review page inlines no test results — it fetches them from the API after load).
- **Walked 7 review pages on one jar**: `current-pageviews` incremented 1 → 7 and `page-history`
  accumulated URLs, but **`access_state` never changed** across any of them.
- The `page_body` API call **carrying** those metering cookies: 402 rows, **0 unblurred
  `insider_only`** — identical to the cookieless call.
- The API call does **not** increment `current-pageviews`; only the HTML page GET does.

**`current-pageviews` / `page-history` are a nag/CTA meter, not a data gate.** They count anonymous
views but grant nothing. The UI's "unlock" affordance is `paywall-unlock-separator` — a 508-byte
component whose entire body is `<a class="e-button" href="/join" data-membership-cta-id="friction_paywall…">`.
A signup prompt, not a metered unlock.

> **Consequence for the design.** Rotating transport identity yields N byte-identical blurred
> payloads — there is no per-identity budget to reset, so there is nothing an identity strategy could
> accumulate. This is a measurement, not a policy position: the blur is applied server-side before
> serialization (§2), so the numbers are not in the bytes at any anonymous identity count. It also
> means `max_rotations=0` (`SPEC.md` §6) costs us nothing on the anonymous path.

**The one non-member unblur path is a member gift link.** `application-setup` renders
"A member has gifted you temporary access to our test results and images for this page" when
`insider_notice.dataset.hasAccessThroughShareToken === "true"`, and "The gifted access … has expired"
for `hadAccessThroughExpiredShareToken`. This is the `share_token` path (§10 q4) — member-generated,
max 3, ~2-day expiry. Sanctioned, but not a bulk source and not ours to generate.

Confirmed present in the request/bundle: a **`share_token`** path (`hasAccessThroughShareToken` in
the bundle) — mechanism unmapped (§10 q4) — and **`unblur_product_ids[]`** + **`force_blur`** in the
`test_results` request.

**A client-supplied `unblur_product_ids` is ignored anonymously (confirmed 2026-09-02).** Sent
`unblur_product_ids:["39008"]` on a gated test; product 39008 came back `unblurred:false, value:null`
like every other row. Privileged request *values* were confirmed not to affect the blur and were
not explored further; `SPEC.md` §9 forbids sending them, and this recon records no benefit from
doing so.

### The client render path — member data must flow through this same API (JS-confirmed 2026-09-02)

Read from the minified front-end bundles (public assets; no bypass involved — the finding is that
the paywall is *not* client-circumventable):

- **The client only displays a server-set flag.** The base cell mixin computes `is_blurred = !get(value,
  "unblurred", true)` and renders `value.rendered_value` verbatim via `innerHTML`. No decryption, no
  transform (`grep decrypt|crypto|atob|aes|xor` across the bundles → zero). If the server sends
  `value:null`, nothing on the client can produce a number, and nothing removes blur — client code
  can only *add* it (experiment flags, `paywall_test` display-force).
- **No credential but the cookie.** All `table_tool__*` queries go through one `fetch_json(POST
  /api/v2/safe/<name>, {variables})` with `credentials:"same-origin"` and `Content-Type` only. The
  CSRF meta tag exists and the client reads it, but attaches it **only** to `sign_out` and legacy
  form XHRs — never to `/api/v2/safe/`. Matches the captured request.
- **`app/`-prefixed queries differ:** their body is `{variables, share_token, url_path:
  location.pathname}` — the extra fields carry the review-page access resolution. `table_tool__*` send
  bare `{variables}`.
- **The member CSV export serializes these exact in-memory rows** (gated `is_insider_or_admin`).
  RTINGS would not ship a member feature that exports "Lock" — the strongest inference that a member
  session receives `unblurred:true` from this same API.

**Consequence for Phase 0 (§10 q1): ~90% likely a plain member cookie on the same request returns
real values** — the blur is a server-side per-session decision and there is no other member data path
for the table tool. The residual risk is server-side `Origin`/`Referer`/`Sec-Fetch` validation that
JS cannot prove; the cheap mitigation is to send the real browser headers unconditionally
(`SPEC.md` §6, §9). Build member mode on the API `value`, never on parsing `rendered_value` (it is
`null` when blurred and identical to `value` when not).

### The member gift link — format measured, token non-guessable (confirmed 2026-09-03)

The `share_token` field in the `product_vue_page__page_body` body (§1) is a **member gift link**
(§10 q4). Fully characterized from the client bundle plus a real public example.

**How the client builds it** (`member-shareable-link-controls.vue_…-DJOd9r-Q.js`): the member calls
`app/member_shareable_links__create_link`, and the client renders the URL as
`` `${window.location.protocol}//${window.location.host}${link.url}` `` — i.e. **the server returns
the entire path (`link.url`) and the client only prepends the origin.** There is no client-side token
construction, no charset logic, no path template — nothing to reverse-engineer. The overlay copy
confirms the model: *"you can gift full access to it by using the link below"*, *"Gifted links
expire after two days from creation."* `create_link` is capped (the UI enforces a small max) and
existing links come back from `app/member_shareable_links__user_links`.

**The inbound URL format** is a plain review URL with a `?share_token=` query param. The concrete
URL, slug and token are deliberately not recorded — see the note below. The server resolves the token
on the request and sets the access props server-side — the SPA receives
`has_preview_access_through_share_token` as a **pre-computed Boolean prop** and renders the
"A member has gifted you temporary access…" notice from a server-set `dataset` flag; it does not
compute access from the token itself.

**The token is a cryptographic secret, not guessable:**

| property | value |
|---|---|
| length | **32 chars** |
| charset | `[A-Za-z0-9_-]` — base64url |
| entropy | **~192 bits** if uniformly random (32 × 6) |
| brute-force @ 1e9/s | ~10⁴¹ years |

**Expiry is server-enforced, on both surfaces (confirmed 2026-09-03).** Replaying the (months-old,
now-expired) public token above: the page returns `has_preview_access_through_share_token:false`,
`had_preview_access_through_expired_share_token:true` (the server recognised it as a *real* token past
its window, not garbage), and the `product_vue_page__page_body` API returned **0 unblurred insider
rows** — identical to anonymous. So an expired gift link grants nothing; there is no client-side
grace and no stale-cache leak.

**Consequence for the design.** The path is real and structurally replayable — a review URL with a
query param, which a client that is *handed* a link could honor — but it is **not a data source we
can produce or predict**. A member must mint each link; it is per-page, ~2-day expiry, and capped per
member. 192-bit tokens are not enumerable, and attempting to enumerate them would be abuse regardless
(and pointless). So gift links stay **out of the data flow**: rtings-mcp does not generate them, does
not guess them, and does not depend on them. (A future nicety — honoring a `share_token` a *user*
pastes — is possible since the server does all resolution, but it is not member mode and not planned
for v1.) This resolves §10 q4.

### Unblurred data shape — observed once via a publicly posted gift link (2026-09-03)

**Sanctioned source, recorded without the method.** RTINGS' own staff publish "unlocked 48hrs" gift
links publicly, meant to be opened by anyone; one such link was opened **once** to observe what an
unblurred row looks like structurally. That is using access the publisher handed out, not
circumvention.

**Deliberately not recorded here:** the token, the product slug, the exact request body that makes the
API honor a token, and where live links are posted. This repo is public, the design does not depend on
gift links (below), and a step-by-step for redeeming someone else's grant is not a measurement — it is
a recipe. Only the row *shape* is kept, because that is what the normalizer is built on.

**Two facts the design does rest on:** the grant is **per-product** (1 of 97 products came back
`unblurred:true`; the other 96 stayed blurred), and it is enforced identically on the review path and
the table path. The per-product scope is why `SPEC.md` §8 merges tier files **per row** rather than
per file.

**No unblurred value is recorded here or committed to a fixture** — only structure (CLAUDE.md fixture
rule).

**The unblurred row shapes (the load-bearing capture):**

- **Table tool (`table_tool__test_results`) — carries a clean machine value.**
  ```
  unblurred: {value:<raw>, score:<raw>, rendered_value:"<formatted>", unblurred:true, status:"tested", original_id, product_id, kind, …}
  blurred:   {value:null,  score:null,  rendered_value:"<span class=\"e-blurred\">Lock</span> : 1", unblurred:false, status:"tested"}
  ```
  When unblurred, `value` holds the raw number/string and `score` the raw 0–10 — exactly the fields
  that are `null` when blurred. **Parse `value`, not `rendered_value`.**

- **Review body (`product_vue_page__page_body`) — NO machine value, ever.** The unblurred review row
  has real data only in `rendered_value` (a formatted string, e.g. `"1950 cd/m²"`, `"Yes"`) plus a
  real `score` float; **there is no `value` key at all**, blurred or unblurred (`value_extended_hash`
  is `{}`, not a hiding place). So `rt_product` on this path must **parse `rendered_value`** — strip
  the unit via the schema's `number_display_unit`/`_precision` — whereas the table path hands over a
  clean `value`. Two different extraction strategies for the two surfaces.

**Phase 0 impact (this is the big one).** This is direct, measured proof — not JS inference — that:

1. the **API itself** serves unblurred values when the server grants access (both the review body and
   the bulk table tool), not merely the SSR HTML;
2. the **unblurred row shapes are exactly as the design assumes** — table `value`/`score` populate,
   review `rendered_value`/`score` populate;
3. `rendered_value` fully replaces the `Lock…` placeholder (0 of 396 unblurred rows still say "Lock").

So §10 q1's *mechanism and shape* are now confirmed. The residual is narrow: a **member cookie** is a
session-wide grant across **all** products, whereas a share token is per-product — Phase 0 still must
confirm the member cookie specifically flips `unblurred` at that scope (RECON §5's CSV-export evidence
already points that way). Buying the membership is still needed for member mode, but the risky
unknowns (does the API unblur at all? what does an unblurred row look like?) are answered.

---

## 6. The schema — `column_options`

`table_tool__column_options` (`{silo_url_part:"tv",named_version:"public"}`) → `data.silo`:

| key | TV value | meaning |
|---|---|---|
| `id` | `"1"` | silo id |
| `tested_products_count` | 548 | |
| `test_benches` | 14 | methodology versions (§8) |
| `test_bench` | `{usages[11], tests[402]}` | the **current** bench's definitions |
| `legacy_usages` / `legacy_tests` | 4 / 161 | older-bench definitions |

**Test definition** (`test_bench.tests[]`) carries everything needed to render and coerce a value
without inspecting the value:

```
original_id  name  kind  has_score  insider_only  parent_original_id  order  published
number_display_unit  number_display_precision  number_input_unit  number_prefix  words[]  …
```

`kind` distribution across the 402 TV tests (confirmed):

```
number 152   word 86   picture 72   group 57   category 12   video 8   freeform 6   graph 5   dropdown_images 4
```

Confirmed and load-bearing:

- **`insider_only` is the schema-level paywall flag** — 396 of 402 `true`, matching per-row
  `unblurred:false` exactly. It is **session-independent and payload-derived**, so it, plus observed
  `unblurred`, is what `scores_available` is computed from (`SPEC.md` §7). CR had no equivalent.
- **`kind` is the declared type — coerce by it, never by value shape.** Boolean-style fields are
  `word` tests with `words:[{value:"No"},{value:"Yes"}]`; numeric-looking `word` values exist.
- **`original_id` is the stable key; `name` is not** — names repeat (six different tests are all
  named "Peak 100% Window" across sub-groups; `id` disambiguates).
- **Hierarchy** via `parent_original_id` (`group`/`category` parents of `number`/`word` leaves).
  `number_display_unit`/`number_display_precision` give unit and rounding — never infer a unit.
- **Join definitions from `column_options`, not the per-row `test:{id}` stub.** A `test_results` row
  carries only `test:{id}` (a numeric primary key); name, kind, unit and `insider_only` live in the
  schema, keyed by `original_id`. Note **`id` ≠ `original_id`** — the row references the test by
  `original_id`.

### The "not tested" row shape — a not-tested pair is an ABSENT row (confirmed 2026-09-02)

Measured: requested 4 tests across 6 benches (old v0.8–v1.0 + recent v2.0.1–v2.2), 251 products.
**Every returned row is `status:"tested"`** — there is no `not_tested` status and no null-value
placeholder row. Coverage was test-and-bench specific:

| test | rows | old-bench products | recent-bench products |
|---|---|---|---|
| 208 Resolution (spec) | 251 | 126/126 | 116/116 |
| 5 Bright Room (legacy v0.9) | 37 | 37/126 | **0/116** |
| 28334 CIELAB DCI-P3 (modern) | 125 | **0/126** | 116/116 |
| 11 Native Contrast | 251 | 126/126 | 116/116 |

An old-bench product simply has **no row** for a modern test, and vice versa (e.g. product 182
returned rows for tests 11 and 208 only, not 5 or 28334). So the two states the safety property must
separate are **structurally distinct**, and the API gives both:

- **present row, `unblurred:false`** ⇒ RTINGS measured it, you can't see it → `value:null, gated:true`
- **absent row** ⇒ RTINGS did not test this product on this test → an explicit `not_tested`, not a gated null

This resolves the concern that the API gave "only half" the distinction — it gives both, cleanly.
Coverage is **bench-specific**: a product carries rows only for tests in its own bench.

### `status:"na"` — a fourth state, and a trap (confirmed 2026-09-03)

The 2026-09-02 pass concluded "every returned row is `status:"tested"`". **That holds for
`table_tool__test_results` and is false for the review body.** Both products sampled on the review
path returned rows with `status:"na"`:

| product | bench | rows | `tested` | `na` |
|---|---|---|---|---|
| Sony W600B (154) | 2 (v0.9) | 54 | 51 | **3** |
| Sony X90L (39008) | 227 (v2.2) | 402 | 401 | **1** |

Every `na` row carries `unblurred:false, insider_only:true, score:null` — i.e. **it is
byte-for-byte indistinguishable from a gated row on the `unblurred` flag alone.** The W600B's three:

```
oid 48   Motion Interpolation Picture   kind picture   rendered_value null
oid 590  3D Picture                     kind picture   rendered_value null
oid 593  Motion Interpolation           kind word      rendered_value "<span class=\"e-blurred\">Locked</span>"
```

These are **not applicable** to the product (a 2014 set with no motion interpolation, no 3D), not
measured-and-withheld. A three-state normalizer keyed on `unblurred` alone reports all three as
`tested_gated` — telling an agent "RTINGS measured this, buy a membership to see it" about a test
that does not apply. That is the project's stated worst failure mode, reached by a different route
than the one §6 anticipated.

**Rule:** branch on `status` **before** `unblurred`. `status:"na"` ⇒ `not_applicable`. The
normalizer emits **seven** states (`SPEC.md` §5/§7): `tested_visible` / `tested_gated` /
`not_applicable` / `not_tested` / `review_unpublished` / `coverage_unknown` /
`unknown_row_status`. It was **four** when this section was written (2026-09-03); the other
three were each forced by a later measurement — see §12.10 (Early Access), §8 (coverage).
 The observed `status` domain is `{"tested","na"}`; treat it as
open — an unrecognised value maps to `unknown_row_status` with a warning, never to `tested_visible`.

### The review path returns one row per test in the product's **own** bench (confirmed 2026-09-03)

`app/product_vue_page__page_body` row counts match the product's bench test count **exactly**:

| product | bench | `test_results` rows | bench `tests` in `column_options` |
|---|---|---|---|
| Sony W600B (154) | 2 (v0.9) | 54 | 54 |
| Sony X90L (39008) | 227 (v2.2) | 402 | 402 |

Two consequences that differ from the table path:

- **The review body never spans the silo** — a v0.9 product returns 54 rows, not 402. Body size
  tracks it: 49 KB vs 437 KB. So "absent ⇒ not_tested" is safe here **only against the product's own
  bench**; joining review rows against the full silo schema would invent ~350 false `not_tested`s.
- **Within the bench, no row is ever absent.** All 54/54 and 402/402 are present. So on this path
  not-applicable is expressed as `status:"na"`, *not* as absence — the opposite of the table path,
  where the same fact is an absent row. Both encodings must map to the same normalized state.

**The two paths return different row shapes.** The table row carries `value`; the review row has
**no `value` key at all** — it carries `rendered_value` (an HTML string) plus `score`:

```
table  row: {original_id, value, score, rendered_value, unblurred, status}
review row: {test:{…}, rendered_value, score, unblurred, insider_only, status,
             graph_data_url, graph_tool_url, linked_description, asset_url, …}
```

The review row's `test:{…}` stub does carry `original_id`, `kind`, `insider_only`, `has_score` and
`parent`, but **not** `number_display_unit`/`number_display_precision` — so the §6 join rule stands:
units and precision come from `column_options`. Live confirmation that **`id` ≠ `original_id`**:
`{"id":"43","original_id":"23"}` for "Max white".

---

## 7. Recommendation pages — ranked best-of, page-extracted

`RecommendationVuePage` (e.g. `/tv/reviews/best/tvs` → "The 7 Best TVs of 2026"), rendered
anonymously. `recommendation.product_recommendations[]` — 7 ranked picks, each with `title`
("Best TV"), `subtitle`, `description` (~1.4 KB reasoning prose), `product`, `featured_deals`, plus
`featured_test_results[]`/`ratings[]` that are **blurred like everywhere else** except the same 6
public spec fields.

So this layer gives an agent an **ordered shortlist with human reasoning** for free — the closest
RTINGS analogue to CR's "anonymous is a real tier" — but not the numbers.

**Source caveat (confirmed):** this data was read from the page's `data-props`, **not** from a query
in the §1 catalog. No recommendations API query was observed. So `rt_recommendations` is a
**page-extraction** path — the one exception to "API, not pages" — and `SPEC.md` isolates it with its
own parser and drift alarm. Whether an API query exists is unknown (§10 q10).

**IMPORTANT: there are now TWO best-of templates (measured 2026-09-05, §12.17).** The above
describes the `RecommendationVuePage` one. mattress and running-shoes have moved to a
**server-rendered** template with no `page_data` anywhere on the page, and the old parser
returned `recommendations_missing` for every one of their lists.

---

## 8. Test benches — the cross-comparison axis

TVs carry **14 benches**: v0.8, v0.9, v1.0, v1.2, v1.5, v1.6, v1.7, v1.8, v1.9, v1.10, v1.11, v2.0.1,
v2.1, v2.2 (ids `1,2,3,9,81,89,101,108,118,121,124,197,210,227`). Each product is on exactly one
bench (`review.test_bench.id`).

**Confirmed:** the site's own default table tool requests the **three most recent** benches together
— `test_bench_ids:[197,210,227]` (v2.0.1/v2.1/v2.2), 116 products across the three. So RTINGS *does*
render multiple benches in one table.

### The recent-bench set is a server-set flag: `is_recent` (confirmed 2026-09-03)

`[197,210,227]` is not a magic number and not derivable from `column_options`. Every silo's HTML
page embeds, in `GLOBALS`, a bench list carrying the flag directly:

```json
"latest_test_bench_id": "227",
"test_benches": [{"major":false,"is_recent":true,"id":"227"},
                 {"major":false,"is_recent":true,"id":"210"},
                 {"major":false,"is_recent":true,"id":"197"},
                 {"major":true, "is_recent":false,"id":"171"}, …]
```

**`is_recent:true` reproduces the TV set exactly**, and the set size varies per silo — so a fixed
"three most recent" rule would be wrong for most of them:

| silo | `latest_test_bench_id` | `is_recent:true` ids | n |
|---|---|---|---|
| tv | 227 | 227, 210, 197 | 3 |
| headphones | 252 | 252, 244, 230, 183 | 4 |
| mouse | 233 | 233, 199 | 2 |

Rules that were tested and **fail**: "top 3 in list order" (right for TV, wrong for headphones and
mouse); "share the current major version" (headphones → 1 bench, mouse → all 8); `major` (uniformly
`false` in `column_options`, so it separates nothing there).

Two further facts, both load-bearing:

- **The page's bench list is longer than `column_options`'.** TV: 18 on the page, 14 in the schema
  (the page adds 171, 80, 12, 5). `column_options` lists only benches with a published schema — and
  it is the page, not the schema, that carries `is_recent`. Bench *ids* therefore come from the
  page; bench *definitions* from `column_options`. Do not assume the two lists match.
- **`major` is not uniformly false** — TV bench 171 is `major:true` on the page. `column_options`
  simply omits every bench where it is true, which is why the schema view looks flat.

`GLOBALS.static.silos[]` (all 28, one fetch) carries each silo's **current** bench as a full object —
`test_bench.{id, name, replaces_id, test_bench_page_id, retest_message, …}` — where `replaces_id`
chains benches backwards (227→210, 252→244, 233→199, 238→221). But `static.silos[]` does **not**
carry `test_benches[]` or `is_recent`. So: one fetch gives the current bench for all 28 silos; the
`is_recent` set costs **one page fetch per silo** (cacheable, and the same fetch the auth probe and
silo discovery already make).

**Unmeasured:** whether a measurement on v1.11 and one on v2.2 are directly comparable, or whether
adjacent minor benches are rescored vs additive (§10 q7). The conservative default (`SPEC.md` §7):
treat the **site's own recent-bench set as the comparable population**, carry `test_bench` on every
product, and nest (never flatten) when a caller widens beyond it. This is a design default pending
measurement, **not** a claim that benches are incomparable.

---

## 9. Prior art — the graph seam is trodden, the structured layer is empty

Searched GitHub (repos + code), the MCP registries/directories, and general web, 2026-09-02. **No
RTINGS MCP server exists.** (npm and PyPI registries were **not** directly queried — do not claim
they were.) Everything found is graph-curve extraction:

| Project | What it does |
|---|---|
| `jaakkopasanen/AutoEq` — `dbtools/rtings_crawler.py` | canonical; uses **exactly** `graph_tool__product_graph_data_url` + CDN JSON for headphone FR, `named_version:"public"`, years unchanged |
| `lukenuyen/RTINGs_data_downloader` (2025-09) | "downloads RTINGs graph data directly from JSON" |
| `ShuiJu/…FR-Extractor…`, `CosmicIndustries/rtingsExtractor`, `rezarajan/freq-response-extractor`, `bogus7000/auto-fr-score` | more FR rippers |
| `GuacOn/monitor-review-finder` | Firefox ext; uses `app/search__search_results` (confirmed anonymous here) + scrapes the scorecard |
| `neil-mithipati/signal`, `…/reviewer` | LLM-over-scrape "agentic reviewer", not structured |

**Everyone took the headphone-FR curve slice. Nobody built the category-wide structured layer** —
schema, catalog, cross-product table, bench versioning. That is open, and every endpoint it needs
answers anonymously. What no scraper solves is the scalars and the 0–10 scores — still gated (§2).

---

## 10. Open questions — settle before the code they gate

Ordered by how much they gate. A membership will be bought to answer q1–q4.

1. **Does a member/insider cookie flip `unblurred:true` on the API?** **Mechanism + shape confirmed
   2026-09-03 (§5)** via a live, RTINGS-posted gift link: the safe API (both `product_vue_page__page_body`
   and `table_tool__test_results`) *does* return unblurred `value`/`score` when the server grants
   access, and the unblurred row shapes match the design. **Residual (still blocking for member-mode
   code):** confirm a **member `_rtings_session`** produces that same unblur across **all** products
   (a share token is per-product; a member cookie should be session-wide). One
   `table_tool__test_results` call with a logged-in cookie answers it — now a confirmation, not a
   leap.
2. **The free-account quota.** **Half-resolved 2026-09-03 (§5):** the anonymous side is settled —
   `access_level 1 < preview_level 2`, so anonymous gets **zero** previews and no identity strategy
   changes that. What remains open is the *logged-in* side: the value of `access_level` and
   `access_limit` on a free account, and the meter's unit. **Lead (JS, 2026-09-02):**
   the free-preview meter is entirely server-side in `access_state`, incremented on the review-page
   GET / `app/product_vue_page__page_body` (which carries `url_path`) — **not** by any client unlock
   call, and the `table_tool__*` endpoints carry no `url_path`. So a free preview most likely unblurs
   a specific product's *review* (the `rt_product` path), not the bulk table. Confirm with the
   account which endpoints a metered unlock actually touches.
3. ~~**Session lifetime / rotation.**~~ **RESOLVED anonymously (§12.15, 2026-09-04).** RTINGS
   re-issues `_rtings_session` on **every** response with `expires = now + 30 days`, so the window
   is a sliding *idle* one: the session lives indefinitely while used and dies 30 days after it
   stops. Rotation write-back is therefore required, gated on a logged-in probe. Still to confirm
   once logged in: that the same holds for a member value, and that the browser stays signed in
   while the server uses the same blob.
4. ~~`share_token` — the member gift link.~~ **RESOLVED (§5, 2026-09-03).** URL format is a plain
   review URL + `?share_token=<TOKEN>`; token is a **32-char base64url ~192-bit secret** (not
   guessable); server resolves it and sets access props server-side; ~2-day expiry, member-minted,
   capped. **Not a data source** — stays out of the flow. Created via
   `app/member_shareable_links__create_link`.
5. ~~Per-silo paywall boundary.~~ **RESOLVED (§2)** — generalizes: each silo gates all but a small
   set of `word` spec tests (headphones 8/234, mattress 4/142). **Amended 2026-09-03:** the boundary
   is per-**(silo, bench)**, not per-silo — TV bench 227 exposes 6 public tests, TV bench 2 exposes
   1. Derive `scores_available` per (silo, bench).
6. **API behaviour under sustained / member-authenticated volume.** All requests here were
   low-volume anonymous; no WAF or rate limit hit, but not stressed.
7. **Minor-bench comparability.** Are v2.0.1/v2.1/v2.2 rescored or additive (§8)? Decides whether the
   default population is one bench or the recent set. (Needs member data to compare scores.)
8. ~~Non-empty `unblur_product_ids` anonymously.~~ **RESOLVED (§5)** — ignored; the server decides
   blur by session, not request params. `force_blur:true` still unprobed but low-value.
9. ~~The "not tested" row shape.~~ **RESOLVED (§6)** — a not-tested pair is an **absent row**.
    **Amended 2026-09-03:** "every present row is `status:"tested"`" holds only for
    `table_tool__test_results`. The review body also emits `status:"na"` (not applicable), which is
    indistinguishable from gated on `unblurred` alone → the normalizer emits four states. **Now
    seven** — see §6 and §12.10.
15. **What decides whether a silo enforces the paywall?** Measured: 12 of 28 enforce, 16 do not
    (§11.1). The aggregate correlates with size and age, but robot-vacuum/vacuum (same first-published
    date, same bench count, and the **open** one has more reviews), keyboard-switch/router and
    camera/projector all falsify "maturity" as a rule. Mechanism unknown. **Probably not answerable
    from the API** — it is a business decision — so the split is a dated snapshot, re-scanned per
    release (CLAUDE.md > Release).
16. **Does enforcement hold on LEGACY benches, the review path, and non-leaf kinds?** Both sweeps
    covered only each silo's **current** bench and its first 40–50 `number`/`word` leaf tests (§11).
    `page_body`, `picture`/`video`/`audio`/`graph` kinds and older benches are unmeasured.
10. ~~**Does a recommendations API query exist**~~ **— ANSWERED: no (§11.7).** Page extraction is
    the only path. Original note follows. A static
    grep of the minified bundles was inconclusive (URLs are built dynamically); needs a browser
    network capture of a `/reviews/best/` page. Non-blocking — the page-extraction path works. **List
    discovery IS resolved (2026-09-03):** the silo landing page `/{silo}` enumerates its best-of
    lists as `/{silo}/reviews/best/<slug>` links (15 on TV — `by-size`, `by-usage`, `budget`, `ps5`,
    `mini-led`, …); `rt_recommendations` extracts those hrefs, no slug-guessing (`SPEC.md` §7, §13 q9).
14. ~~Do usage definitions carry `insider_only`?~~ **RESOLVED (2026-09-03)** — they do **not**
    (tv/headphones/monitor/mouse all: usage keys are `original_id, name, kind, order, published,
    is_sub_usage, parent_usage_name` — no `insider_only`; only *test* defs carry it). So
    `scores_available.usage_ratings` (`SPEC.md` §7) derives from observed `unblurred`, not a flag.
11. ~~Per-silo default bench set.~~ **RESOLVED (§8)** — `is_recent:true` on the page-embedded
    `GLOBALS` bench list; set size varies per silo (tv 3, headphones 4, mouse 2).
12. ~~**Does `status` carry values beyond `{"tested","na"}`?**~~ **RESOLVED 2026-09-03 (§11.3) — yes,
    a third value `untested` (154 rows).** Original note: two observed across 456 review rows and
    7,500 table rows. The domain is almost certainly larger (retired? pending?). Non-blocking — the
    normalizer already treats an unrecognised value as `unknown_row_status` — but worth a wider
    sweep once more silos are ingested.
13. ~~**Is `status:"na"` ever `unblurred:true`?**~~ **RESOLVED 2026-09-03 (§11.4) — yes, 1,025 of
    2,186 (47%).** Original note: all 4 observed `na` rows were `unblurred:false` on
    gated tests. If a *public* test can be `na`, the value branch must still not run. Cheap to check
    once more products are ingested; the seven-state normalizer is already ordered to be safe either
    way (`status` is branched first).

---

## 11. Anonymous sweep, 2026-09-03 — the paywall model was wrong

**Two anonymous sweeps, 2026-09-03. Keep them straight — different scopes, different claims rest on
each.**

| | Sweep A | Sweep B |
|---|---|---|
| Silos | **10** | **all 28** |
| Leaf tests per silo | first 50 | first 40 |
| Rows | **41,944** total rows | **65,147** `insider_only` rows |
| What rests on it | the `status` domain, the `(status, unblurred, value)` census, the 47% `na`-unblurred figure (§11.3–11.4) | the enforcement map (§11.1), `docs/enforcement-snapshot.json` |

Both used the current bench from `GLOBALS.static.silos` and one `table_tool__test_results` POST per
silo. **Everything below is scoped to a silo's CURRENT bench and its first 40–50 `number`/`word` leaf
tests.** Legacy benches, the review path (`page_body`), and the `picture`/`video`/`audio`/`graph`
kinds were **not** swept — do not generalize to them.

### 11.1 The complete model — two independent gates, all 28 silos measured

**A row is blurred if and only if:**

```
blurred  ⇔  product.published == false            (Early Access — an Insider perk, see §12.10)
         ∨  (test.insider_only == true  ∧  the SILO enforces the paywall)
```

Nothing else. This accounts for every row in both sweeps, with zero exceptions.

**Enforcement is per-(silo, current bench) and binary — 12 of 28 enforce, 16 do not.** All 28 carry
`has_paywall: true`, so that field is useless as a signal. Measured anonymously, current bench, first
40 `number`/`word` leaf tests per silo (`open%` below is over `insider_only` rows only):

| Silo | open% | reviews | benches | since | group |
|---|---|---|---|---|---|
| headphones | 0 | 912 | 9 | 2016-02-26 | audio |
| tv | 0 | 548 | 14 | 2011-11-22 | home-entertainment |
| monitor | 0 | 408 | 6 | 2017-10-12 | computer |
| mouse | 0 | 408 | 8 | 2019-12-11 | computer |
| keyboard | 0 | 297 | 6 | 2020-01-20 | computer |
| soundbar | 0 | 254 | 4 | 2019-09-30 | home-entertainment |
| speaker | 0 | 200 | 3 | 2020-12-14 | audio |
| printer | 0 | 183 | 6 | 2020-05-11 | computer |
| laptop | 0 | 179 | 4 | 2021-11-01 | computer |
| robot-vacuum | 0 | 99 | 5 | 2020-07-06 | home |
| projector | 0 | 98 | 4 | 2024-02-13 | home-entertainment |
| router | 0 | 79 | 1 | 2024-07-22 | computer |
| **running-shoes** | **77.0** | 284 | 8 | 2025-04-02 | shoes |
| **toaster-oven** | **90.9** | 28 | 1 | 2025-02-07 | kitchen |
| **camera** | **98.7** | 127 | 4 | 2021-02-08 | photo-video |
| keyboard-switch | 100 | 153 | 1 | 2023-11-06 | computer |
| blender | 100 | 138 | 3 | 2020-09-08 | kitchen |
| vacuum | 100 | 115 | 5 | 2020-07-06 | home |
| mattress | 100 | 69 | 1 | 2025-07-15 | home |
| air-purifier | 100 | 53 | 2 | 2024-05-13 | home |
| air-fryer | 100 | 53 | 2 | 2024-09-23 | kitchen |
| refrigerator | 100 | 40 | 1 | 2025-10-08 | kitchen |
| microwave | 100 | 37 | 1 | 2024-07-10 | kitchen |
| toaster | 100 | 28 | 1 | 2023-12-15 | kitchen |
| vpn | 100 | 23 | 2 | 2025-03-31 | computer |
| air-conditioner | 100 | 22 | 1 | 2025-06-06 | home |
| dehumidifier | 100 | 21 | 1 | 2024-07-03 | home |
| humidifier | 100 | 20 | 1 | 2024-11-22 | home |

**The three "partial" silos are not partial.** Their gating is **100% per-product**, never per-test —
0 of 36/31/37 tests are partially blurred, while 3/3/1 products are *entirely* blurred. Cross-tabbed
against the catalog's `published` flag:

| Silo | `published:false` | `published:true` |
|---|---|---|
| running-shoes | 106 rows, **all blurred** | 252 rows, **all unblurred** |
| toaster-oven | 93 rows, **all blurred** | 899 rows, **all unblurred** |
| camera | 50 rows, **all blurred** | 3,650 rows, **all unblurred** |

Zero exceptions. So running-shoes, toaster-oven and camera are **fully open silos with unpublished
reviews in them** — there is no third mechanism, and the enforcement split is a clean 12/16.

**What predicts enforcement is UNKNOWN — see §10 q15.** In aggregate the gated set is larger and
older (median 227 reviews and 5.5 benches, back to 2011; open median 46.5 reviews and 1 bench, none
before mid-2020), which invites a "maturity" story. **The same data falsifies it as a rule:**

- **robot-vacuum (gated) and vacuum (open)** share a first-published date (2020-07-06) *and* bench
  count (5) — and the **open** one has more reviews (115 vs 99).
- **keyboard-switch (open, 153 reviews)** vs **router (gated, 79)**, both 1 bench.
- **camera (open, 2021)** is older than **projector (gated, 2024)**.
- 7 of the 16 open silos have more than one bench; 5 are older than a gated silo.

So the aggregate correlation is real and the mechanism is not established. Treat the split as
**measured and unexplained**; do not describe it as tracking maturity, and do not predict which silo
flips next.

**IMPORTANT: treat this map as a snapshot, not a constant.** The correlation with age and bench count
says enforcement is rolled out as a category matures, so a silo that is open today can be gated later
(and `RECON.md` cannot see the reverse being impossible either). **Never hardcode this table.** Derive
the boundary from observed `unblurred` per (silo, bench) on every fetch, and let the cache's
never-downgrade rule absorb a silo flipping open → gated.

### 11.2 Consequence — anonymous is a FULL DATA mode for 16 of 28 silos

The values on open silos are real RTINGS measurements with scores, not specs:

```json
{"original_id":"31953","value":"50.59487784","score":0.0,
 "rendered_value":"51 Pa/mm","unblurred":true,"status":"tested"}   // mattress Normalized Stiffness @ Lumbar
{"original_id":"38052","value":"44.26","score":5.4,
 "rendered_value":"44 mm (1.7\")","unblurred":true,"status":"tested"}  // mattress Indent @ 50 kg
```

0 of 1,144 air-purifier rows and 0 of 4,050 mattress rows carry a `Lock`/`e-blurred` marker.

So the §2 framing — "anonymous is thin on numbers by design" — is **TV-centric and wrong for the
majority of the catalog**. It holds for the 12 flagship silos and fails completely for the other 16,
where an anonymous caller gets values, scores, ranking and comparison. `SPEC.md` §5 and §7 are
updated accordingly.

**`published:false` must never be reported as `tested_gated`.** It is a review-in-progress blur that
the paywall does not explain, and it occurs in gated *and* open silos alike. **See §12.10:
it is Early Access, and a membership does lift it.** `published` ships in
`products_list`, so it is detectable anonymously and must be checked before any blur is attributed to
the paywall.

### 11.3 `status` has a THIRD value — `untested` — and the table path uses it

Across 41,944 rows: **`tested` 39,604, `na` 2,186, `untested` 154** (mattress 75, mouse 79).

This corrects §6. "Not-tested on the table path is an **absent** row" is only half true: the table path
emits **both** encodings — absent rows *and* explicit `status:"untested"` rows, in the same silo.
A normalizer that maps `untested` to `unknown_row_status` (the previous spec's step 4) would raise a
false drift alarm on an ordinary state **and fail to report `not_tested`.** Map `untested` →
`not_tested`.

### 11.4 `status:"na"` IS frequently `unblurred:true` — §10 q13 answered

**1,025 of 2,186 `na` rows are `unblurred:true`** (47%). The earlier "all 4 observed were
`unblurred:false`" was a TV-only artifact. `untested` likewise: 80 of 154 are `unblurred:true`.

Full `(status, unblurred, has_value)` census:

| status | unblurred | value present | rows |
|---|---|---|---|
| tested | false | no | 28,630 |
| tested | true | **yes** | 10,972 |
| na | false | no | 1,161 |
| na | **true** | no | 1,025 |
| untested | **true** | no | 80 |
| untested | false | no | 74 |
| tested | true | **no** | 2 |

**This strengthens the "branch `status` before `unblurred`" rule and proves it necessary in BOTH
directions.** The old justification was only that an `na` row looks like a gated row. The measured
reality is worse: an `na` or `untested` row that is `unblurred:true` would be read as
**`tested_visible` with a null value** — i.e. "RTINGS measured this and the answer is nothing."

The 2 `tested + unblurred:true + value:null` rows (mattress `original_id 32186`) also carry
`rendered_value: null`. A visible tested row **can** be genuinely empty; `tested_visible` must
tolerate a null value rather than assuming a value exists.

### 11.5 Schema facts

- **Usage definitions carry no `insider_only`** — 0 of 10 silos have the key (was 4). §10 q14 closed.
- **Public tests are not all `word`.** `number` publics exist: laptop `Size`, monitor `Size` /
  `Max Refresh Rate`, mouse `Default Weight`, vpn `Data Limit`. Any "public ⇒ `word` spec field"
  shorthand is wrong.
- **Public-test counts per silo (current bench):** air-purifier 2, laptop 2, camera 3, vpn 3,
  mattress 4, mouse 4, monitor 5, printer 5, tv 6, headphones 8.
- **`kind` domain is larger than TV's**: beyond `number`/`word`/`group`/`category`/`graph`/`picture`/
  `video`/`freeform`/`dropdown_images`, the sweep found **`audio`** (headphones 15, camera 1),
  **`3d_model`** (mouse 1) and **`download`** (monitor 1).
- **IMPORTANT: `test_benches[].tests[]` is a REFERENCE list — each entry is `{original_id}` only.**
  Definitions live in `silo.test_bench.tests[]` (current bench) and `silo.legacy_tests[]`. Bench→test
  *membership* comes from `test_benches[]`; test *definitions* come from the other two. Joining the
  wrong one yields an empty schema silently.
- `silo.test_benches[]` length varies widely: tv 14, headphones 9, mouse 8, monitor 6, printer 6,
  camera 4, laptop 4, air-purifier 2, vpn 2, **mattress 1**.

### 11.6 `last_updated_at` is a bulk-job field, not a retest signal

Distinct `last_updated_at` values (to the minute) per catalog: camera 7 across 74 products (65 share
one minute), mouse 35 across 110 (60 share one), vpn 4 across 22 (19 share one), air-purifier 15
across 51 (29 share one). TV is the exception (82 distinct across 97).

So `last_updated_at` is dominated by site-wide re-indexing. It **cannot** be used to detect "this
product was tested since my cached slice", in either direction: it fires constantly without a retest,
and nothing shows it reliably fires *with* one. The `coverage_stale` hole in `SPEC.md` §8 stays open,
and the mitigation there (an `as_of` stamp on every `not_tested`, plus a shorter TTL on current-bench
slices) is the right one — not a `last_updated_at` comparison.

### 11.7 §10 q10 answered — there is NO recommendations API

A browser network capture of `/tv/reviews/best/tvs` (Playwright, full load) shows **exactly two**
`/api/v2/safe/` POSTs: `comment_list__comments` and `price_box__batch_prices`. Neither carries
recommendation content — the ranked picks are server-rendered into the page. **Page extraction is the
only path**, confirming `SPEC.md` §7's isolated `rt_recommendations` design.

Two incidental findings: `price_box__batch_prices` and `comment_list__comments` are previously
unrecorded queries; and **best-of slugs redirect** — `/tv/reviews/best/tvs` → `/tv/reviews/best/
tvs-on-the-market`. `rt_recommendations` must follow redirects and cache the canonical slug.

---

## 12. Implementation pass, 2026-09-03/04 — facts the build measured

Everything below was measured while writing the server, anonymously, against the live API.
It **corrects or extends** earlier sections; where it corrects one, the correction is
marked. The 28-silo enforcement re-scan run at the end of the build reproduced
**12 enforcing / 16 open exactly**, with zero drift against `docs/enforcement-snapshot.json`.

### 12.1 Response paths, confirmed by fetching each one

| Query | Payload path | Notes |
|---|---|---|
| `table_tool__column_options` | `data.silo` | `test_bench` = `{tests, usages}` and carries **no `id`** — the current bench's id is not in the schema |
| `table_tool__products_list` | `data.products` | product carries `review.test_bench.{id,display_name}`, `published`, `page.url` |
| `table_tool__test_results` | `data.test_results` | row carries `product_id` directly — that is the join key |
| `table_tool__ratings` | `data.ratings` | see §12.3 |
| `graph_tool__product_graph_data_url` | `data.product.review.test_results[0].graph_data_url` | |
| `app/search__search_results` | `data.search_results` | `{query, max_score, total_count, results[]}` |
| `app/product_vue_page__page_body` | **`data.page`** | the review is at `data.page.product.review`; **corrects** any assumption of a `data.page_body` key |

### 12.2 `test_results` has a WIDER product population than `products_list`

Requesting TV benches `[227,210,197]` returns **118** products from `products_list` but rows
for **127** distinct products from `test_results`. The 9 extra ids (`108448`, `125243`,
`127553-8`, `127950`, `127954`) appear in **no** `products_list` response across all 18
benches, and every one of their rows is blurred.

**This is structural, not staleness.** A row for a product the catalog does not carry is
therefore *not* evidence the catalog is behind — an earlier draft of the warning said it was.
Such rows are filed under `tests/_unassigned/` rather than dropped (a dropped row becomes a
false `not_tested` if the catalog later catches up) and are summarised as a `coverage:
uncatalogued` group of ids, never ranked by default.

**Identified 2026-09-06 (member session), by resolving four of them through
`app/side_by_side__review`, which answers by bare id and whose `product` block carries
`product_page__url`, `fullname`, `silo__url_part` and `product_page__early_access`:**

| id | silo | `fullname` | `product_page__url` | bench |
|---|---|---|---|---|
| 108448 | tv | LG G5 OLED (Copy) | `/tv/reviews/lg/g5-oled-copy` | 197 |
| 125243 | tv | Samsung QN90F (Copy) | `/tv/reviews/samsung/qn90f-copy` | 210 |
| 138806 | mattress | Boring Hybrid (Spring Layer Firmness Baseline) | `/mattress/reviews/boring/hybrid-copy` | 236 |
| 113724 | mattress | Boring Mattress - TBF 1.0.1 | `/mattress/reviews/boring/mattress-copy` | 49 |
| 113606 | mattress | Sleep On Latex Pure Green Organic - TBF 1.0.1 | `/mattress/reviews/sleep-on-latex/pure-green-organic-tbf-1-0-1` | 202 |

They are RTINGS' **internal copies, baselines and retests** ("(Copy)", "TBF 1.0.1"), kept
out of `products_list` on purpose, not for-sale products. The counts per silo are large —
41 of mattress's 110, 23 of headphones', 9 of tv's — so ranking them beside real products
handed a shopper "LG G5 OLED (Copy)" as a pick. The §12.10 guess below that the 9 TV ids
are early-access reviews is therefore **wrong**: a member's `products_list` for
`[227,210,197]` returned 119 (118 + the one Early Access LG B6E), not 127.

### 12.3 `table_tool__ratings` rows carry NO `status` field

```json
{"original_id":"1","product_id":"92248","score":null,"suitable":true,"unblurred":false,
 "usage":{"kind":"usage","performance_tooltip_text":"...","is_unscored":false}}
```

There is no `status`, so the "branch `status` before `unblurred`" ordering has nothing to
branch on here: a usage rating is present-and-unblurred, present-and-gated, or absent. The
usage normalizer is therefore a **different function** from the test normalizer, not a
parameterization of it.

**And usage ratings are NOT uniformly gated on the open silos.** §5's "assume gated there"
caveat is falsified for at least mattress: `Side Sleeping` came back `score: 7.7,
unblurred: true` anonymously. TV's usage rows are 100% blurred, as recorded.

### 12.4 `latest_test_bench_id` can name a bench that does not exist yet

**The most consequential correction in this section.** §8 recorded that the page's bench
list is longer than `column_options`'. It goes further: on **5 of 28 silos**
`GLOBALS.static.silo.latest_test_bench_id` names a bench that is absent from
`column_options` **and** absent from the `is_recent` set — apparently a bench under
development:

| silo | `latest_test_bench_id` | `is_recent` set |
|---|---|---|
| air-conditioner | 258 | 39 |
| air-fryer | 265 | 231, 201 |
| laptop | 285 | 242, 198, 194 |
| router | 269 | 253, 235, 224, 195 |
| toaster-oven | 266 | 247, 148 |

Taking it at face value is a real correctness cost: nothing ever matches it, so every slice
on those silos falls to the 30-day legacy TTL instead of the 7-day current-bench one — which
is the named mitigation for the coverage hole that has no other signal (§11.6). **"Current
bench" must be derived as the newest bench that is both rendered by the site and has a
published schema**, not read off `latest_test_bench_id`.

The per-page bench list lives at **`GLOBALS.static.silo`** (verified: `/mouse/tools/table`
→ latest 233, recent `[233,199]`). Its entries are `{id, is_recent, major}` and carry **no
`display_name`** — that comes from `column_options.test_benches[]`, which is also where
`display_name` and the reference `tests[]`/`usages[]` lists live.

### 12.5 Recommendations — discovery, payload, and a row shape with no join key

- **Discovery is `silo_layout.best`**, in a `data-props` blob on the silo landing page: 20
  entries on TV as `{title, url, short, type, group_id, img}`. §10 q9's href scan finds only
  **5 of those 20**, because most list paths are **two segments**
  (`/tv/reviews/best/by-size/65-inch`, `by-usage/video-gaming`, `by-type/oled`). A
  single-segment slug pattern silently rejects the majority.
- **The ranking lives at `page_data.page.recommendation`** in another `data-props` blob:
  `{seasonal_title, introduction, conclusion, product_recommendations[], ...}`. Each pick is
  `{title, subtitle, description, product_id, product, featured_test_results[], ratings[]}`,
  and `product.preferred_scoreset_score` is served anonymously (9 for the Samsung S95H).
- **`featured_test_results[].test` carries no `original_id`** — only `{id, name, kind,
  insider_only, featured_name}`. Those rows therefore **cannot be joined to the schema**, so
  units and precision are unavailable for them and no value may be coerced. They are still
  reported with the same states, using the stub's own name and kind.

### 12.6 `has_insider_access` is inside an HTML-escaped attribute

It appears exactly once on `/tv`, inside `data-props="{&quot;has_insider_access&quot;:false,…}"`.
A raw-text regex over the page **never matches it**, silently returning "the page does not
carry this signal" on every page that does. The probe must unescape `data-props` before
looking.

### 12.7 Review-path prose sits on `group` rows and survives the blur

On the Sony X90L (bench 227, 402 rows), **53 rows carry a `linked_description`** and **all
53 are `kind:"group"`** — RTINGS hangs its commentary on section headers, not on leaves.
All 53 are `unblurred:false`, and the prose is present anyway, confirming §3's "words free,
numbers not" on this path.

Consequence: a normalizer that (correctly) skips `group`/`category` rows as structure will
discard **all** of a review's prose unless it collects it separately. It must never be
attached as a result — a `group` with a `status` is a category header reported as a
measurement.

### 12.8 Search hits carry `published`

`app/search__search_results` results include `"published": true`. It describes the **page**,
not the product's review row, and `kind` may be `page` rather than `product` — so it must not
be used for the blur decision. That still comes from the bench catalog.

### 12.9 The SDK renamed the class

`mcp` 2.x renamed `FastMCP` to `MCPServer`: `from mcp.server.fastmcp import FastMCP` raises
`ModuleNotFoundError` on `mcp==2.1.1`, with a migration message pointing at
`from mcp.server.mcpserver import MCPServer`. Tool schema attributes are snake_case
(`tool.input_schema` / `tool.output_schema`). This is the canonical SDK; the standalone
`fastmcp` 4.x package remains a separate, diverged project.

### 12.10 `published:false` is EARLY ACCESS — an Insider perk, not a permanent blur

**This reverses a documented rule.** §11.2 called `published:false` "a review-in-progress blur
that no membership lifts", inferred from anonymous data alone. RTINGS says the opposite, in
its own words (`/monitor/learn/how-we-test`, fetched 2026-09-04, same copy on router, vpn,
printer, keyboard, blender):

> "Once the results have been approved, **the writer publishes it for Early Access so that
> Insiders who support us can see the data without any text.**"

The catalog corroborates it structurally. Both `published:false` TVs on bench 227 carry an
`/early-access/` URL:

```
136668  LG B6 OLED 2026  page.url = /early-access/tv/reviews/lg/b6-oled-2026
136479  TCL RM9L         page.url = /early-access/tv/reviews/tcl/rm9l
```

So the state means **"the data exists and is published for Insiders, but was withheld from
*this* session"** — a membership is exactly what lifts it. Two consequences:

- **Check `unblurred` BEFORE the `published` flag.** A normalizer that branches on
  `published:false` first returns `review_unpublished, value:null` for a member's real
  early-access values and throws them away. The safe ordering is: any `unblurred:true` row is
  `tested_visible` whatever the flag says; `published:false ∧ unblurred:false` is
  `review_unpublished`; anything else gated is the paywall.
- **`/early-access/` puts the silo in the SECOND path segment.** Deriving it from the first
  yields `"early-access"`, which is not a silo. Live consequence before the fix:
  `rt_product("/early-access/tv/reviews/lg/b6-oled-2026")` fell through to search and
  returned **LG B6 OLED 2016** (product 361, bench 3, a nine-year-old TV) with 125
  `tested_gated` rows and no warning.

`silo.reviews_in_progress_count` is **9** for TV while only **2** early-access products appear
in the recent-bench catalog — which matches the 9 orphan ids of §12.2 numerically, and that
is a coincidence: **resolved 2026-09-06, the 9 are internal "(Copy)" retests (§12.2), and a
member's `products_list` returned 119, not 127.** `catalog/` stays untiered.

### 12.11 `products_list` omits a third of the mattress silo, and those products are UNBLURRED

§12.2 recorded 9 TV orphans, all blurred, and concluded they were structural. The population
is much larger elsewhere and the rows are real data:

| silo | `products_list` (recent benches) | distinct products in `test_results` | orphans |
|---|---|---|---|
| tv | 118 | 127 | 9 (all blurred) |
| mattress | 69 | 109 | **40 (all `unblurred:true`)** |

The 40 mattress orphans carry genuine anonymous measurements (`25.4 cm`, `19.2 cm`, `29.7 cm`
on Thickness; 34 `tested`, 6 `untested`) and are absent from the **all-bench** catalog too, so
this is not a bench-selection artifact. The table-tool JS sends `products_list` with exactly
`{test_bench_ids, named_version, is_admin}` — there is no filter to relax, so the omission is
server-side (discontinued products are the likeliest explanation).

**Consequence:** filing them under `_unassigned` and not reporting them makes a third of the
silo unreachable, and "compare Purple vs Nectar" silently loses one of them — which reads as
"not tested". They must be surfaced as their own labelled group: real values, and no name,
brand or bench, because the catalog is where those live.

### 12.12 An unscored test still ships `score: 0.0`

Mattress `Mattress Type` (`has_score: false`) returns `value: "Foam", score: 0.0`. Passing
the score through reads as "0 out of 10" for something RTINGS never scored. Null the score
when the definition says `has_score: false`.

### 12.13 The category→group hierarchy is POSITIONAL — the API states none

Every one of TV's 69 `category`/`group` rows carries `parent_original_id: null`. Leaves point
at their group, but no group points at a category, so the stated hierarchy is one level deep
and 12 categories look empty. The **list order** carries the missing link:

```
category  oid=31615  parent=None   Brightness
group     oid=4      parent=None   HDR Brightness      <- belongs to Brightness
number    oid=12470  parent=4      Hallway Lights (~1950 cd/m²)
number    oid=141    parent=4      Peak 2% Window
...
```

A `category` row is followed by the groups beneath it, each followed by its own leaves.
Reading `parent_original_id` alone makes `rt_schema("tv")` a flat scramble of 57 groups plus
12 categories reporting `leaf_test_count: 0, children: []` — which is what it returned until
2026-09-04, and it makes the discovery tool nearly useless for finding a test id.

Attaching each top-level group to the most recent preceding `category` reproduces the site's
own navigation exactly: **12 categories** (Brightness → 3 groups, Black Level → 5, Color → 6,
Processing → 4, Game Mode Responsiveness → 8, Motion Handling → 8, …), and a leaf's hierarchy
becomes two levels (`["Brightness", "HDR Brightness"]`). Store it in a **separate derived
field** — the API's own `parent_original_id` really is null, and overwriting it would hide
that.

### 12.14 The tested size lives in the catalog, not in a test

Most silos have no "Size" test at all, so "which 65-inch TV is brightest?" looks unanswerable.
It is not: the catalog row names the SKU RTINGS actually reviewed.

```json
"reviewed_sku_id": "8752",
"variant_skus": [
  {"id": "8751", "name": "XR-55X90L", "variation": "55\""},
  {"id": "8752", "name": "XR-65X90L", "variation": "65\""},   <- the one tested
  {"id": "8753", "name": "XR-75X90L", "variation": "75\""}, ...
]
```

So the tested variant is `variant_skus[reviewed_sku_id].variation`. Exposing it turns a
whole class of question from impossible into a filter (90 of the recent-bench TVs are 65-inch),
and it matters for correctness too: RTINGS' results describe **that** SKU, and other sizes in
the family often differ.

### 12.15 §10 q3 ANSWERED — the session SLIDES; there is no 30-day re-login

Measured 2026-09-04, anonymously, three ways. **RTINGS re-issues `_rtings_session` on every
single response** — HTML GET *and* API POST alike — with a new encrypted value and a fresh
`expires` of exactly 30 days from **that response**:

```
GET  /tv/tools/table          expires=Sun, 04 Oct 2026 15:22:18 GMT
GET  /headphones/tools/table  expires=Sun, 04 Oct 2026 15:22:21 GMT   (+3s, the gap)
POST /api/v2/safe/app/search__search_results   expires=... 15:23:00   value changed
POST /api/v2/safe/app/search__search_results   expires=... 15:23:02   value changed again
now 2026-09-04 15:23:39 -> expires 2026-10-04 15:23:39  (29d 23h from THIS response)
```

So the 30 days is a **sliding idle window**, not a deadline from login: a session lives
indefinitely while it is used and dies 30 days after it stops. `/login` carries **no
"remember me"** — it does not need one, and there is no separate durable auth cookie. (The
CDN sets no cookies at all, confirming the two-session split.)

**This reverses the "never write a rotated cookie back to disk" rule.** That rule was
defensible while the sliding was unmeasured, but it causes the harm it was meant to prevent:
freezing the stored blob at the pasted value means it expires 30 days after the paste
*however much the server is used*, so the user re-pastes monthly because of us, not because
of RTINGS.

The danger it guarded against is real — an anonymous GET also mints a cookie, and a blind
write-back would overwrite the credential with an anonymous one. The answer is proof, not
abstinence: **persist only the jar value that just produced a response with `current_user`
non-null.** An anonymous session cannot satisfy that. `RTINGS_SESSION_COOKIE` (env) cannot
be refreshed, so a stored credential (`rtings-mcp auth`) is the durable path and the env var
is warned about once.

Two consequences worth noting: a cookie value in a fixture or log is stale within one
request, and **presence checks remain useless** (§5) — the jar always holds *a*
`_rtings_session`; only comparison against the configured value means anything.

### 12.16 The API surface is larger than §1 records — and `errors[]` does not mean failure

Scanned every JS bundle referenced by the table, graph, compare, review, best-of and silo
pages (2026-09-04). Beyond the seven queries in §1, three unused ones are real, and one
correction matters more than any of them.

**`errors[]` beside `data` is a PARTIAL-FIELD notice, not a failure.** RTINGS strips
admin-only fields and *says so* while returning a complete payload:

```
POST distribution_tooltip__test  -> {"data": {...full...},
  "errors": ["The field edit_url on an object of type Comparison was hidden due to
              permissions", "...methodology_url...", "...review_notes_url..."]}
```

Treating any `errors[]` as fatal discards that data. And the case `api_error` was originally
documented for is not an `errors[]` case at all — `column_options` with an unknown silo
returns `{"data": {"silo": null}}` and **no errors**, which is `payload_missing`. Measured
blast radius today: **none of the eight queries the server uses returns `errors[]`**, so this
was latent rather than active — but it would have fired the moment RTINGS permission-stripped
a field on a query we do use.

**`app/side_by_side__review`** — bare `{product_id}`, no `variables` wrapper. Returns, on a
**gated** silo, anonymously:

| field | anonymous content |
|---|---|
| `product_score_sets` | 11 usage entries: `score: null` (gated) but **`linked_description` prose on all 11** — "The Sony X90L is good for mixed usage. It looks very good in a bright room thanks to its amazing SDR brightness…" |
| `summaries` | **17 pros/cons blurbs** with the score sets each supports — "Good HDR brightness for impactful highlights." |
| `score_sets` | the **scoring formula**: each usage's component tests/sub-scores with `weight` (Mixed Usage = 25% + 35% + …) |
| `test_results` | 402 rows, **all `value: null`** and — importantly — **no `unblurred` key at all** |
| `user_has_access` | `false` anonymously |

Two things follow. First, this is a **third row shape**: no `unblurred` field, so the
normalizer cannot use its usual branch and this path must never be joined into the table/review
paths. Second, **`user_has_access` is an auth marker inside the API**, which qualifies §1's
"the API carries no auth field" — that remains true of the seven table/review queries, and is
false here. It is per-review and matches the free-preview grant, so it is a candidate signal
for the free tier.

**`distribution_tooltip__test` / `__usage`** — bare `{id, product_id, review_version_id,
use_latest}` where `id` is the test's **internal** id (`38545`), not `original_id`; result at
`data.target`. The distribution itself is withheld anonymously (`histogram: null`, `items:
[]`), but the **test documentation comes through even for a gated test**:

```
help_what: "The TV's maximum luminance, even if only maintained for a short time, of a
            white square covering 100% of the screen."
help_when: "In bright rooms or with bright scenes in HDR that are on-screen for a short time."
help_good: "> 700 cd/m²"
url:       "/tv/tests/picture-quality/hdr-peak-brightness#test_463"
```

`help_good` is RTINGS' own threshold for a good result — useful precisely where the value is
withheld. It costs one request per test and is **not** in `column_options`, so it is
enrichment, not a bulk source.

Also present and unused: `app/product_vue_page__compared_texts`, `app/side_by_side__sbs_item_by_id`,
`app/side_by_side__status_discussions`, and the `app/table_tools_page__*` user-preset queries
(a member feature). Names matching `x__y` in the bundles are often **field** names
(`score_set__original_id`, `test__original_id`, `product_page__early_access`), not queries —
do not probe them blindly.

---

### 12.17 There are TWO best-of templates, and the migration is in progress

Measured 2026-09-05, anonymously.

`rt_recommendations` failed on **every** mattress list — 20 lists discovered, all raising
`recommendations_missing`. The page was fine: HTTP 200, 307 KB, titled "The 5 Best California King
Mattresses of 2026", picks and prose all present.

**Cause: RTINGS is migrating best-of pages onto a second template.**

| | old | new |
| --- | --- | --- |
| marker | `data-vue="RecommendationVuePage"` | no `RecommendationVuePage` |
| picks | `data-props` → `page_data.page.recommendation.product_recommendations[]` | server-rendered HTML |
| Vue parts | one monolithic component | islands: `RecommendationPagePrices`, `BookmarkControls`, `DistributionTooltip` |
| bundle | `recommendation-page-*.js` | `recommendation-page-**static**-*.js` |

**Re-measured 2026-09-08 across ALL 28 silos** (`rtings-mcp drift`, now the automated gate —
`docs/recommendation-template-snapshot.json`): **28 / 28 extract, 26 props / 2 static**. The split
is unchanged from the 14-silo sample below: mattress and running-shoes are server-rendered, the
other 26 are `RecommendationVuePage`. This is no longer a manual checklist step.

**Scope (14 silos sampled, one best-of list each):** only **mattress** and **running-shoes** are on
the new template. tv, headphones, monitor, soundbar, camera, laptop, blender, refrigerator,
air-conditioner, vpn, air-purifier and toaster-oven are still on the old one. All four mattress
lists checked (`mattress`, `king`, `queen`, `california-king`) are migrated, so it is per-silo, not
per-list.

**It does NOT track silo age** — refrigerator is the newest silo in the catalog (first published
2025-10) and still renders the old template, while mattress (2025-07) has moved. So this is a
rollout, and **more silos will migrate silently**. Same shape as the paywall map: nothing announces
it, and the only signal is the served page changing.

**Still no API (re-confirms §11.7).** The new page's only bundle is
`recommendation-page-static-DNkErhA8.js`, **3,103 bytes, with zero `/api/v2/safe/` references**. The
picks are not fetched client-side. Page extraction remains the only route.

**The new template's anchors** (5 of each for 5 picks, verified on both migrated silos):

- pick container `<li class="recommendation_vue_page-pr">`; title in `<h2 class="… e-page_section_title">`
- product name + review URL in `<a class="recommendation_vue_page-pr-name t-h3" href=…>`
- **ranked `product_id` from the island props**: `RecommendationPagePrices` carries
  `{"product_id": "105249", "sku_ids": [...], "index": 0}` — `index` is the rank
- reasoning in `recommendation_vue_page-pr-description` (wrapped in an `e-rich_content` div, which
  the old template's `description` is not — unwrap it or the two shapes differ)
- page intro in `recommendation_vue_page-intro e-rich_content`; update stamp in a
  **`<span>`** `recommendation_vue_page-hero-update`, rendered as "Updated Aug 26, 2026 at 12:45 pm"
- featured strip: `recommendation_featured_list-item`, with `-item-name`, then EITHER a
  `score_box-value` (a 0–10 score) OR an `-item-value` (a display string)

**IMPORTANT: the featured tooltip's `target_id` is NOT the schema `original_id`.** Its
`DistributionTooltip` props give `target_label` and `target_type` (`usage` vs `test`, the only way to
tell them apart here), but for "Side Sleeping" `target_id` is **38309** while the schema's usage id is
**36553**. Emitting it as an `original_id` would be a confidently wrong join key; a null id with a
real name is the honest pair.

**Resolved 2026-09-08: the join is (`target_label`, `target_type`), not `target_id`.** Measured on
the live mattress list: **0 of 9 `target_id`s are schema ids** (they form their own block,
38303–38311 + 40163 + 40192), while every one of the 9 `target_label`s resolves against the schema
by name once `target_type` picks the namespace — which matters, because "Cooling" is *both* a test
(26886) and a usage (33364) and the label alone is ambiguous. `_featured_ratings` now does for
usages what `_featured_results` already did for tests: a name unique on the silo gets its real
`original_id`, a repeated one keeps a null id and lists the candidates. Verified live: Side
Sleeping → 36553, Back Sleeping → 36554, Stomach Sleeping → 36555, Cooling → 33364.

**Also resolved 2026-09-08: "Notable Mentions" IS extractable on the server-rendered template.**
It is a real section (`<a id="mentions">`, `<h2>Notable Mentions</h2>`, a
`recommendation_vue_page-mentions` block, and an entry in the page's own table of contents), and
`recommendation_mentions` came back `[]` only because nothing parsed it. Each item is
`<strong>Name:&nbsp;</strong>` + a rich-content span + a "See our review" link; `<li>` is often
unclosed here too, so items are cut at the next `<li`. Both templates now emit the props shape
(`{description, sku, product:{fullname, page:{url}}}`), and `rt_recommendations` surfaces it as
`mentions` — it had reached no caller on either template. Verified live: 2 on the static mattress
list, 5 on the props TV list.

Both migrated silos are **open** silos, so no blurred sample of this template was observed and its
blur marker (if any) is unknown. A featured item that renders neither a score nor a value is
therefore `unknown_row_status`, never `tested_gated` — claiming a paywall nobody measured is the
project's core failure mode.

### 12.18 The Best nav carries per-BRAND pages one segment above `/best/`

Measured 2026-09-08, anonymously, on `/tv`.

`silo_layout.best` has 20 entries on TV and they are **two URL shapes**, not one:

| shape | count | example | `type` | `group_id` |
| --- | --- | --- | --- | --- |
| `/{silo}/reviews/best/{slug}` | 17 | `/tv/reviews/best/by-size/65-inch` | `Recommendation` | 1–48 |
| `/{silo}/reviews/{brand}` | 3 | `/tv/reviews/tcl` ("Best TCL TVs") | `Recommendation` | 50 |

**RTINGS' own `type` does not distinguish them** — both are `Recommendation` — so the URL shape
is the only discriminator. `group_id: 50` correlates but is not a rule: `/tv/reviews/best/roku`
is also 50 while living under `/best/`.

Discovery kept only the `/best/` shape, so `list="tcl"` guessed `/tv/reviews/best/tcl` and
reported `unknown_list` for a page that exists. Both shapes are now kept, tagged `kind`
(`"best"` / `"brand"`), and `/{silo}/reviews/{slug}` is tried when no template matched the
first — RTINGS answers an unknown best-of slug with a 200 landing page, not a 404, so
"the GET succeeded" is not the same as "the list is there". The brand form is offered **only
for a single-segment slug**; see §14.7 for why that is a paywall guard and not tidiness.

Verified live: `/tv/reviews/tcl` extracts through the ordinary props template — picks
`TCL X11L`, `TCL QM8K`.

### 12.19 `learn` articles: a second page-extraction path, prose only

Measured 2026-09-08, anonymously.

`/{silo}/learn/{slug}` pages answer what no measurement can ("does Sony sell a bigger OLED this
year" → `/tv/learn/2026-lineup`, "2026 TV Lineup: The Year Of RGB Mini LED"). They are **not
discoverable from `silo_layout`** — it has exactly three keys, `best`, `popular` and `tools`,
and no `learn` — but RTINGS' own search index carries them: `rt_search("2026 TV lineup")`
returns `/tv/learn/2026-lineup` as hit #1, which is why `rt_article` takes the `url` search
hands back rather than trying to enumerate them.

The body is at `page.article`: `title`, `introduction`, `text` (26,838 chars on the 2026
lineup), `text_with_anchors` (27,567 — the same prose carrying the headings the table of
contents links to, and what `section=` is sliced out of), `toc_items` (nested `name`/`url`),
`latest_update_date`, `meta_description`; the authors are at `page.authors[].name`.

The headings in `text_with_anchors` carry **no `id` attribute** — the anchor is a slugified
form of the heading text — so sectioning matches on heading TEXT, not on the anchor.

Nothing on these pages is gated: no `unblurred` bit, no test row, no `insider_only`. They are
cached at `ANONYMOUS` like the best-of index. `/{silo}/learn` with no slug **redirects to
`/research`**, and an unknown slug is answered with some other page rather than a 404 — so "no
`page.article` object" is the only honest signal that the page asked for does not exist.

### 12.23 q7 ANSWERED — a product is on exactly ONE bench, so the recent set is a partition

Measured 2026-09-08. §10 q7 asked whether minor benches (v2.0.1 / v2.1 / v2.2) are **rescored or
additive**, to decide whether the default comparable population is one bench or the set. The data
answers it before the scoring question arises: **the catalogs are disjoint.**

| silo | recent benches (with a schema) | catalog sizes | products in common |
| --- | --- | --- | --- |
| tv | 227, 210, 197 | 98 / 1 / 20 | **0** |
| laptop | 242, 194 | 41 / 40 | **0** |
| mouse | 233, 199 | 110 / 85 | **0** |
| monitor | 238, 221 | 117 / 3 | **0** |

**6 bench pairs across 4 silos, 0 sharing a single product.** headphones, soundbar and mattress
render one recent bench with a published schema, so they have no pair at all.

Confirmed on a member session too: across bench 227 and 210, with three shared usages and every
score unblurred, there is **no (product, usage) scored on both** — because there is no product on
both. "Rescored vs additive" is therefore unanswerable *and moot*: no cross-bench comparison exists
in the data to be right or wrong about.

**So the default population is the recent SET, partitioned by bench** — which is what the server
already does. Grouping by bench loses nothing and duplicates nothing, each product falls in exactly
one group, and `limit`/`offset` within a group is the honest paging unit. The rule "rank and
compare within a `test_bench`" is not a conservative choice against a cost; it is the only thing
the data supports.

### 12.22 q6 — sustained volume, a second passive sample: still no limit, no rate headers

Measured 2026-09-08 from `telemetry/requests.jsonl` on a scratch cache, over the day's research
(enforcement re-scan, the 28-silo drift check, the usage sweep, the legacy-bench sweep):

| | |
| --- | --- |
| requests | **193** |
| non-200 | **0** |
| `Retry-After` seen | **0** |
| `x-ratelimit-*` ever present | **none** |
| `x-cache` / `age` ever present | **none** (origin, not a CDN edge, on these paths) |
| window | 843 s → **13.7 req/min** sustained |

Consistent with the first sample (~140 requests over ~3.5 min). RTINGS ships **no rate-limit
headers at all** on these endpoints, so there is nothing to honour and nothing to read: the
server's own token bucket is the only limiter, and `Retry-After` handling remains a
never-exercised safety branch (covered by tests, not by observation). q6 stays answered
**passively** — this is not evidence of where the limit is, only that ~14 req/min for 14 minutes
does not reach it.

### 12.2b The 9 orphan TV products are GONE — the population is transient

Re-measured 2026-09-08, anonymously. §12.2 recorded 9 product ids that returned `test_results`
rows while appearing in no `products_list`, and the standing reading was "almost certainly reviews
in progress that the catalog filters out" — an inference, not a measurement.

Re-run across all **14** TV benches with a published schema, 2 leaf tests each: **551 ids in
`test_results`, 551 in the catalog, 0 orphans.** Four days on, the set is empty and the two
populations match exactly.

That does not prove the mechanism, but it rules out the alternative that mattered: a permanently
uncatalogued population would still be uncatalogued. A set that empties on its own is what a
review-in-progress does. The server's handling is unchanged and stays right either way — an
uncatalogued id is served as its own `coverage: "uncatalogued"` group with no invented name.

### 12.20 Usage ratings gate EXACTLY with the enforcement map — all 28 silos, no partials

Measured 2026-09-08, anonymously (fresh scratch cache, no cookie), 2 top-level usages per silo on
each silo's current bench, `published:false` products excluded. §12.3 had falsified "assume usage
ratings are gated everywhere" from a single mattress observation (`Side Sleeping` = 7.7,
unblurred); the open question was whether that generalised. It does, and the result is **perfectly
bimodal over 3,006 rows**:

| | silos | rows sampled | unblurred |
| --- | --- | --- | --- |
| the 12 that enforce | 12 | 2,104 | **0 (0.0%)** |
| the 16 metered | 15 + keyboard-switch | 902 | **902 (100%)** |

Not one silo came back partial, and not one row contradicted its silo. `keyboard-switch` defines
**no usages at all** on its current bench, which is why it is neither — an absence, not a gate.

So the usage surface needs no separate map: `scores_available.usage_ratings` and the insider-test
boundary are the same boundary, derived per (silo, bench) from observed `unblurred` exactly as
before. This is a measurement of today's business decisions and it can move like any other — the
release-gate scan covers the test surface, and this table is the dated baseline for the usage one.

### 12.21 q16 ANSWERED — and a counterexample to the blur rule, on a legacy bench

Measured 2026-09-08, anonymously. §10 q16 asked whether enforcement holds on **legacy benches**,
on the **review path**, and for **non-leaf kinds** — both earlier sweeps covered only each silo's
current bench and its first 40–50 `number`/`word` leaves.

**Legacy benches (10 silos, oldest bench with a published schema):** the boundary follows the
silo, with one exception.

| silo | legacy bench | rows | unblurred | current |
| --- | --- | --- | --- | --- |
| tv | 1 | 96 | 0% | enforces ✓ |
| monitor | 8 | 292 | 0% | enforces ✓ |
| mouse | 84 | 910 | 0% | enforces ✓ |
| soundbar | 86 | 390 | 0% | enforces ✓ |
| printer | 82 | 290 | 0% | enforces ✓ |
| **headphones** | **4** | **119** | **13.4%** | enforces ✗ |
| vacuum / camera / air-purifier | 34 / 120 / 254 | 128 | 100% | metered ✓ |

**Non-leaf kinds are not a separate mechanism.** On tv, `picture` (384 rows) and `video` (288) are
0% unblurred; on headphones, `graph` (808 rows) is 0%; on the metered mattress, `picture`, `graph`
and `video` are 100%. The boundary is kind-agnostic. (A `graph` test's table row being blurred is
not the curve being withheld — `rt_graph` serves curves anonymously; the row's cell and the
`graph_data_url` payload are different surfaces.)

**The headphones exception is per TEST, and that breaks the stated invariant.** All 16 unblurred
rows are one test:

| test | kind | unblurred | |
| --- | --- | --- | --- |
| 287 `Transducer` | word | **16 / 16** | `insider_only: true`, served to everyone |
| 307 `Weight`, 539 `Clamping Force`, 292 `Call/Music Control`, 294 `Volume Control`, 652–654 | number/word | 0 / 16 (0 / 13) | withheld |

Every one of the 16 products is "mixed" at exactly 1 of 8 — the signature of a per-**test**
exception, not the per-**product** unblurring a gift link or a preview produces. Re-checked across
headphones/tv/monitor on both their current and legacy benches, 25 insider tests each: **this is
the only case in 6 bench-samples.**

So the rule recorded as an invariant —
`blurred ⟺ published:false ∨ (insider_only ∧ silo enforces)` — is **not exact**, and
"gating within a silo is per-product, never per-test" is **falsified** as stated. Both are good
approximations with at least one known counterexample on a legacy bench.

**This changes nothing in the server, and that is the point.** The boundary is derived from
OBSERVED `unblurred` per row, never from the `insider_only` flag and never from a map
(`SPEC.md` §5) — so `Transducer` is simply reported as `tested_visible`, which is what it is. A
build that had trusted the flag would have reported a value RTINGS served as `tested_gated`, which
is the project's core failure mode pointed the other way.

**Consequence for the release gate:** `enforces_paywall` is derived as `ratio == 0.0` over a
sample of insider tests, so a bench whose sample contains such a test reads as `partial` rather
than `ENFORCES`. No current bench does today (the 2026-09-08 scan is 12/16 with no partials), and
the scan only ever samples current benches — but a future `partial` is to be investigated per-test
before it is believed to be a paywall change.

## 13. Member session, 2026-09-06 — Phase 0 measured

A membership was bought 2026-09-06 and signed in through `rt_sign_in` (headed browser, cookie
captured from the jar, validated, stored). Everything below was measured in that one session,
through the project's own transport, into a **scratch cache dir** so no member-derived bytes
reached the real cache. No fixture here carries a name, an email or a cookie.

### 13.1 §10 q1 ANSWERED — a member cookie DOES flip `unblurred` on the API

The blocking unknown, and it succeeded on the first rung: no header ladder, no `page_body`
fallback. One `table_tool__test_results`, silo `tv`, bench `227`, the first 6 `insider_only`
`number` tests (`12470`, `12471`, `12472`, `141`, `461`, `462`) x 98 products = 588 rows.

| session | rows | `status:"tested"` | `unblurred:true` | non-null `value` |
|---|---|---|---|---|
| member | 588 | 588 | **588** | **588** |
| anonymous | 588 | 588 | **0** | **0** |

Identical call, same process, minutes apart. Sample member row: `original_id 141`,
`product_id 92248`, `value "1873"`, `score 8.8`. **tv is one of the 12 enforcing silos**
(§11.1), so this is the gate opening, not an open silo answering.

Consequence: the entire tier mechanism — `cache_tier` in the filename, demand/write rules,
write-time demotion — is now justified by measurement rather than assumption. `RTINGS_MEMBER_MODE`
is a flag, not a migration.

### 13.2 Capture (a) — the logged-in `GLOBALS.session`, and the member/free boundary

The shape, redacted (`<str:n>` = a string of that length, never its value):

- `current_user` — `is_insider: true`, `insider_status: <str:10>`, `insider_end_at: <str:17>`,
  `is_confirmed: true`, `is_admin: false`, `paywall_test_account: null`, `email`/`username`/`id`,
  and a `user_mailinglists` array.
- `access_state` — `access_level: 3`, `preview_level: 2`, `access_limit: null`,
  `previewed_products: []`.
- page markers — `user_is_insider: true`, `has_insider_access: true`,
  **`membership_type: "yearly"`**, **`user_type: "Insider"`** (anonymously these read `"no plan"`
  and `"Visitor"`, so both flip, and `membership_type` names the billing period rather than the
  tier — do not parse it for entitlement).

**`current_user.is_insider` is the field that separates `member` from `free`** — a literal
boolean naming exactly the distinction, inside the object the probe already parses. Until now
the classifier guessed from the analytics marker, `has_insider_access`, and `access_level >
preview_level`, and called that boundary "provisional"; it no longer has to. The three older
signals all agreed with it here, so they remain as corroboration.

`access_limit: null` and an empty `previewed_products` confirm a member has **no meter** — the
preview budget is a free-account mechanism only.

### 13.3 Capture (d) — the browser headers make NO difference

The same `test_results` POST, member cookie, with and without `Origin` / `Referer` /
`Sec-Fetch-*`:

| request | rows | `unblurred` |
|---|---|---|
| with the browser headers | 98 | 98 |
| without them | 98 | 98 |

So there is **no server-side origin validation** on `/api/v2/safe/` — the one "unprovable"
Phase-0 risk is now measured, and it is absent. The headers stay (they cost nothing and keep the
request shaped like the real client's), but nothing depends on them.

### 13.4 Capture (f) CONFIRMED — the probe page is not CloudFront-cached

`GET /tv/tools/table` with the cookie: `x-cache: Miss from cloudfront`,
`cache-control: max-age=0, private, must-revalidate`, no `age`. As predicted anonymously, so the
`should_demote` guard against a cache-HIT probe should never fire in practice. It stays as a
guard, not a design assumption.

### 13.5 Capture (p) ANSWERED — `user_has_access` DOES flip for a member

`app/side_by_side__review`, anonymous, is `false` on tv and `true` on mattress — which looked like
it tracked silo enforcement rather than membership (§12.16 called that a lead, not a fact). With
the member cookie it is **`true` on tv**, with 11 of 11 usage score sets scored. So it tracks
**both**, and it is a real auth marker *inside* the API — the exception §1's "no auth field in the
response" already noted, now measured in the member direction too.

This is a second, independent confirmation of §13.1. It does **not** change the rule that
`verdicts/` demotion keys on the usage scores rather than on this flag: the concern that motivated
that rule was a flag which never flips for a member (which would deadlock demotion), and that
concern is now retired rather than the rule being wrong. Keying on the scores stays correct and
depends on nothing unmeasured.

### 13.6 Capture (g) ANSWERED — curves are identical, so `graphs/` may stay untiered

`graph_tool__product_graph_data_url` for tv product `39008`, test `13907` (`kind:"graph"`)
returned **the same CDN path** (`graph-pqeotf.json`) for the member and for anonymous, and the
fetched curve JSON was **byte-identical**. The design assumption that `graphs/` needs no
`cache_tier` is confirmed, and the CDN still needs no cookie.

### 13.8 The review path on a LEGACY bench unblurs for a member (the other half of q16)

Measured 2026-09-08 on the real membership, scratch cache. `rt_product` on a bench-1 TV review
(Panasonic S60, the oldest bench tv publishes a schema for) returned **8 / 8 rows
`tested_visible`**, with `session: member` and `data_tier: unblurred`.

So the membership lifts the paywall on the **review** path and on **legacy** benches, not only on
the table path and the current bench. Together with §12.21 (enforcement holds on legacy benches
anonymously, one per-test exception) and §12.20 (usage ratings follow the same map), q16 is
answered on every axis it named: legacy benches, the review path, and non-leaf kinds.

**This measurement was blocked by a real defect, found in the attempt** (see
`docs/rules/tools-and-responses.md`, 2026-09-08): `_product_from_url` scanned only the RECENT
benches, so every legacy-bench review was unresolvable by URL — while the same product resolved
by its numeric id — under an error that read as "no such product". Fixed by widening the
exact-match scan to every bench with a published schema, after the recent set misses.

### 13.7 What a membership CANNOT settle — q2 and capture (o) need a FREE account

Both remaining questions are about the metered preview, and a member has no meter:
`access_limit: null`, `previewed_products: []`, and nothing to spend. **q2** (the meter's unit —
per product, per session, per day) and **capture (o)** (is `app/side_by_side__review` metered?)
therefore need a **free, logged-in** account, not this one. The budget control is built and
enforced; only the constant is unknown, and it stays unknown.

Capture (c) — "the exact `test_results` body a logged-in front end sends" — is answered by
construction: the body this project already sends (§1) returned fully unblurred member data in
§13.1, so it needs no adjustment for the member case.

## 14. The anonymous preview meter, 2026-09-07 — the "16 open silos" are METERED

Measured anonymously with `curl`, fresh cookie jars, 2 s spacing, no credential. Every number
below reproduces on a second jar. This section corrects §11.1's framing: the split is real, but
the 16 silos are not *open* — they give anonymous a **three-product preview budget**, and while
it is unspent the table tool serves everything. Every scan so far ran on a fresh jar, i.e. with
the budget unspent.

### 14.1 `access_state` is per SILO, not per session

The same anonymous jar reads different `access_state` on different pages:

| Page | `access_level` | `access_limit` |
|---|---|---|
| `/`, `/login`, `/signup`, a 404, and the landing page of each of the 16 "open" silos | 2 | 3 |
| the landing page of each of the 12 enforcing silos | 1 | null |

`preview_level` is 2 everywhere. Swept 2026-09-07 over all 28 `/{silo}` landing pages: level 1
on **exactly** the 12 silos §11.1 calls enforcing (headphones, keyboard, laptop, monitor, mouse,
printer, projector, robot-vacuum, router, soundbar, speaker, tv), level 2 on the other 16. So
one anonymous GET of `/{silo}` is a per-silo enforcement signal that agrees with the 41,944-row
sweep — cheaper than a `test_results` fetch, and a candidate cross-check for the release scan.
§5's "anonymous is `access_level 1`" was measured on a TV page and is TV-specific.

### 14.2 The meter: one unit per PRODUCT, spent by the review page's HTML GET

`previewed_products` (and the `product-previews` cookie behind it, §14.4) grows by one product id
per **distinct product review page** fetched as HTML (`/{silo}/reviews/{brand}/{model}`):

- mattress: `[]` → Puffy Monarch → `[141499]` → SweetNight CoolNest → `[113330, 141499]`;
  re-fetching Monarch leaves it at two. Camera: the same, `[36568]`, `[33705, 36568]`, …
- The list is **global** (the TV landing page shows the mattress ids) while `access_limit` is
  per silo (§14.1).
- After the third distinct product, the **fourth** review GET drops `access_level` to **1** on the
  metered silos and the list stops growing (mattress and camera both: `[3 ids]`, level 1).
- Pages that do **not** count: `/{silo}`, `/{silo}/tools/table`, `/{silo}/tools/compare`, the
  best-of pages (`/{silo}/reviews/best/...`), and — decisively — **`POST
  app/product_vue_page__page_body` does not count** (fresh jar, Purple mattress: list stays
  empty), nor does `POST app/side_by_side__review` (fresh jar, Nectar: list stays empty,
  `user_has_access: true`).

### 14.3 Spent meter ⇒ the TABLE path blurs on the metered silo

With the budget spent (jar at level 1) the two table surfaces blur to exactly the previewed
products:

| Surface, mattress bench 236 | fresh jar | spent jar |
|---|---|---|
| `table_tool__test_results`, 5 insider leaf tests, tested rows | 405 / 405 unblurred | **15 / 405** (3 products × 5 tests) |
| `table_tool__ratings`, 3 usages | 207 / 207 | **9 / 207** (3 × 3) |
| camera bench 278, 5 insider tests | 375 / 375 | **15 / 375** |

So on these silos blur is `insider_only ∧ (access_level < preview_level)`, and the level is a
function of the meter. §11.1's rule "blurred ⇔ published:false ∨ (insider_only ∧ silo
enforces)" holds for a fresh session and is incomplete for a spent one. The 12 "enforcing"
silos are simply the ones where anonymous has no budget (`access_limit: null`, level 1 from
the start).

### 14.4 The state lives in a plain cookie, not the Rails session

The jar after three previews carries `product-previews=109851-141499-113330` (not HttpOnly).
Splitting that jar:

- session cookie kept, `product-previews` dropped → landing reads level 2, `previewed_products: []`;
- `product-previews` kept, `_rtings_session` dropped → level 1, the three ids.

So the meter is entirely the cookie. A client that does not persist cookies starts every
process unspent — which is what a browser's private window does, and what this server does
by construction (wafer's jar is in-memory; the only cookie ever written to disk is a **proven
logged-in** `_rtings_session`).

**What this means for the server.** It never fetches a product review as HTML — `rt_product`
is the `page_body` POST, which does not count — so its own jar never spends the budget, and the
"full" it observes on 16 silos is exactly what RTINGS serves to a session that has read fewer
than three reviews. Three rules follow, all about *not* engineering around the meter:

1. **Never add a review-HTML fetch path.** One GET per product spends the budget, and the fourth
   blurs the table tool for the whole silo, for the rest of the process. (The sign-in browser
   window is the human's, not the server's jar.)
2. **Never strip, reset or rewrite `product-previews`, and never persist it.** Dropping it is a
   bypass; persisting an anonymous cookie is the credential-hygiene rule's other half.
3. **Describe the 16 honestly**: "three-review preview budget, and an API client that never
   opens a review page keeps it unspent" — not "open", not "no paywall".

### 14.5 What this settles and what still needs a FREE account

- **§10 q2 (the meter's unit) is ANSWERED: per product**, incremented by the review page HTML
  GET, idempotent per product, shared across silos, limit 3 for anonymous on the 16 metered
  silos and none on the 12. Measured anonymously, which §13.7 thought impossible — the
  anonymous tier *has* a meter on 16 silos; §5 saw `access_limit: null` because it looked at TV.
- **Capture (o) is ANSWERED: `side_by_side__review` is not metered.** Neither is `page_body`.
- **`rt_product`'s preview budget guards an endpoint that does not spend.** Kept as a guard —
  a free account could in principle be metered on the API — but the measured trigger is the
  HTML page, which the server never requests.
- Still open, free account only: whether a logged-in free account carries the same cookie
  meter (and with what `access_limit` on the 12 gated silos), and whether its
  `previewed_products` moves server-side once there is a user to attach it to.

### 14.6 A FREE account, measured — it is anonymous with a username

Signed in a free (non-Insider) account in a browser on 2026-09-07 and walked the same pages:

- `current_user` is an object with `is_insider: false` (keys: `id, username, is_admin,
  is_insider, is_confirmed, insider_end_at, email, user_mailinglists, paywall_test_account,
  insider_status`), so the probe's `free` classification is confirmed against a real one.
- **Gated silo (tv): `access_level 1, access_limit null`, `previewed_products: []`** — identical
  to anonymous. Four TV review pages later, still level 1, still empty. `table_tool__test_results`
  on bench 227: **0 / 490** tested insider rows unblurred. A free account has **no** preview
  budget on the 12 gated silos.
- **Metered silo (mattress): the same three-product cookie meter as anonymous** — level 2,
  limit 3, `[141499]`, `[113330, 141499]`, three ids, then level 1 on the fourth product, and
  the table blurs to 15 / 405. Dropping the `product-previews` cookie while keeping the
  logged-in session resets it to level 2 with an empty list: the meter is **not** attached to
  the account.
- The ladder RTINGS' bundle implied (1 anonymous, 2 free, 3 insider) is therefore not what the
  server enforces: for data access there are two tiers, **not-Insider** and **Insider**, and
  "free" is a mailing-list signup. §13.7's remaining question is closed.

**Consequences for the server.** `rt_product`'s preview gate guarded a cost nobody is charged:
`page_body` does not increment the meter (§14.2) and a free account has no budget to spend.
`preview_would_spend` now arms only when the probe reports a non-null `access_limit`, so a
`free` session behaves like `anonymous` (which it is) and the guard re-arms by itself if
RTINGS ever meters the API. The `free` cache tier on `reviews/` stays as a harmless label —
its bytes are anonymous bytes.

### 14.7 Two more HTML pages measured against the meter, 2026-09-08: neither spends

Two page shapes were added to the server (the brand best-of page, §12.18, and the `learn`
article, §12.19), and both are HTML GETs, so each was measured against the meter before it
shipped. Method: a **fresh** anonymous jar, the meter read from a **metered** silo's landing
page (`/mattress/tools/table` — `tv` reports `access_limit: null` and would have shown
nothing either way), then re-read after each fetch.

| after | `access_level` | `access_limit` | `previewed_products` |
| --- | --- | --- | --- |
| fresh jar | 2 | 3 | `[]` |
| `GET /tv/reviews/tcl` (brand best-of) | 2 | 3 | `[]` |
| `GET /tv/learn/2026-lineup` (article) | 2 | 3 | `[]` |

Consistent with §14.2: the unit is a **product review page**, `/{silo}/reviews/{brand}/{model}`,
and neither shape is one — a brand page is one segment where a review is two, and `/learn/` is
a different branch entirely. Both are guarded structurally rather than by intent:
`recommendation_paths` offers the one-segment brand form **only for a slug with no slash**, and
`rt_article` accepts `/{silo}/learn/{slug}` and nothing else. Neither guard can be satisfied by
a review URL, whatever a caller passes.
