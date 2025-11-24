from fastapi import FastAPI
from pydantic import BaseModel
from qdrant_client import QdrantClient
from sentence_transformers import SentenceTransformer
import requests

app = FastAPI()
client = QdrantClient("http://localhost:6333")
embed_model = SentenceTransformer("all-MiniLM-L6-v2")

OLLAMA_URL = "http://localhost:11434/api/generate"
COLLECTION = "aks_chunks"

class DiagnoseReq(BaseModel):
    query: str

def embed(q):
    return embed_model.encode(q).tolist()

@app.post("/diagnose")
def diagnose(req: DiagnoseReq):
    vec = embed(req.query)

    hits = client.search(
        collection_name=COLLECTION,
        query_vector=vec,
        limit=5
    )

    evidence = "\n\n".join([h.payload["content"][:1500] for h in hits])

    prompt = f"""
You are a Kubernetes troubleshooting AI.

Logs:
{evidence}

Explain:
1. Root cause
2. Evidence lines
3. Fix with kubectl commands
"""

    resp = requests.post(
        OLLAMA_URL,
        json={"model":"qwen2.5:7b-instruct", "prompt": prompt}
    )

    return {
        "diagnosis": resp.json().get("text", ""),
        "chunks": len(hits)
    }
