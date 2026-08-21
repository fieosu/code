"""
Webhook 推送模块

支持飞书 / 钉钉 / 企业微信 / 通用 Webhook。
异常时推送告警文本 + 截图图片。
"""

import base64
import hashlib
import hmac
import json
import time
from urllib.parse import quote_plus, urlencode

import requests

import config
from utils import logger, retry


def _load_image_base64(path: str) -> str:
    """读取图片文件并转为 base64 字符串。"""
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()


def _image_md5(path: str) -> str:
    """计算图片文件的 MD5（钉钉/企业微信需要）。"""
    with open(path, "rb") as f:
        return hashlib.md5(f.read()).hexdigest()


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
    b64 = _load_image_base64(image_path)
    md5 = _image_md5(image_path)

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

    # 再发说明文字
    requests.post(url, json={"msgtype": "text", "text": {"content": message}}, timeout=15)


# ──────────────────────────────────────────────
# 钉钉
# ──────────────────────────────────────────────
@retry()
def _send_dingtalk(image_path: str, message: str):
    """
    钉钉自定义机器人推送。

    支持加签（如果配置了 DINGTALK_SECRET）。
    文档：https://open.dingtalk.com/document/custom-robot/send-message-type
    """
    url = config.DINGTALK_WEBHOOK_URL

    # 加签处理
    if config.DINGTALK_SECRET:
        timestamp = str(round(time.time() * 1000))
        secret_enc = config.DINGTALK_SECRET.encode()
        string_to_sign = f"{timestamp}\n{config.DINGTALK_SECRET}"
        sign = hmac.new(secret_enc, string_to_sign.encode(), hashlib.sha256).digest()
        sign_url = quote_plus(base64.b64encode(sign).decode())
        url = f"{url}&timestamp={timestamp}&sign={sign_url}"

    # 钉钉不直接支持图片上传，用 markdown + 外链方式
    # 这里推送文本告警；可改用钉钉文件上传或把图传到 CDN
    headers = {"Content-Type": "application/json"}
    payload = {
        "msgtype": "markdown",
        "markdown": {
            "title": "网页异常告警",
            "text": f"### ⚠️ 网页异常告警\n\n{message}\n\n（截图见附件，需配合图片上传接口使用）",
        },
    }
    resp = requests.post(url, json=payload, headers=headers, timeout=15).json()
    if resp.get("errcode") != 0:
        raise RuntimeError(f"钉钉推送失败: {resp}")


# ──────────────────────────────────────────────
# 飞书
# ──────────────────────────────────────────────
@retry()
def _send_feishu(image_path: str, message: str):
    """
    飞书自定义机器人推送。

    流程：调用 image/v1/images 上传图片拿到 image_key，再用 image 消息发送。
    文档：https://open.feishu.cn/document/client-docs/bot-v3/add-custom-bot
    """
    url = config.FEISHU_WEBHOOK_URL

    # 加签处理
    if config.FEISHU_SECRET:
        timestamp = str(int(time.time()))
        string_to_sign = f"{timestamp}\n{config.FEISHU_SECRET}"
        sign = hmac.new(
            config.FEISHU_SECRET.encode(), string_to_sign.encode(), hashlib.sha256
        ).digest()
        sign_b64 = base64.b64encode(sign).decode()
    else:
        timestamp, sign_b64 = "", ""

    # 1) 上传图片
    with open(image_path, "rb") as f:
        resp = requests.post(
            url,
            files={"image": f},
            data={"image_type": "message"},
            timeout=15,
        )
    resp_json = resp.json()
    if resp_json.get("code") != 0:
        raise RuntimeError(f"飞书图片上传失败: {resp_json}")
    image_key = resp_json["data"]["image_key"]

    # 2) 发文字 + 图片
    payload = {
        "timestamp": timestamp,
        "sign": sign_b64,
        "msg_type": "post",
        "content": {
            "post": {
                "zh_cn": {
                    "title": "⚠️ 网页异常告警",
                    "content": [[{"tag": "text", "text": message}],
                               [{"tag": "image", "image_key": image_key}]],
                }
            }
        },
    }
    resp2 = requests.post(url, json=payload, timeout=15).json()
    if resp2.get("code") != 0:
        raise RuntimeError(f"飞书消息发送失败: {resp2}")


# ──────────────────────────────────────────────
# 通用 Webhook
# ──────────────────────────────────────────────
@retry()
def _send_generic(image_path: str, message: str):
    """
    通用 Webhook：将告警信息和图片 base64 编码后 POST JSON。

    接收方需要自行解析。
    """
    payload = {
        "message": message,
        "screenshot_base64": _load_image_base64(image_path),
        "filename": image_path,
    }
    resp = requests.post(config.GENERIC_WEBHOOK_URL, json=payload, timeout=15)
    resp.raise_for_status()


# ──────────────────────────────────────────────
# 统一入口
# ──────────────────────────────────────────────
_SENDERS = {
    "wecom": _send_wecom,
    "dingtalk": _send_dingtalk,
    "feishu": _send_feishu,
    "generic": _send_generic,
}


def _build_message(site_name: str, reason: str) -> str:
    """拼接告警文本：基础消息 + 站点 + 异常原因。"""
    msg = config.NOTIFY_MESSAGE
    lines = []
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
        logger.error(f"不支持的 Webhook 类型: {config.WEBHOOK_TYPE}")
        return

    msg = _build_message(site_name, reason)

    logger.info(f"正在推送告警 → {sender_name} ...")
    sender(image_path, msg)
    logger.info("告警推送成功")
