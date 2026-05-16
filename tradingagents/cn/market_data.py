"""Market data provider contract for China-market analysis."""

from __future__ import annotations

from abc import ABC, abstractmethod

from tradingagents.cn.symbol import CNSymbol


class ChinaMarketDataProvider(ABC):
    provider_name = "unknown"

    @abstractmethod
    def get_security_profile(self, symbol: CNSymbol) -> dict:
        raise NotImplementedError

    @abstractmethod
    def get_daily_bars(self, symbol: CNSymbol, start_date: str, end_date: str) -> list[dict]:
        raise NotImplementedError

    @abstractmethod
    def get_financial_summary(self, symbol: CNSymbol) -> dict:
        raise NotImplementedError

    @abstractmethod
    def get_announcements(self, symbol: CNSymbol, limit: int = 20) -> list[dict]:
        raise NotImplementedError

    @abstractmethod
    def get_news(self, symbol: CNSymbol, limit: int = 20) -> list[dict]:
        raise NotImplementedError

    @abstractmethod
    def get_trade_calendar(self, start_date: str, end_date: str) -> list[dict]:
        raise NotImplementedError

    def get_policy_events(self, symbol: CNSymbol, analysis_date: str, limit: int = 10) -> list[dict]:
        return []

    def get_capital_flow(self, symbol: CNSymbol, analysis_date: str) -> dict:
        return {}

    def get_lockup_events(self, symbol: CNSymbol, analysis_date: str, forward_days: int = 90) -> list[dict]:
        return []

    def reset_run_state(self) -> None:
        return None

    def get_data_limitations(self) -> list[str]:
        return []
