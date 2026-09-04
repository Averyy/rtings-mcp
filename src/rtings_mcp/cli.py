"""Command line: ``serve`` (the default), ``auth``, and the release-gate ``scan``.

``auth`` validates immediately by fetching **one HTML page** and reading
``GLOBALS.session`` — not the API, which carries no auth marker at all. Paste and know.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from typing import Any

from .auth import clear_credential, load_credential, parse_curl_cookie, store_credential
from .config import SESSION_COOKIE_NAME, load_config
from .context import Context
from .errors import RtingsError
from .server import configure_logging
from .server import main as serve_main


def _print(*parts: object) -> None:
    print(*parts, file=sys.stdout)


async def _probe(ctx: Context) -> None:
    probe = await ctx.auth.session_probe(force=True)
    _print(f"session:            {probe.session}")
    _print(f"logged in:          {probe.logged_in}")
    _print(f"access level:       {probe.access_level} (preview threshold {probe.preview_level})")
    if probe.access_limit is not None:
        _print(f"review previews:    {probe.previews_remaining} of {probe.access_limit} left")
    if probe.has_insider_access is not None:
        _print(f"insider access:     {probe.has_insider_access}")
    # Printed verbatim: these name the member/free boundary directly, and Phase 0's whole
    # job on the auth side is seeing what a real membership puts here.
    for key in ("user_is_insider", "membership_type", "user_type"):
        if key in probe.markers:
            _print(f"{key + ':':<20}{probe.markers[key]!r}")
    if probe.x_cache:
        _print(f"probe X-Cache:      {probe.x_cache}")
    if probe.note:
        _print(f"note:               {probe.note}")

    if probe.session == "member":
        _print("\nMember session active.")
    elif probe.session == "free":
        _print(
            "\nFree account. This unlocks metered review previews on rt_product only — the "
            "table and ratings surfaces are unchanged."
        )
    elif probe.session == "expired":
        _print("\nThat cookie did not authenticate. Capture a fresh one and re-run `auth`.")
    elif probe.session == "anonymous":
        _print(
            "\nAnonymous. That is a first-class mode: roughly half of RTINGS' categories "
            "serve their full measurements and scores without any session."
        )
    else:
        _print("\nCould not read the session from RTINGS. Try again shortly.")


async def _cmd_auth(args: argparse.Namespace) -> int:
    config = load_config()

    if args.clear:
        removed = clear_credential(config)
        _print("credential removed" if removed else "no stored credential to remove")
        return 0

    if args.status:
        credential = load_credential(config)
        if credential.present:
            _print(f"credential source:  {credential.source}")
        else:
            _print("credential source:  none (anonymous)")
        ctx = Context.build(config)
        await _probe(ctx)
        return 0

    _print(
        f"{SESSION_COOKIE_NAME} is HttpOnly, so document.cookie cannot read it and "
        '"Copy as cURL" is the only way to capture it.\n'
        "\n"
        "  1. Open rtings.com in your browser, logged in\n"
        "  2. DevTools -> Network -> right-click any request -> Copy -> Copy as cURL\n"
        "  3. Paste it below, then press Ctrl-D\n"
    )
    pasted = sys.stdin.read()
    cookie = parse_curl_cookie(pasted)
    if not cookie:
        _print(
            f"\nNo {SESSION_COOKIE_NAME} found in that paste. Make sure you copied a request "
            "made while logged in."
        )
        return 1

    path = store_credential(config, cookie)
    _print(f"\nStored (0600) at {path}. Only the one cookie is kept; nothing else from the "
           "paste is read or written.\n")

    ctx = Context.build(config)
    await _probe(ctx)
    return 0


async def _cmd_scan(args: argparse.Namespace) -> int:
    """The release gate: recompute the per-silo enforcement map from live anonymous fetches.

    **A diff against the committed snapshot is a SPEC CHANGE, not a test failure.** The
    paywall map is a snapshot of RTINGS' business decisions; it moves silently, and a stale
    map makes the server misdescribe what anonymous gets — which is the product's central
    claim.
    """
    config = load_config()
    ctx = Context.build(config)
    # The enforcement map is an ANONYMOUS measurement by definition. With a member cookie
    # configured every gated silo would scan as `open`, `--out` would overwrite the baseline
    # with "0 enforcing", and member data would land in anonymous-tier cache files. The
    # docstring said "no cookie"; now it is enforced.
    had_credential = ctx.transport.credential.present
    ctx.transport.credential.configured = None
    ctx.auth._probe = ctx.auth.unknown_probe("scan runs anonymously by construction")
    if had_credential:
        _print("(a credential is configured; the scan ignores it and runs anonymously)\n")
    repo = ctx.repo
    silos = await repo.silo_index()
    names = [args.silo] if args.silo else sorted(silos)
    out: dict[str, Any] = {}

    for name in names:
        try:
            info = await repo.bench_info(name)
            schema = await repo.schema(name)
            bench_id = info.current_id(schema)
            if bench_id is None:
                out[name] = {"error": "no current bench"}
                continue
            leaves = [t for t in schema.leaf_tests_for_bench(bench_id) if t.insider_only]
            sample = [t.original_id for t in leaves[: args.tests]]
            if not sample:
                out[name] = {"bench_id": bench_id, "insider_leaf_tests": 0}
                continue
            await repo.ensure_test_slices(name, [bench_id], sample, refresh=args.refresh)
            generations = await repo.catalog(name, [bench_id])
            generation = generations.get(bench_id)
            unpublished = set(generation.unpublished_ids) if generation else set()
            total = 0
            unblurred = 0
            for test_id in sample:
                meta = repo.slice_meta("tests", bench_id, test_id, demand="anonymous")
                if meta is None:
                    continue
                for row in (meta.payload or {}).get("rows", []):
                    if row.get("status") != "tested":
                        continue
                    if str(row.get("product_id")) in unpublished:
                        continue
                    total += 1
                    if row.get("unblurred"):
                        unblurred += 1
            ratio = (unblurred / total) if total else None
            silo_row = silos.get(name) or {}
            # Same per-silo shape as the committed baseline, so `--out` can overwrite it
            # in place without losing the provenance fields a reader needs to judge it.
            out[name] = {
                "bench_scanned": bench_id,
                "enforces_paywall": (ratio == 0.0) if ratio is not None else None,
                "pct_insider_rows_unblurred": (
                    round(100 * ratio, 1) if ratio is not None else None
                ),
                "insider_rows_sampled": total,
                "review_count": silo_row.get("review_count"),
                "test_benches": len(info.all_ids),
                "tests_current_bench": len(schema.tests_for_bench(bench_id)),
                "first_published": str(silo_row.get("first_published_at") or "")[:10] or None,
                "silo_group": silo_row.get("silo_group"),
                "unpublished_products": len(unpublished),
            }
            _print(
                f"{name:<18} bench {bench_id:<5} rows {total:<6} "
                f"unblurred {unblurred:<6} "
                f"{'ENFORCES' if ratio == 0.0 else 'open' if ratio == 1.0 else 'partial'}"
            )
        except RtingsError as exc:
            out[name] = {"error": exc.code, "message": exc.message}
            _print(f"{name:<18} ERROR {exc.code}: {exc.message}")

    enforcing = sum(1 for v in out.values() if v.get("enforces_paywall") is True)
    open_silos = sum(1 for v in out.values() if v.get("enforces_paywall") is False)

    if args.out:
        document = {
            "_comment": (
                "Baseline for the release-time enforcement re-scan (see CLAUDE.md > "
                "Release). Anonymous data only. 'enforces_paywall' is DERIVED from observed "
                "unblurred on insider_only rows, excluding published:false products. "
                "Re-scan and diff before every version bump; a change here is a spec "
                "change, not a test failure."
            ),
            "scanned_at": time.strftime("%Y-%m-%d"),
            "method": {
                "source": (
                    "table_tool__column_options + table_tool__test_results, anonymous, "
                    "no cookie"
                ),
                "scope": (
                    f"current bench per silo (newest rendered bench with a published "
                    f"schema), first {args.tests} insider_only number/word leaf tests"
                ),
                "rule": (
                    "blurred <=> product.published==false OR (test.insider_only AND silo "
                    "enforces)"
                ),
            },
            "totals": {"silos": len(out), "enforcing": enforcing, "open": open_silos},
            "silos": out,
        }
        with open(args.out, "w", encoding="utf-8") as handle:
            json.dump(document, handle, indent=2, sort_keys=False)
            handle.write("\n")
        _print(f"\nwrote {args.out}")
    _print(f"\n{enforcing} enforcing / {open_silos} open of {len(out)} scanned")
    _print(
        "A diff against docs/enforcement-snapshot.json is a SPEC CHANGE, not a test "
        "failure: update the snapshot, RECON.md §11.1 and the README framing, and say so "
        "in the release notes."
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rtings-mcp", description="MCP server for RTINGS.com test data"
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("serve", help="run the MCP server on stdio (default)")

    auth = sub.add_parser("auth", help="store and validate your RTINGS session cookie")
    auth.add_argument("--status", action="store_true", help="show the current session only")
    auth.add_argument("--clear", action="store_true", help="delete the stored credential")

    scan = sub.add_parser("scan", help="release gate: re-derive the per-silo enforcement map")
    scan.add_argument("--silo", help="scan one silo instead of all 28")
    scan.add_argument("--tests", type=int, default=20, help="insider tests to sample per silo")
    scan.add_argument("--out", help="write the result as JSON to this path")
    scan.add_argument("--refresh", action="store_true", help="ignore cached slices")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    command = args.command or "serve"

    if command == "serve":
        serve_main()
        return 0

    configure_logging("WARNING")
    if command == "auth":
        return asyncio.run(_cmd_auth(args))
    if command == "scan":
        return asyncio.run(_cmd_scan(args))
    parser.print_help()
    return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
