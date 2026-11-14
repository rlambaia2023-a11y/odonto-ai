import chromadb
from chromadb.utils import embedding_functions
from ..config import CHROMA_PATH, COLLECTION_NAME, EMBEDDING_MODEL


def get_collection():
    client = chromadb.PersistentClient(path=CHROMA_PATH)
    ef = embedding_functions.SentenceTransformerEmbeddingFunction(model_name=EMBEDDING_MODEL)
    return client.get_or_create_collection(name=COLLECTION_NAME, embedding_function=ef)


def upsert_chunks(chunks: list[dict]) -> int:
    col = get_collection()
    ids = [f"{c['metadata']['source']}:{c['metadata']['id']}:{i}" for i, c in enumerate(chunks)]
    col.upsert(documents=[c["text"] for c in chunks],
               metadatas=[c["metadata"] for c in chunks], ids=ids)
    return len(chunks)


def query(q: str, k: int = 5) -> list[dict]:
    col = get_collection()
    res = col.query(query_texts=[q], n_results=k)
    out = []
    for doc, meta in zip(res["documents"][0], res["metadatas"][0]):
        out.append({"text": doc, "metadata": meta})
    return out
