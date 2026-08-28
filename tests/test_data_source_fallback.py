# -*- coding: utf-8 -*-
"""数据缺失换源兜底测试：

1. 筹码分布：所有筹码接口失败时，改用「多源日K + 换手衰减」估算；
2. 资金流向：东方财富接口失败时，改走同花顺个股/行业资金流；
3. 概念排行：东方财富接口失败时，改走新浪概念板块。
"""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

from data_provider.base import (
    DataFetcherManager,
    _is_meaningful_chip_distribution,
    estimate_chip_distribution_from_daily,
)


def _synthetic_daily(rows: int = 120, base: float = 10.0) -> pd.DataFrame:
    dates = pd.date_range("2026-01-01", periods=rows, freq="B")
    close = [base + i * 0.05 for i in range(rows)]
    return pd.DataFrame(
        {
            "date": dates,
            "open": [c - 0.03 for c in close],
            "high": [c + 0.06 for c in close],
            "low": [c - 0.06 for c in close],
            "close": close,
            "volume": [1_000_000 + i * 100 for i in range(rows)],
        }
    )


class TestChipEstimateFromDaily(unittest.TestCase):
    def test_estimate_produces_meaningful_chip(self):
        chip = estimate_chip_distribution_from_daily(_synthetic_daily(), "600519")
        self.assertIsNotNone(chip)
        self.assertTrue(_is_meaningful_chip_distribution(chip))
        self.assertEqual(chip.source, "kline_estimate")
        # 单边上行行情：收盘价高于大部分历史成本 → 获利比例高
        self.assertGreater(chip.profit_ratio, 0.6)
        self.assertLess(chip.profit_ratio, 1.0)
        self.assertGreater(chip.avg_cost, 10.0)
        self.assertLess(chip.avg_cost, 16.0)
        for field in ("cost_90_low", "cost_90_high", "cost_70_low", "cost_70_high"):
            self.assertGreater(getattr(chip, field), 0)
        self.assertGreater(chip.concentration_90, 0)
        self.assertLess(chip.concentration_90, 1)
        self.assertLess(chip.concentration_70, chip.concentration_90)

    def test_estimate_needs_min_history(self):
        self.assertIsNone(estimate_chip_distribution_from_daily(_synthetic_daily(20), "600519"))
        self.assertIsNone(estimate_chip_distribution_from_daily(None, "600519"))
        self.assertIsNone(estimate_chip_distribution_from_daily(pd.DataFrame(), "600519"))

    def test_manager_chip_fallback_uses_daily_estimate(self):
        class _BrokenChipFetcher:
            name = "BrokenChipFetcher"
            priority = 0

            def get_chip_distribution(self, stock_code):  # noqa: ANN001
                raise RuntimeError("chip api down")

        manager = DataFetcherManager(fetchers=[_BrokenChipFetcher()])
        daily = _synthetic_daily()

        def _fake_daily(self, stock_code, days=180, **kwargs):  # noqa: ANN001
            return daily, "FakeSource"

        with patch.object(DataFetcherManager, "get_daily_data", _fake_daily), patch(
            "src.config.get_config",
            return_value=SimpleNamespace(enable_chip_distribution=True),
        ):
            chip = manager.get_chip_distribution("600519")

        self.assertIsNotNone(chip)
        self.assertEqual(chip.source, "kline_estimate")
        self.assertTrue(_is_meaningful_chip_distribution(chip))

    def test_manager_chip_fallback_tolerates_daily_failure(self):
        class _BrokenChipFetcher:
            name = "BrokenChipFetcher"
            priority = 0

            def get_chip_distribution(self, stock_code):  # noqa: ANN001
                raise RuntimeError("chip api down")

        manager = DataFetcherManager(fetchers=[_BrokenChipFetcher()])

        def _raise_daily(*args, **kwargs):
            raise RuntimeError("daily down")

        with patch.object(DataFetcherManager, "get_daily_data", _raise_daily), patch(
            "src.config.get_config",
            return_value=SimpleNamespace(enable_chip_distribution=True),
        ):
            chip = manager.get_chip_distribution("600519")

        self.assertIsNone(chip)


class TestCapitalFlowFallback(unittest.TestCase):
    def test_ths_individual_fund_flow_used_when_em_fails(self):
        from data_provider.fundamental_adapter import AkshareFundamentalAdapter

        ths_df = pd.DataFrame(
            [
                {
                    "股票代码": "600519",
                    "股票简称": "贵州茅台",
                    "最新价": 1297.4,
                    "涨跌幅": 0.39,
                    "流入资金": 2_000_000_000.0,
                    "流出资金": 1_500_000_000.0,
                    "净额": 500_000_000.0,
                }
            ]
        )

        adapter = AkshareFundamentalAdapter()

        def _fake_call(*args):  # noqa: ANN001
            candidates = args[-1]
            errors = []
            for func_name, _kwargs in candidates:
                if func_name == "stock_fund_flow_individual":
                    return ths_df, func_name, errors
                errors.append(f"{func_name}:RuntimeError")
            return None, None, errors

        with patch.object(AkshareFundamentalAdapter, "_call_df_candidates", _fake_call):
            payload = adapter.get_capital_flow("600519")

        self.assertEqual(payload["stock_flow"]["main_net_inflow"], 500_000_000.0)
        self.assertIn("capital_stock:stock_fund_flow_individual", payload["source_chain"])

    def test_candidates_include_ths_and_em_rank(self):
        from data_provider.fundamental_adapter import AkshareFundamentalAdapter

        seen = {}

        def _fake_call(*args):  # noqa: ANN001
            candidates = args[-1]
            if "capital" not in seen:
                seen["capital"] = [name for name, _ in candidates]
                return pd.DataFrame({"股票代码": ["600519"], "净额": [1.0]}), "x", []
            seen["sector"] = [name for name, _ in candidates]
            return pd.DataFrame({"板块名称": ["白酒"], "主力净流入": [1.0]}), "y", []

        adapter = AkshareFundamentalAdapter()
        with patch.object(AkshareFundamentalAdapter, "_call_df_candidates", _fake_call):
            adapter.get_capital_flow("600519")

        self.assertIn("stock_fund_flow_individual", seen["capital"])
        self.assertIn("stock_individual_fund_flow_rank", seen["capital"])
        self.assertIn("stock_fund_flow_industry", seen["sector"])


class TestConceptRankingFallback(unittest.TestCase):
    def test_sina_concept_used_when_em_fails(self):
        from data_provider.akshare_fetcher import AkshareFetcher

        sina_df = pd.DataFrame(
            {
                "label": ["gn_1", "gn_2", "gn_3"],
                "板块": ["AI眼镜", "白酒", "机器人"],
                "公司家数": [10, 20, 15],
                "平均价格": [10.0, 50.0, 30.0],
                "涨跌幅": [5.0, -2.0, 3.0],
            }
        )

        fetcher = AkshareFetcher()
        with patch("akshare.stock_board_concept_name_em", side_effect=RuntimeError("em down")), patch(
            "akshare.stock_sector_spot", return_value=sina_df
        ) as mock_sina:
            top, bottom = fetcher.get_concept_rankings(2)

        mock_sina.assert_called_once()
        self.assertEqual([t["name"] for t in top], ["AI眼镜", "机器人"])
        self.assertEqual([b["name"] for b in bottom], ["白酒", "机器人"])
        self.assertEqual(top[0]["change_pct"], 5.0)

    def test_returns_none_when_both_sources_fail(self):
        from data_provider.akshare_fetcher import AkshareFetcher

        fetcher = AkshareFetcher()
        with patch("akshare.stock_board_concept_name_em", side_effect=RuntimeError("em down")), patch(
            "akshare.stock_sector_spot", side_effect=RuntimeError("sina down")
        ):
            result = fetcher.get_concept_rankings(2)

        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
