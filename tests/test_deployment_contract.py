"""Pin the public deployment contract so the README snippet cannot silently rot.

Production inserts only the repo's ``server/`` directory on ``sys.path`` and
imports ``from serve import create_combined_app``. These checks run in a clean
subprocess (no pytest conftest path fiddling leaking in) so they exercise the
exact contract a deployment sees.
"""

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SERVER = REPO / "server"


def _run(source: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", source],
        capture_output=True,
        text=True,
        cwd=str(REPO),
    )


def test_deployment_contract_constructs_app() -> None:
    source = f"""
import sys
sys.path.insert(0, {str(SERVER)!r})
from serve import create_combined_app
app = create_combined_app(public_base_url="https://altermagnets.anyterial.se/")
assert app is not None
print("deployment contract OK")
"""
    result = _run(source)
    assert result.returncode == 0, result.stderr
    assert "deployment contract OK" in result.stdout


def test_no_stale_optimade_under_repo() -> None:
    """A stale in-repo ``optimade`` package must not reappear (the real PyPI one is fine)."""
    source = f"""
import sys
from pathlib import Path
sys.path.insert(0, {str(SERVER)!r})
repo = Path({str(REPO)!r}).resolve()
try:
    import optimade
except ImportError:
    print("ok: no optimade")
else:
    locations = [Path(p).resolve() for p in (getattr(optimade, "__path__", None) or [])]
    if getattr(optimade, "__file__", None):
        locations.append(Path(optimade.__file__).resolve())
    stale = [p for p in locations if repo in p.parents or p == repo]
    if stale:
        raise SystemExit(f"a stale in-repo 'optimade' package resurfaced: {{stale}}")
    print("ok: optimade is external")
"""
    result = _run(source)
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("ok:") or "\nok:" in result.stdout
