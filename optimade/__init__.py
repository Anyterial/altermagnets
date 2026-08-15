"""The altermagnets OPTIMADE serving library.

This package holds the store-envelope policy, dataset assembly, figure-byte
machinery, service factory, validation, and combined-site composition that the
``serve_optimade`` and ``serve_combined`` entry scripts drive. It self-inserts
``src/functions`` on ``sys.path`` so ``import optimade`` is self-sufficient.
"""

import sys
from pathlib import Path

_FUNCTIONS_ROOT = Path(__file__).resolve().parents[1] / "src" / "functions"
if str(_FUNCTIONS_ROOT) not in sys.path:
    sys.path.insert(0, str(_FUNCTIONS_ROOT))

from .adapter import SORTABLE_PROPERTIES, AltermagnetStoreAdapter
from .combined import create_combined_app
from .dataset import (
    DEFAULT_PUBLIC_BASE_URL,
    AltermagnetStructureProvider,
    build_dataset,
    build_providers,
    load_schema_definitions,
)
from .figures import figure_file_is_servable
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
    "SORTABLE_PROPERTIES",
    "AltermagnetStoreAdapter",
    "AltermagnetStructureProvider",
    "build_dataset",
    "build_providers",
    "build_service_app",
    "create_combined_app",
    "figure_file_is_servable",
    "load_schema_definitions",
    "run_validation",
]
