# parse_events.py
import re
import json
import argparse
from pathlib import Path
from typing import List, Dict, Any

COLS = ["namespace", "last_seen", "type", "reason", "object", "message"]

def parse_table_line(line: str, col_count: int) -> List[str]:
    # Split by 2+ spaces (table-like alignment)
    parts = re.split(r"\s{2,}", line.rstrip())
    if len(parts) < col_count:
        # pad with empty strings
        parts += [""] * (col_count - len(parts))
    return parts[:col_count]

def parse_events_file(input_path: Path) -> List[Dict[str, Any]]:
    lines = [l for l in input_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    if not lines:
        return []

    header = lines[0]
    # Use same split logic on header to determine column positions
    header_cols = re.split(r"\s{2,}", header.strip())
    col_count = len(header_cols)

    events = []
    for line in lines[1:]:
        parts = parse_table_line(line, col_count)
        row = dict(zip(COLS, parts))
        # derive pod and resource type from object
        obj = row.get("object", "")
        resource = None
        pod = None
        if "/" in obj:
            resource, name = obj.split("/", 1)
            if resource == "pod":
                pod = name
        row["resource"] = resource
        row["pod"] = pod
        # simple severity level
        row["severity_hint"] = derive_severity(row)
        events.append(row)
    return events

def derive_severity(ev: Dict[str, Any]) -> int:
    etype = (ev.get("type") or "").lower()
    reason = (ev.get("reason") or "").lower()
    msg = (ev.get("message") or "").lower()
    if "imagepullbackoff" in msg or "imagepull" in reason:
        return 8
    if "failed" in reason or etype == "warning":
        return 7
    if "backoff" in reason or "back-off" in msg:
        return 6
    if etype == "normal":
        return 2
    return 3

def group_into_chunks(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Group events by (namespace, pod). For non-pod objects (secretstore, clustersecretstore),
    group by (namespace, object).
    """
    groups = {}
    for ev in events:
        ns = ev.get("namespace") or "default"
        pod = ev.get("pod")
        obj = ev.get("object")
        if pod:
            key = (ns, f"pod/{pod}")
        else:
            key = (ns, obj or "unknown")
        groups.setdefault(key, []).append(ev)

    chunks = []
    for (ns, objkey), evs in groups.items():
        # sort by severity_hint descending, then keep all
        evs_sorted = sorted(evs, key=lambda e: e.get("severity_hint", 0), reverse=True)
        lines = []
        max_sev = 0
        pod = None
        for e in evs_sorted:
            max_sev = max(max_sev, e.get("severity_hint", 0))
            if e.get("pod"):
                pod = e["pod"]
            line = (
                f"NAMESPACE={e.get('namespace')} "
                f"LAST_SEEN={e.get('last_seen')} "
                f"TYPE={e.get('type')} "
                f"REASON={e.get('reason')} "
                f"OBJECT={e.get('object')} "
                f"MESSAGE={e.get('message')}"
            )
            lines.append(line)
        context_text = "\n".join(lines)
        chunk_id = f"{ns}-{objkey}"
        chunks.append({
            "id": chunk_id,
            "cluster": "aks-cluster",    # or set externally if you know
            "namespace": ns,
            "pod": pod,
            "object": objkey,
            "start_ts": None,            # events don't have absolute time here; optional
            "end_ts": None,
            "severity_hint": max_sev,
            "context_text": context_text
        })
    return chunks

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="Path to raw AKS events log file")
    parser.add_argument("--events_out", default="processed/events.jsonl", help="Output JSONL for individual events")
    parser.add_argument("--chunks_out", default="processed/event_chunks.jsonl", help="Output JSONL for chunks (for embedding)")
    args = parser.parse_args()

    input_path = Path(args.input)
    events = parse_events_file(input_path)
    print(f"Parsed {len(events)} events")

    # Write events JSONL
    with open(args.events_out, "w", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")
    print(f"Wrote events JSONL -> {args.events_out}")

    # Build chunks
    chunks = group_into_chunks(events)
    with open(args.chunks_out, "w", encoding="utf-8") as f:
        for ch in chunks:
            f.write(json.dumps(ch) + "\n")
    print(f"Wrote chunks JSONL -> {args.chunks_out} (for embedding)")

if __name__ == "__main__":
    main()
