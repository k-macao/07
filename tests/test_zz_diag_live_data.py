# -*- coding: utf-8 -*-
"""临时诊断测试（勿合并）：在 CI 运行器上复现推送报告的数据块抓取。

背景：最新一次 ds-day 推送的微信页面出现「数据缺失」。沙箱无法直连
境内数据源，也拉不到 Actions 制品，因此借 CI 运行器（外网畅通）现场
抓一遍 600519 的全部数据块。结论通过 ``::error::`` 工作流命令写成
注解（可用 GitHub API 读回），同时写入 GITHUB_STEP_SUMMARY。
"""
from __future__ import annotations

import json
import os
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from typing import Any, Callable, Dict, List, Tuple

STOCK = os.environ.get("DIAG_STOCK", "600519")
DEADLINE_SECONDS = 80.0  # pytest --timeout=120 硬限
MAX_ANNOTATION_CHARS = 220

_PROBES: Dict[str, Callable[[Any], Any]] = {}


def probe(name: str):
    def deco(fn):
        _PROBES[name] = fn
        return fn

    return deco


@probe("stock_name")
def _stock_name(m):
    return m.get_stock_name(STOCK, allow_realtime=True)


@probe("realtime_quote")
def _quote(m):
    q = m.get_realtime_quote(STOCK, log_final_failure=False)
    if q is None:
        return "None"
    keys = (
        "price change_pct volume_ratio turnover_rate pe_ratio pb_ratio "
        "total_market_cap source"
    ).split()
    return {k: getattr(q, k, None) for k in keys}


@probe("daily_kline")
def _daily(m):
    df, source = m.get_daily_data(STOCK, days=120)
    if df is None or df.empty:
        return {"source": source, "rows": 0}
    tail = df.tail(1).to_dict("records")[0]
    return {
        "source": source,
        "rows": len(df),
        "last_date": str(tail.get("date")),
        "ma": {k: tail.get(k) for k in ("ma5", "ma10", "ma20") if k in tail},
    }


@probe("chip_distribution")
def _chip(m):
    c = m.get_chip_distribution(STOCK)
    if c is None:
        return "None"
    return {k: getattr(c, k, None) for k in ("profit_ratio", "avg_cost", "concentration", "source")}


@probe("capital_flow")
def _capital(m):
    b = m.get_capital_flow_context(STOCK, budget_seconds=20)
    return {
        "status": b.get("status"),
        "data": b.get("data"),
        "errors": (b.get("errors") or [])[:4],
    }


@probe("dragon_tiger")
def _dragon(m):
    b = m.get_dragon_tiger_context(STOCK, budget_seconds=20)
    return {"status": b.get("status"), "data": b.get("data"), "errors": b.get("errors")}


@probe("fundamental")
def _fundamental(m):
    b = m.get_fundamental_context(STOCK, budget_seconds=25)
    blocks = b.get("blocks") or {}
    return {"status": b.get("status"), "block_status": {n: x.get("status") for n, x in blocks.items() if isinstance(x, dict)}}


@probe("belong_boards")
def _boards(m):
    boards = m.get_belong_boards(STOCK)
    return {"count": len(boards or [])}


@probe("main_indices")
def _indices(m):
    data = m.get_main_indices("cn")
    return {"count": len(data or [])}


@probe("market_stats")
def _stats(m):
    d = m.get_market_stats(purpose="diag")
    return {"keys": sorted(d.keys())[:10]}


@probe("sector_rankings")
def _sector(m):
    top, bottom = m.get_sector_rankings(5)
    return {"top": len(top or []), "bottom": len(bottom or [])}


@probe("concept_rankings")
def _concept(m):
    top, bottom = m.get_concept_rankings(5)
    return {"top": len(top or []), "bottom": len(bottom or [])}


@probe("hot_stocks")
def _hot(m):
    data = m.get_hot_stocks(10)
    return {"count": len(data or [])}


@probe("limit_up_pool")
def _limitup(m):
    data = m.get_limit_up_pool(n=20)
    return {"count": len(data or [])}


@probe("news_search")
def _news(m):
    from src.search_service import get_search_service

    svc = get_search_service()
    return {"available": bool(svc.is_available), "providers": [p.name for p in getattr(svc, "_providers", [])]}


def _compact(value: Any) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except Exception:  # noqa: BLE001
        text = repr(value)
    text = text.replace("\r", " ").replace("\n", " ")
    if len(text) > MAX_ANNOTATION_CHARS:
        text = text[: MAX_ANNOTATION_CHARS - 1] + "…"
    return text


def test_diag_live_data_blocks(capfd):  # noqa: D401 - 临时诊断，只记录事实不断言
    def _emit(line: str) -> None:
        """直写 stdout（绕过 pytest 捕获），让 Actions 生成可经 API 读取的注解。"""
        try:
            with capfd.disabled():
                print(line, flush=True)
        except Exception:  # noqa: BLE001
            pass

    try:
        from data_provider.base import DataFetcherManager

        manager = DataFetcherManager()
        started = time.time()
        pool = ThreadPoolExecutor(max_workers=6)

        pending: List[Tuple[Any, str]] = []
        for name, fn in _PROBES.items():
            pending.append((pool.submit(fn, manager), name))

        results: Dict[str, str] = {}

        def _record(fut, name):
            try:
                value = fut.result(timeout=0.05)
                results[name] = _compact(value)
            except FutureTimeoutError:
                pass
            except Exception as exc:  # noqa: BLE001
                detail = f"{type(exc).__name__}: {exc}"
                try:
                    frame = traceback.extract_tb(exc.__traceback__)[-1]
                    detail += f" @{frame.name}"
                except Exception:  # noqa: BLE001
                    pass
                results[name] = _compact({"EXC": detail[:180]})

        while pending and (DEADLINE_SECONDS - (time.time() - started)) > 2:
            still = []
            for fut, name in pending:
                if fut.done():
                    _record(fut, name)
                else:
                    still.append((fut, name))
            pending = still
            if pending:
                time.sleep(0.5)

        for _fut, name in pending:
            results[name] = _compact({"TIMEOUT": f">{DEADLINE_SECONDS:.0f}s"})
            pending = []

        pool.shutdown(wait=False, cancel_futures=True)

        for name in _PROBES:
            _emit(f"::error title=DIAG {name}::{_compact(results.get(name, 'NOT_RUN'))}")

        fetchers = ",".join(manager.available_fetchers)
        _emit(f"::error title=DIAG meta::fetchers={fetchers[:180]} elapsed={time.time() - started:.0f}s stock={STOCK}")

        rows = ["| 块 | 结果 |", "|---|---|"]
        for name in _PROBES:
            rows.append(f"| {name} | {results.get(name, 'NOT_RUN')} |")

        summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary_path:
            with open(summary_path, "a", encoding="utf-8") as fh:
                fh.write(
                    f"\n## 🔍 数据块诊断（{STOCK}）\n\n"
                    + "\n".join(rows)
                    + f"\n\n- fetchers: {fetchers}\n"
                )
    except Exception as exc:  # noqa: BLE001 - 诊断自身异常也要外显
        _emit(f"::error title=DIAG internal::{_compact({'INTERNAL_EXC': f'{type(exc).__name__}: {exc}'})}")
