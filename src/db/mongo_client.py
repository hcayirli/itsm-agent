import os
from motor.motor_asyncio import AsyncIOMotorClient
from src.utils.logger import get_logger

logger = get_logger(__name__)

_collection = None
_settings_collection = None

# Büyük alanlar liste sorgusunda hariç tutulur, sadece detay sorgusunda getirilir
_SUMMARY_PROJECTION = {
    "_id": 0,
    "llm_output": 0,
    "system_prompt": 0,
    "user_prompt": 0,
    "kb_matches": 0,
    "investigation_findings": 0,
}


def _get_db():
    uri = os.getenv("MONGO_URI", "mongodb://mongodb:27017")
    db_name = os.getenv("MONGO_DB", "itsm_agent")
    return AsyncIOMotorClient(uri)[db_name]


def _get_collection():
    global _collection
    if _collection is None:
        _collection = _get_db()["jobs"]
    return _collection


def _get_settings_collection():
    global _settings_collection
    if _settings_collection is None:
        _settings_collection = _get_db()["settings"]
    return _settings_collection


async def save_job(job_dict: dict) -> None:
    try:
        col = _get_collection()
        doc = {**job_dict, "_id": job_dict["key"]}
        await col.replace_one({"_id": job_dict["key"]}, doc, upsert=True)
    except Exception as e:
        logger.error(f"MongoDB kayıt hatası ({job_dict.get('key')}): {e}")


async def get_all_db_jobs_summary() -> list[dict]:
    """Sadece özet alanları getirir (llm_output vb. hariç) — liste için."""
    try:
        col = _get_collection()
        cursor = col.find({}, _SUMMARY_PROJECTION)
        return await cursor.to_list(length=None)
    except Exception as e:
        logger.error(f"MongoDB özet okuma hatası: {e}")
        return []


async def get_db_job(key: str) -> dict | None:
    """Tek bir job'un tüm alanlarını getirir — detay görünümü için."""
    try:
        col = _get_collection()
        return await col.find_one({"_id": key}, {"_id": 0})
    except Exception as e:
        logger.error(f"MongoDB job okuma hatası ({key}): {e}")
        return None


async def get_done_keys() -> set[str]:
    try:
        col = _get_collection()
        cursor = col.find({"status": "done"}, {"key": 1, "_id": 0})
        docs = await cursor.to_list(length=None)
        return {d["key"] for d in docs}
    except Exception as e:
        logger.error(f"MongoDB done-keys hatası: {e}")
        return set()


async def delete_job(key: str) -> None:
    try:
        col = _get_collection()
        await col.delete_one({"_id": key})
    except Exception as e:
        logger.error(f"MongoDB job silme hatası ({key}): {e}")


async def clear_all_jobs() -> None:
    try:
        col = _get_collection()
        await col.delete_many({})
    except Exception as e:
        logger.error(f"MongoDB temizleme hatası: {e}")


async def save_settings(env_vars: dict) -> None:
    """Dashboard'dan kaydedilen env ayarlarını MongoDB'ye yazar."""
    try:
        col = _get_settings_collection()
        await col.replace_one(
            {"_id": "app_config"},
            {"_id": "app_config", **env_vars},
            upsert=True,
        )
    except Exception as e:
        logger.error(f"MongoDB settings kayıt hatası: {e}")


async def load_settings() -> dict:
    """Startup'ta MongoDB'den kaydedilmiş env ayarlarını yükler."""
    try:
        col = _get_settings_collection()
        doc = await col.find_one({"_id": "app_config"}, {"_id": 0})
        return doc or {}
    except Exception as e:
        logger.warning(f"MongoDB settings okuma hatası: {e}")
        return {}
