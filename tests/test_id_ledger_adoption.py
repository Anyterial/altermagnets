"""F3 tests: altermagnets allocates every entry id from the sealed id ledger.

These exercise the double-build id-stability headline and the ledger keying rules
(``amdb:<id>:...`` structure/run/record/file keys, ``doi:<lowered>`` reference
keys), the shared-structure alias, the explicit-id enforcement, and the sealed
in-build verification. They require a configured operator identity so the build
can sign the ledger (the build refuses to run unsigned).
"""

import json
import logging
from pathlib import Path

import duckdb
import material_store
import pytest
from conftest import write_detail_assets, write_source_tables
from httk.atomistic import CartesianSiteMoments, Cell, Sites, Species, UnitcellStructure
from httk.core import FileRecord
from httk.core.project.sealing import resolve_seal_keys
from httk.store import Backend, IdLedger, IdLedgerError, SqlStore
from material_store import (
    AltermagnetScreeningResult,
    _entry_records_layout,
    _open_ledger,
    _reference_ids_by_doi,
    _structure_key,
    _structure_mains,
    build_store,
)

#: The store table backing each ledger-managed served family, for id readback.
_FAMILY_TABLES = {
    "structures": "atomistic_unitcell_structure",
    "references": "altermagnets_references",
    "runs": "core_run",
    "records": "altermagnets_data_records",
    "files": "core_file",
}


def _served_ids(store_path: Path) -> dict[str, set[str]]:
    """Return the distinct served id set of each ledger-managed family."""
    connection = duckdb.connect(str(store_path), read_only=True)
    try:
        return {
            family: {row[0] for row in connection.execute(f"select distinct id from {table}").fetchall() if row[0]}
            for family, table in _FAMILY_TABLES.items()
        }
    finally:
        connection.close()


def _outcar(energy: float) -> str:
    """Return a minimal VASP OUTCAR whose final TOTEN is *energy*."""
    return (
        " vasp.5.2.12 synthetic\n"
        "   FREE ENERGIE OF THE ION-ELECTRON SYSTEM (eV)\n"
        f"   free  energy   TOTEN  =       {energy:.8f} eV\n"
        f"   energy  without entropy=      {energy:.8f}  energy(sigma->0) =      {energy:.8f}\n"
        " General timing and accounting informations\n"
    )


_RUN_POSCAR = """Fixture POSCAR
1.0
1 0 0
0 1 0
0 0 1
H He Li
1 1 1
Direct
0 0 0
0.5 0.5 0.5
0.25 0.25 0.25
"""


def _write_scf_run(runs: Path, material: str, *, energy: float = -1.0) -> None:
    """Write one finished v1 SCF run for *material* with a structure, energy and file output."""
    task = runs / "1" / "Runs" / f"ht.task.tetralith--default.{material}_SCF.cleanup.0.unclaimed.3.finished"
    step = task / "ht.run.2025-01-01_00.00.00" / "ht.task.any.0.cleanup.0.unclaimed.3.finished"
    inner = step / "ht.run.2025-01-01_00.00.01"
    inner.mkdir(parents=True, exist_ok=True)  # a re-write perturbs the OUTCAR in place
    (step / "POSCAR").write_text(_RUN_POSCAR, encoding="utf-8")
    (inner / "CONTCAR").write_text(_RUN_POSCAR, encoding="utf-8")
    (inner / "OUTCAR").write_text(_outcar(energy), encoding="utf-8")
    (inner / "vasprun.xml").write_text("<modeling/>\n", encoding="utf-8")


def _fixture_tree(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Return ``(tables, details, runs)`` for a coupled-run fixture store."""
    tables = write_source_tables(tmp_path / "tables")
    details = write_detail_assets(tmp_path / "details")
    runs = tmp_path / "runs"
    _write_scf_run(runs, "CrSb")  # couples anyt.am-1-1: structure, total_energy, vasprun file, run
    return tables, details, runs


def _local_signer_fingerprint(project_root: Path) -> str:
    """Return the fingerprint the build will actually sign the ledger with on THIS host.

    Derived exactly as the build derives its signing key, so the audit-surface test
    asserts the logged signer without assuming the local identity's fingerprint.
    """
    from httk.core.crypto import ed25519_public_key
    from httk.core.project.anchor import format_public_key, key_fingerprint

    _role, seed = resolve_seal_keys(material_store.LEDGER_SIGNER_REFS, project_root=project_root).keys[0]
    return key_fingerprint(format_public_key(ed25519_public_key(seed)))


def _structure(a: float) -> object:
    """Return a stored structure record whose lattice parameter is *a* (content varies with a)."""
    structure = UnitcellStructure(
        Cell([[a, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]),
        Sites([[0.0, 0.0, 0.0], [0.5, 0.0, 0.0]]),
        [Species(name="Fe", chemical_symbols=("Fe",), concentration=(1.0,))],
        ["Fe", "Fe"],
        site_moments=CartesianSiteMoments([[0.0, 0.0, 1.0], [0.0, 0.0, -1.0]]),
    )
    return material_store._material_structure_record(structure)


def _result(material_id: str, structure: object | None, *, dois: tuple[str, ...] = ()) -> AltermagnetScreeningResult:
    """Return a minimal screening result carrying only the fields the ledger paths read."""
    return AltermagnetScreeningResult(
        id=material_id,
        screening_rank=1,
        formula="Fe2",
        space_group="",
        space_group_search="",
        classification="",
        electronic_type="unknown",
        max_ss=None,
        avg_ss=None,
        fdelta_pct=None,
        bandgap=None,
        min_abund_ppm=None,
        magndata_links=(),
        figures=(),
        elements=(),
        magnetic_phases=(),
        wave_classes=(),
        parent_spacegroups=(),
        parent_spacegroups_latex=(),
        icsd_ids=(),
        dois=dois,
        search_text="",
        structure=structure,  # type: ignore[arg-type]
    )


def test_double_build_keeps_every_served_id_identical(tmp_path: Path) -> None:
    """THE HEADLINE: a from-scratch rebuild consuming the ledger serves identical ids everywhere."""
    tables, details, runs = _fixture_tree(tmp_path)
    first = build_store(tmp_path / "s1.duckdb", data_dir=tables, tables_dir=tables, details_dir=details, runs_dir=runs)
    ledger_after_first = (tables / material_store.LEDGER_FILENAME).read_bytes()
    second = build_store(tmp_path / "s2.duckdb", data_dir=tables, tables_dir=tables, details_dir=details, runs_dir=runs)

    ids_first = _served_ids(first)
    ids_second = _served_ids(second)
    assert ids_first == ids_second
    # Every family actually carries ids (an empty family would make equality vacuous).
    for family in _FAMILY_TABLES:
        assert ids_first[family], family
    # An idempotent rebuild reseals nothing: the committed ledger is byte-identical.
    assert (tables / material_store.LEDGER_FILENAME).read_bytes() == ledger_after_first


def test_record_value_change_keeps_the_same_id(tmp_path: Path) -> None:
    """Changing a record's VALUE between builds keeps its id: content becomes a revision."""
    tables, details, runs = _fixture_tree(tmp_path)
    first = build_store(tmp_path / "s1.duckdb", data_dir=tables, tables_dir=tables, details_dir=details, runs_dir=runs)
    connection = duckdb.connect(str(first), read_only=True)
    try:
        before = connection.execute("select id, value_number from altermagnets_data_records").fetchall()
    finally:
        connection.close()
    assert before and before[0][1] == -1.0

    _write_scf_run(runs, "CrSb", energy=-2.5)  # same structure (CONTCAR), different total energy
    second = build_store(
        tmp_path / "s2.duckdb", data_dir=tables, tables_dir=tables, details_dir=details, runs_dir=runs, refresh_coupling=True
    )
    connection = duckdb.connect(str(second), read_only=True)
    try:
        after = connection.execute("select id, value_number from altermagnets_data_records").fetchall()
    finally:
        connection.close()
    assert after and after[0][1] == -2.5
    assert {row[0] for row in before} == {row[0] for row in after}  # same record id, revised value


def test_shared_structure_records_one_id_and_an_alias(tmp_path: Path) -> None:
    """Two materials sharing one structure yield one assigned id and an alias of the smaller key."""
    with _open_ledger(tmp_path) as ledger:
        shared = _structure(1.0)
        id_map, mains = _structure_mains([_result("anyt.am-1-2", shared), _result("anyt.am-1-5", shared)], ledger)
        assert id_map["anyt.am-1-2"] == id_map["anyt.am-1-5"]  # one shared structure id
        assert len(mains) == 1
        owner_id = id_map["anyt.am-1-2"]  # the SMALLER key owns (sorted, build-order independent)
        assert ledger.lookup(_structure_key("anyt.am-1-2")) == owner_id
        assert ledger.lookup(_structure_key("anyt.am-1-5")) == owner_id
    document = json.loads((tmp_path / material_store.LEDGER_FILENAME).read_text())
    assigns = [r for r in document["records"] if "alias_of" in r]
    assert assigns == [{"key": _structure_key("anyt.am-1-5"), "alias_of": owner_id}]


def _records_for(tmp_path: Path, key: str) -> list[dict[str, object]]:
    """Return the ledger's append-ordered records for one key (newest last)."""
    document = json.loads((tmp_path / material_store.LEDGER_FILENAME).read_text())
    return [record for record in document["records"] if record["key"] == key]


def test_shared_structure_split_supersedes_the_departed_member(tmp_path: Path) -> None:
    """A sharer whose content splits off gets a FRESH id via supersession; the owner keeps its id.

    Fails without the wiring: re-assigning the departed member's aliased key raises
    unless the reconcile escalates to ``supersede=True``.
    """
    shared = _structure(1.0)
    with _open_ledger(tmp_path) as ledger:
        first, _mains = _structure_mains([_result("anyt.am-1-2", shared), _result("anyt.am-1-5", shared)], ledger)
    owner_id = first["anyt.am-1-2"]
    assert first["anyt.am-1-2"] == first["anyt.am-1-5"] == owner_id  # one shared id, 1-2 the smaller key owns

    with _open_ledger(tmp_path) as ledger:
        second, _mains = _structure_mains(
            [_result("anyt.am-1-2", shared), _result("anyt.am-1-5", _structure(2.0))], ledger
        )
    assert second["anyt.am-1-2"] == owner_id  # smallest-key owner keeps its id
    forked = second["anyt.am-1-5"]
    assert forked != owner_id  # the departed member forks to a fresh served id

    member = _records_for(tmp_path, _structure_key("anyt.am-1-5"))
    assert len(member) == 2  # the original alias, then the superseding assignment (append-only)
    assert member[0] == {"key": _structure_key("anyt.am-1-5"), "alias_of": owner_id}
    assert member[-1] == {
        "key": _structure_key("anyt.am-1-5"),
        "family": "structures",
        "id": forked,
        "supersedes": owner_id,
    }


def test_shared_structure_merge_supersedes_the_absorbed_assignment(tmp_path: Path) -> None:
    """Two distinct structures made content-identical merge onto the smaller key's id via a superseding alias.

    Fails without the wiring: aliasing the absorbed member's assigned key raises
    unless the reconcile escalates to ``supersede=True``.
    """
    with _open_ledger(tmp_path) as ledger:
        first, _mains = _structure_mains(
            [_result("anyt.am-1-2", _structure(1.0)), _result("anyt.am-1-7", _structure(3.0))], ledger
        )
    owner_id = first["anyt.am-1-2"]
    absorbed_id = first["anyt.am-1-7"]
    assert owner_id != absorbed_id  # two distinct structures, each its own assignment

    with _open_ledger(tmp_path) as ledger:
        merged, mains = _structure_mains(
            [_result("anyt.am-1-2", _structure(1.0)), _result("anyt.am-1-7", _structure(1.0))], ledger
        )
    assert merged["anyt.am-1-2"] == owner_id  # smallest-key owner keeps its id as the group id
    assert merged["anyt.am-1-7"] == owner_id  # the absorbed member now serves the group id
    assert len(mains) == 1  # one shared structure main after the merge

    member = _records_for(tmp_path, _structure_key("anyt.am-1-7"))
    assert len(member) == 2  # the original assignment, then the superseding alias (append-only)
    assert member[0] == {"key": _structure_key("anyt.am-1-7"), "family": "structures", "id": absorbed_id}
    assert member[-1] == {"key": _structure_key("anyt.am-1-7"), "alias_of": owner_id, "supersedes": absorbed_id}


def test_doi_case_variants_collapse_to_one_reference_id(tmp_path: Path) -> None:
    """Two DOIs differing only in case map to one reference id (keys are lower-cased)."""
    with _open_ledger(tmp_path) as ledger:
        mapping = _reference_ids_by_doi(
            [_result("anyt.am-1-1", None, dois=("10.1000/AbC",)), _result("anyt.am-1-2", None, dois=("10.1000/abc",))],
            ledger,
        )
        assert mapping["10.1000/AbC"] == mapping["10.1000/abc"]
    document = json.loads((tmp_path / material_store.LEDGER_FILENAME).read_text())
    references = [r for r in document["records"] if r.get("family") == "references"]
    assert len(references) == 1
    assert references[0]["key"] == "doi:10.1000/abc"


def test_open_rejects_a_bases_map_that_drifts_from_the_committed_file(tmp_path: Path) -> None:
    """Reopening the ledger with a mismatched bases map is a loud error, not a silent adoption.

    Defends the code-side LEDGER_BASES pin against divergence from the stored subject.
    """
    with _open_ledger(tmp_path):
        pass  # create the committed-format ledger
    keys = resolve_seal_keys(material_store.LEDGER_SIGNER_REFS, project_root=tmp_path).keys
    drifted = {**material_store.LEDGER_BASES, "structures": "anyt.am.drifted"}
    with pytest.raises(IdLedgerError, match="not the expected"):
        IdLedger.open(
            tmp_path / material_store.LEDGER_FILENAME,
            keys=keys,
            bases=drifted,
            series=material_store.LEDGER_SERIES,
        )


def test_row_without_amdb_id_is_an_error(tmp_path: Path) -> None:
    """A screening row lacking an AMDBId is a hard error (no positional fallback id)."""
    tables = write_source_tables(tmp_path / "tables")
    screening = tables / material_store.SCREENING_RESULTS_FILENAME
    text = screening.read_text(encoding="utf-8").splitlines()
    # Blank the first data row's AMDBId (semicolon-delimited, AMDBId is column 0).
    text[1] = ";" + text[1].split(";", 1)[1]
    screening.write_text("\n".join(text) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="AMDBId"):
        build_store(
            tmp_path / "store.duckdb", data_dir=tables, tables_dir=tables, details_dir=tmp_path / "details", runs_dir=tmp_path / "no-runs"
        )


def test_missing_id_is_rejected_without_a_minting_scheme(tmp_path: Path) -> None:
    """Enforcement: a ledger-managed family save without an explicit id fails loudly."""
    database = Backend.duckdb(tmp_path / "enforce.duckdb")
    try:
        store = SqlStore(database, entry_records=_entry_records_layout(), entry_ids=None)
        with pytest.raises(ValueError, match="no id"):
            store.save(FileRecord(url="payload/x", name="x"))  # id defaults to None -> no minting fallback
    finally:
        database.dispose()


def test_reopen_logs_the_signer_as_an_audit_record_and_refuses_a_tampered_ledger(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The seal is an audit record: reopening logs who signed it, and a tampered file is refused.

    Trust is not enforced (no pinned signer), but the integrity self-check always
    is: an edited byte breaks the seal and the reopen raises.
    """

    tables, details, runs = _fixture_tree(tmp_path)
    build_store(tmp_path / "s1.duckdb", data_dir=tables, tables_dir=tables, details_dir=details, runs_dir=runs)
    # Reopening logs the actual signer's fingerprint as the manual-audit surface.
    with caplog.at_level(logging.INFO, logger="httk.store.id_ledger"):
        build_store(tmp_path / "s2.duckdb", data_dir=tables, tables_dir=tables, details_dir=details, runs_dir=runs)
    message = next(record.getMessage() for record in caplog.records if "audit record" in record.getMessage())
    assert _local_signer_fingerprint(tables) in message

    # Tamper: flip a byte in the committed ledger. The signature no longer matches
    # its own content, so the reopen refuses (integrity self-check, always on).
    ledger_path = tables / material_store.LEDGER_FILENAME
    text = ledger_path.read_text(encoding="utf-8")
    ledger_path.write_text(text.replace("anyt.am.structure", "anyt.am.structured", 1), encoding="utf-8")
    with pytest.raises(IdLedgerError, match="signature does not verify|restore"):
        build_store(tmp_path / "s3.duckdb", data_dir=tables, tables_dir=tables, details_dir=details, runs_dir=runs)
