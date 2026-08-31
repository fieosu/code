"""
Webhook 推送模块

仅支持企业微信机器人：异常时先上传截图拿到 base64 + md5，
再以 image 类型发送告警文本 + 截图。
"""

import base64
import hashlib

import requests

import config
from utils import logger, retry


def _load_image_payload(path: str) -> tuple[str, str]:
    """读取图片文件，返回 (base64 字符串, MD5)。

    只读一次字节、两用派生，避免大图重复 I/O（重试时最多重复读 2×重试次数）。
    """
    with open(path, "rb") as f:
        data = f.read()
    return base64.b64encode(data).decode(), hashlib.md5(data).hexdigest()


# ──────────────────────────────────────────────
# 企业微信
# ──────────────────────────────────────────────
@retry()
def _send_wecom(image_path: str, message: str):
    """
    企业微信机器人推送。

    流程：先上传图片拿到 base64 + md5，再以 image 类型发送。
    文档：https://developer.work.weixin.qq.com/document/path/91770
    """
    url = f"https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key={config.WECOM_WEBHOOK_KEY}"
    b64, md5 = _load_image_payload(image_path)

    payload = {
        "msgtype": "image",
        "image": {
            "base64": b64,
            "md5": md5,
        },
    }
    # 先发图片
    resp = requests.post(url, json=payload, timeout=15).json()
    if resp.get("errcode") != 0:
        raise RuntimeError(f"企业微信图片发送失败: {resp}")

    # 再发说明文字。响应同样要校验 errcode：被限流（如 45009）时 HTTP 仍返回 200，
    # 不校验会把"只发了图、没发文字"的半成功当成推送成功。
    resp2 = requests.post(
        url, json={"msgtype": "text", "text": {"content": message}}, timeout=15
    ).json()
    if resp2.get("errcode") != 0:
        raise RuntimeError(f"企业微信文字发送失败: {resp2}")


# ──────────────────────────────────────────────
# 统一入口
# ──────────────────────────────────────────────
_SENDERS = {
    "wecom": _send_wecom,
}


def webhook_configured() -> bool:
    """当前配置的 WEBHOOK_TYPE 是否有对应的发送器（供启动校验用）。"""
    return config.WEBHOOK_TYPE.lower() in _SENDERS


def _build_message(site_name: str, reason: str) -> str:
    """拼接告警文本：基础消息 + 站点 + 异常原因。"""
    msg = config.NOTIFY_MESSAGE
    lines: list[str] = []
    if site_name:
        lines.append(f"站点: {site_name}")
    if reason:
        lines.append(f"异常原因: {reason}")
    if lines:
        msg += "\n\n" + "\n".join(lines)
    return msg


def notify(image_path: str, reason: str = "", site_name: str = ""):
    """
    推送告警到配置的 Webhook。

    Args:
        image_path: 异常截图路径
        reason: 异常原因（附在消息里）
        site_name: 站点名（附在消息里）
    """
    sender_name = config.WEBHOOK_TYPE.lower()
    sender = _SENDERS.get(sender_name)
    if not sender:
        # 必须抛异常而非记日志返回：只记日志会让 monitor 的推送失败处理完全无感，
        # 告警全部静默丢失（如 config_local.py 里残留 WEBHOOK_TYPE="dingtalk"）
        raise RuntimeError(
            f"不支持的 Webhook 类型: {config.WEBHOOK_TYPE}（目前仅支持: {', '.join(_SENDERS)}）"
        )

    msg = _build_message(site_name, reason)

    logger.info(f"正在推送告警 → {sender_name} ...")
    sender(image_path, msg)
    logger.info("告警推送成功")
