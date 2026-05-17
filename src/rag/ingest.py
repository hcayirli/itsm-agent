"""KB JSON dosyalarını ChromaDB collection'larına yükler. Bağımsız script olarak çalıştırılır."""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.rag.chroma_client import get_client, get_embedding_fn, get_or_create_collection
from src.utils.logger import get_logger

logger = get_logger(__name__)

KB_COLLECTION_MAP = {
    "app_kb.json": "kb_application",
    "database_kb.json": "kb_database",
    "os_kb.json": "kb_os",
    "network_kb.json": "kb_network",
    "network_device_kb.json": "kb_network_device",
    "auth_kb.json": "kb_auth",
    "storage_kb.json": "kb_storage",
    "security_kb.json": "kb_security",
    "generic_kb.json": "kb_infrastructure",
    "middleware_kb.json": "kb_middleware",
}


def load_kb_file(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def record_to_document(record: dict) -> tuple[str, str, dict]:
    """(id, document_text, metadata) döner."""
    doc_id = record.get("id", "")
    title = record.get("title", record.get("baslik", ""))
    content = record.get("content", record.get("icerik", ""))
    tags = record.get("tags", record.get("etiketler", []))
    severity = record.get("severity", record.get("onem_derecesi", ""))

    text = f"{title}\n\n{content}"
    metadata = {
        "title": title,
        "tags": ", ".join(tags) if isinstance(tags, list) else str(tags),
        "severity": severity,
        "kb": record.get("kb", ""),
    }
    return doc_id, text, metadata


def ingest_all(kb_dir: str = "kb"):
    client = get_client()
    kb_path = Path(kb_dir)

    for filename, collection_name in KB_COLLECTION_MAP.items():
        filepath = kb_path / filename
        if not filepath.exists():
            logger.warning(f"{filepath} bulunamadı, atlanıyor.")
            continue

        records = load_kb_file(filepath)
        col = get_or_create_collection(client, collection_name)

        ids, docs, metas = [], [], []
        for rec in records:
            doc_id, text, meta = record_to_document(rec)
            if not doc_id or not text.strip():
                continue
            # Tekrar yükleme önlemi: id zaten varsa atla
            existing = col.get(ids=[doc_id])
            if existing["ids"]:
                continue
            ids.append(doc_id)
            docs.append(text)
            metas.append(meta)

        if ids:
            col.add(ids=ids, documents=docs, metadatas=metas)
            logger.info(f"{collection_name}: {len(ids)} kayıt yüklendi ({filename})")
        else:
            logger.info(f"{collection_name}: Yeni kayıt yok ({filename})")


if __name__ == "__main__":
    kb_dir = sys.argv[1] if len(sys.argv) > 1 else "kb"
    ingest_all(kb_dir)
    logger.info("Ingest tamamlandı.")
