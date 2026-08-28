# -*- coding: utf-8 -*-
"""临时诊断测试（勿合并）：在 CI 运行器上复现推送报告的数据块抓取。

背景：最新一次 ds-day 推送的微信页面出现「数据缺失」。沙箱无法直连
境内数据源，也拉不到 Actions 制品，因此借 CI 运行器（外网畅通）现场
抓一遍 600519 的全部数据块，把每个块的状态写入 GITHUB_STEP_SUMMARY，
再通过运行页读取结论。
"""
from __future__ import annotations

import os
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from typing import Any, Callable, Dict, List, Tuple

STOCK = os.environ.get("DIAG_STOCK", "600519")
DEADLINE_SECONDS = 95.0  # pytest --timeout=120 硬限

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
        return None
    keys = (
        "price open high low change_pct volume volume_ratio turnover_rate "
        "pe_ratio pb_ratio total_market_cap change_5d change_20d change_60d "
        "volume_ratio_desc source"
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
        "ma_sample": {k: tail.get(k) for k in ("ma5", "ma10", "ma20") if k in tail},
    }


@probe("chip_distribution")
def _chip(m):
    c = m.get_chip_distribution(STOCK)
    if c is None:
        return None
    return {
        k: getattr(c, k, None)
        for k in ("profit_ratio", "avg_cost", "90_cost_low", "90_cost_high", "concentration", "source")
    }


@probe("capital_flow")
def _capital(m):
    b = m.get_capital_flow_context(STOCK, budget_seconds=25)
    return {
        "status": b.get("status"),
        "data": b.get("data"),
        "errors": b.get("errors"),
        "chain": b.get("source_chain"),
    }


@probe("dragon_tiger")
def _dragon(m):
    b = m.get_dragon_tiger_context(STOCK, budget_seconds=25)
    return {"status": b.get("status"), "data": b.get("data"), "errors": b.get("errors")}


@probe("fundamental")
def _fundamental(m):
    b = m.get_fundamental_context(STOCK, budget_seconds=35)
    blocks = b.get("blocks") or {}
    out = {"status": b.get("status"), "block_status": {}}
    for name, blk in blocks.items():
        if isinstance(blk, dict):
            out["block_status"][name] = blk.get("status")
    return out


@probe("belong_boards")
def _boards(m):
    boards = m.get_belong_boards(STOCK)
    return {"count": len(boards or []), "first": (boards or [{}])[0]}


@probe("main_indices")
def _indices(m):
    data = m.get_main_indices("cn")
    return {"count": len(data or []), "names": [d.get("name") for d in (data or [])[:6]]}


@probe("market_stats")
def _stats(m):
    d = m.get_market_stats(purpose="diag")
    return {"keys": sorted(d.keys())[:12], "up": d.get("up_count"), "down": d.get("down_count")}


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
    return {
        "available": bool(svc.is_available),
        "providers": getattr(svc, "provider_names", None),
    }


def _fmt(value: Any) -> str:
    text = repr(value)
    if len(text) > 600:
        text = text[:600] + "…"
    return text.replace("|", "\\|").replace("\n", " ")


def test_diag_live_data_blocks():  # noqa: D401 - 临时诊断，只记录事实不断言
    from data_provider.base import DataFetcherManager

    manager = DataFetcherManager()
    started = time.time()
    pool = ThreadPoolExecutor(max_workers=6)

    pending: List[Tuple[Any, str, float]] = []
    for name, fn in _PROBES.items():
        pending.append((pool.submit(fn, manager), name, time.time()))

    rows: List[str] = ["| 块 | 结果 |", "|---|---|"]

    def _record_done(fut, name, t0):
        try:
            value = fut.result(timeout=0.05)
            rows.append(f"| {name} | OK {_fmt(value)} |")
        except Exception as exc:  # noqa: BLE001
            detail = f"{type(exc).__name__}: {exc}"
            try:
                frame = traceback.extract_tb(exc.__traceback__)[-1]
                detail += f" @{frame.name}"
            except Exception:  # noqa: BLE001
                pass
            rows.append(f"| {name} | EXC {_fmt(detail)} |")

    while pending and (DEADLINE_SECONDS - (time.time() - started)) > 2:
        still: List[Tuple[Any, str, float]] = []
        for fut, name, t0 in pending:
            if fut.done():
                _record_done(fut, name, t0)
            else:
                still.append((fut, name, t0))
        pending = still
        if pending:
            time.sleep(0.5)

    for fut, name, _t0 in pending:
        rows.append(f"| {name} | TIMEOUT(>{DEADLINE_SECONDS:.0f}s) |")

    pool.shutdown(wait=False, cancel_futures=True)

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as fh:
            fh.write(
                f"\n## 🔍 数据块诊断（{STOCK}）\n\n"
                + "\n".join(rows)
                + f"\n\n- 总耗时 {time.time() - started:.1f}s\n- fetchers: "
                + ", ".join(manager.available_fetchers)
                + "\n"
            )
    print("\n".join(rows))
