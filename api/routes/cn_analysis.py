from datetime import date, timedelta
import json
import os
from queue import Empty, Queue
from threading import Event, Thread
import time
from typing import Any

from pydantic import BaseModel, Field
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from tradingagents.cn.multi_agent_decision import CNAstockLLMDecisionWriter, CNMultiAgentDecisionFlow
from tradingagents.cn.services.analysis_service import CNAnalysisService, get_cn_runtime_status
from tradingagents.cn.symbol import normalize_cn_symbol


router = APIRouter(prefix="/cn", tags=["China Market Analysis"])
service: CNAnalysisService | None = None


def _new_service(model_preset: str | None = None) -> CNAnalysisService:
    """Per-request service so two concurrent analyses don't share mutable state
    (provider _limitations / _quote_cache / _used_fallback)."""
    if service is not None and model_preset is None:
        return service
    return CNAnalysisService(model_preset=model_preset)


class AnalyzeRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=32)
    analysis_date: str | None = None
    depth: str = "quick"
    include_decision: bool = True
    model: str | None = None


@router.post("/analyze")
def analyze(request: AnalyzeRequest) -> dict:
    service = _new_service(request.model)
    try:
        if request.include_decision:
            report, decision = service.analyze_with_decision(
                raw_symbol=request.symbol,
                analysis_date=request.analysis_date,
                depth=request.depth,
            )
        else:
            report = service.analyze(
                raw_symbol=request.symbol,
                analysis_date=request.analysis_date,
                depth=request.depth,
            )
            decision = None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"error_code": "INVALID_SYMBOL", "message": str(exc)}) from exc
    payload = {"status": "done", "report": report.model_dump()}
    if decision is not None:
        payload["decision"] = decision.model_dump()
    return payload


@router.get("/status")
def status() -> dict:
    return get_cn_runtime_status()


@router.get("/analyze/stream")
async def analyze_stream(
    request: Request,
    symbol: str,
    analysis_date: str | None = None,
    depth: str = "quick",
    include_decision: bool = True,
    model: str | None = None,
) -> StreamingResponse:
    events: Queue[dict[str, Any] | None] = Queue()
    cancel_event = Event()
    service = _new_service(model)

    def emit(event: str, payload: dict[str, Any] | None = None) -> None:
        if cancel_event.is_set():
            return
        events.put({"event": event, "elapsed": round(time.monotonic() - started_at, 2), **(payload or {})})

    def worker() -> None:
        try:
            cn_symbol = normalize_cn_symbol(symbol)
            run_date = analysis_date or date.today().isoformat()
            start_date = (date.fromisoformat(run_date) - timedelta(days=180)).isoformat()
            emit("analysis_start", {"label": "开始分析", "symbol": cn_symbol.standard})
            if cancel_event.is_set():
                return

            service.data_provider.reset_run_state()
            emit("market_data_start", {"label": "加载 K 线和趋势数据"})
            profile = service.data_provider.get_security_profile(cn_symbol)
            bars = service.data_provider.get_daily_bars(cn_symbol, start_date, run_date)
            normalized_bars = _normalize_bars(bars)
            emit(
                "market_data_done",
                {
                    "label": "K 线和趋势数据已加载",
                    "visualization": {
                        "symbol": cn_symbol.standard,
                        "name": profile.get("name") or cn_symbol.display_name,
                        "data_provider": getattr(service.data_provider, "provider_name", "unknown"),
                        "kline": normalized_bars,
                        "trend": _trend_points(normalized_bars),
                    },
                },
            )
            if cancel_event.is_set():
                return

            emit("report_start", {"label": "生成中性公开信息报告"})
            report = service.graph.run(symbol=cn_symbol, analysis_date=run_date, depth=depth)
            emit("report_done", {"label": "中性报告已生成", "report": report.model_dump()})
            if cancel_event.is_set():
                return

            decision = None
            if include_decision:
                emit("decision_start", {"label": "启动多智能体交易决策"})
                llm_writer = _stream_llm_decision_writer(emit, service.config_override)
                flow = CNMultiAgentDecisionFlow(
                    service.data_provider,
                    llm_decision_writer=llm_writer,
                    progress_callback=emit,
                )
                decision = flow.run(
                    symbol=cn_symbol,
                    analysis_date=run_date,
                    depth=depth,
                    neutral_report=report,
                )
                emit("decision_done", {"label": "交易决策已生成", "decision": decision.model_dump()})
            if cancel_event.is_set():
                return

            emit(
                "done",
                {
                    "label": "分析完成",
                    "report": report.model_dump(),
                    "decision": decision.model_dump() if decision is not None else None,
                },
            )
        except Exception as exc:
            if not cancel_event.is_set():
                emit("error", {"label": str(exc)})
        finally:
            events.put(None)

    async def stream():
        thread = Thread(target=worker, daemon=True)
        thread.start()
        # 2KB padding comment up-front so Cloudflare/Cloudflared/proxies flush the
        # first bytes of the SSE stream immediately instead of waiting for their
        # internal buffer to fill. Without this, all "LLM 思考辩论" progress events
        # appear lumped together at the very end of the run.
        yield ":" + (" " * 2048) + "\n\n"
        last_send = time.monotonic()
        try:
            while True:
                if await request.is_disconnected():
                    cancel_event.set()
                    break
                try:
                    item = events.get(timeout=0.5)
                except Empty:
                    if time.monotonic() - last_send >= 5:
                        last_send = time.monotonic()
                        yield ": keepalive\n\n"
                    continue
                if item is None:
                    break
                last_send = time.monotonic()
                yield f"data: {json.dumps(item, ensure_ascii=False, default=str)}\n\n"
        finally:
            cancel_event.set()

    started_at = time.monotonic()
    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


def _stream_llm_decision_writer(progress_callback, config: dict | None = None) -> CNAstockLLMDecisionWriter | None:
    if not _env_flag("CN_ENABLE_LLM_DECISION", _env_flag("CN_ENABLE_LLM", True)):
        return None
    try:
        return CNAstockLLMDecisionWriter(progress_callback=progress_callback, config=config)
    except Exception:
        return None


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@router.get("/visualization")
def visualization(
    symbol: str,
    analysis_date: str | None = None,
    depth: str = "quick",
    include_decision: bool = True,
    model: str | None = None,
) -> dict:
    service = _new_service(model)
    try:
        cn_symbol = normalize_cn_symbol(symbol)
        run_date = analysis_date or date.today().isoformat()
        start_date = (date.fromisoformat(run_date) - timedelta(days=180)).isoformat()
        service.data_provider.reset_run_state()
        profile = service.data_provider.get_security_profile(cn_symbol)
        bars = service.data_provider.get_daily_bars(cn_symbol, start_date, run_date)
        decision = None
        if include_decision:
            report = service.graph.run(symbol=cn_symbol, analysis_date=run_date, depth=depth)
            deterministic_flow = CNMultiAgentDecisionFlow(service.data_provider, llm_decision_writer=None)
            decision = deterministic_flow.run(
                symbol=cn_symbol,
                analysis_date=run_date,
                depth=depth,
                neutral_report=report,
            )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"error_code": "INVALID_SYMBOL", "message": str(exc)}) from exc

    normalized_bars = _normalize_bars(bars)
    return {
        "status": "done",
        "symbol": cn_symbol.standard,
        "name": profile.get("name") or cn_symbol.display_name,
        "generated_at": date.today().isoformat(),
        "data_provider": getattr(service.data_provider, "provider_name", "unknown"),
        "kline": normalized_bars,
        "trend": _trend_points(normalized_bars),
        "debate": _debate_payload(decision.model_dump() if decision is not None else None),
        "data_limitations": service.data_provider.get_data_limitations(),
    }


def _normalize_bars(bars: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = []
    for bar in bars:
        try:
            normalized.append(
                {
                    "date": str(bar.get("date") or ""),
                    "open": float(bar.get("open") or 0),
                    "high": float(bar.get("high") or 0),
                    "low": float(bar.get("low") or 0),
                    "close": float(bar.get("close") or 0),
                    "volume": int(float(bar.get("volume") or 0)),
                }
            )
        except (TypeError, ValueError):
            continue
    return [bar for bar in normalized if bar["date"] and bar["high"] >= bar["low"]]


def _trend_points(bars: list[dict[str, Any]]) -> list[dict[str, Any]]:
    closes = [bar["close"] for bar in bars]
    trend = []
    for index, bar in enumerate(bars):
        ma5 = _moving_average(closes, index, 5)
        ma20 = _moving_average(closes, index, 20)
        trend.append(
            {
                "date": bar["date"],
                "close": bar["close"],
                "ma5": ma5,
                "ma20": ma20,
                "momentum": ((bar["close"] - closes[index - 1]) / closes[index - 1] * 100)
                if index and closes[index - 1]
                else 0,
            }
        )
    return trend


def _moving_average(values: list[float], index: int, window: int) -> float | None:
    if index + 1 < window:
        return None
    segment = values[index + 1 - window : index + 1]
    return round(sum(segment) / window, 4)


def _debate_payload(decision: dict[str, Any] | None) -> dict[str, Any]:
    if not decision:
        return {"stages": [], "summary": "未开启 LLM 思考辩论可视化。"}

    stages = []
    for analyst in decision.get("analyst_reports") or []:
        stages.append(_stage("分析师", analyst))

    investment_debate = decision.get("investment_debate") or {}
    for key, label in (("bull_case", "多方研究"), ("bear_case", "空方研究")):
        if investment_debate.get(key):
            stages.append(_stage(label, investment_debate[key]))
    if investment_debate.get("manager_synthesis"):
        stages.append(
            {
                "phase": "研究经理",
                "agent": "Research Manager",
                "stance": "综合",
                "summary": investment_debate["manager_synthesis"],
                "evidence": [],
            }
        )

    if decision.get("trader_plan"):
        stages.append(
            {
                "phase": "交易员",
                "agent": "Trader",
                "stance": decision.get("trade_decision") or "",
                "summary": decision["trader_plan"],
                "evidence": decision.get("key_drivers") or [],
            }
        )

    risk_debate = decision.get("risk_debate") or {}
    for key, label in (
        ("aggressive_view", "进取风控"),
        ("neutral_view", "中性风控"),
        ("conservative_view", "保守风控"),
    ):
        if risk_debate.get(key):
            stages.append(_stage(label, risk_debate[key]))
    if risk_debate.get("portfolio_manager_decision"):
        stages.append(
            {
                "phase": "组合经理",
                "agent": "Portfolio Manager",
                "stance": decision.get("simulated_action") or decision.get("investment_recommendation") or "",
                "summary": risk_debate["portfolio_manager_decision"],
                "evidence": decision.get("risk_controls") or [],
            }
        )

    return {
        "stages": stages,
        "summary": decision.get("simulated_action") or decision.get("investment_recommendation") or "已生成辩论链路",
    }


def _stage(phase: str, view: dict[str, Any]) -> dict[str, Any]:
    return {
        "phase": phase,
        "agent": view.get("agent") or phase,
        "stance": view.get("stance") or "",
        "summary": view.get("summary") or "",
        "evidence": view.get("evidence") or [],
    }


# ============ Hot sectors (real-time) ============
import requests
from concurrent.futures import ThreadPoolExecutor

# 12 representative A-share thematic ETFs as sector proxies (Tencent code with prefix)
_HOT_SECTOR_PROXIES = [
    {"name": "科创 50", "code": "sh588000", "leader": "科创50ETF华夏"},
    {"name": "创业板", "code": "sz159915", "leader": "创业板ETF易方达"},
    {"name": "AI 大模型", "code": "sz159819", "leader": "人工智能ETF易方达"},
    {"name": "半导体", "code": "sh512760", "leader": "芯片 ETF 国泰"},
    {"name": "新能源车", "code": "sz159853", "leader": "新能源车 ETF"},
    {"name": "光伏储能", "code": "sh515790", "leader": "光伏 ETF"},
    {"name": "医药创新", "code": "sh512010", "leader": "医药 ETF"},
    {"name": "白酒消费", "code": "sh512690", "leader": "酒 ETF"},
    {"name": "国防军工", "code": "sh512660", "leader": "军工 ETF"},
    {"name": "金融", "code": "sh510230", "leader": "金融 ETF"},
    {"name": "互联网", "code": "sh513050", "leader": "中概互联网"},
    {"name": "红利低波", "code": "sh515100", "leader": "红利低波 ETF"},
]


_HOT_SECTORS_CACHE: dict[str, Any] = {"ts": 0.0, "data": None}


@router.get("/hot_sectors")
def hot_sectors() -> dict:
    now = time.time()
    if _HOT_SECTORS_CACHE["data"] and now - _HOT_SECTORS_CACHE["ts"] < 30:
        return _HOT_SECTORS_CACHE["data"]

    quotes = _tencent_batch_quote([s["code"] for s in _HOT_SECTOR_PROXIES])

    def fetch_spark(code: str) -> list[float]:
        try:
            return _tencent_daily_close(code, days=24)
        except Exception:
            return []

    sectors: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=8) as executor:
        spark_futures = {meta["code"]: executor.submit(fetch_spark, meta["code"]) for meta in _HOT_SECTOR_PROXIES}
        for meta in _HOT_SECTOR_PROXIES:
            quote = quotes.get(meta["code"]) or {}
            spark = spark_futures[meta["code"]].result() or []
            sectors.append(
                {
                    "name": meta["name"],
                    "symbol": meta["code"][2:],
                    "leader": meta["leader"],
                    "price": quote.get("price"),
                    "pct": quote.get("pct"),
                    "change": quote.get("change"),
                    "volume": quote.get("volume_yi"),
                    "leader_pct": quote.get("pct"),
                    "spark": spark,
                }
            )

    payload = {
        "status": "ok",
        "generated_at": int(now * 1000),
        "data_provider": "tencent",
        "sectors": sectors,
    }
    _HOT_SECTORS_CACHE["data"] = payload
    _HOT_SECTORS_CACHE["ts"] = now
    return payload


def _tencent_batch_quote(codes: list[str]) -> dict[str, dict[str, Any]]:
    if not codes:
        return {}
    urls = [
        "http://qt.gtimg.cn/q=" + ",".join(codes),
        "https://qt.gtimg.cn/q=" + ",".join(codes),
        "https://web.sqt.gtimg.cn/q=" + ",".join(codes),
    ]
    raw = ""
    for url in urls:
        try:
            response = requests.get(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0",
                    "Referer": "https://stockapp.finance.qq.com/",
                },
                timeout=4,
            )
            if response.status_code != 200:
                continue
            response.encoding = "gbk"
            raw = response.text
            if raw.strip():
                break
        except requests.RequestException:
            continue
    if not raw:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for line in raw.split(";"):
        line = line.strip()
        if not line or "=" not in line:
            continue
        head, body = line.split("=", 1)
        key = head.replace("v_", "").strip()
        body = body.strip().strip('"')
        parts = body.split("~")
        if len(parts) < 33:
            continue
        try:
            price = float(parts[3]) if parts[3] else None
            change = float(parts[31]) if parts[31] else None
            pct = float(parts[32]) if parts[32] else None
            volume_yi = float(parts[37]) / 10000 if len(parts) > 37 and parts[37] else None
        except ValueError:
            price = change = pct = volume_yi = None
        out[key] = {
            "price": price,
            "change": change,
            "pct": pct,
            "volume_yi": volume_yi,
        }
    return out


def _tencent_daily_close(code: str, days: int = 24) -> list[float]:
    urls = [
        f"http://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={code},day,,,{days},qfq",
        f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={code},day,,,{days},qfq",
    ]
    payload: dict[str, Any] = {}
    for url in urls:
        try:
            response = requests.get(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0",
                    "Referer": "https://stockapp.finance.qq.com/",
                },
                timeout=4,
            )
            if response.status_code != 200:
                continue
            payload = response.json()
            if payload:
                break
        except (requests.RequestException, ValueError):
            continue
    data = payload.get("data") or {}
    series = data.get(code) or {}
    bars = series.get("qfqday") or series.get("day") or []
    closes = []
    for bar in bars:
        if len(bar) < 3:
            continue
        try:
            closes.append(round(float(bar[2]), 4))
        except (TypeError, ValueError):
            continue
    return closes[-days:]
