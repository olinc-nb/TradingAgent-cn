"""Provider backed by simonlin1212/a-stock-data endpoint patterns.

The upstream project is distributed as a self-contained SKILL.md rather than a
regular Python package. This provider implements the stable no-key endpoints
directly and treats akshare/mootdx as optional enhancements.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from datetime import date, datetime, timedelta
from typing import Any, Callable

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from tradingagents.cn.market_data import ChinaMarketDataProvider
from tradingagents.cn.mock_provider import MockChinaMarketDataProvider
from tradingagents.cn.symbol import CNSymbol

logger = logging.getLogger(__name__)


# TTL (seconds) per data category. Quote data is short-lived; fundamentals and
# news change slowly, so keep them around for several minutes to absorb bursts.
_TTL_QUOTE = 8
_TTL_FUND_FLOW = 30
_TTL_NEWS = 300
_TTL_PROFILE = 1800
_TTL_FINANCIAL = 1800
_TTL_LHB = 600
_TTL_LOCKUP = 1800
_TTL_GLOBAL_NEWS = 120
_TTL_NORTHBOUND = 60
_TTL_BAIDU_BLOCKS = 1800
_TTL_BARS = 600


class _TTLCache:
    """Thread-safe TTL cache with negative caching to soak up repeat misses."""

    def __init__(self, negative_ttl: float = 30.0) -> None:
        self._store: dict[str, tuple[float, Any]] = {}
        self._lock = threading.Lock()
        self._negative_ttl = negative_ttl

    def get(self, key: str) -> tuple[bool, Any]:
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return False, None
            expires_at, value = entry
            if expires_at < time.monotonic():
                self._store.pop(key, None)
                return False, None
            return True, value

    def set(self, key: str, value: Any, ttl: float) -> None:
        with self._lock:
            self._store[key] = (time.monotonic() + ttl, value)

    def remember_failure(self, key: str) -> None:
        self.set(key, _CACHE_FAILURE, self._negative_ttl)


_CACHE_FAILURE = object()
_HTTP_CACHE = _TTLCache(negative_ttl=30.0)
_CALL_CACHE = _TTLCache(negative_ttl=30.0)


def _build_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        status=3,
        backoff_factor=0.6,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET", "HEAD"),
        respect_retry_after_header=True,
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=20, pool_maxsize=20)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


_SESSION_LOCAL = threading.local()


def _session() -> requests.Session:
    sess = getattr(_SESSION_LOCAL, "session", None)
    if sess is None:
        sess = _build_session()
        _SESSION_LOCAL.session = sess
    return sess


def _http_get_text(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    encoding: str | None = None,
    timeout: float = 10.0,
    ttl: float = _TTL_QUOTE,
) -> str | None:
    cache_key = f"text::{url}"
    hit, cached = _HTTP_CACHE.get(cache_key)
    if hit:
        return None if cached is _CACHE_FAILURE else cached
    try:
        response = _session().get(url, headers=headers, timeout=timeout)
        response.raise_for_status()
        if encoding:
            response.encoding = encoding
        text = response.text
    except Exception as exc:
        logger.debug("HTTP GET text failed for %s: %s", url, exc)
        _HTTP_CACHE.remember_failure(cache_key)
        raise
    _HTTP_CACHE.set(cache_key, text, ttl)
    return text


def _http_get_json(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: float = 10.0,
    ttl: float = _TTL_FUND_FLOW,
) -> Any:
    cache_key = f"json::{url}"
    hit, cached = _HTTP_CACHE.get(cache_key)
    if hit:
        if cached is _CACHE_FAILURE:
            raise RuntimeError("recent failure cached")
        return cached
    try:
        response = _session().get(url, headers=headers, timeout=timeout)
        response.raise_for_status()
        data = response.json()
    except Exception as exc:
        logger.debug("HTTP GET json failed for %s: %s", url, exc)
        _HTTP_CACHE.remember_failure(cache_key)
        raise
    _HTTP_CACHE.set(cache_key, data, ttl)
    return data


def _cached_call(namespace: str, key: str, ttl: float, func: Callable[[], Any]) -> Any:
    """Cache the result of an arbitrary callable (e.g. an akshare call).

    Negative results (exceptions) are remembered for `negative_ttl` so that a
    flapping upstream does not get hammered on every analysis pass.
    """
    cache_key = f"{namespace}::{key}"
    hit, cached = _CALL_CACHE.get(cache_key)
    if hit:
        if cached is _CACHE_FAILURE:
            raise RuntimeError(f"{namespace} recently failed; cached miss")
        return cached
    try:
        result = func()
    except Exception:
        _CALL_CACHE.remember_failure(cache_key)
        raise
    _CALL_CACHE.set(cache_key, result, ttl)
    return result


def _hash_args(*parts: Any) -> str:
    payload = "|".join(str(part) for part in parts)
    return hashlib.md5(payload.encode("utf-8")).hexdigest()  # noqa: S324 — cache key only


class AStockDataProvider(ChinaMarketDataProvider):
    provider_name = "a-stock-data"

    def __init__(self, fallback_provider: ChinaMarketDataProvider | None = None) -> None:
        self.fallback_provider = fallback_provider or MockChinaMarketDataProvider()
        self._quote_cache: dict[str, dict] = {}
        self._limitations: list[str] = []
        self._used_fallback = False

    def get_security_profile(self, symbol: CNSymbol) -> dict:
        quote = self._get_quote(symbol)
        profile = {
            "symbol": symbol.standard,
            "name": quote.get("name") or symbol.display_name,
            "exchange": symbol.exchange,
            "board": symbol.board,
            "industry": "ETF" if symbol.asset_type == "etf" else "未获取",
            "listing_date": "未知",
            "source": "腾讯财经 API",
        }

        info = self._akshare_individual_info(symbol) if symbol.asset_type == "stock" else {}
        if info:
            profile.update(
                {
                    "name": info.get("股票简称") or profile["name"],
                    "industry": info.get("行业") or profile["industry"],
                    "listing_date": _format_date(info.get("上市时间")) or profile["listing_date"],
                    "total_market_cap": info.get("总市值"),
                    "float_market_cap": info.get("流通市值"),
                    "source": "腾讯财经 API + akshare stock_individual_info_em",
                }
            )
        elif not quote and symbol.asset_type == "stock":
            self._used_fallback = True
            return self.fallback_provider.get_security_profile(symbol)

        return profile

    def get_daily_bars(self, symbol: CNSymbol, start_date: str, end_date: str) -> list[dict]:
        bars = self._mootdx_daily_bars(symbol)
        if bars:
            return bars

        bars = self._tencent_daily_bars(symbol)
        if bars:
            self._add_limitation("mootdx 不可用，已使用腾讯财经日线接口补齐 K 线。")
            return bars

        quote = self._get_quote(symbol)
        if not quote:
            self._used_fallback = True
            return self.fallback_provider.get_daily_bars(symbol, start_date, end_date)

        self._add_limitation("mootdx/腾讯日线均不可用，日线行情暂以实时行情快照构造，不能替代完整 K 线。")
        end = date.fromisoformat(end_date)
        close = quote.get("price") or quote.get("last_close") or 0
        last_close = quote.get("last_close") or close
        return [
            {
                "date": (end - timedelta(days=1)).isoformat(),
                "open": last_close,
                "high": last_close,
                "low": last_close,
                "close": last_close,
                "volume": 0,
            },
            {
                "date": end.isoformat(),
                "open": quote.get("open") or close,
                "high": quote.get("high") or close,
                "low": quote.get("low") or close,
                "close": close,
                "volume": int(quote.get("volume") or 0),
            },
        ]

    def get_financial_summary(self, symbol: CNSymbol) -> dict:
        quote = self._get_quote(symbol)
        if not quote:
            if symbol.asset_type != "stock":
                self._add_limitation("腾讯财经行情接口不可用，未使用股票 Mock 财务数据补齐 ETF。")
                return {
                    "asset_type": symbol.asset_type,
                    "period": "腾讯财经实时估值快照",
                    "revenue_yoy": "不适用",
                    "net_profit_yoy": "不适用",
                    "gross_margin": "不适用",
                    "operating_cash_flow": "不适用",
                    "debt_asset_ratio": "不适用",
                }
            self._used_fallback = True
            return self.fallback_provider.get_financial_summary(symbol)
        return {
            "asset_type": symbol.asset_type,
            "period": "腾讯财经实时估值快照",
            "revenue_yoy": "未获取",
            "net_profit_yoy": "未获取",
            "gross_margin": "未获取",
            "operating_cash_flow": "未获取",
            "debt_asset_ratio": "未获取",
            "price": quote.get("price"),
            "last_close": quote.get("last_close"),
            "open": quote.get("open"),
            "high": quote.get("high"),
            "low": quote.get("low"),
            "change_pct": quote.get("change_pct"),
            "amount_wan": quote.get("amount_wan"),
            "pe_ttm": quote.get("pe_ttm"),
            "pe_static": quote.get("pe_static"),
            "pb": quote.get("pb"),
            "mcap_yi": quote.get("mcap_yi"),
            "float_mcap_yi": quote.get("float_mcap_yi"),
            "turnover_pct": quote.get("turnover_pct"),
        }

    def get_announcements(self, symbol: CNSymbol, limit: int = 20) -> list[dict]:
        if symbol.asset_type != "stock":
            return []

        try:
            import akshare as ak
        except Exception as exc:
            self._add_limitation(f"akshare 未安装或不可用，未接入巨潮公告: {exc}")
            self._used_fallback = True
            return self.fallback_provider.get_announcements(symbol, limit=limit)

        try:
            df = _cached_call(
                "ak.stock_zh_a_disclosure_report_cninfo",
                _hash_args(symbol.code, _cninfo_market(symbol.code)),
                _TTL_NEWS,
                lambda: ak.stock_zh_a_disclosure_report_cninfo(
                    symbol=symbol.code,
                    market=_cninfo_market(symbol.code),
                ),
            )
        except Exception as exc:
            self._add_limitation(f"巨潮公告接口调用失败，使用 Mock 公告: {exc}")
            self._used_fallback = True
            return self.fallback_provider.get_announcements(symbol, limit=limit)

        records = []
        for row in _iter_records(df)[:limit]:
            records.append(
                {
                    "title": _pick(row, "公告标题", "title") or "未命名公告",
                    "published_at": _format_date(_pick(row, "公告日期", "date")),
                    "summary": _pick(row, "公告类型", "类型") or "公告原文需通过公告链接阅读。",
                    "source": "巨潮资讯",
                    "url": _pick(row, "公告链接", "url"),
                }
            )
        return records or self.fallback_provider.get_announcements(symbol, limit=limit)

    def get_news(self, symbol: CNSymbol, limit: int = 20) -> list[dict]:
        if symbol.asset_type != "stock":
            return []

        try:
            import akshare as ak
        except Exception as exc:
            self._add_limitation(f"akshare 未安装或不可用，未接入个股新闻: {exc}")
            self._used_fallback = True
            return self.fallback_provider.get_news(symbol, limit=limit)

        try:
            df = _cached_call(
                "ak.stock_news_em",
                _hash_args(symbol.code),
                _TTL_NEWS,
                lambda: ak.stock_news_em(symbol=symbol.code),
            )
        except Exception as exc:
            self._add_limitation(f"东财个股新闻接口调用失败，使用 Mock 新闻: {exc}")
            self._used_fallback = True
            return self.fallback_provider.get_news(symbol, limit=limit)

        records = []
        for row in _iter_records(df)[:limit]:
            title = _pick(row, "新闻标题", "标题", "title") or "未命名新闻"
            content = _pick(row, "新闻内容", "摘要", "内容", "summary") or title
            records.append(
                {
                    "title": title,
                    "published_at": _format_date(_pick(row, "发布时间", "日期", "time")),
                    "summary": str(content)[:240],
                    "source": _pick(row, "文章来源", "source") or "东方财富新闻",
                    "url": _pick(row, "新闻链接", "链接", "url"),
                }
            )
        return records or self.fallback_provider.get_news(symbol, limit=limit)

    def get_trade_calendar(self, start_date: str, end_date: str) -> list[dict]:
        return self.fallback_provider.get_trade_calendar(start_date, end_date)

    def get_policy_events(self, symbol: CNSymbol, analysis_date: str, limit: int = 10) -> list[dict]:
        if symbol.asset_type != "stock":
            return []

        keywords = ("政策", "监管", "证监会", "国务院", "发改委", "工信部", "财政部", "央行", "产业", "补贴")
        events: list[dict] = []

        try:
            import akshare as ak
            df = _cached_call(
                "ak.stock_news_em",
                _hash_args(symbol.code),
                _TTL_NEWS,
                lambda: ak.stock_news_em(symbol=symbol.code),
            )
            for row in _iter_records(df):
                title = str(_pick(row, "新闻标题", "标题", "title") or "")
                content = str(_pick(row, "新闻内容", "摘要", "内容", "summary") or "")
                if not any(keyword in title or keyword in content for keyword in keywords):
                    continue
                events.append(
                    {
                        "title": title or "未命名政策相关新闻",
                        "published_at": _format_date(_pick(row, "发布时间", "日期", "time")),
                        "summary": content[:240] or title,
                        "source": _pick(row, "文章来源", "source") or "东方财富新闻",
                        "url": _pick(row, "新闻链接", "链接", "url"),
                        "impact": "需结合政策口径和公司业务映射判断",
                    }
                )
                if len(events) >= limit:
                    return events
        except Exception as exc:
            self._add_limitation(f"政策相关新闻获取失败: {exc}")

        try:
            import akshare as ak
            for fetcher, source, namespace in (
                (ak.stock_info_global_cls, "财联社快讯", "ak.stock_info_global_cls"),
                (ak.stock_info_global_em, "东方财富全球资讯", "ak.stock_info_global_em"),
            ):
                df = _cached_call(namespace, "global", _TTL_GLOBAL_NEWS, fetcher)
                for row in _iter_records(df):
                    title = str(_pick(row, "标题", "title") or "")
                    content = str(_pick(row, "内容", "摘要", "content", "summary") or "")
                    if any(keyword in title or keyword in content for keyword in keywords):
                        events.append(
                            {
                                "title": title or "未命名政策事件",
                                "published_at": _format_date(_pick(row, "发布时间", "日期", "time")),
                                "summary": content[:240] or title,
                                "source": source,
                                "url": _pick(row, "链接", "url"),
                                "impact": "宏观/行业政策事件，需进一步映射到个股业务",
                            }
                        )
                    if len(events) >= limit:
                        return events
        except Exception as exc:
            self._add_limitation(f"宏观政策事件获取失败: {exc}")

        return events[:limit]

    def get_capital_flow(self, symbol: CNSymbol, analysis_date: str) -> dict:
        if symbol.asset_type != "stock":
            return {}

        result: dict = {"source": "百度股市通/同花顺/akshare"}
        result.update(self._baidu_concept_blocks(symbol))
        result.update(self._baidu_fund_flow(symbol, analysis_date))
        result.update(self._northbound_flow())
        result.update(self._dragon_tiger_board(symbol, analysis_date))
        return {key: value for key, value in result.items() if value not in (None, "", [], {})}

    def get_lockup_events(self, symbol: CNSymbol, analysis_date: str, forward_days: int = 90) -> list[dict]:
        if symbol.asset_type != "stock":
            return []

        events: list[dict] = []
        try:
            import akshare as ak
            end_dt = datetime.strptime(analysis_date, "%Y-%m-%d") + timedelta(days=forward_days)
            start_param = analysis_date.replace("-", "")
            end_param = end_dt.strftime("%Y%m%d")
            df = _cached_call(
                "ak.stock_restricted_release_detail_em",
                _hash_args(start_param, end_param),
                _TTL_LOCKUP,
                lambda: ak.stock_restricted_release_detail_em(
                    start_date=start_param,
                    end_date=end_param,
                ),
            )
            for row in _iter_records(df):
                if str(_pick(row, "股票代码", "代码")) != symbol.code:
                    continue
                events.append(
                    {
                        "unlock_date": _format_date(_pick(row, "解禁时间", "解禁日期")),
                        "share_type": _pick(row, "限售股类型", "类型") or "未披露",
                        "amount": _pick(row, "实际解禁数量", "解禁数量"),
                        "ratio": _pick(row, "占流通市值比例", "占总市值比例"),
                        "source": "东方财富解禁日历",
                    }
                )
        except Exception as exc:
            self._add_limitation(f"未来限售解禁日历获取失败: {exc}")

        if events:
            return events

        try:
            import akshare as ak
            df = _cached_call(
                "ak.stock_restricted_release_queue_em",
                _hash_args(symbol.code),
                _TTL_LOCKUP,
                lambda: ak.stock_restricted_release_queue_em(symbol=symbol.code),
            )
            for row in _iter_records(df)[:5]:
                events.append(
                    {
                        "unlock_date": _format_date(_pick(row, "解禁时间", "解禁日期")),
                        "share_type": _pick(row, "限售股类型", "类型") or "历史解禁记录",
                        "amount": _pick(row, "实际解禁数量", "解禁数量"),
                        "ratio": _pick(row, "占总市值比例", "占流通市值比例"),
                        "source": "东方财富个股解禁记录",
                    }
                )
        except Exception as exc:
            self._add_limitation(f"个股限售解禁记录获取失败: {exc}")

        return events

    def get_data_limitations(self) -> list[str]:
        limitations = list(self._limitations)
        if self._used_fallback:
            limitations.append("部分数据因外部接口不可用，已使用 Mock Provider 补齐。")
        return list(dict.fromkeys(limitations))

    def reset_run_state(self) -> None:
        self._limitations = []
        self._used_fallback = False

    def _get_quote(self, symbol: CNSymbol) -> dict:
        if symbol.code not in self._quote_cache:
            self._quote_cache[symbol.code] = self._tencent_quote(symbol.code)
        return self._quote_cache[symbol.code]

    def _tencent_quote(self, code: str) -> dict:
        prefixed = _tencent_prefix(code) + code
        data = ""
        errors = []
        headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"}
        for base_url in (
            "https://web.sqt.gtimg.cn/q=",
            "http://web.sqt.gtimg.cn/q=",
            "https://qt.gtimg.cn/q=",
        ):
            try:
                text = _http_get_text(
                    base_url + prefixed,
                    headers=headers,
                    encoding="gbk",
                    ttl=_TTL_QUOTE,
                )
                if text:
                    data = text
                    break
            except Exception as exc:
                errors.append(f"{base_url}: {exc}")
        if not data:
            self._add_limitation(f"腾讯财经行情接口调用失败: {'; '.join(errors)}")
            return {}

        for line in data.strip().split(";"):
            if not line.strip() or "=" not in line or '"' not in line:
                continue
            values = line.split('"')[1].split("~")
            if len(values) < 53 or not values[1]:
                continue
            return {
                "name": values[1],
                "price": _to_float(values[3]),
                "last_close": _to_float(values[4]),
                "open": _to_float(values[5]),
                "volume": _to_float(values[6]),
                "change_amt": _to_float(values[31]),
                "change_pct": _to_float(values[32]),
                "high": _to_float(values[33]),
                "low": _to_float(values[34]),
                "amount_wan": _to_float(values[37]),
                "turnover_pct": _to_float(values[38]),
                "pe_ttm": _to_float(values[39]),
                "amplitude_pct": _to_float(values[43]),
                "mcap_yi": _to_float(values[44]),
                "float_mcap_yi": _to_float(values[45]),
                "pb": _to_float(values[46]),
                "limit_up": _to_float(values[47]),
                "limit_down": _to_float(values[48]),
                "vol_ratio": _to_float(values[49]),
                "pe_static": _to_float(values[52]),
            }
        self._add_limitation("腾讯财经行情接口未返回可解析数据。")
        return {}

    def _mootdx_daily_bars(self, symbol: CNSymbol) -> list[dict]:
        try:
            from mootdx.quotes import Quotes
        except Exception as exc:
            self._add_limitation(f"mootdx 未安装或不可用，未获取完整日线: {exc}")
            return []

        def _fetch_bars():
            client = Quotes.factory(market="std")
            return client.bars(symbol=symbol.code, category=4, offset=120)

        try:
            df = _cached_call(
                "mootdx.bars",
                _hash_args(symbol.code, 4, 120),
                _TTL_BARS,
                _fetch_bars,
            )
        except Exception as exc:
            self._add_limitation(f"mootdx 日线接口调用失败: {exc}")
            return []

        bars = []
        for row in _iter_records(df):
            bars.append(
                {
                    "date": str(_pick(row, "datetime", "date"))[:10],
                    "open": _to_float(_pick(row, "open")),
                    "high": _to_float(_pick(row, "high")),
                    "low": _to_float(_pick(row, "low")),
                    "close": _to_float(_pick(row, "close")),
                    "volume": int(_to_float(_pick(row, "vol", "volume"))),
                }
            )
        return [bar for bar in bars if bar["date"] and bar["close"]]

    def _tencent_daily_bars(self, symbol: CNSymbol) -> list[dict]:
        code = _tencent_prefix(symbol.code) + symbol.code
        url = (
            f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
            f"?param={code},day,,,120,qfq"
        )
        try:
            response = _session().get(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0",
                    "Referer": "https://stockapp.finance.qq.com/",
                },
                timeout=8,
            )
            if response.status_code != 200:
                return []
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            self._add_limitation(f"腾讯日线接口调用失败: {exc}")
            return []

        series = (payload.get("data") or {}).get(code) or {}
        rows = series.get("qfqday") or series.get("day") or []
        bars: list[dict] = []
        for row in rows:
            if not row or len(row) < 6:
                continue
            try:
                bars.append(
                    {
                        "date": str(row[0])[:10],
                        "open": float(row[1]),
                        "close": float(row[2]),
                        "high": float(row[3]),
                        "low": float(row[4]),
                        "volume": int(float(row[5])),
                    }
                )
            except (TypeError, ValueError):
                continue
        return [bar for bar in bars if bar["date"] and bar["high"] >= bar["low"]]

    def _akshare_individual_info(self, symbol: CNSymbol) -> dict:
        try:
            import akshare as ak
        except Exception as exc:
            self._add_limitation(f"akshare 未安装或不可用，未获取个股基本面: {exc}")
            return {}

        try:
            df = _cached_call(
                "ak.stock_individual_info_em",
                _hash_args(symbol.code),
                _TTL_PROFILE,
                lambda: ak.stock_individual_info_em(symbol=symbol.code),
            )
        except Exception as exc:
            self._add_limitation("akshare 个股基本面接口暂不可用，已使用腾讯行情和估值快照继续分析。")
            return {}

        info = {}
        for row in _iter_records(df):
            item = row.get("item")
            value = row.get("value")
            if item:
                info[str(item)] = value
        return info

    def _add_limitation(self, message: str) -> None:
        if message not in self._limitations:
            self._limitations.append(message)

    def _baidu_concept_blocks(self, symbol: CNSymbol) -> dict:
        try:
            data = _http_get_json(
                "https://finance.pae.baidu.com/api/getrelatedblock"
                f'?stock=[{{"code":"{symbol.code}","market":"ab","type":"stock"}}]'
                "&finClientType=pc",
                headers=_BAIDU_PAE_HEADERS,
                ttl=_TTL_BAIDU_BLOCKS,
            )
            if str(data.get("ResultCode", -1)) != "0":
                self._add_limitation(f"百度股市通概念板块接口返回异常: {data.get('ResultMsg', data.get('ResultCode'))}")
                return {}
            categories = data.get("Result", {}).get(symbol.code, [])
            blocks = []
            industry = None
            for category in categories:
                name = category.get("name", "")
                items = category.get("list", []) or []
                if name in {"行业", "所属行业"} and items:
                    industry = items[0].get("name")
                if name == "概念":
                    blocks.extend(item.get("name") for item in items if item.get("name"))
            return {"industry_block": industry, "concept_blocks": blocks[:12]}
        except Exception as exc:
            self._add_limitation(f"百度股市通概念板块获取失败: {exc}")
            return {}

    def _baidu_fund_flow(self, symbol: CNSymbol, analysis_date: str) -> dict:
        result = {}
        try:
            data = _http_get_json(
                "https://finance.pae.baidu.com/vapi/v1/fundflow"
                f"?finance_type=stock&fund_flow_type=&type=stock&market=ab&code={symbol.code}"
                "&belongs=stocklevelone&finClientType=pc",
                headers=_BAIDU_PAE_HEADERS,
                ttl=_TTL_FUND_FLOW,
            )
            if str(data.get("ResultCode", -1)) == "0":
                rows = (
                    data.get("Result", {})
                    .get("content", {})
                    .get("fundFlowMinute", {})
                    .get("data", "")
                    .split(";")
                )
                rows = [row for row in rows if row]
                if rows:
                    parts = rows[-1].split(",")
                    if len(parts) >= 4:
                        result["main_force_wan"] = _to_float(parts[2])
                        result["retail_wan"] = _to_float(parts[3])
        except Exception as exc:
            self._add_limitation(f"百度股市通资金流获取失败: {exc}")

        try:
            data = _http_get_json(
                "https://finance.pae.baidu.com/vapi/v1/fundsortlist"
                f"?code={symbol.code}&market=ab&finance_type=stock&tab=day&from=history"
                f"&date={analysis_date.replace('-', '')}&pn=0&rn=5&finClientType=pc",
                headers=_BAIDU_PAE_HEADERS,
                ttl=_TTL_FUND_FLOW,
            )
            if str(data.get("ResultCode", -1)) == "0":
                history = data.get("Result", {}).get("content", []) or []
                result["fund_flow_history"] = [
                    {
                        "date": item.get("showtime"),
                        "main_force": item.get("extMainIn"),
                        "super_big": item.get("superNetIn"),
                        "large": item.get("largeNetIn"),
                    }
                    for item in history[:5]
                ]
        except Exception as exc:
            self._add_limitation(f"百度股市通历史资金流获取失败: {exc}")

        return result

    def _northbound_flow(self) -> dict:
        try:
            data = _http_get_json(
                "https://data.hexin.cn/market/hsgtApi/method/dayChart/",
                headers={
                    "User-Agent": "Mozilla/5.0",
                    "Host": "data.hexin.cn",
                    "Referer": "https://data.hexin.cn/",
                },
                ttl=_TTL_NORTHBOUND,
            )
            hgt = data.get("hgt") or []
            sgt = data.get("sgt") or []
            if hgt or sgt:
                hgt_close = _to_float(hgt[-1]) if hgt else 0
                sgt_close = _to_float(sgt[-1]) if sgt else 0
                return {
                    "northbound_hgt_yi": hgt_close,
                    "northbound_sgt_yi": sgt_close,
                    "northbound_total_yi": hgt_close + sgt_close,
                }
        except Exception as exc:
            self._add_limitation(f"北向资金获取失败: {exc}")
        return {}

    def _dragon_tiger_board(self, symbol: CNSymbol, analysis_date: str) -> dict:
        try:
            import akshare as ak
            end_dt = datetime.strptime(analysis_date, "%Y-%m-%d")
            start_dt = end_dt - timedelta(days=30)
            start_param = start_dt.strftime("%Y%m%d")
            end_param = end_dt.strftime("%Y%m%d")
            df = _cached_call(
                "ak.stock_lhb_detail_em",
                _hash_args(start_param, end_param),
                _TTL_LHB,
                lambda: ak.stock_lhb_detail_em(
                    start_date=start_param,
                    end_date=end_param,
                ),
            )
            records = [
                {
                    "date": _format_date(_pick(row, "上榜日", "日期")),
                    "reason": _pick(row, "上榜原因", "原因"),
                    "net_buy_wan": _pick(row, "龙虎榜净买额", "净买额"),
                    "turnover_pct": _pick(row, "换手率"),
                }
                for row in _iter_records(df)
                if str(_pick(row, "代码", "股票代码")) == symbol.code
            ]
            return {"dragon_tiger_records": records[:5]}
        except Exception as exc:
            self._add_limitation(f"龙虎榜获取失败: {exc}")
            return {}


def _tencent_prefix(code: str) -> str:
    if code.startswith(("5", "6", "9")):
        return "sh"
    if code.startswith("8"):
        return "bj"
    return "sz"


def _cninfo_market(code: str) -> str:
    return "沪深京"


_BAIDU_PAE_HEADERS = {
    "Host": "finance.pae.baidu.com",
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/vnd.finance-web.v1+json",
    "Origin": "https://gushitong.baidu.com",
    "Referer": "https://gushitong.baidu.com/",
}


def _iter_records(df) -> list[dict]:
    if df is None:
        return []
    if hasattr(df, "to_dict"):
        return df.to_dict("records")
    if isinstance(df, list):
        return [item for item in df if isinstance(item, dict)]
    return []


def _pick(row: dict, *keys: str):
    for key in keys:
        if key in row and row[key] not in (None, ""):
            return row[key]
    return None


def _format_date(value) -> str | None:
    if value in (None, ""):
        return None
    text = str(value)
    return text[:10]


def _to_float(value) -> float:
    try:
        if value in (None, ""):
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0
