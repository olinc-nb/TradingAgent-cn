# TradingAgent-cn

`TradingAgent-cn` 是基于 TradingAgents 的中国市场适配版，面向 A 股公开信息分析和多智能体投资决策演示。项目保留原版 TradingAgents 的多角色协作框架，并新增中国市场数据适配、A 股代码标准化、结构化中性报告、交易决策输出和 FastAPI 前端页面。

> 免责声明：本项目仅用于研究、学习和系统验证，不构成任何投资建议、交易建议或收益承诺。

## 主要功能

- A 股代码标准化：支持 `600519`、`600519.SH`、`SH600519`、中文股票名等输入。
- 中国市场数据层：默认使用公开 A 股数据源，支持腾讯财经行情，并可通过 `akshare`、`mootdx` 增强。
- 中性结构化报告：输出公司概览、市场表现、新闻摘要、资金/政策/风险等公开信息分析。
- 多智能体决策：包含市场、情绪、新闻、基本面、政策、资金、限售解禁等视角，并生成交易员、风控和组合经理决策。
- API 与前端：FastAPI 提供 `POST /cn/analyze`，并内置本地网页用于交互查看分析结果。

## 环境要求

- Python 3.10+
- 推荐使用虚拟环境
- 如需真实 LLM 输出，需要配置至少一个模型供应商 API Key

## 安装

```bash
git clone https://github.com/olinc-nb/TradingAgent-cn.git
cd TradingAgent-cn

python -m venv .venv
source .venv/bin/activate

pip install -e .
```

也可以直接安装依赖：

```bash
pip install -r requirements.txt
```

## 配置方法

项目不会提交真实密钥。请复制示例配置后，在本机 `.env` 中填写自己的 key：

```bash
cp .env.example .env
```

`.env` 已被 `.gitignore` 忽略，不要把它加入 Git。仓库只保留 `.env.example` 和 `.env.enterprise.example` 这类不含真实密钥的模板文件。

常用配置示例：

```env
# 选择一个可用供应商填写即可
DEEPSEEK_API_KEY=your_key_here

# 中国市场默认数据源和 LLM 开关
CN_DATA_PROVIDER=a_stock_data
CN_ENABLE_LLM=true

# TradingAgents 模型配置
TRADINGAGENTS_LLM_PROVIDER=deepseek
TRADINGAGENTS_QUICK_THINK_LLM=deepseek-chat
TRADINGAGENTS_DEEP_THINK_LLM=deepseek-reasoner
```

支持的 LLM provider 包括 OpenAI、Google、Anthropic、xAI、DeepSeek、DashScope/Qwen、智谱 GLM、MiniMax、OpenRouter、Ollama、Azure OpenAI 等。完整环境变量见 `.env.example`。

如果使用 Ollama，本地默认地址为：

```env
OLLAMA_BASE_URL=http://localhost:11434/v1
```

## 运行 API 和前端

```bash
uvicorn api.main:app --reload --port 8000
```

打开浏览器访问：

```text
http://127.0.0.1:8000/
```

示例 API 请求：

```bash
curl -X POST http://127.0.0.1:8000/cn/analyze \
  -H 'Content-Type: application/json' \
  -d '{"symbol":"600519","analysis_date":"2026-05-11","depth":"quick"}'
```

如果只需要中性报告，不需要交易决策板块：

```bash
curl -X POST http://127.0.0.1:8000/cn/analyze \
  -H 'Content-Type: application/json' \
  -d '{"symbol":"600519","analysis_date":"2026-05-11","depth":"quick","include_decision":false}'
```

## CLI 使用

安装后可运行：

```bash
tradingagents
```

或直接从源码运行：

```bash
python -m cli.main
```

## Docker

```bash
cp .env.example .env
# 在 .env 中填写自己的 key
docker compose run --rm tradingagents
```

使用 Ollama：

```bash
docker compose --profile ollama run --rm tradingagents-ollama
```

## 测试

```bash
pytest
```

针对中国市场适配的核心测试：

```bash
pytest \
  tests/test_cn_symbol.py \
  tests/test_cn_analysis_service.py \
  tests/test_cn_a_stock_data_provider.py \
  tests/test_cn_llm_report_writer.py \
  tests/test_cn_multi_agent_decision.py \
  tests/test_cn_api_frontend.py
```

## 密钥安全

- 不要提交 `.env`、`.env.local`、私钥、证书或任何真实 token。
- 真实 key 只放在本机环境变量、`.env` 或部署平台的 secret 管理中。
- 提交前建议检查：

```bash
git status --short
git diff --cached
```

## 目录说明

- `tradingagents/`：TradingAgents 核心框架和中国市场适配模块。
- `api/`：FastAPI 服务和静态前端。
- `cli/`：命令行交互入口。
- `tests/`：单元测试和适配测试。
- `assets/`：README 和 CLI 使用的图片资源。
- `README_CN_ADAPTATION.md`：中国市场适配细节说明。
