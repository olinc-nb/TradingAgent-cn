from tradingagents.cn.llm_report_writer import CNLLMReportWriter
from tradingagents.cn.services.analysis_service import CNAnalysisService


class _FakeLLM:
    model_name = "fake-model"

    def invoke(self, prompt):
        assert "不得输出买入" in prompt
        assert "base_report" in prompt
        return type(
            "Response",
            (),
            {
                "content": """
                {
                  "symbol": "IGNORED",
                  "name": "IGNORED",
                  "generated_at": "IGNORED",
                  "company_overview": "LLM 改写后的公司概况。",
                  "market_data_summary": "LLM 改写后的行情摘要。",
                  "financial_summary": "LLM 改写后的财务摘要。",
                  "announcement_summary": "LLM 改写后的公告摘要。",
                  "news_summary": "LLM 改写后的新闻摘要。",
                  "technical_indicator_explanation": "LLM 改写后的技术指标解释。",
                  "risk_factors": ["LLM 改写后的风险因素。"],
                  "data_limitations": ["LLM 改写后的数据限制。"],
                  "neutral_summary": "LLM 改写后的中性总结。"
                }
                """
            },
        )()


class _FakeClient:
    def get_llm(self):
        return _FakeLLM()


def test_cn_llm_report_writer_uses_original_factory(monkeypatch):
    calls = []

    def fake_create_llm_client(**kwargs):
        calls.append(kwargs)
        return _FakeClient()

    monkeypatch.setattr(
        "tradingagents.cn.llm_report_writer.create_llm_client",
        fake_create_llm_client,
    )
    writer = CNLLMReportWriter(
        {
            "llm_provider": "deepseek",
            "quick_think_llm": "deepseek-chat",
            "deep_think_llm": "deepseek-reasoner",
            "backend_url": None,
            "openai_reasoning_effort": "low",
            "anthropic_effort": None,
            "google_thinking_level": None,
            "llm_timeout": 12,
        }
    )

    report = CNAnalysisService(llm_report_writer=writer).analyze("600519", analysis_date="2026-05-11")

    assert calls == [
        {
            "provider": "deepseek",
            "model": "deepseek-chat",
            "base_url": None,
            "reasoning_effort": "low",
            "anthropic_effort": None,
            "google_thinking_level": None,
            "timeout": 12,
        }
    ]
    assert report.symbol == "600519.SH"
    assert report.name == "贵州茅台"
    assert report.company_overview == "LLM 改写后的公司概况。"
    assert report.source_references
    assert report.forbidden_advice_detected is False


class _FailingLLM:
    def invoke(self, prompt):
        raise RuntimeError("quota exceeded")


class _FailingClient:
    def get_llm(self):
        return _FailingLLM()


def test_cn_llm_report_writer_falls_back_when_provider_fails(monkeypatch):
    monkeypatch.setattr(
        "tradingagents.cn.llm_report_writer.create_llm_client",
        lambda **kwargs: _FailingClient(),
    )
    writer = CNLLMReportWriter(
        {
            "llm_provider": "openai",
            "quick_think_llm": "gpt-5.5",
            "deep_think_llm": "gpt-5.5",
            "backend_url": None,
            "openai_reasoning_effort": None,
            "anthropic_effort": None,
            "google_thinking_level": None,
        }
    )

    report = CNAnalysisService(llm_report_writer=writer).analyze("600519", analysis_date="2026-05-11")

    assert report.symbol == "600519.SH"
    assert report.company_overview.startswith("贵州茅台")
    assert any("LLM 报告生成失败" in item for item in report.data_limitations)
