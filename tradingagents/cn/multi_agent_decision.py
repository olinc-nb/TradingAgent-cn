"""China-market multi-agent decision adapter.

This module mirrors the original TradingAgents role flow for A-share inputs:
analysts -> bull/bear researchers -> trader -> risk debate -> portfolio manager.
The first implementation is deterministic so it remains testable without LLM
credentials; it consumes the same market provider data used by the neutral
report endpoint and exposes the trading output as a separate research panel.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Callable
from datetime import datetime
from zoneinfo import ZoneInfo

from dotenv import dotenv_values, find_dotenv
import requests

from tradingagents.cn.market_data import ChinaMarketDataProvider
from tradingagents.cn.peg import compute_peg
from tradingagents.cn.schema import (
    CNAgentView,
    CNInvestmentDebate,
    CNNeutralStockReport,
    CNRiskDebate,
    CNTradingDecision,
)
from tradingagents.cn.services.peg_data import PEGDataCollector
from tradingagents.cn.symbol import CNSymbol
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.llm_clients.api_key_env import get_api_key_env
from tradingagents.llm_clients.factory import create_llm_client


logger = logging.getLogger(__name__)


class CNMultiAgentDecisionFlow:
    def __init__(
        self,
        data_provider: ChinaMarketDataProvider,
        llm_decision_writer: "CNAstockLLMDecisionWriter | None" = None,
        progress_callback: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> None:
        self.data_provider = data_provider
        self.llm_decision_writer = llm_decision_writer
        self.progress_callback = progress_callback
        if self.llm_decision_writer is not None and progress_callback is not None:
            self.llm_decision_writer.progress_callback = progress_callback

    def _emit(self, event: str, **payload: Any) -> None:
        if self.progress_callback is None:
            return
        try:
            self.progress_callback(event, payload)
        except Exception:
            logger.debug("CN decision progress callback failed", exc_info=True)

    def run(
        self,
        symbol: CNSymbol,
        analysis_date: str,
        depth: str,
        neutral_report: CNNeutralStockReport,
    ) -> CNTradingDecision:
        self._emit("decision_data_start", label="读取行情、公告、新闻、政策、资金与解禁数据")
        profile = self.data_provider.get_security_profile(symbol)
        bars = self.data_provider.get_daily_bars(symbol, analysis_date, analysis_date)
        financials = self.data_provider.get_financial_summary(symbol)
        announcements = self.data_provider.get_announcements(symbol, limit=_limit_for_depth(depth))
        news = self.data_provider.get_news(symbol, limit=_limit_for_depth(depth))
        policy_events = self.data_provider.get_policy_events(symbol, analysis_date, limit=_limit_for_depth(depth))
        capital_flow = self.data_provider.get_capital_flow(symbol, analysis_date)
        lockup_events = self.data_provider.get_lockup_events(symbol, analysis_date)
        self._emit("peg_start", label="采集一致预期 EPS 与历史净利润，计算 PEG")
        peg_payload = _collect_peg_payload(self.data_provider, symbol, financials)
        self._emit(
            "peg_done",
            label="PEG 估值已计算",
            peg=peg_payload.get("peg"),
            rating=peg_payload.get("rating"),
        )

        context = {
            "profile": profile,
            "daily_bars": bars,
            "financials": financials,
            "announcements": announcements,
            "news": news,
            "policy_events": policy_events,
            "capital_flow": capital_flow,
            "lockup_events": lockup_events,
            "peg": peg_payload,
            "depth": depth,
            "data_provider": getattr(self.data_provider, "provider_name", "unknown"),
        }
        metrics = _derive_metrics(
            bars,
            financials,
            announcements,
            news,
            policy_events,
            capital_flow,
            lockup_events,
            peg_payload,
        )
        self._emit(
            "decision_data_done",
            label="数据上下文已汇总",
            score=metrics.get("score"),
            bars=len(bars),
            announcements=len(announcements),
            news=len(news),
        )
        analysts = _analyst_views(neutral_report, metrics)
        debate = _investment_debate(metrics, analysts)
        trader_plan = _trader_plan(metrics, debate)
        risk_debate = _risk_debate(metrics, neutral_report)
        action, confidence = _portfolio_action(metrics)
        recommendation = _investment_recommendation(metrics)
        trade_decision = _trade_decision(metrics)
        target_range, stop_loss, take_profit = _price_plan(metrics)

        base_decision = CNTradingDecision(
            symbol=symbol.standard,
            generated_at=datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
            analyst_reports=analysts,
            investment_debate=debate,
            trader_plan=trader_plan,
            risk_debate=risk_debate,
            investment_recommendation=recommendation,
            trade_decision=trade_decision,
            simulated_action=action,
            confidence=confidence,
            horizon="短中期观察窗口，通常需结合未来 1-4 个公告或交易周复核。",
            target_price_range=target_range,
            position_suggestion=_position_suggestion(metrics),
            stop_loss=stop_loss,
            take_profit=take_profit,
            key_drivers=_key_drivers(profile, metrics),
            risk_controls=_risk_controls(metrics),
            decision_basis=[
                "多智能体流程复用原版 TradingAgents 的角色分工，但数据源切换为 A 股公开信息 provider。",
                "投资建议/交易决策作为额外板块输出，和中性公开信息报告分区展示。",
                "PEG 估值已作为 PEG Analyst 视角融入分析师层、投研辩论与组合经理决策。",
                "所有结论受数据完整性、外部接口稳定性和模型/规则假设影响。",
            ],
            peg_metrics=peg_payload,
        )
        if self.llm_decision_writer is None:
            self._emit("decision_done", label="确定性多智能体流程完成")
            return base_decision
        self._emit("decision_llm_start", label="进入 LLM 多智能体辩论层")
        return self.llm_decision_writer.rewrite_decision(
            base_decision=base_decision,
            neutral_report=neutral_report,
            context=context,
            metrics=metrics,
        )


class CNAstockLLMDecisionWriter:
    """LLM decision writer inspired by TradingAgents-astock's full A-share graph.

    The local CN API still owns data collection and deterministic fallback. When
    credentials are configured, this writer asks the quick model for the 7
    analyst layer and the deep model for the manager/portfolio synthesis.
    """

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        progress_callback: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> None:
        self.config = (config or DEFAULT_CONFIG).copy()
        self.progress_callback = progress_callback
        self.provider = self.config["llm_provider"]
        self.quick_model = self.config.get("quick_think_llm") or self.config.get("deep_think_llm")
        self.deep_model = self.config.get("deep_think_llm") or self.quick_model
        self.base_url = self.config.get("backend_url")
        self.timeout = self.config.get("llm_timeout", 60)
        llm_kwargs = _provider_kwargs(self.config)
        if self.base_url:
            self.quick_client = None
            self.deep_client = None
            self._quick_llm = None
            self._deep_llm = None
            return
        self.quick_client = create_llm_client(
            provider=self.provider,
            model=self.quick_model,
            base_url=self.base_url,
            timeout=self.timeout,
            **llm_kwargs,
        )
        self.deep_client = create_llm_client(
            provider=self.provider,
            model=self.deep_model,
            base_url=self.base_url,
            timeout=self.timeout,
            **llm_kwargs,
        )
        self._quick_llm = None
        self._deep_llm = None

    def _emit(self, event: str, **payload: Any) -> None:
        if self.progress_callback is None:
            return
        try:
            self.progress_callback(event, payload)
        except Exception:
            logger.debug("CN LLM decision progress callback failed", exc_info=True)

    def rewrite_decision(
        self,
        base_decision: CNTradingDecision,
        neutral_report: CNNeutralStockReport,
        context: dict[str, Any],
        metrics: dict[str, Any],
    ) -> CNTradingDecision:
        analyst_reports = base_decision.analyst_reports
        portfolio_synthesis_started = False
        try:
            self._emit("analysts_llm_start", label="7 个分析师调用 quick 模型生成观点")
            analyst_reports = self._try_rewrite_analysts(base_decision, neutral_report, context, metrics)
            self._emit("analysts_llm_done", label="分析师观点已生成", count=len(analyst_reports))
            candidate_base = CNTradingDecision.model_validate(
                {
                    **base_decision.model_dump(),
                    "analyst_reports": [_dump_model(item) for item in analyst_reports],
                }
            )
            portfolio_synthesis_started = True
            content = self._invoke_portfolio_synthesis(candidate_base, neutral_report, context, metrics)
            parsed = _parse_json_object(content)
            candidate = _decision_from_llm_payload(candidate_base, parsed)
            self._emit("portfolio_llm_done", label="组合经理决策已生成")
            return _preserve_decision_invariants(base_decision, candidate)
        except Exception as exc:
            logger.exception("CNAstockLLMDecisionWriter failed; using deterministic decision fallback")
            self._emit(
                "llm_decision_fallback",
                label="LLM 多智能体决策失败，已回退到确定性决策",
                stage="portfolio" if portfolio_synthesis_started else "analysts",
                error=str(exc)[:300],
            )
            return base_decision.model_copy(
                update={
                    "analyst_reports": analyst_reports,
                    "decision_basis": [
                        *base_decision.decision_basis,
                        f"LLM 多智能体决策生成失败，已回退到确定性适配层: {exc}",
                    ]
                }
            )

    def _try_rewrite_analysts(
        self,
        base_decision: CNTradingDecision,
        neutral_report: CNNeutralStockReport,
        context: dict[str, Any],
        metrics: dict[str, Any],
    ) -> list[CNAgentView]:
        try:
            return self._rewrite_analysts(base_decision, neutral_report, context, metrics)
        except Exception as exc:
            logger.warning("Astock analyst LLM layer failed; continuing with deterministic 7 analyst reports: %s", exc)
            return base_decision.analyst_reports

    def _rewrite_analysts(
        self,
        base_decision: CNTradingDecision,
        neutral_report: CNNeutralStockReport,
        context: dict[str, Any],
        metrics: dict[str, Any],
    ) -> list[CNAgentView]:
        content = self._invoke_quick(_build_analyst_prompt(base_decision, neutral_report, context, metrics))
        parsed = _parse_json_object(content)
        reports = parsed.get("analyst_reports")
        if not isinstance(reports, list):
            raise ValueError("analyst_reports must be a list")
        views = [CNAgentView.model_validate(item) for item in reports]
        agents = {item.agent for item in views}
        required = {
            "Market Analyst",
            "Sentiment Analyst",
            "News Analyst",
            "Fundamentals Analyst",
            "Policy Analyst",
            "Hot Money Tracker",
            "Lockup Watcher",
            "PEG Analyst",
        }
        missing = sorted(required - agents)
        if missing:
            raise ValueError("missing analyst reports: " + ", ".join(missing))
        return views

    def _get_quick_llm(self):
        if self._quick_llm is None:
            self._quick_llm = self.quick_client.get_llm()
        return self._quick_llm

    def _get_deep_llm(self):
        if self._deep_llm is None:
            self._deep_llm = self.deep_client.get_llm()
        return self._deep_llm

    def _invoke_quick(self, prompt: str) -> str:
        if self.base_url:
            return self._invoke_openai_compatible(prompt, self.quick_model)
        return _invoke_llm(self._get_quick_llm(), prompt)

    def _invoke_deep(self, prompt: str) -> str:
        if self.base_url:
            return self._invoke_openai_compatible(prompt, self.deep_model)
        return _invoke_llm(self._get_deep_llm(), prompt)

    def _invoke_portfolio_synthesis(
        self,
        candidate_base: CNTradingDecision,
        neutral_report: CNNeutralStockReport,
        context: dict[str, Any],
        metrics: dict[str, Any],
    ) -> str:
        try:
            self._emit("portfolio_llm_full_start", label="调用 deep 模型进行完整组合经理综合")
            return self._invoke_deep(
                _build_portfolio_prompt(candidate_base, neutral_report, context, metrics, self.config)
            )
        except Exception as exc:
            logger.warning("Full portfolio LLM synthesis failed; retrying with compact prompt: %s", exc)
            self._emit(
                "portfolio_llm_compact_retry",
                label="完整上下文被网关断开，改用压缩上下文重试",
                error=str(exc)[:300],
            )
            if self.base_url:
                content = self._invoke_openai_compatible(
                    _build_compact_portfolio_prompt(candidate_base, neutral_report, context, metrics, self.config),
                    self.deep_model,
                    max_tokens=1400,
                )
                self._emit("portfolio_llm_compact_done", label="压缩上下文 LLM 综合已返回")
                return content
            content = _invoke_llm(self._get_deep_llm(), _build_compact_portfolio_prompt(
                candidate_base, neutral_report, context, metrics, self.config
            ))
            self._emit("portfolio_llm_compact_done", label="压缩上下文 LLM 综合已返回")
            return content

    def _invoke_openai_compatible(self, prompt: str, model: str, max_tokens: int | None = None) -> str:
        api_key_env = get_api_key_env(self.provider)
        api_key = _api_key_from_env_file(api_key_env) or os.environ.get(api_key_env or "")
        if not api_key:
            raise ValueError(f"{api_key_env} is not set")
        payload = {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": "你是 TradingAgents-astock 风格的 A 股多智能体投研系统，只输出符合要求的 JSON。",
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.2,
            "max_tokens": max_tokens or 2600,
        }
        errors = []
        session = _llm_gateway_session()
        for attempt in range(1, 3):
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
                        raise ValueError("LLM gateway returned empty content")
                    return content
                except Exception as exc:
                    errors.append(f"attempt {attempt} {url}: {exc}")
        raise ValueError("LLM gateway call failed; " + "; ".join(errors))


def _provider_kwargs(config: dict[str, Any]) -> dict[str, Any]:
    kwargs: dict[str, Any] = {}
    provider = str(config.get("llm_provider", "")).lower()
    if provider == "openai" and config.get("openai_reasoning_effort"):
        kwargs["reasoning_effort"] = config.get("openai_reasoning_effort")
    if provider == "anthropic" and config.get("anthropic_effort"):
        kwargs["effort"] = config.get("anthropic_effort")
    if provider == "google" and config.get("google_thinking_level"):
        kwargs["thinking_level"] = config.get("google_thinking_level")
    return kwargs


def _invoke_llm(llm, prompt: str) -> str:
    response = llm.invoke(prompt)
    content = getattr(response, "content", response)
    if not content:
        raise ValueError("LLM returned empty content")
    return str(content)


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
        raise ValueError("LLM gateway returned HTML; check whether base URL should include /v1")
    try:
        return response.json()
    except ValueError as exc:
        preview = text[:200].replace("\n", " ")
        raise ValueError(f"LLM gateway returned non-JSON response: {preview}") from exc


def _build_analyst_prompt(
    base_decision: CNTradingDecision,
    neutral_report: CNNeutralStockReport,
    context: dict[str, Any],
    metrics: dict[str, Any],
) -> str:
    payload = {
        "base_analyst_reports": [_dump_model(item) for item in base_decision.analyst_reports],
        "neutral_report": _dump_model(neutral_report),
        "market_context": _compact_context(context),
        "derived_metrics": metrics,
    }
    return (
        "你是 TradingAgents-astock 风格的 A 股 8 Analyst 层。请基于公开数据生成八份研报观点。\n"
        "必须覆盖且只用这些 agent 名称: Market Analyst, Sentiment Analyst, News Analyst, "
        "Fundamentals Analyst, Policy Analyst, Hot Money Tracker, Lockup Watcher, PEG Analyst。\n"
        "PEG Analyst 必须基于 derived_metrics 中的 peg / forward_pe / cagr_pct / digestion_years 字段写明：\n"
        "- 评级口径：PEG<0.5 极度低估、[0.5,1) 低估、[1,1.5) 合理、[1.5,2) 偏贵、>=2 高估；\n"
        "- 引用真实 PEG 数值与消化年限，并指出 PEG 在亏损股/周期股/金融股上的适用边界；\n"
        "- 数据缺失或 CAGR 非正时，stance 写 “PEG 数据不足，暂不计入估值打分”。\n"
        "吸收 astock 架构重点: 政策、游资/资金流、限售解禁是一等分析师；每个 analyst 要写明工具证据，"
        "无法获取的数据用 [数据缺失: xxx] 标注。\n"
        "只输出 JSON 对象，格式为 {\"analyst_reports\": [CNAgentView, ...]}。"
        "CNAgentView 字段为 agent, stance, summary, evidence。每个 summary 控制在 160 字内。\n"
        f"{json.dumps(payload, ensure_ascii=False, default=str)}"
    )


def _build_portfolio_prompt(
    base_decision: CNTradingDecision,
    neutral_report: CNNeutralStockReport,
    context: dict[str, Any],
    metrics: dict[str, Any],
    config: dict[str, Any],
) -> str:
    payload = {
        "base_decision": _dump_model(base_decision),
        "neutral_report": _dump_model(neutral_report),
        "market_context": _compact_context(context),
        "derived_metrics": metrics,
        "max_debate_rounds": config.get("max_debate_rounds", 1),
        "max_risk_discuss_rounds": config.get("max_risk_discuss_rounds", 1),
    }
    return (
        "你是 TradingAgents-astock 风格的 Research Manager 与 Portfolio Manager。"
        "请基于输入生成完整 CNTradingDecision JSON。\n"
        "流程必须体现: Bull vs Bear 投研辩论 -> Research Manager 综合研判 -> "
        "Trader A 股交易方案 -> Aggressive/Conservative/Neutral 三方风险辩论 -> "
        "Portfolio Manager 最终 Buy/Hold/Sell 与仓位。\n"
        "A 股交易方案必须显式考虑 T+1、涨跌停、最小 100 股手数、ST/停牌/流动性、公告原文复核。"
        "PEG 估值评级（derived_metrics.peg / peg_rating / digestion_years）必须显式参与正反方辩论与最终结论：\n"
        "- PEG < 1 视为成长性背书，可在仓位与买入力度上给予正面权重；\n"
        "- PEG > 1.5 视为估值偏贵，需要更高的成长或催化才能维持持有；\n"
        "- PEG 数据不足时，仓位与买入力度需要相应保守。\n"
        "决策可以给研究演示用 Buy/Hold/Sell、仓位、止损止盈，但不得承诺收益。\n"
        "只输出符合 CNTradingDecision 的 JSON 对象，不要 Markdown，不要解释。"
        "重要：直接返回平铺的 JSON 对象，不要再包一层 {\"base_decision\": {...}} 或 "
        "{\"decision\": {...}} 这样的外壳。\n"
        "必须保留 8 个 analyst_reports（含 PEG Analyst）；investment_recommendation 使用 买入/持有/回避；"
        "simulated_action 使用 建议买入/建议持有/建议暂不买入 之一。"
        f"{json.dumps(payload, ensure_ascii=False, default=str)}"
    )


def _build_compact_portfolio_prompt(
    base_decision: CNTradingDecision,
    neutral_report: CNNeutralStockReport,
    context: dict[str, Any],
    metrics: dict[str, Any],
    config: dict[str, Any],
) -> str:
    payload = {
        "symbol": base_decision.symbol,
        "analyst_reports": [_compact_agent(item) for item in base_decision.analyst_reports],
        "base_debate": {
            "bull_case": _compact_agent(base_decision.investment_debate.bull_case),
            "bear_case": _compact_agent(base_decision.investment_debate.bear_case),
            "manager_synthesis": base_decision.investment_debate.manager_synthesis,
        },
        "base_risk_debate": {
            "aggressive_view": _compact_agent(base_decision.risk_debate.aggressive_view),
            "neutral_view": _compact_agent(base_decision.risk_debate.neutral_view),
            "conservative_view": _compact_agent(base_decision.risk_debate.conservative_view),
        },
        "base_trade_plan": {
            "investment_recommendation": base_decision.investment_recommendation,
            "trade_decision": base_decision.trade_decision,
            "simulated_action": base_decision.simulated_action,
            "target_price_range": base_decision.target_price_range,
            "position_suggestion": base_decision.position_suggestion,
            "stop_loss": base_decision.stop_loss,
            "take_profit": base_decision.take_profit,
        },
        "neutral_report_digest": {
            "market_data_summary": neutral_report.market_data_summary,
            "financial_summary": neutral_report.financial_summary,
            "technical_indicator_explanation": neutral_report.technical_indicator_explanation,
            "risk_factors": neutral_report.risk_factors[:3],
            "data_limitations": neutral_report.data_limitations[:3],
            "neutral_summary": neutral_report.neutral_summary,
        },
        "derived_metrics": _compact_metrics(metrics),
        "market_context": {
            "profile": context.get("profile", {}),
            "capital_flow": context.get("capital_flow") or {},
            "lockup_events": (context.get("lockup_events") or [])[:3],
            "data_provider": context.get("data_provider"),
        },
        "max_debate_rounds": config.get("max_debate_rounds", 1),
        "max_risk_discuss_rounds": config.get("max_risk_discuss_rounds", 1),
    }
    return (
        "你是 TradingAgents-astock 风格的 A 股 Research Manager 与 Portfolio Manager。"
        "网关对长上下文敏感，所以这里只给压缩摘要。请基于输入优化交易决策补丁。\n"
        "只输出 JSON 对象，不要 Markdown。JSON 字段只能包含这些可选字段: "
        "investment_debate, trader_plan, risk_debate, investment_recommendation, trade_decision, "
        "simulated_action, confidence, horizon, target_price_range, position_suggestion, stop_loss, "
        "take_profit, key_drivers, risk_controls, decision_basis。\n"
        "investment_debate 若输出，必须包含 bull_case, bear_case, manager_synthesis；"
        "risk_debate 若输出，必须包含 aggressive_view, neutral_view, conservative_view, portfolio_manager_decision。"
        "所有 agent view 使用 agent, stance, summary, evidence。"
        "A 股交易方案必须考虑 T+1、涨跌停、100 股手数、流动性、公告原文复核；不得承诺收益。"
        "investment_recommendation 使用 买入/持有/观察/回避；"
        "simulated_action 使用 建议买入/建议持有/建议暂不买入 之一。"
        f"{json.dumps(payload, ensure_ascii=False, default=str)}"
    )


def _compact_agent(agent: CNAgentView) -> dict[str, Any]:
    return {
        "agent": agent.agent,
        "stance": agent.stance,
        "summary": agent.summary[:180],
        "evidence": agent.evidence[:3],
    }


def _compact_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "change_pct",
        "last_close",
        "ma5",
        "ma20",
        "positive_cashflow",
        "has_growth",
        "disclosure_count",
        "news_count",
        "policy_count",
        "lockup_count",
        "main_force_wan",
        "northbound_total_yi",
        "concept_count",
        "valuation_flags",
        "peg",
        "peg_rating",
        "peg_zone",
        "forward_pe",
        "cagr_pct",
        "digestion_years",
        "score",
    )
    return {key: metrics.get(key) for key in keys}


def _compact_context(context: dict[str, Any]) -> dict[str, Any]:
    return {
        "profile": context.get("profile", {}),
        "financials": context.get("financials", {}),
        "announcements": (context.get("announcements") or [])[:8],
        "news": (context.get("news") or [])[:8],
        "policy_events": (context.get("policy_events") or [])[:8],
        "capital_flow": context.get("capital_flow") or {},
        "lockup_events": (context.get("lockup_events") or [])[:8],
        "peg": _compact_peg(context.get("peg") or {}),
        "daily_bars": _edge_items(context.get("daily_bars") or [], 5),
        "depth": context.get("depth"),
        "data_provider": context.get("data_provider"),
    }


def _compact_peg(peg: dict[str, Any]) -> dict[str, Any]:
    if not peg:
        return {}
    return {
        "peg": peg.get("peg"),
        "rating": peg.get("rating"),
        "rating_zone": peg.get("rating_zone"),
        "forward_pe": peg.get("forward_pe"),
        "cagr_pct": peg.get("cagr_pct"),
        "cagr_years": peg.get("cagr_years"),
        "consensus_eps": peg.get("consensus_eps"),
        "consensus_eps_year": peg.get("consensus_eps_year"),
        "digestion_years": peg.get("digestion_years"),
        "digestion_label": peg.get("digestion_label"),
        "net_profit_history": (peg.get("net_profit_history") or [])[-4:],
        "peers": (peg.get("peers") or [])[:5],
        "notes": (peg.get("notes") or [])[:5],
    }


def _edge_items(items: list[Any], count: int) -> list[Any]:
    if len(items) <= count * 2:
        return items
    return [*items[:count], *items[-count:]]


def _dump_model(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if isinstance(value, list):
        return [_dump_model(item) for item in value]
    if isinstance(value, dict):
        return {key: _dump_model(item) for key, item in value.items()}
    return value


def _parse_json_object(content: str) -> dict[str, Any]:
    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("LLM response does not contain a JSON object")
    raw = text[start : end + 1]
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        from json_repair import repair_json

        repaired = repair_json(raw)
        parsed = json.loads(repaired)
    return _unwrap_decision(parsed)


_DECISION_REQUIRED_KEYS = {"symbol", "generated_at"}


def _unwrap_decision(parsed: Any) -> dict[str, Any]:
    """Strip wrappers like {"base_decision": {...}} or {"decision": {...}} that
    some models leak from the prompt structure."""
    if not isinstance(parsed, dict):
        raise ValueError("LLM response is not a JSON object")
    if _DECISION_REQUIRED_KEYS.issubset(parsed.keys()):
        return parsed
    for key in ("base_decision", "decision", "data", "result"):
        inner = parsed.get(key)
        if isinstance(inner, dict) and _DECISION_REQUIRED_KEYS.issubset(inner.keys()):
            return inner
    return parsed


def _preserve_decision_invariants(
    base_decision: CNTradingDecision,
    candidate: CNTradingDecision,
) -> CNTradingDecision:
    return candidate.model_copy(
        update={
            "symbol": base_decision.symbol,
            "generated_at": base_decision.generated_at,
        }
    )


def _decision_from_llm_payload(base_decision: CNTradingDecision, payload: dict[str, Any]) -> CNTradingDecision:
    payload = _normalize_decision_payload(payload)
    if {"symbol", "generated_at", "analyst_reports"}.issubset(payload):
        return CNTradingDecision.model_validate(payload)

    merged = base_decision.model_dump()
    patch_fields = {
        "investment_debate",
        "trader_plan",
        "risk_debate",
        "investment_recommendation",
        "trade_decision",
        "simulated_action",
        "confidence",
        "horizon",
        "target_price_range",
        "position_suggestion",
        "stop_loss",
        "take_profit",
        "key_drivers",
        "risk_controls",
        "decision_basis",
        "disclaimer",
    }
    for key in patch_fields:
        value = payload.get(key)
        if value not in (None, "", [], {}):
            merged[key] = value
    return CNTradingDecision.model_validate(merged)


def _normalize_decision_payload(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(payload)
    for key in (
        "trader_plan",
        "investment_recommendation",
        "trade_decision",
        "simulated_action",
        "confidence",
        "horizon",
        "target_price_range",
        "position_suggestion",
        "stop_loss",
        "take_profit",
        "disclaimer",
    ):
        if key in normalized:
            normalized[key] = _stringify_llm_value(normalized[key])

    debate = normalized.get("investment_debate")
    if isinstance(debate, dict) and "manager_synthesis" in debate:
        debate = dict(debate)
        debate["manager_synthesis"] = _stringify_llm_value(debate["manager_synthesis"])
        normalized["investment_debate"] = debate

    risk = normalized.get("risk_debate")
    if isinstance(risk, dict) and "portfolio_manager_decision" in risk:
        risk = dict(risk)
        risk["portfolio_manager_decision"] = _stringify_llm_value(risk["portfolio_manager_decision"])
        normalized["risk_debate"] = risk

    return normalized


def _stringify_llm_value(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, dict):
        parts = []
        for key, item in value.items():
            if item in (None, "", [], {}):
                continue
            parts.append(f"{key}: {_stringify_llm_value(item)}")
        return "；".join(parts)
    if isinstance(value, list):
        return "；".join(_stringify_llm_value(item) for item in value if item not in (None, "", [], {}))
    return str(value)


def _limit_for_depth(depth: str) -> int:
    return {"quick": 5, "standard": 15, "deep": 30}.get(depth, 5)


def _derive_metrics(
    bars: list[dict],
    financials: dict,
    announcements: list[dict],
    news: list[dict],
    policy_events: list[dict],
    capital_flow: dict,
    lockup_events: list[dict],
    peg: dict | None = None,
) -> dict:
    closes = [float(bar.get("close") or 0) for bar in bars if bar.get("close")]
    first = closes[0] if closes else 0
    last = closes[-1] if closes else 0
    change_pct = ((last - first) / first * 100) if first else 0
    ma5 = sum(closes[-5:]) / min(5, len(closes)) if closes else 0
    ma20 = sum(closes[-20:]) / min(20, len(closes)) if closes else 0
    positive_cashflow = financials.get("operating_cash_flow") == "保持为正"
    has_growth = _contains_positive_growth(financials.get("revenue_yoy")) or _contains_positive_growth(
        financials.get("net_profit_yoy")
    )
    disclosure_count = len(announcements)
    news_count = len(news)
    policy_count = len(policy_events)
    lockup_count = len(lockup_events)
    main_force_wan = float(capital_flow.get("main_force_wan") or 0)
    northbound_total_yi = float(capital_flow.get("northbound_total_yi") or 0)
    concept_blocks = capital_flow.get("concept_blocks") or []
    dragon_tiger_records = capital_flow.get("dragon_tiger_records") or []
    valuation_flags = sum(
        1
        for key in ("pe_ttm", "pb", "mcap_yi")
        if financials.get(key) not in (None, "", "未获取", 0)
    )
    peg_value = (peg or {}).get("peg")
    peg_zone = (peg or {}).get("rating_zone")
    score = 0
    score += 1 if change_pct > 2 else -1 if change_pct < -2 else 0
    score += 1 if ma5 and ma20 and ma5 >= ma20 else -1 if ma5 and ma20 else 0
    score += 1 if positive_cashflow else -1
    score += 1 if has_growth else 0
    score += 1 if disclosure_count or news_count else -1
    score += 1 if valuation_flags >= 2 else 0
    score += 1 if policy_count else 0
    score += 1 if main_force_wan > 0 else -1 if main_force_wan < 0 else 0
    score += 1 if northbound_total_yi > 0 else -1 if northbound_total_yi < 0 else 0
    score -= 1 if lockup_count else 0
    if peg_zone == "deep-value":
        score += 2
    elif peg_zone == "undervalued":
        score += 1
    elif peg_zone == "rich":
        score -= 1
    elif peg_zone == "overvalued":
        score -= 2
    return {
        "change_pct": change_pct,
        "last_close": last,
        "ma5": ma5,
        "ma20": ma20,
        "positive_cashflow": positive_cashflow,
        "has_growth": has_growth,
        "disclosure_count": disclosure_count,
        "news_count": news_count,
        "policy_count": policy_count,
        "lockup_count": lockup_count,
        "main_force_wan": main_force_wan,
        "northbound_total_yi": northbound_total_yi,
        "concept_blocks": concept_blocks,
        "concept_count": len(concept_blocks),
        "industry_block": capital_flow.get("industry_block"),
        "dragon_tiger_count": len(dragon_tiger_records),
        "valuation_flags": valuation_flags,
        "peg": peg_value,
        "peg_rating": (peg or {}).get("rating"),
        "peg_zone": peg_zone,
        "forward_pe": (peg or {}).get("forward_pe"),
        "cagr_pct": (peg or {}).get("cagr_pct"),
        "digestion_years": (peg or {}).get("digestion_years"),
        "score": score,
    }


def _contains_positive_growth(value) -> bool:
    if value is None:
        return False
    text = str(value)
    return "%" in text and not text.strip().startswith("-")


def _analyst_views(report: CNNeutralStockReport, metrics: dict) -> list[CNAgentView]:
    trend = "价格序列偏强" if metrics["change_pct"] > 2 else "价格序列偏弱" if metrics["change_pct"] < -2 else "价格序列平稳"
    cashflow = "现金流描述较稳" if metrics["positive_cashflow"] else "现金流信息不足或需要核对"
    disclosure = "公告/新闻覆盖可用于交叉验证" if metrics["disclosure_count"] or metrics["news_count"] else "事件数据覆盖不足"
    policy = "存在政策/监管公开信息线索" if metrics["policy_count"] else "政策事件覆盖不足"
    hot_money = (
        "资金面偏流入"
        if metrics["main_force_wan"] > 0 or metrics["northbound_total_yi"] > 0
        else "资金面偏流出"
        if metrics["main_force_wan"] < 0 or metrics["northbound_total_yi"] < 0
        else "资金面未见明确方向"
    )
    lockup = "存在解禁/减持供给线索" if metrics["lockup_count"] else "未发现近期解禁压力线索"
    return [
        CNAgentView(
            agent="Market Analyst",
            stance=trend,
            summary=report.market_data_summary,
            evidence=[f"区间收盘变化约 {metrics['change_pct']:.2f}%", report.technical_indicator_explanation],
        ),
        CNAgentView(
            agent="Fundamentals Analyst",
            stance=cashflow,
            summary=report.financial_summary,
            evidence=[f"可用估值字段数量: {metrics['valuation_flags']}", f"收入/利润增长线索: {metrics['has_growth']}"],
        ),
        CNAgentView(
            agent="News Analyst",
            stance=disclosure,
            summary=report.announcement_summary,
            evidence=[report.news_summary, f"公告 {metrics['disclosure_count']} 条，新闻 {metrics['news_count']} 条"],
        ),
        CNAgentView(
            agent="Sentiment Analyst",
            stance="公开情绪需保守解读",
            summary="A 股适配版当前以公告和公开新闻替代 StockTwits/Reddit 社交情绪。",
            evidence=["中文社交平台尚未接入稳定授权数据源", "情绪结论不参与单独交易触发"],
        ),
        CNAgentView(
            agent="Policy Analyst",
            stance=policy,
            summary="A 股政策市特征明显，政策、监管和产业方向会影响板块估值与交易情绪。",
            evidence=[f"政策事件数量: {metrics['policy_count']}", f"行业/板块: {metrics.get('industry_block') or '未获取'}"],
        ),
        CNAgentView(
            agent="Hot Money Tracker",
            stance=hot_money,
            summary="资金视角关注北向资金、主力资金、龙虎榜和概念板块热度，不单独构成交易触发。",
            evidence=[
                f"主力资金: {metrics['main_force_wan']:.2f} 万元",
                f"北向合计: {metrics['northbound_total_yi']:.2f} 亿元",
                f"龙虎榜记录: {metrics['dragon_tiger_count']}",
                f"概念数量: {metrics['concept_count']}",
            ],
        ),
        CNAgentView(
            agent="Lockup Watcher",
            stance=lockup,
            summary="解禁与减持属于 A 股供给端压力因素，需要结合公告原文、持有人类型和减持新规复核。",
            evidence=[f"解禁/减持事件数量: {metrics['lockup_count']}"],
        ),
        _peg_analyst_view(metrics),
    ]


def _peg_analyst_view(metrics: dict) -> CNAgentView:
    peg = metrics.get("peg")
    rating = metrics.get("peg_rating")
    forward_pe = metrics.get("forward_pe")
    cagr_pct = metrics.get("cagr_pct")
    digestion = metrics.get("digestion_years")
    if peg is None or rating is None:
        stance = "PEG 数据不足，暂不计入估值打分"
        evidence = [
            "缺少一致预期 EPS 或近 3 年净利润数据" if forward_pe is None or cagr_pct is None else "净利润 CAGR 非正，PEG 公式不适用",
            "PEG 在亏损股、强周期股或银行/保险板块上不适用",
        ]
    else:
        stance = f"PEG ≈ {peg:.2f}（{rating}）"
        evidence = [
            f"前瞻 PE: {forward_pe:.2f}×" if forward_pe else "前瞻 PE: 数据缺失",
            f"净利润 CAGR: {cagr_pct:.2f}%" if cagr_pct is not None else "CAGR: 数据缺失",
            f"消化到 30× 锚的年限: {digestion:.2f} 年" if digestion is not None else "消化年限: 数据缺失",
        ]
    return CNAgentView(
        agent="PEG Analyst",
        stance=stance,
        summary=(
            "彼得·林奇 PEG 视角把估值与盈利成长性绑定：PEG = 前瞻PE / 增速%。"
            "PEG < 1 视为低估，1.0–1.5 合理，> 1.5 偏贵，> 2.0 高估。"
            "估值评级与消化年限作为多智能体辩论的成长性背书。"
        ),
        evidence=evidence,
    )


def _investment_debate(metrics: dict, analysts: list[CNAgentView]) -> CNInvestmentDebate:
    bull_points = [item.stance for item in analysts if "不足" not in item.stance and "偏弱" not in item.stance]
    bear_points = [item.stance for item in analysts if "不足" in item.stance or "偏弱" in item.stance]
    return CNInvestmentDebate(
        bull_case=CNAgentView(
            agent="Bull Researcher",
            stance="支持纳入观察池" if metrics["score"] >= 2 else "仅支持继续跟踪",
            summary="正方关注价格韧性、经营增长线索、现金流和公告披露是否形成一致证据。",
            evidence=bull_points or ["暂无足够强的正向共振证据"],
        ),
        bear_case=CNAgentView(
            agent="Bear Researcher",
            stance="强调数据缺口和回撤风险",
            summary="反方关注样本长度、估值字段缺失、新闻覆盖不足以及外部接口降级带来的判断偏差。",
            evidence=bear_points or ["即使信号偏正，也需要等待更多正式披露确认"],
        ),
        manager_synthesis=(
            "研究经理结论：当前证据得分为 "
            f"{metrics['score']}，进入交易员环节生成分层投资建议和交易决策。"
        ),
    )


def _trader_plan(metrics: dict, debate: CNInvestmentDebate) -> str:
    if metrics["score"] >= 4:
        posture = "交易员建议逢回调分批买入，优先等待价格靠近短期均线或公告确认后的低吸机会。"
    elif metrics["score"] >= 2:
        posture = "交易员建议持有/观察，若后续公开信息继续改善再提高买入力度。"
    else:
        posture = "交易员建议暂不买入，先补齐行情、财务和事件数据后再重新评估。"
    return f"{posture}依据：{debate.manager_synthesis}"


def _risk_debate(metrics: dict, report: CNNeutralStockReport) -> CNRiskDebate:
    return CNRiskDebate(
        aggressive_view=CNAgentView(
            agent="Aggressive Risk Analyst",
            stance="建议进攻型建仓" if metrics["score"] >= 4 else "不建议提高风险暴露",
            summary="激进视角强调趋势和增长线索，证据共振较强时允许提高买入力度。",
            evidence=[f"综合得分: {metrics['score']}", f"MA5/MA20: {metrics['ma5']:.2f}/{metrics['ma20']:.2f}"],
        ),
        neutral_view=CNAgentView(
            agent="Neutral Risk Analyst",
            stance="以观察和复核为主",
            summary="中性视角要求把数据限制、公告原文和行业变化作为复核条件。",
            evidence=report.data_limitations[:3],
        ),
        conservative_view=CNAgentView(
            agent="Conservative Risk Analyst",
            stance="优先控制误判成本",
            summary="保守视角不把短期价格变化作为充分依据，要求在信息不足时降低仓位或暂缓交易。",
            evidence=report.risk_factors[:3],
        ),
        portfolio_manager_decision="组合经理根据多方辩论结果输出投资建议、交易动作、仓位和止盈止损参考。",
    )


def _portfolio_action(metrics: dict) -> tuple[str, str]:
    if metrics["score"] >= 4:
        return "建议买入", "medium"
    if metrics["score"] >= 2:
        return "建议持有/观察", "low-medium"
    return "建议暂不买入", "low"


def _investment_recommendation(metrics: dict) -> str:
    if metrics["score"] >= 4:
        return "买入"
    if metrics["score"] >= 2:
        return "持有/观察"
    return "回避/等待"


def _trade_decision(metrics: dict) -> str:
    if metrics["score"] >= 4:
        return "分批建仓"
    if metrics["score"] >= 2:
        return "保持观察，突破关键均线后再行动"
    return "暂缓交易"


def _price_plan(metrics: dict) -> tuple[str | None, str | None, str | None]:
    close = metrics.get("last_close") or 0
    if close <= 0:
        return None, None, None
    if metrics["score"] >= 4:
        return (
            f"{close * 1.06:.2f}-{close * 1.12:.2f}",
            f"{close * 0.94:.2f}",
            f"{close * 1.10:.2f}",
        )
    if metrics["score"] >= 2:
        return (
            f"{close * 1.03:.2f}-{close * 1.08:.2f}",
            f"{close * 0.95:.2f}",
            f"{close * 1.06:.2f}",
        )
    return (
        f"{close * 0.96:.2f}-{close * 1.04:.2f}",
        f"{close * 0.93:.2f}",
        f"{close * 1.04:.2f}",
    )


def _position_suggestion(metrics: dict) -> str:
    if metrics["score"] >= 4:
        return "20%-30% 试探仓，分 2-3 次执行"
    if metrics["score"] >= 2:
        return "0%-10% 观察仓，等待确认信号"
    return "0%，暂不建仓"


def _key_drivers(profile: dict, metrics: dict) -> list[str]:
    drivers = [
        f"{profile.get('name') or profile.get('symbol') or '标的'} 所属板块: {profile.get('board', '未知')}",
        f"区间价格变化: {metrics['change_pct']:.2f}%",
        f"经营增长线索: {metrics['has_growth']}",
        f"公告/新闻覆盖: {metrics['disclosure_count']} / {metrics['news_count']}",
    ]
    if metrics.get("policy_count"):
        drivers.append(f"政策/监管相关公开信息: {metrics['policy_count']} 条")
    if metrics.get("concept_blocks"):
        drivers.append("相关概念板块: " + "、".join(metrics["concept_blocks"][:5]))
    if metrics.get("main_force_wan") or metrics.get("northbound_total_yi"):
        drivers.append(
            f"资金面: 主力 {metrics['main_force_wan']:.2f} 万元，北向合计 {metrics['northbound_total_yi']:.2f} 亿元"
        )
    if metrics.get("lockup_count"):
        drivers.append(f"限售解禁/减持相关线索: {metrics['lockup_count']} 条")
    if metrics.get("peg") is not None and metrics.get("peg_rating"):
        drivers.append(
            f"PEG 估值: {metrics['peg']:.2f}（{metrics['peg_rating']}）"
            f"，前瞻 PE {metrics.get('forward_pe') or 0:.2f}× / CAGR {metrics.get('cagr_pct') or 0:.2f}%"
        )
    return drivers


def _risk_controls(metrics: dict) -> list[str]:
    controls = [
        "必须回看公告原文和财报附注，不能只依赖摘要字段。",
        "外部数据源降级或字段缺失时，模拟动作自动降低一个强度。",
        "若价格跌破止损参考或公告出现重大不利变化，交易决策需要重新评估。",
    ]
    if metrics["score"] < 2:
        controls.append("综合证据偏弱，优先补齐行情、财务和事件数据。")
    if metrics.get("lockup_count"):
        controls.append("存在解禁/减持线索时，需要核对解禁规模、持有人类型和减持预披露。")
    if metrics.get("main_force_wan", 0) < 0:
        controls.append("主力资金净流出时，降低短线仓位假设并等待量价重新确认。")
    if metrics.get("peg_zone") in ("rich", "overvalued"):
        controls.append("PEG 已偏贵或高估，需要更明确的成长加速催化才能维持持有，并降低单笔买入力度。")
    elif metrics.get("peg") is None:
        controls.append("PEG 数据不足（缺少一致预期 EPS 或 CAGR 非正），仓位假设需相应保守。")
    return controls


def _collect_peg_payload(
    data_provider: ChinaMarketDataProvider,
    symbol: CNSymbol,
    financials: dict[str, Any],
) -> dict[str, Any]:
    if symbol.asset_type != "stock":
        return {"notes": ["PEG 估值仅适用于个股，已跳过 ETF / 指数。"]}
    try:
        collector = PEGDataCollector(data_provider)
        raw = collector.collect(symbol)
    except Exception as exc:
        logger.warning("PEG data collection failed: %s", exc)
        return {"notes": [f"PEG 数据采集失败：{exc}"]}

    consensus = raw.get("consensus_eps") or {}
    quote = raw.get("quote") or {}
    price = quote.get("price") or financials.get("price")
    pe_ttm = quote.get("pe_ttm") or financials.get("pe_ttm")
    pe_static = quote.get("pe_static") or financials.get("pe_static")

    computation = compute_peg(
        price=price,
        pe_ttm=pe_ttm,
        pe_static=pe_static,
        consensus_eps=consensus.get("primary_eps"),
        consensus_eps_year=consensus.get("primary_year"),
        net_profit_history=raw.get("net_profit_history") or [],
    )
    payload = computation.to_dict()
    payload["consensus_eps_by_year"] = (consensus or {}).get("by_year") or {}
    payload["peers"] = raw.get("peers") or []
    payload["notes"] = list(dict.fromkeys((payload.get("notes") or []) + (raw.get("notes") or [])))
    payload["data_provider"] = raw.get("data_provider")
    return payload
