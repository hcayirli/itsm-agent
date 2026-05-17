"""Jira Agent — sooperset/mcp-atlassian MCP sunucusu üzerinden Jira işlemleri.

Transport: SSE — MCP_ATLASSIAN_URL adresindeki /sse endpoint'ine bağlanır.
Auth: JIRA_API_TOKEN (mcp-atlassian servisi tarafından yönetilir).
Markdown yorumlar: mcp-atlassian → ADF dönüşümünü içeride halleder.
"""
import json
import os
from contextlib import asynccontextmanager

from mcp import ClientSession
from mcp.client.sse import sse_client

from src.utils.logger import get_logger

logger = get_logger(__name__)


def _mcp_url() -> str:
    return os.getenv("MCP_ATLASSIAN_URL", "http://mcp-atlassian:9000/sse")


@asynccontextmanager
async def _session():
    async with sse_client(_mcp_url()) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


def _get_text(result) -> str:
    if getattr(result, "isError", False):
        raise Exception(f"MCP araç hatası: {getattr(result, 'content', result)}")
    content = getattr(result, "content", [])
    return content[0].text if content else ""


def _parse_json(text: str) -> dict | list:
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return {}


def _str(val) -> str:
    if isinstance(val, dict):
        return val.get("name", "")
    return str(val) if val else ""


# ─── Jira Agent ────────────────────────────────────────────────────────────────

class JiraMcpAgent:
    """sooperset/mcp-atlassian MCP sunucusu üzerinden Jira işlemlerini yöneten agent."""

    async def _log_tools(self, session: ClientSession) -> None:
        tools = await session.list_tools()
        names = [t.name for t in tools.tools]
        logger.info(f"MCP araçları ({len(names)}): {names}")

    async def get_all_tickets(self, project_key: str | None = None, max_results: int = 100) -> list[str]:
        jql = f"project={project_key} AND statusCategory!=Done" if project_key else "statusCategory!=Done"
        keys: list[str] = []
        start_at = 0
        batch_size = 50

        while len(keys) < max_results:
            fetch = min(batch_size, max_results - len(keys))
            async with _session() as session:
                if not keys and start_at == 0:
                    await self._log_tools(session)
                result = await session.call_tool("jira_search", {
                    "jql": jql,
                    "limit": fetch,
                    "start_at": start_at,
                    "fields": "key,summary",
                })
            text = _get_text(result)
            data = _parse_json(text)

            if isinstance(data, list):
                issues = data
            elif isinstance(data, dict):
                issues = data.get("issues", data.get("results", []))
            else:
                break

            batch_keys = [i["key"] for i in issues if isinstance(i, dict) and "key" in i]
            keys.extend(batch_keys)

            if len(batch_keys) < fetch:
                break
            start_at += fetch

        logger.info(f"Jira JQL → {len(keys)} ticket bulundu (jql={jql!r})")
        return keys[:max_results]

    async def get_ticket(self, issue_key: str) -> dict:
        async with _session() as session:
            result = await session.call_tool("jira_get_issue", {"issue_key": issue_key})
        text = _get_text(result)
        data = _parse_json(text)

        if isinstance(data, dict):
            return {
                "key": data.get("key", issue_key),
                "summary": data.get("summary", ""),
                "description": data.get("description", ""),
                "status": _str(data.get("status", "")),
                "priority": _str(data.get("priority", "")),
            }
        return {"key": issue_key, "summary": "", "description": text, "status": "", "priority": ""}

    async def add_comment(self, issue_key: str, body: str) -> None:
        async with _session() as session:
            await session.call_tool("jira_add_comment", {
                "issue_key": issue_key,
                "body": body,
            })
        logger.info(f"{issue_key} için Jira yorumu eklendi.")


# ─── Modül-düzey singleton + backward-compatible fonksiyonlar ─────────────────

_agent = JiraMcpAgent()


async def get_all_tickets(project_key: str | None = None, max_results: int = 100) -> list[str]:
    return await _agent.get_all_tickets(project_key, max_results)


async def get_ticket(issue_key: str) -> dict:
    return await _agent.get_ticket(issue_key)


async def add_comment(issue_key: str, body: str) -> None:
    return await _agent.add_comment(issue_key, body)
