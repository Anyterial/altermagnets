"""The AMDB store-envelope policy over the generic lazy store adapter."""

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import replace as replace_dataclass
from typing import Any

import material_store
from httk.serve.optimade import adapter_from_stores
from httk.serve.optimade.model import ResultRow
from httk.store.backend.sql import StoredEntrySource

SORTABLE_PROPERTIES = {
    "structures": (
        "id",
        "_anyterial_formula",
        "_anyterial_classification",
        "_anyterial_space_group",
        "_httk_magndata_ids",
        "_anyterial_max_spin_splitting",
        "_anyterial_avg_spin_splitting",
        "_anyterial_spin_splitting_fraction",
        "_httk_dft_band_gap",
        "_anyterial_min_crustal_abundance",
        "_anyterial_screening_rank",
    )
}

_PUBLIC_ID = "_httk_custom_public_id"
_REFERENCE_IDS = "_httk_custom_reference_ids"
_INTERNAL_STORE_PROPERTIES = {_PUBLIC_ID, _REFERENCE_IDS}


class _LiveResults:
    """Preserve store result metadata while enriching only the returned page."""

    def __init__(self, source: Any, rows: Sequence[ResultRow]) -> None:
        self._source = source
        self._rows = tuple(rows)

    @property
    def more_data_available(self) -> bool:
        return bool(self._source.more_data_available)

    def count(self) -> int:
        return int(self._source.count())

    def __iter__(self) -> Iterator[ResultRow]:
        return iter(self._rows)


def _rewrite_id_filter(node: Any) -> Any:
    """Route public OPTIMADE id predicates to AMDB's durable public-id field."""
    if isinstance(node, tuple):
        if len(node) == 2 and node == ("Identifier", "id"):
            return ("Identifier", _PUBLIC_ID)
        return tuple(_rewrite_id_filter(item) for item in node)
    return node


def _absolute_figure_urls(value: object, public_base_url: str) -> object:
    if not isinstance(value, list):
        return value
    figures: list[object] = []
    for item in value:
        if not isinstance(item, Mapping):
            figures.append(item)
            continue
        projected = dict(item)
        for name in ("url", "dark_url"):
            url = projected.get(name)
            if isinstance(url, str) and url.startswith("/"):
                projected[name] = public_base_url + url
        figures.append(projected)
    return figures


def _public_store_schema(schema: Any) -> Any:
    """Hide storage-only projections from the published OPTIMADE schema."""

    def public_names(names: Sequence[str]) -> tuple[str, ...]:
        return tuple(name for name in names if name not in _INTERNAL_STORE_PROPERTIES)

    entry_info = {
        entry: {
            **info,
            "properties": {
                name: value for name, value in info["properties"].items() if name not in _INTERNAL_STORE_PROPERTIES
            },
        }
        for entry, info in schema.entry_info.items()
    }
    return replace_dataclass(
        schema,
        entry_info=entry_info,
        properties_by_entry={entry: public_names(names) for entry, names in schema.properties_by_entry.items()},
        default_response_fields={entry: public_names(names) for entry, names in schema.default_response_fields.items()},
        required_response_fields={
            entry: public_names(names) for entry, names in schema.required_response_fields.items()
        },
        unknown_response_fields={entry: public_names(names) for entry, names in schema.unknown_response_fields.items()},
        sortable_response_fields={
            entry: public_names(names) for entry, names in schema.sortable_response_fields.items()
        },
        property_definitions={
            entry: {name: value for name, value in definitions.items() if name not in _INTERNAL_STORE_PROPERTIES}
            for entry, definitions in schema.property_definitions.items()
        },
    )


class AltermagnetStoreAdapter:
    """Thin AMDB envelope policy over the generic lazy store adapter.

    Filtering, sorting, counting, pagination, and hydration remain in the
    underlying store. This layer only restores deployment-owned public IDs,
    relationships, and absolute figure URLs on the bounded returned page.
    """

    def __init__(self, store: Any, public_base_url: str) -> None:
        self._adapter = adapter_from_stores(
            (
                StoredEntrySource(store, material_store.AltermagnetStructureEntry, "amdb-structures"),
                StoredEntrySource(store, material_store.AltermagnetReferenceEntry, "amdb-references"),
                # Serves the producing runs at _httk_runs so a structure serves the
                # runs' derived reverse StrongLink relationships (_httk_is_artifact/
                # _httk_is_output) and the runs serve their forward _httk_has_* edges.
                # The id/filter/sort remaps below are structures/references-scoped, so
                # runs pass through this envelope unmangled (audited).
                StoredEntrySource(store, material_store.RunEntry, "amdb-runs"),
                # The records and files families the runs' edges point at. Both serve
                # raw store-minted ids (anyt.am.records-1-N / anyt.am.files-1-N) with no
                # public-id column, so the id/filter/sort remaps deliberately skip them;
                # served files pages get their tree-relative url rewritten to the byte
                # route below.
                StoredEntrySource(store, material_store.AltermagnetDataRecordEntry, "amdb-records"),
                StoredEntrySource(store, material_store.FileEntry, "amdb-files"),
            ),
            sortable=SORTABLE_PROPERTIES,
        )
        self._public_base_url = public_base_url.rstrip("/")
        self.schema = _public_store_schema(self._adapter.schema)

    def query_function(self):
        query = self._adapter.query_function()

        def execute(
            entries: list[str],
            response_fields: list[str],
            unknown_response_fields: list[str],
            page_limit: int,
            page_offset: int,
            filter_ast: Any = None,
            *,
            as_of: int | None = None,
            sort: Sequence[tuple[str, bool]] | None = None,
            revisions: bool = False,
            alternatives: bool = False,
            immutable_id: str | None = None,
            debug: bool = False,
        ) -> _LiveResults:
            entry_type = entries[0] if len(entries) == 1 else ""
            remapped = entry_type in {"structures", "references"}
            # On the revisions/alternatives routes the engine synthesizes its own
            # id/_httk_id filters and the backend returns composite <id>~<kind> (or
            # per-revision) ids; leave those untouched so the public-id remap does
            # not mangle the synthesized filters or clobber the composite id.
            id_remapped = remapped and not (revisions or alternatives)
            requested = set(response_fields)
            fields = list(response_fields)
            if remapped and _PUBLIC_ID not in fields:
                fields.append(_PUBLIC_ID)
            if entry_type == "structures" and _REFERENCE_IDS not in fields:
                fields.append(_REFERENCE_IDS)
            store_sort = tuple(
                (_PUBLIC_ID if name == "id" and id_remapped else name, descending) for name, descending in (sort or ())
            )
            source = query(
                entries,
                fields,
                unknown_response_fields,
                page_limit,
                page_offset,
                _rewrite_id_filter(filter_ast) if id_remapped else filter_ast,
                as_of=as_of,
                sort=store_sort,
                revisions=revisions,
                alternatives=alternatives,
                immutable_id=immutable_id,
                debug=debug,
            )
            rows: list[ResultRow] = []
            for row in source:
                values = dict(row.values)
                public_id = values.get(_PUBLIC_ID)
                if id_remapped and isinstance(public_id, str):
                    values["id"] = public_id
                reference_ids = values.pop(_REFERENCE_IDS, None)
                if _PUBLIC_ID not in requested:
                    values.pop(_PUBLIC_ID, None)
                if "_httk_custom_figures" in values:
                    values["_httk_custom_figures"] = _absolute_figure_urls(
                        values["_httk_custom_figures"], self._public_base_url
                    )
                if entry_type == "files" and isinstance(values.get("id"), str):
                    # The stored url is the tree-relative locator; serve it as the
                    # absolute byte-route url (mirrors _absolute_figure_urls; absolute so
                    # the widget's www-origin fetch resolves against the api origin).
                    values["url"] = f"{self._public_base_url}/extensions/files/entry/{values['id']}"
                relationships = dict(row.relationships)
                if entry_type == "structures" and isinstance(reference_ids, list) and reference_ids:
                    relationships["references"] = [{"id": value} for value in reference_ids]
                rows.append(ResultRow(values, relationships, dict(row.property_metadata)))
            return _LiveResults(source, rows)

        return execute
