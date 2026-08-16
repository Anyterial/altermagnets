import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import publish_static


def _widget_config(document: str, marker: str) -> dict[str, object]:
    start = document.index(marker)
    payload = document[start:].split('type="application/json">', 1)[1].split("</script>", 1)[0]
    return json.loads(payload)


def test_publish_uses_absolute_optimade_base_and_no_legacy_live_tables(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ALTERMAGNETS_OPTIMADE_BASE_URL", "https://api.example.org/optimade/amdb")
    publish_static.publish_site(tmp_path)

    search = (tmp_path / "search.html").read_text(encoding="utf-8")
    material = (tmp_path / "material.html").read_text(encoding="utf-8")
    search_config = _widget_config(search, "httk-serve-optimade-table")
    assert search_config["base_url"] == "https://api.example.org/optimade/amdb"
    assert search_config["summary"]["noun"] == "screened entries"
    assert search_config["advanced_filter"]["help_url"] == "https://schemas.anyterial.se/defs/"
    assert _widget_config(material, "site-material-detail")["base_url"] == "https://api.example.org/optimade/amdb"
    for page in tmp_path.rglob("*.html"):
        text = page.read_text(encoding="utf-8")
        assert "/_httk/serve/table/" not in text
        assert "hosting: dynamic" not in text


def test_publish_defaults_all_widgets_to_nested_amdb(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("ALTERMAGNETS_OPTIMADE_BASE_URL", raising=False)
    publish_static.publish_site(tmp_path)

    search = (tmp_path / "search.html").read_text(encoding="utf-8")
    material = (tmp_path / "material.html").read_text(encoding="utf-8")
    index = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert _widget_config(search, "httk-serve-optimade-table")["base_url"] == "/optimade/amdb"
    assert _widget_config(material, "site-material-detail")["base_url"] == "/optimade/amdb"
    assert _widget_config(index, "site-stats")["base_url"] == "/optimade/amdb"
