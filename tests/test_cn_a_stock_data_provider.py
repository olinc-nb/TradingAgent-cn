from tradingagents.cn.a_stock_data_provider import AStockDataProvider, _tencent_prefix
from tradingagents.cn.services.analysis_service import CNAnalysisService


def test_a_stock_data_provider_can_drive_report(monkeypatch):
    provider = AStockDataProvider()

    monkeypatch.setattr(
        provider,
        "_tencent_quote",
        lambda code: {
            "name": "贵州茅台",
            "price": 1500.0,
            "last_close": 1490.0,
            "open": 1495.0,
            "high": 1510.0,
            "low": 1488.0,
            "volume": 120000,
            "pe_ttm": 25.5,
            "pb": 8.2,
            "mcap_yi": 18000.0,
            "float_mcap_yi": 18000.0,
        },
    )
    monkeypatch.setattr(provider, "_mootdx_daily_bars", lambda symbol: [])
    monkeypatch.setattr(provider, "_akshare_individual_info", lambda symbol: {})

    report = CNAnalysisService(data_provider=provider).analyze("600519", analysis_date="2026-05-11")

    assert report.symbol == "600519.SH"
    assert report.name == "贵州茅台"
    assert "PE(TTM) 为 25.5" in report.financial_summary
    assert any("mootdx" in item for item in report.data_limitations)
    assert not any("当前为 Mock 数据 Provider" in item for item in report.data_limitations)


def test_env_selects_a_stock_data_provider(monkeypatch):

    service = CNAnalysisService()

    assert isinstance(service.data_provider, AStockDataProvider)


def test_default_selects_a_stock_data_provider(monkeypatch):
    monkeypatch.delenv("CN_DATA_PROVIDER", raising=False)

    service = CNAnalysisService()

    assert isinstance(service.data_provider, AStockDataProvider)


def test_tencent_prefix_handles_sse_etf_codes():
    assert _tencent_prefix("588290") == "sh"
    assert _tencent_prefix("510300") == "sh"
    assert _tencent_prefix("159915") == "sz"


def test_a_stock_data_provider_handles_etf_without_stock_mock_events(monkeypatch):
    provider = AStockDataProvider()

    monkeypatch.setattr(
        provider,
        "_tencent_quote",
        lambda code: {
            "name": "科创芯片ETF华安",
            "price": 3.487,
            "last_close": 3.392,
            "open": 3.29,
            "high": 3.49,
            "low": 3.281,
            "volume": 1050005,
            "change_pct": 2.8,
            "amount_wan": 35506,
            "turnover_pct": 7.92,
            "mcap_yi": 46.25,
        },
    )
    monkeypatch.setattr(provider, "_mootdx_daily_bars", lambda symbol: [])

    report = CNAnalysisService(data_provider=provider).analyze("588290", analysis_date="2026-05-13")

    assert report.symbol == "588290.SH"
    assert report.name == "科创芯片ETF华安"
    assert "ETF 不适用公司收入" in report.financial_summary
    assert "年度权益分派实施公告" not in report.announcement_summary
    assert not any("未使用股票 Mock" in item for item in report.data_limitations)
    assert not any("未获取到公告数据" in item for item in report.data_limitations)
    assert not any("未获取到新闻数据" in item for item in report.data_limitations)


def test_a_stock_data_provider_limitations_do_not_leak_between_runs(monkeypatch):
    provider = AStockDataProvider()

    monkeypatch.setattr(provider, "_tencent_quote", lambda code: {})
    monkeypatch.setattr(provider, "_mootdx_daily_bars", lambda symbol: [])

    stock_report = CNAnalysisService(data_provider=provider).analyze("600519", analysis_date="2026-05-13")
    assert stock_report.data_limitations

    monkeypatch.setattr(
        provider,
        "_tencent_quote",
        lambda code: {
            "name": "科创芯片ETF华安",
            "price": 3.487,
            "last_close": 3.392,
            "open": 3.29,
            "high": 3.49,
            "low": 3.281,
            "volume": 1050005,
            "change_pct": 2.8,
            "amount_wan": 35506,
            "turnover_pct": 7.92,
            "mcap_yi": 46.25,
        },
    )

    etf_report = CNAnalysisService(data_provider=provider).analyze("588290", analysis_date="2026-05-13")

    assert not any("akshare 个股基本面" in item for item in etf_report.data_limitations)
    assert not any("股票 Mock" in item for item in etf_report.data_limitations)
