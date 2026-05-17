import os
import chromadb
from chromadb.utils import embedding_functions
from src.utils.logger import get_logger

logger = get_logger(__name__)

COLLECTIONS = [
    "kb_application", "kb_database", "kb_os", "kb_network", "kb_network_device",
    "kb_auth", "kb_storage", "kb_security",
    "kb_infrastructure", "kb_middleware",
]


def get_client() -> chromadb.HttpClient:
    return chromadb.HttpClient(
        host=os.getenv("CHROMA_HOST", "chromadb"),
        port=int(os.getenv("CHROMA_PORT", "8000")),
    )


def get_embedding_fn():
    model = os.getenv("EMBEDDING_MODEL", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
    return embedding_functions.SentenceTransformerEmbeddingFunction(model_name=model)


def get_or_create_collection(client: chromadb.HttpClient, name: str):
    return client.get_or_create_collection(name=name, embedding_function=get_embedding_fn())


def search_all_collections(query: str, top_k: int = 5) -> list[dict]:
    client = get_client()
    ef = get_embedding_fn()
    results = []

    for col_name in COLLECTIONS:
        try:
            col = client.get_collection(name=col_name, embedding_function=ef)
            count = col.count()
            if count == 0:
                continue
            res = col.query(query_texts=[query], n_results=min(top_k, count))
            for i, doc in enumerate(res["documents"][0]):
                results.append({
                    "collection": col_name,
                    "document": doc,
                    "distance": res["distances"][0][i],
                    "metadata": res["metadatas"][0][i] if res.get("metadatas") else {},
                })
        except Exception as e:
            logger.warning(f"{col_name} sorgulanamadı: {e}")

    results.sort(key=lambda x: x["distance"])
    return results[:top_k]
