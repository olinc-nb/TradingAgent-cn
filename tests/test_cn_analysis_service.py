from tradingagents.cn.services.analysis_service import CNAnalysisService


def test_cn_analysis_service_generates_neutral_report(monkeypatch):
    monkeypatch.setenv("CN_DATA_PROVIDER", "mock")

    report = CNAnalysisService().analyze("600519", analysis_date="2026-05-11")

    assert report.symbol == "600519.SH"
    assert report.name == "贵州茅台"
    assert report.risk_factors
    assert report.data_limitations
    assert "政策" in report.announcement_summary
    assert "资金面" in report.news_summary
    assert "板块归因" in report.technical_indicator_explanation
    assert any("限售解禁" in item.title for item in report.source_references)
    assert "投资建议" in report.disclaimer
    assert report.forbidden_advice_detected is False
