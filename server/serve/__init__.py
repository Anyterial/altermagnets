"""The altermagnets OPTIMADE serving library.

This package holds the store-envelope policy, dataset assembly, figure-byte
machinery, service factory, validation, and combined-site composition that the
``serve_optimade`` and ``serve_combined`` entry scripts drive. It self-inserts
``src/functions`` on ``sys.path`` so ``from serve import ...`` is self-sufficient
once the repo's ``server/`` directory is on ``sys.path``.
"""

import sys
from pathlib import Path

_FUNCTIONS_ROOT = Path(__file__).resolve().parents[2] / "src" / "functions"
if str(_FUNCTIONS_ROOT) not in sys.path:
    sys.path.insert(0, str(_FUNCTIONS_ROOT))

from .adapter import RESULT_TYPE, SORTABLE_PROPERTIES, AltermagnetStoreAdapter
from .combined import create_combined_app
from .dataset import (
    DEFAULT_PUBLIC_BASE_URL,
    AltermagnetScreeningResultProvider,
    build_dataset,
    build_providers,
    load_schema_definitions,
)
from .dsp import build_dsp_app
from .files import figure_file_is_servable
from .service import (
    AMDB_DESCRIPTION,
    AMDB_HOMEPAGE,
    AMDB_NAME,
    AMDB_PROVIDER,
    INDEX_DESCRIPTION,
    INDEX_NAME,
    build_service_app,
)
from .validation import run_validation

__all__ = [
    "AMDB_DESCRIPTION",
    "AMDB_HOMEPAGE",
    "AMDB_NAME",
    "AMDB_PROVIDER",
    "DEFAULT_PUBLIC_BASE_URL",
    "INDEX_DESCRIPTION",
    "INDEX_NAME",
    "RESULT_TYPE",
    "SORTABLE_PROPERTIES",
    "AltermagnetScreeningResultProvider",
    "AltermagnetStoreAdapter",
    "build_dataset",
    "build_dsp_app",
    "build_providers",
    "build_service_app",
    "create_combined_app",
    "figure_file_is_servable",
    "load_schema_definitions",
    "run_validation",
]
