#!/usr/bin/env python3
"""Check that a SlopCodeBench run used a clean harness.

"Clean" means the agent ran with only what the arm is supposed to carry:

* Claude Code arms (cc-clean): every ``system``/``init`` event in the
  stream-json trace lists only builtin plugins, no MCP servers, and only the
  skills/agents that stock Claude Code 2.1.304 ships with. Anything namespaced
  (``plugin:skill``) or outside the stock allowlist means user or project
  configuration leaked into the container.
* pi arms: the pi system message lists only pi's builtin tools plus, for
  pi-dev-team arms, the pi-dev-team tools; only known system-prompt sections
  appear; skills load only from the pi-dev-team checkout; custom messages and
  session entries come only from pi-dev-team.

SCB stores each checkpoint's agent stdout at
``<run>/<problem>/<checkpoint>/agent/stdout.jsonl`` (or inside
``agent.tar.gz`` when artifacts are compressed). This script scans all of
them, writes ``clean_check.json`` into the run directory and, if a
``manifest.json`` exists there, records the verdict under ``clean``.

Exit status: 0 CLEAN, 1 DIRTY, 2 UNKNOWN (no traces or no init info).
"""

from __future__ import annotations

import argparse
import io
import json
import re
import sys
import tarfile
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

# --- Allowlists -------------------------------------------------------------

# Stock Claude Code 2.1.304 (observed in a clean container: empty HOME, no
# project .claude/, no MCP config). Update when the pinned CC version changes.
CC_STOCK_SKILLS = frozenset(
    {
        "deep-research",
        "design",
        "design-sync",
        "dataviz",
        "update-config",
        "verify",
        "debug",
        "code-review",
        "simplify",
        "batch",
        "fewer-permission-prompts",
        "doctor",
        "loop",
        "schedule",
        "claude-api",
        "workflow-authoring",
        "run",
        "run-skill-generator",
        "plugin-authoring",
    }
)
CC_STOCK_AGENTS = frozenset(
    {"claude", "Explore", "general-purpose", "Plan", "statusline-setup"}
)
CC_BUILTIN_PLUGIN_PATH = "builtin"

# pi 1.3.0 builtin tools (read/bash/edit/write are on by default; grep/find/ls
# are opt-in builtins).
PI_BUILTIN_TOOLS = frozenset({"read", "bash", "edit", "write", "grep", "find", "ls"})
PI_CORE_SECTIONS = frozenset({"preamble", "tools", "rules", "docs", "cwd"})

# pi-dev-team (sanjit-roopra/pi-dev-team @ 3c34bf5).
PDT_TOOLS = frozenset(
    {"dev_team_subagent", "ask_user", "web_fetch", "dev_team_brief", "skill"}
)
PDT_SECTIONS = frozenset({"dev_team"})
PDT_ROOT = "/opt/pi-dev-team"
PDT_CUSTOM_PREFIX = "dev-team"

ARMS = {
    "cc-clean": {"kind": "cc"},
    "pidt": {"kind": "pi", "dev_team": True},
    "pidt-opus": {"kind": "pi", "dev_team": True},
    "pi-stock": {"kind": "pi", "dev_team": False},
}

TRACE_NAME = "stdout.jsonl"
TAR_NAME = "agent.tar.gz"
REPORT_NAME = "clean_check.json"
MANIFEST_NAME = "manifest.json"

_SKILL_LOCATION_RE = re.compile(r'<skill\b[^>]*\blocation="([^"]+)"')


# --- Result types -----------------------------------------------------------


@dataclass
class TraceResult:
    path: str
    status: str = "UNKNOWN"  # CLEAN | DIRTY | UNKNOWN
    reasons: list[str] = field(default_factory=list)
    info: dict[str, object] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "status": self.status,
            "reasons": self.reasons,
            "info": self.info,
        }


def _finish(result: TraceResult, saw_init: bool) -> TraceResult:
    if result.reasons:
        result.status = "DIRTY"
    elif saw_init:
        result.status = "CLEAN"
    else:
        result.status = "UNKNOWN"
        result.reasons.append("no init/system information found in trace")
    return result


# --- Event parsing ----------------------------------------------------------


def iter_events(lines: Iterable[str]) -> Iterator[dict]:
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            yield event


# --- Claude Code ------------------------------------------------------------


def check_cc_events(
    events: Iterable[dict],
    path: str = "<events>",
    expect_version: str | None = None,
) -> TraceResult:
    result = TraceResult(path=path)
    inits = 0
    versions: set[str] = set()
    for event in events:
        if event.get("type") != "system" or event.get("subtype") != "init":
            continue
        inits += 1
        reasons = result.reasons

        version = event.get("claude_code_version")
        if isinstance(version, str):
            versions.add(version)
            if expect_version and version != expect_version:
                reasons.append(
                    f"claude_code_version {version!r} != expected {expect_version!r}"
                )

        for plugin in event.get("plugins") or []:
            if not isinstance(plugin, dict):
                reasons.append(f"malformed plugin entry {plugin!r}")
                continue
            if plugin.get("path") != CC_BUILTIN_PLUGIN_PATH:
                reasons.append(
                    f"non-builtin plugin {plugin.get('name')!r} "
                    f"(path={plugin.get('path')!r})"
                )

        mcp = event.get("mcp_servers") or []
        if mcp:
            names = [m.get("name") if isinstance(m, dict) else m for m in mcp]
            reasons.append(f"MCP servers present: {names}")

        for skill in event.get("skills") or []:
            if ":" in str(skill):
                reasons.append(f"namespaced (plugin) skill {skill!r}")
            elif skill not in CC_STOCK_SKILLS:
                reasons.append(f"skill {skill!r} not in stock allowlist")

        for agent in event.get("agents") or []:
            if ":" in str(agent):
                reasons.append(f"namespaced (plugin) agent {agent!r}")
            elif agent not in CC_STOCK_AGENTS:
                reasons.append(f"agent {agent!r} not in stock allowlist")

        for cmd in event.get("slash_commands") or []:
            if ":" in str(cmd):
                reasons.append(f"namespaced (plugin) slash command {cmd!r}")

    result.info = {"init_events": inits, "claude_code_versions": sorted(versions)}
    result.reasons = sorted(set(result.reasons))
    return _finish(result, saw_init=inits > 0)


# --- pi ---------------------------------------------------------------------


def _text_of(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "\n".join(parts)
    return ""


def _messages_of(event: dict) -> Iterator[dict]:
    msg = event.get("message")
    if isinstance(msg, dict):
        yield msg
    for msg in event.get("messages") or []:
        if isinstance(msg, dict):
            yield msg


def check_pi_events(
    events: Iterable[dict],
    path: str = "<events>",
    dev_team: bool = True,
) -> TraceResult:
    result = TraceResult(path=path)
    allowed_tools = PI_BUILTIN_TOOLS | (PDT_TOOLS if dev_team else frozenset())
    allowed_sections = PI_CORE_SECTIONS | (PDT_SECTIONS if dev_team else frozenset())
    allowed_skill_roots = (PDT_ROOT.rstrip("/") + "/",) if dev_team else ()

    system_msgs = 0
    tools_seen: set[str] = set()
    sections_seen: set[str] = set()
    models: set[str] = set()
    subagent_models: set[str] = set()
    reasons = result.reasons

    def check_custom(custom_type: object, where: str) -> None:
        ct = str(custom_type)
        if not (dev_team and ct.startswith(PDT_CUSTOM_PREFIX)):
            reasons.append(f"{where} customType {ct!r} not from pi-dev-team")

    def check_skills(content: object) -> None:
        for loc in _SKILL_LOCATION_RE.findall(_text_of(content)):
            if not (allowed_skill_roots and loc.startswith(allowed_skill_roots)):
                reasons.append(f"skill loaded from {loc!r}")

    for event in events:
        etype = event.get("type")

        if etype == "entry_appended":
            entry = event.get("entry") or {}
            if isinstance(entry, dict) and entry.get("type") == "custom":
                check_custom(entry.get("customType"), "session entry")
                data = entry.get("data") or {}
                if isinstance(data, dict) and data.get("model"):
                    subagent_models.add(str(data["model"]))
            continue

        if etype not in ("message_start", "message_end", "agent_end", "turn_end"):
            continue

        for msg in _messages_of(event):
            role = msg.get("role")
            if role == "system":
                system_msgs += 1
                for tool in msg.get("toolsAdded") or []:
                    name = tool.get("name") if isinstance(tool, dict) else tool
                    tools_seen.add(str(name))
                    if name not in allowed_tools:
                        reasons.append(f"tool {name!r} from an unexpected extension")
                for sec in (msg.get("sections") or {}).keys():
                    sections_seen.add(sec)
                    if sec not in allowed_sections:
                        reasons.append(f"unexpected system-prompt section {sec!r}")
                check_skills(msg.get("content"))
            elif role == "user":
                check_skills(msg.get("content"))
            elif role == "custom":
                check_custom(msg.get("customType"), "custom message")
            elif role == "assistant":
                if msg.get("provider") and msg.get("model"):
                    models.add(f"{msg['provider']}/{msg['model']}")
                content = msg.get("content")
                if isinstance(content, list):
                    for item in content:
                        if isinstance(item, dict) and item.get("type") == "toolCall":
                            name = item.get("name")
                            if name and name not in allowed_tools:
                                reasons.append(f"call to unexpected tool {name!r}")

    result.info = {
        "system_messages": system_msgs,
        "tools": sorted(tools_seen),
        "sections": sorted(sections_seen),
        "models": sorted(models),
        "subagent_models": sorted(subagent_models),
        "dev_team_loaded": bool(sections_seen & PDT_SECTIONS)
        or bool(tools_seen & PDT_TOOLS),
    }
    if dev_team and system_msgs and not result.info["dev_team_loaded"]:
        reasons.append("pi-dev-team expected but not loaded")
    result.reasons = sorted(set(reasons))
    return _finish(result, saw_init=system_msgs > 0)


# --- Run-level --------------------------------------------------------------


def find_traces(run_dir: Path) -> Iterator[tuple[str, list[str]]]:
    """Yield (display path, lines) for every agent stdout trace in a run."""
    for p in sorted(run_dir.rglob(TRACE_NAME)):
        if p.parent.name != "agent":
            continue
        yield str(p.relative_to(run_dir)), p.read_text(errors="replace").splitlines()
    for p in sorted(run_dir.rglob(TAR_NAME)):
        try:
            with tarfile.open(p, "r:gz") as tar:
                for member in tar.getmembers():
                    if member.isfile() and Path(member.name).name == TRACE_NAME:
                        fh = tar.extractfile(member)
                        if fh is None:
                            continue
                        text = io.TextIOWrapper(fh, errors="replace").read()
                        yield f"{p.relative_to(run_dir)}!{member.name}", text.splitlines()
        except (tarfile.TarError, OSError) as exc:
            yield f"{p.relative_to(run_dir)}!<unreadable: {exc}>", []


def check_trace(
    lines: Iterable[str], arm: str, path: str, expect_cc_version: str | None
) -> TraceResult:
    spec = ARMS[arm]
    events = iter_events(lines)
    if spec["kind"] == "cc":
        return check_cc_events(events, path, expect_version=expect_cc_version)
    return check_pi_events(events, path, dev_team=bool(spec.get("dev_team")))


def check_run(
    run_dir: Path, arm: str, expect_cc_version: str | None = None
) -> dict[str, object]:
    traces = [
        check_trace(lines, arm, path, expect_cc_version)
        for path, lines in find_traces(run_dir)
    ]
    if not traces:
        status = "UNKNOWN"
    elif any(t.status == "DIRTY" for t in traces):
        status = "DIRTY"
    elif all(t.status == "CLEAN" for t in traces):
        status = "CLEAN"
    else:
        status = "UNKNOWN"
    return {
        "status": status,
        "arm": arm,
        "traces_checked": len(traces),
        "dirty": sum(t.status == "DIRTY" for t in traces),
        "unknown": sum(t.status == "UNKNOWN" for t in traces),
        "traces": [t.as_dict() for t in traces],
    }


def update_manifest(run_dir: Path, report: dict[str, object]) -> None:
    manifest_path = run_dir / MANIFEST_NAME
    if not manifest_path.exists():
        return
    manifest = json.loads(manifest_path.read_text())
    manifest["clean"] = {
        "status": report["status"],
        "traces_checked": report["traces_checked"],
        "dirty": report["dirty"],
        "unknown": report["unknown"],
        "report": REPORT_NAME,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--arm", required=True, choices=sorted(ARMS))
    parser.add_argument("--expect-cc-version", default=None)
    parser.add_argument(
        "--no-write", action="store_true", help="print only; do not write files"
    )
    args = parser.parse_args(argv)

    if not args.run_dir.is_dir():
        print(f"check_clean: not a directory: {args.run_dir}", file=sys.stderr)
        return 2

    report = check_run(args.run_dir, args.arm, args.expect_cc_version)
    if not args.no_write:
        (args.run_dir / REPORT_NAME).write_text(json.dumps(report, indent=2) + "\n")
        update_manifest(args.run_dir, report)

    print(
        f"check_clean: {report['status']} "
        f"({report['traces_checked']} traces, {report['dirty']} dirty, "
        f"{report['unknown']} unknown)"
    )
    for t in report["traces"]:
        for reason in t["reasons"]:
            print(f"  {t['status']} {t['path']}: {reason}")
    return {"CLEAN": 0, "DIRTY": 1}.get(str(report["status"]), 2)


if __name__ == "__main__":
    raise SystemExit(main())
