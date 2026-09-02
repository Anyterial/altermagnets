import importlib.util
import json
import sys
from pathlib import Path

from httk.serve.web.widgets import WidgetContext

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "src" / "widgets" / "material_detail.py"
if str(MODULE_PATH.parent) not in sys.path:
    sys.path.insert(0, str(MODULE_PATH.parent))
SPEC = importlib.util.spec_from_file_location("material_detail_widget", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _context() -> WidgetContext:
    return WidgetContext(
        route="material",
        render_mode="serve",
        widget_id="material-detail",
        query={},
        postvars={},
        page={"relbaseurl": "."},
        source_path=ROOT / "src" / "content" / "material.md",
        url_for=lambda value: value,
        absolute_url_for=lambda value: value,
    )


def test_material_widget_emits_shell_config_and_both_assets(monkeypatch) -> None:
    monkeypatch.setenv("ALTERMAGNETS_OPTIMADE_BASE_URL", "https://api.example.test/optimade/amdb")
    result = MODULE.render(_context())

    assert 'data-site-material-detail="1"' in result.html
    assert "serve-optimade-table-protocol.mjs" in result.html
    assert "site-material-detail.mjs" in result.html
    config_text = result.html.split('type="application/json">', 1)[1].split("</script>", 1)[0]
    config = json.loads(config_text)
    assert config["base_url"] == "https://api.example.test/optimade/amdb"
    assert config["id_query"] == "id"
    assert config["widget_id"] == "material-detail"
    assert "_anyterial_magndata_variants" in config["response_fields"]
    assert {asset.path for asset in result.assets} == {
        "serve-optimade-table-protocol.mjs",
        "site-material-detail.mjs",
    }


def test_material_widget_json_escapes_script_terminators(monkeypatch) -> None:
    monkeypatch.setenv("ALTERMAGNETS_OPTIMADE_BASE_URL", "https://api.example.test/optimade/amdb</script>&")
    result = MODULE.render(_context())
    config_text = result.html.split('type="application/json">', 1)[1].split("</script>", 1)[0]

    assert "</script>" not in config_text
    config = json.loads(config_text)
    assert config["base_url"] == "https://api.example.test/optimade/amdb</script>&"


def test_material_widget_requests_every_attribute_used_by_detail_js() -> None:
    result = MODULE.render(_context())
    config_text = result.html.split('type="application/json">', 1)[1].split("</script>", 1)[0]
    config = json.loads(config_text)
    # Pinned to the attributes.* accesses in src/widgets/material-detail.mjs.
    consumed_fields = {
        "chemical_formula_reduced",
        "_anyterial_avg_spin_splitting",
        "_anyterial_classification",
        "_anyterial_electronic_type",
        "_anyterial_elements",
        "_httk_custom_figures",
        "_httk_custom_total_energy",
        "_anyterial_formula",
        "_anyterial_icsd_ids",
        "_anyterial_magndata_variants",
        "_anyterial_magnetic_phases",
        "_anyterial_max_spin_splitting",
        "_anyterial_min_crustal_abundance",
        "_anyterial_parent_spacegroups",
        "_anyterial_space_group",
        "_anyterial_spin_splitting_fraction",
        "_anyterial_wave_classes",
        "_httk_dft_band_gap",
        "_httk_magndata_ids",
        # Structure fields consumed by the CrysViz iframe embed.
        "lattice_vectors",
        "cartesian_site_positions",
        "species",
        "species_at_sites",
        "_httk_site_moments",
    }

    assert set(config["response_fields"]) == consumed_fields


def test_material_widget_embeds_field_info_first_lines() -> None:
    config_text = MODULE.render(_context()).html.split('type="application/json">', 1)[1].split("</script>", 1)[0]
    field_info = json.loads(config_text)["field_info"]
    # Populated from the same served schema the service serves; every entry is a
    # single first-line description keyed by a requested response field.
    assert set(field_info).issubset(set(MODULE.RESPONSE_FIELDS))
    assert "_httk_dft_band_gap" in field_info
    band_gap = field_info["_httk_dft_band_gap"]["description"]
    assert band_gap.startswith("The Kohn-Sham band gap")
    assert "\n" not in band_gap
