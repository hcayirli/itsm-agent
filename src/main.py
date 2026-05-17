import hmac
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from src.agent.orchestrator import run_rca
from src.dashboard import state as job_state
from src.dashboard.router import router as dashboard_router
from src.db import mongo_client
from src.integrations.jira_agent import get_all_tickets, get_ticket
from src.utils.logger import get_logger

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    saved = await mongo_client.load_settings()
    for key, val in saved.items():
        os.environ[key] = str(val)
    if saved:
        logger.info(f"MongoDB'den {len(saved)} kaydedilmiş ayar yüklendi.")
    yield


app = FastAPI(title="RCA Agent", lifespan=lifespan)

_STATIC = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(_STATIC)), name="static")
app.include_router(dashboard_router)

WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")


def _verify_secret(request_secret: str | None) -> None:
    if not WEBHOOK_SECRET:
        return
    if not request_secret or not hmac.compare_digest(request_secret, WEBHOOK_SECRET):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Geçersiz webhook secret.")


async def _process_ticket(issue_key: str) -> None:
    try:
        ticket = await get_ticket(issue_key)
        job_state.ensure_job(issue_key, ticket["summary"])
        job_state.update_job(
            issue_key,
            priority=ticket.get("priority", ""),
            description=ticket.get("description", ""),
        )
        await run_rca(issue_key, ticket["summary"], ticket["description"])
    except Exception as e:
        logger.error(f"Ticket işleme hatası ({issue_key}): {e}")
        job_state.update_job(issue_key, status=job_state.JobStatus.FAILED, error=str(e))


@app.get("/")
async def serve_dashboard():
    return FileResponse(str(_STATIC / "index.html"))


@app.post("/webhook/jira", status_code=status.HTTP_202_ACCEPTED)
async def jira_webhook(request: Request, background_tasks: BackgroundTasks):
    _verify_secret(request.headers.get("X-Webhook-Secret"))

    payload = await request.json()
    issue_key = payload.get("issue", {}).get("key")
    if not issue_key:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="issue.key bulunamadı.")

    job_state.create_job(issue_key, "Yükleniyor...")
    background_tasks.add_task(_process_ticket, issue_key)
    logger.info(f"Webhook alındı: {issue_key}")
    return {"status": "kabul edildi", "issue": issue_key}


class ManualTrigger(BaseModel):
    issue_key: str


@app.post("/rca/trigger", status_code=status.HTTP_202_ACCEPTED)
async def manual_trigger(body: ManualTrigger, background_tasks: BackgroundTasks):
    job_state.create_job(body.issue_key, "Yükleniyor...")
    background_tasks.add_task(_process_ticket, body.issue_key)
    return {"status": "kabul edildi", "issue": body.issue_key}


class BulkTrigger(BaseModel):
    project_key: str | None = None
    max_results: int = 100


@app.post("/rca/run-all", status_code=status.HTTP_202_ACCEPTED)
async def run_all(body: BulkTrigger, background_tasks: BackgroundTasks):
    keys = await get_all_tickets(body.project_key, body.max_results)
    if not keys:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="İşlenecek ticket bulunamadı.")

    def _key_num(k: str) -> tuple:
        prefix, _, num = k.rpartition("-")
        return (prefix, int(num)) if num.isdigit() else (k, 0)

    for key in sorted(keys, key=_key_num):
        job_state.create_job(key, "Yükleniyor...")
        background_tasks.add_task(_process_ticket, key)
    logger.info(f"{len(keys)} ticket kuyruğa alındı.")
    return {"status": "kabul edildi", "queued": len(keys), "tickets": keys}


@app.get("/health")
async def health():
    return {"status": "ok"}
