"""PEG data collection for A-share equities.

Pulls together inputs needed for Peter Lynch PEG analysis:
- live quote (price, PE TTM, PB, market cap) via Tencent
- consensus EPS via akshare's profit forecast endpoint
- historical net profit via akshare's financial abstract endpoint

External calls fail soft: any missing field becomes a `note` in the output and
the caller falls back to deterministic explanations.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from tradingagents.cn.a_stock_data_provider import (
    AStockDataProvider,
    _TTL_FINANCIAL,
    _TTL_PROFILE,
    _cached_call,
    _hash_args,
    _iter_records,
)
from tradingagents.cn.symbol import CNSymbol

logger = logging.getLogger(__name__)


class PEGDataCollector:
    def __init__(self, provider: AStockDataProvider | None = None) -> None:
        self.provider = provider or AStockDataProvider()
        self._notes: list[str] = []

    def collect(self, symbol: CNSymbol) -> dict[str, Any]:
        self._notes = []
        if symbol.asset_type != "stock":
            self._add_note("PEG 估值仅适用于个股，已跳过 ETF / 指数。")

        quote = self.provider._get_quote(symbol)  # noqa: SLF001 — internal helper reuse
        consensus = self._fetch_consensus_eps(symbol)
        net_profit_history = self._fetch_net_profit_history(symbol)
        peers = self._fetch_industry_peers(symbol)

        return {
            "symbol": symbol.standard,
            "code": symbol.code,
            "name": quote.get("name") or symbol.display_name or symbol.code,
            "quote": {
                "price": quote.get("price"),
                "pe_ttm": quote.get("pe_ttm"),
                "pe_static": quote.get("pe_static"),
                "pb": quote.get("pb"),
                "mcap_yi": quote.get("mcap_yi"),
                "float_mcap_yi": quote.get("float_mcap_yi"),
                "change_pct": quote.get("change_pct"),
            },
            "consensus_eps": consensus,
            "net_profit_history": net_profit_history,
            "peers": peers,
            "notes": list(self._notes),
            "data_provider": "tencent + akshare",
        }

    def _fetch_consensus_eps(self, symbol: CNSymbol) -> dict[str, Any]:
        try:
            import akshare as ak
        except Exception as exc:
            self._add_note(f"akshare 未安装或不可用，无法获取一致预期 EPS：{exc}")
            return {}

        try:
            df = _cached_call(
                "ak.stock_profit_forecast_ths.eps",
                _hash_args(symbol.code),
                _TTL_FINANCIAL,
                lambda: ak.stock_profit_forecast_ths(symbol=symbol.code, indicator="预测年报每股收益"),
            )
        except Exception as exc:
            self._add_note(f"一致预期 EPS 接口调用失败：{exc}")
            return {}

        rows = _iter_records(df)
        if not rows:
            self._add_note("一致预期 EPS 数据为空。")
            return {}

        bucket: dict[str, list[float]] = {}
        for row in rows:
            for key, value in row.items():
                year = _extract_year(str(key))
                if not year:
                    continue
                eps = _to_float_or_none(value)
                if eps is None:
                    continue
                bucket.setdefault(year, []).append(eps)

        averaged = {year: sum(values) / len(values) for year, values in bucket.items() if values}
        if not averaged:
            self._add_note("一致预期 EPS 数据未能解析有效年度。")
            return {}

        sorted_years = sorted(averaged.keys())
        primary_year = next((y for y in sorted_years if int(y) >= 2026), sorted_years[-1])
        return {
            "primary_year": primary_year,
            "primary_eps": round(averaged[primary_year], 4),
            "by_year": {year: round(value, 4) for year, value in averaged.items()},
        }

    def _fetch_net_profit_history(self, symbol: CNSymbol) -> list[dict[str, Any]]:
        try:
            import akshare as ak
        except Exception as exc:
            self._add_note(f"akshare 未安装，无法获取历史净利润：{exc}")
            return []

        try:
            df = _cached_call(
                "ak.stock_financial_abstract_ths.year",
                _hash_args(symbol.code),
                _TTL_FINANCIAL,
                lambda: ak.stock_financial_abstract_ths(symbol=symbol.code, indicator="按年度"),
            )
        except Exception:
            try:
                df = _cached_call(
                    "ak.stock_financial_abstract_ths.report",
                    _hash_args(symbol.code),
                    _TTL_FINANCIAL,
                    lambda: ak.stock_financial_abstract_ths(symbol=symbol.code, indicator="按报告期"),
                )
            except Exception as exc:
                self._add_note(f"历史净利润接口调用失败：{exc}")
                return []

        rows = _iter_records(df)
        if not rows:
            return []

        history: list[dict[str, Any]] = []
        for row in rows:
            period = _pick(row, "报告期", "报告日期", "日期", "年度")
            net_profit = _coerce_yi(_pick(row, "净利润", "归属母公司净利润", "归属于母公司股东的净利润"))
            revenue = _coerce_yi(_pick(row, "营业总收入", "营业收入"))
            roe = _coerce_pct(_pick(row, "净资产收益率", "ROE"))
            eps = _to_float_or_none(_pick(row, "每股收益", "基本每股收益"))
            if not period:
                continue
            history.append(
                {
                    "period": str(period)[:10],
                    "net_profit": net_profit,
                    "revenue": revenue,
                    "roe_pct": roe,
                    "eps": eps,
                }
            )

        history = [row for row in history if re.search(r"\d{4}", row["period"])]
        history.sort(key=lambda r: r["period"])
        annual = [row for row in history if "12-31" in row["period"] or row["period"].endswith("12")]
        if annual:
            history = annual
        return history[-6:]

    def _fetch_industry_peers(self, symbol: CNSymbol) -> list[dict[str, Any]]:
        try:
            import akshare as ak
        except Exception:
            return []

        try:
            info_df = _cached_call(
                "ak.stock_individual_info_em",
                _hash_args(symbol.code),
                _TTL_PROFILE,
                lambda: ak.stock_individual_info_em(symbol=symbol.code),
            )
        except Exception:
            return []

        industry = None
        for row in _iter_records(info_df):
            if str(row.get("item")) == "行业":
                industry = row.get("value")
                break
        if not industry:
            return []

        try:
            board_df = _cached_call(
                "ak.stock_board_industry_cons_em",
                _hash_args(industry),
                _TTL_PROFILE,
                lambda: ak.stock_board_industry_cons_em(symbol=str(industry)),
            )
        except Exception as exc:
            self._add_note(f"行业成分接口调用失败：{exc}")
            return []

        peers: list[dict[str, Any]] = []
        for row in _iter_records(board_df)[:20]:
            code = str(_pick(row, "代码", "股票代码") or "")
            if not code or code == symbol.code:
                continue
            peers.append(
                {
                    "code": code,
                    "name": _pick(row, "名称", "股票简称"),
                    "pe": _to_float_or_none(_pick(row, "市盈率-动态", "市盈率")),
                    "pb": _to_float_or_none(_pick(row, "市净率")),
                    "mcap_yi": _coerce_yi(_pick(row, "总市值")),
                }
            )
            if len(peers) >= 8:
                break
        return [
            {**peer, "industry": str(industry)}
            for peer in peers
        ]

    def _add_note(self, message: str) -> None:
        if message and message not in self._notes:
            self._notes.append(message)


def _pick(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in row and row[key] not in (None, ""):
            return row[key]
    return None


def _to_float_or_none(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        text = str(value).replace(",", "").strip()
        if not text or text in {"-", "--"}:
            return None
        try:
            return float(text)
        except ValueError:
            return None


def _coerce_yi(value: Any) -> float | None:
    """Net profit / revenue come either as raw yuan or as '亿' / '万' strings."""
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return float(value) / 1e8
    text = str(value).strip()
    if not text or text in {"-", "--"}:
        return None
    text = text.replace(",", "")
    try:
        if text.endswith("亿"):
            return float(text[:-1])
        if text.endswith("万"):
            return float(text[:-1]) / 10000
        if text.endswith("万亿"):
            return float(text[:-2]) * 10000
        return float(text) / 1e8
    except ValueError:
        return None


def _coerce_pct(value: Any) -> float | None:
    if value in (None, ""):
        return None
    text = str(value).strip().replace(",", "")
    if not text or text in {"-", "--"}:
        return None
    text = text.rstrip("%")
    try:
        return float(text)
    except ValueError:
        return None


def _extract_year(text: str) -> str | None:
    match = re.search(r"(20\d{2})", text)
    return match.group(1) if match else None
