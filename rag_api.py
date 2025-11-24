# rag_api.py

import os, json, requests
from fastapi import FastAPI
from pydantic import BaseModel
import chromadb
from sentence_transformers import SentenceTransformer
from prompts import SYSTEM_PROMPT
from utils_rag import extract_llm_text

app = FastAPI()

# ---------- ENV ----------
EMBED_MODEL = os.getenv("EMBED_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
LLM_URL     = os.getenv("LLM_URL", "http://localhost:4891/v1/chat/completions")
LLM_MODEL   = os.getenv("LLM_MODEL", "qwen2.5-3b-instruct-q4_k_m.gguf")
CHROMA_PATH = os.getenv("CHROMA_PATH", "./chroma_store")

print(f"[INIT] Using embedding model: {EMBED_MODEL}")

# ---------- LOAD EMBEDDINGS SAFELY ----------
try:
    embed_model = SentenceTransformer(EMBED_MODEL)
    print("[INIT] Embedding model loaded.")
except Exception as e:
    print(f"[ERROR] Embedding model failed: {e}")
    embed_model = None


# ---------- CONNECT TO CHROMA ----------
client = chromadb.PersistentClient(path=CHROMA_PATH)
collection = client.get_or_create_collection("aks_chunks")


# ---------- REQUEST BODY ----------
class DiagnoseReq(BaseModel):
    namespace: str | None = None
    pod: str | None = None
    query: str | None = None
    k: int = 5


# ---------- ENDPOINT ----------
@app.post("/diagnose")
def diagnose(req: DiagnoseReq):

    # Ensure embeddings are operational
    if embed_model is None:
        return {
            "error": "Embedding model failed to load. Restart and check EMBED_MODEL path/name.",
            "stage": "embedding_failure"
        }

    search_query = req.query or f"Investigate issue in pod {req.pod}"
    print(f"[QUERY] {search_query}")

    try:
        qvec = embed_model.encode(search_query).tolist()
    except Exception as e:
        return {"error": str(e), "stage": "embedding_encode"}

    # Query nearest chunks
    try:
        result = collection.query(
            query_embeddings=[qvec],
            n_results=req.k,
            include=["documents", "metadatas"]
        )
    except Exception as e:
        return {"error": str(e), "stage": "chroma_query"}

    docs = result["documents"][0]
    metas = result["metadatas"][0]

    if not docs:
        return {"message": "No matching Kubernetes events found in database."}

    # Build evidence for LLM
    evidence_text = "\n\n".join(
        f"[{m.get('namespace','?')}/{m.get('pod','?')}] {d}"
        for d, m in zip(docs, metas)
    )

    payload = {
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"Kubernetes Events:\n\n{evidence_text}\n\nRespond ONLY in JSON."
            }
        ],
        "max_tokens": 600,
        "temperature": 0.2
    }

    print("[LLM] Sending request to model...")

    try:
        resp = requests.post(LLM_URL, json=payload, timeout=170)
        raw = resp.json()
        llm_output = extract_llm_text(raw)
    except Exception as e:
        return {"error": str(e), "stage": "llm_inference"}

    if not llm_output or llm_output.strip() == "":
        return {
            "error": "LLM returned empty output. Model may still be loading.",
            "raw_response": raw,
            "stage": "empty_llm_output"
        }

    return {
        "diagnosis": llm_output,
        "matched_chunks": len(docs),
        "query": search_query
    }
