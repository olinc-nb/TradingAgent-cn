# TradingAgents China Market Adaptation

这个目录是从 `TradingAgents-main` 复制出来的中国市场版本。当前版本目标是跑通 A 股公开信息分析、多智能体辩论和交易决策输出闭环：

```text
A 股代码输入
  -> 代码标准化
  -> Mock 中国市场数据
  -> 中性结构化报告
  -> A 股完整多智能体辩论
  -> 交易员计划
  -> 风控辩论
  -> 组合经理决策
  -> 投资建议/交易决策额外板块
  -> HTTP API + 前端展示
```

当前版本先不启用合规改写，以功能适配和交互验证为优先。

## 已新增模块

- `tradingagents/cn/symbol.py`：A 股代码标准化，支持 `600519`、`600519.SH`、`SH600519`、`贵州茅台` 等输入。
- `tradingagents/cn/market_data.py`：中国市场数据 Provider 抽象。
- `tradingagents/cn/mock_provider.py`：第一阶段 Mock 数据源。
- `tradingagents/cn/a_stock_data_provider.py`：接入 `simonlin1212/a-stock-data` 中的无 Key 数据源模式，当前优先使用腾讯财经行情，`akshare`/`mootdx` 可选增强。
- `tradingagents/cn/schema.py`：`CNNeutralStockReport`、`CNTradingDecision` 等结构化模型。
- `tradingagents/cn/services/compliance_service.py`：合规审查服务保留，但当前主流程暂不启用。
- `tradingagents/cn/graph/cn_analysis_graph.py`：同步版中性分析流程。
- `tradingagents/cn/multi_agent_decision.py`：A 股完整多智能体辩论、交易员、风控、组合经理流程适配层，复用原版“分析师 -> 多空研究员 -> 交易员 -> 风控辩论 -> 组合经理”的角色分工，并吸收 `TradingAgents-astock` 的 7 Analyst、A 股交易约束和组合经理决策结构。
- `tradingagents/cn/services/analysis_service.py`：业务入口。
- `api/routes/cn_analysis.py`：`POST /cn/analyze` API。
- `api/static/`：轻量前端页面，展示中性报告和独立投资建议/交易决策板块。

## 适配状态

| 功能 | 状态 | 说明 |
| --- | --- | --- |
| A 股完整多智能体辩论、交易员、风控、组合经理流程 | 已适配 | 默认保留确定性回退；启用 LLM 时按 `TradingAgents-astock` 风格生成 7 Analyst、投研辩论、Research Manager、Trader、三方风险辩论和 Portfolio Manager 决策。 |
| A 股投资建议/交易决策输出 | 已适配 | 作为额外 `decision` 板块返回，包含投资建议、交易动作、目标区间、仓位、止损、止盈、风控依据。 |
| A 股特化视角 | 已增强 | 吸收 `TradingAgents-astock` 的政策、资金/游资、限售解禁三类视角，接入报告和决策板块。 |
| 交互前端 | 已优化 | FastAPI 直接挂载静态页面，左右分栏展示中性报告与交易决策。 |
| 合规改写 | 暂不启用 | 服务代码保留，当前版本按需求先不管合规。 |

## 与 TradingAgents-astock 的差异和吸收点

`TradingAgents-astock` 是对原版 TradingAgents 主链路的深度 A 股 fork，直接把数据层、分析师角色和交易约束改造成 A 股原生模式。当前中文市场版保持独立 `tradingagents/cn` API 适配层，不整包替换原版 LangGraph 主链路，避免破坏上游兼容性。

已吸收的优点：

- 数据源默认贴近中国市场：腾讯财经行情，`akshare`/`mootdx` 可选增强。
- 报告新增政策/监管公开信息、概念板块、资金流、龙虎榜、限售解禁来源。
- 决策新增 `Policy Analyst`、`Hot Money Tracker`、`Lockup Watcher` 三个 A 股特化 agent 视角。
- A 股特有风险会进入 `risk_factors`、`key_drivers` 和风控约束，而不只停留在普通新闻摘要。

## 本地测试

```bash
cd TradingAgents-cn
pytest tests/test_cn_symbol.py tests/test_cn_compliance.py tests/test_cn_analysis_service.py
```

当前建议测试：

```bash
pytest tests/test_cn_symbol.py tests/test_cn_analysis_service.py tests/test_cn_a_stock_data_provider.py tests/test_cn_llm_report_writer.py tests/test_cn_multi_agent_decision.py tests/test_cn_api_frontend.py
```

## API 运行

```bash
cd TradingAgents-cn
uvicorn api.main:app --reload --port 8000
```

默认使用 `a-stock-data` 中国市场公开数据源，并按 `.env` / `TRADINGAGENTS_*` 配置调用真实 LLM。

完整数据能力建议安装：

```bash
pip install mootdx akshare requests pandas stockstats
```

## LLM 配置

中国市场模块复用原版 LLM 配置，不维护第二套模型模板。按原版 `.env.example` 填写 API Key，并通过 `TRADINGAGENTS_*` 环境变量选择 provider 和模型。

```bash
cp .env.example .env
```

示例：DeepSeek。

```env
DEEPSEEK_API_KEY=你的 DeepSeek Key
TRADINGAGENTS_LLM_PROVIDER=deepseek
TRADINGAGENTS_QUICK_THINK_LLM=deepseek-chat
TRADINGAGENTS_DEEP_THINK_LLM=deepseek-reasoner
```

示例：通义千问中国区。

```env
DASHSCOPE_CN_API_KEY=你的 DashScope CN Key
TRADINGAGENTS_LLM_PROVIDER=qwen-cn
TRADINGAGENTS_QUICK_THINK_LLM=qwen-plus
TRADINGAGENTS_DEEP_THINK_LLM=qwen-max
```

LLM 报告生成默认启用。也可以显式声明默认运行方式：

```bash
CN_ENABLE_LLM=true CN_DATA_PROVIDER=a_stock_data \
uvicorn api.main:app --reload --port 8000
```

中文市场版沿用原版 `tradingagents.llm_clients.factory.create_llm_client()`，所以支持原版已有的 OpenAI、Anthropic、Google、Azure OpenAI、xAI、DeepSeek、Qwen、Qwen CN、GLM、GLM CN、MiniMax、OpenRouter、Ollama 等 provider。

LLM 报告输出会先按 `CNNeutralStockReport` 结构校验；当前版本不启用合规改写。如果模型调用或结构化解析失败，会回退到确定性模板并在 `data_limitations` 中记录原因。

LLM 决策输出默认跟随 `CN_ENABLE_LLM`，也可以单独控制：

```bash
CN_ENABLE_LLM_DECISION=true
```

决策层会先用 quick model 生成 `Market / Sentiment / News / Fundamentals / Policy / Hot Money / Lockup` 七个 analyst 研报，再用 deep model 生成 `Bull vs Bear -> Research Manager -> Trader -> Aggressive/Conservative/Neutral -> Portfolio Manager` 的完整 `CNTradingDecision`。模型输出必须通过结构化校验；失败时会回退到确定性适配层，并在 `decision_basis` 中记录原因。

示例请求：

```bash
curl -X POST http://127.0.0.1:8000/cn/analyze \
  -H 'Content-Type: application/json' \
  -d '{"symbol":"600519","analysis_date":"2026-05-11","depth":"quick"}'
```

默认响应会同时返回：

- `report`：中性公开信息结构化报告。
- `decision`：额外的 TradingAgents 多智能体投资建议/交易决策板块。

如只需要中性报告，可关闭决策板块：

```bash
curl -X POST http://127.0.0.1:8000/cn/analyze \
  -H 'Content-Type: application/json' \
  -d '{"symbol":"600519","analysis_date":"2026-05-11","depth":"quick","include_decision":false}'
```

浏览器交互页面：

```text
http://127.0.0.1:8000/
```

## 当前边界

中文市场版默认启用 LLM 报告生成，并默认使用 `a_stock_data` 数据源以贴近中国 A 股市场公开数据。`a-stock-data` 模式已经可以接入腾讯财经行情；如果本地安装 `akshare` 和 `mootdx`，会进一步尝试获取个股基本面、新闻、巨潮公告和日线数据。外部数据源失败时会写入 `data_limitations` 并自动降级，保证 API 闭环可验证。

多智能体交易决策板块已有两层实现：默认调用 `TradingAgents-astock` 风格的 A 股多阶段决策 writer；当外部数据源或 LLM 网关异常时，服务会回退到确定性适配层并在输出中记录原因，避免 API 中断。后续可继续把当前 writer 升级为真正的 LangGraph tool-loop 节点编排。
