"""PDF dosyasını LLM ile analiz edip ChromaDB KB collection'larına yükler."""
import asyncio
import hashlib
import io
import json
import re

from src.integrations import llm_client
from src.rag.chroma_client import get_client, get_or_create_collection
from src.utils.logger import get_logger

logger = get_logger(__name__)

VALID_COLLECTIONS = [
    "kb_application", "kb_database", "kb_os", "kb_network",
    "kb_auth", "kb_storage", "kb_security", "kb_infrastructure", "kb_middleware",
]

_COLLECTION_DESCRIPTIONS = "\n".join([
    "- kb_application: Uygulama hataları, yazılım sorunları, servis çökmeleri",
    "- kb_database: Veritabanı hataları, sorgu sorunları, bağlantı havuzu",
    "- kb_os: İşletim sistemi sorunları, kernel hatası, process yönetimi",
    "- kb_network: Ağ ve bağlantı sorunları, DNS, güvenlik duvarı",
    "- kb_auth: Kimlik doğrulama, yetkilendirme, SSO sorunları",
    "- kb_storage: Depolama, disk sorunları, dosya sistemi",
    "- kb_security: Güvenlik açıkları, saldırı, veri ihlali",
    "- kb_infrastructure: Genel altyapı, sunucu, container, donanım sorunları",
    "- kb_middleware: Middleware, mesaj kuyruğu, servis bus sorunları",
])

_SYSTEM = f"""Sen bir ITSM bilgi bankası analistsin. Verilen metin bölümünü analiz edip yapılandırılmış KB (Knowledge Base) kayıtlarına dönüştürüyorsun.

Görevin:
1. Metni anlamlı konulara/sorunlara böl (her önemli konu için ayrı kayıt).
2. Her konu için uygun collection seç.
3. Başlık, içerik, etiket ve önem derecesi üret (hepsi Türkçe).

Kullanılabilir collection'lar:
{_COLLECTION_DESCRIPTIONS}

Her kayıt için JSON formatı:
{{"id":"<verilecek-prefix>-<kisa-slug>","collection":"<collection_adi>","title":"Kısa başlık","content":"Detaylı içerik (Türkçe)","tags":["etiket1","etiket2"],"severity":"Yüksek|Orta|Düşük"}}

SADECE JSON array döndür. Başka metin, açıklama veya kod bloğu ekleme."""

_USER = """ID prefix: {prefix}

Metin:
{chunk}"""

_CHUNK_SIZE = 8000


async def _llm_extract_chunk(chunk: str, prefix: str) -> list[dict]:
    raw = await llm_client.complete(_SYSTEM, _USER.format(prefix=prefix, chunk=chunk))
    raw = re.sub(r"```(?:json)?", "", raw).strip().strip("`").strip()
    # LLM bazen yanıt başına/sonuna açıklama ekler — sadece JSON array kısmını al
    match = re.search(r"\[.*\]", raw, re.DOTALL)
    if not match:
        logger.warning(f"LLM'den JSON array ayrıştırılamadı. Ham yanıt: {raw[:200]}")
        return []
    parsed = json.loads(match.group())
    return [r for r in parsed if isinstance(r, dict) and r.get("id") and r.get("content")]


def _ingest_records(records: list[dict]) -> dict[str, int]:
    """Kayıtları ChromaDB'ye upsert eder. {collection: count} döner."""
    client = get_client()
    stats: dict[str, int] = {}
    for rec in records:
        col_name = rec.get("collection", "kb_infrastructure")
        if col_name not in VALID_COLLECTIONS:
            col_name = "kb_infrastructure"
        col = get_or_create_collection(client, col_name)
        text = f"{rec.get('title', '')}\n\n{rec.get('content', '')}"
        metadata = {
            "title": rec.get("title", ""),
            "tags": ", ".join(rec.get("tags", [])) if isinstance(rec.get("tags"), list) else str(rec.get("tags", "")),
            "severity": rec.get("severity", ""),
            "kb": "pdf_upload",
        }
        col.upsert(ids=[rec["id"]], documents=[text], metadatas=[metadata])
        stats[col_name] = stats.get(col_name, 0) + 1
    return stats


async def process_pdf(pdf_bytes: bytes, filename: str) -> dict:
    """PDF bytes → metin çıkar → LLM ile KB kayıtlarına dönüştür → ChromaDB'ye yaz."""
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(pdf_bytes))
    pages_text = [p.extract_text() or "" for p in reader.pages]
    full_text = "\n\n".join(t for t in pages_text if t.strip())

    if not full_text.strip():
        raise ValueError("PDF'den metin çıkarılamadı (taranmış/görsel PDF olabilir).")

    logger.info(f"PDF '{filename}': {len(reader.pages)} sayfa, {len(full_text)} karakter.")

    # Dosya içeriğine göre tekrarlanabilir kısa prefix (çakışma önlemi)
    file_hash = hashlib.md5(pdf_bytes).hexdigest()[:6].upper()

    chunks = [full_text[i:i + _CHUNK_SIZE] for i in range(0, len(full_text), _CHUNK_SIZE)]
    logger.info(f"{len(chunks)} chunk LLM'e gönderiliyor.")

    all_records: list[dict] = []
    for idx, chunk in enumerate(chunks):
        prefix = f"PDF-{file_hash}-{idx}"
        try:
            recs = await _llm_extract_chunk(chunk, prefix)
            all_records.extend(recs)
            logger.info(f"Chunk {idx+1}/{len(chunks)}: {len(recs)} kayıt üretildi.")
        except Exception as e:
            logger.warning(f"Chunk {idx+1} işlenemedi: {e}")

    if not all_records:
        raise ValueError("LLM hiç KB kaydı üretemedi.")

    stats = await asyncio.to_thread(_ingest_records, all_records)
    logger.info(f"ChromaDB'ye yazıldı: {stats}")

    return {
        "filename": filename,
        "pages": len(reader.pages),
        "chunks_processed": len(chunks),
        "records_created": len(all_records),
        "collections": stats,
        "records": [
            {"id": r["id"], "title": r.get("title", ""), "collection": r.get("collection", "")}
            for r in all_records
        ],
    }
