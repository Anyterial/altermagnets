#!/usr/bin/env python3
"""One-shot migration of the committed id ledger from the JSON seal to sqlite.

Reads the legacy ``tables/amdb_ids.json`` seal document, verifies it as an audit
record, and writes an ``httk-idledger-sqlite`` ``tables/amdb_ids.sqlite`` whose
records are the JSON seal's records imported VERBATIM: no id is ever re-minted,
reordered, or altered. The records are placed into a freshly created ledger by
reaching into ``IdLedger``'s internals (underscore access, the accepted pattern
for this one-shot import — ``assign``/``alias`` would MINT), and the enclosing
context manager writes one signed segment covering all of them on close.

The new container is then reopened (full invariant + signature validation runs on
open) and checked record-for-record against the JSON seal, ``lookup`` parity is
asserted for every key, per-family assignment counts are compared, and a final
no-op reopen is asserted to leave the file byte-identical. Run once, from a
checkout that carries an operator identity; it refuses if the sqlite file exists.
"""

import hashlib
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FUNCTIONS = ROOT / "src" / "functions"
if str(FUNCTIONS) not in sys.path:
    sys.path.insert(0, str(FUNCTIONS))

import material_store
from httk.core.crypto import ed25519_public_key
from httk.core.project.anchor import format_public_key, key_fingerprint
from httk.core.project.sealing import INVALID, read_seal, resolve_seal_keys, verify_seal
from httk.store import IdLedger

TABLES = ROOT / "tables"
JSON_PATH = TABLES / material_store.LEDGER_FILENAME.replace(".sqlite", ".json")
SQLITE_PATH = TABLES / material_store.LEDGER_FILENAME

_ASSIGN_KEYS = frozenset({"key", "family", "id"})
_ALIAS_KEYS = frozenset({"key", "alias_of"})


def _canonical(record: dict[str, str]) -> tuple[str | None, ...]:
    """Return a record as an order-independent, field-complete comparison tuple."""

    return (
        record.get("key"),
        record.get("family"),
        record.get("id"),
        record.get("alias_of"),
        record.get("supersedes"),
    )


def _validate_record(record: dict[str, object], index: int) -> dict[str, str]:
    """Validate one JSON-seal record's shape and return it as a plain ``str`` dict."""

    if not all(isinstance(value, str) for value in record.values()):
        raise SystemExit(f"record {index} has a non-string value: {record!r}")
    required = frozenset(record) - {"supersedes"}
    if required not in (_ASSIGN_KEYS, _ALIAS_KEYS):
        raise SystemExit(f"record {index} has unexpected keys {sorted(record)}: {record!r}")
    return {key: str(value) for key, value in record.items()}


def _live_id(record: dict[str, str]) -> str:
    """Return the id a record binds its key to (an assignment's id or an alias target)."""

    return record["id"] if "id" in record else record["alias_of"]


def main() -> None:
    """Migrate the JSON id ledger to sqlite, then verify byte-for-record fidelity."""

    # 1. Read and verify the legacy JSON seal (audit record; refuse only on INVALID).
    seal = read_seal(JSON_PATH)
    verdict = verify_seal(JSON_PATH)
    print(f"JSON seal: {JSON_PATH}")
    print(f"  verdict: {verdict.verdict} ({verdict.reason})")
    print(f"  signers: {', '.join(verdict.signers) or 'none'}")
    if verdict.verdict == INVALID:
        raise SystemExit("refusing to migrate: the JSON seal signature is INVALID")

    subject = seal.subject
    bases = subject.get("bases")
    series = subject.get("series")
    if not isinstance(bases, dict) or not isinstance(series, str):
        raise SystemExit("JSON seal subject is missing a bases map or series")
    records = [_validate_record(dict(record), index) for index, record in enumerate(seal.records, start=1)]

    # 2. Create the sqlite ledger and import the records VERBATIM via internals.
    if SQLITE_PATH.exists():
        raise SystemExit(f"refusing to overwrite existing ledger: {SQLITE_PATH}")
    keys = resolve_seal_keys(material_store.LEDGER_SIGNER_REFS, project_root=TABLES).keys
    if not keys:
        raise SystemExit("no signing key resolved; configure an operator identity (`httk identity`)")

    with IdLedger.create(SQLITE_PATH, bases=bases, series=series, keys=keys) as ledger:
        ledger._records = [dict(record) for record in records]
        ledger._live = {}
        for record in ledger._records:
            ledger._live[record["key"]] = record  # newest record per key wins (append order)
        ledger._dirty = True
    # Context exit writes one signed segment covering every imported record.

    fingerprint = key_fingerprint(format_public_key(ed25519_public_key(keys[0][1])))

    # 3. Verification pass: reopen (full validation) and assert record-for-record fidelity.
    with IdLedger.open(SQLITE_PATH, keys=keys, bases=bases, series=series) as ledger:
        rebuilt = ledger._records
        if len(rebuilt) != len(records):
            raise SystemExit(f"record count mismatch: sqlite {len(rebuilt)} != json {len(records)}")
        for index, (want, got) in enumerate(zip(records, rebuilt, strict=True), start=1):
            if _canonical(want) != _canonical(got):
                raise SystemExit(f"record {index} differs: json={want!r} sqlite={got!r}")
        expected_live: dict[str, str] = {}
        for record in records:
            expected_live[record["key"]] = _live_id(record)  # append order -> newest wins
        for key, want_id in expected_live.items():
            got_id = ledger.lookup(key)
            if got_id != want_id:
                raise SystemExit(f"lookup parity failed for {key!r}: {got_id!r} != {want_id!r}")

    json_counts = Counter(record["family"] for record in records if "id" in record)
    sqlite_counts = Counter(record["family"] for record in rebuilt if "id" in record)
    if json_counts != sqlite_counts:
        raise SystemExit(f"per-family assignment count mismatch: json {dict(json_counts)} != sqlite {dict(sqlite_counts)}")

    # 4. Byte-identity invariant: a no-op reopen writes nothing.
    sha_before = hashlib.sha256(SQLITE_PATH.read_bytes()).hexdigest()
    with IdLedger.open(SQLITE_PATH, keys=keys, bases=bases, series=series):
        pass
    sha_after = hashlib.sha256(SQLITE_PATH.read_bytes()).hexdigest()
    if sha_before != sha_after:
        raise SystemExit(f"no-op reopen changed the file: {sha_before} != {sha_after}")

    print(f"\nMigrated {len(records)} records to {SQLITE_PATH}")
    print(f"  aliases: {sum(1 for record in records if 'alias_of' in record)}")
    print("  per-family assignment counts:")
    for family in sorted(sqlite_counts):
        print(f"    {family}: {sqlite_counts[family]}")
    print(f"  signer fingerprint: {fingerprint}")
    print(f"  sha256 (byte-identical after no-op reopen): {sha_after}")


if __name__ == "__main__":
    main()
