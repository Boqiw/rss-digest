#!/usr/bin/env python
"""
WeChat Push Script - 通过Server酱推送Markdown摘要到微信
读取 digest.md 文件内容，推送到Server酱
"""
import sys
import os
import requests
import json
from datetime import datetime, timezone, timedelta

# ========== 配置 ==========
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
KEY_FILE = os.path.join(SCRIPT_DIR, "serverchan_key.txt")
DIGEST_FILE = os.path.join(SCRIPT_DIR, "digest.md")

# 北京时间
BJT = timezone(timedelta(hours=8))


def load_key():
    """从文件读取Server酱SendKey"""
    if not os.path.exists(KEY_FILE):
        print(f"[ERROR] Server酱key文件不存在: {KEY_FILE}")
        print("请创建该文件并写入你的Server酱SendKey（一行纯文本）")
        print("获取地址: https://sct.ftqq.com/")
        return None
    with open(KEY_FILE, "r", encoding="utf-8") as f:
        key = f.read().strip()
    if not key:
        print("[ERROR] Server酱key文件为空")
        return None
    return key


def push_to_wechat(key, title, content):
    """通过Server酱推送消息"""
    url = f"https://sctapi.ftqq.com/{key}.send"
    data = {
        "title": title,
        "desp": content,
    }
    try:
        resp = requests.post(url, data=data, timeout=30)
        result = resp.json()
        if result.get("code") == 0:
            print(f"[OK] 推送成功! title={title}")
            return True
        else:
            print(f"[ERROR] 推送失败: {result}")
            return False
    except Exception as e:
        print(f"[ERROR] 请求异常: {e}")
        return False


def main():
    # 读取digest文件
    digest_path = DIGEST_FILE
    if len(sys.argv) > 1:
        digest_path = sys.argv[1]

    if not os.path.exists(digest_path):
        print(f"[ERROR] 摘要文件不存在: {digest_path}")
        return 1

    with open(digest_path, "r", encoding="utf-8") as f:
        content = f.read()

    if not content.strip():
        print("[INFO] 摘要内容为空，跳过推送")
        return 0

    # 加载Server酱key
    key = load_key()
    if not key:
        return 1

    # 生成标题
    now_bjt = datetime.now(BJT)
    title = f"信息摘要 {now_bjt.strftime('%m-%d %H:%M')}"

    # 推送
    success = push_to_wechat(key, title, content)
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
