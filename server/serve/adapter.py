"""The AMDB store-envelope policy over the generic lazy store adapter."""

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import replace as replace_dataclass
from typing import Any

import material_store
from httk.serve.optimade import adapter_from_stores
from httk.serve.optimade.model import ResultRow
from httk.store.backend.sql import StoredEntrySource

#: The AMDB main entity's served (wire) entry type. The screening science, sorting,
#: public-id remap, and envelope injection all key on it now that ``structures`` is a
#: slim standard type (its ``served_form()`` name -- see ``AltermagnetScreeningResultEntry``).
RESULT_TYPE = material_store.AltermagnetScreeningResultEntry.entry_type_definition().served_form().name

SORTABLE_PROPERTIES = {
    RESULT_TYPE: (
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
        # ``_anyterial_screening_rank`` is deliberately NOT sortable here: the
        # published rank is a curated (not query-stable) ordering; a raw sort on it
        # 400s, and the widget's legacy ``screening_rank`` alias maps to ``id`` order
        # (httk-serve rejects an empty alias value, so store order was not expressible).
    )
}

_PUBLIC_ID = "_httk_custom_public_id"
_REFERENCE_IDS = "_httk_custom_reference_ids"
_RUN_ID = "_httk_custom_run_id"
_INTERNAL_STORE_PROPERTIES = {_PUBLIC_ID, _REFERENCE_IDS, _RUN_ID}

#: Served entry types to include by default on single-entry GETs when the client
#: sends no ``include=`` (unioned automatically with ``references`` by httk-serve).
DEFAULT_INCLUDES = {
    RESULT_TYPE: ("_httk_records",),
    "_httk_runs": ("structures", "_httk_records", "files"),
}


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


#: Result properties demoted out of the default response (still described/servable
#: via an explicit ``response_fields=`` request, and still filterable): heavy or
#: rarely-needed payloads that would otherwise ride along on every default GET.
_DEMOTED_DEFAULT_FIELDS = {
    RESULT_TYPE: (
        "_httk_custom_figures",
        "_anyterial_magndata_variants",
        "_anyterial_parent_spacegroups",
        "_anyterial_icsd_ids",
        "_httk_magnetic_space_group_bns",
        "_anyterial_screening_rank",
        "_anyterial_search_text",
    )
}


def _public_store_schema(schema: Any) -> Any:
    """Hide storage-only projections and demote heavy fields from the published schema."""

    def public_names(names: Sequence[str]) -> tuple[str, ...]:
        return tuple(name for name in names if name not in _INTERNAL_STORE_PROPERTIES)

    def public_defaults(entry: str, names: Sequence[str]) -> tuple[str, ...]:
        demoted = _DEMOTED_DEFAULT_FIELDS.get(entry, ())
        return tuple(name for name in public_names(names) if name not in demoted)

    entry_info = {
        entry: {
            **info,
            "properties": {
                name: (
                    {**value, "default_response": False} if name in _DEMOTED_DEFAULT_FIELDS.get(entry, ()) else value
                )
                for name, value in info["properties"].items()
                if name not in _INTERNAL_STORE_PROPERTIES
            },
        }
        for entry, info in schema.entry_info.items()
    }
    return replace_dataclass(
        schema,
        entry_info=entry_info,
        properties_by_entry={entry: public_names(names) for entry, names in schema.properties_by_entry.items()},
        default_response_fields={
            entry: public_defaults(entry, names) for entry, names in schema.default_response_fields.items()
        },
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
                # The AMDB main entity (primary search endpoint) and the slim standard
                # structures family it references (needed so include=structures resolves).
                StoredEntrySource(store, material_store.AltermagnetScreeningResultEntry, "amdb-screening-results"),
                StoredEntrySource(store, material_store.AltermagnetStructureEntry, "amdb-structures"),
                StoredEntrySource(store, material_store.AltermagnetReferenceEntry, "amdb-references"),
                # Serves the producing runs at _httk_runs: the runs serve forward
                # _httk_has_output edges only (artifacts are retired in this
                # deployment — they duplicated outputs; see _save_reconstructed_runs),
                # and their edge targets — the typed records, the slim structure, the
                # files — serve the derived reverse _httk_is_output blocks. The result
                # reaches its run through the injected _httk_runs relationship below
                # (stamped run_id), with the records relationship for the science.
                # The id/filter/sort remaps below are result/references-scoped, so
                # runs/structures pass through unmangled.
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
            default_includes=DEFAULT_INCLUDES,
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
            remapped = entry_type in {RESULT_TYPE, "references"}
            # On the revisions/alternatives routes the engine synthesizes its own
            # id/_httk_id filters and the backend returns composite <id>~<kind> (or
            # per-revision) ids; leave those untouched so the public-id remap does
            # not mangle the synthesized filters or clobber the composite id.
            id_remapped = remapped and not (revisions or alternatives)
            requested = set(response_fields)
            fields = list(response_fields)
            if remapped and _PUBLIC_ID not in fields:
                fields.append(_PUBLIC_ID)
            # The screening result carries the private reference-id projection used
            # to inject its references relationship block (references are id-string
            # linked, not a Related field, so the federation cannot serve them; the
            # structures block IS served natively off the typed structure reference).
            if entry_type == RESULT_TYPE and _REFERENCE_IDS not in fields:
                fields.append(_REFERENCE_IDS)
            # Mirrors the reference-id projection above: the coupled run id is served
            # only through this private column, used to inject the _httk_runs
            # relationship block (references pattern) below.
            if entry_type == RESULT_TYPE and _RUN_ID not in fields:
                fields.append(_RUN_ID)
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
                run_id = values.pop(_RUN_ID, None)
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
                if entry_type == RESULT_TYPE and isinstance(reference_ids, list) and reference_ids:
                    # Envelope-inject only the references block: references are id-string
                    # linked (no Related field), so the federation cannot serve them. The
                    # structures block is served natively off the typed structure reference
                    # (E3), so row.relationships already carries it.
                    relationships["references"] = [{"id": value} for value in reference_ids]
                if entry_type == RESULT_TYPE and isinstance(run_id, str) and run_id:
                    # Envelope-inject the producing run's relationship block off the
                    # private stamped run id (the references pattern again): the run is
                    # id-string linked here, not a Related field, so the federation
                    # cannot serve it. This is already include-hydratable (the entries
                    # collector falls back to the block key -- "_httk_runs" -- as the
                    # related resource's type when no "type" key is present); only
                    # depth-1 filtering/include *through* _httk_runs would need a typed
                    # reference field here instead of this private scalar (the upgrade
                    # path noted on AltermagnetScreeningResult.run_id).
                    relationships["_httk_runs"] = [{"id": run_id}]
                rows.append(ResultRow(values, relationships, dict(row.property_metadata)))
            return _LiveResults(source, rows)

        return execute
