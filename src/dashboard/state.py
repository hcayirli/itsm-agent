from datetime import datetime, timezone
from enum import Enum
from pydantic import BaseModel, Field
from typing import Optional


class JobStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    DONE = "done"
    FAILED = "failed"


class Job(BaseModel):
    key: str
    summary: str
    status: JobStatus = JobStatus.PENDING
    created_at: str = ""
    updated_at: str = ""
    priority: str = ""
    description: str = ""
    kb_matches: list[dict] = Field(default_factory=list)
    similar_tickets: list[dict] = Field(default_factory=list)
    system_prompt: str = ""
    user_prompt: str = ""
    agent_step: str = ""
    investigation_findings: str = ""
    review_feedback: str = ""
    revision_count: int = 0
    llm_output: str = ""
    error: str = ""
    saved_to_db: bool = False

    def model_post_init(self, __context):
        now = _now()
        if not self.created_at:
            self.created_at = now
        if not self.updated_at:
            self.updated_at = now


_jobs: dict[str, Job] = {}

# ─── Prompt overrides ─────────────────────────────────────────────────────────

DEFAULT_USER_TEMPLATE = (
    "Aşağıdaki Jira ticket için RCA raporu hazırla.\n\n"
    "## Ticket Bilgileri\n"
    "**Özet:** {summary}\n"
    "**Açıklama:** {description}\n\n"
    "## Bilgi Bankası (KB) Referansları\n"
    "{kb_context}\n\n"
    "## Geçmiş Benzer Ticketlar\n"
    "{similar_context}\n\n"
    "Yukarıdaki bilgilere dayanarak RCA raporunu yaz."
)

_prompt_overrides: dict[str, str] = {}


def get_system_prompt() -> str:
    return _prompt_overrides.get("system", "")


def get_user_template() -> str:
    return _prompt_overrides.get("user_template", "")


def set_prompts(system: str = "", user_template: str = "") -> None:
    _prompt_overrides["system"] = system
    _prompt_overrides["user_template"] = user_template


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_job(key: str, summary: str) -> Job:
    job = Job(key=key, summary=summary)
    _jobs[key] = job
    return job


def ensure_job(key: str, summary: str) -> Job:
    if key not in _jobs:
        return create_job(key, summary)
    job = _jobs[key]
    if job.summary in ("", "Yükleniyor..."):
        job.summary = summary
        job.updated_at = _now()
    return job


def update_job(key: str, **kwargs) -> None:
    if key not in _jobs:
        return
    job = _jobs[key]
    for k, v in kwargs.items():
        if hasattr(job, k):
            setattr(job, k, v)
    job.updated_at = _now()


def get_job(key: str) -> Optional[Job]:
    return _jobs.get(key)


def get_all_jobs() -> list[Job]:
    return sorted(_jobs.values(), key=lambda j: j.created_at)


def clear_jobs() -> None:
    _jobs.clear()


def remove_job(key: str) -> None:
    _jobs.pop(key, None)
