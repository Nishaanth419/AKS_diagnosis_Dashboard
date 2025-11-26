from chromadb import PersistentClient

client = PersistentClient(path="./chroma_store")
coll = client.get_or_create_collection("aks_chunks")

print("IDs:", coll.get()['ids'][:10])
print("Count:", len(coll.get()['ids']))
