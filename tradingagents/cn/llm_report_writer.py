"""LLM-backed neutral report writer for the China-market module."""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from dotenv import dotenv_values, find_dotenv
from pydantic import ValidationError
import requests

from tradingagents.cn.schema import CNNeutralStockReport
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.llm_clients.api_key_env import get_api_key_env
from tradingagents.llm_clients.factory import create_llm_client

logger = logging.getLogger(__name__)


class CNLLMReportWriter:
    """Rewrite a deterministic base report through the original LLM client stack."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = (config or DEFAULT_CONFIG).copy()
        self.provider = self.config["llm_provider"]
        self.model = self.config.get("quick_think_llm") or self.config.get("deep_think_llm")
        self.base_url = self.config.get("backend_url")
        self.timeout = self.config.get("llm_timeout", 45)
        self.llm = None
        if self.base_url:
            self.client = None
            return
        self.client = create_llm_client(
            provider=self.provider,
            model=self.model,
            base_url=self.base_url,
            timeout=self.timeout,
            reasoning_effort=self.config.get("openai_reasoning_effort"),
            anthropic_effort=self.config.get("anthropic_effort"),
            google_thinking_level=self.config.get("google_thinking_level"),
        )

    def rewrite_report(self, base_report: CNNeutralStockReport, context: dict[str, Any]) -> CNNeutralStockReport:
        prompt = _build_prompt(base_report, context)
        try:
            if self.base_url:
                content = self._invoke_openai_compatible(prompt)
            else:
                response = self._get_llm().invoke(prompt)
                content = getattr(response, "content", response)
            parsed = _parse_json_object(str(content))
            return _preserve_invariants(base_report, CNNeutralStockReport.model_validate(parsed))
        except Exception as exc:
            logger.warning("CNLLMReportWriter free-text fallback failed: %s", exc)
            return base_report.model_copy(
                update={
                    "data_limitations": [
                        *base_report.data_limitations,
                        f"LLM 报告生成失败，已回退到确定性模板: {exc}",
                    ]
                }
            )

    def _get_llm(self):
        if self.llm is None:
            self.llm = self.client.get_llm()
        return self.llm

    def _invoke_openai_compatible(self, prompt: str) -> str:
        api_key_env = get_api_key_env(self.provider)
        api_key = _api_key_from_env_file(api_key_env) or os.environ.get(api_key_env or "")
        if not api_key:
            raise ValueError(f"{api_key_env} is not set")

        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": "你是中国 A 股公开信息解读助手，只输出符合要求的 JSON。",
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.2,
            "max_tokens": 1800,
        }
        errors = []
        session = _llm_gateway_session()
        for url in _chat_completion_urls(self.base_url):
            try:
                response = session.post(
                    url,
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    json=payload,
                    timeout=self.timeout,
                )
                response.raise_for_status()
                data = _json_response(response)
                content = data["choices"][0]["message"].get("content")
                if not content:
                    raise ValueError("LLM 网关返回空 content")
                return content
            except Exception as exc:
                errors.append(f"{url}: {exc}")
        raise ValueError("LLM 网关调用失败；" + "；".join(errors))


def _build_prompt(base_report: CNNeutralStockReport, context: dict[str, Any]) -> str:
    payload = base_report.model_dump()
    return (
        "你是中国 A 股公开信息解读助手。请基于下方 JSON（这就是 base_report 的全部内容）"
        "重写一份更自然的中性中文结构化报告。\n"
        "只能解释公开信息和历史数据，不得输出买入、卖出、持有、目标价、仓位、止盈止损、"
        "短期涨跌预测、收益承诺或荐股表达。\n"
        "必须严格保留输入 JSON 的顶层字段结构（symbol、generated_at、company_overview、"
        "market_data_summary、financial_summary 等），返回与 CNNeutralStockReport 同构的 JSON 对象。\n"
        "重要：直接返回平铺的 JSON 对象，不要再包一层 {\"base_report\": {...}} 或 "
        "{\"report\": {...}} 这样的外壳。\n"
        "保持简洁，每个字符串字段控制在 120 字以内。source_references 可原样保留或留空。\n"
        f"{json.dumps(payload, ensure_ascii=False, default=str)}"
    )


def _compact_context(context: dict[str, Any]) -> dict[str, Any]:
    return {
        "profile": context.get("profile", {}),
        "financials": context.get("financials", {}),
        "announcements": (context.get("announcements") or [])[:5],
        "news": (context.get("news") or [])[:5],
        "daily_bars": _edge_items(context.get("daily_bars") or [], 5),
        "depth": context.get("depth"),
        "data_provider": context.get("data_provider"),
    }


def _edge_items(items: list[Any], count: int) -> list[Any]:
    if len(items) <= count * 2:
        return items
    return [*items[:count], *items[-count:]]


def _api_key_from_env_file(api_key_env: str | None) -> str:
    if not api_key_env:
        return ""
    env_path = find_dotenv(usecwd=True)
    if not env_path:
        return ""
    return dotenv_values(env_path).get(api_key_env) or ""


def _llm_gateway_session() -> requests.Session:
    session = requests.Session()
    session.trust_env = _env_flag("TRADINGAGENTS_LLM_USE_PROXY", False)
    return session


def _env_flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _chat_completion_urls(base_url: str) -> list[str]:
    base = base_url.rstrip("/")
    if base.endswith("/v1"):
        urls = [base + "/chat/completions"]
    else:
        urls = [base + "/v1/chat/completions", base + "/chat/completions"]
    return list(dict.fromkeys(urls))


def _json_response(response: requests.Response) -> dict[str, Any]:
    content_type = response.headers.get("content-type", "")
    text = response.text.strip()
    if "html" in content_type.lower() or text.startswith("<!doctype html") or text.startswith("<html"):
        raise ValueError("LLM 网关返回 HTML 页面，请检查 base URL 是否应包含 /v1")
    try:
        return response.json()
    except ValueError as exc:
        preview = text[:200].replace("\n", " ")
        raise ValueError(f"LLM 网关返回非 JSON 响应: {preview}") from exc


def _parse_json_object(content: str) -> dict[str, Any]:
    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("LLM 响应中未找到 JSON 对象")
    parsed = json.loads(text[start : end + 1])
    return _unwrap_report(parsed)


_REPORT_REQUIRED_KEYS = {"symbol", "generated_at", "company_overview"}


def _unwrap_report(parsed: Any) -> dict[str, Any]:
    """Some models wrap the answer in {"base_report": {...}} or {"report": {...}}.
    Strip that shell so model_validate sees the flat fields."""
    if not isinstance(parsed, dict):
        raise ValueError("LLM 响应不是 JSON 对象")
    if _REPORT_REQUIRED_KEYS.issubset(parsed.keys()):
        return parsed
    for key in ("base_report", "report", "data", "result"):
        inner = parsed.get(key)
        if isinstance(inner, dict) and _REPORT_REQUIRED_KEYS.issubset(inner.keys()):
            return inner
    return parsed


def _preserve_invariants(
    base_report: CNNeutralStockReport,
    candidate: CNNeutralStockReport,
) -> CNNeutralStockReport:
    """Keep identity, references, and compliance metadata under local control."""
    return candidate.model_copy(
        update={
            "symbol": base_report.symbol,
            "name": base_report.name,
            "generated_at": base_report.generated_at,
            "source_references": base_report.source_references,
            "forbidden_advice_detected": base_report.forbidden_advice_detected,
            "compliance_notes": base_report.compliance_notes,
            "disclaimer": base_report.disclaimer,
        }
    )
