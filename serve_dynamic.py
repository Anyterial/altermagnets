#!/usr/bin/env python3

from pathlib import Path

from httk.web import serve

ROOT = Path(__file__).parent
serve(ROOT / "src", host="127.0.0.1", port=8080, config_name="config_dynamic")
