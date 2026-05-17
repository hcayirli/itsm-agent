import os
import chromadb
from chromadb.utils import embedding_functions
from src.utils.logger import get_logger

logger = get_logger(__name__)

TICKET_COLLECTION = "ticket_vectors"


def _get_threshold() -> float:
    # Benzerlik eşiği: 0.80 = %80+ benzerlik gerekli (distance ≤ 0.20 anlamına gelir)
    return float(os.getenv("TICKET_SIMILARITY_THRESHOLD", "0.80"))


def _get_collection():
    host = os.getenv("CHROMA_HOST", "chromadb")
    port = int(os.getenv("CHROMA_PORT", "8000"))
    model = os.getenv("EMBEDDING_MODEL", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
    client = chromadb.HttpClient(host=host, port=port)
    ef = embedding_functions.SentenceTransformerEmbeddingFunction(model_name=model)
    return client.get_or_create_collection(
        name=TICKET_COLLECTION,
        embedding_function=ef,
        metadata={"hnsw:space": "cosine"},
    )


def upsert_ticket(key: str, summary: str, description: str) -> None:
    try:
        col = _get_collection()
        col.upsert(
            ids=[key],
            documents=[f"{summary} {description}"],
            metadatas=[{"key": key, "summary": summary}],
        )
    except Exception as e:
        logger.warning(f"Ticket embedding upsert hatası ({key}): {e}")


def find_similar_tickets(key: str, summary: str, description: str, top_k: int = 5) -> list[dict]:
    """
    Mevcut ticket ile semantik olarak benzer geçmiş ticketları döner.
    Dönen liste: [{"key": ..., "summary": ..., "distance": ..., "similarity_pct": ...}]
    """
    try:
        col = _get_collection()
        count = col.count()
        if count == 0:
            return []
        # +1 çünkü kendisi de sonuçlara gelebilir
        n = min(top_k + 1, count)
        res = col.query(
            query_texts=[f"{summary} {description}"],
            n_results=n,
        )
        similarity_threshold = _get_threshold()
        distance_threshold = 1 - similarity_threshold  # 0.80 benzerlik → 0.20 max distance
        results = []
        for i, doc_id in enumerate(res["ids"][0]):
            if doc_id == key:
                continue
            distance = res["distances"][0][i]
            if distance > distance_threshold:
                continue
            meta = res["metadatas"][0][i] if res.get("metadatas") else {}
            results.append({
                "key": doc_id,
                "summary": meta.get("summary", ""),
                "distance": round(distance, 4),
                "similarity_pct": round((1 - distance) * 100, 1),
            })
        return results[:top_k]
    except Exception as e:
        logger.warning(f"Benzer ticket sorgusu başarısız ({key}): {e}")
        return []
