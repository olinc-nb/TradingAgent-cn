"""Compliance checks for neutral China-market reports."""

from __future__ import annotations

from tradingagents.cn.schema import CNNeutralStockReport, ComplianceReviewResult


FORBIDDEN_TERMS = (
    "买入",
    "卖出",
    "持有",
    "加仓",
    "减仓",
    "满仓",
    "清仓",
    "目标价",
    "止盈",
    "止损",
    "抄底",
    "逃顶",
    "稳赚",
    "必涨",
    "暴涨",
    "翻倍",
    "推荐",
    "荐股",
    "上涨概率",
    "短线机会",
    "交易信号",
    "买点",
    "卖点",
)

_REWRITE_MAP = {
    "建议买入": "可进一步阅读相关公开信息",
    "建议卖出": "需结合公开信息独立判断",
    "建议持有": "需结合公开信息独立判断",
    "建议关注": "后续可观察的信息包括",
    "目标价": "估值相关信息",
    "利好": "该事件披露了相关事实",
    "利空": "该事件提示了相关风险",
    "短线机会": "近期交易活跃度变化",
    "交易信号": "指标状态",
    "买点": "指标观察位置",
    "卖点": "指标观察位置",
}


class ComplianceService:
    def review_text(self, text: str) -> ComplianceReviewResult:
        violations = [term for term in FORBIDDEN_TERMS if term in text]
        rewritten = text
        for source, target in _REWRITE_MAP.items():
            rewritten = rewritten.replace(source, target)

        approved = not any(term in rewritten for term in FORBIDDEN_TERMS)
        return ComplianceReviewResult(
            approved=approved,
            violations=violations,
            rewritten_text=None if rewritten == text else rewritten,
            notes=[] if approved else ["仍存在需要人工复核的投资建议相关表达"],
        )

    def review_report(self, report: CNNeutralStockReport) -> CNNeutralStockReport:
        payload = report.model_dump()
        text_fields = [
            "company_overview",
            "market_data_summary",
            "financial_summary",
            "announcement_summary",
            "news_summary",
            "technical_indicator_explanation",
            "neutral_summary",
        ]

        notes: list[str] = []
        forbidden_detected = False
        for field in text_fields:
            result = self.review_text(payload[field])
            if result.violations:
                forbidden_detected = True
                notes.append(f"{field}: 命中 {', '.join(result.violations)}")
            if result.rewritten_text:
                payload[field] = result.rewritten_text

        clean_risks = []
        for item in payload["risk_factors"]:
            result = self.review_text(item)
            if result.violations:
                forbidden_detected = True
                notes.append(f"risk_factors: 命中 {', '.join(result.violations)}")
            clean_risks.append(result.rewritten_text or item)
        payload["risk_factors"] = clean_risks
        payload["forbidden_advice_detected"] = forbidden_detected
        payload["compliance_notes"] = notes
        return CNNeutralStockReport(**payload)
