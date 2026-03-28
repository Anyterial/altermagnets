#!/usr/bin/env python3

import argparse
import csv
from pathlib import Path

import analyze_data_integrity as integrity


def _build_replacement_map(
    issues: list[integrity.PotentialIssue],
) -> dict[str, dict[str, str]]:
    replacements: dict[str, dict[str, str]] = {}
    for issue in issues:
        replacements.setdefault(issue.material_entry_id, {})[issue.missing_id] = issue.suggested_id
    return replacements


def _fix_rows(
    rows: list[dict[str, str]],
    replacements: dict[str, dict[str, str]],
) -> tuple[list[dict[str, str]], int]:
    fixed_rows: list[dict[str, str]] = []
    replacement_count = 0
    for row_index, row in enumerate(rows, start=1):
        entry_id = f"amdb-{row_index:04d}"
        row_replacements = replacements.get(entry_id, {})
        original_ids = integrity._split_screening_ids(row.get("MAGNDATA ID", ""))
        fixed_ids: list[str] = []
        for magndata_id in original_ids:
            replacement = row_replacements.get(magndata_id, magndata_id)
            if replacement != magndata_id:
                replacement_count += 1
            fixed_ids.append(replacement)
        fixed_row = dict(row)
        fixed_row["MAGNDATA ID"] = ", ".join(fixed_ids)
        fixed_rows.append(fixed_row)
    return fixed_rows, replacement_count


def _verify_fix(
    original_rows: list[dict[str, str]],
    fixed_rows: list[dict[str, str]],
    fieldnames: list[str],
    replacements: dict[str, dict[str, str]],
) -> tuple[int, list[str]]:
    problems: list[str] = []
    changed_rows = 0

    if len(original_rows) != len(fixed_rows):
        problems.append(f"Row count changed: {len(original_rows)} -> {len(fixed_rows)}")
        return changed_rows, problems

    for row_index, (original_row, fixed_row) in enumerate(zip(original_rows, fixed_rows, strict=False), start=1):
        entry_id = f"amdb-{row_index:04d}"
        row_changed = original_row["MAGNDATA ID"] != fixed_row["MAGNDATA ID"]
        if row_changed:
            changed_rows += 1
        expected_replacements = replacements.get(entry_id, {})
        for fieldname in fieldnames:
            if fieldname == "MAGNDATA ID":
                continue
            if original_row[fieldname] != fixed_row[fieldname]:
                problems.append(
                    f"{entry_id}: field {fieldname!r} changed unexpectedly: "
                    f"{original_row[fieldname]!r} -> {fixed_row[fieldname]!r}"
                )
        original_ids = integrity._split_screening_ids(original_row["MAGNDATA ID"])
        fixed_ids = integrity._split_screening_ids(fixed_row["MAGNDATA ID"])
        if len(original_ids) != len(fixed_ids):
            problems.append(f"{entry_id}: MAGNDATA ID count changed unexpectedly.")
            continue
        for original_id, fixed_id in zip(original_ids, fixed_ids, strict=False):
            expected_id = expected_replacements.get(original_id, original_id)
            if fixed_id != expected_id:
                problems.append(f"{entry_id}: expected {original_id!r} -> {expected_id!r}, got {fixed_id!r}")
    return changed_rows, problems


def main() -> None:
    parser = argparse.ArgumentParser(description="Write a corrected screening CSV using the MAGNDATA ID integrity analysis.")
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=integrity._default_data_dir(),
        help="Directory containing the screening and symmetry CSV files.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional output path. Defaults to <data-dir>/high_throughput_screening_results_fixed.csv.",
    )
    parser.add_argument(
        "--max-added-zeros",
        type=int,
        default=3,
        help="Maximum number of trailing zeros to consider when fixing truncated MAGNDATA IDs.",
    )
    args = parser.parse_args()

    data_dir = args.data_dir.resolve()
    input_path = data_dir / "high_throughput_screening_results.csv"
    output_path = (args.output or (data_dir / "high_throughput_screening_results_fixed.csv")).resolve()
    max_added_zeros = max(1, args.max_added_zeros)

    screening_rows = integrity._load_screening_rows(data_dir)
    symmetry_index = integrity._load_symmetry_matches(data_dir)
    issues, unmatched = integrity._find_potential_issues(
        screening_rows,
        symmetry_index,
        max_added_zeros=max_added_zeros,
    )
    replacements = _build_replacement_map(issues)

    with input_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle, delimiter=";")
        fieldnames = list(reader.fieldnames or [])
        original_rows = list(reader)

    fixed_rows, replacement_count = _fix_rows(original_rows, replacements)
    changed_rows, verification_problems = _verify_fix(original_rows, fixed_rows, fieldnames, replacements)

    if verification_problems:
        details = "\n".join(verification_problems[:20])
        raise SystemExit(f"Verification failed:\n{details}")

    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter=";")
        writer.writeheader()
        writer.writerows(fixed_rows)

    print(f"Read: {input_path}")
    print(f"Wrote: {output_path}")
    print(f"Potential issues applied: {len(issues)}")
    print(f"MAGNDATA ID replacements applied: {replacement_count}")
    print(f"Rows with first-column changes: {changed_rows}")
    print(f"Unmatched missing IDs left unchanged: {len(unmatched)}")
    print("Verified: all non-first-column values are unchanged.")


if __name__ == "__main__":
    main()
