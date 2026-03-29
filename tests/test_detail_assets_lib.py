import importlib.util
import sys
import bz2
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "tools" / "detail_assets_lib.py"
sys.path.insert(0, str(MODULE_PATH.parent))
SPEC = importlib.util.spec_from_file_location("detail_assets_lib", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_details_dir_for_material_shards_by_numeric_id() -> None:
    details_root = Path("/tmp/details")
    result = MODULE.details_dir_for_material(details_root, "amdb-1-0123")
    assert result == details_root / "amdb-1" / "0" / "01" / "012" / "amdb-1-0123"


def test_extract_final_magnetization_section_uses_last_block() -> None:
    text = (
        "header\n"
        " magnetization (x)\n"
        "# of ion       s       p       d       tot\n"
        "------------------------------------------\n"
        "    1        0.001   0.000   0.000   0.001\n"
        "--------------------------------------------------\n"
        "tot          0.001   0.000   0.000   0.001\n"
        "\n"
        "middle\n"
        " magnetization (x)\n"
        "# of ion       s       p       d       tot\n"
        "------------------------------------------\n"
        "    1        0.002   0.000   0.000   0.002\n"
        "--------------------------------------------------\n"
        "tot          0.002   0.000   0.000   0.002\n"
        "\n"
        "footer\n"
    )

    section = MODULE.extract_final_magnetization_section(text)

    assert section.startswith(" magnetization (x)\n")
    assert "0.002" in section
    assert "0.001" not in section
    assert section.endswith("\n")


def test_parse_task_name_handles_scf_and_band_tasks() -> None:
    assert MODULE.parse_task_name("ht.task.tetralith--default.CsCoCl3_SCF.cleanup.0.unclaimed.2.finished") == (
        "CsCoCl3",
        "SCF",
    )
    assert MODULE.parse_task_name("ht.task.tetralith--default.CsCoCl3_SCF_BAND.cleanup.0.unclaimed.2.finished") == (
        "CsCoCl3",
        "SCF_BAND",
    )


def test_formula_from_task_label_strips_variant_suffix() -> None:
    assert MODULE.formula_from_task_label("CsCoCl3-2") == "CsCoCl3"
    assert MODULE.formula_from_task_label("CrSb") == "CrSb"


def test_save_svg_with_png_fallback_writes_png_for_large_svg(tmp_path: Path) -> None:
    class FakeFigure:
        def savefig(self, path, format=None, **kwargs):
            target = Path(path)
            if format == "svg":
                target.write_bytes(b"<svg>" + (b"a" * 200) + b"</svg>")
            elif format == "png":
                target.write_bytes(b"\x89PNG\r\n\x1a\n")
            else:
                raise ValueError(f"Unexpected format: {format}")

    svg_path = tmp_path / "band.svg"
    wrote_png = MODULE.save_svg_with_png_fallback(
        FakeFigure(),
        svg_path,
        svg_to_png_fallback_bytes=64,
    )

    assert wrote_png is True
    assert svg_path.exists()
    assert (tmp_path / "band.png").exists()


def test_save_svg_with_png_fallback_skips_png_for_small_svg(tmp_path: Path) -> None:
    class FakeFigure:
        def savefig(self, path, format=None, **kwargs):
            target = Path(path)
            if format == "svg":
                target.write_bytes(b"<svg/>")
            elif format == "png":
                target.write_bytes(b"\x89PNG\r\n\x1a\n")
            else:
                raise ValueError(f"Unexpected format: {format}")

    svg_path = tmp_path / "bz.svg"
    wrote_png = MODULE.save_svg_with_png_fallback(
        FakeFigure(),
        svg_path,
        svg_to_png_fallback_bytes=64,
    )

    assert wrote_png is False
    assert svg_path.exists()
    assert not (tmp_path / "bz.png").exists()


def test_preferred_task_favors_canonical_runs_path() -> None:
    canonical = MODULE.RawTask(
        task_root=Path("/tmp/raw/2/Runs/canonical"),
        deepest_run_dir=Path("/tmp/raw/2/Runs/canonical/ht.run.1"),
        raw_relpath="2/Runs/ht.task.tetralith--default.CrSb_SCF.cleanup.0.unclaimed.3.finished",
        task_label="CrSb",
        kind="SCF",
        batch_number=2,
        latest_run_token="ht.run.2025-01-01_00.00.00",
        formula="CrSb",
        space_group="P6_3/mmc",
    )
    failed_job = MODULE.RawTask(
        task_root=Path("/tmp/raw/2/band_step/failed_jobs/new/Runs/failed"),
        deepest_run_dir=Path("/tmp/raw/2/band_step/failed_jobs/new/Runs/failed/ht.run.2"),
        raw_relpath="2/band_step/failed_jobs/new/Runs/ht.task.tetralith--default.CrSb_SCF.cleanup.0.unclaimed.3.finished",
        task_label="CrSb",
        kind="SCF",
        batch_number=2,
        latest_run_token="ht.run.2025-12-31_23.59.59",
        formula="CrSb",
        space_group="P6_3/mmc",
    )

    assert MODULE.preferred_task([failed_job, canonical]) == canonical


def test_load_screening_entries_prefers_explicit_amdb_id_column(tmp_path: Path) -> None:
    tables_dir = tmp_path / "tables"
    tables_dir.mkdir()
    (tables_dir / "high_throughput_screening_results_fixed.csv").write_text(
        "AMDBId;MAGNDATA ID;Material;Space group;FdeltaPct;MaxSS;AvgSS;Bandgap;MinAbundPpm\n"
        "amdb-1-9001;0.528;CrSb;P6_3/mmc;34.375;1.8724;0.763170313;0.0;0.2\n",
        encoding="utf-8",
    )

    entries = MODULE.load_screening_entries(tables_dir)

    assert [entry.material_id for entry in entries] == ["amdb-1-9001"]


def test_generate_material_details_writes_sharded_raw_artifacts(tmp_path: Path, monkeypatch) -> None:
    tables_dir = tmp_path / "tables"
    raw_dir = tmp_path / "raw"
    details_dir = tmp_path / "details"
    tables_dir.mkdir()
    deepest_run_dir = raw_dir / "2" / "Runs" / "ht.task.tetralith--default.CrSb_SCF.cleanup.0.unclaimed.2.finished" / "ht.run.2025-01-01_00.00.00"
    deepest_run_dir.mkdir(parents=True)

    (tables_dir / "high_throughput_screening_results_fixed.csv").write_text(
        "MAGNDATA ID;Material;Space group;FdeltaPct;MaxSS;AvgSS;Bandgap;MinAbundPpm\n"
        "0.528;CrSb;P6_3/mmc;34.375;1.8724;0.763170313;0.0;0.2\n",
        encoding="utf-8",
    )
    with bz2.open(deepest_run_dir / "POSCAR.bz2", "wt", encoding="utf-8") as handle:
        handle.write("POSCAR placeholder\n")
    with bz2.open(deepest_run_dir / "CONTCAR.bz2", "wt", encoding="utf-8") as handle:
        handle.write("CONTCAR placeholder\n")
    with bz2.open(deepest_run_dir / "OUTCAR.bz2", "wt", encoding="utf-8") as handle:
        handle.write(
            "header\n"
            " magnetization (x)\n"
            "# of ion       s       p       d       tot\n"
            "------------------------------------------\n"
            "    1        0.002   0.000   0.000   0.002\n"
            "--------------------------------------------------\n"
            "tot          0.002   0.000   0.000   0.002\n"
            "\n"
        )

    fake_task = MODULE.RawTask(
        task_root=deepest_run_dir.parent,
        deepest_run_dir=deepest_run_dir,
        raw_relpath="2/Runs/ht.task.tetralith--default.CrSb_SCF.cleanup.0.unclaimed.2.finished",
        task_label="CrSb",
        kind="SCF",
        batch_number=2,
        latest_run_token="ht.run.2025-01-01_00.00.00",
        formula="CrSb",
        space_group="P6_3/mmc",
    )

    def fake_discover_tasks(raw_root: Path, *, kind: str, material_formulas=None):
        if kind == "SCF":
            return ({fake_task.key(): fake_task}, {})
        return ({}, {})

    monkeypatch.setattr(MODULE, "discover_tasks", fake_discover_tasks)

    results, warnings = MODULE.generate_material_details(
        tables_dir=tables_dir,
        raw_dir=raw_dir,
        details_dir=details_dir,
        render_plots=False,
    )

    target_dir = details_dir / "amdb-1" / "0" / "00" / "000" / "amdb-1-0001"
    assert len(results) == 1
    assert results[0].details_dir == target_dir
    assert warnings == []
    assert (target_dir / "POSCAR.bz2").exists()
    assert (target_dir / "CONTCAR.bz2").exists()
    assert (target_dir / "MAGN.bz2").exists()
    assert (target_dir / "amdb-1-0001.json").exists()
    assert (details_dir / "parse.log").exists()
    assert "\"raw_path\": \"2/Runs/ht.task.tetralith--default.CrSb_SCF.cleanup.0.unclaimed.2.finished\"" in (
        target_dir / "amdb-1-0001.json"
    ).read_text(encoding="utf-8")

    with bz2.open(target_dir / "MAGN.bz2", "rt", encoding="utf-8") as handle:
        assert "magnetization (x)" in handle.read()

    second_results, second_warnings = MODULE.generate_material_details(
        tables_dir=tables_dir,
        raw_dir=raw_dir,
        details_dir=details_dir,
        render_plots=False,
    )

    assert second_results == []
    assert second_warnings == []


def test_generate_material_details_parallelizes_jobs_and_serializes_parse_log(
    tmp_path: Path,
    monkeypatch,
) -> None:
    tables_dir = tmp_path / "tables"
    raw_dir = tmp_path / "raw"
    details_dir = tmp_path / "details"
    tables_dir.mkdir()
    (tables_dir / "high_throughput_screening_results_fixed.csv").write_text(
        "MAGNDATA ID;Material;Space group;FdeltaPct;MaxSS;AvgSS;Bandgap;MinAbundPpm\n"
        "0.528;CrSb;P6_3/mmc;34.375;1.8724;0.763170313;0.0;0.2\n"
        "0.607;RuO2;P4_2/mnm;25.0;0.8654;0.350574667;0.0;0.001\n",
        encoding="utf-8",
    )

    def make_fake_task(batch: str, label: str, formula: str, space_group: str) -> MODULE.RawTask:
        deepest_run_dir = raw_dir / batch / "Runs" / f"ht.task.tetralith--default.{label}_SCF.cleanup.0.unclaimed.2.finished" / "ht.run.2025-01-01_00.00.00"
        deepest_run_dir.mkdir(parents=True)
        with bz2.open(deepest_run_dir / "POSCAR.bz2", "wt", encoding="utf-8") as handle:
            handle.write("POSCAR placeholder\n")
        with bz2.open(deepest_run_dir / "CONTCAR.bz2", "wt", encoding="utf-8") as handle:
            handle.write("CONTCAR placeholder\n")
        with bz2.open(deepest_run_dir / "OUTCAR.bz2", "wt", encoding="utf-8") as handle:
            handle.write(
                " magnetization (x)\n"
                "# of ion       s       p       d       tot\n"
                "------------------------------------------\n"
                "    1        0.002   0.000   0.000   0.002\n"
                "--------------------------------------------------\n"
                "tot          0.002   0.000   0.000   0.002\n"
                "\n"
            )
        return MODULE.RawTask(
            task_root=deepest_run_dir.parent,
            deepest_run_dir=deepest_run_dir,
            raw_relpath=f"{batch}/Runs/ht.task.tetralith--default.{label}_SCF.cleanup.0.unclaimed.2.finished",
            task_label=label,
            kind="SCF",
            batch_number=int(batch),
            latest_run_token="ht.run.2025-01-01_00.00.00",
            formula=formula,
            space_group=space_group,
        )

    fake_crsb = make_fake_task("2", "CrSb", "CrSb", "P6_3/mmc")
    fake_ruo2 = make_fake_task("3", "RuO2", "RuO2", "P4_2/mnm")

    def fake_discover_tasks(raw_root: Path, *, kind: str, material_formulas=None):
        if kind == "SCF":
            return (
                {fake_crsb.key(): fake_crsb, fake_ruo2.key(): fake_ruo2},
                {},
            )
        return ({}, {})

    monkeypatch.setattr(MODULE, "discover_tasks", fake_discover_tasks)

    results, warnings = MODULE.generate_material_details(
        tables_dir=tables_dir,
        raw_dir=raw_dir,
        details_dir=details_dir,
        render_plots=False,
        workers=2,
    )

    assert sorted(result.material_id for result in results) == ["amdb-1-0001", "amdb-1-0002"]
    assert warnings == []
    parse_log_lines = (details_dir / "parse.log").read_text(encoding="utf-8").splitlines()
    assert len(parse_log_lines) == 2


def test_generate_material_details_parallel_logs_failures_serially(
    tmp_path: Path,
    monkeypatch,
) -> None:
    tables_dir = tmp_path / "tables"
    raw_dir = tmp_path / "raw"
    details_dir = tmp_path / "details"
    tables_dir.mkdir()
    (tables_dir / "high_throughput_screening_results_fixed.csv").write_text(
        "MAGNDATA ID;Material;Space group;FdeltaPct;MaxSS;AvgSS;Bandgap;MinAbundPpm\n"
        "0.528;CrSb;P6_3/mmc;34.375;1.8724;0.763170313;0.0;0.2\n"
        "0.607;RuO2;P4_2/mnm;25.0;0.8654;0.350574667;0.0;0.001\n",
        encoding="utf-8",
    )

    def make_fake_task(
        batch: str,
        label: str,
        formula: str,
        space_group: str,
        *,
        valid_outcar: bool,
    ) -> MODULE.RawTask:
        deepest_run_dir = raw_dir / batch / "Runs" / f"ht.task.tetralith--default.{label}_SCF.cleanup.0.unclaimed.2.finished" / "ht.run.2025-01-01_00.00.00"
        deepest_run_dir.mkdir(parents=True)
        with bz2.open(deepest_run_dir / "POSCAR.bz2", "wt", encoding="utf-8") as handle:
            handle.write("POSCAR placeholder\n")
        with bz2.open(deepest_run_dir / "CONTCAR.bz2", "wt", encoding="utf-8") as handle:
            handle.write("CONTCAR placeholder\n")
        outcar_text = (
            " magnetization (x)\n"
            "# of ion       s       p       d       tot\n"
            "------------------------------------------\n"
            "    1        0.002   0.000   0.000   0.002\n"
            "--------------------------------------------------\n"
            "tot          0.002   0.000   0.000   0.002\n"
            "\n"
            if valid_outcar
            else "header only\n"
        )
        with bz2.open(deepest_run_dir / "OUTCAR.bz2", "wt", encoding="utf-8") as handle:
            handle.write(outcar_text)
        return MODULE.RawTask(
            task_root=deepest_run_dir.parent,
            deepest_run_dir=deepest_run_dir,
            raw_relpath=f"{batch}/Runs/ht.task.tetralith--default.{label}_SCF.cleanup.0.unclaimed.2.finished",
            task_label=label,
            kind="SCF",
            batch_number=int(batch),
            latest_run_token="ht.run.2025-01-01_00.00.00",
            formula=formula,
            space_group=space_group,
        )

    fake_crsb = make_fake_task("2", "CrSb", "CrSb", "P6_3/mmc", valid_outcar=True)
    fake_ruo2 = make_fake_task("3", "RuO2", "RuO2", "P4_2/mnm", valid_outcar=False)

    def fake_discover_tasks(raw_root: Path, *, kind: str, material_formulas=None):
        if kind == "SCF":
            return (
                {fake_crsb.key(): fake_crsb, fake_ruo2.key(): fake_ruo2},
                {},
            )
        return ({}, {})

    monkeypatch.setattr(MODULE, "discover_tasks", fake_discover_tasks)

    results, warnings = MODULE.generate_material_details(
        tables_dir=tables_dir,
        raw_dir=raw_dir,
        details_dir=details_dir,
        render_plots=False,
        workers=2,
    )

    assert [result.material_id for result in results] == ["amdb-1-0001"]
    assert len(warnings) == 1
    assert "amdb-1-0002" in warnings[0]
    records = [json.loads(line) for line in (details_dir / "parse.log").read_text(encoding="utf-8").splitlines()]
    assert sorted(record["status"] for record in records) == ["failed", "ok"]
    assert sorted(record["material_id"] for record in records) == ["amdb-1-0001", "amdb-1-0002"]
