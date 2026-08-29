# -*- coding: utf-8 -*-
"""Static checks for the push design in 00-daily-analysis.yml.

The daily workflow deliberately pushes only one page: the PushPlus
「电子杂志 × 电子墨水」WeChat long page. The analysis step runs without any
notification secrets, and a dedicated post-analysis step injects just
PUSHPLUS_TOKEN / PUSHPLUS_TOPIC and calls scripts/pushplus_push_report.py.
"""

from pathlib import Path

import yaml

from scripts.generate_notification_actions_env_table import (
    extract_managed_block,
    generate_table,
    load_daily_analysis_env,
    normalize_markdown_block,
)


ROOT_DIR = Path(__file__).resolve().parent.parent
WORKFLOW_PATH = ROOT_DIR / ".github/workflows/00-daily-analysis.yml"
NOTIFICATIONS_DOC_PATH = ROOT_DIR / "docs/notifications.md"

PUSH_STEP_NAME = "推送微信杂志页面（PushPlus 竖版长页）"


def _load_workflow() -> dict:
    return yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))


def _steps() -> list:
    return _load_workflow()["jobs"]["analyze"]["steps"]


def _step_named(name: str) -> dict:
    steps = _steps()
    step = next((s for s in steps if s.get("name") == name), None)
    available = [s.get("name", "<unnamed>") for s in steps]
    assert step is not None, f"Missing step {name!r}; available: {available}"
    return step


# 分析步骤：跑分析，不注入任何通知渠道密钥 ----------------------------------

# 本工作流不再推送的渠道/路由/降噪 key（改由 PushPlus 杂志页统一推送）
REMOVED_NOTIFICATION_KEYS = {
    # 多渠道凭证
    "WECHAT_WEBHOOK_URL",
    "FEISHU_WEBHOOK_URL",
    "FEISHU_WEBHOOK_SECRET",
    "DINGTALK_WEBHOOK_URL",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
    "EMAIL_SENDER",
    "EMAIL_PASSWORD",
    "EMAIL_RECEIVERS",
    "PUSHOVER_USER_KEY",
    "PUSHOVER_API_TOKEN",
    "NTFY_URL",
    "NTFY_TOKEN",
    "GOTIFY_URL",
    "GOTIFY_TOKEN",
    "CUSTOM_WEBHOOK_URLS",
    "DISCORD_WEBHOOK_URL",
    "DISCORD_BOT_TOKEN",
    "SLACK_WEBHOOK_URL",
    "SLACK_BOT_TOKEN",
    "ASTRBOT_URL",
    "ASTRBOT_TOKEN",
    "SERVERCHAN3_SENDKEY",
    "FEISHU_APP_ID",
    "FEISHU_APP_SECRET",
    # 路由 / 降噪策略
    "NOTIFICATION_REPORT_CHANNELS",
    "NOTIFICATION_ALERT_CHANNELS",
    "NOTIFICATION_SYSTEM_ERROR_CHANNELS",
    "NOTIFICATION_QUIET_HOURS",
    "NOTIFICATION_MIN_SEVERITY",
    "NOTIFICATION_DAILY_DIGEST_ENABLED",
    # 单股逐只通知开关
    "SINGLE_STOCK_NOTIFY",
}


def _analysis_env() -> dict:
    return _step_named("执行股票分析").get("env", {})


def test_analysis_step_injects_no_notification_secrets() -> None:
    env = _analysis_env()
    leaked = sorted(REMOVED_NOTIFICATION_KEYS & set(env))
    assert not leaked, f"分析步骤不应再注入这些通知 key: {leaked}"


def test_analysis_step_has_no_pushplus_token() -> None:
    # 分析阶段不推送，PUSHPLUS_TOKEN 只允许出现在独立推送步骤
    env = _analysis_env()
    assert "PUSHPLUS_TOKEN" not in env


# 推送步骤：只推 PushPlus 杂志页 -------------------------------------------

def _push_env() -> dict:
    return _step_named(PUSH_STEP_NAME).get("env", {})


def test_push_step_injects_only_pushplus_keys() -> None:
    env = _push_env()
    assert env.get("PUSHPLUS_TOKEN") == "${{ secrets.PUSHPLUS_TOKEN }}"
    assert "PUSHPLUS_TOPIC" in env
    # 推送步骤不允许夹带其他渠道密钥
    leaked = sorted(REMOVED_NOTIFICATION_KEYS & set(env))
    assert not leaked, f"推送步骤只能有 PushPlus key，多出: {leaked}"


def test_push_step_runs_pushplus_script() -> None:
    run = _step_named(PUSH_STEP_NAME).get("run", "")
    assert "scripts/pushplus_push_report.py" in run
    assert "--dir reports/" in run
    assert "--channel pushplus" in run


def test_push_step_runs_after_analysis() -> None:
    names = [s.get("name") for s in _steps()]
    assert names.index("执行股票分析") < names.index(PUSH_STEP_NAME)


# 定时：工作日北京时间 17:00（UTC 09:00）----------------------------------

def test_schedule_is_weekday_1700_beijing() -> None:
    workflow = _load_workflow()
    on_trigger = workflow.get(True, workflow.get("on"))
    crons = [entry["cron"] for entry in on_trigger["schedule"]]
    assert "0 9 * * 1-5" in crons
    # 旧的 18:00 (UTC 10:00) 定时不应再保留
    assert "0 10 * * 1-5" not in crons


# 文档表与 workflow 保持一致 -----------------------------------------------

def test_notification_actions_env_table_matches_generated_output() -> None:
    current = extract_managed_block(NOTIFICATIONS_DOC_PATH.read_text(encoding="utf-8"))
    expected = generate_table()

    assert normalize_markdown_block(current) == normalize_markdown_block(expected)


def test_aggregated_env_exposes_pushplus_keys_for_table() -> None:
    # 汇总各步骤 env 后（用于生成文档表），PushPlus 两个 key 必须在
    env = load_daily_analysis_env()
    assert "PUSHPLUS_TOKEN" in env
    assert "PUSHPLUS_TOPIC" in env
