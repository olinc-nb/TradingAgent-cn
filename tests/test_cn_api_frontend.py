from fastapi.testclient import TestClient

import api.routes.cn_analysis as cn_analysis
from api.main import app
from tradingagents.cn.services.analysis_service import CNAnalysisService


def test_cn_analyze_api_includes_decision_by_default(monkeypatch):
    monkeypatch.setenv("CN_DATA_PROVIDER", "mock")
    monkeypatch.setenv("CN_ENABLE_LLM_DECISION", "false")
    monkeypatch.setattr(cn_analysis, "service", CNAnalysisService())
    client = TestClient(app)

    response = client.post(
        "/cn/analyze",
        json={"symbol": "600519", "analysis_date": "2026-05-11", "depth": "quick"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["report"]["symbol"] == "600519.SH"
    assert payload["decision"]["symbol"] == "600519.SH"
    assert payload["decision"]["investment_recommendation"]
    assert payload["decision"]["target_price_range"]
    assert payload["decision"]["trader_plan"]


def test_index_serves_frontend():
    client = TestClient(app)

    response = client.get("/")

    assert response.status_code == 200
    assert "A 股多智能体交易台" in response.text
    assert "输入股票、ETF 或指数代码" in response.text
    assert "LLM 思考辩论过程" in response.text
    assert 'id="kline-chart"' in response.text
    assert 'id="trend-chart"' in response.text
    assert 'name="analysis_date"' not in response.text


def test_visualization_page_served():
    client = TestClient(app)

    response = client.get("/visualization")

    assert response.status_code == 200
    assert "真实 K 线" in response.text
    assert "LLM 思考辩论可视化" in response.text
    assert "/static/visualization.js" in response.text


def test_cn_visualization_api_returns_kline_trend_and_debate(monkeypatch):
    monkeypatch.setenv("CN_DATA_PROVIDER", "mock")
    monkeypatch.setenv("CN_ENABLE_LLM_DECISION", "false")
    monkeypatch.setattr(cn_analysis, "service", CNAnalysisService())
    client = TestClient(app)

    response = client.get(
        "/cn/visualization",
        params={"symbol": "600519", "analysis_date": "2026-05-11", "depth": "quick"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["symbol"] == "600519.SH"
    assert payload["kline"]
    assert {"date", "open", "high", "low", "close", "volume"}.issubset(payload["kline"][0])
    assert len(payload["trend"]) == len(payload["kline"])
    assert {"date", "close", "ma5", "ma20", "momentum"}.issubset(payload["trend"][0])
    assert payload["debate"]["stages"]
    assert any(stage["agent"] == "Portfolio Manager" for stage in payload["debate"]["stages"])


def test_cn_analyze_stream_emits_progress_before_done(monkeypatch):
    monkeypatch.setenv("CN_DATA_PROVIDER", "mock")
    monkeypatch.setenv("CN_ENABLE_LLM_DECISION", "false")
    monkeypatch.setattr(cn_analysis, "service", CNAnalysisService())
    client = TestClient(app)

    with client.stream(
        "GET",
        "/cn/analyze/stream",
        params={"symbol": "600519", "analysis_date": "2026-05-11", "depth": "quick"},
    ) as response:
        assert response.status_code == 200
        chunks = "".join(response.iter_text())

    assert '"event": "market_data_start"' in chunks
    assert '"event": "report_done"' in chunks
    assert '"event": "decision_data_start"' in chunks
    assert '"event": "done"' in chunks
    assert chunks.index('"event": "market_data_start"') < chunks.index('"event": "done"')


def test_cn_status_exposes_runtime_without_secrets(monkeypatch):
    monkeypatch.delenv("CN_ENABLE_LLM", raising=False)
    monkeypatch.delenv("CN_DATA_PROVIDER", raising=False)
    client = TestClient(app)

    response = client.get("/cn/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["llm_enabled"] is True
    assert payload["data_provider"] == "a_stock_data"
    assert "key" not in str(payload).lower()
