#!/usr/bin/env python3
"""Write or finalize a run manifest (``manifest.json``) for one eval run.

``write`` records everything needed to reproduce and compare a run before it
starts: arm, subset, repeat, SCB fork SHA, pi-dev-team SHA, Claude Code and pi
versions, model ids, prompt, host, timestamp and the exact slop-code command.
``finish`` adds the end time and exit status after the run, and records the
docker images again (they may only exist once the run has built them).
``expect-model`` prints the provider/model id the agent should report at run
time, which ``check_clean.py --expect-model`` asserts.

Credentials are never read or recorded here.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import platform
import re
import socket
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCB = ROOT.parent  # this fork; the eval lives in its harness_eval/
MANIFEST_NAME = "manifest.json"

_SHA_PATTERNS = (
    re.compile(r"PDT_SHA\s*=\s*[\"']([0-9a-f]{7,40})[\"']"),
    re.compile(r"pdt([0-9a-f]{7,40})\b"),
    re.compile(r"pi-dev-team[^\n]*?checkout\s+(?:-q\s+)?([0-9a-f]{7,40})\b"),
    re.compile(r"PDT_SHA[=:\s\"']+([0-9a-f]{7,40})\b"),
)
_PI_VERSION_RES = (
    re.compile(r"PI_VERSION\s*=\s*[\"'](\d+\.\d+\.\d+)[\"']"),
    re.compile(r"pi-coding-agent@(\d+\.\d+\.\d+)"),
)


def _now() -> str:
    return dt.datetime.now(dt.UTC).isoformat(timespec="seconds")


def _git(repo: Path, *args: str) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), *args],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip()


def git_info(repo: Path) -> dict[str, object]:
    # Untracked files count: an uncommitted new agent config or prompt is a
    # change to what ran. Ignored files (results/, outputs/) do not.
    status = _git(repo, "status", "--porcelain", "--untracked-files=all")
    changes = status.splitlines() if status else []
    return {
        "path": str(repo.relative_to(ROOT)) if repo.is_relative_to(ROOT) and repo != ROOT else "." if repo == ROOT else str(repo),
        "sha": _git(repo, "rev-parse", "HEAD"),
        "branch": _git(repo, "rev-parse", "--abbrev-ref", "HEAD"),
        "remote": _git(repo, "config", "--get", "remote.origin.url"),
        "dirty": bool(changes) if status is not None else None,
        "changes": changes[:50],
    }


def _sha256_file(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def file_info(ref: str | None, builtin_dir: Path | None = None) -> dict[str, object] | None:
    """Path + sha256 of a config file; bare names resolve under ``builtin_dir``."""
    if not ref:
        return None
    path = Path(ref)
    bare = "/" not in ref and path.suffix not in (".yaml", ".yml")
    if builtin_dir is not None and bare:
        path = builtin_dir / f"{ref}.yaml"
    elif not path.is_absolute():
        path = ROOT / path
    info: dict[str, object] = {"ref": ref, "path": str(path), "sha256": _sha256_file(path)}
    if info["sha256"] is None:
        info["missing"] = True
    return info


def _load_yaml(path: Path) -> dict:
    try:
        import yaml
    except ImportError:  # pragma: no cover - SCB env always has pyyaml
        return {}
    try:
        data = yaml.safe_load(path.read_text())
    except (OSError, yaml.YAMLError):
        return {}
    return data if isinstance(data, dict) else {}


def agent_info(agent_yaml: Path) -> dict[str, object]:
    info: dict[str, object] = {"config": str(agent_yaml), "exists": agent_yaml.exists()}
    if not agent_yaml.exists():
        return info
    text = agent_yaml.read_text()
    data = _load_yaml(agent_yaml)
    template = data.get("docker_template")
    if isinstance(template, str):
        tpath = Path(template) if Path(template).is_absolute() else ROOT / template
        info["docker_template"] = template
        if tpath.exists():
            ttext = tpath.read_text()
            info["docker_template_sha256"] = hashlib.sha256(ttext.encode()).hexdigest()
            # Template first: it carries the full SHA and the pinned pi version.
            text = ttext + "\n" + text
    info.update(
        {
            "type": data.get("type"),
            "version": data.get("version"),
            "timeout": data.get("timeout"),
            "extra_args": data.get("extra_args"),
            "provider": data.get("provider"),
            "thinking": data.get("thinking"),
            "sha256": hashlib.sha256(text.encode()).hexdigest(),
        }
    )
    for pat in _SHA_PATTERNS:
        m = pat.search(text)
        if m:
            info["pi_dev_team_sha"] = m.group(1)
            break
    for pat in _PI_VERSION_RES:
        m = pat.search(text)
        if m:
            info["pi_version"] = m.group(1)
            break
    if "pi_version" not in info and data.get("type") == "pi" and isinstance(
        data.get("version"), str
    ):
        info["pi_version"] = data["version"].split("-")[0]
    if data.get("type") == "claude_code":
        info["claude_code_version"] = data.get("version")
    return info


_DOCKER_PREFIX = "slop-code"  # slop_code.execution.docker_runtime.IMAGE_NAME_PREFIX


def _docker_inspect(ref: str) -> dict[str, object]:
    info: dict[str, object] = {"ref": ref, "id": None, "repo_digests": None}
    try:
        out = subprocess.run(
            ["docker", "image", "inspect", "--format", "{{json .Id}} {{json .RepoDigests}}", ref],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        return info
    if out.returncode != 0 or not out.stdout.strip():
        return info
    image_id, _, digests = out.stdout.strip().partition(" ")
    try:
        info["id"] = json.loads(image_id)
        info["repo_digests"] = json.loads(digests) if digests else []
    except json.JSONDecodeError:
        pass
    return info


def image_info(agent: dict[str, object], environment: dict[str, object] | None) -> dict[str, object]:
    """Image tags SCB builds/uses for this run (mirrors AgentConfigBase.get_image
    and DockerEnvironmentSpec.get_base_image), each with its local image id and
    registry digests when present. The upstream base is a floating tag, so its
    digest is what pins it."""
    env = _load_yaml(Path(str(environment["path"]))) if environment and environment.get("path") else {}
    env_name = env.get("name")
    upstream = (env.get("docker") or {}).get("image") if isinstance(env.get("docker"), dict) else None
    out: dict[str, object] = {}
    if env_name and agent.get("type"):
        version = agent.get("version")
        tag = f"{agent['type']}-{version}-{env_name}" if version else f"{agent['type']}-{env_name}"
        out["agent"] = _docker_inspect(f"{_DOCKER_PREFIX}:{tag}")
    if env_name:
        out["base"] = _docker_inspect(f"{_DOCKER_PREFIX}:{env_name}")
    if upstream:
        out["upstream"] = _docker_inspect(str(upstream))
    return out


def expected_runtime_model(model: str, agent_type: str | None) -> str | None:
    """The model id the agent should report at run time for ``provider/name``.

    claude_code: the slug in the CC ``system``/``init`` event (``model``).
    pi: ``<pi provider>/<slug>`` as on pi assistant messages. Mirrors
    PiAgent._resolve_model_slug for the providers used here (SCB provider name
    == pi provider name, e.g. github-copilot).
    """
    info = model_info(model)
    if not info.get("internal_name"):
        return None
    provider = str(info["provider"])
    slugs = info.get("provider_slugs") or {}
    if agent_type == "claude_code":
        return str(info["internal_name"])
    if agent_type == "pi":
        pi_provider = provider
        try:  # SCB env: use its provider mapping when importable
            from slop_code.agent_runner.agents.pi.agent import _SCB_TO_PI_PROVIDER

            pi_provider = _SCB_TO_PI_PROVIDER.get(provider, provider)
        except Exception:  # noqa: BLE001 - fall back to identity
            pass
        slug = slugs.get(pi_provider) or slugs.get(provider) or info["internal_name"]
        return f"{pi_provider}/{slug}"
    return None


def model_info(model: str) -> dict[str, object]:
    provider, _, name = model.partition("/")
    info: dict[str, object] = {"cli": model, "provider": provider, "name": name}
    models_dir = SCB / "configs" / "models"
    candidates = [models_dir / f"{name}.yaml"]
    candidates += sorted(models_dir.glob("*.yaml")) if models_dir.exists() else []
    for path in candidates:
        if not path.exists():
            continue
        data = _load_yaml(path)
        if path.stem == name or name in (data.get("aliases") or []):
            info["catalog_file"] = os.path.relpath(path, ROOT)
            info["internal_name"] = data.get("internal_name")
            info["provider_slugs"] = data.get("provider_slugs") or {}
            info["pricing"] = data.get("pricing")
            break
    else:
        info["catalog_file"] = None
    return info


def prompt_info(prompt: str) -> dict[str, object]:
    path = Path(prompt)
    if not path.suffix:
        path = SCB / "configs" / "prompts" / f"{prompt}.jinja"
    info: dict[str, object] = {"ref": prompt, "name": path.stem, "path": str(path)}
    if path.exists():
        info["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    else:
        info["missing"] = True
    return info


def host_info() -> dict[str, object]:
    docker = None
    try:
        out = subprocess.run(
            ["docker", "version", "--format", "{{.Server.Version}}"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        docker = out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        pass
    return {
        "hostname": socket.gethostname(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "docker_server": docker,
    }


def build_manifest(args: argparse.Namespace) -> dict[str, object]:
    agent = agent_info(Path(args.agent))
    environment = file_info(args.environment, SCB / "configs" / "environments")
    model = model_info(args.model)
    model["expected_runtime"] = expected_runtime_model(args.model, agent.get("type"))  # type: ignore[arg-type]
    return {
        "schema": 1,
        "arm": args.arm,
        "subset": args.subset,
        "repeat": args.repeat,
        "run_dir": str(args.run_dir),
        "started_at": _now(),
        "status": "dry-run" if args.dry_run else "running",
        "eval_coding": git_info(ROOT),
        "scb": git_info(SCB),
        "agent": agent,
        "versions": {
            "claude_code": agent.get("claude_code_version"),
            "pi": agent.get("pi_version"),
            "pi_dev_team_sha": agent.get("pi_dev_team_sha"),
        },
        "model": model,
        "thinking": args.thinking,
        "timeout_s": agent.get("timeout"),
        "prompt": prompt_info(args.prompt),
        "environment": environment,
        "images": image_info(agent, environment),
        "problems": args.problem or None,
        "run_config": file_info(args.run_config),
        "smoke_checkpoints": args.smoke_checkpoints,
        "host": host_info(),
        "command": args.command,
        "clean": {"status": "PENDING"},
        "cost_note": "Costs under subscriptions (Claude OAuth, Copilot) are notional list price.",
    }


def cmd_write(args: argparse.Namespace) -> int:
    manifest = build_manifest(args)
    if args.stdout:
        json.dump(manifest, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0
    run_dir = Path(args.run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"manifest: {run_dir / MANIFEST_NAME}")
    return 0


def cmd_finish(args: argparse.Namespace) -> int:
    path = Path(args.run_dir) / MANIFEST_NAME
    manifest = json.loads(path.read_text())
    manifest["finished_at"] = _now()
    manifest["exit_code"] = args.exit_code
    manifest["status"] = "finished" if args.exit_code == 0 else "failed"
    env = manifest.get("environment")
    if isinstance(env, dict) and isinstance(manifest.get("agent"), dict):
        manifest["images"] = image_info(manifest["agent"], env)
    path.write_text(json.dumps(manifest, indent=2) + "\n")
    return 0


def cmd_expect_model(args: argparse.Namespace) -> int:
    expected = expected_runtime_model(args.model, agent_info(Path(args.agent)).get("type"))  # type: ignore[arg-type]
    if not expected:
        print(f"manifest: cannot resolve runtime model for {args.model}", file=sys.stderr)
        return 1
    print(expected)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)

    w = sub.add_parser("write")
    w.add_argument("--run-dir", required=True)
    w.add_argument("--arm", required=True)
    w.add_argument("--subset", required=True)
    w.add_argument("--repeat", type=int, required=True)
    w.add_argument("--agent", required=True, help="agent YAML path")
    w.add_argument("--model", required=True, help="provider/name")
    w.add_argument("--prompt", required=True, help="prompt path or SCB bare name")
    w.add_argument("--environment", required=True)
    w.add_argument("--thinking", default="high")
    w.add_argument("--run-config", default=None)
    w.add_argument("--problem", action="append", default=[])
    w.add_argument("--smoke-checkpoints", type=int, default=None)
    w.add_argument("--command", default="")
    w.add_argument("--dry-run", action="store_true")
    w.add_argument("--stdout", action="store_true", help="print instead of writing")
    w.set_defaults(func=cmd_write)

    f = sub.add_parser("finish")
    f.add_argument("--run-dir", required=True)
    f.add_argument("--exit-code", type=int, required=True)
    f.set_defaults(func=cmd_finish)

    e = sub.add_parser("expect-model")
    e.add_argument("--model", required=True, help="provider/name")
    e.add_argument("--agent", required=True, help="agent YAML path")
    e.set_defaults(func=cmd_expect_model)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
