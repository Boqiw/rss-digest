#!/usr/bin/env python
"""
ntfy.sh Push Script - 通过 ntfy 推送摘要到安卓手机
按分类拆分推送，每类一条通知，适配手机阅读
保留 <details> 深度拆解块，手机上可展开查看
使用 Markdown 格式渲染，标题加粗、链接可点击

特性：
- 推送标题包含时间窗口，一眼区分新旧
- 总览消息顶部显示时间范围和新文章数
- 两句话摘要 + 链接在明面可见
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
    """将文本转换为美观的 Markdown 格式，保留 <details> 深度拆解块"""
    # 先提取 <details>...</details> 块，用占位符替换
    details_blocks = []

    def save_details(m):
        details_blocks.append(m.group(0))
        return f"__DETAILS_PLACEHOLDER_{len(details_blocks) - 1}__"

    text = re.sub(r'<details>.*?</details>', save_details, text, flags=re.DOTALL)

    lines = text.split('\n')
    output_lines = []
    first_article = True

    for line in lines:
        stripped = line.strip()

        # 匹配文章标题行: ### 1. [来源] 标题
        title_match = re.match(r'^###\s+\d+\.\s+(.+)', stripped)
        if title_match:
            if not first_article:
                output_lines.append('')
                output_lines.append(SEPARATOR)
                output_lines.append('')
            first_article = False
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


def split_by_category(markdown_text):
    """将 digest.md 按分类拆分成多段，格式化为 Markdown（保留深度拆解）"""
    lines = markdown_text.split('\n')

    sections = []
    current_title = None
    current_lines = []

    for line in lines:
        if re.match(r'^## .+', line):
            if current_title is not None:
                raw_body = '\n'.join(current_lines)
                formatted_body = format_as_markdown(raw_body)
                sections.append((current_title, formatted_body))
            current_title = line.strip('# ').strip()
            current_lines = []
        else:
            current_lines.append(line)

    if current_title is not None:
        raw_body = '\n'.join(current_lines)
        formatted_body = format_as_markdown(raw_body)
        sections.append((current_title, formatted_body))

    return sections


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


def split_single_article(article_text):
    """拆分单篇超长文章：将摘要+链接与深度拆解分离，分别作为独立消息。
    如果深度拆解本身仍超长，按维度（** 标题）进一步拆分。
    返回 list[str]，每个元素是一条消息的 body。
    """
    # 提取 <details>...</details> 块
    details_match = re.search(r'<details>.*?</details>', article_text, re.DOTALL)
    if not details_match:
        # 没有 details 块，无法进一步拆分
        return [article_text]

    details_block = details_match.group(0)
    main_part = article_text[:details_match.start()].strip()

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
        inner_content = article_text[inner_start:inner_end].strip()

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


def split_section_if_too_long(title, body):
    """如果一个分类的消息太长，按文章拆分成多条消息。
    单篇文章超长时，自动将深度拆解分离为独立消息，确保内容不被截断。"""
    body_bytes = len(body.encode("utf-8"))
    if body_bytes <= MAX_MSG_BYTES:
        return [(title, body)]

    # 按分隔线拆分单篇文章
    articles = re.split(r'(?=\*\*\[)', body)
    # 第一段可能是分类标题前缀
    prefix = ""
    article_list = []
    for art in articles:
        art = art.strip()
        if not art:
            continue
        if not art.startswith("**["):
            prefix = art
            continue
        article_list.append(art)

    if not article_list:
        # 无法拆分，直接截断
        return [(title, body[:MAX_MSG_BYTES - 100] + "\n\n...(内容过长已截断)")]

    # 逐条累积，超限时拆分
    chunks = []  # 每个元素是一条消息的 body text
    current_chunk = prefix + "\n\n" if prefix else ""
    current_size = len(current_chunk.encode("utf-8"))

    for art in article_list:
        art_size = len(art.encode("utf-8"))

        # 单篇文章本身超长：先推送当前累积内容，再拆分这篇文章
        if art_size > MAX_MSG_BYTES:
            if current_chunk.strip():
                chunks.append(current_chunk.strip())
                current_chunk = ""
                current_size = 0
            # 拆分：摘要+链接 一条，深度拆解 一条或多条
            sub_parts = split_single_article(art)
            for sub_text in sub_parts:
                chunks.append(sub_text)
            continue

        # 正常累积
        if current_size + art_size > MAX_MSG_BYTES and current_chunk.strip():
            chunks.append(current_chunk.strip())
            current_chunk = ""
            current_size = 0
        current_chunk += art + "\n\n" + SEPARATOR + "\n\n"
        current_size += art_size + len(SEPARATOR.encode("utf-8")) + 4

    if current_chunk.strip():
        chunks.append(current_chunk.strip())

    # 加上序号
    result = []
    total_chunks = len(chunks)
    for i, chunk in enumerate(chunks):
        suffix = f" ({i+1}/{total_chunks})" if total_chunks > 1 else ""
        result.append((f"{title}{suffix}", chunk))

    return result


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

    sections = split_by_category(content)

    if not sections:
        formatted = format_as_markdown(content)
        success = push_to_ntfy(
            topic,
            push_title,
            formatted[:MAX_MSG_BYTES],
            tags="newspaper",
            priority="default",
        )
        if success:
            mark_links_as_pushed()
        return 0 if success else 1

    print(f"推送 {len(sections)} 个分类到 ntfy topic: {topic}")
    print()

    # === 推送总览（带时间窗口和新文章数）===
    total_articles = 0
    for _, body in sections:
        # 统计 **[ 开头的行数（每篇文章的加粗标题）
        total_articles += len(re.findall(r'\*\*\[', body))

    overview_lines = []
    if window and window["start"]:
        overview_lines.append(f"\U0001f4c5 **{window['start']} \u2192 {window['end']}**")
        overview_lines.append("")
        overview_lines.append(f"\U0001f195 **{total_articles} 篇新文章**")
        overview_lines.append("")
        overview_lines.append("\u2500" * 20)
        overview_lines.append("")

    for title, body in sections:
        article_count = len(re.findall(r'\*\*\[', body))
        overview_lines.append(f"**{title}**  {article_count} 篇")

    overview = "\n".join(overview_lines)
    overview_success = push_to_ntfy(topic, push_title, overview, tags="newspaper,chart", priority="default")

    # 逐分类推送（必要时按文章拆分）
    success_count = 1 if overview_success else 0
    all_success = overview_success

    for title, body in sections:
        body = body.strip()
        if not body:
            continue

        # 添加分类 emoji 前缀
        emoji = EMOJI_MAP.get(title, "")
        if emoji:
            body = f"{emoji} **{title}**\n\n{body}"

        # 如果太长，按文章拆分
        sub_sections = split_section_if_too_long(title, body)

        for sub_title, sub_body in sub_sections:
            sub_body = sub_body.strip()
            if not sub_body:
                continue

            # 用 TITLE_MAP 映射成 ASCII，如果是拆分子消息则加上序号
            base_ascii = TITLE_MAP.get(title, title.encode("ascii", "replace").decode())
            suffix_match = re.search(r'\(\d+/\d+\)', sub_title)
            if suffix_match:
                ascii_title = f"{base_ascii} {suffix_match.group(0)}"
            else:
                ascii_title = base_ascii
            tags = TAG_MAP.get(title, "newspaper")

            success = push_to_ntfy(topic, ascii_title, sub_body, tags=tags)
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
