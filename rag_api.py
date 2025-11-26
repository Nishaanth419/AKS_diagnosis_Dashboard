import os
import json
import requests
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from typing import Optional, List

import chromadb
from sentence_transformers import SentenceTransformer
from utils_rag import extract_llm_text


# ============================================================
# FASTAPI APP
# ============================================================
app = FastAPI(title="AKS RAG API")


# ============================================================
# CONFIG / MODEL INITIALIZATION
# ============================================================
EMBED_MODEL = os.getenv("EMBED_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
LLM_URL = os.getenv("LLM_URL", "http://localhost:4891/v1/chat/completions")
LLM_MODEL = os.getenv("LLM_MODEL", "qwen2.5-3b-instruct-q4_k_m.gguf")
CHROMA_PATH = os.getenv("CHROMA_PATH", "./chroma_store")

# Load embed model
try:
    embed_model = SentenceTransformer(EMBED_MODEL)
except Exception as e:
    embed_model = None
    print("[WARN] Embedding model load failed:", e)

# Connect to Chroma
client = chromadb.PersistentClient(path=CHROMA_PATH)
collection = client.get_or_create_collection("aks_chunks")


# ============================================================
# pydantic models
# ============================================================
class LogsListResponse(BaseModel):
    count: int
    items: List[dict]


class DiagnoseByIdRequest(BaseModel):
    chunk_id: Optional[str] = None
    query: Optional[str] = None
    k: int = 5


# ============================================================
# /logs ENDPOINT (works with new Chroma versions)
# ============================================================
@app.get("/logs", response_model=LogsListResponse)
def list_logs(
    namespace: Optional[str] = Query(None),
    pod: Optional[str] = Query(None),
    reason: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    sort_by: Optional[str] = Query("start_ts"),
    order: Optional[str] = Query("desc"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0)
):
    def norm(s):
        return s.strip().lower() if s else None

    ns_f = norm(namespace)
    pod_f = norm(pod)
    reason_f = norm(reason)
    q_f = q.strip() if q else None

    items = []

    # Vector search
    if q_f and embed_model:
        try:
            res = collection.query(
                query_embeddings=[embed_model.encode(q_f).tolist()],
                n_results=limit + offset,
                include=["documents", "metadatas", "distances"]
            )
        except Exception as e:
            raise HTTPException(500, f"Chroma query failed: {e}")

        docs = res["documents"][0]
        metas = res["metadatas"][0]
        ids = res["ids"][0]

        for cid, doc, meta in zip(ids, docs, metas):
            meta = meta or {}

            if ns_f and norm(meta.get("namespace")) != ns_f:
                continue
            if pod_f and norm(meta.get("pod")) != pod_f:
                continue
            if reason_f and norm(meta.get("reason")) != reason_f:
                continue

            items.append({"id": cid, "document": doc, "metadata": meta})

    else:
        # Full DB scan
        try:
            got = collection.get(include=["documents", "metadatas"])
        except Exception as e:
            raise HTTPException(500, f"Chroma get failed: {e}")

        ids = got["ids"]
        docs = got["documents"]
        metas = got["metadatas"]

        for cid, doc, meta in zip(ids, docs, metas):
            meta = meta or {}

            if ns_f and norm(meta.get("namespace")) != ns_f:
                continue
            if pod_f and norm(meta.get("pod")) != pod_f:
                continue
            if reason_f and norm(meta.get("reason")) != reason_f:
                continue

            items.append({"id": cid, "document": doc, "metadata": meta})

    # Normalize metadata
    KEYS = ["start_ts", "timestamp", "namespace", "pod", "node", "reason", "severity_hint"]
    for it in items:
        meta = it["metadata"]
        for k in KEYS:
            if not meta.get(k):
                meta[k] = ""
        it["metadata"] = meta

    # Sort
    reverse = (order.lower() == "desc")

    def sort_val(it):
        return it["metadata"].get(sort_by, "")

    try:
        items = sorted(items, key=sort_val, reverse=reverse)
    except:
        items = sorted(items, key=lambda x: x["id"], reverse=reverse)

    # Pagination
    total = len(items)
    page = items[offset: offset + limit]

    return {"count": total, "items": page}


# ============================================================
# /diagnose ENDPOINT — UPDATED WITH STRICT OUTPUT FORMAT
# ============================================================
@app.post("/diagnose")
def diagnose(req: DiagnoseByIdRequest):

    if not req.chunk_id and not req.query:
        raise HTTPException(400, "Either chunk_id or query must be provided.")

    # Case 1: direct fetch by ID
    if req.chunk_id:
        try:
            got = collection.get(
                ids=[req.chunk_id],
                include=["documents", "metadatas"]
            )
        except Exception as e:
            raise HTTPException(500, f"Chroma get failed: {e}")

        ids = got["ids"]
        docs = got["documents"]
        metas = got["metadatas"]

        if not ids:
            raise HTTPException(404, "Chunk not found.")

        evidence = [{
            "id": ids[0],
            "doc": docs[0],
            "meta": metas[0] or {}
        }]

    # Case 2: vector search
    else:
        if not embed_model:
            raise HTTPException(500, "Embedding model not configured.")

        try:
            res = collection.query(
                query_embeddings=[embed_model.encode(req.query).tolist()],
                n_results=req.k,
                include=["documents", "metadatas"]
            )
        except Exception as e:
            raise HTTPException(500, f"Chroma query failed: {e}")

        ids = res["ids"][0]
        docs = res["documents"][0]
        metas = res["metadatas"][0]

        evidence = [{"id": cid, "doc": d, "meta": m or {}} for cid, d, m in zip(ids, docs, metas)]

    # Build LLM prompt
    formatted = []
    for e in evidence:
        m = e["meta"]
        ts = m.get("start_ts") or m.get("timestamp") or ""
        formatted.append(
            f"ID: {e['id']}\nTimestamp: {ts}\nNamespace: {m.get('namespace')}\nPod: {m.get('pod')}\nNode: {m.get('node')}\n\n{e['doc']}"
        )

    evidence_text = "\n\n---\n\n".join(formatted)
    evidence_text = evidence_text[:3500]

    # 🔥 STRICT OUTPUT FORMAT INSTRUCTION
    user_prompt = f"""
You are an Azure Kubernetes troubleshooting assistant.

You MUST respond **ONLY** in the EXACT format below:

Root Cause
<short one-line reason>

Affected Components
<pod, namespace, node – list only relevant ones>

Recommended Fix
<step-by-step instructions + kubectl commands>

Severity (0–10)

---

Analyze the following AKS logs:

{evidence_text}
""".strip()

    payload = {
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": "You are an expert Kubernetes incident analyst."},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": 1000,
        "temperature": 0.5,
    }

    try:
        resp = requests.post(LLM_URL, json=payload, timeout=180)
    except Exception as e:
        raise HTTPException(500, f"LLM call failed: {e}")

    if resp.status_code != 200:
        raise HTTPException(500, f"LLM error: {resp.text}")

    text = extract_llm_text(resp.json())

    return {
        "diagnosis": text,
        "evidence": evidence,
        "matched": len(evidence)
    }
