#!/usr/bin/env python3

import argparse
import csv
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, UTC
from pathlib import Path


@dataclass(frozen=True)
class SymmetryMatch:
    table_name: str
    magndata_id: str
    chemical_formula: str
    parent_spacegroup: str


@dataclass(frozen=True)
class PotentialIssue:
    material_entry_id: str
    material: str
    screening_ids: str
    screening_formula: str
    missing_id: str
    suggested_id: str
    added_zeros: int
    matches: tuple[SymmetryMatch, ...]


class FormulaParseError(ValueError):
    pass


def _default_data_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "data"


def _clean_formula_text(text: str) -> str:
    return re.sub(r"\\allowbreak|\s+", "", text or "")


def _parse_number(text: str, start: int) -> tuple[int, int]:
    end = start
    while end < len(text) and text[end].isdigit():
        end += 1
    return (int(text[start:end] or "1"), end)


def _parse_formula_counts(text: str) -> Counter[str]:
    formula = _clean_formula_text(text)
    index = 0

    def parse_group(stop_char: str | None = None) -> Counter[str]:
        nonlocal index
        counts: Counter[str] = Counter()
        while index < len(formula):
            char = formula[index]
            if stop_char is not None and char == stop_char:
                index += 1
                return counts
            if char in "([":
                index += 1
                subgroup = parse_group(")" if char == "(" else "]")
                multiplier, index_after = _parse_number(formula, index)
                index = index_after
                for element, amount in subgroup.items():
                    counts[element] += amount * multiplier
                continue
            if char.isupper():
                end = index + 1
                while end < len(formula) and formula[end].islower():
                    end += 1
                element = formula[index:end]
                multiplier, index_after = _parse_number(formula, end)
                counts[element] += multiplier
                index = index_after
                continue
            raise FormulaParseError(f"Unsupported formula token {char!r} in {text!r}")
        if stop_char is not None:
            raise FormulaParseError(f"Missing closing delimiter {stop_char!r} in {text!r}")
        return counts

    result = parse_group()
    if index != len(formula):
        raise FormulaParseError(f"Unparsed trailing characters in {text!r}")
    return result


def _formulas_equivalent(left: str, right: str) -> bool:
    left_clean = _clean_formula_text(left)
    right_clean = _clean_formula_text(right)
    if left_clean == right_clean:
        return True
    try:
        return _parse_formula_counts(left) == _parse_formula_counts(right)
    except FormulaParseError:
        return False


def _load_screening_rows(data_dir: Path) -> list[dict[str, str]]:
    path = data_dir / "high_throughput_screening_results.csv"
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle, delimiter=";"))


def _load_symmetry_matches(data_dir: Path) -> dict[str, list[SymmetryMatch]]:
    index: dict[str, list[SymmetryMatch]] = {}
    for table_name in ("altermagnets_collinear.csv", "altermagnets_noncollinear.csv"):
        path = data_dir / table_name
        with path.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                magndata_id = (row.get("MAGNDATAId") or "").strip()
                if not magndata_id:
                    continue
                index.setdefault(magndata_id, []).append(
                    SymmetryMatch(
                        table_name=table_name,
                        magndata_id=magndata_id,
                        chemical_formula=(row.get("ChemicalFormula") or "").strip(),
                        parent_spacegroup=(row.get("ParentSpacegroup") or "").strip(),
                    )
                )
    return index


def _split_screening_ids(text: str) -> list[str]:
    return [part.strip() for part in (text or "").split(",") if part.strip()]


def _find_potential_issues(
    screening_rows: list[dict[str, str]],
    symmetry_index: dict[str, list[SymmetryMatch]],
    *,
    max_added_zeros: int,
) -> tuple[list[PotentialIssue], list[tuple[str, str, str]]]:
    issues: list[PotentialIssue] = []
    unmatched: list[tuple[str, str, str]] = []

    for row_index, row in enumerate(screening_rows, start=1):
        entry_id = f"amdb-{row_index:04d}"
        material = (row.get("Material") or "").strip()
        screening_ids_text = (row.get("MAGNDATA ID") or "").strip()
        for missing_id in _split_screening_ids(screening_ids_text):
            if missing_id in symmetry_index:
                continue
            issue: PotentialIssue | None = None
            for added_zeros in range(1, max_added_zeros + 1):
                suggested_id = missing_id + ("0" * added_zeros)
                matches = tuple(
                    match
                    for match in symmetry_index.get(suggested_id, [])
                    if _formulas_equivalent(material, match.chemical_formula)
                )
                if not matches:
                    continue
                issue = PotentialIssue(
                    material_entry_id=entry_id,
                    material=material,
                    screening_ids=screening_ids_text,
                    screening_formula=material,
                    missing_id=missing_id,
                    suggested_id=suggested_id,
                    added_zeros=added_zeros,
                    matches=matches,
                )
                break
            if issue is not None:
                issues.append(issue)
            else:
                unmatched.append((entry_id, material, missing_id))

    return issues, unmatched


def _format_match_summary(issue: PotentialIssue) -> str:
    unique_formulas = sorted({match.chemical_formula for match in issue.matches})
    unique_spacegroups = sorted({match.parent_spacegroup for match in issue.matches if match.parent_spacegroup})
    unique_tables = sorted({match.table_name for match in issue.matches})
    formula_note = ""
    if any(_clean_formula_text(formula) != _clean_formula_text(issue.screening_formula) for formula in unique_formulas):
        formula_note = " (formula text differs, but parsed composition matches)"
    return "\n".join(
        [
            f"{issue.material_entry_id} | {issue.material}",
            f"  screening MAGNDATA IDs: {issue.screening_ids}",
            f"  potential truncation: {issue.missing_id} -> {issue.suggested_id} (+{issue.added_zeros} zero{'s' if issue.added_zeros != 1 else ''})",
            f"  matching table(s): {', '.join(unique_tables)}",
            f"  matching formula(s): {', '.join(unique_formulas)}{formula_note}",
            (
                f"  parent spacegroup(s): {', '.join(unique_spacegroups)}"
                if unique_spacegroups
                else "  parent spacegroup(s): n/a"
            ),
        ]
    )


def _build_report(
    issues: list[PotentialIssue],
    unmatched: list[tuple[str, str, str]],
    screening_rows: list[dict[str, str]],
    *,
    max_added_zeros: int,
) -> str:
    total_ids = sum(len(_split_screening_ids(row.get("MAGNDATA ID", ""))) for row in screening_rows)
    generated = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%SZ")
    lines = [
        "Potential data integrity issues for high_throughput_screening_results.csv",
        f"Generated: {generated}",
        "",
        "Detection rule:",
        "  Flag screening MAGNDATA IDs that are missing from the symmetry tables but have a chemically equivalent match",
        (
            "  at exactly the same string plus between one and "
            f"{max_added_zeros} trailing zero(s). MAGNDATA IDs are treated as strings."
        ),
        "",
        f"Screening rows checked: {len(screening_rows)}",
        f"Screening MAGNDATA references checked: {total_ids}",
        f"Potential truncation issues found: {len(issues)}",
        f"Missing references without a same-formula padded-ID match: {len(unmatched)}",
        "",
        "Flagged issues:",
        "",
    ]

    if issues:
        for index, issue in enumerate(issues, start=1):
            lines.append(f"[{index}]")
            lines.append(_format_match_summary(issue))
            lines.append("")
    else:
        lines.append("None")
        lines.append("")

    lines.append("Missing references without a same-formula padded-ID match:")
    if unmatched:
        lines.append("")
        for entry_id, material, missing_id in unmatched:
            lines.append(f"- {entry_id} | {material} | missing ID {missing_id}")
    else:
        lines.append("  None")

    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze potential MAGNDATA ID truncation issues in altermagnets data.")
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=_default_data_dir(),
        help="Directory containing the screening and symmetry CSV files.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional output path. Defaults to <data-dir>/potential_data_integrity_issues.txt.",
    )
    parser.add_argument(
        "--max-added-zeros",
        type=int,
        default=3,
        help="Maximum number of trailing zeros to try when looking for truncated MAGNDATA IDs.",
    )
    args = parser.parse_args()

    data_dir = args.data_dir.resolve()
    output_path = (args.output or (data_dir / "potential_data_integrity_issues.txt")).resolve()

    screening_rows = _load_screening_rows(data_dir)
    symmetry_index = _load_symmetry_matches(data_dir)
    issues, unmatched = _find_potential_issues(
        screening_rows,
        symmetry_index,
        max_added_zeros=max(1, args.max_added_zeros),
    )
    report = _build_report(
        issues,
        unmatched,
        screening_rows,
        max_added_zeros=max(1, args.max_added_zeros),
    )

    output_path.write_text(report, encoding="utf-8")
    print(f"Wrote {len(issues)} potential issue(s) to {output_path}")


if __name__ == "__main__":
    main()
