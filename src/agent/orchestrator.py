import os
from src.utils.logger import get_logger

logger = get_logger(__name__)


async def run_rca(ticket_key: str, summary: str, description: str) -> str:
    from src.dashboard import state
    from src.agent.graph import rca_graph

    logger.info(f"RCA başlıyor: {ticket_key}")
    state.update_job(ticket_key, status=state.JobStatus.PROCESSING)

    final = await rca_graph.ainvoke({
        "ticket_key": ticket_key,
        "summary": summary,
        "description": description,
        "top_k": int(os.getenv("RAG_TOP_K", "5")),
        "kb_results": [],
        "similar_tickets": [],
        "system_prompt": "",
        "user_prompt": "",
        "investigation_findings": "",
        "draft_report": "",
        "review_feedback": "",
        "revision_count": 0,
        "llm_output": "",
        "error": "",
    })

    return final.get("llm_output", "")
