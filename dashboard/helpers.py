import chromadb

CHROMA_PATH = "../chroma_store"

client = chromadb.PersistentClient(path=CHROMA_PATH)
collection = client.get_collection("aks_chunks")

def load_namespaces():
    metas = collection.get(include=["metadatas"])["metadatas"]
    return sorted(set(m.get("namespace") for m in metas if m.get("namespace")))

def load_pods(namespace: str):
    metas = collection.get(include=["metadatas"])["metadatas"]
    return sorted(set(m.get("pod") for m in metas if m.get("namespace") == namespace and m.get("pod")))
