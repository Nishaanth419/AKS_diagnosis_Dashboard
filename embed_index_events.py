# embed_index_events.py
import json
from pathlib import Path
from sentence_transformers import SentenceTransformer
import chromadb

# ---- CONFIG ----
MODEL_NAME = "sentence-transformers/paraphrase-MiniLM-L3-v2"  # Fast CPU embedding model
INPUT_FILE = Path("processed/event_chunks.jsonl")
PERSIST_DIR = Path("chroma_store")
BATCH_SIZE = 2000  # must be < 5461 limit

# ---- Load Embedding Model ----
print("[INFO] Loading embedding model:", MODEL_NAME)
model = SentenceTransformer(MODEL_NAME)

# ---- Initialize Chroma ----
client = chromadb.PersistentClient(path=str(PERSIST_DIR))
COL_NAME = "aks_chunks"
collection = client.get_or_create_collection(name=COL_NAME)

def index_chunks():
    if not INPUT_FILE.exists():
        print(f"[ERROR] Missing: {INPUT_FILE}")
        return

    lines = INPUT_FILE.read_text().splitlines()
    print(f"[INFO] Total Chunks: {len(lines)}")

    # Split into batches to satisfy Chroma constraints
    for batch_start in range(0, len(lines), BATCH_SIZE):
        batch = lines[batch_start: batch_start + BATCH_SIZE]

        ids, docs, embeddings, metas = [], [], [], []

        print(f"[INFO] Processing batch {batch_start} → {batch_start + len(batch)}")

        for i, line in enumerate(batch):
            entry = json.loads(line)
            text = entry.get("context_text", "").strip()
            if not text:
                continue

            vec = model.encode(text).tolist()

            chunk_id = entry.get("id", f"chunk_{batch_start+i}")
            ids.append(chunk_id)
            docs.append(text)
            embeddings.append(vec)

            metas.append({
                "namespace": entry.get("namespace", ""),
                "object": entry.get("object", ""),
                "severity_hint": entry.get("severity_hint", ""),
            })

        if ids:
            collection.upsert(
                ids=ids,
                documents=docs,
                embeddings=embeddings,
                metadatas=metas
            )
            print(f"[✓] Indexed batch size: {len(ids)}")

    print("\n🎉 [SUCCESS] Finished embedding ALL event logs into ChromaDB!\n")

if __name__ == "__main__":
    index_chunks()
