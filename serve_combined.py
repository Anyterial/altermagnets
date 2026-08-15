"""Serve the dynamic altermagnet site and its OPTIMADE services together.

A thin CLI over :func:`optimade.create_combined_app`.
"""

import argparse

from httk.serve.web.runtime.devserver import run_dev_server
from optimade import create_combined_app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Serve the altermagnets website and OPTIMADE services together.")
    parser.add_argument("--host", default="127.0.0.1", help="host to bind when serving")
    parser.add_argument("--port", type=int, default=8080, help="port to bind when serving")
    parser.add_argument(
        "--public-base-url",
        help="public HTTP(S) origin; OPTIMADE services are appended at /optimade/index and /optimade/amdb",
    )
    args = parser.parse_args(argv)
    if args.public_base_url is None:
        if args.host in {"0.0.0.0", "::"}:
            parser.error("--public-base-url is required when binding a wildcard host")
        public_host = f"[{args.host}]" if ":" in args.host else args.host
        args.public_base_url = f"http://{public_host}:{args.port}"
    run_dev_server(
        app=create_combined_app(public_base_url=args.public_base_url),
        host=args.host,
        port=args.port,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
