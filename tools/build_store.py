"""Build the altermagnets site's immutable runtime DuckDB store offline."""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FUNCTIONS = ROOT / "src" / "functions"
if str(FUNCTIONS) not in sys.path:
    sys.path.insert(0, str(FUNCTIONS))

from material_store import build_store, default_store_path, resolve_data_dir


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--target",
        type=Path,
        default=None,
        help=(
            "output DuckDB file (default: ALTERMAGNETS_STORE_PATH, then "
            f"{default_store_path()})"
        ),
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="directory containing the three source CSVs (default: ALTERMAGNETS_DATA_DIR, then data/tables)",
    )
    return parser.parse_args()


def main() -> int:
    arguments = _arguments()
    target = build_store(arguments.target, data_dir=arguments.data_dir)
    print(f"Built {target} from {resolve_data_dir(arguments.data_dir)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
