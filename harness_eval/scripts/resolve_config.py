#!/usr/bin/env python3
"""Resolve a slop-code run configuration without running anything.

Takes the same arguments run.sh passes to ``slop-code run`` and goes through
SCB's own loaders: run config + overrides, environment spec, model catalog
lookup, agent config validation and the docker image name. No credentials are
resolved, no container is built, no model is called. Run it inside the SCB
project env (``uv run --project .. python ...``).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--config", "-c", type=Path)
    p.add_argument("--agent", "-a")
    p.add_argument("--environment", "-e")
    p.add_argument("--prompt", "-p")
    p.add_argument("--model", "-m")
    p.add_argument("--problem", action="append", default=[])
    p.add_argument("--num-workers", "-n")
    p.add_argument("--evaluate", "--no-evaluate", action="store_true")
    p.add_argument("--live-progress", "--no-live-progress", action="store_true")
    p.add_argument("overrides", nargs="*")
    args = p.parse_intermixed_args(argv)

    from slop_code.agent_runner.registry import build_agent_config
    from slop_code.common.llms import ModelCatalog
    from slop_code.entrypoints import utils
    from slop_code.entrypoints.config import load_run_config
    from slop_code.entrypoints.config import loader as config_loader

    flags: dict[str, object] = {}
    for key in ("agent", "environment", "prompt"):
        if getattr(args, key):
            flags[key] = getattr(args, key)
    if args.model:
        parsed = utils.parse_model_override(args.model)
        flags["model"] = {"provider": parsed.provider, "name": parsed.name}

    errors: list[str] = []
    run_cfg = load_run_config(
        config_path=args.config, cli_flags=flags, cli_overrides=args.overrides
    )
    env_spec = config_loader.resolve_environment(
        run_cfg.environment_config_path or run_cfg.environment
    )
    ModelCatalog.ensure_loaded()
    model_def = ModelCatalog.get(run_cfg.model.name)
    if model_def is None:
        errors.append(f"model {run_cfg.model.name!r} not found in SCB catalog")
    agent_cfg = build_agent_config(run_cfg.agent)

    image = None
    try:
        image = agent_cfg.get_image(env_spec.name)  # type: ignore[attr-defined]
    except Exception as exc:  # noqa: BLE001 - informational only
        image = f"<unresolved: {exc}>"

    problems = args.problem or list(run_cfg.problems) or ["<all in catalog>"]
    out = {
        "output_path": run_cfg.output_path,
        "agent_type": run_cfg.agent.get("type"),
        "agent_version": run_cfg.agent.get("version"),
        "agent_config": str(run_cfg.agent_config_path),
        "docker_template": run_cfg.agent.get("docker_template"),
        "image": image,
        "environment": f"{env_spec.type}:{env_spec.name}",
        "environment_config": str(run_cfg.environment_config_path),
        "model": f"{run_cfg.model.provider}/{run_cfg.model.name}",
        "model_internal_name": getattr(model_def, "internal_name", None),
        "thinking": run_cfg.thinking,
        "prompt_path": str(run_cfg.prompt_path),
        "problems": problems,
        "errors": errors,
    }
    print(json.dumps(out, indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (FileNotFoundError, ValueError) as exc:
        print(f"resolve_config: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
