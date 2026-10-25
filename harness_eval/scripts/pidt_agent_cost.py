"""Per-sub-agent cost, calls and agent-minutes from a pidt run's file_merger traces.

Usage: python3 harness_eval/scripts/pidt_agent_cost.py <run dir> [checkpoint ...]  (default: checkpoints 1-4)
"""
import json, sys, collections, subprocess
run = sys.argv[1]
CKS = [int(c) for c in sys.argv[2:]] or [1, 2, 3, 4]
agg = collections.defaultdict(lambda: [0, 0.0, 0.0])  # calls, cost, minutes
main = 0.0; wall = collections.Counter()
for ck in CKS:
    f = f"{run}/file_merger/checkpoint_{ck}/agent/stdout.jsonl"
    p = subprocess.run(["grep", '"type":"message_end"', f], capture_output=True, text=True).stdout
    for l in p.splitlines():
        m = json.loads(l).get("message", {})
        u = m.get("usage") or {}
        if m.get("role") == "assistant":
            main += (u.get("cost") or {}).get("total", 0)
        elif m.get("role") == "toolResult":
            for r in (m.get("details") or {}).get("results") or []:
                a = agg[r.get("agent")]; ru = r.get("usage") or {}
                c = ru.get("cost"); c = c.get("total") if isinstance(c, dict) else c
                a[0] += 1; a[1] += c or 0; a[2] += (r.get("durationMs") or 0) / 60000
sub = sum(v[1] for v in agg.values())
print(f"main session ${main:.2f}   sub-agents ${sub:.2f}   total ${main+sub:.2f}")
print(f"{'agent':32} calls   cost  share  agent-min")
for k, v in sorted(agg.items(), key=lambda x: -x[1][1]):
    print(f"{k:32} {v[0]:5} {v[1]:6.2f} {v[1]/(main+sub):6.1%} {v[2]:8.1f}")
