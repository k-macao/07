# -*- coding: utf-8 -*-
"""个股新闻兜底数据源（免密钥，东方财富）。

背景：搜索服务（Bocha/Tavily/SerpAPI/SearXNG 等）未配置任何 API Key，
或全部引擎搜索失败/被过滤为空时，报告的「舆情情报」块会直接缺失，
微信推送页面上相应显示「数据缺失」。

本模块提供最后一级换源：改走 AkShare 的东方财富个股新闻接口
（``ak.stock_news_em``，免密钥、境内直连），把最近新闻格式化成与
``news_context`` 兼容的文本，供 classic 管线与 SearchService 共用。
"""
from __future__ import annotations

import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional, Tuple

from src.services.run_diagnostics import record_provider_run

logger = logging.getLogger(__name__)

# 复用常驻单线程池：超时后放弃等待，线程自行结束，不阻塞分析主流程
_POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="akshare-news")
_CN_CODE_RE = re.compile(r"^\d{6}$")
_DEFAULT_TIMEOUT_SECONDS = 15.0

PROVIDER_NAME = "AkshareEM"


def is_cn_stock_code(stock_code: str) -> bool:
    """仅 6 位纯数字的 A 股 / ETF 代码走东财个股新闻接口。"""
    return bool(_CN_CODE_RE.match((stock_code or "").strip()))


def _fetch_rows_uncached(stock_code: str, max_results: int) -> List[Dict[str, str]]:
    import akshare as ak

    df = ak.stock_news_em(symbol=stock_code)
    if df is None or df.empty:
        return []

    rows: List[Dict[str, str]] = []
    for _, record in df.head(max(max_results * 3, max_results)).iterrows():
        title = str(record.get("新闻标题") or "").strip()
        content = str(record.get("新闻内容") or "").strip()
        source = str(record.get("文章来源") or "").strip() or "东方财富"
        published = str(record.get("发布时间") or "").strip()
        url = str(record.get("新闻链接") or "").strip()
        if not title:
            continue
        rows.append(
            {
                "title": title,
                "snippet": content[:200],
                "source": source,
                "published_date": published,
                "url": url,
            }
        )
    return rows


def fetch_akshare_news_rows(
    stock_code: str,
    max_results: int = 5,
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
) -> List[Dict[str, str]]:
    """拉取东财个股新闻原始行；失败 / 超时 / 非A股代码返回空列表。

    结果计入 provider 诊断（run_diagnostics），便于在运行面板里观察
    兜底源的健康度。
    """
    code = (stock_code or "").strip()
    if not is_cn_stock_code(code):
        return []

    started = time.monotonic()
    future = _POOL.submit(_fetch_rows_uncached, code, int(max_results))
    try:
        rows = future.result(timeout=max(0.5, float(timeout_seconds)))
    except Exception as exc:  # noqa: BLE001 - 兜底路径不允许抛出
        record_provider_run(
            data_type="news_search",
            provider=PROVIDER_NAME,
            operation="stock_news_em",
            success=False,
            latency_ms=int((time.monotonic() - started) * 1000),
            error_type=type(exc).__name__,
            error_message=str(exc)[:200],
        )
        logger.info("[新闻兜底] %s 东财个股新闻获取失败: %s: %s", code, type(exc).__name__, exc)
        return []

    record_provider_run(
        data_type="news_search",
        provider=PROVIDER_NAME,
        operation="stock_news_em",
        success=bool(rows),
        latency_ms=int((time.monotonic() - started) * 1000),
        record_count=len(rows),
        error_type=None if rows else "empty",
        error_message=None if rows else "empty result",
    )
    return rows


def format_news_rows(
    rows: List[Dict[str, str]],
    stock_code: str,
    stock_name: Optional[str] = None,
    max_results: int = 5,
) -> str:
    """把兜底新闻行格式化为可注入 ``news_context`` 的文本。"""
    display_name = stock_name or stock_code
    lines = [f"【{display_name}({stock_code}) 个股新闻】（来源：东方财富-兜底数据源）"]
    for i, row in enumerate(rows[:max_results], 1):
        date_str = f" ({row['published_date']})" if row.get("published_date") else ""
        lines.append(f"\n{i}. 【{row['source']}】{row['title']}{date_str}")
        if row.get("snippet"):
            lines.append(f"   {row['snippet']}")
    return "\n".join(lines)


def fetch_stock_news_via_akshare(
    stock_code: str,
    stock_name: Optional[str] = None,
    max_results: int = 5,
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
) -> Optional[Tuple[str, int]]:
    """免密钥换源入口：返回 ``(news_context 文本, 条数)``，失败返回 None。"""
    rows = fetch_akshare_news_rows(stock_code, max_results=max_results, timeout_seconds=timeout_seconds)
    if not rows:
        return None
    text = format_news_rows(rows, stock_code, stock_name=stock_name, max_results=max_results)
    return text, len(rows[:max_results])
