from tradingagents.cn.graph.cn_analysis_graph import CNAnalysisGraph
from tradingagents.cn.mock_provider import MockChinaMarketDataProvider
from tradingagents.cn.multi_agent_decision import (
    CNAstockLLMDecisionWriter,
    CNMultiAgentDecisionFlow,
    _chat_completion_urls,
    _llm_gateway_session,
)
from tradingagents.cn.services.analysis_service import CNAnalysisService
from tradingagents.cn.symbol import normalize_cn_symbol


def test_cn_analysis_service_returns_decision_panel(monkeypatch):
    monkeypatch.setenv("CN_DATA_PROVIDER", "mock")
    monkeypatch.setenv("CN_ENABLE_LLM_DECISION", "false")

    report, decision = CNAnalysisService().analyze_with_decision("600519", analysis_date="2026-05-11")

    assert report.symbol == "600519.SH"
    assert decision.symbol == "600519.SH"
    assert decision.analyst_reports
    assert {item.agent for item in decision.analyst_reports} >= {
        "Policy Analyst",
        "Hot Money Tracker",
        "Lockup Watcher",
    }
    assert decision.investment_debate.bull_case.agent == "Bull Researcher"
    assert decision.risk_debate.conservative_view.agent == "Conservative Risk Analyst"
    assert decision.investment_recommendation in {"买入", "持有/观察", "回避/等待"}
    assert decision.trade_decision
    assert decision.target_price_range
    assert decision.position_suggestion
    assert decision.stop_loss
    assert decision.take_profit
    assert any("政策" in item for item in decision.key_drivers)
    assert any("资金面" in item for item in decision.key_drivers)
    assert decision.simulated_action in {"建议买入", "建议持有/观察", "建议暂不买入"}


class _FakeLLM:
    def __init__(self, content):
        self.content = content

    def invoke(self, prompt):
        return type("Response", (), {"content": self.content(prompt)})()


class _FakeClient:
    def __init__(self, content):
        self.content = content

    def get_llm(self):
        return _FakeLLM(self.content)


def test_cn_astock_llm_decision_writer_uses_quick_and_deep_models(monkeypatch):
    calls = []

    def fake_create_llm_client(**kwargs):
        calls.append(kwargs)
        model = kwargs["model"]

        def content(prompt):
            assert "TradingAgents-astock" in prompt
            if model == "quick-model":
                return """
                {
                  "analyst_reports": [
                    {"agent":"Market Analyst","stance":"趋势偏强","summary":"量价结构改善。","evidence":["调用行情工具"]},
                    {"agent":"Sentiment Analyst","stance":"情绪中性","summary":"社交情绪暂不作为触发。","evidence":["[数据缺失: 授权社交数据]"]},
                    {"agent":"News Analyst","stance":"新闻偏正","summary":"公告和新闻可交叉验证。","evidence":["调用新闻工具"]},
                    {"agent":"Fundamentals Analyst","stance":"基本面稳健","summary":"收入和利润有增长线索。","evidence":["调用财务工具"]},
                    {"agent":"Policy Analyst","stance":"政策中性偏正","summary":"行业政策支持仍在。","evidence":["调用政策新闻工具"]},
                    {"agent":"Hot Money Tracker","stance":"资金偏流入","summary":"主力资金和北向资金偏正。","evidence":["调用资金流工具"]},
                    {"agent":"Lockup Watcher","stance":"轻微解禁压力","summary":"解禁比例较低但需复核。","evidence":["调用解禁日历工具"]}
                  ]
                }
                """
            return """
            {
              "symbol": "IGNORED",
              "generated_at": "IGNORED",
              "analyst_reports": [
                {"agent":"Market Analyst","stance":"趋势偏强","summary":"量价结构改善。","evidence":["调用行情工具"]},
                {"agent":"Sentiment Analyst","stance":"情绪中性","summary":"社交情绪暂不作为触发。","evidence":["[数据缺失: 授权社交数据]"]},
                {"agent":"News Analyst","stance":"新闻偏正","summary":"公告和新闻可交叉验证。","evidence":["调用新闻工具"]},
                {"agent":"Fundamentals Analyst","stance":"基本面稳健","summary":"收入和利润有增长线索。","evidence":["调用财务工具"]},
                {"agent":"Policy Analyst","stance":"政策中性偏正","summary":"行业政策支持仍在。","evidence":["调用政策新闻工具"]},
                {"agent":"Hot Money Tracker","stance":"资金偏流入","summary":"主力资金和北向资金偏正。","evidence":["调用资金流工具"]},
                {"agent":"Lockup Watcher","stance":"轻微解禁压力","summary":"解禁比例较低但需复核。","evidence":["调用解禁日历工具"]}
              ],
              "investment_debate": {
                "bull_case": {"agent":"Bull Researcher","stance":"支持买入观察","summary":"政策、资金和基本面共振。","evidence":["政策","资金"]},
                "bear_case": {"agent":"Bear Researcher","stance":"提示解禁与数据缺口","summary":"需要公告原文复核。","evidence":["解禁","数据缺失"]},
                "manager_synthesis": "Research Manager 深度综合后给出小仓位试探计划。"
              },
              "trader_plan": "Trader 考虑 T+1、涨跌停和 100 股手数，采用分批限价试探。",
              "risk_debate": {
                "aggressive_view": {"agent":"Aggressive Risk Analyst","stance":"允许小仓位进攻","summary":"趋势和资金支持。","evidence":["资金流"]},
                "neutral_view": {"agent":"Neutral Risk Analyst","stance":"等待复核","summary":"以公告和成交量确认。","evidence":["公告原文"]},
                "conservative_view": {"agent":"Conservative Risk Analyst","stance":"控制回撤","summary":"解禁和流动性需约束仓位。","evidence":["解禁"]},
                "portfolio_manager_decision": "Portfolio Manager 最终给出 Buy/Hold/Sell 与仓位。"
              },
              "investment_recommendation": "买入",
              "trade_decision": "小仓位分批买入",
              "simulated_action": "建议买入",
              "confidence": "medium",
              "horizon": "1-4 个交易周滚动复核",
              "target_price_range": "110-116",
              "position_suggestion": "10%-20%，按 100 股整数手执行",
              "stop_loss": "跌破 20 日均线复核",
              "take_profit": "接近涨停或放量冲高分批止盈",
              "key_drivers": ["政策支持", "资金面流入", "基本面增长"],
              "risk_controls": ["T+1 不可日内卖出", "涨跌停限制", "公告原文复核"],
              "decision_basis": ["LLM astock 多智能体流程", "7 Analyst + 双辩论 + PM"]
            }
            """

        return _FakeClient(content)

    monkeypatch.setattr("tradingagents.cn.multi_agent_decision.create_llm_client", fake_create_llm_client)
    provider = MockChinaMarketDataProvider()
    symbol = normalize_cn_symbol("600519")
    report = CNAnalysisGraph(provider).run(symbol, "2026-05-11")
    writer = CNAstockLLMDecisionWriter(
        {
            "llm_provider": "deepseek",
            "quick_think_llm": "quick-model",
            "deep_think_llm": "deep-model",
            "backend_url": None,
            "openai_reasoning_effort": None,
            "anthropic_effort": None,
            "google_thinking_level": None,
            "llm_timeout": 12,
        }
    )

    decision = CNMultiAgentDecisionFlow(provider, llm_decision_writer=writer).run(
        symbol=symbol,
        analysis_date="2026-05-11",
        depth="quick",
        neutral_report=report,
    )

    assert [call["model"] for call in calls] == ["quick-model", "deep-model"]
    assert decision.symbol == "600519.SH"
    assert decision.investment_recommendation == "买入"
    assert any("T+1" in item for item in decision.risk_controls)


def test_cn_astock_llm_decision_writer_emits_fallback_when_portfolio_parse_fails(monkeypatch):
    def fake_create_llm_client(**kwargs):
        model = kwargs["model"]

        def content(prompt):
            if model == "quick-model":
                return """
                {
                  "analyst_reports": [
                    {"agent":"Market Analyst","stance":"趋势偏强","summary":"量价结构改善。","evidence":["调用行情工具"]},
                    {"agent":"Sentiment Analyst","stance":"情绪中性","summary":"社交情绪暂不作为触发。","evidence":["数据缺失"]},
                    {"agent":"News Analyst","stance":"新闻偏正","summary":"公告和新闻可交叉验证。","evidence":["调用新闻工具"]},
                    {"agent":"Fundamentals Analyst","stance":"基本面稳健","summary":"收入和利润有增长线索。","evidence":["调用财务工具"]},
                    {"agent":"Policy Analyst","stance":"政策中性偏正","summary":"行业政策支持仍在。","evidence":["调用政策新闻工具"]},
                    {"agent":"Hot Money Tracker","stance":"资金偏流入","summary":"主力资金和北向资金偏正。","evidence":["调用资金流工具"]},
                    {"agent":"Lockup Watcher","stance":"轻微解禁压力","summary":"解禁比例较低但需复核。","evidence":["调用解禁日历工具"]}
                  ]
                }
                """
            return "组合经理返回了非 JSON 内容"

        return _FakeClient(content)

    monkeypatch.setattr("tradingagents.cn.multi_agent_decision.create_llm_client", fake_create_llm_client)
    provider = MockChinaMarketDataProvider()
    symbol = normalize_cn_symbol("600519")
    report = CNAnalysisGraph(provider).run(symbol, "2026-05-11")
    events = []
    writer = CNAstockLLMDecisionWriter(
        {
            "llm_provider": "deepseek",
            "quick_think_llm": "quick-model",
            "deep_think_llm": "deep-model",
            "backend_url": None,
            "openai_reasoning_effort": None,
            "anthropic_effort": None,
            "google_thinking_level": None,
            "llm_timeout": 12,
        },
        progress_callback=lambda event, payload: events.append((event, payload)),
    )

    decision = CNMultiAgentDecisionFlow(provider, llm_decision_writer=writer).run(
        symbol=symbol,
        analysis_date="2026-05-11",
        depth="quick",
        neutral_report=report,
    )

    fallback_events = [payload for event, payload in events if event == "llm_decision_fallback"]
    assert fallback_events
    assert fallback_events[0]["stage"] == "portfolio"
    assert any("已回退到确定性适配层" in item for item in decision.decision_basis)


def test_openai_compatible_gateway_uses_v1_once_and_ignores_proxy_by_default(monkeypatch):
    assert _chat_completion_urls("https://api.example.com/v1") == [
        "https://api.example.com/v1/chat/completions"
    ]
    assert _chat_completion_urls("https://api.example.com") == [
        "https://api.example.com/v1/chat/completions",
        "https://api.example.com/chat/completions",
    ]

    monkeypatch.delenv("TRADINGAGENTS_LLM_USE_PROXY", raising=False)
    assert _llm_gateway_session().trust_env is False

    monkeypatch.setenv("TRADINGAGENTS_LLM_USE_PROXY", "true")
    assert _llm_gateway_session().trust_env is True
