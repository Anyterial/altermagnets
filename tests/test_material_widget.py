import importlib.util
import json
from pathlib import Path

from httk.serve.web.widgets import WidgetContext

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "src" / "widgets" / "material_detail.py"
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
    monkeypatch.setenv("ALTERMAGNETS_OPTIMADE_BASE_URL", "https://api.example.test/optimade")
    result = MODULE.render(_context())

    assert 'data-site-material-detail="1"' in result.html
    assert "serve-optimade-table-protocol.mjs" in result.html
    assert "site-material-detail.mjs" in result.html
    config_text = result.html.split('type="application/json">', 1)[1].split("</script>", 1)[0]
    config = json.loads(config_text)
    assert config["base_url"] == "https://api.example.test/optimade"
    assert config["id_query"] == "id"
    assert config["widget_id"] == "material-detail"
    assert "_anyterial_magndata_variants" in config["response_fields"]
    assert {asset.path for asset in result.assets} == {
        "serve-optimade-table-protocol.mjs",
        "site-material-detail.mjs",
    }
