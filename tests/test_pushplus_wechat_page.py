# -*- coding: utf-8 -*-
"""章鱼 AI 全景分析 微信推送页面渲染器测试。"""
import unittest

from src.pushplus_wechat_page import (
    FOOTER_ABOUT,
    FOOTER_AUTHOR,
    FOOTER_DISCLAIMER,
    PAGE_SUBTITLE,
    PAGE_TITLE,
    render_wechat_page,
)


class TestRenderWechatPage(unittest.TestCase):
    def test_single_page_contains_title_subtitle_footer(self):
        html = render_wechat_page("# 标题\n\n正文 **重点**")

        self.assertIn(PAGE_TITLE, html)
        self.assertIn(PAGE_SUBTITLE, html)
        self.assertIn(FOOTER_AUTHOR, html)
        self.assertIn(FOOTER_DISCLAIMER, html)
        self.assertIn("Claude", FOOTER_ABOUT)
        self.assertIn("<strong>重点</strong>", html)
        # 荧光绿 × 黑 主题关键色
        self.assertIn("#00C46A", html)
        self.assertIn("#3DFF8B", html)
        self.assertIn("#EFF1ED", html)

    def test_title_has_no_pushplus_or_time(self):
        html = render_wechat_page("正文")
        self.assertNotIn("pushplus", html.lower().replace("octo", ""))
        self.assertNotIn("股票分析报告", html)
        self.assertEqual(PAGE_TITLE, "章鱼 AI 全景分析")

    def test_middle_part_skips_subtitle_and_footer(self):
        html = render_wechat_page("正文", part_index=2, part_total=3)
        self.assertNotIn(PAGE_SUBTITLE, html)
        self.assertNotIn(FOOTER_AUTHOR, html)
        self.assertIn("PAGE 2 / 3", html)

    def test_last_part_has_footer(self):
        html = render_wechat_page("正文", part_index=3, part_total=3)
        self.assertIn(FOOTER_AUTHOR, html)
        self.assertIn("PAGE 3 / 3", html)

    def test_markdown_table_rendering(self):
        md = "| A | B |\n|---|---|\n| 1 | 2 |"
        html = render_wechat_page(md)
        self.assertIn("<table>", html)
        self.assertIn("<td>1</td>", html)


if __name__ == "__main__":
    unittest.main()
