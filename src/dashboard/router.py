import os
from fastapi import APIRouter, BackgroundTasks, File, HTTPException, UploadFile
from pydantic import BaseModel
from typing import Optional

from src.dashboard import state
from src.db import mongo_client
from src.integrations.jira_agent import get_all_tickets, get_ticket
from src.agent.orchestrator import run_rca
from src.agent.prompts import RCA_SYSTEM_PROMPT
from src.utils.logger import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/api")


class ConfigIn(BaseModel):
    provider: str
    openrouter_model: Optional[str] = None
    openrouter_api_key: Optional[str] = None
    ollama_model: Optional[str] = None
    ollama_base_url: Optional[str] = None
    llm_timeout: Optional[int] = None
    jira_base_url: Optional[str] = None
    jira_user_email: Optional[str] = None
    jira_api_token: Optional[str] = None
    rag_top_k: Optional[int] = None
    ticket_similarity_threshold: Optional[float] = None


class FetchIn(BaseModel):
    project_key: Optional[str] = None


class PromptsIn(BaseModel):
    system_prompt: str
    user_template: str


# ─── Config ───────────────────────────────────────────────────────────────────

@router.get("/config")
def get_config():
    return {
        "provider": os.getenv("LLM_PROVIDER", "openrouter"),
        "openrouter_model": os.getenv("OPENROUTER_MODEL", "openai/gpt-oss-120b:free"),
        "openrouter_api_key": os.getenv("OPENROUTER_API_KEY", ""),
        "ollama_model": os.getenv("OLLAMA_MODEL", "llama3.2:3b"),
        "ollama_base_url": os.getenv("OLLAMA_BASE_URL", "http://ollama:11434"),
        "llm_timeout": int(os.getenv("LLM_TIMEOUT", "120")),
        "jira_base_url": os.getenv("JIRA_BASE_URL", ""),
        "jira_user_email": os.getenv("JIRA_USER_EMAIL", ""),
        "jira_api_token": os.getenv("JIRA_API_TOKEN", ""),
        "rag_top_k": int(os.getenv("RAG_TOP_K", "5")),
        "ticket_similarity_threshold": float(os.getenv("TICKET_SIMILARITY_THRESHOLD", "0.80")),
    }


@router.post("/config")
async def save_config(body: ConfigIn):
    updates: dict[str, str] = {"LLM_PROVIDER": body.provider}
    if body.openrouter_model is not None:
        updates["OPENROUTER_MODEL"] = body.openrouter_model
    if body.openrouter_api_key is not None:
        updates["OPENROUTER_API_KEY"] = body.openrouter_api_key
    if body.ollama_model is not None:
        updates["OLLAMA_MODEL"] = body.ollama_model
    if body.ollama_base_url is not None:
        updates["OLLAMA_BASE_URL"] = body.ollama_base_url
    if body.llm_timeout is not None:
        updates["LLM_TIMEOUT"] = str(body.llm_timeout)
    if body.jira_base_url is not None:
        updates["JIRA_BASE_URL"] = body.jira_base_url
    if body.jira_user_email is not None:
        updates["JIRA_USER_EMAIL"] = body.jira_user_email
    if body.jira_api_token is not None:
        updates["JIRA_API_TOKEN"] = body.jira_api_token
    if body.rag_top_k is not None:
        updates["RAG_TOP_K"] = str(body.rag_top_k)
    if body.ticket_similarity_threshold is not None:
        updates["TICKET_SIMILARITY_THRESHOLD"] = str(body.ticket_similarity_threshold)
    for key, val in updates.items():
        os.environ[key] = val
    await mongo_client.save_settings(updates)
    return {"ok": True}


# ─── Prompts ──────────────────────────────────────────────────────────────────

@router.get("/prompts")
def get_prompts():
    custom_sys = state.get_system_prompt()
    custom_usr = state.get_user_template()
    return {
        "system_prompt":    custom_sys or RCA_SYSTEM_PROMPT,
        "user_template":    custom_usr or state.DEFAULT_USER_TEMPLATE,
        "system_is_custom": bool(custom_sys),
        "user_is_custom":   bool(custom_usr),
    }


@router.post("/prompts")
def save_prompts(body: PromptsIn):
    state.set_prompts(system=body.system_prompt, user_template=body.user_template)
    return {"ok": True}


@router.post("/prompts/reset")
def reset_prompts():
    state.set_prompts(system="", user_template="")
    return {"ok": True}


# ─── Jobs ─────────────────────────────────────────────────────────────────────

@router.get("/jobs")
async def list_jobs():
    mem = {j.key: j for j in state.get_all_jobs()}
    db_rows = await mongo_client.get_all_db_jobs_summary()

    result = {
        key: {
            "key": j.key,
            "summary": j.summary,
            "status": j.status,
            "priority": j.priority,
            "created_at": j.created_at,
            "updated_at": j.updated_at,
            "error": j.error,
            "saved_to_db": j.saved_to_db,
        }
        for key, j in mem.items()
    }

    for row in db_rows:
        key = row.get("key", "")
        if key and key not in result:
            result[key] = {
                "key": key,
                "summary": row.get("summary", ""),
                "status": row.get("status", "done"),
                "priority": row.get("priority", ""),
                "created_at": row.get("created_at", ""),
                "updated_at": row.get("updated_at", ""),
                "error": row.get("error", ""),
                "saved_to_db": True,
            }

    def _ticket_num(j: dict) -> tuple:
        key = j.get("key", "")
        prefix, _, num = key.rpartition("-")
        return (prefix, int(num)) if num.isdigit() else (key, 0)

    jobs = sorted(result.values(), key=_ticket_num)
    return jobs


@router.get("/jobs/{key}")
async def get_job_detail(key: str):
    job = state.get_job(key)
    if job:
        return job.model_dump()
    db_job = await mongo_client.get_db_job(key)
    if db_job:
        return {**db_job, "saved_to_db": True}
    try:
        ticket = await get_ticket(key)
        return {
            "key": key,
            "summary": ticket.get("summary", ""),
            "description": ticket.get("description", ""),
            "priority": ticket.get("priority", ""),
            "status": "done",
            "agent_step": "",
            "kb_matches": [],
            "similar_tickets": [],
            "system_prompt": "",
            "user_prompt": "",
            "investigation_findings": "",
            "review_feedback": "",
            "revision_count": 0,
            "llm_output": "",
            "error": "",
            "saved_to_db": False,
            "created_at": "",
            "updated_at": "",
        }
    except Exception:
        raise HTTPException(status_code=404, detail="Job bulunamadı.")


@router.delete("/jobs/{key}")
async def delete_job(key: str):
    state.remove_job(key)
    return {"ok": True}


@router.delete("/jobs")
async def clear_jobs():
    state.clear_jobs()
    return {"ok": True}


# ─── Fetch & Process ──────────────────────────────────────────────────────────

@router.post("/fetch-and-process")
async def fetch_and_process(body: FetchIn, background_tasks: BackgroundTasks):
    try:
        keys = await get_all_tickets(project_key=body.project_key)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Jira bağlantı hatası: {e}")
    if not keys:
        raise HTTPException(status_code=404, detail="İşlenecek ticket bulunamadı.")

    done_keys = await mongo_client.get_done_keys()

    def _key_num(k: str) -> tuple:
        prefix, _, num = k.rpartition("-")
        return (prefix, int(num)) if num.isdigit() else (k, 0)

    queued = []
    skipped = []
    for key in sorted(keys, key=_key_num):
        if key in done_keys:
            skipped.append(key)
            continue
        existing = state.get_job(key)
        if existing and existing.status in (state.JobStatus.PENDING, state.JobStatus.PROCESSING):
            continue
        state.create_job(key, "Yükleniyor...")
        background_tasks.add_task(_process_ticket, key)
        queued.append(key)

    return {"queued": len(queued), "skipped": len(skipped), "total": len(keys), "keys": queued}


# ─── KB PDF Upload ────────────────────────────────────────────────────────────

@router.post("/kb/upload")
async def upload_kb_pdf(file: UploadFile = File(...)):
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Sadece PDF dosyaları kabul edilir.")
    from src.rag.pdf_ingest import process_pdf
    try:
        pdf_bytes = await file.read()
        result = await process_pdf(pdf_bytes, file.filename or "upload.pdf")
        return result
    except Exception as e:
        logger.error(f"PDF yükleme hatası ({file.filename}): {e}")
        raise HTTPException(status_code=500, detail=str(e))


async def _process_ticket(issue_key: str) -> None:
    try:
        ticket = await get_ticket(issue_key)
        state.ensure_job(issue_key, ticket["summary"])
        state.update_job(
            issue_key,
            priority=ticket.get("priority", ""),
            description=ticket.get("description", ""),
        )
        await run_rca(issue_key, ticket["summary"], ticket["description"])
    except Exception as e:
        logger.error(f"Dashboard ticket işleme hatası ({issue_key}): {e}")
        state.update_job(issue_key, status=state.JobStatus.FAILED, error=str(e))
