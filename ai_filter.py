#!/usr/bin/env python
"""
AI Filter & Digest Generator - 使用 DeepSeek API 筛选文章并生成深度摘要

两阶段流程：
  Stage 1: 批量筛选 - 将所有文章标题+描述发给AI，返回值得推送的文章索引
  Stage 2: 逐篇生成 - 对每篇选中文章生成两句话摘要 + 深度拆解

输出: digest.md（供 push_ntfy.py 推送）

环境变量:
  DEEPSEEK_API_KEY  - DeepSeek API 密钥（必须）
  DEEPSEEK_BASE_URL - API 地址（默认 https://api.deepseek.com）
  DEEPSEEK_MODEL    - 模型名（默认 deepseek-chat）
"""
import json
import os
import sys
import time
import requests
from datetime import datetime, timezone, timedelta

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ARTICLES_JSON = os.path.join(SCRIPT_DIR, "rss_articles.json")
DIGEST_MD = os.path.join(SCRIPT_DIR, "digest.md")

BJT = timezone(timedelta(hours=8))

CATEGORY_ORDER = ["智库分析", "科技深度分析", "投资人分析"]

# ========== Stage 1: 筛选 Prompt ==========
FILTER_SYSTEM_PROMPT = """你是一个科技信息筛选助手。你将收到一批文章的标题和摘要描述。
请从中筛选出与以下主题相关的文章：

1. 脑机接口（BCI）、Neuralink、神经科学、脑科学
2. AI 技术与商业应用（大模型、AI Agent、AI基础设施）
3. 中美科技竞争（半导体、芯片、出口管制、供应链）
4. AI 在医疗和教育领域的应用
5. 光互联、光子学、光电芯片
6. AI 安全、AI 对齐、AI 监管政策
7. 科技政策、创新政策、国家安全科技议题

筛选标准：
- 优先选择有深度分析的文章，排除纯新闻快讯
- 优先选择对决策有参考价值的文章
- 每天筛选 5-15 篇，宁缺毋滥

请以 JSON 数组格式返回选中文章的索引（从0开始），例如：[0, 3, 5, 7]
只返回 JSON 数组，不要其他内容。"""

# ========== Stage 2: 摘要生成 Prompt ==========
DIGEST_SYSTEM_PROMPT = """你是一个科技信息深度分析助手。请对给定文章生成：

1. 两句话摘要（直接展示，让读者快速判断是否值得阅读）
2. 深度拆解（包含以下维度，每个维度内容分点清晰，不要揉成一大段）：

<details>
<summary>深度拆解</summary>

**核心论点**：文章的核心主张或发现（2-3句）

**技术细节**：关键技术/方法/数据（如有，用 1) 2) 3) 分点列出）

**影响分析**：对行业/政策/投资的影响（用 1) 2) 3) 分点列出，每点 1-2 句）

**关键数据**：文中提到的重要数字、指标（如有，用 - 逐条列出）

**启示**：对中国读者或科技决策者的启示（用 1) 2) 3) 分点列出，每点 1-2 句）

</details>

要求：
- 摘要简洁有力，两句话点明文章价值
- 深度拆解质量优先，充分展开，不要因长度压缩内容
- **分点要求**：凡是多个要点的维度，必须用 "1) 2) 3)" 或 "- " 分点列出，禁止揉成一大段无结构文字
- 使用中文撰写
- <details> 标签使用真实尖括号，不要用 HTML 实体编码
- 如果文章内容与主题关联度不高，深度拆解可以适当精简"""


def get_api_config():
    """从环境变量读取 DeepSeek API 配置"""
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        print("[ERROR] 未设置 DEEPSEEK_API_KEY 环境变量")
        return None
    base_url = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    model = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
    return {"api_key": api_key, "base_url": base_url, "model": model}


def call_deepseek(config, system_prompt, user_prompt, max_tokens=4096, temperature=0.3):
    """调用 DeepSeek API（OpenAI 兼容格式），返回文本响应"""
    url = f"{config['base_url']}/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {config['api_key']}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": config["model"],
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }

    max_retries = 3
    for attempt in range(max_retries):
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=120)
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]
        except Exception as e:
            if attempt < max_retries - 1:
                wait = (attempt + 1) * 10
                print(f"    [API RETRY {attempt+1}/{max_retries}] {str(e)[:100]}, waiting {wait}s...")
                time.sleep(wait)
            else:
                print(f"    [API ERROR] {str(e)[:200]}")
                return None


def stage1_filter(config, articles):
    """Stage 1: 批量筛选文章"""
    # 构建文章列表文本
    lines = []
    for i, art in enumerate(articles):
        lines.append(f"[{i}] [{art['source']}] {art['title']}")
        if art.get("description"):
            desc = art["description"][:200]
            lines.append(f"    摘要: {desc}")
        lines.append("")

    user_prompt = f"以下是 {len(articles)} 篇文章，请筛选出与上述主题相关的高价值文章：\n\n" + "\n".join(lines)

    print(f"[Stage 1] Sending {len(articles)} articles to DeepSeek for filtering...")
    response = call_deepseek(config, FILTER_SYSTEM_PROMPT, user_prompt, max_tokens=1024, temperature=0.1)

    if not response:
        print("[Stage 1] API call failed, keeping all articles as fallback")
        return list(range(len(articles)))

    # 解析 JSON 数组
    try:
        # 去除可能的 markdown 代码块标记
        clean = response.strip()
        if clean.startswith("```"):
            clean = clean.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        indices = json.loads(clean)
        if isinstance(indices, list):
            valid = [i for i in indices if isinstance(i, int) and 0 <= i < len(articles)]
            print(f"[Stage 1] Filtered: {len(valid)}/{len(articles)} articles selected")
            return valid
    except (json.JSONDecodeError, ValueError) as e:
        print(f"[Stage 1] Failed to parse response: {e}")
        print(f"    Response: {response[:200]}")

    print("[Stage 1] Parse failed, keeping all articles as fallback")
    return list(range(len(articles)))


def stage2_generate_digest(config, article):
    """Stage 2: 为单篇文章生成摘要+深度拆解"""
    title = article["title"]
    source = article["source"]
    link = article["link"]
    desc = article.get("description", "")

    user_prompt = f"""请为以下文章生成摘要和深度拆解：

标题: {title}
来源: {source}
链接: {link}
描述: {desc}

请按以下格式输出（严格遵循）：

### {source} - {title}

摘要：（两句话摘要）

链接：{link}

<details>
<summary>深度拆解</summary>

（深度拆解内容，按核心论点/技术细节/影响分析/关键数据/启示五个维度展开）

</details>"""

    response = call_deepseek(config, DIGEST_SYSTEM_PROMPT, user_prompt, max_tokens=2048, temperature=0.3)

    if not response:
        # Fallback: 生成基础格式
        return f"### {source} - {title}\n\n摘要：{desc[:150]}\n\n链接：{link}\n"

    return response.strip()


def build_digest(articles, selected_indices, config):
    """构建 digest.md"""
    selected = [articles[i] for i in selected_indices]

    # 按分类分组
    by_category = {}
    for art in selected:
        cat = art.get("category", "其他")
        if cat not in by_category:
            by_category[cat] = []
        by_category[cat].append(art)

    # 按固定顺序输出
    now_bjt = datetime.now(BJT)
    header = f"# RSS Digest - {now_bjt.strftime('%Y-%m-%d %H:%M')} BJT\n\n"
    header += f"共 {len(selected)} 篇精选文章\n\n---\n\n"

    sections = []
    for cat in CATEGORY_ORDER:
        if cat not in by_category:
            continue
        arts = by_category[cat]
        section_parts = [f"## {cat}\n"]

        for idx, art in enumerate(arts, 1):
            print(f"[Stage 2] ({idx}/{len(arts)}) [{cat}] {art['source']}: {art['title'][:50]}...")
            digest_entry = stage2_generate_digest(config, art)
            section_parts.append(digest_entry)
            section_parts.append("")
            # Rate limit: 避免API过载
            time.sleep(1)

        sections.append("\n".join(section_parts))

    # 处理未分类的文章
    other_cats = [c for c in by_category if c not in CATEGORY_ORDER]
    for cat in other_cats:
        arts = by_category[cat]
        section_parts = [f"## {cat}\n"]
        for idx, art in enumerate(arts, 1):
            print(f"[Stage 2] ({idx}/{len(arts)}) [{cat}] {art['source']}: {art['title'][:50]}...")
            digest_entry = stage2_generate_digest(config, art)
            section_parts.append(digest_entry)
            section_parts.append("")
            time.sleep(1)
        sections.append("\n".join(section_parts))

    return header + "\n".join(sections)


def main():
    config = get_api_config()
    if not config:
        return 1

    if not os.path.exists(ARTICLES_JSON):
        print(f"[ERROR] 文章文件不存在: {ARTICLES_JSON}")
        return 1

    with open(ARTICLES_JSON, "r", encoding="utf-8") as f:
        data = json.load(f)

    articles = data.get("articles", [])
    if not articles:
        print("[INFO] 没有文章需要处理")
        # 写入空 digest
        now_bjt = datetime.now(BJT)
        with open(DIGEST_MD, "w", encoding="utf-8") as f:
            f.write(f"# RSS Digest - {now_bjt.strftime('%Y-%m-%d %H:%M')} BJT\n\n今日无新文章。\n")
        return 0

    print(f"Loaded {len(articles)} articles from {ARTICLES_JSON}")

    # Stage 1: 筛选
    selected = stage1_filter(config, articles)

    # Stage 2: 逐篇生成摘要
    print(f"\n[Stage 2] Generating digests for {len(selected)} articles...")
    digest_content = build_digest(articles, selected, config)

    with open(DIGEST_MD, "w", encoding="utf-8") as f:
        f.write(digest_content)

    print(f"\nDigest saved to: {DIGEST_MD}")
    print(f"Selected: {len(selected)}/{len(articles)} articles")
    return 0


if __name__ == "__main__":
    sys.exit(main())
