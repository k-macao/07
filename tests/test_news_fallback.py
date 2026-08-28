# -*- coding: utf-8 -*-
"""免密钥新闻兜底数据源（东方财富个股新闻）测试。"""
from __future__ import annotations

import unittest
from unittest.mock import patch

import pandas as pd

from src.news_fallback import (
    fetch_akshare_news_rows,
    fetch_stock_news_via_akshare,
    format_news_rows,
    is_cn_stock_code,
)


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


if __name__ == "__main__":
    unittest.main()
