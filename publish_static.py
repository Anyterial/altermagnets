#!/usr/bin/env python3

from pathlib import Path

from httk.web import publish

ROOT = Path(__file__).parent

# Publish target base URL (used for page.absurl style fields).
BASEURL = "http://127.0.0.1/"

# URL style for generated links in publish mode:
# - False => links include ".html" suffix (for broad static-host compatibility)
# - True  => extensionless links
USE_URLS_WITHOUT_EXT = False

publish(ROOT / "src", ROOT / "public", BASEURL, use_urls_without_ext=USE_URLS_WITHOUT_EXT)

print("*****\nNow open public/index.html in your web browser.\n*****")
