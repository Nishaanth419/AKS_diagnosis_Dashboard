# etl_chunker.py
import json, uuid, os
from datetime import datetime, timedelta
from pathlib import Path

RAW_DIR = Path("samples/raw")
OUT_DIR = Path("processed")
OUT_DIR.mkdir(exist_ok=True, parents=True)
WINDOW_MINUTES = 5

ERROR_KEYS = ["OOMKilled","CrashLoopBackOff","ImagePullBackOff","ERROR","Exception","DiskPressure","evicting"]

def parse_ts(ts: str):
    return datetime.fromisoformat(ts.replace("Z","+00:00"))

def load_logs():
    logs = []
    for f in RAW_DIR.glob("*.json"):
        with f.open('r', encoding='utf-8') as fh:
            for line in fh:
                line=line.strip()
                if not line: continue
                rec = json.loads(line)
                rec.setdefault("cluster","local")
                rec.setdefault("namespace","default")
                rec.setdefault("pod","unknown")
                rec.setdefault("node","unknown")
                rec.setdefault("message","")
                rec["timestamp"] = parse_ts(rec["timestamp"])
                logs.append(rec)
    return sorted(logs, key=lambda r: r["timestamp"])

def is_error_line(msg: str):
    if not msg: return False
    for k in ERROR_KEYS:
        if k in msg:
            return True
    return False

def make_chunks(logs):
    chunks = []
    for r in logs:
        if is_error_line(r["message"]):
            ts = r["timestamp"]
            start = ts - timedelta(minutes=WINDOW_MINUTES)
            end = ts + timedelta(minutes=WINDOW_MINUTES)
            context = [l for l in logs if start <= l["timestamp"] <= end and l["pod"]==r["pod"]]
            texts = "\n".join(f"{l['timestamp'].isoformat()} {l['node']} {l['namespace']} {l['pod']} {l['message']}" for l in context)
            chunk = {
                "id": str(uuid.uuid4()),
                "cluster": r["cluster"],
                "namespace": r["namespace"],
                "pod": r["pod"],
                "node": r["node"],
                "start_ts": start.isoformat(),
                "end_ts": end.isoformat(),
                "content": texts
            }
            chunks.append(chunk)
    return chunks

def main():
    logs = load_logs()
    chunks = make_chunks(logs)
    for c in chunks:
        out = OUT_DIR / f"{c['id']}.json"
        with out.open('w', encoding='utf-8') as fh:
            json.dump(c, fh, ensure_ascii=False, indent=2)
    print(f"[ETL] Created {len(chunks)} chunks -> {OUT_DIR}")

if __name__ == "__main__":
    main()
