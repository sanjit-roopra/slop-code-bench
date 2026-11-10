#!/usr/bin/env python3
"""Compare harness arms (example-check versions and baselines) on the 10 comparison problems.

Each arm maps to one or more run directories under ``results/``. Earlier
directories win; a later directory only fills problems the earlier ones lack
(so a smoke-r1 on the target problems plus a smoke-r2 on the held-out ones
form one arm). A checkpoint counts when its ``evaluation.json`` exists.

Two views, each split into targets / held-out / all:

* ``common``: only checkpoints every compared arm has, so the arms are scored
  on the same work;
* ``available``: every checkpoint each arm has.

Per arm and group: checkpoints, core-pass (all Core tests pass), iso (Core +
Functionality + Error), strict (iso + Regression), tests passed/total, and
the cost each checkpoint's ``inference_result.json`` reports (main session
plus subagents). Also prints tests passed per problem per arm.

The common set is limited by the sparsest arm (v6 ran only 2 held-out
problems), so ``--arms`` picks the arms to compare, and with ``--v7`` a
``pairs`` view scores v7 against each other arm on the checkpoints those two
share. Warnings name an arm with no checkpoints in a group, and arms whose
run manifests set different ``smoke_checkpoints`` (a smoke run with the
run.sh default of 2 against the others' 8 shrinks the common view and flatters
the available one).

Usage:
  python3 harness_eval/scripts/cmp_versions.py [--v7 DIR [DIR ...]] [--v8 DIR [DIR ...]] [--arms a,b,...] [--json OUT] [--results DIR]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

RESULTS = Path(__file__).resolve().parents[1] / "results"

TARGETS = ("file_backup", "layered_config_synthesizer", "recli", "trajectory_api")
HELD_OUT = (
    "dynamic_config_service_api",
    "eve_market_tools",
    "eve_route_planner",
    "meshctl",
    "pwd_manager",
    "sheeteval",
)
PROBLEMS = TARGETS + HELD_OUT
GROUPS = {"targets": TARGETS, "held-out": HELD_OUT, "all": PROBLEMS}

ARM_DIRS: dict[str, list[str]] = {
    "v1": ["20261103-pidt-gate-examples-smoke-r1", "20261103-pidt-gate-examples-smoke-r2"],
    "v5": ["20261104-pidt-gate-examples5-smoke-r1", "20261105-pidt-gate-examples5-smoke-r2"],
    "v6": ["20261105-pidt-gate-examples6-smoke-r1"],
    "gate": ["20261102-pidt-gate-direct-full-r1"],
    "stock": ["20261101-pi-stock-full-r1"],
    "cc": ["20261025-cc-clean-full-r1"],
}

ISO_GROUPS = ("Core", "Functionality", "Error")
STRICT_GROUPS = ISO_GROUPS + ("Regression",)
METRICS = ("checkpoints", "core", "iso", "strict", "passed", "total", "cost")


def _checkpoint_key(name: str) -> tuple[int, str]:
    tail = name.rsplit("_", 1)[-1]
    return (int(tail) if tail.isdigit() else 10**9, name)


def read_checkpoint(cp_dir: Path) -> dict | None:
    """Scores of one checkpoint, or None when it has no readable evaluation.json."""
    try:
        ev = json.loads((cp_dir / "evaluation.json").read_text())
    except (OSError, ValueError):
        return None
    pc = ev.get("pass_counts") or {}
    tc = ev.get("total_counts") or {}

    def ok(groups: tuple[str, ...]) -> bool:
        return all(pc.get(g, 0) == tc.get(g, 0) for g in groups)

    try:
        cost = float(json.loads((cp_dir / "inference_result.json").read_text())["usage"]["cost"] or 0)
    except (OSError, ValueError, KeyError, TypeError):
        cost = 0.0
    return {
        "core": ok(("Core",)),
        "iso": ok(ISO_GROUPS),
        "strict": ok(STRICT_GROUPS),
        "passed": sum(pc.values()),
        "total": sum(tc.values()),
        "cost": cost,
    }


def run_path(results: Path, d: str) -> Path:
    """A run dir as given when it exists (absolute, or relative to the current folder), else under results/."""
    return Path(d) if Path(d).is_absolute() or Path(d).is_dir() else results / d


def smoke_checkpoints(results: Path, dirs: list[str]) -> set:
    """The ``smoke_checkpoints`` of the arm's run manifests (None for a full run or no manifest)."""
    values = set()
    for d in dirs:
        try:
            values.add(json.loads((run_path(results, d) / "manifest.json").read_text()).get("smoke_checkpoints"))
        except (OSError, ValueError, AttributeError):
            values.add(None)
    return values


def load_arm(results: Path, dirs: list[str]) -> dict[str, dict[str, dict]]:
    """{problem: {checkpoint: scores}} for the comparison problems, first dir with a problem wins."""
    arm: dict[str, dict[str, dict]] = {}
    for d in dirs:
        run = run_path(results, d)
        for problem in PROBLEMS:
            if problem in arm:
                continue
            cps = {}
            pdir = run / problem
            if pdir.is_dir():
                for cp in sorted(pdir.glob("checkpoint_*"), key=lambda p: _checkpoint_key(p.name)):
                    scores = read_checkpoint(cp)
                    if scores is not None:
                        cps[cp.name] = scores
            if cps:
                arm[problem] = cps
    return arm


def aggregate(arm: dict[str, dict[str, dict]], keys: set[tuple[str, str]] | None, problems) -> dict:
    tot = dict.fromkeys(METRICS, 0)
    tot["cost"] = 0.0
    for problem in problems:
        for cp, s in arm.get(problem, {}).items():
            if keys is not None and (problem, cp) not in keys:
                continue
            tot["checkpoints"] += 1
            for k in ("core", "iso", "strict"):
                tot[k] += int(s[k])
            tot["passed"] += s["passed"]
            tot["total"] += s["total"]
            tot["cost"] += s["cost"]
    tot["pct"] = tot["passed"] / tot["total"] if tot["total"] else 0.0
    tot["cost"] = round(tot["cost"], 4)
    return tot


def keys_of(arm: dict[str, dict[str, dict]]) -> set[tuple[str, str]]:
    return {(p, cp) for p, cps in arm.items() for cp in cps}


def warnings_for(data: dict, present: list[str], smoke: dict[str, set]) -> list[str]:
    out = []
    for a in present:
        lacking = [g for g, probs in GROUPS.items() if g != "all" and not any(data[a].get(p) for p in probs)]
        if lacking:
            out.append(f"{a} has no {' or '.join(lacking)} checkpoints: the common view has none there "
                       "(use --arms, or the pairs view)")
    smoke_arms = {a: sorted(v for v in smoke[a] if v is not None) for a in present if any(v is not None for v in smoke[a])}
    if len({tuple(v) for v in smoke_arms.values()}) > 1:
        out.append("smoke_checkpoints differ between arms (" + ", ".join(f"{a}={'/'.join(map(str, v))}"
                   for a, v in smoke_arms.items()) + "): run with SMOKE_CHECKPOINTS=8 to match")
    return out


def compare(arm_dirs: dict[str, list[str]], results: Path = RESULTS, focus: str | None = None) -> dict:
    data = {name: load_arm(results, dirs) for name, dirs in arm_dirs.items()}
    smoke = {name: smoke_checkpoints(results, dirs) for name, dirs in arm_dirs.items()}
    present = [a for a in data if data[a]]
    missing = [a for a in data if not data[a]]
    common: set[tuple[str, str]] | None = None
    for a in present:
        keys = keys_of(data[a])
        common = keys if common is None else common & keys
    common = common or set()
    pairs = {}
    if focus in present:
        for other in present:
            if other == focus:
                continue
            shared = keys_of(data[focus]) & keys_of(data[other])
            pairs[other] = {g: {focus: aggregate(data[focus], shared, probs), other: aggregate(data[other], shared, probs)}
                            for g, probs in GROUPS.items()}

    views = {}
    for view, keys in (("common", common), ("available", None)):
        views[view] = {
            g: {a: aggregate(data[a], keys, probs) for a in present} for g, probs in GROUPS.items()
        }
    per_problem = {
        p: {
            a: {
                "checkpoints": len(data[a].get(p, {})),
                "passed": sum(s["passed"] for s in data[a].get(p, {}).values()),
                "total": sum(s["total"] for s in data[a].get(p, {}).values()),
                "common_passed": sum(
                    s["passed"] for cp, s in data[a].get(p, {}).items() if (p, cp) in common
                ),
                "common_total": sum(
                    s["total"] for cp, s in data[a].get(p, {}).items() if (p, cp) in common
                ),
            }
            for a in present
        }
        for p in PROBLEMS
    }
    return {
        "arms": arm_dirs,
        "compared": present,
        "no_data": missing,
        "common_checkpoints": sorted(common, key=lambda k: (k[0], _checkpoint_key(k[1]))),
        "views": views,
        "focus": focus if pairs else None,
        "pairs": pairs,
        "per_problem": per_problem,
        "warnings": warnings_for(data, present, smoke),
    }


def format_report(report: dict) -> str:
    out: list[str] = []
    arms = report["compared"]
    if report["no_data"]:
        out.append(f"no data (left out): {', '.join(report['no_data'])}")
    out += [f"warning: {w}" for w in report.get("warnings", [])]
    n_common = len(report["common_checkpoints"])
    probs = sorted({p for p, _ in report["common_checkpoints"]})
    out.append(f"common checkpoints: {n_common} across {len(probs)} problems ({', '.join(probs) or '-'})")
    for view in ("common", "available"):
        out.append("")
        out.append(f"== {view} checkpoints")
        out.append(
            f"{'group':9} {'arm':6} {'cps':>4} {'core':>5} {'iso':>4} {'strict':>6} "
            f"{'tests':>11} {'pct':>6} {'cost':>8}"
        )
        for g in GROUPS:
            for a in arms:
                t = report["views"][view][g][a]
                out.append(
                    f"{g:9} {a:6} {t['checkpoints']:4} {t['core']:5} {t['iso']:4} {t['strict']:6} "
                    f"{t['passed']:>5}/{t['total']:<5} {t['pct']:6.1%} ${t['cost']:7.2f}"
                )
    focus = report.get("focus")
    if focus:
        out.append("")
        out.append(f"== pairs: {focus} against each arm, on the checkpoints both have")
        out.append(f"{'group':9} {'vs':6} {'cps':>4} {focus + ' core':>9} {'core':>5} {focus + ' pct':>8} {'pct':>6} "
                   f"{focus + ' cost':>9} {'cost':>8}")
        for other, groups in report["pairs"].items():
            for g in GROUPS:
                f, o = groups[g][focus], groups[g][other]
                out.append(f"{g:9} {other:6} {f['checkpoints']:4} {f['core']:9} {o['core']:5} {f['pct']:8.1%} "
                           f"{o['pct']:6.1%} ${f['cost']:8.2f} ${o['cost']:7.2f}")
    out.append("")
    out.append("== tests passed per problem (available checkpoints; [cps])")
    out.append(f"{'problem':28}" + "".join(f"{a:>17}" for a in arms))
    for p, row in report["per_problem"].items():
        cells = []
        for a in arms:
            r = row[a]
            cells.append(f"{r['passed']}/{r['total']} [{r['checkpoints']}]" if r["checkpoints"] else "-")
        out.append(f"{p:28}" + "".join(f"{c:>17}" for c in cells))
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--v7", nargs="+", metavar="DIR", default=None, help="v7 run dir(s)")
    parser.add_argument("--v8", nargs="+", metavar="DIR", default=None,
                        help="v8 run dir(s); v8 becomes the arm the pairs view scores against the others")
    parser.add_argument("--json", type=Path, default=None, metavar="OUT", help="also write the report as JSON")
    parser.add_argument("--results", type=Path, default=RESULTS, help="directory holding the run dirs")
    parser.add_argument("--arms", default=None, metavar="A,B,...",
                        help=f"compare only these arms (of {', '.join(ARM_DIRS)}, v7); the common set is theirs")
    args = parser.parse_args(argv)

    arm_dirs = {k: list(v) for k, v in ARM_DIRS.items()}
    if args.v7:
        arm_dirs["v7"] = args.v7
    if args.v8:
        arm_dirs["v8"] = args.v8
    if args.arms:
        wanted = [a.strip() for a in args.arms.split(",") if a.strip()]
        unknown = [a for a in wanted if a not in arm_dirs]
        if unknown:
            parser.error(f"--arms: unknown arm(s) {', '.join(unknown)} (known: {', '.join(arm_dirs)})")
        arm_dirs = {a: arm_dirs[a] for a in wanted}
    report = compare(arm_dirs, args.results, focus="v8" if args.v8 else "v7" if args.v7 else None)
    print(format_report(report))
    if args.json:
        args.json.write_text(json.dumps(report, indent=2) + "\n")
    if args.v7 and "v7" in report["no_data"]:
        print(f"cmp_versions: --v7 {' '.join(args.v7)}: no checkpoint with evaluation.json found", file=sys.stderr)
        return 1
    return 0 if report["compared"] else 1


if __name__ == "__main__":
    sys.exit(main())
