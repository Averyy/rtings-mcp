# Tool behaviour and response-shaping rules

> Moved verbatim out of `CLAUDE.md` on 2026-09-07 so the always-loaded file stays short.
> These are the project's hard-won rules: each one was forced by a measurement or a defect,
> and the date/`RECON.md` reference beside it says which. Read the file for the area you are
> touching before changing it; add new rules here, one bullet each, with the date and the
> evidence.

- **Best-of discovery is `silo_layout.best` in the landing page's `data-props`, not an href
  scan** — 20 lists on TV vs 5, because most list paths are **two segments**
  (`by-size/65-inch`). A single-segment slug pattern silently rejects the majority. A slug is
  validated then **flattened** into one filename component, never used as a path segment.
- **IMPORTANT: a test's 0-10 SCORE is not a stand-in for its value.** A usage rating has
  only a score; a test has a value *and* a score. Falling back to the score when a test's
  value is null makes `peak_brightness < 10` match a TV whose value is unknown because its
  *score* is 8.5 — confidently wrong rows with no signal, which is worse than the empty
  result the filter guard exists for. One `_comparable(entry, kind)` helper decides what a
  filter or sort may compare, and every call site goes through it: `value` for a test,
  `score` for a usage, `None` otherwise (including a `tested_visible` row that is genuinely
  empty). It also drives the "is this field populated?" census, or a null-valued visible row
  makes the guard think the field is usable.
- **Paginate WITHIN a group, never across benches.** A group is one comparable bench
  population, so a global sort-then-slice can return zero rows from a widened bench that
  matched — and it ranks across the very boundary `rank_scope: within_bench` exists to keep.
  `limit`/`offset` apply per group; each group reports its own `matched`.
- **IMPORTANT: a field a filter or sort NAMES must be FETCHED (added 2026-09-05).** Predicates run
  over the rows actually served, so a field nobody projected is absent from every row and the
  filter quietly does nothing. Measured live on **mattress — an OPEN silo, nothing gated**:
  `filters={"Thickness": ">1"}` returned all 69 products with "no value is populated", when the
  truth was that the test had never been requested. That is the project's core failure mode wearing
  a different hat, reachable where the paywall is not involved at all. `_fields_to_fetch` unions
  filter/sort fields into the projection (strip the `+`/`-` sort prefix first). And **a name works
  wherever an `original_id` works** — `filters`/`sort` always took either, so `tests=` rejecting a
  name made the documented remedy fail with `unknown_test` on the string the filter had just
  accepted.
- **"Not applied" has THREE reasons and they must not be collapsed:** *gated* (buy a membership),
  *absent from the bench* (call `rt_schema`), *genuinely empty* (RTINGS published nothing). Reporting
  the middle one as "no value is populated" sends the caller to buy a membership they do not need.
- **IMPORTANT: a row the server cannot compare sorts LAST in both directions.** `reverse=True` flips
  the whole sort key, so a fixed "missing" rank put gated and untested rows at the TOP of every
  descending sort — "the brightest TVs" led by TVs whose brightness is unknown. Pre-flip the presence
  flag; do not rely on the tuple ordering alone.
- **IMPORTANT: Early-Access status comes from the ROW's own slice envelope, never today's catalog.**
  The catalog refreshes on its own 3-day clock, so a review published on day 4 makes a day-1 blurred
  slice read as `tested_gated` — "buy a membership" for a review RTINGS had not finished. `rt_product`
  always did this; `rt_ratings` did not until 2026-09-05. Same rule for `_verdicts_only`, which
  defaulted `unpublished=False` and so reported an Early-Access verdict as paywalled.
- **A `kind:"graph"` test has no scalar on EITHER path.** The review path said so; the table path did
  not, so a curve test requested through `rt_ratings` came back `tested_visible, value:null` —
  "measured, and the answer is nothing", when the answer is a curve. `rt_schema` lists graph tests
  beside real ones, so this needs no unusual input to reach.
- **IMPORTANT: a negative is a well-formed EMPTY payload; an unrecognised shape is DRIFT.** Measured
  2026-09-04: a genuine "no curve" is `{"data":{"product":{"review":{"test_results":[]}}}}`. A blanket
  `except RtingsError: return None` around `_dig` therefore turned any transient shape change into a
  written `graph_not_available` file — a confident structural claim, cached three days, from a
  transport blip. Negatives get cached, so only an explicit empty may produce one.
- **Filtering and sorting must not silently use gated fields.** Anonymously every gated
  value is `null`, so a filter on one matches **zero** products — and "0 results" reads as
  *no product qualifies* rather than *you cannot see it*. Check the **served rows**, never
  the silo: a field that resolves to `tested_gated` for the population is **not applied**,
  and the envelope names it. The default sort is a public catalog field, never a gated score.
- **Never return the raw payload.** `column_options` is ~357 KB, a full review ~442 KB, a
  graph up to ~361 KB (measured 2026-09-05 across silos: speaker "Raw Frequency Response
  Graph" 361 KB, soundbar 335 KB, headphones 190 KB — TV's ~74 KB is the SMALL end, and a
  256 KB CDN cap sized from it turned real published curves into `fetch_failed`).
  `rt_schema` bounds by group; `rt_graph` resamples to ~200 points
  (`full=true` opts in); `rt_ratings` defaults `limit=10` per group. `rt_product` is the
  per-product drilldown and **is itself bounded** — a current-bench review is 402 rows /
  437 KB, so default to leaf value kinds (`number`/`word`), each row carrying a `hierarchy`
  breadcrumb (a flat list, NOT nested — see `TODO.md`), with prose
  (`linked_description`), media and verdicts opt-in. `group`/`category` rows are **structure,
  not results** — they never get a `status` or a value.
- **IMPORTANT: measure the payload ON THE WIRE, not the dict you built.** The services return
  lean dicts, but the output model re-adds every declared field as `null` when it serializes —
  one `rt_product` response was 55,857 bytes of content and **113,406** delivered, and a
  single gated row 424 bytes of which 256 were nulls. Row models drop their null optionals;
  **`value`, `gated` and `status` are exempt** because "a gated value is null, never absent"
  — and **`score` is exempt on `RatingOut`/`VerdictOut` only**, where a usage rating has *only*
  a score so it plays `value`'s role; it stays droppable on `ValueOut`, where it is secondary
  and null on most rows. The exemption is a per-model `ALWAYS_PRESENT`, not one global set,
  is the whole promise, and envelope models are exempt entirely so `out["error"] is None`
  cannot become a `KeyError`.
- **Resample by SELECTING shipped points, never by interpolating.** Decimation or LTTB over the
  `(x,y)` pairs as shipped; never average, smooth, or synthesize a point. An interpolated value is a
  number RTINGS did not measure, which is the derivation prohibition by another name. For the same
  reason do not return a curve's `y_range`/max as a headline figure — a peak-luminance maximum reads
  exactly like the gated scalar it summarizes; if bounds are returned at all, label them as
  `axis_bounds_of_served_points`.
- **Fetch curve JSON UNCREDENTIALED.** The CDN needs no cookie (`RECON.md` §4, confirmed), and
  `_rtings_session` is `Domain=.rtings.com`, so a default session would offer the credential to
  `i.rtings.com` for nothing.
- **A repeated test name is an ERROR, never "the first one" (2026-09-06).** headphones has three
  leaves called "RMS Deviation From Target" (bass, mid, treble). `_field_lookup` used to return
  whichever dict order offered, ranking the bass band for a caller who asked for treble. Now
  it raises `unknown_test` listing the qualified forms and accepts `"Group/Name"` (a suffix of
  the hierarchy) as well as the id. `rt_schema(find=…)` is the discovery path — one substring
  search over the bench's tests and usages instead of the three-to-six tree-then-group calls
  every shopper agent made.
- **Test ids and usage ids are SEPARATE spaces that collide (2026-09-06).** vacuum's `35602`
  is both the "Maximum Runtime" test and the "Pet Hair Pickup" usage. `_field_lookup` used to
  try tests first, so a filter meant for the usage compared minutes; now a digit that exists in
  both raises `unknown_test` naming the `test:`/`usage:` prefixes, which it also accepts.
- **`rt_schema(find=)` matches WORD STARTS over the full path and prefers the phrase.** A
  plain substring found "pet" inside "carpet" and ranked "Low-Pile Carpet" above "Pet Hair
  Pickup"; a leaf-name-only search missed every "Printing Speed" test (named "Black Only Text
  Document") and reported "input lag" as not measured. Stopwords and a light stem
  ("printing" → "print"), `(?<![a-z0-9])` boundaries, +10 for the phrase in order. A miss on
  price/cost words answers with the "no prices" rule instead of "try a synonym", which sent one
  agent through six synonyms for a fact the instructions already ruled out.
- **A best-of pick's featured row is tied to the schema by NAME, never by its stub `id`.**
  `column_options` carries no per-bench `id`, so the stub's `test.id` joins to nothing. A
  unique name gets `original_id` + `hierarchy`; a repeated one (air-purifier features
  "Measured PM1.0 CADR" twice — max speed on one list, quiet setting on another) lists
  `original_id_candidates`. A `kind: "group"` row is RTINGS' group score and is deliberately
  not joined: vacuum's "Pet Hair Pickup" group (carpet pickup tests) and the usage of the same
  name are different numbers, and `featured_notice` says so.
- **An unmatched `product_ids` entry is explained, one lookup each (2026-09-06).** Three ids in,
  two rows out, no word about the third: it was tested on a legacy bench outside the recent set,
  and a shrinking `matched` reads as "never tested". Each missing id (capped) is resolved
  through `resolve_product` and the warning names its bench and the `bench=[…]` to pass.
- **`app/side_by_side__review` is RTINGS' WORDS, and they survive the paywall.** Bare
  `{product_id}`. On a gated silo it returns, anonymously, per-usage verdict prose, pros/cons
  blurbs (`priority` below 0 is a con) and the score formula — the substantive answer where the
  numbers are null. **Ignore its `test_results`**: a third row shape with no `unblurred` key,
  so the normalizer has nothing to branch on. `user_has_access` is this path's only blur
  signal; it *appears* to track silo enforcement rather than membership (`false` on TV, `true`
  on mattress, both anonymous) — two data points, so a lead, not a fact.
  **A missing score under `user_has_access:true` is NOT `not_tested`.** One review-wide
  boolean cannot support "RTINGS did not measure this", and the row is present with prose and
  a `suitable` flag — it is `tested_visible` with a null score. Early Access rows are
  `review_unpublished`, never `tested_gated`.
  **Give the verdicts their own notice key**: `data.notice` already explains why the
  measurements are null, and overwriting it with verdicts framing erases the Early Access
  distinction. Metering is unverified — it is the public compare tool, not the review page —
  so a spent preview budget degrades to verdicts-only rather than an error.
- **Do not repeat a response-constant on every row.** `rt_product` carried `product_id` and
  `as_of` on all 243 rows: identical every time, already in `data.product` and the envelope,
  and 13 KB of a 69 KB response. In `rt_ratings`, `as_of` stays per-row (it varies per slice)
  but `product_id` is dropped from the rows nested under a product — the parent carries it
  (2026-09-06).
- **IMPORTANT: the client's tool-result cap counts the INDENTED text form, and `rt_ratings`
  budgets against it (added 2026-09-06).** The SDK renders a result as `indent=2` JSON in the
  text block beside the structured copy: 41 K compact became 75 K on the wire, and three of
  five shopper agents lost their first ranking call outright — no partial result, no hint
  which knob to turn. `_fit_response_budget` measures `json.dumps(data, indent=2)` plus an
  envelope margin against `RTINGS_MAX_RESPONSE_CHARS` (default 40,000), trims each group from
  the tail (the head of the ranking survives, `offset` continues from where it stopped) and
  warns `response_truncated` naming the offset; `matched` still describes the whole
  population. Never below one product per group. Same axis for curves: a graph is bounded in
  CELLS (`GRAPH_MAX_CELLS`, rows x columns), since headphones' sound profile is 13 columns
  wide and 200 rows of it was 60 K characters.
- **`rt_ratings` rows carry the ANSWER; `data.tests` carries the DEFINITION (2026-09-06).** A
  value row repeated its test's name, kind, unit, precision, hierarchy, `insider_only`,
  `value_source` and `raw_value` on every product, so 3 tests x 83 keyboard switches fitted 11
  products in the budget. `_slim_value_rows` strips `LEGEND_ROW_KEYS`; `_test_legend` states
  each projected test once, with `score_direction` derived from the concordance of value and
  score in the served rows (`higher_is_better` / `lower_is_better` / `mixed`, only with three
  or more pairs, labelled derived) — every shopper agent had inferred this by eye. `rt_product`
  keeps its per-row `hierarchy`: it is one product, and the breadcrumb is the point.
- **A clock unit ("mm:ss") describes the DISPLAY; the machine value is SECONDS (2026-09-06).**
  toaster-oven "Time To Reach 350°F" is `value: 105`, `rendered_value: "01:45"`, and both
  `number_input_unit` and `number_display_unit` say "mm:ss". Labelled as shipped, 105 "mm:ss"
  is nonsense, and the review path parsed "01:45" as 1.0 — off by 60-100x on two products.
  `TestDef.value_unit` maps `CLOCK_UNITS` to "seconds" with `display_unit: "mm:ss"`, and
  `parse_rendered_number` folds `h:mm:ss` into seconds.
- **`data_tier` is derived from the rows behind the products SERVED, not the slice fetched.** A
  `product_ids` filter matching nothing returned zero rows and `data_tier: unblurred` — proof
  from rows the caller never saw. `scores_available` stays population-level (it describes the
  bench); `data_tier` describes this response.
- **`rt_schema(find=)` also searches a word test's VALUES** ("countertop" → microwave
  "Installation", `match: "value"`), and a miss lists the bench's usage names, because
  "reheat" is the "Leftovers" usage and no synonym table would have said so.
- **`filters={"size": …}` means the tested variant ONLY where no test is called Size (2026-09-06).**
  Monitor and laptop have a numeric "Size" test; the catalog alias hijacked it, so
  `{"Size": ">31"}` matched the tested-variant string and returned nothing, silently, while the
  same predicate by id matched 46 — "0 results" reading as "no such monitor", the failure the
  whole project exists to prevent. `_apply_filters` and `_fields_to_fetch` both defer to
  `_field_lookup(schema, "size")` before treating the word as the alias.
- **A test name may CONTAIN a slash, so the whole string is tried as a name before the
  `Group/Name` split (2026-09-06).** Monitor "Rotate Portrait/Landscape", copied from
  rt_schema's own output, was rejected as a filter key.
- **A field is never blamed on the bench once an earlier filter emptied the set.** The
  "populated / gated / absent" census over zero rows read as "no row carries it — call
  rt_schema", a false diagnosis for a field the bench defines; the warning now says which
  filter left 0 products. A word filter that matches nothing quotes the values the rows do
  carry (`filter_matched_nothing`): "3840x2160" against RTINGS' "3840 x 2160".
- **A word test's filter value is TEXT, whatever it looks like (2026-09-06).** `{"Native
  Resolution": "1440"}` parsed 1440 as a number and matched nothing against "2560 x 1440";
  the docstring promised a substring. `_parse_clauses(textual=True)` for `kind: word`.
- **`recommended_sku` is GONE.** The best-of page's `sku` block was wrong on 2 of 3 picks of
  the 43-inch TV list (a Samsung model number on the Vizio pick, a C4 SKU on the C6 review);
  a field presented as "the SKU to buy" cannot be one RTINGS got wrong a third of the time.
  The review's own size table (rt_product `variants`, now read from `variant_skus`) is the
  source. **The landing page's list index is not exhaustive**: a review's prose links to
  `by-usage/bluetooth-headset-for-phone-calls`, which the index omits, so a slug outside it
  is fetched and only a page matching neither template is `unknown_list`.
- **A filter takes several clauses on one field** (`"13..14"`, `"13 to 14"`, `">=13 <=14"`,
  `">=13,<=14"`; added 2026-09-06): a 13-to-14-inch laptop took two passes and a hand filter
  with one comparator per field.
- **`filters={"product_ids": [...]}` is the head-to-head path.** Every agent comparing two
  named products guessed a `name_contains` substring after an `rt_search`, which matches
  siblings. Ids are identity; it applies to the uncatalogued group too.
- **`rt_recommendations` is the one page-extraction path** (`RECON.md` §7) — isolate it: own parser,
  own `recommendations_missing` drift alarm, never on the table/graph code path.
- **IMPORTANT: there are TWO best-of templates and BOTH are legitimate (added 2026-09-05,
  `RECON.md` §12.17).** RTINGS is migrating best-of pages off the monolithic
  `RecommendationVuePage` (one `data-props` blob) onto a **server-rendered** template whose only
  Vue parts are islands. **mattress and running-shoes have moved; the other 12 silos sampled have
  not**, and it does *not* track silo age — refrigerator is the newest silo and still on the old
  one, so more will migrate silently. Supporting only the props shape made `rt_recommendations`
  fail on **all 20** mattress lists — the tool advertised lists it could not fetch. So try props,
  then static, and fire `recommendations_missing` only when **neither** matches; that, not "the
  props are missing", is the drift signal. There is still **no API** (the new page's whole bundle
  is 3 KB with zero `/api/v2/safe/` references), so extraction remains the only route.
  The static template's `DistributionTooltip` gives `target_label`/`target_type` but its
  **`target_id` is NOT the schema `original_id`** (Side Sleeping: 38309 vs 36553) — emit a null id
  with a real name, never the wrong join key. Both migrated silos are open, so no blurred sample
  exists: a featured item rendering neither score nor value is `unknown_row_status`, never
  `tested_gated`.
- **The server `instructions` must spell parameters as the schema names them (member round,
  2026-09-07).** Three of fifteen graders lost a call to `rt_product(url, ...)` in the
  instructions text; the parameter is `product`. `rt_product(product=<url>, ...)` everywhere.
- **The "no prices" rule names its one measured exception.** Printer `Black-Only Printing Cost`
  (US$/print) is a test, found with `find='cost'`; an agent reading rule 7 literally refused a
  cost-per-page question that the data answers (S6).
- **A wrong `Group/Name` qualifier lists the tests with that leaf name (S3).** "No test named
  `Treble/RMS Deviation From Target`" sent the caller back to the schema; the qualifier must be
  the group's full name (`Treble Profile: Target Compliance/...`), and the forms that work are
  now in `details.matches`, the same shape the ambiguity error uses.
- **A group named in `tests=` lists its leaves and any same-named usage (S12).** mattress
  `tests=["Firmness"]` resolves to the group; the error now carries `details.tests`
  (`Firmness Level` 31954, the two stiffness tests) and `details.usages` (usage 40380).
- **`score_direction` is derived over every product the call matched, not the rows served.**
  The legend said "in this response" while `_score_direction` ran over the pre-limit population,
  which is the more robust reading (mouse `Delay At Half Movement` is `mixed` over 159 rows and
  100% discordant over the top 10); the docs now say which (S5).
- **Graph axes: read `vAxes` and `scaleType` too (S8).** tv's PQ EOTF curve declares two y axes
  under `options.vAxes` ("0" output stimulus, "1" luminance) with `vAxis` as a placeholder, and
  spells the scale `scaleType`. Read from `vAxis`/`scale` alone the curve had `y: null` and
  `x.scale: null`. `axes.y` is the first declared axis; a dual-axis chart adds `y_axes` and
  `series_y_axis_index` (per value column, cut to the columns that ship — RTINGS declares 10
  styling slots for 6 columns).
- **`[nolink:Name]` in best-of prose is unwrapped to `Name` (S9).** RTINGS' shortcode for a
  product it chose not to link; two of five soundbar picks carried it into `reasoning`.
- **`rt_schema(find=...)` usage hits carry `is_unscored` (S12).** A usage that is never scored
  (mattress `Firmness`) sorted only by falling back; the tree already said so, `find` did not.
- **Measured 2026-09-07, member round: the default recent-set group is ONE group** (mouse 2
  benches, headphones 4, laptop 3) with `test_bench` on every row, per `SPEC.md`; a bench the
  site renders as recent but publishes no schema for (headphones 244/230/183, laptop 198) has no
  display name anywhere RTINGS ships, so it lists as `{"id": ...}` alone.
- **`terms_with_no_matches` must not be vouched for by an empty `matched_terms`
  (2026-09-08).** `_unmatched_terms` read `term in (h.get("matched_terms") or [term])`, and an
  empty list is falsy, so one hit that matched some *other* term only partially made every term
  read as matched and the field came back `[]` for a six-term search that matched two.
  `matched_terms` is absent only on the single-term shape (where the hit itself is the match);
  `[]` is a real answer and must be read as one — `h.get("matched_terms", [term])`.
- **A lone digit in a `find` term scores only beside a word of the same term (2026-09-08).**
  `_find_words("hdmi 2.1")` gives `hdmi/2/1`, and on their own the bare digits hit "Peak 2%
  Window", "USB Ports" (values `1`, `2`) and sixty more rows, filling the 60-row cap with noise
  while the real matches sat past it. A term that is *nothing but* a digit keeps it — that is all
  the caller gave us (`find="1440"`).
- **An off-bench `usages=`/`tests=` entry is a warning and a partial answer, not a failed call
  (2026-09-08).** It now matches `product_ids`, which has always warned: the warning names the
  usage or test, its name, and the `bench=[...]` that carries it. The one exception is a request
  where *every* entry is off-bench — dropping them all would leave `usages=[]`, which reads as
  "no scores exist", so that still raises `unknown_test` with `details.available` and
  `details.on_other_benches`.
- **MEASURED 2026-09-08: the client cuts a tool description at exactly 2048 characters** and
  appends "… [truncated]". `rt_ratings` was 4,229 characters with the filter vocabulary
  (`brand`, `variant`, `name_contains`, `published`) at ~1,540 and `sort`'s "no prefix means
  descending" past 2,600 — so an agent filtered by `name_contains` and read sizes by eye for
  an answer the tool already had (shopper round 3). The description is now ordered by what a
  caller cannot work without: filters, sort, the not-applied rule, the `status` domain, then
  paging, all inside the window; Size/Shape/Uncatalogued are reference and may fall past it.
  `test_every_tool_description_fits_or_front_loads_the_client_budget` asserts each essential
  phrase is inside the first 2048 characters, so a future edit cannot push one out silently.
- **`sold_in` filters on the sizes a product is SOLD in (2026-09-08).** `variant` matches the
  TESTED sku only, and "which Sony OLED comes at 83 inches or bigger" had no filter at all —
  one session took the whole brand's table and scanned by eye. It takes the same comparators
  and ranges as any other filter (`{"sold_in": ">=83"}`), matches loosely when textual
  (`"California King"`), and warns rather than returning a bare 0 when nobody sells that size.
- **Each `variants[]` entry carries the manufacturer model number (2026-09-08).** RTINGS' sku
  `name` is `"XR-83A80L"` / `"UN43TU7000FXZA"` — what a retailer is searched by — and only
  `variation` was kept, so a session went to a web search for it. `variants` is now
  `[{"variation": "83\"", "model": "XR-83A80L"}]`; `model` is omitted where it only repeats
  the product's own name, which is all some silos put there.
- **`rt_schema(find=...)` names a catalog field instead of returning nothing (2026-09-08).**
  `find="size, brand, year, price"` matched no test and said nothing at all. Terms that match
  nothing are now checked against the catalog vocabulary and answered in `catalog_fields`
  ("that is `filters={'brand': ...}` on rt_ratings, not a test"; for price, that RTINGS
  publishes none). Only terms in `terms_with_no_matches` are checked, so a silo that really
  has a "Size" test (laptop, monitor) matches the test and never sees the hint.
- **`rt_search(silo=...)` scopes the cross-silo index (2026-09-08).** "Sony A80J" returned 822
  hits with cameras and soundbars on page one of a TV question. RTINGS' query takes no
  category, so the filter is applied here — over more hits than `count` (`scanned`), reporting
  `silo_matches`, and saying in the notice that a product ranked below them will not appear.
- **`rt_article` reads a `learn` page as prose (2026-09-08).** RTINGS' lineup and explainer
  articles answer what no measurement can, and nothing surfaced them. It takes the `url`
  `rt_search` returns, lists the article's headings in `sections`, and slices one out with
  `section=` — a lineup article is past 25,000 characters, so one heading is the readable
  unit. Prose only, never a measurement, and never gated. See `RECON.md` §12.19, and §14.7
  for the meter measurement that let it ship.
- **`rt_product`'s results are nested by section, and MEASURED 2026-09-08 that is a shape
  win, not a byte win.** The flat rows repeated `hierarchy` on every one — on a real TV
  review (Sony BRAVIA 9 II, 243 results) that breadcrumb is **18,589 of 69,852 characters,
  26.6%**, which is where the "~23%" estimate came from. Removing it recovers far less than
  that, because nesting adds two indent levels to 243 rows: **flat 69,852 → nested 68,592,
  only 1.8%**. A flat row carrying a `group` id against a breadcrumb legend measured 65,249
  (6.6%) — better on bytes, worse on shape. Nesting was kept for what it buys instead: the
  response reads like the sectioned review it came from, and each section's `group_id` goes
  straight back as `rt_product(group=...)` to fetch that section alone — which is what the
  budget warning now tells a caller to do. Do not re-derive the 23%: it is the breadcrumb's
  share, not the saving.
- **`rt_product` never bounded its response, and now does (2026-09-08).** "Bound every
  response on the wire" was true of `rt_ratings` only: a TV review measured **77,407
  characters against the 40,000 budget** with no warning and no trimming. It now drops whole
  sections from the TAIL (a half-section reads as a section with tests missing), then rows
  from the last one, never below one; `result_count` still counts the WHOLE review, and
  `groups_omitted` names every dropped section with its `group_id` so the rest is one call
  away. The index of what was cut is itself part of the response, so it is measured inside
  the trim loop — computing it afterwards left the answer 2 KB over budget.
- **`rt_recommendations` surfaces `mentions` (2026-09-08).** RTINGS' "Notable Mentions" — the
  runners-up below the ranked picks, each with its own reasoning — reached no caller on either
  template: unparsed on the server-rendered one, parsed but never emitted on the props one. Both
  now emit the same shape and the tool returns it, honouring `include_reasoning` like the picks.
- **A featured usage joins to the schema by NAME + `target_type`, never by `target_id`
  (2026-09-08).** Measured: 0 of 9 tooltip `target_id`s are schema ids, while all 9 labels
  resolve once the type picks the namespace ("Cooling" is both a test and a usage). Unique name →
  the real `original_id`; repeated name → a null id and a `candidates` list. Same rule
  `_featured_results` has used for tests since 2026-09-06 — a wrong join key is worse than none.
- **A review URL resolves against EVERY bench, not just the recent set (2026-09-08).**
  `_product_from_url` scanned `info.recent_ids` only, so every legacy-bench review was
  unresolvable by URL while the same product resolved by its numeric id — the Samsung TU7000
  (bench 124) failed on the URL RTINGS' own catalog gives for it, under "no product in the tv
  catalog has the URL", which reads as "no such product". A review URL is `rt_product`'s
  documented primary input, so the scan widened rather than the promise narrowing: recent benches
  first (cheap, usually cached), then the rest, and the error now says every bench was searched.
  **Search is still forbidden as a URL fallback** — a relevance hit is not an identity, and that
  rule is what stopped `/early-access/tv/reviews/lg/b6-oled-2026` returning the 2016 B6.
- **The recent-bench set is a strict PARTITION (2026-09-08, `RECON.md` §12.23).** 6 bench pairs
  across 4 silos share zero products. Grouping by bench loses nothing and duplicates nothing, and
  a cross-bench score comparison does not exist in the data to be made — q7 is answered and moot.
