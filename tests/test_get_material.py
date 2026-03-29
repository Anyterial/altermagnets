import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "src" / "functions" / "get_material.py"
sys.path.insert(0, str(MODULE_PATH.parent))
SPEC = importlib.util.spec_from_file_location("get_material", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_load_detail_assets_reads_sharded_svg_outputs(tmp_path: Path) -> None:
    details_root = tmp_path / "details"
    target_dir = details_root / "amdb-1" / "0" / "00" / "000" / "amdb-1-0001"
    target_dir.mkdir(parents=True)
    (target_dir / "amdb-1-0001.json").write_text(
        json.dumps({"raw_path": "2/Runs/ht.task.tetralith--default.CrSb_SCF.cleanup.0.unclaimed.3.finished"})
        + "\n",
        encoding="utf-8",
    )
    (target_dir / "band.svg").write_text(
        "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 10 10'><text>band</text></svg>",
        encoding="utf-8",
    )
    (target_dir / "structure.svg").write_text(
        "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 10 10'><text>structure</text></svg>",
        encoding="utf-8",
    )

    assets = MODULE._load_detail_assets("amdb-1-0001", {"detail_assets_root": details_root})

    assert assets["raw_path"] == "2/Runs/ht.task.tetralith--default.CrSb_SCF.cleanup.0.unclaimed.3.finished"
    assert assets["available_count"] == 2
    figures = {figure["key"]: figure for figure in assets["figures"]}
    assert figures["band"]["available"] is True
    assert figures["band"]["data_url"].startswith("data:image/svg+xml;base64,")
    assert figures["structure"]["available"] is True
    assert figures["bz"]["available"] is False


def test_load_detail_assets_returns_empty_for_missing_detail_directory(tmp_path: Path) -> None:
    details_root = tmp_path / "details"

    assets = MODULE._load_detail_assets("amdb-1-0001", {"detail_assets_root": details_root})

    assert assets["raw_path"] == ""
    assert assets["available_count"] == 0
    assert all(figure["available"] is False for figure in assets["figures"])


def test_load_detail_assets_handles_binary_svg_and_bad_metadata_safely(tmp_path: Path) -> None:
    details_root = tmp_path / "details"
    target_dir = details_root / "amdb-1" / "0" / "00" / "000" / "amdb-1-0001"
    target_dir.mkdir(parents=True)

    # Metadata file contains invalid JSON and should be ignored safely.
    (target_dir / "amdb-1-0001.json").write_text("{not valid json", encoding="utf-8")

    # SVG file contains arbitrary binary-like payload with invalid UTF-8 bytes.
    (target_dir / "band.svg").write_bytes(b"\x89\xff\x00<svg>\xfe\xfa</svg>")

    assets = MODULE._load_detail_assets("amdb-1-0001", {"detail_assets_root": details_root})

    assert assets["raw_path"] == ""
    assert assets["available_count"] == 1
    figures = {figure["key"]: figure for figure in assets["figures"]}
    assert figures["band"]["available"] is True
    assert figures["band"]["data_url"].startswith("data:image/svg+xml;base64,")


def test_load_detail_assets_ignores_oversized_svg_files(tmp_path: Path) -> None:
    details_root = tmp_path / "details"
    target_dir = details_root / "amdb-1" / "0" / "00" / "000" / "amdb-1-0001"
    target_dir.mkdir(parents=True)

    oversized_bytes = b"<svg>" + (b"a" * 120) + b"</svg>"
    (target_dir / "band.svg").write_bytes(oversized_bytes)

    assets = MODULE._load_detail_assets("amdb-1-0001", {"detail_assets_root": details_root, "max_svg_bytes": 64})

    figures = {figure["key"]: figure for figure in assets["figures"]}
    assert figures["band"]["available"] is False
    assert figures["band"]["data_url"] == ""


def test_load_detail_assets_respects_configured_max_svg_bytes(tmp_path: Path) -> None:
    details_root = tmp_path / "details"
    target_dir = details_root / "amdb-1" / "0" / "00" / "000" / "amdb-1-0001"
    target_dir.mkdir(parents=True)

    svg_bytes = b"<svg>" + (b"a" * 120) + b"</svg>"
    (target_dir / "band.svg").write_bytes(svg_bytes)

    low_cap_assets = MODULE._load_detail_assets(
        "amdb-1-0001",
        {"detail_assets_root": details_root, "max_svg_bytes": 64},
    )
    high_cap_assets = MODULE._load_detail_assets(
        "amdb-1-0001",
        {"detail_assets_root": details_root, "max_svg_bytes": 4096},
    )

    low_figures = {figure["key"]: figure for figure in low_cap_assets["figures"]}
    high_figures = {figure["key"]: figure for figure in high_cap_assets["figures"]}
    assert low_figures["band"]["available"] is False
    assert high_figures["band"]["available"] is True


def test_svg_dark_variant_injects_text_style_for_default_black_glyphs() -> None:
    svg = """<svg xmlns='http://www.w3.org/2000/svg'>
  <g id='text_1'>
    <defs><path id='glyph_a' d='M 0 0 L 1 1'/></defs>
    <use xlink:href='#glyph_a'/>
  </g>
</svg>"""
    dark_svg = MODULE._svg_dark_variant(svg)
    assert 'id="httk-dark-svg-text"' in dark_svg
    assert 'g[id^="text_"] path' in dark_svg
    assert MODULE.SVG_DARK_LIGHT_COLOR in dark_svg
