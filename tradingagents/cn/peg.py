"""PEG (Price/Earnings to Growth) computation for A-share equities.

Implements Peter Lynch's PEG methodology localized for China A-shares:
- Forward PE = current price / consensus EPS
- CAGR = compound annual growth rate of net profit (last 3 fiscal years)
- PEG = Forward PE / (CAGR * 100)
- PE digestion years = ln(Forward PE / 30) / ln(1 + CAGR)
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any


REASONABLE_PE_ANCHOR = 30.0


@dataclass
class PEGComputation:
    price: float | None = None
    pe_ttm: float | None = None
    pe_static: float | None = None
    consensus_eps: float | None = None
    consensus_eps_year: str | None = None
    forward_pe: float | None = None
    net_profit_history: list[dict[str, Any]] = field(default_factory=list)
    cagr: float | None = None
    cagr_years: int | None = None
    peg: float | None = None
    rating: str | None = None
    rating_zone: str | None = None
    digestion_years: float | None = None
    digestion_label: str | None = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "price": _round(self.price, 4),
            "pe_ttm": _round(self.pe_ttm, 4),
            "pe_static": _round(self.pe_static, 4),
            "consensus_eps": _round(self.consensus_eps, 4),
            "consensus_eps_year": self.consensus_eps_year,
            "forward_pe": _round(self.forward_pe, 4),
            "net_profit_history": self.net_profit_history,
            "cagr": _round(self.cagr, 6),
            "cagr_pct": _round(self.cagr * 100, 4) if self.cagr is not None else None,
            "cagr_years": self.cagr_years,
            "peg": _round(self.peg, 4),
            "rating": self.rating,
            "rating_zone": self.rating_zone,
            "digestion_years": _round(self.digestion_years, 4),
            "digestion_label": self.digestion_label,
            "notes": self.notes,
        }


def compute_peg(
    price: float | None,
    pe_ttm: float | None,
    pe_static: float | None,
    consensus_eps: float | None,
    consensus_eps_year: str | None,
    net_profit_history: list[dict[str, Any]],
) -> PEGComputation:
    result = PEGComputation(
        price=price,
        pe_ttm=pe_ttm,
        pe_static=pe_static,
        consensus_eps=consensus_eps,
        consensus_eps_year=consensus_eps_year,
        net_profit_history=net_profit_history,
    )

    if consensus_eps and price and consensus_eps > 0 and price > 0:
        result.forward_pe = price / consensus_eps
    elif pe_ttm and pe_ttm > 0:
        result.forward_pe = pe_ttm
        result.notes.append("缺少机构一致预期 EPS，前瞻 PE 暂用 PE(TTM) 替代。")
    else:
        result.notes.append("无法计算前瞻 PE：缺少价格、一致预期 EPS 或 PE(TTM)。")

    cagr, years = _compute_cagr(net_profit_history)
    result.cagr = cagr
    result.cagr_years = years
    if cagr is None:
        result.notes.append("近 3 年净利润数据不全，无法计算 CAGR。")
    elif cagr <= 0:
        result.notes.append("净利润 CAGR 非正，PEG 在亏损或下滑情形下不适用。")

    if result.forward_pe and cagr is not None and cagr > 0:
        result.peg = result.forward_pe / (cagr * 100)
        result.rating, result.rating_zone = _rate_peg(result.peg)
    else:
        result.notes.append("PEG 计算条件不满足，已跳过 PEG 数值。")

    if result.forward_pe and cagr is not None and cagr > 0 and result.forward_pe > 0:
        try:
            result.digestion_years = math.log(result.forward_pe / REASONABLE_PE_ANCHOR) / math.log(1 + cagr)
        except ValueError:
            result.digestion_years = None
        if result.digestion_years is not None:
            result.digestion_label = _digestion_label(result.digestion_years)

    return result


def _compute_cagr(history: list[dict[str, Any]]) -> tuple[float | None, int | None]:
    """history rows expected to be sorted oldest -> newest with 'period' and 'net_profit'."""
    cleaned = [
        row for row in history
        if row.get("net_profit") is not None and row.get("period")
    ]
    if len(cleaned) < 2:
        return None, None

    cleaned.sort(key=lambda row: row["period"])
    cleaned = cleaned[-4:]
    start = float(cleaned[0]["net_profit"])
    end = float(cleaned[-1]["net_profit"])
    years = len(cleaned) - 1
    if start <= 0 or end <= 0 or years <= 0:
        return None, years
    cagr = (end / start) ** (1 / years) - 1
    return cagr, years


def _rate_peg(peg: float) -> tuple[str, str]:
    if peg < 0.5:
        return "极度低估", "deep-value"
    if peg < 1.0:
        return "低估", "undervalued"
    if peg < 1.5:
        return "合理", "fair"
    if peg < 2.0:
        return "偏贵", "rich"
    return "高估", "overvalued"


def _digestion_label(years: float) -> str:
    if years < 0:
        return "已低于合理 PE 锚"
    if years < 2:
        return "成长性强（<2 年）"
    if years < 4:
        return "正常（2-4 年）"
    return "需谨慎（>4 年）"


def _round(value: float | None, ndigits: int) -> float | None:
    if value is None:
        return None
    try:
        return round(float(value), ndigits)
    except (TypeError, ValueError):
        return None
