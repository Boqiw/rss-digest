# RSS Digest - 自动化信息摘要推送系统

每天早上 9:00（北京时间）自动抓取 15 个精选 RSS 源，经 AI 筛选和深度摘要后推送到手机。

## 架构流程

```
fetch_rss.py → rss_articles.json → ai_filter.py → digest.md → push_ntfy.py → ntfy.sh → 手机
```

## 信息源（15个）

| 分类 | 数量 | 源 |
|------|------|-----|
| 智库分析 | 7 | CSIS, Foreign Affairs, CSET, The Diplomat, ASPI, RAND, ITIF |
| 科技深度分析 | 4 | IEEE Spectrum, Wired, Ars Technica, The Gradient |
| 投资人分析 | 4 | Stratechery, Benedict Evans, ARK Invest, SemiAnalysis |

## GitHub Actions 部署步骤

### 1. 创建 GitHub 仓库

将 `rss_monitor/` 目录内容上传到 GitHub 仓库。

### 2. 配置 GitHub Secrets

在仓库 Settings → Secrets and variables → Actions 中添加：

| Secret 名 | 值 | 说明 |
|-----------|-----|------|
| `DEEPSEEK_API_KEY` | 你的 DeepSeek API Key | 从 https://platform.deepseek.com 获取 |
| `NTFY_TOPIC` | `rss_digest_8a3f7x` | ntfy.sh 订阅 topic |

### 3. 确认文件结构

```
rss_monitor/
├── .github/workflows/daily-digest.yml  # GitHub Actions 工作流
├── .gitignore
├── requirements.txt
├── fetch_rss.py          # Step 1: 抓取 RSS
├── ai_filter.py          # Step 2: AI 筛选 + 生成摘要
├── push_ntfy.py          # Step 3: 推送到 ntfy
├── pushed_links.json     # 去重状态（自动回写）
└── last_run.txt          # 上次运行时间（自动回写）
```

### 4. 手动测试

在 GitHub 仓库 → Actions 页面，选择 "RSS Digest Daily" → "Run workflow" 手动触发一次。

### 5. 手机订阅

在手机上安装 ntfy 应用，订阅 topic `rss_digest_8a3f7x`。

## 本地运行

```bash
# 设置环境变量
export DEEPSEEK_API_KEY="your-api-key"
export NTFY_TOPIC="rss_digest_8a3f7x"

# 或创建 ntfy_topic.txt 文件
echo "rss_digest_8a3f7x" > ntfy_topic.txt

# 运行
pip install -r requirements.txt
python fetch_rss.py
python ai_filter.py
python push_ntfy.py
```

## 筛选主题

AI 筛选保留以下主题的文章：
- 脑机接口（BCI）/ 神经科学
- AI 技术与商业应用
- 中美科技竞争（半导体/出口管制）
- AI 医疗 / AI 教育
- 光互联 / 光子学
- AI 安全 / AI 监管
- 科技政策 / 国家安全科技

## 推送格式

- 总览消息：时间窗口 + 各分类文章数
- 分类消息：两句话摘要 + 原文链接（明面可见）
- 深度拆解：折叠在 `<details>` 中（核心论点/技术细节/影响分析/关键数据/启示）
- 超长文章自动拆分为多条消息（ntfy 4KB 限制）
