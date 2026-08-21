# -*- coding: utf-8 -*-
"""
章鱼 AI 全景分析 · 微信推送页面渲染器

参考 Guizang PPT Skill（https://github.com/op7418/guizang-ppt-skill）的
Style A「电子杂志 × 电子墨水」美学，改造成适合微信阅读的竖版长页面：

- 衬线标题（Noto Serif SC）+ 非衬线正文 + 等宽元数据（IBM Plex Mono 系）
- 细黑发丝线（hairline）分隔、杂志式眉题（kicker）
- 荧光绿标题字体，正文黑色，重点字为「黑底荧光绿」高亮块
- 整体浅灰纸感背景，全局字号偏小，荧光绿 × 黑色配搭

输出为 HTML 片段（<section> + 作用域 <style>），配合 PushPlus 的
``template="html"`` 使用，在微信内打开即是完整长页面。
"""
from __future__ import annotations

import html as _html
from typing import List, Optional

import markdown2

# ---------------------------------------------------------------------------
# 页面文案（标题不带渠道名与时间）
# ---------------------------------------------------------------------------
PAGE_TITLE = "章鱼 AI 全景分析"
PAGE_SUBTITLE = "全网 AI 调研境内境外数据，由多个大模型混合部署。"
PAGE_KICKER = "OCTOPUS AI · PANORAMA REPORT"
FOOTER_AUTHOR = "作者：章鱼 AI"
FOOTER_DISCLAIMER = "仅供参考，分析研究"
FOOTER_ABOUT = (
    "全网境内外为你寻找蛛丝马迹 —— 提供全景视野分析，由多模型协同推理决策。"
    "底层所使用的大语言模型（LLM）多模式背后结合使用了多种不同的先进模型，"
    "包括但不限于 Claude、ChatGPT、Gemini、Grok、Qwen 以及 Kimi。"
    "根据不同的资产管理任务需求，更好地发挥各个模型的优势来提供数据支持！[加油]"
)

_MARKDOWN_EXTRAS = ["tables", "fenced-code-blocks", "break-on-newline", "cuddled-lists"]

# 荧光绿 × 黑 × 浅灰 主题变量
_CSS = """
#octo-page{
  background:#EFF1ED;
  color:#101210;
  max-width:640px;
  margin:0 auto;
  padding:18px 16px 30px;
  font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Helvetica Neue","Noto Sans SC","Microsoft YaHei",sans-serif;
  font-size:13px;
  line-height:1.8;
  letter-spacing:.01em;
  -webkit-text-size-adjust:100%;
}
#octo-page .octo-mono{
  font-family:"IBM Plex Mono","SF Mono",SFMono-Regular,Menlo,Consolas,"Liberation Mono",monospace;
}
#octo-page .octo-serif{
  font-family:"Noto Serif SC","Songti SC","STSong",Georgia,"Times New Roman",serif;
}
/* ---- 页眉：杂志刊头 ---- */
#octo-page .octo-rule{border:0;border-top:1px solid #101210;margin:0;}
#octo-page .octo-rule--thick{border-top:3px solid #101210;}
#octo-page .octo-kicker{
  display:flex;justify-content:space-between;align-items:center;
  font-size:9px;letter-spacing:.32em;text-transform:uppercase;
  color:#101210;padding:6px 0;
}
#octo-page .octo-kicker b{
  background:#0A0C0A;color:#3DFF8B;font-weight:600;
  padding:1px 7px;letter-spacing:.22em;
}
#octo-page .octo-title{
  margin:16px 0 6px;
  font-size:23px;line-height:1.35;font-weight:700;
  color:#00C46A;
  letter-spacing:.02em;
}
#octo-page .octo-subtitle{
  margin:0 0 14px;
  font-size:12px;color:#3A3E3A;line-height:1.7;
}
#octo-page .octo-meta{
  display:flex;flex-wrap:wrap;gap:6px;
  font-size:9px;letter-spacing:.18em;text-transform:uppercase;
  color:#101210;margin:10px 0 4px;
}
#octo-page .octo-meta span{
  border:1px solid #101210;padding:1px 7px;
}
#octo-page .octo-meta span.octo-meta--inv{
  background:#0A0C0A;color:#3DFF8B;border-color:#0A0C0A;
}
/* ---- 正文 ---- */
#octo-page .octo-body{padding-top:14px;}
#octo-page .octo-body p{margin:0 0 10px;color:#101210;}
#octo-page .octo-body h1,
#octo-page .octo-body h2,
#octo-page .octo-body h3,
#octo-page .octo-body h4{
  font-family:"Noto Serif SC","Songti SC","STSong",Georgia,serif;
  color:#00C46A;font-weight:700;line-height:1.4;
}
#octo-page .octo-body h1{
  font-size:17px;margin:22px 0 10px;padding-bottom:6px;
  border-bottom:1px solid #101210;
}
#octo-page .octo-body h2{
  font-size:15px;margin:20px 0 8px;padding-left:9px;
  border-left:3px solid #0A0C0A;
}
#octo-page .octo-body h3{font-size:13.5px;margin:16px 0 6px;}
#octo-page .octo-body h4{font-size:12.5px;margin:14px 0 6px;}
#octo-page .octo-body strong{
  background:#0A0C0A;color:#3DFF8B;
  font-weight:600;font-size:.95em;
  padding:0 5px;margin:0 1px;
  border-radius:2px;
  box-decoration-break:clone;-webkit-box-decoration-break:clone;
}
#octo-page .octo-body em{
  font-style:normal;color:#009653;
  border-bottom:1px dashed #00C46A;
}
#octo-page .octo-body a{color:#009653;text-decoration:none;border-bottom:1px solid #3DFF8B;}
#octo-page .octo-body ul,#octo-page .octo-body ol{margin:0 0 10px;padding-left:20px;}
#octo-page .octo-body li{margin:2px 0;}
#octo-page .octo-body li::marker{color:#00C46A;font-weight:700;}
#octo-page .octo-body blockquote{
  margin:0 0 10px;padding:6px 12px;
  border-left:3px solid #0A0C0A;
  background:rgba(10,12,10,.05);
  color:#3A3E3A;font-size:12px;
}
#octo-page .octo-body blockquote p{margin:0;color:#3A3E3A;}
#octo-page .octo-body hr{border:0;border-top:1px solid #101210;margin:16px 0;}
#octo-page .octo-body table{
  width:100%;border-collapse:collapse;
  margin:12px 0;font-size:11.5px;line-height:1.6;
  display:block;overflow-x:auto;
}
#octo-page .octo-body th{
  background:#0A0C0A;color:#3DFF8B;
  font-weight:600;text-align:left;
  padding:4px 8px;border:1px solid #0A0C0A;
  white-space:nowrap;
}
#octo-page .octo-body td{
  padding:4px 8px;border:1px solid #101210;color:#101210;
}
#octo-page .octo-body tr:nth-child(2n) td{background:rgba(10,12,10,.04);}
#octo-page .octo-body code{
  font-family:"IBM Plex Mono","SF Mono",SFMono-Regular,Menlo,Consolas,monospace;
  font-size:11px;background:#0A0C0A;color:#3DFF8B;
  padding:1px 5px;border-radius:2px;
}
#octo-page .octo-body pre{
  background:#0A0C0A;color:#3DFF8B;
  padding:10px 12px;overflow-x:auto;
  font-size:11px;line-height:1.6;margin:0 0 10px;
}
#octo-page .octo-body pre code{background:none;padding:0;}
/* ---- 页脚 ---- */
#octo-page .octo-footer{margin-top:26px;}
#octo-page .octo-footer .octo-author{
  display:flex;justify-content:space-between;align-items:center;
  padding:8px 0 2px;font-size:10px;letter-spacing:.14em;
}
#octo-page .octo-footer .octo-author b{
  background:#0A0C0A;color:#3DFF8B;font-weight:600;
  padding:2px 8px;letter-spacing:.14em;
}
#octo-page .octo-footer .octo-author i{
  font-style:normal;color:#3A3E3A;
}
#octo-page .octo-footer .octo-about{
  margin:10px 0 0;font-size:10.5px;line-height:1.9;color:#3A3E3A;
}
#octo-page .octo-footer .octo-about b{
  background:none;color:#009653;font-weight:600;padding:0;
}
#octo-page .octo-part{
  text-align:right;font-size:9px;letter-spacing:.3em;
  color:#3A3E3A;margin-top:14px;text-transform:uppercase;
}
"""


def render_markdown_body(markdown_text: str) -> str:
    """将 Markdown 正文转成 HTML 片段（不含页面骨架）。"""
    return markdown2.markdown(markdown_text, extras=_MARKDOWN_EXTRAS)


def render_wechat_page(
    markdown_text: str,
    *,
    title: str = PAGE_TITLE,
    subtitle: str = PAGE_SUBTITLE,
    part_index: Optional[int] = None,
    part_total: Optional[int] = None,
) -> str:
    """把 Markdown 报告包装成「电子杂志 × 电子墨水」竖版长页面 HTML。

    Args:
        markdown_text: 报告 Markdown 内容。
        title: 页面主标题（默认「章鱼 AI 全景分析」，不含渠道名与时间）。
        subtitle: 副标题。
        part_index: 分批推送时的当前批次（从 1 开始，可选）。
        part_total: 分批推送时的总批次（可选）。

    Returns:
        HTML 片段（<section> + 作用域 <style>），可直接作为
        PushPlus ``template="html"`` 的 content。
    """
    body_html = render_markdown_body(markdown_text)

    is_first = part_index is None or part_index <= 1
    is_last = part_total is None or part_index is None or part_index >= part_total

    header_parts: List[str] = [
        '<hr class="octo-rule octo-rule--thick"/>',
        '<div class="octo-kicker octo-mono">'
        f"<span>{_html.escape(PAGE_KICKER)}</span><b>MULTI-LLM</b></div>",
        '<hr class="octo-rule"/>',
    ]
    if is_first:
        header_parts += [
            f'<h1 class="octo-title octo-serif">{_html.escape(title)}</h1>',
            f'<p class="octo-subtitle">{_html.escape(subtitle)}</p>',
            '<div class="octo-meta octo-mono">'
            '<span class="octo-meta--inv">境内 × 境外</span>'
            "<span>全网 AI 调研</span>"
            "<span>多模型协同推理</span>"
            "</div>",
            '<hr class="octo-rule"/>',
        ]
    else:
        header_parts += [
            f'<h1 class="octo-title octo-serif">{_html.escape(title)}</h1>',
            '<hr class="octo-rule"/>',
        ]

    footer_parts: List[str] = []
    if is_last:
        footer_parts = [
            '<div class="octo-footer">',
            '<hr class="octo-rule"/>',
            '<div class="octo-author octo-mono">'
            f"<b>{_html.escape(FOOTER_AUTHOR)}</b>"
            f"<i>{_html.escape(FOOTER_DISCLAIMER)}</i></div>",
            '<hr class="octo-rule"/>',
            f'<p class="octo-about">{_html.escape(FOOTER_ABOUT)}</p>',
            '<hr class="octo-rule octo-rule--thick" style="margin-top:12px"/>',
            "</div>",
        ]

    part_marker = ""
    if part_index is not None and part_total is not None and part_total > 1:
        part_marker = (
            f'<div class="octo-part octo-mono">PAGE {part_index} / {part_total}</div>'
        )

    return (
        f"<style>{_CSS}</style>"
        '<section id="octo-page">'
        + "".join(header_parts)
        + f'<div class="octo-body">{body_html}</div>'
        + "".join(footer_parts)
        + part_marker
        + "</section>"
    )
