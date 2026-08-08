# -*- coding: utf-8 -*-
"""
PushPlus 报告推送脚本

将指定目录下的 Markdown 报告文件推送到 PushPlus（微信公众号）。
长内容由 PushplusSender 自动按字节分批发送，避免单条消息超长。

用法示例：
    python scripts/pushplus_push_report.py --dir reports/ --max-chars 100000 --channel pushplus

环境变量：
    PUSHPLUS_TOKEN  PushPlus 用户令牌（必填）
    PUSHPLUS_TOPIC  PushPlus 群组编码（可选，一对多推送）

退出码：
    0  全部报告推送成功（或目录为空）
    1  参数错误 / 未配置 token / 存在推送失败
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Tuple

# 允许以 `python scripts/pushplus_push_report.py` 直接运行
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

logger = logging.getLogger("pushplus_push_report")

# PushPlus 单条消息建议上限（字节）。脚本按字符预切分时以此换算为保守的字符预算；
# 实际发送时 PushplusSender 仍会按 pushplus_max_bytes 做二次字节级分批。
DEFAULT_PUSHPLUS_MAX_BYTES = 20000


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="将目录中的 Markdown 报告推送到 PushPlus（支持长内容分段）",
    )
    parser.add_argument(
        "--dir",
        required=True,
        help="报告目录（读取其中的 .md 文件）",
    )
    parser.add_argument(
        "--max-chars",
        type=int,
        default=100000,
        help="单个报告推送的字符数上限（超出会按段落预切分；默认 100000）",
    )
    parser.add_argument(
        "--channel",
        default="pushplus",
        choices=("pushplus",),
        help="推送渠道（当前仅支持 pushplus，保留参数以兼容工作流）",
    )
    parser.add_argument(
        "--pattern",
        default="*.md",
        help="报告文件匹配模式（默认 *.md）",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="输出调试日志",
    )
    return parser.parse_args()


def _collect_report_files(report_dir: Path, pattern: str) -> List[Path]:
    if not report_dir.exists() or not report_dir.is_dir():
        raise FileNotFoundError(f"报告目录不存在或不是目录: {report_dir}")

    files = sorted(
        (p for p in report_dir.glob(pattern) if p.is_file()),
        key=lambda p: p.stat().st_mtime,
    )
    return files


def _split_by_char_budget(content: str, max_chars: int) -> List[str]:
    """在不切断段落/标题的前提下，按字符预算把长报告预切分成多段。

    实际发送仍由 PushplusSender 做字节级分批，这里只是把超长报告控制在合理大小，
    避免一次性把整份长报告交给发送层。
    """
    if max_chars <= 0 or len(content) <= max_chars:
        return [content]

    # 优先按二级及以上标题切分，保证每段结构完整
    sections: List[str] = []
    current: List[str] = []
    current_len = 0
    for line in content.splitlines(keepends=True):
        if line.startswith("#") and current and current_len + len(line) > max_chars:
            sections.append("".join(current))
            current = [line]
            current_len = len(line)
        else:
            current.append(line)
            current_len += len(line)
    if current:
        sections.append("".join(current))

    # 若某段仍超长（例如没有标题），再按行强制切分
    chunks: List[str] = []
    for section in sections:
        if len(section) <= max_chars:
            chunks.append(section)
            continue
        buf: List[str] = []
        buf_len = 0
        for line in section.splitlines(keepends=True):
            if buf and buf_len + len(line) > max_chars:
                chunks.append("".join(buf))
                buf = [line]
                buf_len = len(line)
            else:
                buf.append(line)
                buf_len += len(line)
        if buf:
            chunks.append("".join(buf))
    return chunks


def _push_file(
    sender: "PushplusSender",
    path: Path,
    max_chars: int,
) -> Tuple[bool, int]:
    """推送单个报告文件。返回 (是否全部成功, 发送段数)。"""
    from src.formatters import strip_hidden_markdown_metadata

    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.error("读取报告失败 %s: %s", path, exc)
        return False, 0

    content = strip_hidden_markdown_metadata(raw).strip()
    if not content:
        logger.warning("报告为空，跳过: %s", path)
        return True, 0

    date_str = datetime.now().strftime("%Y-%m-%d")
    base_title = f"📈 股票分析报告 - {date_str} - {path.stem}"

    chunks = _split_by_char_budget(content, max_chars)
    total = len(chunks)
    success_count = 0

    for idx, chunk in enumerate(chunks, start=1):
        title = base_title if total == 1 else f"{base_title} ({idx}/{total})"
        ok = sender.send_to_pushplus(chunk, title=title)
        if ok:
            success_count += 1
            logger.info("推送成功: %s (%d/%d)", path.name, idx, total)
        else:
            logger.error("推送失败: %s (%d/%d)", path.name, idx, total)

    return success_count == total, total


def main() -> int:
    args = _parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # 延迟导入：让 --help 在依赖未安装时也可用，且配置加载失败能给出清晰日志
    from src.config import get_config
    from src.formatters import strip_hidden_markdown_metadata
    from src.notification_sender.pushplus_sender import PushplusSender

    report_dir = Path(args.dir).expanduser().resolve()
    try:
        files = _collect_report_files(report_dir, args.pattern)
    except FileNotFoundError as exc:
        logger.error(str(exc))
        return 1

    if not files:
        logger.warning("目录 %s 下没有匹配 %s 的报告文件，无需推送", report_dir, args.pattern)
        return 0

    logger.info("待推送报告 %d 个: %s", len(files), ", ".join(p.name for p in files))

    config = get_config()
    if not getattr(config, "pushplus_token", None):
        logger.error("未配置 PUSHPLUS_TOKEN，无法推送到 PushPlus")
        return 1

    sender = PushplusSender(config)

    failed: List[str] = []
    for path in files:
        ok, sent_chunks = _push_file(sender, path, max_chars=args.max_chars)
        if not ok:
            failed.append(path.name)
        elif sent_chunks == 0:
            logger.info("报告 %s 为空内容，已跳过", path.name)

    if failed:
        logger.error("以下报告推送失败: %s", ", ".join(failed))
        return 1

    logger.info("全部报告推送完成（%d 个文件）", len(files))
    return 0


if __name__ == "__main__":
    sys.exit(main())
