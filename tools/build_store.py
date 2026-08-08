"""Build the altermagnets site's immutable runtime DuckDB store offline."""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FUNCTIONS = ROOT / "src" / "functions"
if str(FUNCTIONS) not in sys.path:
    sys.path.insert(0, str(FUNCTIONS))

from material_store import (
    build_store,
    default_store_path,
    resolve_data_dir,
    resolve_details_dir,
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--target",
        type=Path,
        default=None,
        help=(f"output DuckDB file (default: ALTERMAGNETS_STORE_PATH, then {default_store_path()})"),
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="directory containing the three source CSVs (default: ALTERMAGNETS_DATA_DIR, then data/tables)",
    )
    parser.add_argument(
        "--details-dir",
        type=Path,
        default=None,
        help=("directory containing generated plot assets (default: ALTERMAGNETS_DETAILS_DIR, then data/details)"),
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="report diagnostics on the httk channel (-v info, -vv debug)",
    )
    return parser.parse_args()


def main() -> int:
    arguments = _arguments()
    if arguments.verbose:
        from httk.core import report

        report.configure_reporting(level="debug" if arguments.verbose > 1 else "info")
    timings: dict[str, float] = {}
    target = build_store(
        arguments.target,
        data_dir=arguments.data_dir,
        details_dir=arguments.details_dir,
        timings=timings,
    )
    print(
        f"Built {target} from {resolve_data_dir(arguments.data_dir)} "
        f"with plots from {resolve_details_dir(arguments.details_dir)} "
        f"in {timings['total']:.1f}s "
        f"(load {timings['load']:.1f}s, write {timings['write']:.1f}s, finalize {timings['finalize']:.1f}s)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
