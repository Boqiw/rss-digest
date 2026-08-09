#!/usr/bin/env python
"""
ntfy.sh Push Script - 通过 ntfy 推送摘要到安卓手机
每篇文章独立推送一条消息，格式统一：
  🚀 科技深度分析          <- 分类行（emoji + 分类名）
  **Wired - 文章标题**     <- 标题（来源 - 标题）
  摘要内容                 <- 两句话摘要
  🔗 [阅读原文](url)      <- 链接
  **核心论点**：...        <- 深度拆解直接展开（不折叠）
  **技术细节**：...

深度拆解不再折叠在 <details> 中，直接展开显示，阅读体验更流畅。
单篇文章超长时自动拆分（头部一条 + 维度按块多条），
确保每条消息不超过 ntfy 4KB 内联限制，不会变成 txt 附件。

特性：
- 一篇文章 = 一条推送（格式统一，无多篇混排）
- 总览消息顶部显示时间范围和新文章数
- 深度拆解直接展开，分点清晰
- 推送成功后自动标记已推链接，下次不会重复推送

用法:
  python push_ntfy.py                    # 读取 digest.md 推送
  python push_ntfy.py digest.md          # 指定文件
  python push_ntfy.py --topic my_topic   # 指定 topic
"""
import sys
import os
import re
import json
import requests
from datetime import datetime, timezone, timedelta

# ========== 配置 ==========
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DIGEST = os.path.join(SCRIPT_DIR, "digest.md")
TOPIC_FILE = os.path.join(SCRIPT_DIR, "ntfy_topic.txt")
ARTICLES_JSON = os.path.join(SCRIPT_DIR, "rss_articles.json")
NEW_LINKS_FILE = os.path.join(SCRIPT_DIR, "new_article_links.json")
PUSHED_LINKS_FILE = os.path.join(SCRIPT_DIR, "pushed_links.json")
NTFY_SERVER = "https://ntfy.sh"

# ntfy 消息大小限制：ntfy.sh 内联文本上限 4096 字节，超过会自动转成 txt 附件
# 用 3800 留余量（UTF-8 中文每字 3 字节）
MAX_MSG_BYTES = 3800

# 北京时间
BJT = timezone(timedelta(hours=8))

# 分类中文名 -> ASCII 标题（ntfy header 不支持非 ASCII）
TITLE_MAP = {
    "智库分析": "Think Tank",
    "科技深度分析": "Tech Analysis",
    "投资人分析": "Investor Analysis",
    "AI过滤精选": "Daily Picks",
}

# 分类 -> ntfy emoji tag
TAG_MAP = {
    "智库分析": "building",
    "科技深度分析": "rocket",
    "投资人分析": "money",
    "AI过滤精选": "star",
}

# 分类 -> emoji 前缀（用在 body 里）
EMOJI_MAP = {
    "智库分析": "\U0001f3db",      # 🏛
    "科技深度分析": "\U0001f680",   # 🚀
    "投资人分析": "\U0001f4b0",     # 💰
    "AI过滤精选": "\u2b50",        # ⭐
}

SEPARATOR = "\u2500" * 20  # ────────────────────


def load_topic():
    """从环境变量、命令行参数或文件读取 ntfy topic。
    优先级：--topic 参数 > NTFY_TOPIC 环境变量 > ntfy_topic.txt 文件
    """
    # 1. 命令行 --topic 参数
    for i, arg in enumerate(sys.argv):
        if arg == "--topic" and i + 1 < len(sys.argv):
            return sys.argv[i + 1]

    # 2. 环境变量（GitHub Actions secrets 注入）
    env_topic = os.environ.get("NTFY_TOPIC")
    if env_topic:
        return env_topic.strip()

    # 3. 本地文件
    if os.path.exists(TOPIC_FILE):
        with open(TOPIC_FILE, "r", encoding="utf-8") as f:
            topic = f.read().strip()
            if topic:
                return topic

    print(f"[ERROR] 未找到 ntfy topic")
    print(f"请设置环境变量 NTFY_TOPIC，或创建文件: {TOPIC_FILE}")
    return None


def load_window_info():
    """从 rss_articles.json 读取时间窗口信息"""
    if os.path.exists(ARTICLES_JSON):
        try:
            with open(ARTICLES_JSON, "r", encoding="utf-8") as f:
                data = json.load(f)
                return {
                    "start": data.get("window_start_bjt", ""),
                    "end": data.get("window_end_bjt", ""),
                    "hours": data.get("time_window_hours", ""),
                    "total": data.get("total_articles", 0),
                }
        except Exception:
            pass
    return None


def mark_links_as_pushed():
    """推送成功后，将 new_article_links.json 合并到 pushed_links.json"""
    if not os.path.exists(NEW_LINKS_FILE):
        return

    try:
        with open(NEW_LINKS_FILE, "r", encoding="utf-8") as f:
            new_links = json.load(f)
    except Exception:
        return

    # 加载已有的已推送链接
    existing = set()
    if os.path.exists(PUSHED_LINKS_FILE):
        try:
            with open(PUSHED_LINKS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    existing = set(data)
                elif isinstance(data, dict) and "links" in data:
                    existing = set(data["links"])
        except Exception:
            pass

    # 合并
    merged = existing | set(new_links)

    # 限制记录数量（保留最近2000条，避免文件无限增长）
    if len(merged) > 2000:
        merged_list = list(merged)[-2000:]
    else:
        merged_list = list(merged)

    with open(PUSHED_LINKS_FILE, "w", encoding="utf-8") as f:
        json.dump(merged_list, f, ensure_ascii=False, indent=2)

    print(f"[DEDUP] Marked {len(new_links)} links as pushed (total: {len(merged_list)})")


def format_title(raw_title):
    """将 '[Wired] Title' 转为 'Wired - Title'"""
    m = re.match(r'^\[(.+?)\]\s*(.+)', raw_title)
    if m:
        return f"{m.group(1)} - {m.group(2)}"
    return raw_title


def parse_article(article_text):
    """解析单篇文章为两部分结构。

    返回 (en_title, zh_title, summary, link, deep_body, insight)
    - en_title: 英文原标题（[来源] 标题 格式）
    - zh_title: 标题翻译（中文）
    - summary: 一句话总结
    - link: 原文链接 URL
    - deep_body: 深度总结正文（第一部分，多段落，含小标题/加粗）
    - insight: 我的理解（第二部分，个人理解/影响/批判思考）
    """
    en_title = None
    zh_title = None
    summary = None
    link = None
    deep_body = None
    insight = None

    lines = article_text.split('\n')
    part1_lines = []
    part2_lines = []
    in_deep = False      # 正在收集深度总结
    in_insight = False   # 正在收集我的理解

    for line in lines:
        stripped = line.strip()

        # 标题行: ### [来源] 英文标题
        tm = re.match(r'^###\s+(.+)$', stripped)
        if tm and en_title is None:
            en_title = tm.group(1).strip()
            continue

        # 标题翻译
        zm = re.match(r'^标题翻译[：:]\s*(.+)', stripped)
        if zm and zh_title is None:
            zh_title = zm.group(1).strip()
            continue

        # 一句话总结
        sm = re.match(r'^一句话总结[：:]\s*(.+)', stripped)
        if sm and summary is None:
            summary = sm.group(1).strip()
            continue

        # 原文链接
        lm = re.match(r'^原文链接[：:]\s*(https?://\S+)', stripped)
        if lm and link is None:
            link = lm.group(1)
            continue

        # 【我的理解】分隔标记：第一部分结束，第二部分开始
        im = re.match(r'^【我的理解】', stripped)
        if im:
            in_deep = False
            in_insight = True
            continue

        # 深度总结开始（第一部分）
        dm = re.match(r'^深度总结[：:]\s*(.*)', stripped)
        if dm:
            in_deep = True
            in_insight = False
            if dm.group(1).strip():
                part1_lines.append(dm.group(1).strip())
            continue

        # 正文收集
        if in_insight:
            part2_lines.append(line)
        elif in_deep:
            part1_lines.append(line)

    if part1_lines:
        deep_body = '\n'.join(part1_lines).strip()
    if part2_lines:
        insight = '\n'.join(part2_lines).strip()

    return en_title, zh_title, summary, link, deep_body, insight


def _truncate_to_limit(text, limit=MAX_MSG_BYTES):
    """将文本截断到指定字节数以内（按 UTF-8 字节安全截断）"""
    if len(text.encode("utf-8")) <= limit:
        return text
    # 逐个字符累积到 limit
    result = ""
    size = 0
    for ch in text:
        ch_size = len(ch.encode("utf-8"))
        if size + ch_size > limit:
            break
        result += ch
        size += ch_size
    return result.rstrip() + "..."


def build_article_messages(category, article_text, article_index=None, total_in_cat=None):
    """将单篇文章构造成推送消息列表 [(标题, body), ...]。

    每篇文章固定两条消息：
      [第一条] 🚀 科技深度分析 + **中文标题** + 一句话总结 + 🔗 阅读原文 + 深度总结
      [第二条] 🚀 科技深度分析 + **我的理解** + 个人理解正文

    标题命名：xxxx 序号/总数-I 和 xxxx 序号/总数-II
    例如第 3 篇（共 10 篇）: Tech Analysis 3/10-I / Tech Analysis 3/10-II

    强制保证：返回恰好 2 条消息（正文过长时截断，绝不拆分出第 3 条）。
    """
    en_title, zh_title, summary, link, deep_body, insight = parse_article(article_text)

    emoji = EMOJI_MAP.get(category, "")
    label = f"{emoji} **{category}**" if emoji else f"**{category}**"

    base_ascii = TITLE_MAP.get(category, category.encode("ascii", "replace").decode())
    if article_index is not None and total_in_cat:
        prefix = f"{base_ascii} {article_index}/{total_in_cat}"
    else:
        prefix = base_ascii

    # === 第一条：标题 + 一句话总结 + 链接 + 深度总结 ===
    msg1_parts = [label]
    if zh_title:
        msg1_parts.append(f"**{zh_title}**")
    elif en_title:
        msg1_parts.append(f"**{format_title(en_title)}**")
    if summary:
        msg1_parts.append(summary)
    if link:
        msg1_parts.append(f'\U0001f517 [阅读原文]({link})')
    if deep_body:
        msg1_parts.append(deep_body)
    msg1_body = "\n\n".join(msg1_parts)
    msg1_body = _truncate_to_limit(msg1_body)

    # === 第二条：我的理解 ===
    msg2_parts = [label, f"**我的理解**"]
    if insight:
        msg2_parts.append(insight)
    else:
        msg2_parts.append("（该文章未生成个人理解部分）")
    msg2_body = "\n\n".join(msg2_parts)
    msg2_body = _truncate_to_limit(msg2_body)

    return [(f"{prefix}-I", msg1_body), (f"{prefix}-II", msg2_body)]


def parse_articles_by_category(content):
    """解析 digest.md，按分类拆分成文章列表。

    返回: {分类名: [文章原始文本, ...]}
    digest 格式:
      ## 分类名
      ### 1. [来源] 标题
      摘要：...
      链接：https://...
      <details>...</details>
    """
    categories = {}
    current_cat = None
    current_article_lines = []
    articles_in_cat = []

    def flush_article():
        nonlocal current_article_lines
        text = '\n'.join(current_article_lines).strip()
        if text:
            articles_in_cat.append(text)
        current_article_lines = []

    for line in content.split('\n'):
        stripped = line.strip()
        if re.match(r'^##\s+\S', stripped):
            # 新分类：flush 上一篇文章和上一分类
            flush_article()
            if current_cat and articles_in_cat:
                categories[current_cat] = articles_in_cat
            current_cat = stripped.lstrip('# ').strip()
            articles_in_cat = []
            current_article_lines = []  # 丢弃 header/空行残留
        elif re.match(r'^###\s+\S', stripped):
            # 新文章
            flush_article()
            current_article_lines.append(line)
        else:
            current_article_lines.append(line)

    # 收尾
    flush_article()
    if current_cat and articles_in_cat:
        categories[current_cat] = articles_in_cat

    return categories


def push_to_ntfy(topic, title_ascii, message, tags="", priority="default"):
    """通过 ntfy 推送一条消息。title_ascii 必须为纯 ASCII。"""
    url = f"{NTFY_SERVER}/{topic}"
    headers = {
        "Title": title_ascii,
        "Priority": priority,
        "Markdown": "yes",
    }
    if tags:
        headers["Tags"] = tags

    try:
        resp = requests.post(url, data=message.encode("utf-8"), headers=headers, timeout=30)
        if resp.status_code == 200:
            print(f"  [OK] {title_ascii}")
            return True
        else:
            print(f"  [ERROR] {title_ascii}: HTTP {resp.status_code} - {resp.text[:100]}")
            return False
    except Exception as e:
        print(f"  [ERROR] {title_ascii}: {e}")
        return False


def main():
    digest_path = DEFAULT_DIGEST
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if "--topic" in sys.argv:
        topic_idx = sys.argv.index("--topic")
        if topic_idx + 1 < len(sys.argv):
            args = [a for a in args if a != sys.argv[topic_idx + 1]]
    if args:
        digest_path = args[0]

    if not os.path.exists(digest_path):
        print(f"[ERROR] 摘要文件不存在: {digest_path}")
        return 1

    with open(digest_path, "r", encoding="utf-8") as f:
        content = f.read()

    if not content.strip():
        print("[INFO] 摘要内容为空，跳过推送")
        return 0

    topic = load_topic()
    if not topic:
        return 1

    # 加载时间窗口信息
    window = load_window_info()

    now_bjt = datetime.now(BJT)
    date_str = now_bjt.strftime("%m-%d %H:%M")

    # 构建标题：包含时间窗口
    if window and window["start"]:
        title_time = f"{window['start']}>{window['end']}"
        title_time_ascii = title_time.encode("ascii", "replace").decode()
        push_title = f"Digest {title_time_ascii}"
    else:
        push_title = f"Digest {date_str}"

    # 按分类解析文章
    categories = parse_articles_by_category(content)

    if not categories:
        print("[INFO] 未解析到分类内容，跳过推送")
        return 0

    total_articles = sum(len(arts) for arts in categories.values())
    print(f"推送 {len(categories)} 个分类, {total_articles} 篇文章到 ntfy topic: {topic}")
    print()

    # === 推送总览（带时间窗口和新文章数）===
    overview_lines = []
    if window and window["start"]:
        overview_lines.append(f"\U0001f4c5 **{window['start']} \u2192 {window['end']}**")
        overview_lines.append("")
        overview_lines.append(f"\U0001f195 **{total_articles} 篇新文章**")
        overview_lines.append("")
        overview_lines.append(SEPARATOR)
        overview_lines.append("")

    for cat, arts in categories.items():
        overview_lines.append(f"**{cat}**  {len(arts)} 篇")

    overview = "\n".join(overview_lines)
    overview_success = push_to_ntfy(topic, push_title, overview, tags="newspaper,chart", priority="default")

    success_count = 1 if overview_success else 0
    all_success = overview_success

    # === 每篇文章独立推送 ===
    for cat, arts in categories.items():
        tags = TAG_MAP.get(cat, "newspaper")
        total_in_cat = len(arts)
        for idx, art in enumerate(arts, 1):
            messages = build_article_messages(cat, art, article_index=idx, total_in_cat=total_in_cat)
            for msg_title, msg_body in messages:
                success = push_to_ntfy(topic, msg_title, msg_body, tags=tags)
                if success:
                    success_count += 1
                else:
                    all_success = False

    print(f"\n推送完成: {success_count} 条消息成功")

    # 只有全部成功才标记去重（部分失败则不标记，下次重试）
    if all_success:
        mark_links_as_pushed()
    else:
        print("[WARN] 部分推送失败，不标记去重，下次将重试")

    return 0 if success_count > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
