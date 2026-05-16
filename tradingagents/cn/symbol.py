"""A-share symbol normalization."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field


class CNSymbol(BaseModel):
    raw: str
    code: str = Field(pattern=r"^\d{6}$")
    exchange: Literal["SSE", "SZSE", "BSE"]
    suffix: Literal["SH", "SZ", "BJ"]
    asset_type: Literal["stock", "etf", "convertible_bond", "index"] = "stock"
    board: str | None = None
    display_name: str | None = None

    @property
    def standard(self) -> str:
        return f"{self.code}.{self.suffix}"


_NAME_ALIASES = {
    "贵州茅台": "600519",
    "平安银行": "000001",
    "宁德时代": "300750",
    "中芯国际": "688981",
}


def normalize_cn_symbol(raw_symbol: str) -> CNSymbol:
    """Normalize common A-share user inputs into an internal symbol model."""
    if not raw_symbol or not raw_symbol.strip():
        raise ValueError("股票代码不能为空")

    raw = raw_symbol.strip().upper()
    code = _NAME_ALIASES.get(raw_symbol.strip(), raw)

    match = re.fullmatch(r"(?:(SH|SZ|BJ))?(\d{6})(?:\.(SH|SZ|BJ))?", code)
    if not match:
        raise ValueError(f"无法识别股票代码: {raw_symbol}")

    prefix_suffix, digits, dotted_suffix = match.groups()
    explicit_suffix = dotted_suffix or prefix_suffix
    inferred = _infer_market(digits)

    if explicit_suffix and explicit_suffix != inferred["suffix"]:
        raise ValueError(
            f"股票代码 {digits} 与交易所后缀 {explicit_suffix} 不匹配，预期 {inferred['suffix']}"
        )

    return CNSymbol(
        raw=raw_symbol,
        code=digits,
        exchange=inferred["exchange"],
        suffix=inferred["suffix"],
        asset_type=inferred["asset_type"],
        board=inferred["board"],
        display_name=_display_name_for_code(digits),
    )


def _infer_market(code: str) -> dict[str, str]:
    if code.startswith(("600", "601", "603", "605")):
        return _stock("SSE", "SH", "上交所主板")
    if code.startswith("688"):
        return _stock("SSE", "SH", "科创板")
    if code.startswith(("000", "001", "002", "003")):
        return _stock("SZSE", "SZ", "深交所主板")
    if code.startswith(("300", "301")):
        return _stock("SZSE", "SZ", "创业板")
    if code.startswith(("8", "4")):
        return _stock("BSE", "BJ", "北交所")
    if code.startswith(("510", "588")):
        return {"exchange": "SSE", "suffix": "SH", "asset_type": "etf", "board": "上交所 ETF"}
    if code.startswith("159"):
        return {"exchange": "SZSE", "suffix": "SZ", "asset_type": "etf", "board": "深交所 ETF"}
    raise ValueError(f"暂不支持的 A 股代码前缀: {code}")


def _stock(exchange: str, suffix: str, board: str) -> dict[str, str]:
    return {"exchange": exchange, "suffix": suffix, "asset_type": "stock", "board": board}


def _display_name_for_code(code: str) -> str | None:
    for name, alias_code in _NAME_ALIASES.items():
        if alias_code == code:
            return name
    return None
