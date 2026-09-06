"""AMDB curation tool: inspect and hand-confirm run<->material ledger bindings.

The build (:mod:`build_store`) already writes bindings automatically for every
unambiguous run<->material match (see the run-coupling-ledger-bindings design).
This tool covers what the build cannot: seeing the current coupling state
(``status``) and confirming the ambiguous/unmatched leftovers by hand
(``couple``). Neither subcommand duplicates the build; both read the same id
ledger the build writes, via the same key conventions and matching helpers
(``material_store._run_key`` / ``_run_source_key`` / ``_resolve_run_binding`` /
``_couple_runs``'s matching passes).

``status`` never mutates the ledger (opened lock-free, read-only). ``couple``
with no ``--assign`` (the default, aka ``--list``) also never mutates: it
re-runs the build's matching passes against a read-only ledger and reports the
ambiguous/unmatched leftovers without writing anything -- ambiguous/unmatched
state is deliberately ephemeral and regenerable (design D5), never persisted.
``couple --assign``/``--attach``/``--assign --supersede`` open the ledger for
write and are the only three ways this tool ever mutates it:

- ``--assign M S`` binds an UNBOUND material to (a newly minted or already
  registered) run S; refuses if M is already bound to something else.
- ``--attach M S`` records that S is the intrinsic id of the run M is
  ALREADY bound to (the legacy self-migration op, exposed by hand); refuses
  if M is unbound, or if S is already registered to a different run.
- ``--assign M S --supersede`` re-binds M to S as a genuinely DIFFERENT run
  (minting S fresh if it is not registered yet -- it names a different run
  by definition), superseding whatever M was bound to before.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
FUNCTIONS = ROOT / "src" / "functions"
if str(FUNCTIONS) not in sys.path:
    sys.path.insert(0, str(FUNCTIONS))
# material_store's served projections import serve.dataset's per-field readers,
# so the server package must be importable for the curation CLI too.
SERVER = ROOT / "server"
if str(SERVER) not in sys.path:
    sys.path.insert(0, str(SERVER))

from httk.core.project.sealing import SealError
from httk.store import IdLedger, IdLedgerError
from material_store import (
    LEDGER_BASES,
    LEDGER_SERIES,
    SCREENING_RESULTS_FILENAME,
    _is_run_material,
    _load_csv_rows,
    _open_ledger,
    _resolve_run_binding,
    _result_key,
    _run_key,
    _run_observations,
    _run_owners,
    _run_source_key,
    default_data_dir,
    default_details_dir,
    default_ledger_path,
    default_runs_dir,
    details_raw_path,
    resolve_data_dir,
    resolve_details_dir,
    resolve_ledger_path,
    resolve_runs_dir,
)

#: The v1 workflow package whose collector interprets a finished tree, exactly
#: as the build resolves it (see ``build_store.build_store``).
WORKFLOW_DIR = ROOT / "workflows" / "scf_httk_v1"


def _open_read_only_ledger(ledger_path: Path) -> IdLedger:
    """Open the id ledger lock-free for reporting: never mutates, needs no signing key."""
    return IdLedger.open(ledger_path, bases=LEDGER_BASES, series=LEDGER_SERIES, read_only=True)


# -- status -------------------------------------------------------------------


def _status_data(ledger: IdLedger) -> dict[str, Any]:
    """Summarize entity/run coupling coverage from the ledger's live bindings."""
    bindings = ledger.bindings()
    entity_ids = sorted({binding.id for binding in bindings.values() if binding.family == "results"})
    unbound_ids = [entity_id for entity_id in entity_ids if bindings.get(_run_key(entity_id)) is None]

    run_keys = sorted(key for key, binding in bindings.items() if key.startswith("run:") and binding.family == "runs")
    owners = _run_owners(ledger)  # run id -> owning amdb id, derived from amdb:<id>:run keys
    unclaimed = [{"source_key": key, "run_id": bindings[key].id} for key in run_keys if bindings[key].id not in owners]

    return {
        "entities": {
            "total": len(entity_ids),
            "coupled": len(entity_ids) - len(unbound_ids),
            "unbound": len(unbound_ids),
            "unbound_ids": unbound_ids,
        },
        "runs": {
            "total": len(run_keys),
            "unclaimed": len(unclaimed),
            "unclaimed_runs": unclaimed,
        },
    }


def _print_status(data: dict[str, Any]) -> None:
    entities = data["entities"]
    runs = data["runs"]
    print(f"entities: {entities['total']} total, {entities['coupled']} coupled, {entities['unbound']} unbound")
    for amdb_id in entities["unbound_ids"]:
        print(f"  unbound    {amdb_id}")
    print(f"runs:     {runs['total']} total, {runs['unclaimed']} unclaimed")
    for entry in runs["unclaimed_runs"]:
        print(f"  unclaimed  {entry['source_key']}  (id {entry['run_id']})")


def handle_status(args: argparse.Namespace) -> int:
    """Report entity/run coupling coverage; always exits 0 (it is a report, not a check)."""
    ledger_path = resolve_ledger_path(args.ledger_path)
    with _open_read_only_ledger(ledger_path) as ledger:
        data = _status_data(ledger)
    if args.json:
        print(json.dumps(data, indent=2, sort_keys=True))
    else:
        _print_status(data)
    return 0


# -- couple --list (the ephemeral review queue) --------------------------------


def _review_queue(
    data_dir: Path, details_dir: Path, runs_dir: Path, ledger: IdLedger
) -> tuple[int, list[dict[str, Any]]]:
    """Recompute the build's ambiguous/unmatched leftovers, without mutating *ledger*.

    Mirrors ``material_store._couple_runs``'s matching passes (details ``raw_path``
    authority, then name matching) exactly, but stops before the ledger-binding
    resolution step (:func:`material_store._resolve_run_binding`), so it performs
    zero ledger writes.

    :return: The count of already-bound materials (for orientation) and the
        ambiguous/unmatched queue entries.
    """
    screening = _load_csv_rows(data_dir / SCREENING_RESULTS_FILENAME, delimiter=";")
    source_materials: dict[str, str] = {}
    materials_to_amdb: dict[str, list[str]] = {}
    for row in screening:
        material = (row.get("Material") or "").strip()
        amdb_id = ledger.lookup(_result_key(row.get("MAGNDATA ID", "")))
        if not amdb_id or not material:
            continue  # row not yet seeded into the results family; nothing to report
        source_materials[amdb_id] = material
        materials_to_amdb.setdefault(material, []).append(amdb_id)

    already_bound = {amdb_id for amdb_id in source_materials if ledger.lookup(_run_key(amdb_id)) is not None}
    pending = {amdb_id: material for amdb_id, material in source_materials.items() if amdb_id not in already_bound}

    details_paths = {amdb_id: details_raw_path(details_dir, amdb_id) for amdb_id in source_materials}
    claimed = {path for path in details_paths.values() if path}

    items: list[Any] = []
    if runs_dir.is_dir():
        from httk.workflow.compat.v1 import collect_finished_tree

        items = list(collect_finished_tree(runs_dir, workflow_dir=WORKFLOW_DIR))
    observations = _run_observations(items)

    # Never offer a run already owned by some OTHER (already-bound) material as a
    # fresh candidate -- mirrors the run_owners guard the build's ledger-binding
    # resolution applies (see material_store._resolve_run_binding).
    owned_run_ids = {run_id for amdb_id in already_bound if (run_id := ledger.lookup(_run_key(amdb_id))) is not None}
    observations = tuple(
        obs for obs in observations if ledger.lookup(_run_source_key(obs.item.run.source_id)) not in owned_run_ids
    )

    by_material: dict[str, list[Any]] = {}
    by_raw_path: dict[str, Any] = {}
    for obs in observations:
        by_material.setdefault(obs.material, []).append(obs)
        by_raw_path[obs.raw_path] = obs

    matched: dict[str, Any] = {}
    coupled_paths: set[str] = set()
    for amdb_id in sorted(pending):
        details_path = details_paths.get(amdb_id, "")
        if not details_path:
            continue
        observation = by_raw_path.get(details_path)
        if observation is None or observation.raw_path in coupled_paths:
            continue
        matched[amdb_id] = observation
        coupled_paths.add(observation.raw_path)

    queue: list[dict[str, Any]] = []
    for amdb_id, material in sorted(pending.items()):
        if amdb_id in matched:
            continue
        exact = [
            item
            for item in by_material.get(material, [])
            if item.raw_path not in coupled_paths and item.raw_path not in claimed
        ]
        variants = [
            item
            for item in observations
            if _is_run_material(item.material, material)
            and item.material != material
            and item.raw_path not in coupled_paths
            and item.raw_path not in claimed
        ]
        candidates = exact + variants
        if len(exact) == 1 and not variants and len(materials_to_amdb[material]) == 1:
            coupled_paths.add(exact[0].raw_path)
            continue  # unambiguous; the next build binds it automatically
        tag = "ambiguous" if candidates else "unmatched"
        queue.append(
            {
                "amdb_id": amdb_id,
                "formula": material,
                "tag": tag,
                "candidates": [
                    {"source_id": item.item.run.source_id, "raw_path": item.raw_path} for item in candidates
                ],
            }
        )
    return len(already_bound), queue


def _print_queue(already_bound: int, queue: list[dict[str, Any]]) -> None:
    print(f"already bound: {already_bound}")
    if not queue:
        print("review queue is empty")
        return
    for entry in queue:
        print(f"{entry['tag']}: {entry['amdb_id']} ({entry['formula']})")
        candidates = entry["candidates"]
        if not candidates:
            print("    no candidates found")
        for candidate in candidates:
            print(f"    candidate  source_id={candidate['source_id']}  raw_path={candidate['raw_path']}")


def _do_list(ledger_path: Path, args: argparse.Namespace) -> int:
    data_dir = resolve_data_dir(args.data_dir)
    details_dir = resolve_details_dir(args.details_dir)
    runs_dir = resolve_runs_dir(args.runs_dir)
    with _open_read_only_ledger(ledger_path) as ledger:
        already_bound, queue = _review_queue(data_dir, details_dir, runs_dir, ledger)
    _print_queue(already_bound, queue)
    return 0


# -- couple --assign / --attach / --assign --supersede (the write paths) -------
#
# Three distinct curator intents share the binding matrix
# (material_store._resolve_run_binding), but each needs its own guardrail:
#   --assign          bind an UNBOUND material (matrix branches A/C only).
#   --attach          declare S as an ALREADY-bound material's run's intrinsic
#                     id (branch B, exposed by hand).
#   --assign
#     --supersede     re-bind a material to S as a genuinely DIFFERENT run
#                     (mint S fresh when unregistered -- it names a different
#                     run by definition -- then supersede the old binding).
# Collapsing these into one code path is exactly the bug this split fixes:
# "attach a source id to the run M already has" and "re-bind M to a
# different run" are different intents that must never share one branch.


def _known_entity_ids(ledger: IdLedger) -> set[str]:
    return {binding.id for binding in ledger.bindings().values() if binding.family == "results"}


def _rebind(ledger: IdLedger, binding_key: str, target: str) -> None:
    """Point *binding_key* at *target*, superseding whatever it previously resolved to, if anything.

    ``alias(key, target, supersede=True)`` only re-binds a key that is currently
    an ASSIGNMENT (the split half of the ledger's regrouping API): a legacy,
    pre-redesign binding key is exactly that shape, so it takes this directly.
    Every post-redesign binding key is itself already an ALIAS (design D3), so
    it must first be split back into a throwaway assignment before it can be
    merged onto *target* -- the same two-step ceremony documented on
    ``material_store._alias_structure_member``. A never-bound key just aliases
    directly (nothing to supersede).
    """
    current = ledger.bindings().get(binding_key)
    if current is None:
        ledger.alias(binding_key, target)
    elif current.is_alias:
        ledger.assign(binding_key, "runs", supersede=True)
        ledger.alias(binding_key, target, supersede=True)
    else:
        ledger.alias(binding_key, target, supersede=True)


def _do_assign(ledger_path: Path, amdb_id: str, source_id: str) -> int:
    """Bind an unbound material (matrix branches A/C); refuse if it is already bound."""
    with _open_ledger(ledger_path) as ledger:
        if amdb_id not in _known_entity_ids(ledger):
            print(f"refusing: {amdb_id!r} is not a known entity id in the ledger's results bindings", file=sys.stderr)
            return 1
        binding_key = _run_key(amdb_id)
        source_key = _run_source_key(source_id)
        existing = ledger.lookup(binding_key)
        intrinsic = ledger.lookup(source_key)
        if existing is not None:
            if intrinsic is not None and intrinsic == existing:
                print(f"already bound: {binding_key} -> {existing}")
                return 0
            print(
                f"refusing: {amdb_id} is already bound to run {existing!r}; use --attach to register "
                f"{source_id!r} as an additional id for that same run, or --supersede to re-bind "
                f"{amdb_id} to a different run",
                file=sys.stderr,
            )
            return 1
        run_owners = _run_owners(ledger)
        run_id = _resolve_run_binding(ledger, amdb_id, source_id, run_owners)
        if run_id is None:
            print(
                f"refusing: run {source_id!r} is already bound to a different material; not stealing it",
                file=sys.stderr,
            )
            return 1
        if intrinsic is None:
            print(f"minted {source_key} -> {run_id}")
        print(f"bound: {binding_key} -> {run_id}")
        return 0


def _do_attach(ledger_path: Path, amdb_id: str, source_id: str) -> int:
    """Declare *source_id* as the intrinsic id of the run *amdb_id* is already bound to (matrix branch B)."""
    with _open_ledger(ledger_path) as ledger:
        if amdb_id not in _known_entity_ids(ledger):
            print(f"refusing: {amdb_id!r} is not a known entity id in the ledger's results bindings", file=sys.stderr)
            return 1
        binding_key = _run_key(amdb_id)
        source_key = _run_source_key(source_id)
        existing = ledger.lookup(binding_key)
        if existing is None:
            print(f"refusing: {amdb_id} is not bound to any run yet; use --assign to bind it first", file=sys.stderr)
            return 1
        intrinsic = ledger.lookup(source_key)
        if intrinsic is not None:
            if intrinsic == existing:
                print(f"already attached: {source_key} -> {existing}")
                return 0
            print(
                f"refusing: {source_key} is already registered to {intrinsic!r}, not {existing!r} ({binding_key})",
                file=sys.stderr,
            )
            return 1
        ledger.alias(source_key, existing)
        print(f"attached: {source_key} -> {existing}")
        return 0


def _do_supersede(ledger_path: Path, amdb_id: str, source_id: str) -> int:
    """Re-bind *amdb_id* to *source_id* as a genuinely different run, minting it fresh if unregistered."""
    with _open_ledger(ledger_path) as ledger:
        if amdb_id not in _known_entity_ids(ledger):
            print(f"refusing: {amdb_id!r} is not a known entity id in the ledger's results bindings", file=sys.stderr)
            return 1
        binding_key = _run_key(amdb_id)
        source_key = _run_source_key(source_id)
        existing = ledger.lookup(binding_key)
        intrinsic = ledger.lookup(source_key)
        if intrinsic is not None and intrinsic == existing:
            print(f"already bound: {binding_key} -> {existing}")
            return 0
        target = intrinsic
        if target is None:
            # S is unregistered: it names a run distinct from anything already in the
            # ledger, so a fresh id is correct here, never the id M happens to hold today.
            target = ledger.assign(source_key, "runs")
            print(f"minted {source_key} -> {target}")
        run_owners = _run_owners(ledger)
        owner = run_owners.get(target)
        if owner is not None and owner != amdb_id:
            print(f"refusing: run {source_id!r} (id {target}) is already bound to material {owner!r}", file=sys.stderr)
            return 1
        _rebind(ledger, binding_key, target)
        print(f"superseded: {binding_key} -> {target}")
        return 0


def handle_couple(args: argparse.Namespace) -> int:
    if args.supersede and args.assign is None:
        print("error: --supersede requires --assign", file=sys.stderr)
        return 2
    ledger_path = resolve_ledger_path(args.ledger_path)
    if args.assign is not None:
        amdb_id, source_id = args.assign
        if args.supersede:
            return _do_supersede(ledger_path, amdb_id, source_id)
        return _do_assign(ledger_path, amdb_id, source_id)
    if args.attach is not None:
        amdb_id, source_id = args.attach
        return _do_attach(ledger_path, amdb_id, source_id)
    return _do_list(ledger_path, args)


# -- argument parsing -----------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="curate.py", description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    status_parser = subparsers.add_parser("status", help="report entity/run coupling coverage (read-only)")
    status_parser.add_argument(
        "--ledger-path",
        type=Path,
        default=None,
        help=f"the sealed id ledger file (default: ALTERMAGNETS_LEDGER_PATH, then {default_ledger_path()})",
    )
    status_parser.add_argument("--json", action="store_true", help="emit one JSON object instead of plain text")
    status_parser.set_defaults(handler=handle_status)

    couple_parser = subparsers.add_parser("couple", help="review, and hand-confirm, run<->material bindings")
    couple_parser.add_argument(
        "--ledger-path",
        type=Path,
        default=None,
        help=f"the sealed id ledger file (default: ALTERMAGNETS_LEDGER_PATH, then {default_ledger_path()})",
    )
    couple_parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help=f"directory containing the mounted screening CSV (default: ALTERMAGNETS_DATA_DIR, then {default_data_dir()})",
    )
    couple_parser.add_argument(
        "--details-dir",
        type=Path,
        default=None,
        help=f"directory containing generated plot assets (default: ALTERMAGNETS_DETAILS_DIR, then {default_details_dir()})",
    )
    couple_parser.add_argument(
        "--runs-dir",
        type=Path,
        default=None,
        help=f"directory containing finished httk v1 runs (default: {default_runs_dir()})",
    )
    mode_group = couple_parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--list",
        action="store_true",
        help="show the ambiguous/unmatched review queue (the default when none of --assign/--attach is given)",
    )
    mode_group.add_argument(
        "--assign",
        nargs=2,
        metavar=("AMDB_ID", "SOURCE_ID"),
        default=None,
        help="bind an unbound AMDB_ID to the run with intrinsic id SOURCE_ID (add --supersede to re-bind a "
        "material that is already bound, to a different run)",
    )
    mode_group.add_argument(
        "--attach",
        nargs=2,
        metavar=("AMDB_ID", "SOURCE_ID"),
        default=None,
        help="declare SOURCE_ID as the intrinsic id of the run AMDB_ID is ALREADY bound to",
    )
    couple_parser.add_argument(
        "--supersede",
        action="store_true",
        help="with --assign, re-bind a material already bound to a different run (a deliberate re-coupling)",
    )
    couple_parser.set_defaults(handler=handle_couple)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except (IdLedgerError, FileNotFoundError, ValueError, RuntimeError, SealError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
