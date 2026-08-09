#!/usr/bin/env python
"""
ntfy.sh Push Script - 通过 ntfy 推送摘要到安卓手机
每篇文章独立推送一条消息，格式统一：
  分类标签 + 文章标题 + 摘要 + 原文链接 + 折叠的深度拆解
单篇文章超长时自动拆分（摘要+链接一条，深度拆解按维度多条），
确保每条消息不超过 ntfy 4KB 内联限制，不会变成 txt 附件。

特性：
- 一篇文章 = 一条推送（格式统一，无多篇混排）
- 总览消息顶部显示时间范围和新文章数
- 深度拆解折叠在 <details> 标签中，需要时展开
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


def format_as_markdown(text):
    """将单篇文章文本转换为美观的 Markdown 格式，保留 <details> 深度拆解块"""
    # 先提取 <details>...</details> 块，用占位符替换
    details_blocks = []

    def save_details(m):
        details_blocks.append(m.group(0))
        return f"__DETAILS_PLACEHOLDER_{len(details_blocks) - 1}__"

    text = re.sub(r'<details>.*?</details>', save_details, text, flags=re.DOTALL)

    lines = text.split('\n')
    output_lines = []

    for line in lines:
        stripped = line.strip()

        # 匹配文章标题行: ### 1. [来源] 标题 或 ### [来源] 标题
        title_match = re.match(r'^###\s+(?:\d+\.\s*)?(.+)', stripped)
        if title_match:
            # 加粗标题
            output_lines.append(f'**{title_match.group(1)}**')
            output_lines.append('')
            continue

        # 匹配摘要行: 摘要：xxx  或  📝 摘要：xxx
        summary_match = re.match(r'^(?:\U0001f4dd\s*)?摘要[：:]\s*(.+)', stripped)
        if summary_match:
            output_lines.append(summary_match.group(1))
            output_lines.append('')
            continue

        # 匹配链接行: 链接：xxx  或  🔗 xxx
        link_match = re.match(r'^(?:\U0001f517\s*)?链接[：:]\s*(https?://\S+)', stripped)
        if link_match:
            url = link_match.group(1)
            output_lines.append(f'\U0001f517 [阅读原文]({url})')
            output_lines.append('')
            continue

        # 匹配占位符（深度拆解块）
        placeholder_match = re.match(r'^__DETAILS_PLACEHOLDER_(\d+)__$', stripped)
        if placeholder_match:
            idx = int(placeholder_match.group(1))
            output_lines.append(details_blocks[idx])
            output_lines.append('')
            continue

        # 其他非空行直接保留
        if stripped:
            output_lines.append(stripped)

    return '\n'.join(output_lines).strip()


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


def split_single_article(article_body):
    """拆分单篇超长文章：将摘要+链接与深度拆解分离，分别作为独立消息。
    如果深度拆解本身仍超长，按维度（** 标题）进一步拆分。
    返回 list[str]，每个元素是一条消息的 body。
    """
    # 提取 <details>...</details> 块
    details_match = re.search(r'<details>.*?</details>', article_body, re.DOTALL)
    if not details_match:
        # 没有 details 块，无法进一步拆分
        return [article_body]

    details_block = details_match.group(0)
    main_part = article_body[:details_match.start()].strip()

    parts = []

    # Part 1: 摘要 + 链接（加提示深度拆解在后续消息）
    main_with_note = main_part + "\n\n\U0001f4d6 深度拆解见后续消息"
    if len(main_with_note.encode("utf-8")) <= MAX_MSG_BYTES:
        parts.append(main_with_note)
    else:
        # main_part 本身超长（极不可能），截断
        parts.append(main_part[:MAX_MSG_BYTES - 50].rstrip() + "...")

    # Part 2+: 深度拆解
    details_bytes = len(details_block.encode("utf-8"))
    if details_bytes <= MAX_MSG_BYTES:
        parts.append(details_block)
    else:
        # 提取 <summary> 标签
        summary_match = re.search(r'<summary>(.*?)</summary>', details_block)
        summary_tag = summary_match.group(0) if summary_match else "<summary>深度拆解</summary>"

        # 提取 details 内的内容（去掉 <details>, <summary>...</summary>, </details>）
        if summary_match:
            inner_start = summary_match.end()
        else:
            inner_start = details_match.start() + len("<details>")
        inner_end = details_match.end() - len("</details>")
        inner_content = article_body[inner_start:inner_end].strip()

        # 按 ** 维度标题拆分
        dim_sections = re.split(r'(?=\*\*[^*]+\*\*[：:])', inner_content)

        close_tag = "\n\n</details>"
        close_size = len(close_tag.encode("utf-8"))

        current = f"<details>\n{summary_tag}\n\n"
        current_size = len(current.encode("utf-8"))
        empty_chunk_size = current_size  # 空块的基准大小

        for sec in dim_sections:
            sec = sec.strip()
            if not sec:
                continue
            sec_size = len(sec.encode("utf-8"))

            # Case 1: 维度能放进当前块
            if current_size + sec_size + close_size <= MAX_MSG_BYTES:
                current += sec + "\n\n"
                current_size += sec_size + 2
                continue

            # Case 2: 当前块有内容，先关闭它
            if current_size > empty_chunk_size:
                current += close_tag
                parts.append(current)
                current = "<details>\n<summary>深度拆解 (续)</summary>\n\n"
                current_size = len(current.encode("utf-8"))
                empty_chunk_size = current_size

            # Case 3: 维度单独能放进一个新块
            if current_size + sec_size + close_size <= MAX_MSG_BYTES:
                current += sec + "\n\n"
                current_size += sec_size + 2
                continue

            # Case 4: 维度本身超长，按行（bullet point）拆分
            sec_lines = sec.split('\n')
            dim_header_line = sec_lines[0] if sec_lines else ""
            dim_header_bytes = len((dim_header_line + "\n").encode("utf-8"))

            current += dim_header_line + "\n"
            current_size += dim_header_bytes

            for line in sec_lines[1:]:
                line = line.strip()
                if not line:
                    continue
                line_bytes = len((line + "\n").encode("utf-8"))

                if current_size + line_bytes + close_size > MAX_MSG_BYTES:
                    # 关闭当前块，开启新块，重复维度标题
                    current += close_tag
                    parts.append(current)
                    current = "<details>\n<summary>深度拆解 (续)</summary>\n\n"
                    current_size = len(current.encode("utf-8"))
                    empty_chunk_size = current_size
                    cont_header = dim_header_line + " (续)\n"
                    current += cont_header
                    current_size += len(cont_header.encode("utf-8"))

                current += line + "\n"
                current_size += line_bytes

            current += "\n"
            current_size += 1

        if current_size > empty_chunk_size and not current.rstrip().endswith("</details>"):
            current += close_tag
            parts.append(current)

    return parts


def build_article_messages(category, article_text, article_index=None, total_in_cat=None):
    """将单篇文章构造成推送消息列表 [(标题, body), ...]。

    格式统一：分类标签 + 文章标题 + 摘要 + 链接 + 深度拆解
    - 若总大小 <= MAX_MSG_BYTES：一条消息推送完整内容
    - 若超长：摘要+链接一条，深度拆解按维度多条
    article_index/total_in_cat: 文章在当前分类中的序号，用于标题区分（如 Think Tank 1/4）
    """
    formatted = format_as_markdown(article_text)
    emoji = EMOJI_MAP.get(category, "")
    label = f"{emoji} **{category}**" if emoji else f"**{category}**"
    full_body = f"{label}\n\n{formatted}"

    base_ascii = TITLE_MAP.get(category, category.encode("ascii", "replace").decode())
    if article_index is not None:
        prefix = f"{base_ascii} {article_index}"
        if total_in_cat and total_in_cat > 1:
            prefix = f"{base_ascii} {article_index}/{total_in_cat}"
    else:
        prefix = base_ascii

    # 单条能放下：完整推送
    if len(full_body.encode("utf-8")) <= MAX_MSG_BYTES:
        return [(prefix, full_body)]

    # 超长：拆分为多条
    parts = split_single_article(full_body)
    messages = []
    for i, part in enumerate(parts, 1):
        suffix = f" ({i}/{len(parts)})" if len(parts) > 1 else ""
        messages.append((f"{prefix}{suffix}", part))
    return messages


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
