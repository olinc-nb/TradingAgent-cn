import pytest

from tradingagents.cn.symbol import normalize_cn_symbol


@pytest.mark.parametrize(
    ("raw", "standard", "exchange", "board"),
    [
        ("600519", "600519.SH", "SSE", "上交所主板"),
        ("600519.SH", "600519.SH", "SSE", "上交所主板"),
        ("SH600519", "600519.SH", "SSE", "上交所主板"),
        ("000001", "000001.SZ", "SZSE", "深交所主板"),
        ("300750", "300750.SZ", "SZSE", "创业板"),
        ("688981", "688981.SH", "SSE", "科创板"),
        ("贵州茅台", "600519.SH", "SSE", "上交所主板"),
    ],
)
def test_normalize_cn_symbol(raw, standard, exchange, board):
    symbol = normalize_cn_symbol(raw)

    assert symbol.standard == standard
    assert symbol.exchange == exchange
    assert symbol.board == board


def test_reject_mismatched_suffix():
    with pytest.raises(ValueError):
        normalize_cn_symbol("600519.SZ")
