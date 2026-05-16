"""Service entry point for China-market neutral analysis."""

from __future__ import annotations

from datetime import date
import logging
import os

from tradingagents.cn.a_stock_data_provider import AStockDataProvider
from tradingagents.cn.graph.cn_analysis_graph import CNAnalysisGraph
from tradingagents.cn.llm_report_writer import CNLLMReportWriter
from tradingagents.cn.market_data import ChinaMarketDataProvider
from tradingagents.cn.mock_provider import MockChinaMarketDataProvider
from tradingagents.cn.multi_agent_decision import CNAstockLLMDecisionWriter, CNMultiAgentDecisionFlow
from tradingagents.cn.schema import CNNeutralStockReport, CNTradingDecision
from tradingagents.cn.symbol import normalize_cn_symbol
from tradingagents.default_config import DEFAULT_CONFIG


logger = logging.getLogger(__name__)

DEFAULT_CN_DATA_PROVIDER = "a_stock_data"
DEFAULT_CN_ENABLE_LLM = True
DEFAULT_CN_ENABLE_LLM_DECISION = True

SUPPORTED_MODEL_PRESETS: dict[str, dict[str, str]] = {
    "gpt-5.4": {
        "label": "GPT-5.4",
        "deep": "gpt-5.4",
        "quick": "gpt-5.4-mini",
        "provider": "openai",
    },
    "gpt-5.5": {
        "label": "GPT-5.5",
        "deep": "gpt-5.5",
        "quick": "gpt-5.5",
        "provider": "openai",
    },
}
DEFAULT_MODEL_PRESET = "gpt-5.4"


def resolve_model_preset(name: str | None) -> dict[str, str]:
    if not name:
        return SUPPORTED_MODEL_PRESETS[DEFAULT_MODEL_PRESET]
    return SUPPORTED_MODEL_PRESETS.get(name.strip().lower(), SUPPORTED_MODEL_PRESETS[DEFAULT_MODEL_PRESET])


class CNAnalysisService:
    def __init__(
        self,
        data_provider: ChinaMarketDataProvider | None = None,
        llm_report_writer: CNLLMReportWriter | None = None,
        model_preset: str | None = None,
    ) -> None:
        self.data_provider = data_provider or _provider_from_env()
        self.model_preset = resolve_model_preset(model_preset)
        self.config_override = self._build_config_override()
        self.llm_report_writer = llm_report_writer or _llm_writer_from_env(self.config_override)
        self.graph = CNAnalysisGraph(
            self.data_provider,
            llm_report_writer=self.llm_report_writer,
        )
        self.decision_flow = CNMultiAgentDecisionFlow(
            self.data_provider,
            llm_decision_writer=_llm_decision_writer_from_env(self.config_override),
        )

    def _build_config_override(self) -> dict:
        config = DEFAULT_CONFIG.copy()
        preset = self.model_preset
        config["llm_provider"] = preset.get("provider") or config.get("llm_provider")
        config["deep_think_llm"] = preset["deep"]
        config["quick_think_llm"] = preset["quick"]
        return config

    def analyze(
        self,
        raw_symbol: str,
        analysis_date: str | None = None,
        depth: str = "quick",
    ) -> CNNeutralStockReport:
        self.data_provider.reset_run_state()
        symbol = normalize_cn_symbol(raw_symbol)
        run_date = analysis_date or date.today().isoformat()
        return self.graph.run(symbol=symbol, analysis_date=run_date, depth=depth)

    def analyze_with_decision(
        self,
        raw_symbol: str,
        analysis_date: str | None = None,
        depth: str = "quick",
    ) -> tuple[CNNeutralStockReport, CNTradingDecision]:
        self.data_provider.reset_run_state()
        symbol = normalize_cn_symbol(raw_symbol)
        run_date = analysis_date or date.today().isoformat()
        report = self.graph.run(symbol=symbol, analysis_date=run_date, depth=depth)
        decision = self.decision_flow.run(
            symbol=symbol,
            analysis_date=run_date,
            depth=depth,
            neutral_report=report,
        )
        return report, decision


def _provider_from_env() -> ChinaMarketDataProvider:
    provider = os.getenv("CN_DATA_PROVIDER", DEFAULT_CN_DATA_PROVIDER).strip().lower()
    if provider in {"a_stock_data", "a-stock-data", "live"}:
        return AStockDataProvider()
    return MockChinaMarketDataProvider()


def _llm_writer_from_env(config: dict | None = None) -> CNLLMReportWriter | None:
    if not _env_flag("CN_ENABLE_LLM", DEFAULT_CN_ENABLE_LLM):
        return None
    try:
        return CNLLMReportWriter(config=config)
    except Exception as exc:
        logger.warning("CN LLM report writer initialization failed; using deterministic report fallback: %s", exc)
        return None


def _llm_decision_writer_from_env(config: dict | None = None) -> CNAstockLLMDecisionWriter | None:
    if not _env_flag("CN_ENABLE_LLM_DECISION", _env_flag("CN_ENABLE_LLM", DEFAULT_CN_ENABLE_LLM_DECISION)):
        return None
    try:
        return CNAstockLLMDecisionWriter(config=config)
    except Exception as exc:
        logger.warning("CN LLM decision writer initialization failed; using deterministic decision fallback: %s", exc)
        return None


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def get_cn_runtime_status() -> dict:
    provider = os.getenv("CN_DATA_PROVIDER", DEFAULT_CN_DATA_PROVIDER).strip().lower() or DEFAULT_CN_DATA_PROVIDER
    llm_enabled = _env_flag("CN_ENABLE_LLM", DEFAULT_CN_ENABLE_LLM)
    llm_decision_enabled = _env_flag("CN_ENABLE_LLM_DECISION", llm_enabled)
    config = DEFAULT_CONFIG.copy()
    return {
        "data_provider": provider,
        "llm_enabled": llm_enabled,
        "llm_decision_enabled": llm_decision_enabled,
        "llm_provider": config.get("llm_provider"),
        "quick_model": config.get("quick_think_llm"),
        "deep_model": config.get("deep_think_llm"),
        "backend_url_configured": bool(config.get("backend_url")),
        "model_presets": [
            {"value": key, "label": preset["label"]}
            for key, preset in SUPPORTED_MODEL_PRESETS.items()
        ],
        "default_model_preset": DEFAULT_MODEL_PRESET,
    }
