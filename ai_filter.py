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
DIGEST_SYSTEM_PROMPT = """你是一个科技信息深度分析助手。请对给定文章按以下结构输出，严格遵循顺序与分隔符：

### [来源] 英文原标题
（这一行保留原文标题，不要翻译、不要改动）

标题翻译：（将英文标题翻译为通顺的中文，一句即可）

一句话总结：（用且仅用一句话，高度概括全文最核心的事件或结论）

原文链接：（直接附上链接）

深度总结：（这是第一部分正文，用连贯段落把文章讲透。要求：讲清背景、关键事件、各方反应、核心争议；深挖隐含风险与长远意义；可以自由分段或使用小标题加粗，但严禁拆成"1. 2. 3."或"- xxx"零散列表。控制在约500-700字，不要过长）

【我的理解】
（这是第二部分，用连贯段落写你对这篇文章的独立理解，可写内容包括但不限于：对相关行业/政策/技术发展的影响、批判性思考、发展趋势变化、对中美科技格局的意义等。要求：有个人观点和判断，不要复述原文；可以分段或加粗小标题，但严禁分点罗列。控制在约500-700字）

输出总要求：
- 全部使用中文撰写（英文标题行除外）
- 两个部分之间必须用【我的理解】作为分隔标记
- 不要输出除上述结构外的任何其他内容"""


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
    """Stage 2: 为单篇文章生成中文标题翻译 + 一句话总结 + 深度总结 + 我的理解"""
    title = article["title"]
    source = article["source"]
    link = article["link"]
    desc = article.get("description", "")

    user_prompt = f"""请为以下文章生成中文分析内容：

标题: {title}
来源: {source}
链接: {link}
描述: {desc}

请严格按以下结构输出（不要输出其他内容）：

### [{source}] {title}

标题翻译：（将上面英文标题翻译为通顺的中文）

一句话总结：（用且仅用一句话，高度概括全文最核心的事件或结论）

原文链接：{link}

深度总结：（用连贯的段落把文章讲透，包含背景、关键事件、争议、隐含风险、长远意义。可以自由分段或使用小标题，但严禁分点罗列。控制在约500-700字）

【我的理解】
（用连贯段落写你对这篇文章的独立理解：对相关行业/政策/技术发展的影响、批判性思考、发展趋势变化等。有个人观点和判断，不要复述原文。可以分段或加粗小标题，但严禁分点罗列。控制在约500-700字）"""

    response = call_deepseek(config, DIGEST_SYSTEM_PROMPT, user_prompt, max_tokens=4096, temperature=0.3)

    if not response:
        # Fallback: 生成基础格式
        return f"### [{source}] {title}\n\n标题翻译：{title}\n\n一句话总结：{desc[:150]}\n\n原文链接：{link}\n\n深度总结：暂无可用内容。\n\n【我的理解】\n暂无可用内容。\n"

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
