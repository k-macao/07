# -*- coding: utf-8 -*-
"""临时诊断测试（勿合并）：在 CI 运行器上复现推送报告的数据块抓取。

背景：最新一次 ds-day 推送的微信页面出现「数据缺失」。沙箱无法直连
境内数据源，也拉不到 Actions 制品，因此借 CI 运行器（外外网畅通）现场
抓一遍全部数据块。所有探测在**独立子进程**中执行（不污染 pytest 进程
的全局单例/缓存/线程），结论通过 ``::error::`` 工作流命令写成注解，
可用 GitHub API 读回。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

STOCK = os.environ.get("DIAG_STOCK", "600519")
WORKER = r"""
import json, os, sys, time
sys.path.insert(0, os.getcwd())

STOCK = os.environ.get("DIAG_STOCK", "600519")

from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FTimeout

PROBES = {}

def probe(name):
    def deco(fn):
        PROBES[name] = fn
        return fn
    return deco

@probe("news_search")
def _news(m):
    from src.search_service import get_search_service
    svc = get_search_service()
    return {"available": bool(svc.is_available),
            "providers": [p.name for p in getattr(svc, "_providers", [])]}

@probe("stock_name")
def _stock_name(m):
    return m.get_stock_name(STOCK, allow_realtime=True)

@probe("concept_rankings")
def _concept(m):
    top, bottom = m.get_concept_rankings(5)
    return {"top": len(top or []), "bottom": len(bottom or [])}

@probe("hot_stocks")
def _hot(m):
    return {"count": len(m.get_hot_stocks(10) or [])}

@probe("limit_up_pool")
def _limitup(m):
    return {"count": len(m.get_limit_up_pool(n=20) or [])}

@probe("market_stats")
def _stats(m):
    d = m.get_market_stats(purpose="diag")
    return {"keys": sorted(d.keys())[:8]}

@probe("realtime_quote")
def _quote(m):
    q = m.get_realtime_quote(STOCK, log_final_failure=False)
    if q is None:
        return "None"
    keys = "price change_pct volume_ratio turnover_rate pe_ratio pb_ratio source".split()
    return {k: getattr(q, k, None) for k in keys}

@probe("daily_kline")
def _daily(m):
    df, source = m.get_daily_data(STOCK, days=120)
    if df is None or df.empty:
        return {"source": source, "rows": 0}
    tail = df.tail(1).to_dict("records")[0]
    return {"source": source, "rows": len(df), "last": str(tail.get("date")),
            "ma": {k: tail.get(k) for k in ("ma5", "ma10", "ma20") if k in tail}}

@probe("chip_distribution")
def _chip(m):
    c = m.get_chip_distribution(STOCK)
    if c is None:
        return "None"
    return {k: getattr(c, k, None) for k in ("profit_ratio", "avg_cost", "concentration", "source")}

@probe("capital_flow")
def _capital(m):
    b = m.get_capital_flow_context(STOCK, budget_seconds=20)
    return {"status": b.get("status"), "data": b.get("data"), "errors": (b.get("errors") or [])[:3]}

@probe("dragon_tiger")
def _dragon(m):
    b = m.get_dragon_tiger_context(STOCK, budget_seconds=20)
    return {"status": b.get("status"), "data": b.get("data")}

@probe("fundamental")
def _fundamental(m):
    b = m.get_fundamental_context(STOCK, budget_seconds=25)
    return {"status": b.get("status"), "keys": sorted((b.get("blocks") or {}).keys()),
            "statuses": {n: x.get("status") for n, x in (b.get("blocks") or {}).items() if isinstance(x, dict)}}

@probe("belong_boards")
def _boards(m):
    return {"count": len(m.get_belong_boards(STOCK) or [])}

@probe("main_indices")
def _indices(m):
    return {"count": len(m.get_main_indices("cn") or [])}

@probe("sector_rankings")
def _sector(m):
    top, bottom = m.get_sector_rankings(5)
    return {"top": len(top or []), "bottom": len(bottom or [])}

DEADLINE = 80.0

def main():
    from data_provider.base import DataFetcherManager
    m = DataFetcherManager()
    started = time.time()
    pool = ThreadPoolExecutor(max_workers=6)
    pending = [(pool.submit(fn, m), name) for name, fn in PROBES.items()]
    results = {}
    def record(fut, name):
        try:
            results[name] = fut.result(timeout=0.05)
        except FTimeout:
            pass
        except Exception as exc:
            results[name] = {"EXC": f"{type(exc).__name__}: {exc}"[:170]}
    while pending and (DEADLINE - (time.time() - started)) > 2:
        still = []
        for fut, name in pending:
            if fut.done():
                record(fut, name)
            else:
                still.append((fut, name))
        pending = still
        if pending:
            time.sleep(0.5)
    for _f, name in pending:
        results[name] = {"TIMEOUT": f">{DEADLINE:.0f}s"}
    pool.shutdown(wait=False, cancel_futures=True)
    try:
        m.close()
    except Exception:
        pass
    results["_meta"] = {"fetchers": m.available_fetchers, "elapsed": round(time.time() - started)}
    print("###DIAG_JSON###")
    print(json.dumps(results, ensure_ascii=False, default=str))

main()
"""


def _compact(value, limit: int = 200) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except Exception:  # noqa: BLE001
        text = repr(value)
    text = text.replace("\r", " ").replace("\n", " ")
    if len(text) > limit:
        text = text[: limit - 1] + "…"
    return text


def test_diag_live_data_blocks(capfd):  # noqa: D401 - 临时诊断，只记录事实不断言
    def _emit(line: str) -> None:
        try:
            with capfd.disabled():
                print(line, flush=True)
        except Exception:  # noqa: BLE001
            pass

    try:
        env = dict(os.environ)
        env["DIAG_STOCK"] = STOCK
        proc = subprocess.run(
            [sys.executable, "-c", WORKER],
            timeout=110,
            capture_output=True,
            text=True,
            cwd=os.getcwd(),
            env=env,
        )
        payload = {}
        if "###DIAG_JSON###" in proc.stdout:
            json_part = proc.stdout.split("###DIAG_JSON###", 1)[1].strip()
            for candidate in (json_part, json_part.splitlines()[0] if json_part else ""):
                try:
                    payload = json.loads(candidate)
                    break
                except Exception:  # noqa: BLE001
                    continue
        if not payload:
            _emit(f"::error title=DIAG worker-failed::rc={proc.returncode} stderr={_compact(proc.stderr[-300:])}")
            return

        # 1) 汇总注解（最重要，防止超过 10 条上限被截）
        summary = {name: _compact(val, 90) for name, val in payload.items()}
        _emit(f"::error title=DIAG ALL::{_compact(summary, 1800)}")

        # 2) 每块单独注解（最多再补 9 条）
        for name in list(payload)[:9]:
            _emit(f"::error title=DIAG {name}::{_compact(payload[name])}")
    except subprocess.TimeoutExpired:
        _emit("::error title=DIAG worker-failed::timeout>110s")
    except Exception as exc:  # noqa: BLE001
        _emit(f"::error title=DIAG internal::{_compact({'EXC': f'{type(exc).__name__}: {exc}'})}")
