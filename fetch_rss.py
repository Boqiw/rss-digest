#!/usr/bin/env python
"""
RSS Feed Fetcher - 智库 + 科技深度分析 + 投资人分析源
只抓高密度分析内容，不含普通新闻媒体

特性：
- 15个精选源（7智库 + 4科技深度 + 4投资人）
- 追补逻辑：记录上次运行时间，关机后开机自动抓取缺失时段
- 去重机制：已推送过的文章不会再次抓取，避免重复推送
- 网络容错：单个源失败不影响其他源

输出: rss_articles.json, new_article_links.json
"""
import feedparser
import json
import sys
import os
import time
import requests
from datetime import datetime, timedelta, timezone

# 浏览器UA，避免被RSS源拒绝
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept": "application/rss+xml, application/xml, text/xml, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
    "Cache-Control": "no-cache",
}

# ========== RSS 源配置 (15个精选源) ==========
RSS_SOURCES = [
    # === 智库分析 (7) ===
    ("CSIS", "https://www.csis.org/rss.xml", "智库分析", "direct"),
    ("Foreign Affairs", "https://www.foreignaffairs.com/rss.xml", "智库分析", "direct"),
    ("CSET", "https://cset.georgetown.edu/feed/", "智库分析", "direct"),
    ("The Diplomat", "https://thediplomat.com/feed/", "智库分析", "direct"),
    ("ASPI Strategist", "https://www.aspistrategist.org.au/feed/", "智库分析", "direct"),
    ("RAND Corporation", "https://www.rand.org/pubs/commentary.xml", "智库分析", "direct"),
    ("ITIF", "https://itif.org/feed", "智库分析", "direct"),

    # === 科技深度分析 (4) ===
    ("IEEE Spectrum", "https://spectrum.ieee.org/feeds/feed.rss", "科技深度分析", "direct"),
    ("Wired", "https://www.wired.com/feed/rss", "科技深度分析", "direct"),
    ("Ars Technica", "https://feeds.arstechnica.com/arstechnica/index", "科技深度分析", "direct"),
    ("The Gradient", "https://thegradient.pub/rss/", "科技深度分析", "direct"),

    # === 投资人分析 (4) ===
    ("Stratechery", "https://stratechery.com/feed", "投资人分析", "direct"),
    ("Benedict Evans", "https://benevans.substack.com/feed", "投资人分析", "direct"),
    ("ARK Invest", "https://ark-invest.com/feed/", "投资人分析", "direct"),
    ("SemiAnalysis", "https://semianalysis.substack.com/feed", "投资人分析", "direct"),
]

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LAST_RUN_FILE = os.path.join(SCRIPT_DIR, "last_run.txt")
PUSHED_LINKS_FILE = os.path.join(SCRIPT_DIR, "pushed_links.json")
NEW_LINKS_FILE = os.path.join(SCRIPT_DIR, "new_article_links.json")

# 北京时间
BJT = timezone(timedelta(hours=8))


def get_fetch_window(default_hours=24):
    """
    追补逻辑：
    - 读取上次成功运行的时间
    - 如果距上次运行超过 default_hours，则抓取自上次运行以来的全部文章
    - 如果没有记录或距上次运行不足 default_hours，使用 default_hours
    """
    if os.path.exists(LAST_RUN_FILE):
        try:
            with open(LAST_RUN_FILE, "r") as f:
                last_run_str = f.read().strip()
                last_run = datetime.fromisoformat(last_run_str)
                now = datetime.now(timezone.utc)
                gap_hours = (now - last_run).total_seconds() / 3600
                if gap_hours > default_hours:
                    print(f"[CATCH-UP] Last run was {gap_hours:.1f}h ago, catching up...")
                    return int(gap_hours) + 1
        except Exception:
            pass
    return default_hours


def save_run_time():
    """记录本次运行时间"""
    now = datetime.now(timezone.utc)
    with open(LAST_RUN_FILE, "w") as f:
        f.write(now.isoformat())


def load_pushed_links():
    """加载已推送过的文章链接集合"""
    if os.path.exists(PUSHED_LINKS_FILE):
        try:
            with open(PUSHED_LINKS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return set(data)
                elif isinstance(data, dict) and "links" in data:
                    return set(data["links"])
        except Exception:
            pass
    return set()


def save_new_links(links):
    """保存本次新文章的链接（供 push_ntfy.py 在推送成功后合并到 pushed_links.json）"""
    with open(NEW_LINKS_FILE, "w", encoding="utf-8") as f:
        json.dump(list(links), f, ensure_ascii=False, indent=2)


def parse_entry_date(entry):
    """解析文章发布时间，返回timezone-aware datetime"""
    for field in ["published_parsed", "updated_parsed", "created_parsed"]:
        if hasattr(entry, field) and getattr(entry, field):
            try:
                t = time.struct_time(getattr(entry, field))
                return datetime(*t[:6], tzinfo=timezone.utc)
            except Exception:
                continue

    for field in ["published", "updated", "created"]:
        if hasattr(entry, field) and getattr(entry, field):
            try:
                dt = feedparser._parse_date(getattr(entry, field))
                if dt:
                    t = time.struct_time(dt)
                    return datetime(*t[:6], tzinfo=timezone.utc)
            except Exception:
                continue

    return None


def clean_html(text):
    """简单去除HTML标签"""
    import re
    if not text:
        return ""
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text[:500]


def fetch_feed(url, source_type="direct"):
    """抓取单个RSS源，返回feedparser结果。带重试逻辑。"""
    max_retries = 3
    for attempt in range(max_retries):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=30, allow_redirects=True)
            resp.raise_for_status()
            return feedparser.parse(resp.content)
        except Exception as e:
            if attempt < max_retries - 1:
                wait = (attempt + 1) * 5
                print(f"    [RETRY {attempt+1}/{max_retries}] {str(e)[:80]}, waiting {wait}s...")
                time.sleep(wait)
            else:
                raise


def fetch_all_sources(hours=None):
    """抓取所有RSS源，返回最近N小时的文章列表（已去重）"""
    if hours is None:
        hours = get_fetch_window(default_hours=24)

    now = datetime.now(timezone.utc)
    threshold = now - timedelta(hours=hours)
    all_articles = []
    errors = []
    new_links = set()

    # 加载已推送链接，用于去重
    pushed_links = load_pushed_links()
    deduped_count = 0

    print(f"Fetching {len(RSS_SOURCES)} RSS feeds (last {hours}h)...")
    print(f"Current UTC: {now.isoformat()}")
    print(f"Threshold:   {threshold.isoformat()}")
    print(f"Pushed links on record: {len(pushed_links)}")
    print()

    for name, url, category, source_type in RSS_SOURCES:
        try:
            feed = fetch_feed(url, source_type)

            if feed.bozo and not feed.entries:
                errors.append(f"  [ERROR] {name}: feed parse error")
                continue

            if not feed.entries:
                print(f"  [{category}] {name}: 0 entries (empty feed)")
                continue

            count = 0
            skipped = 0
            for entry in feed.entries:
                pub_date = parse_entry_date(entry)

                # 如果无法解析时间，默认包含（宁可多不可少）
                if pub_date is None:
                    pub_date = now

                if pub_date > threshold:
                    link = entry.get("link", "")

                    # 去重：跳过已推送过的文章
                    if link and link in pushed_links:
                        skipped += 1
                        deduped_count += 1
                        continue

                    article = {
                        "category": category,
                        "source": name,
                        "title": entry.get("title", "无标题"),
                        "link": link,
                        "description": clean_html(entry.get("description", entry.get("summary", ""))),
                        "published": pub_date.strftime("%Y-%m-%d %H:%M UTC"),
                    }
                    all_articles.append(article)
                    if link:
                        new_links.add(link)
                    count += 1

            if skipped > 0:
                print(f"  [{category}] {name}: {count} new, {skipped} already pushed (skipped)")
            else:
                status = f"  [{category}] {name}: {count} new" if count > 0 else f"  [{category}] {name}: 0 new"
                print(status)

        except Exception as e:
            errors.append(f"  [ERROR] {name}: {str(e)[:100]}")

    print()
    print(f"Total: {len(all_articles)} new articles (deduped {deduped_count} already-pushed)")
    print(f"Time window: {threshold.strftime('%m-%d %H:%M')} UTC -> {now.strftime('%m-%d %H:%M')} UTC")

    if errors:
        print(f"Errors ({len(errors)}):")
        for e in errors:
            print(e)

    # 保存新链接（供 push_ntfy.py 在推送成功后合并）
    save_new_links(new_links)

    # 保存运行时间
    save_run_time()

    # 返回文章列表和时间窗口信息
    window_start_bjt = threshold.astimezone(BJT)
    window_end_bjt = now.astimezone(BJT)

    return all_articles, {
        "window_start_utc": threshold.isoformat(),
        "window_end_utc": now.isoformat(),
        "window_start_bjt": window_start_bjt.strftime("%m-%d %H:%M"),
        "window_end_bjt": window_end_bjt.strftime("%m-%d %H:%M"),
        "window_hours": hours,
    }


def main():
    hours = None  # None = 自动追补逻辑
    if len(sys.argv) > 1:
        hours = int(sys.argv[1])

    output_path = os.path.join(SCRIPT_DIR, "rss_articles.json")

    articles, window_info = fetch_all_sources(hours=hours)

    output = {
        "fetch_time": datetime.now(timezone.utc).isoformat(),
        "time_window_hours": window_info["window_hours"],
        "window_start_bjt": window_info["window_start_bjt"],
        "window_end_bjt": window_info["window_end_bjt"],
        "total_articles": len(articles),
        "articles": articles,
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\nOutput saved to: {output_path}")
    print(f"New links saved to: {NEW_LINKS_FILE}")
    if not articles:
        print("[INFO] No new articles found today. Workflow will continue to push an empty digest.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
