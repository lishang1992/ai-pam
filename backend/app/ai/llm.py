"""大模型网关：对接任意 OpenAI 兼容接口（OpenAI / Azure / 通义 / DeepSeek / 本地 vLLM）。

当 AIB_LLM_API_KEY 为空或处于演示模式时，系统自动降级到本地规则化 Planner，
保证 Demo 在没有外网、没有 Key 的现场也能完整跑通闭环。
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import httpx

from ..settings import LLM_BASE_URL, LLM_API_KEY, LLM_MODEL, LLM_TEMPERATURE, LLM_TIMEOUT


class LLMError(RuntimeError):
    pass


def available() -> bool:
    from ..settings import llm_enabled

    return llm_enabled()


async def chat(
    messages: List[Dict[str, Any]],
    tool_schemas: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """调用 chat/completions，返回 OpenAI 格式的 message 对象。"""
    if not available():
        raise LLMError("未配置大模型（AIB_LLM_API_KEY 为空或处于演示模式）")

    payload: Dict[str, Any] = {
        "model": LLM_MODEL,
        "messages": messages,
        "temperature": LLM_TEMPERATURE,
    }
    if tool_schemas:
        payload["tools"] = tool_schemas
        payload["tool_choice"] = "auto"

    headers = {
        "Authorization": "Bearer %s" % LLM_API_KEY,
        "Content-Type": "application/json",
    }
    url = LLM_BASE_URL.rstrip("/") + "/chat/completions"
    async with httpx.AsyncClient(timeout=LLM_TIMEOUT) as client:
        resp = await client.post(url, headers=headers, json=payload)
        if resp.status_code >= 400:
            raise LLMError("大模型调用失败：HTTP %s %s" % (resp.status_code, resp.text[:300]))
        data = resp.json()
    try:
        return data["choices"][0]["message"]
    except (KeyError, IndexError):
        raise LLMError("大模型返回格式异常：%s" % json.dumps(data, ensure_ascii=False)[:300])


def parse_tool_call(message: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    calls = message.get("tool_calls") or []
    if not calls:
        return None
    call = calls[0]
    fn = call.get("function", {})
    raw_args = fn.get("arguments") or "{}"
    try:
        args = json.loads(raw_args)
    except Exception:
        args = {}
    return {
        "kind": "tool",
        "tool": fn.get("name"),
        "arguments": args,
        "reason": "由大模型根据当前故障现象规划",
        "call_id": call.get("id", ""),
    }

