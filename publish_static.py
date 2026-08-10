"""Publish the fully static site with its browser-side OPTIMADE endpoint."""

import argparse
import os
from pathlib import Path

from httk.serve.web import publish

ROOT = Path(__file__).parent
BASEURL = "http://127.0.0.1/"
USE_URLS_WITHOUT_EXT = False
OPTIMADE_ENVIRONMENT = "ALTERMAGNETS_OPTIMADE_BASE_URL"


def publish_site(outdir: Path = ROOT / "public", *, optimade_base_url: str | None = None) -> None:
    """Render the site, selecting the browser API base URL from env or argument."""
    selected = optimade_base_url or os.environ.get(OPTIMADE_ENVIRONMENT, "/optimade")
    previous = os.environ.get(OPTIMADE_ENVIRONMENT)
    os.environ[OPTIMADE_ENVIRONMENT] = selected
    try:
        publish(ROOT / "src", outdir, BASEURL, use_urls_without_ext=USE_URLS_WITHOUT_EXT)
    finally:
        if previous is None:
            os.environ.pop(OPTIMADE_ENVIRONMENT, None)
        else:
            os.environ[OPTIMADE_ENVIRONMENT] = previous


def main(argv: list[str] | None = None) -> int:
    """Publish the site and return a process exit code."""
    parser = argparse.ArgumentParser(description="Publish the static altermagnets site.")
    parser.add_argument(
        "--optimade-base-url",
        metavar="URL",
        help="browser OPTIMADE base URL; defaults to ALTERMAGNETS_OPTIMADE_BASE_URL or /optimade",
    )
    args = parser.parse_args(argv)
    publish_site(optimade_base_url=args.optimade_base_url)
    print("*****\nNow open public/index.html in your web browser.\n*****")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
