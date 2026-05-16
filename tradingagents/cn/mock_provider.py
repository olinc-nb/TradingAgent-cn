"""Deterministic mock data provider for the first China-market loop."""

from __future__ import annotations

from datetime import date, timedelta

from tradingagents.cn.market_data import ChinaMarketDataProvider
from tradingagents.cn.symbol import CNSymbol


class MockChinaMarketDataProvider(ChinaMarketDataProvider):
    provider_name = "mock"

    def get_security_profile(self, symbol: CNSymbol) -> dict:
        return {
            "symbol": symbol.standard,
            "name": symbol.display_name or "示例公司",
            "exchange": symbol.exchange,
            "board": symbol.board,
            "industry": "示例行业",
            "listing_date": "2001-08-27",
        }

    def get_daily_bars(self, symbol: CNSymbol, start_date: str, end_date: str) -> list[dict]:
        end = date.fromisoformat(end_date)
        bars = []
        for index in range(30):
            day = end - timedelta(days=29 - index)
            close = round(100 + index * 0.35, 2)
            bars.append(
                {
                    "date": day.isoformat(),
                    "open": round(close - 0.2, 2),
                    "high": round(close + 0.8, 2),
                    "low": round(close - 0.9, 2),
                    "close": close,
                    "volume": 1_000_000 + index * 12_000,
                }
            )
        return bars

    def get_financial_summary(self, symbol: CNSymbol) -> dict:
        return {
            "period": "最近四个季度",
            "revenue_yoy": "8.4%",
            "net_profit_yoy": "6.1%",
            "gross_margin": "42.3%",
            "operating_cash_flow": "保持为正",
            "debt_asset_ratio": "31.8%",
        }

    def get_announcements(self, symbol: CNSymbol, limit: int = 20) -> list[dict]:
        return [
            {
                "title": "年度权益分派实施公告",
                "published_at": "2026-04-28",
                "summary": "公司披露年度权益分派安排，涉及股权登记日和除权除息日。",
                "source": "交易所公告",
            },
            {
                "title": "一季度报告",
                "published_at": "2026-04-20",
                "summary": "公司披露一季度经营和财务数据，收入与利润保持同比增长。",
                "source": "交易所公告",
            },
        ][:limit]

    def get_news(self, symbol: CNSymbol, limit: int = 20) -> list[dict]:
        return [
            {
                "title": "行业需求延续结构性分化",
                "published_at": "2026-05-08",
                "summary": "公开报道显示，相关行业需求表现存在结构差异，企业经营情况需结合财报继续观察。",
                "source": "公开新闻",
            }
        ][:limit]

    def get_trade_calendar(self, start_date: str, end_date: str) -> list[dict]:
        start = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)
        days = []
        cursor = start
        while cursor <= end:
            days.append({"date": cursor.isoformat(), "is_open": cursor.weekday() < 5})
            cursor += timedelta(days=1)
        return days

    def get_policy_events(self, symbol: CNSymbol, analysis_date: str, limit: int = 10) -> list[dict]:
        return [
            {
                "title": "行业支持政策持续落地",
                "published_at": analysis_date,
                "summary": "公开政策信息显示，相关行业仍处于结构性支持和规范并行阶段。",
                "source": "政策公开信息",
                "impact": "中性偏正",
            }
        ][:limit]

    def get_capital_flow(self, symbol: CNSymbol, analysis_date: str) -> dict:
        return {
            "northbound_total_yi": 12.3,
            "main_force_wan": 8500.0,
            "concept_blocks": ["示例行业", "高股息"],
            "dragon_tiger": "近 30 日未上龙虎榜。",
            "source": "Mock 资金流",
        }

    def get_lockup_events(self, symbol: CNSymbol, analysis_date: str, forward_days: int = 90) -> list[dict]:
        return [
            {
                "unlock_date": "2026-06-15",
                "share_type": "股权激励限售",
                "amount": "少量",
                "ratio": "低于 1%",
                "source": "Mock 解禁日历",
            }
        ]

    def get_data_limitations(self) -> list[str]:
        return ["当前为 Mock 数据 Provider 输出，尚未接入正式授权数据源。"]
