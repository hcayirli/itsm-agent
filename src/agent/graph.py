"""LangGraph tabanlı RCA iş akışı — Multi-Agent tasarım.

Düğümler:
  retrieve         → KB araması + benzer ticket araması (paralel, asyncio.gather)
  upsert_embedding → Ticket vektörünü kaydet (arama sonrası; kendini bulmasın)
  build_prompt     → Paylaşılan user_prompt oluştur (ticket + KB + benzer ticketlar)
  investigate      → Ham analiz: zaman çizelgesi, durum, hipotezler, CAPA (Investigator)
  draft            → Ham bulguları RCA şablonuna dönüştür (Drafter); reviewer reddetmişse feedback ile tekrar
  review           → Kural ihlali kontrolü (Reviewer); maks. 2 iterasyon
  post_comment     → Jira yorumu ekle (sadece başarılı akışta)
  persist          → Job durumu güncelle + MongoDB'ye kaydet
"""
import asyncio
import os
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from src.agent.prompts import INVESTIGATOR_SYSTEM_PROMPT, REVIEWER_SYSTEM_PROMPT, RCA_SYSTEM_PROMPT
from src.db import mongo_client
from src.integrations import llm_client
from src.integrations.jira_agent import add_comment
from src.rag.chroma_client import search_all_collections
from src.rag.ticket_similarity import find_similar_tickets, upsert_ticket
from src.utils.logger import get_logger

logger = get_logger(__name__)


class RCAState(TypedDict):
    ticket_key: str
    summary: str
    description: str
    top_k: int
    kb_results: list[dict]
    similar_tickets: list[dict]
    system_prompt: str
    user_prompt: str
    investigation_findings: str
    draft_report: str
    review_feedback: str
    revision_count: int
    llm_output: str
    error: str


async def node_retrieve(state: RCAState) -> dict:
    """KB ve benzer ticket aramasını paralel çalıştır."""
    from src.dashboard import state as job_state

    job_state.update_job(state["ticket_key"], agent_step="retrieve")
    query = f"{state['summary']} {state['description']}"
    top_k = state["top_k"]

    kb_results, similar_tickets = await asyncio.gather(
        asyncio.to_thread(search_all_collections, query, top_k=top_k),
        asyncio.to_thread(
            find_similar_tickets,
            state["ticket_key"], state["summary"], state["description"], top_k,
        ),
        return_exceptions=True,
    )

    if isinstance(kb_results, Exception):
        logger.warning(f"KB araması başarısız: {kb_results}")
        kb_results = []
    if isinstance(similar_tickets, Exception):
        logger.warning(f"Benzer ticket araması başarısız: {similar_tickets}")
        similar_tickets = []

    job_state.update_job(state["ticket_key"], kb_matches=kb_results, similar_tickets=similar_tickets)
    logger.info(f"{len(kb_results)} KB eşleşmesi, {len(similar_tickets)} benzer ticket.")

    return {"kb_results": kb_results, "similar_tickets": similar_tickets}


async def node_upsert_embedding(state: RCAState) -> dict:
    """Ticket vektörünü kaydet (retrieve sonrası; kendini bulmasın)."""
    from src.dashboard import state as job_state
    job_state.update_job(state["ticket_key"], agent_step="upsert_embedding")
    try:
        await asyncio.to_thread(
            upsert_ticket, state["ticket_key"], state["summary"], state["description"]
        )
    except Exception as e:
        logger.warning(f"Ticket embedding kaydı başarısız: {e}")
    return {}


def node_build_prompt(state: RCAState) -> dict:
    """Paylaşılan user_prompt oluştur ve job state'e yaz."""
    from src.dashboard import state as job_state

    job_state.update_job(state["ticket_key"], agent_step="build_prompt")
    kb_results = state.get("kb_results", [])
    similar_tickets = state.get("similar_tickets", [])

    if kb_results:
        parts = []
        for i, r in enumerate(kb_results, 1):
            title = r["metadata"].get("title", "")
            parts.append(f"**[{i}] {title}** (Kaynak: {r['collection']})\n{r['document']}")
        kb_context = "\n\n---\n\n".join(parts)
    else:
        kb_context = "İlgili KB kaydı bulunamadı."

    if similar_tickets:
        lines = [
            f"{i}. **{t['key']}** — {t['summary']} (Benzerlik: %{t['similarity_pct']})"
            for i, t in enumerate(similar_tickets, 1)
        ]
        similar_context = "\n".join(lines)
    else:
        similar_context = "Benzer geçmiş ticket bulunamadı."

    drafter_system_prompt = job_state.get_system_prompt() or RCA_SYSTEM_PROMPT
    user_template = job_state.get_user_template() or job_state.DEFAULT_USER_TEMPLATE

    try:
        user_prompt = user_template.format(
            summary=state["summary"],
            description=state["description"],
            kb_context=kb_context,
            similar_context=similar_context,
        )
    except KeyError:
        user_prompt = user_template

    job_state.update_job(state["ticket_key"], system_prompt=drafter_system_prompt, user_prompt=user_prompt)

    return {"system_prompt": drafter_system_prompt, "user_prompt": user_prompt}


async def node_investigate(state: RCAState) -> dict:
    """Ham analiz: zaman çizelgesi, durum, kök neden hipotezleri, CAPA (Investigator)."""
    from src.dashboard import state as job_state
    job_state.update_job(state["ticket_key"], agent_step="investigate")
    try:
        findings = await llm_client.complete(INVESTIGATOR_SYSTEM_PROMPT, state["user_prompt"])
        job_state.update_job(state["ticket_key"], investigation_findings=findings)
        logger.info(f"Investigator tamamlandı: {state['ticket_key']}")
        return {"investigation_findings": findings}
    except Exception as e:
        logger.error(f"Investigator hatası ({state['ticket_key']}): {e}")
        return {"investigation_findings": "", "error": str(e)}


def _build_drafter_user_prompt(state: RCAState) -> str:
    findings = state.get("investigation_findings", "")
    feedback = state.get("review_feedback", "")

    parts = [
        f"## Ham Analiz Bulguları (Investigator Çıktısı)\n{findings}",
        f"## Kaynak Veri Referansı\n{state['user_prompt']}",
    ]
    if feedback:
        parts.append(
            f"## Reviewer Geri Bildirimi (Bu hataları düzelt)\n{feedback}"
        )
    parts.append("Yukarıdaki bulguları ve kaynak verileri kullanarak RCA raporunu yaz.")
    return "\n\n".join(parts)


async def node_draft(state: RCAState) -> dict:
    """Ham bulguları RCA şablonuna dönüştür (Drafter). Reviewer reddetmişse feedback ile tekrar."""
    from src.dashboard import state as job_state

    job_state.update_job(state["ticket_key"], agent_step="draft", revision_count=state.get("revision_count", 0))
    drafter_user = _build_drafter_user_prompt(state)
    drafter_system = state.get("system_prompt") or RCA_SYSTEM_PROMPT
    try:
        draft = await llm_client.complete(drafter_system, drafter_user)
        job_state.update_job(state["ticket_key"], llm_output=draft)
        logger.info(f"Drafter tamamlandı: {state['ticket_key']}")
        return {"draft_report": draft, "llm_output": draft, "review_feedback": ""}
    except Exception as e:
        logger.error(f"Drafter hatası ({state['ticket_key']}): {e}")
        error_report = (
            "# Otomatik Kök Neden Analizi (RCA) Raporu\n\n"
            "### Hata\n"
            f"RCA üretilemedi: {e}"
        )
        job_state.update_job(state["ticket_key"], llm_output=error_report)
        return {"draft_report": error_report, "llm_output": error_report, "error": str(e)}


async def node_review(state: RCAState) -> dict:
    """Kural ihlali kontrolü (Reviewer). Maks. 2 iterasyon için revision_count arttırır."""
    from src.dashboard import state as job_state
    job_state.update_job(state["ticket_key"], agent_step="review")
    draft = state.get("draft_report", "")
    reviewer_user = (
        f"## Ticket Özeti\n{state['summary']}\n\n"
        f"## İncelenecek Taslak RCA Raporu\n{draft}"
    )
    try:
        review_output = (await llm_client.complete(REVIEWER_SYSTEM_PROMPT, reviewer_user)).strip()
        if review_output.startswith("ONAYLANDI"):
            logger.info(f"Reviewer onayladı: {state['ticket_key']}")
            job_state.update_job(state["ticket_key"], review_feedback="")
            return {"review_feedback": ""}
        else:
            feedback = review_output.replace("REDDEDİLDİ", "", 1).strip()
            revision_count = state.get("revision_count", 0) + 1
            logger.info(f"Reviewer reddetti (iterasyon {revision_count}): {state['ticket_key']}")
            job_state.update_job(state["ticket_key"], review_feedback=feedback, revision_count=revision_count)
            return {"review_feedback": feedback, "revision_count": revision_count}
    except Exception as e:
        logger.warning(f"Reviewer hatası, taslak olduğu gibi kabul ediliyor ({state['ticket_key']}): {e}")
        return {"review_feedback": ""}


async def node_post_comment(state: RCAState) -> dict:
    """RCA raporunu Jira yorumu olarak ekle."""
    try:
        await add_comment(state["ticket_key"], state["llm_output"])
    except Exception as e:
        logger.warning(f"Jira yorum ekleme başarısız ({state['ticket_key']}): {e}")
    return {}


async def node_persist(state: RCAState) -> dict:
    """Job'u sonlandır ve MongoDB'ye kaydet."""
    from src.dashboard import state as job_state

    ticket_key = state["ticket_key"]

    if state.get("error"):
        job_state.update_job(ticket_key, status=job_state.JobStatus.FAILED, error=state["error"])
    else:
        job_state.update_job(ticket_key, status=job_state.JobStatus.DONE)

    job = job_state.get_job(ticket_key)
    if job:
        await mongo_client.save_job(job.model_dump())
        job_state.update_job(ticket_key, saved_to_db=True)

    logger.info(f"RCA tamamlandı: {ticket_key}")
    return {}


def _route_after_review(state: RCAState) -> str:
    feedback = state.get("review_feedback", "")
    revision_count = state.get("revision_count", 0)
    if feedback and revision_count < 2:
        return "draft"
    return "persist" if state.get("error") else "post_comment"


def _build_graph():
    graph = StateGraph(RCAState)

    graph.add_node("retrieve", node_retrieve)
    graph.add_node("upsert_embedding", node_upsert_embedding)
    graph.add_node("build_prompt", node_build_prompt)
    graph.add_node("investigate", node_investigate)
    graph.add_node("draft", node_draft)
    graph.add_node("review", node_review)
    graph.add_node("post_comment", node_post_comment)
    graph.add_node("persist", node_persist)

    graph.add_edge(START, "retrieve")
    graph.add_edge("retrieve", "upsert_embedding")
    graph.add_edge("upsert_embedding", "build_prompt")
    graph.add_edge("build_prompt", "investigate")
    graph.add_edge("investigate", "draft")
    graph.add_edge("draft", "review")
    graph.add_conditional_edges(
        "review",
        _route_after_review,
        {"draft": "draft", "post_comment": "post_comment", "persist": "persist"},
    )
    graph.add_edge("post_comment", "persist")
    graph.add_edge("persist", END)

    return graph.compile()


rca_graph = _build_graph()
