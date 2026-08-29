# -*- coding: utf-8 -*-
"""免密钥新闻兜底数据源（东方财富个股新闻）测试。"""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

import pandas as pd

from src.news_fallback import (
    fetch_akshare_news_rows,
    fetch_stock_news_via_akshare,
    format_news_rows,
    is_cn_stock_code,
)
from src.search_service import SearchResponse, SearchResult
from src.core.pipeline import StockAnalysisPipeline


def _fake_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "关键词": "600519",
                "新闻标题": "贵州茅台发布半年报",
                "新闻内容": "公司上半年营业收入同比增长" + "x" * 300,
                "发布时间": "2026-08-28 09:30:00",
                "文章来源": "上海证券报",
                "新闻链接": "https://example.com/1",
            },
            {
                "关键词": "600519",
                "新闻标题": "白酒板块走强",
                "新闻内容": "今日白酒板块集体上涨。",
                "发布时间": "2026-08-27 15:05:00",
                "文章来源": "财联社",
                "新闻链接": "https://example.com/2",
            },
        ]
    )


class TestIs_cn_stock_code(unittest.TestCase):
    def test_cn_codes(self):
        self.assertTrue(is_cn_stock_code("600519"))
        self.assertTrue(is_cn_stock_code(" 300750 "))

    def test_non_cn_codes(self):
        for code in ("AAPL", "00700", "SH600519", "", None):
            self.assertFalse(is_cn_stock_code(code))


class TestFetchAkshareNewsRows(unittest.TestCase):
    def test_returns_rows_with_mocked_akshare(self):
        with patch("akshare.stock_news_em", return_value=_fake_df()):
            rows = fetch_akshare_news_rows("600519", max_results=5, timeout_seconds=10)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["title"], "贵州茅台发布半年报")
        self.assertEqual(rows[0]["snippet"], "公司上半年营业收入同比增长" + "x" * 187)
        self.assertEqual(rows[1]["source"], "财联社")

    def test_non_cn_code_short_circuits(self):
        with patch("akshare.stock_news_em", side_effect=AssertionError("should not call")):
            self.assertEqual(fetch_akshare_news_rows("AAPL"), [])

    def test_failure_returns_empty(self):
        with patch("akshare.stock_news_em", side_effect=RuntimeError("boom")):
            self.assertEqual(fetch_akshare_news_rows("600519", timeout_seconds=5), [])

    def test_timeout_returns_empty(self):
        import time

        def _slow(*_a, **_k):
            time.sleep(3)
            return _fake_df()

        with patch("akshare.stock_news_em", side_effect=_slow):
            self.assertEqual(
                fetch_akshare_news_rows("600519", timeout_seconds=0.2),
                [],
            )


class TestFormatNewsRows(unittest.TestCase):
    def test_format_contains_expected_fields(self):
        rows = [
            {
                "title": "标题A",
                "snippet": "摘要A",
                "source": "来源A",
                "published_date": "2026-08-28 09:30:00",
                "url": "u",
            }
        ]
        text = format_news_rows(rows, "600519", stock_name="贵州茅台")
        self.assertIn("贵州茅台(600519) 个股新闻", text)
        self.assertIn("【来源A】标题A (2026-08-28 09:30:00)", text)
        self.assertIn("摘要A", text)


class TestFetchStockNewsViaAkshare(unittest.TestCase):
    def test_returns_text_and_count(self):
        with patch("akshare.stock_news_em", return_value=_fake_df()):
            result = fetch_stock_news_via_akshare("600519", stock_name="贵州茅台")
        self.assertIsNotNone(result)
        text, count = result
        self.assertEqual(count, 2)
        self.assertIn("贵州茅台发布半年报", text)

    def test_none_when_empty(self):
        with patch("akshare.stock_news_em", return_value=pd.DataFrame()):
            self.assertIsNone(fetch_stock_news_via_akshare("600519"))


class TestSearchServiceFallback(unittest.TestCase):
    def test_search_stock_news_falls_back_to_akshare(self):
        from src import search_service as ss

        service = ss.SearchService(searxng_public_instances_enabled=False)  # 未配置任何 key
        self.assertFalse(service.is_available)

        fake_rows = [
            {
                "title": "兜底新闻",
                "snippet": "内容",
                "source": "东方财富",
                "published_date": "2026-08-28",
                "url": "https://example.com/n",
            }
        ]
        with patch.object(ss, "fetch_akshare_news_rows", return_value=fake_rows):
            response = service.search_stock_news("600519", "贵州茅台", max_results=5)

        self.assertTrue(response.success)
        self.assertEqual(response.provider, "AkshareEM")
        self.assertEqual(len(response.results), 1)
        self.assertEqual(response.results[0].title, "兜底新闻")
        self.assertIn("兜底新闻", response.to_context())


class TestPipelineIntelNewsGating(unittest.TestCase):
    """回归测试：空情报结果不应抑制东财兜底换源。

    历史缺陷：``format_intel_report`` 即使所有维度都为空也会返回非空占位文本，
    导致 ``news_context`` 恒为真值，下方的东财（akshare）兜底换源被跳过，
    舆情/新闻块最终显示「数据缺失 / 未找到相关信息」。修复后，只有当存在
    至少一条可用结果时才生成情报上下文，否则返回 (None, None) 走兜底。
    """

    def _make_response(self, results, query="贵州茅台 最新消息"):
        return SearchResponse(
            query=query,
            results=results,
            provider="mock",
            success=True,
        )

    def _make_result(self, title="新闻标题", published="2026-08-28"):
        return SearchResult(
            title=title,
            snippet="摘要内容",
            url="https://example.com/1",
            source="来源",
            published_date=published,
        )

    def _build_pipeline(self):
        pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
        pipeline.search_service = MagicMock()
        pipeline.db = MagicMock()
        pipeline.query_id = None
        pipeline.query_source = "test"
        pipeline.source_message = None
        return pipeline

    def test_all_dimensions_empty_returns_none_to_trigger_fallback(self):
        pipeline = self._build_pipeline()
        intel = {
            "latest_news": self._make_response([]),
            "announcements": self._make_response([]),
            "risk_check": self._make_response([]),
            "earnings": self._make_response([]),
            "industry": self._make_response([]),
        }

        news_context, news_result_count = pipeline._build_news_context_from_intel(
            intel,
            code="600519",
            stock_name="贵州茅台",
            query_id="q1",
        )

        self.assertIsNone(news_context)
        self.assertIsNone(news_result_count)
        # 空结果不应生成占位上下文，也不应持久化任何维度
        pipeline.search_service.format_intel_report.assert_not_called()
        pipeline.db.save_news_intel.assert_not_called()

    def test_empty_intel_dict_returns_none(self):
        pipeline = self._build_pipeline()
        news_context, news_result_count = pipeline._build_news_context_from_intel(
            {},
            code="600519",
            stock_name="贵州茅台",
            query_id="q1",
        )
        self.assertIsNone(news_context)
        self.assertIsNone(news_result_count)

    def test_with_usable_results_builds_context_and_persists(self):
        pipeline = self._build_pipeline()
        pipeline.search_service.format_intel_report.return_value = "【贵州茅台 情报搜索结果】..."
        pipeline._build_query_context = MagicMock(return_value={"query_id": "q1"})
        intel = {
            "latest_news": self._make_response([self._make_result()]),
            "risk_check": self._make_response([]),
        }

        news_context, news_result_count = pipeline._build_news_context_from_intel(
            intel,
            code="600519",
            stock_name="贵州茅台",
            query_id="q1",
        )

        self.assertEqual(news_context, "【贵州茅台 情报搜索结果】...")
        self.assertEqual(news_result_count, 1)
        pipeline.search_service.format_intel_report.assert_called_once()
        # 仅成功且有结果的维度被持久化
        self.assertEqual(pipeline.db.save_news_intel.call_count, 1)


if __name__ == "__main__":
    unittest.main()
