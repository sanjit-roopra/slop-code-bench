#!/usr/bin/env python3
"""Score each checkpoint's code quality on its PROGRAM files only.

SCB scores a checkpoint by running pinned ``scb-check`` over the whole
checkpoint snapshot. That is unfair across arms: pi-dev-team snapshots also
carry their own tests (``tests/``, ``test_*.py``) and review scratch scripts
(``.claude/memory/reviews/*.py``), which inflate total loc and clone counts,
while Claude Code snapshots hold only the program. This script re-scores every
checkpoint on the same footing.

Program files rule
------------------
A snapshot file is a program file when it is a Python source file (``*.py``;
SCB problems are Python and ``scb-check`` only scans ``.py``) AND none of the
following applies to its path relative to the snapshot root:

* any path component starts with ``.`` (``.claude/``, ``.pi/``, ``.venv/``,
  ``.git/``, ...): agent/tool state, not shipped code;
* any directory component is ``tests``, ``test``, ``plans`` or
  ``__pycache__``;
* the file name is ``conftest.py``, matches ``test_*.py`` or ``*_test.py``.

Everything else in the snapshot (README, requirements.txt, pytest.ini,
.gitignore, plans) is not scored either way, since neither ``scb-check`` nor
SCB's quality analysis reads it.

Known limits of the rule (no current snapshot hits them): tests named
``tests.py`` / ``*_tests.py``, or living in ``testing/`` / ``spec/``, would be
kept.

Method
------
For each run, problem and checkpoint that has a snapshot, the program files
are copied into ``<tmp>/snapshot/`` with their relative paths (plus the
checkpoint's ``diff.json``), then BOTH of SCB's checkpoint quality pipelines
are re-run on ``<tmp>`` with SCB's own functions, imported rather than copied
so the numbers cannot drift from SCB's:

* scb-check: ``_get_scb_check_metrics`` -- the exact command SCB runs
  (``uvx scb-check==<SCB_CHECK_VERSION> check --report --include-all``) and
  the exact report-to-metrics extraction (verbosity, erosion, clone_*,
  verbosity_flagged_*).
* radon-style quality: ``measure_snapshot_quality`` + ``save_quality_metrics``
  + ``get_quality_metrics`` -- the same calls SCB's runner makes, giving every
  quality field in ``checkpoint_results.jsonl`` (loc, sloc, files, functions,
  lint_errors, lint_per_loc, cc_*, mass.*, single_use_functions, graph_*,
  lines_added, ...). The entry file is ``config.yaml``
  ``environment.commands.entry_file`` formatted with ``problem.yaml``
  ``entry_file``, as in the runner.

So every quality metric in the output is program-only; nothing is left scored
on the whole snapshot. A scb-check failure (SCB swallows it and returns
``{}``) is raised here instead of silently printing ``-``.

``--all-files`` copies the whole snapshot instead (no exclusion). Those
numbers must equal the run's ``checkpoint_results.jsonl`` on every checked
field (``CHECKED_FIELDS``); the script checks this and exits 1 on any
mismatch.

Run inside SCB's environment (it imports ``slop_code``)::

    uv run --project .. python scripts/program_quality.py
"""

from __future__ import annotations

import argparse
import json
import math
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

import yaml
from slop_code.common import DIFF_FILENAME
from slop_code.common import SNAPSHOT_DIR_NAME
from slop_code.metrics.checkpoint.extractors import get_quality_metrics
from slop_code.metrics.driver import measure_snapshot_quality
from slop_code.metrics.quality_io import save_quality_metrics
from slop_code.metrics.checkpoint.driver import SCB_CHECK_VERSION
from slop_code.metrics.checkpoint.driver import _get_scb_check_metrics

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"

EXCLUDED_DIRS = {"tests", "test", "plans", "__pycache__"}
SCB_CHECK_FIELDS = (
    "verbosity",
    "erosion",
    "cloned_pct",
    "verbosity_flagged_pct",
    "cloned_sloc_lines",
    "verbosity_flagged_sloc_lines",
)
QUALITY_FIELDS = (
    "loc", "sloc", "total_lines", "single_comments", "lint_errors",
    "lint_fixable", "files", "functions", "methods", "classes", "statements",
    "symbols_total", "cc_max", "cc_mean", "cc_std", "cc_high_count",
    "cc_extreme_count", "high_cc_mean", "cc_normalized", "cc_concentration",
    "cc_top20", "max_nesting_depth", "lines_per_symbol",
    "single_use_functions", "trivial_wrappers", "unused_variables",
    "lint_per_loc", "graph_cyclic_dependency_mass", "graph_propagation_cost",
    "graph_dependency_entropy", "lines_added", "lines_removed",
    "mean_func_loc", "mass.cc", "mass.high_cc_pct",
)
CHECKED_FIELDS = SCB_CHECK_FIELDS + QUALITY_FIELDS


def is_program_file(rel: Path) -> bool:
    if rel.suffix != ".py":
        return False
    if any(part.startswith(".") for part in rel.parts):
        return False
    if any(part in EXCLUDED_DIRS for part in rel.parts[:-1]):
        return False
    name = rel.name
    return not (
        name == "conftest.py"
        or name.startswith("test_")
        or name.endswith("_test.py")
    )


def select_files(snapshot: Path, *, all_files: bool) -> list[Path]:
    rels = sorted(
        p.relative_to(snapshot) for p in snapshot.rglob("*") if p.is_file()
    )
    return rels if all_files else [r for r in rels if is_program_file(r)]


def line_count(path: Path) -> int:
    return len(path.read_text(errors="replace").splitlines())


def entry_file(run_dir: Path, problem: str) -> str:
    """Formatted entry file, as the runner builds it."""
    config = yaml.safe_load((run_dir / "config.yaml").read_text())
    template = config["environment"]["commands"]["entry_file"]
    name = yaml.safe_load((run_dir / problem / "problem.yaml").read_text())["entry_file"]
    return template.format(entry_file=name)


def score_checkpoint(ckpt: Path, entry: str, *, all_files: bool) -> dict[str, Any]:
    snapshot = ckpt / SNAPSHOT_DIR_NAME
    files = select_files(snapshot, all_files=all_files)
    with tempfile.TemporaryDirectory(prefix="program_quality_") as tmp:
        tmp_ckpt = Path(tmp)
        dest_root = tmp_ckpt / SNAPSHOT_DIR_NAME
        dest_root.mkdir()
        for rel in files:
            dest = dest_root / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(snapshot / rel, dest)
        if (ckpt / DIFF_FILENAME).is_file():
            shutil.copy2(ckpt / DIFF_FILENAME, tmp_ckpt / DIFF_FILENAME)
        metrics = _get_scb_check_metrics(tmp_ckpt)
        if "scb_check_version" not in metrics:
            raise RuntimeError(f"scb-check failed on {snapshot} (see warning above)")
        snap_metrics, file_metrics = measure_snapshot_quality(entry, dest_root)
        save_quality_metrics(tmp_ckpt, snap_metrics, file_metrics)
        quality = get_quality_metrics(tmp_ckpt)
    py_files = {str(r): line_count(snapshot / r) for r in files if r.suffix == ".py"}
    return {
        "files": py_files,
        "program_lines": sum(py_files.values()),
        "scb_check": metrics,
        "quality": quality,
    }


def total_loc(metrics: dict[str, Any]) -> int | None:
    """scb-check total_loc, recovered from SCB's ratio fields."""
    for count_key, pct_key in (
        ("verbosity_flagged_sloc_lines", "verbosity_flagged_pct"),
        ("cloned_sloc_lines", "cloned_pct"),
    ):
        count, pct = metrics.get(count_key), metrics.get(pct_key)
        if count and pct:
            return round(count / pct)
    # Both counts 0 but a pct present: scb-check had total_loc > 0 and
    # flagged nothing; the total is not recoverable from the ratios.
    return None


def run_arm(run_dir: Path) -> str:
    manifest = run_dir / "manifest.json"
    if manifest.is_file():
        arm = json.loads(manifest.read_text()).get("arm")
        if arm:
            return arm
    return "?"


def tests_passed(ckpt: Path) -> tuple[int, int] | None:
    path = ckpt / "evaluation.json"
    if not path.is_file():
        return None
    data = json.loads(path.read_text())
    return sum(data["pass_counts"].values()), sum(data["total_counts"].values())


def recorded_results(run_dir: Path) -> dict[tuple[str, str], dict[str, Any]]:
    path = run_dir / "checkpoint_results.jsonl"
    if not path.is_file():
        return {}
    rows = (json.loads(line) for line in path.read_text().splitlines() if line.strip())
    return {(r["problem"], r["checkpoint"]): r for r in rows}


def checkpoints(run_dir: Path):
    for problem in sorted(p for p in run_dir.iterdir() if p.is_dir()):
        for ckpt in sorted(problem.glob("checkpoint_*"), key=lambda p: int(p.name.split("_")[1])):
            if (ckpt / SNAPSHOT_DIR_NAME).is_dir():
                yield problem.name, ckpt


def compare(ours: dict[str, Any], recorded: dict[str, Any]) -> list[str]:
    diffs = []
    for field in CHECKED_FIELDS:
        a, b = ours.get(field), recorded.get(field)
        same = a == b or (
            isinstance(a, float) and isinstance(b, float) and math.isclose(a, b, rel_tol=1e-12)
        )
        if not same:
            diffs.append(f"{field}: ours={a} recorded={b}")
    return diffs


def fmt(value: Any, spec: str = ".3f") -> str:
    return "-" if value is None else format(value, spec)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "runs", nargs="*", type=Path,
        help="run directories (default: all results/2026*-*)",
    )
    parser.add_argument(
        "--all-files", action="store_true",
        help="score whole snapshots (no exclusion) and check against checkpoint_results.jsonl",
    )
    parser.add_argument(
        "--json-out", type=Path,
        help="JSON output path (default: results/program_quality.json, "
        "or results/program_quality_all_files.json with --all-files)",
    )
    args = parser.parse_args()

    runs = args.runs or sorted(p for p in RESULTS.glob("2026*-*") if p.is_dir())
    json_out = args.json_out or RESULTS / (
        "program_quality_all_files.json" if args.all_files else "program_quality.json"
    )

    rows: list[dict[str, Any]] = []
    mismatches = 0
    for run_dir in runs:
        run_dir = run_dir.resolve()
        recorded = recorded_results(run_dir)
        for problem, ckpt in checkpoints(run_dir):
            scored = score_checkpoint(
                ckpt, entry_file(run_dir, problem), all_files=args.all_files
            )
            metrics = {**scored["quality"], **scored["scb_check"]}
            passed = tests_passed(ckpt)
            row = {
                "run": run_dir.name,
                "arm": run_arm(run_dir),
                "problem": problem,
                "checkpoint": ckpt.name,
                "tests_passed": passed[0] if passed else None,
                "tests_total": passed[1] if passed else None,
                "scb_total_loc": total_loc(metrics),
                **scored,
            }
            if args.all_files:
                rec = recorded.get((problem, ckpt.name))
                if rec is None:
                    row["reproduces"] = None
                else:
                    diffs = compare(metrics, rec)
                    row["reproduces"] = not diffs
                    row["diffs"] = diffs
                    mismatches += bool(diffs)
            rows.append(row)

    json_out.write_text(json.dumps(
        {"scb_check_version": SCB_CHECK_VERSION, "all_files": args.all_files, "rows": rows},
        indent=2,
    ) + "\n")

    header = (
        "| run | arm | ckpt | tests | py files | lines | scb loc | verbosity | erosion "
        "| cloned_pct | sloc | functions | lint_errors | lint/loc | cc_max | cc_mean "
        "| mass.cc | single_use_fns |"
    )
    if args.all_files:
        header += " matches jsonl |"
    print(header)
    print("|" + "---|" * (header.count("|") - 1))
    for r in rows:
        m = {**r["quality"], **r["scb_check"]}
        tests = "-" if r["tests_passed"] is None else f"{r['tests_passed']}/{r['tests_total']}"
        line = (
            f"| {r['run']} | {r['arm']} | {r['checkpoint']} | {tests} | {len(r['files'])} "
            f"| {r['program_lines']} | {fmt(r['scb_total_loc'], 'd')} | {fmt(m.get('verbosity'))} "
            f"| {fmt(m.get('erosion'))} | {fmt(m.get('cloned_pct'))} "
            f"| {fmt(m.get('sloc'), 'd')} | {fmt(m.get('functions'), 'd')} "
            f"| {fmt(m.get('lint_errors'), 'd')} | {fmt(m.get('lint_per_loc'))} "
            f"| {fmt(m.get('cc_max'), 'd')} | {fmt(m.get('cc_mean'), '.2f')} "
            f"| {fmt(m.get('mass.cc'), '.1f')} | {fmt(m.get('single_use_functions'), 'd')} |"
        )
        if args.all_files:
            rep = r["reproduces"]
            line += " n/a (no jsonl) |" if rep is None else (" yes |" if rep else " NO |")
        print(line)
    print(f"\nwrote {json_out}", file=sys.stderr)
    for r in rows:
        for d in r.get("diffs", []):
            print(f"MISMATCH {r['run']} {r['checkpoint']}: {d}", file=sys.stderr)
    return 1 if mismatches else 0


if __name__ == "__main__":
    raise SystemExit(main())
