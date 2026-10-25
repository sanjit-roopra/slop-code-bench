"""Review-lens findings per lens (no-issue runs, severity, blind lenses) from a pidt run's file_merger traces.

Usage: python3 harness_eval/scripts/pidt_lens_findings.py <run dir> [checkpoint ...]  (default: checkpoints 1-4)
"""
import json, sys, re, collections, subprocess
run = sys.argv[1]
CKS = [int(c) for c in sys.argv[2:]] or [1, 2, 3, 4]
st = collections.defaultdict(lambda: collections.Counter())
seq = []
for ck in CKS:
    f = f"{run}/file_merger/checkpoint_{ck}/agent/stdout.jsonl"
    p = subprocess.run(["grep", '"toolName":"dev_team_subagent"', f], capture_output=True, text=True).stdout
    for l in p.splitlines():
        o = json.loads(l)
        if o.get("type") != "message_end": continue
        m = o["message"]
        text = "".join(c.get("text", "") for c in m.get("content", []) if isinstance(c, dict))
        ags = [r["agent"] for r in (m.get("details") or {}).get("results", [])]
        seq.append((ck, ags))
        for sec in re.split(r"\n### ", text):
            name = sec.split(" ", 1)[0].strip()
            js = re.search(r"\{.*\}", sec, re.S)
            if not js or not name.endswith(("review", "critic")): continue
            try: d = json.loads(js.group(0))
            except Exception:
                st[name]["unparsed"] += 1; continue
            s = st[name]; s["runs"] += 1
            iss = d.get("issues") or d.get("findings") or []
            s["no-issue runs"] += not iss
            for i in iss:
                sev = i.get("severity", "?"); conf = i.get("confidence", "?")
                s[f"sev:{sev}"] += 1
                if conf in ("none", "low"): s["conf none/low"] += 1
                if re.search(r"diff was unavailable|no command-execution|cannot run|could not (run|execute)|not verified", i.get("message", ""), re.I): s["blind (no shell)"] += 1
for k, v in sorted(st.items()):
    print(f"{k:30}", dict(v))
print()
for ck, ags in seq: print(ck, ags if len(ags) < 4 else f"{len(ags)} lenses")
