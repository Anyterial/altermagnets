#!/usr/bin/env python3
"""Serve the altermagnets dataset over OPTIMADE.

A thin CLI over the :mod:`optimade` package: it serves the prebuilt store's
``structures`` and ``references`` families through the generic *httk-serve*
OPTIMADE engine, or (with ``--validate``) validates every assembled record.
"""

import argparse
import sys

from httk.core import report
from httk.serve.web.runtime.devserver import run_dev_server
from optimade import DEFAULT_PUBLIC_BASE_URL, build_providers, build_service_app, run_validation


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Serve the altermagnets dataset over OPTIMADE.")
    parser.add_argument("--validate", action="store_true", help="validate every record and exit")
    parser.add_argument("--host", default="127.0.0.1", help="host to bind when serving")
    parser.add_argument("--port", type=int, default=8081, help="port to bind when serving")
    parser.add_argument(
        "--public-base-url",
        default=DEFAULT_PUBLIC_BASE_URL,
        help="absolute base URL used in figure metadata",
    )
    parser.add_argument(
        "--cors-origin",
        action="append",
        default=[],
        help="exact browser origin allowed to query OPTIMADE (repeatable)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="report diagnostics on the httk channel (-v info, -vv debug)",
    )
    args = parser.parse_args(argv)
    if args.verbose:
        report.configure_reporting(level="debug" if args.verbose > 1 else "info")

    if args.validate:
        return run_validation(build_providers(public_base_url=args.public_base_url))
    app = build_service_app(
        public_base_url=args.public_base_url,
        cors_origins=args.cors_origin,
    )
    run_dev_server(app=app, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
