#!/usr/bin/env python3
"""Build a one-problem SCB catalog truncated to the first N checkpoints.

slop-code has no checkpoint-limit flag, but it reads problems from
``$SCBENCH_PROBLEMS_PATH`` when set (a flat directory of problem dirs, each
with ``config.yaml``). This copies one problem from the installed catalog,
keeps only its first N checkpoints (spec, tests, test data, reference
solutions), and prints the new catalog root for run.sh to export.
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

import yaml

_CKPT_RE = re.compile(r"checkpoint_(\d+)")


def _ckpt_index(name: str) -> int | None:
    m = _CKPT_RE.search(name)
    return int(m.group(1)) if m else None


def truncate_problem(src: Path, dest_root: Path, keep: int) -> Path:
    if not (src / "config.yaml").is_file():
        raise SystemExit(f"make_smoke_problem: no config.yaml in {src}")
    dest = dest_root / src.name
    if dest.exists():
        shutil.rmtree(dest)
    dest_root.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dest)

    config_path = dest / "config.yaml"
    config = yaml.safe_load(config_path.read_text())
    checkpoints = config.get("checkpoints") or {}
    ordered = sorted(
        checkpoints.items(), key=lambda kv: (kv[1] or {}).get("order", 0)
    )
    kept = dict(ordered[:keep])
    if len(kept) < 1:
        raise SystemExit("make_smoke_problem: problem has no checkpoints")
    config["checkpoints"] = kept
    header = "".join(
        line for line in config_path.read_text().splitlines(keepends=True)
        if line.startswith("#")
    )
    config_path.write_text(
        header
        + f"# Truncated to {len(kept)} checkpoint(s) by eval_coding make_smoke_problem.py\n"
        + yaml.safe_dump(config, sort_keys=False)
    )

    kept_idx = {_ckpt_index(name) for name in kept}
    for path in sorted(dest.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        idx = _ckpt_index(path.name)
        if idx is not None and idx not in kept_idx and path.exists():
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
    return dest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--src-root", required=True, type=Path)
    parser.add_argument("--dest-root", required=True, type=Path)
    parser.add_argument("--problem", required=True)
    parser.add_argument("--checkpoints", type=int, default=2)
    args = parser.parse_args(argv)

    src = args.src_root / args.problem
    if not src.is_dir():
        print(
            f"make_smoke_problem: {src} not found; install the catalog first "
            "(uv run --project .. slop-code sync)",
            file=sys.stderr,
        )
        return 2
    truncate_problem(src, args.dest_root, args.checkpoints)
    print(args.dest_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
