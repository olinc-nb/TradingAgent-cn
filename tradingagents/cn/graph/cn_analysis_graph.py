"""Synchronous China-market neutral analysis graph.

This first version keeps the flow deterministic so the China-market package can
be tested without external LLM or market-data credentials.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from tradingagents.cn.market_data import ChinaMarketDataProvider
from tradingagents.cn.schema import CNNeutralStockReport, SourceReference
from tradingagents.cn.llm_report_writer import CNLLMReportWriter
from tradingagents.cn.services.compliance_service import ComplianceService
from tradingagents.cn.symbol import CNSymbol


class CNAnalysisGraph:
    def __init__(
        self,
        data_provider: ChinaMarketDataProvider,
        compliance_service: ComplianceService | None = None,
        llm_report_writer: CNLLMReportWriter | None = None,
    ) -> None:
        self.data_provider = data_provider
        self.compliance_service = compliance_service or ComplianceService()
        self.llm_report_writer = llm_report_writer

    def run(self, symbol: CNSymbol, analysis_date: str, depth: str = "quick") -> CNNeutralStockReport:
        profile = self.data_provider.get_security_profile(symbol)
        bars = self.data_provider.get_daily_bars(symbol, analysis_date, analysis_date)
        financials = self.data_provider.get_financial_summary(symbol)
        announcements = self.data_provider.get_announcements(symbol, limit=_limit_for_depth(depth))
        news = self.data_provider.get_news(symbol, limit=_limit_for_depth(depth))
        policy_events = self.data_provider.get_policy_events(symbol, analysis_date, limit=_limit_for_depth(depth))
        capital_flow = self.data_provider.get_capital_flow(symbol, analysis_date)
        lockup_events = self.data_provider.get_lockup_events(symbol, analysis_date)

        report = CNNeutralStockReport(
            symbol=symbol.standard,
            name=profile.get("name"),
            generated_at=datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
            company_overview=_company_overview(profile),
            market_data_summary=_market_data_summary(bars),
            financial_summary=_financial_summary(financials),
            announcement_summary=_join_sections(
                _announcement_summary(announcements, symbol.asset_type),
                _policy_summary(policy_events),
            ),
            news_summary=_join_sections(
                _news_summary(news, symbol.asset_type),
                _capital_flow_summary(capital_flow),
            ),
            technical_indicator_explanation=_join_sections(
                _technical_explanation(bars),
                _concept_summary(capital_flow),
            ),
            risk_factors=_risk_factors(financials, announcements, news, lockup_events, capital_flow, symbol.asset_type),
            data_limitations=_data_limitations(
                symbol.asset_type,
                bars,
                announcements,
                news,
                self.data_provider.get_data_limitations(),
            ),
            neutral_summary=(
                "以上内容基于当前可用的公开数据整理，适合用于了解标的公开信息结构，"
                "不用于判断未来价格表现。"
            ),
            source_references=_source_references(announcements, news, policy_events, lockup_events),
        )
        if self.llm_report_writer is not None:
            report = self.llm_report_writer.rewrite_report(
                report,
                {
                    "profile": profile,
                    "daily_bars": bars,
                    "financials": financials,
                    "announcements": announcements,
                    "news": news,
                    "policy_events": policy_events,
                    "capital_flow": capital_flow,
                    "lockup_events": lockup_events,
                    "depth": depth,
                    "data_provider": getattr(self.data_provider, "provider_name", "unknown"),
                },
            )
        return report


def _limit_for_depth(depth: str) -> int:
    return {"quick": 5, "standard": 15, "deep": 30}.get(depth, 5)


def _company_overview(profile: dict) -> str:
    return (
        f"{profile.get('name', '该公司')}（{profile.get('symbol')}）属于{profile.get('industry', '未披露行业')}，"
        f"上市板块为{profile.get('board', '未披露板块')}，上市日期为{profile.get('listing_date', '未知')}。"
    )


def _market_data_summary(bars: list[dict]) -> str:
    if not bars:
        return "未获取到近期日线行情数据。"
    first = bars[0]
    last = bars[-1]
    change = (last["close"] - first["close"]) / first["close"] * 100
    return (
        f"数据区间内收盘价从 {first['close']} 变为 {last['close']}，区间变化约 {change:.2f}%。"
        f"最近一日成交量为 {last['volume']}，该数据仅用于描述历史交易活跃度。"
    )


def _financial_summary(financials: dict) -> str:
    if financials.get("asset_type") == "etf":
        parts = []
        if financials.get("price"):
            parts.append(f"最新价 {financials['price']}")
        if financials.get("change_pct") not in (None, "", 0):
            parts.append(f"涨跌幅 {financials['change_pct']}%")
        if financials.get("amount_wan"):
            parts.append(f"成交额约 {financials['amount_wan']} 万元")
        if financials.get("turnover_pct"):
            parts.append(f"换手率 {financials['turnover_pct']}%")
        if financials.get("mcap_yi"):
            parts.append(f"规模/市值口径约 {financials['mcap_yi']} 亿元")
        snapshot = "；".join(parts) if parts else "未获取到有效 ETF 行情快照"
        return (
            f"{financials.get('period', '实时快照')}显示：{snapshot}。"
            "ETF 不适用公司收入、净利润、毛利率等单体公司财务指标，需结合基金持仓、指数成分和基金公告阅读。"
        )

    valuation_parts = []
    if financials.get("pe_ttm"):
        valuation_parts.append(f"PE(TTM) 为 {financials['pe_ttm']}")
    if financials.get("pb"):
        valuation_parts.append(f"PB 为 {financials['pb']}")
    if financials.get("mcap_yi"):
        valuation_parts.append(f"总市值约 {financials['mcap_yi']} 亿元")
    valuation_text = "；".join(valuation_parts)
    if valuation_text:
        valuation_text = f"估值快照显示：{valuation_text}。"
    return (
        f"{financials.get('period', '指定期间')}收入同比变化为 {financials.get('revenue_yoy', '未知')}，"
        f"净利润同比变化为 {financials.get('net_profit_yoy', '未知')}，"
        f"毛利率为 {financials.get('gross_margin', '未知')}，"
        f"经营现金流情况为 {financials.get('operating_cash_flow', '未知')}。"
        f"{valuation_text}"
    )


def _announcement_summary(announcements: list[dict], asset_type: str = "stock") -> str:
    if not announcements:
        if asset_type == "etf":
            return "ETF 不适用上市公司公告口径，需结合基金定期报告、持仓、指数成分和基金管理人公告阅读。"
        return "未获取到近期公告。"
    titles = "；".join(item["title"] for item in announcements)
    return f"近期公告包括：{titles}。公告内容需结合原文披露口径阅读。"


def _news_summary(news: list[dict], asset_type: str = "stock") -> str:
    if not news:
        if asset_type == "etf":
            return "未获取到 ETF 相关新闻；ETF 事件解读应优先结合跟踪指数、成分行业和基金公告。"
        return "未获取到近期相关新闻。"
    return "近期公开新闻主要涉及：" + "；".join(item["summary"] for item in news)


def _policy_summary(policy_events: list[dict]) -> str:
    if not policy_events:
        return ""
    titles = "；".join(item.get("title", "未命名政策事件") for item in policy_events[:5])
    return f"政策/监管相关公开信息包括：{titles}。政策影响需映射到公司业务和行业景气度后再判断。"


def _capital_flow_summary(capital_flow: dict) -> str:
    if not capital_flow:
        return ""
    parts = []
    if capital_flow.get("northbound_total_yi") is not None:
        parts.append(f"北向资金当日合计约 {capital_flow['northbound_total_yi']} 亿元")
    if capital_flow.get("main_force_wan") is not None:
        parts.append(f"个股主力资金约 {capital_flow['main_force_wan']} 万元")
    if capital_flow.get("dragon_tiger_records"):
        parts.append(f"近 30 日龙虎榜记录 {len(capital_flow['dragon_tiger_records'])} 条")
    if not parts:
        return ""
    return "资金面公开数据：" + "；".join(parts) + "。资金流数据波动较大，仅作市场结构描述。"


def _concept_summary(capital_flow: dict) -> str:
    concepts = capital_flow.get("concept_blocks") or []
    industry = capital_flow.get("industry_block")
    if not concepts and not industry:
        return ""
    parts = []
    if industry:
        parts.append(f"所属行业/板块为 {industry}")
    if concepts:
        parts.append("相关概念包括 " + "、".join(concepts[:8]))
    return "板块归因：" + "；".join(parts) + "。"


def _technical_explanation(bars: list[dict]) -> str:
    if len(bars) < 20:
        return "行情数据长度不足，暂不解释均线等技术指标。"
    closes = [bar["close"] for bar in bars]
    ma5 = sum(closes[-5:]) / 5
    ma20 = sum(closes[-20:]) / 20
    relation = "高于" if ma5 > ma20 else "低于"
    return (
        f"按当前行情数据计算，5 日均线约为 {ma5:.2f}，20 日均线约为 {ma20:.2f}，"
        f"5 日均线{relation}20 日均线。均线关系只描述历史价格序列的相对状态。"
    )


def _risk_factors(
    financials: dict,
    announcements: list[dict],
    news: list[dict],
    lockup_events: list[dict],
    capital_flow: dict,
    asset_type: str = "stock",
) -> list[str]:
    if asset_type == "etf":
        risks = [
            "ETF 净值、折溢价、跟踪误差和成分股变化需要结合基金公告与指数资料复核。",
            "ETF 交易价格会受跟踪指数、市场流动性和申赎机制影响，不能按单体公司财务指标解读。",
        ]
    else:
        risks = [
            "当前数据未覆盖完整财务报表附注，部分经营细节需要阅读正式公告原文。",
            "行业需求变化可能影响公司经营表现，需结合后续公开披露继续观察。",
        ]
    if asset_type == "stock" and not announcements:
        risks.append("近期公告数据缺失，事件信息可能不完整。")
    if asset_type == "stock" and not news:
        risks.append("近期新闻数据缺失，外部事件覆盖可能不充分。")
    if asset_type == "stock" and financials.get("operating_cash_flow") != "保持为正":
        risks.append("经营现金流变化需要进一步核对。")
    if lockup_events:
        risks.append("未来或历史限售解禁/减持相关信息需要结合公告原文核对解禁规模和减持约束。")
    if capital_flow.get("main_force_wan", 0) < 0:
        risks.append("资金流数据显示主力资金为净流出，短期交易结构可能承压。")
    return risks


def _data_limitations(
    asset_type: str,
    bars: list[dict],
    announcements: list[dict],
    news: list[dict],
    provider_limitations: list[str],
) -> list[str]:
    limitations = list(provider_limitations)
    if len(bars) < 120:
        limitations.append("行情数据少于 120 个交易日，不能代表完整历史区间。")
    if asset_type == "stock" and not announcements:
        limitations.append("未获取到公告数据。")
    if asset_type == "stock" and not news:
        limitations.append("未获取到新闻数据。")
    return limitations


def _source_references(
    announcements: list[dict],
    news: list[dict],
    policy_events: list[dict],
    lockup_events: list[dict],
) -> list[SourceReference]:
    refs = []
    for item in announcements + news + policy_events:
        refs.append(
            SourceReference(
                source=item.get("source", "公开信息"),
                title=item.get("title", "未命名来源"),
                url=item.get("url"),
                published_at=item.get("published_at"),
            )
        )
    for item in lockup_events:
        refs.append(
            SourceReference(
                source=item.get("source", "公开信息"),
                title=f"限售解禁: {item.get('share_type', '未披露类型')}",
                published_at=item.get("unlock_date"),
            )
        )
    return refs


def _join_sections(*sections: str) -> str:
    return "".join(section for section in sections if section)
