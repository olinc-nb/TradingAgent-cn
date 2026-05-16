"""Structured models for China-market neutral reports."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator


FORBIDDEN_REPORT_FIELDS = {
    "recommendation",
    "target_price",
    "action",
    "trade_decision",
    "position_size",
    "stop_loss",
    "take_profit",
    "expected_return",
    "upside_probability",
    "buy_signal",
    "sell_signal",
    "rating",
}


class SourceReference(BaseModel):
    source: str
    title: str
    url: str | None = None
    published_at: str | None = None


class CNNeutralStockReport(BaseModel):
    symbol: str
    name: str | None = None
    generated_at: str
    company_overview: str
    market_data_summary: str
    financial_summary: str
    announcement_summary: str
    news_summary: str
    technical_indicator_explanation: str
    risk_factors: list[str]
    data_limitations: list[str]
    neutral_summary: str
    source_references: list[SourceReference] = Field(default_factory=list)
    forbidden_advice_detected: bool = False
    compliance_notes: list[str] = Field(default_factory=list)
    disclaimer: str = (
        "本内容由 AI 基于公开信息整理生成，仅用于信息阅读和研究辅助，"
        "不构成任何投资建议、证券分析结论或交易依据。市场有风险，投资需独立判断。"
    )

    @model_validator(mode="before")
    @classmethod
    def reject_forbidden_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            forbidden = sorted(FORBIDDEN_REPORT_FIELDS.intersection(data))
            if forbidden:
                raise ValueError(f"报告包含禁止字段: {', '.join(forbidden)}")
        return data


class ComplianceReviewResult(BaseModel):
    approved: bool
    violations: list[str] = Field(default_factory=list)
    rewritten_text: str | None = None
    notes: list[str] = Field(default_factory=list)


class CNAgentView(BaseModel):
    agent: str
    stance: str
    summary: str
    evidence: list[str] = Field(default_factory=list)


class CNInvestmentDebate(BaseModel):
    bull_case: CNAgentView
    bear_case: CNAgentView
    manager_synthesis: str


class CNRiskDebate(BaseModel):
    aggressive_view: CNAgentView
    neutral_view: CNAgentView
    conservative_view: CNAgentView
    portfolio_manager_decision: str


class CNTradingDecision(BaseModel):
    symbol: str
    generated_at: str
    analyst_reports: list[CNAgentView]
    investment_debate: CNInvestmentDebate
    trader_plan: str
    risk_debate: CNRiskDebate
    investment_recommendation: str
    trade_decision: str
    simulated_action: str
    confidence: str
    horizon: str
    target_price_range: str | None = None
    position_suggestion: str | None = None
    stop_loss: str | None = None
    take_profit: str | None = None
    key_drivers: list[str]
    risk_controls: list[str]
    decision_basis: list[str]
    peg_metrics: dict[str, Any] | None = None
    disclaimer: str = (
        "本板块复用 TradingAgents 多智能体研究流程生成 A 股投资建议和交易决策输出，"
        "当前版本用于功能验证和研究演示。"
    )
