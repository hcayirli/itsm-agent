import asyncio
import os
import httpx
from src.utils.logger import get_logger

logger = get_logger(__name__)

_RETRY_DELAYS = [10, 30, 60]


async def complete(system_prompt: str, user_prompt: str) -> str:
    if os.getenv("LLM_PROVIDER", "openrouter") == "ollama":
        return await _ollama(system_prompt, user_prompt)
    return await _openrouter(system_prompt, user_prompt)


async def _openrouter(system_prompt: str, user_prompt: str) -> str:
    api_key = os.getenv("OPENROUTER_API_KEY", "")
    model = os.getenv("OPENROUTER_MODEL", "openai/gpt-oss-120b:free")
    timeout = int(os.getenv("LLM_TIMEOUT", "120"))

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }
    async with httpx.AsyncClient(timeout=timeout) as client:
        for attempt, delay in enumerate(_RETRY_DELAYS + [None], start=1):
            resp = await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {api_key}"},
            )
            if resp.status_code == 429 and delay is not None:
                logger.warning(f"Rate limit (429), {delay}s sonra tekrar deneniyor (deneme {attempt})...")
                await asyncio.sleep(delay)
                continue
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]


async def _ollama(system_prompt: str, user_prompt: str) -> str:
    base_url = os.getenv("OLLAMA_BASE_URL", "http://ollama:11434")
    model = os.getenv("OLLAMA_MODEL", "llama3.2:3b")
    timeout = int(os.getenv("LLM_TIMEOUT", "120"))

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "stream": False,
    }
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(f"{base_url}/api/chat", json=payload)
        resp.raise_for_status()
        return resp.json()["message"]["content"]
