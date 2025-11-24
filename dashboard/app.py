# app.py
import os
from fastapi import FastAPI
from pydantic import BaseModel
from azure.search.documents import SearchClient
from azure.core.credentials import AzureKeyCredential
import requests
import json
from sentence_transformers import SentenceTransformer

SEARCH_ENDPOINT = os.environ.get("AZURE_SEARCH_ENDPOINT")
SEARCH_KEY = os.environ.get("AZURE_SEARCH_KEY")
INDEX_NAME = os.environ.get("AZURE_SEARCH_INDEX", "aks-telemetry")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434/api/generate")  # example for Ollama REST

# local embed model for query vector (same family as indexing)
EMBED_MODEL = os.environ.get("EMBED_MODEL", "intfloat/e5-large")
embed_model = SentenceTransformer(EMBED_MODEL)

search_client = SearchClient(endpoint=SEARCH_ENDPOINT, index_name=INDEX_NAME, credential=AzureKeyCredential(SEARCH_KEY))
app = FastAPI(title="AKS RAG Diagnose API")

class DiagnoseRequest(BaseModel):
    cluster: str
    namespace: str | None = None
    pod: str | None = None
    from_ts: str | None = None
    to_ts: str | None = None
    k: int = 6

def embed_query(q: str):
    vec = embed_model.encode(q)
    return [float(x) for x in vec.tolist()]

def build_filter(req: DiagnoseRequest):
    filters = [f"cluster eq '{req.cluster}'"]
    if req.namespace:
        filters.append(f"namespace eq '{req.namespace}'")
    if req.pod:
        filters.append(f"pod eq '{req.pod}'")
    if req.from_ts:
        filters.append(f"timestamp ge '{req.from_ts}'")
    if req.to_ts:
        filters.append(f"timestamp le '{req.to_ts}'")
    return " and ".join(filters)

@app.post("/diagnose")
def diagnose(req: DiagnoseRequest):
    qtext = "Diagnose Kubernetes failure: which pod/node/cluster is affected and why?"
    qvec = embed_query(qtext)
    filter_str = build_filter(req)
    # vector query via Azure Cognitive Search
    results = search_client.search(search_text="", filter=filter_str,
                                   vector={"value": qvec, "fields": "vector", "k": req.k})
    chunks = []
    for r in results:
        chunks.append({
            "id": r["id"],
            "cluster": r.get("cluster"),
            "namespace": r.get("namespace"),
            "pod": r.get("pod"),
            "content": r.get("content")
        })
    evidence_text = "\n\n---\n\n".join([c["content"][:2000] for c in chunks[:req.k]])
    prompt = f"""You are a Kubernetes incident analyst. Given the following evidence chunks, answer:
1) Top 3 probable root causes (ranked with short explanation and confidence 0-1).
2) For each cause provide 1-3 supporting evidence lines (quote).
3) Suggested remediation steps and kubectl commands (safe first).
4) Any follow-up checks to confirm the cause.

Evidence:
{evidence_text}
"""
    # call local LLM via Ollama or other local API
    payload = {"model": "qwen2.5:7b-instruct", "prompt": prompt, "max_tokens": 800}
    try:
        llm_resp = requests.post(OLLAMA_URL, json=payload, timeout=120)
        llm_text = llm_resp.json().get("text") if llm_resp.status_code == 200 else llm_resp.text
    except Exception as e:
        llm_text = f"LLM call failed: {str(e)}"
    return {"diagnosis": llm_text, "evidence_count": len(chunks), "evidence": chunks}
