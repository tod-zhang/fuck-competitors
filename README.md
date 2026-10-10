<div align="center">

# Fuck Competitors

**盯紧竞争对手的网站，把每一次变化都记成日志。**

[![CI](https://github.com/tod-zhang/fuck-competitors/actions/workflows/ci.yml/badge.svg)](https://github.com/tod-zhang/fuck-competitors/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Docker](https://img.shields.io/badge/docker-ready-2496ED?logo=docker&logoColor=white)](Dockerfile)

一个自托管、开源的竞品监控应用。填入竞品的 `sitemap.xml`，它会定期巡检页面的
**新增 / 删减 / 修改**，像写观察日记一样记录下来。温暖的手账风界面，自带像素眼睛吉祥物。

**[👀 在线演示 → competitors.fuckseo.io](https://competitors.fuckseo.io)**（示例数据，只读）

</div>

## 界面截图

| 最近动态 | 变更日志 |
| --- | --- |
| ![最近动态](docs/screenshots/overview.png) | ![变更日志](docs/screenshots/changelog.png) |
| **内容 diff** | **竞品列表** |
| ![内容 diff](docs/screenshots/diff-drawer.png) | ![竞品列表](docs/screenshots/sites.png) |

---

## 功能特性

- 📒 **变更日志** —— 竞品页面的新增、删减、内容修改，按日期分组、时间倒序，可按竞品和变更类型筛选
- 📰 **内容 Feed** —— 竞品新发的文章排成收件箱，键盘刷：<kbd>j</kbd>/<kbd>k</kbd> 上下、<kbd>s</kbd> 保存、<kbd>d</kbd> 忽略、<kbd>n</kbd> 写笔记；附带 AI 功能（见下文「内容 Feed」）
- 🔍 **两层监控** —— 轻量的 sitemap 增删监控（全站）+ 可选的正文逐行 diff（按竞品开启）
- 🧠 **AI 分析（MCP）** —— 内置只读 MCP server，把变化和 diff 开放给 Claude Code / Codex 等 Agent，直接分析"对手在优化什么"
- 🤫 **静默基线** —— 首次巡检只记录"现在有哪些页"，不会用初始清单刷屏日志
- ⚙️ **竞品设置** —— 随时改名称 / sitemap 地址 / 巡检频率、立即巡检、删除竞品
- 🐳 **一条命令部署** —— 单容器 + 单个 SQLite 文件，无需任何外部服务
- 🪶 **无前端构建** —— 服务端渲染 + 原生 JS，克隆即跑

## 快速开始

```bash
docker compose up
# 打开 http://localhost:9527
```

就这样 —— 一个容器、一个 SQLite 文件（持久化在主机的 `./data` 目录里），不依赖任何外部服务。

## 安装方式

### 方式一：Docker（推荐）

```bash
git clone https://github.com/tod-zhang/fuck-competitors.git
cd fuck-competitors
docker compose up -d          # 后台运行
# 打开 http://localhost:9527
```

数据库就是主机仓库目录下的 `./data/app.db`。它是**目录挂载**（不是 docker 命名卷），所以重建镜像、`docker compose down -v`、改项目名都不会动它——升级代码后重新部署，竞品记录都还在。需要重置为空白：删掉 `./data` 目录再 `up`。

> 从 CI/脚本里**每次 clone 到新目录**部署的话，相对的 `./data` 会落在新目录里。这种情况把 compose 里的挂载改成一个固定的绝对路径（如 `/var/lib/fuck-competitors/data:/data`），数据就和代码目录解耦了。

仓库自带一个 `Makefile` 封装了常用命令（固定 `-p fuck-competitors` 项目名，并在经典构建器下构建，
这样即使项目目录是中文/非 ASCII 路径也能跑）：

```bash
make up        # 构建并后台启动 app(:9527) + mcp(:9528)
make logs      # 跟踪两个服务的日志
make ps        # 查看容器状态
make down      # 停止并移除容器（保留 ./data 里的数据库）
```

### 方式二：源码本地运行

```bash
git clone https://github.com/tod-zhang/fuck-competitors.git
cd fuck-competitors

python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt

uvicorn app.main:app --port 9527        # 打开 http://localhost:9527
```

> ⚠️ 调度器跑在进程内，请用**单个 worker**（默认即是）。多 worker 会重复触发巡检。

## 使用流程

1. **添加竞品** —— 点「＋ 添加竞品」，填竞品的 `sitemap.xml`，选巡检频率。
   首次巡检是**静默建基线**：只记录当前全部页面，**不会**给每个页面灌一条"新增"
   —— 初始清单里没有"变化"这个信号。
2. **让它跑** —— 每个竞品按各自频率自动重抓（或在卡片设置里点「立即巡检」马上查一次）。
   从第二次巡检起，只有真正的 **新增 / 删减 / 疑似修改** 才会进变更日志。
3. **盯关键内容** —— 想看定价、文案、客户案例的**逐行内容 diff**，在「竞品列表」里
   对该竞品打开**详细监控**开关。开启后，每次详细巡检会抓取它**全部页面**的正文做对比，
   记录到底改了什么。

## AI 分析（MCP）

应用自带一个**只读 MCP server**，把竞品的变化和内容 diff 以工具形式开放给 AI Agent
（Claude Code / Codex / Claude Desktop / ChatGPT 等）。接上之后用**自然语言**问就行 ——
Agent 自己调下面的工具拉数据、读 diff、推理出"对手在优化什么"。

| 工具 | 作用 |
| --- | --- |
| `list_competitors` | 列出监控中的竞品（追踪页数、近 7 天活跃度） |
| `get_changes` | 拉某段时间的增 / 删 / 改（可按竞品、类型过滤） |
| `get_diff` | 某条变更的**逐行内容 diff**（最强信号） |
| `get_page_history` | 单个页面随时间的变化轨迹（如 `/pricing`） |
| `summarize_window` | 一次性打包某竞品 N 天内的全部变化 + diff，直接交给模型分析 |

### 方式一：本地（stdio）—— Claude Code / Codex

在项目根目录建 `.mcp.json`，然后**重启客户端**：

```json
{
  "mcpServers": {
    "fuck-competitors": {
      "command": ".venv/bin/python",
      "args": ["-m", "app.mcp_server"],
      "env": { "FC_DB_URL": "sqlite:///./data/app.db" }
    }
  }
}
```

### 方式二：远程（HTTP）—— 自托管后直接输 URL

`docker compose up` 会同时起一个 **MCP HTTP 服务（端口 9528）**，和应用共用同一个数据库。
在支持远程 MCP 的客户端里填这个地址即可：

```
http://<你的服务器>:9528/mcp
```

- **Claude Code**：`claude mcp add --transport http fuck-competitors http://<host>:9528/mcp`
  （设了 token 再加 `--header "Authorization: Bearer <token>"`）
- **Claude Desktop / ChatGPT**：添加远程 MCP / 连接器，粘贴上面的 URL。

> 🔒 **安全**：这个端点**只读，但会暴露你监控了哪些对手以及它们的 diff**。本机 / 内网用可不设鉴权；
> **暴露到公网必须设 `FC_MCP_TOKEN`**（在 compose 的 `mcp` 服务里），客户端连接时带
> `Authorization: Bearer <token>`。

### 接上之后这样问

```
监控了哪些对手？（list_competitors）
用 summarize_window 看 cowseal 最近 14 天，它在优化什么？按定价 / 定位 / 产品 / 内容分类说。
get_page_history /pricing —— 各家定价页这段时间怎么变的？
```

> ⚠️ **前提：库里得有变化数据。** MCP 只读、不爬取。刚加的竞品只有"静默基线"、没有变化，分析不出东西——
> 变化要随巡检积累（或开详细监控才有内容 diff）。想先试效果，可灌一份演示数据：
> ```bash
> FC_DB_URL="sqlite:///./data/demo.db" python tests/seed_demo.py
> ```
> 再把上面的 `FC_DB_URL` / URL 指向 `./data/demo.db` 即可。

## 内容 Feed（每天早上刷一遍）

侧边栏「内容 Feed」把竞品**新上线的文章**按竞品分组排成卡片（标题、摘要、缩略图、阅读时长），可按 1 / 3 / 7 / 14 / 30 天和竞品筛选，
顶部显示上次巡检时间，「立即刷新」马上重新巡检全部竞品。首次巡检时，还会把 `lastmod` 在 30 天内的文章放进来
（如果一个站把所有页面的 `lastmod` 都标成今天，就不信它）。全程键盘操作：

| 键 | 作用 | 键 | 作用 |
| --- | --- | --- | --- |
| `J` / `K` | 下一篇 / 上一篇 | `N` | 写笔记 |
| `S` | 保存，接着弹出笔记框（`Enter` 保存笔记，`Esc` 跳过） | `O` | 打开原文 |
| `D` | 忽略（每组还有「全部忽略」） | `M` / `W` | 展开相似文章 / 拉关键词数据 |
| `U` | 放回待处理 | `?` | 快捷键说明 |

「趋势」标签页给每个竞品一份 30 天内容策略解读（策略判断 + 反复出现的主题和对应文章 + 可跟进的选题），
超过 7 天或之后又发了新文章的标为过期，可以一键生成全部缺失 / 过期的。

四个 AI 功能，各自填了 key 才启用。key 在侧边栏左下角的「设置」里填（存在本机数据库里，每个服务都有「测试连接」），也可以写进 `.env`（见 `.env.example`），设置里填的优先：

| 功能 | 用什么 | 配置 |
| --- | --- | --- |
| **推荐竞品**：按「我的域名」找自然搜索竞品，一键关注并自动找 sitemap | DataForSEO Labs `competitors_domain` | `FC_DATAFORSEO_LOGIN` / `FC_DATAFORSEO_PASSWORD` |
| **我博客里最接近的文章**：同步你的博客 sitemap，向量 + 余弦相似度给每篇竞品文章找最像的 3 篇 | 任意 OpenAI 格式的 embeddings 接口，默认 OpenRouter `qwen/qwen3-embedding-8b` | `FC_EMBEDDING_API_KEY` |
| **关键词数据**（按需）：这篇文章已排名的词 + 核心词的搜索量 / 难度 / 相关词 | DataForSEO Labs `ranked_keywords` + `related_keywords` | 同上 |
| **趋势（30 天内容策略）**：把竞品最近 30 天的文章交给大模型，归纳策略和主题 | 任意 OpenAI 格式的 chat 接口，默认 DeepSeek | `FC_LLM_BASE_URL` / `FC_LLM_API_KEY` / `FC_LLM_MODEL` |

「我的域名」和「我的博客 sitemap」也在「设置」里填，填完点「同步我的博客」。DataForSEO 按次计费：推荐竞品的结果会缓存，
关键词只在你点按钮时才查。

## 监控原理（两层）

| 层级 | 做什么 | 成本 | 覆盖 |
| --- | --- | --- | --- |
| **基础**（始终开启） | 每次巡检 diff sitemap → 页面**新增 / 删减**；若页面带 `<lastmod>`，时间戳变化会标记一条**「疑似修改」** | 低 | 全部页面 |
| **详细**（每个竞品可选） | 抓取该竞品**全部页面**的正文，逐行 **内容 diff** —— 抓到定价 / 定位 / 文案的具体改动 | 中 | 全部页面（受上限约束） |

> **诚实说明**：基础监控只能"**疑似**"判断修改（且仅当站点诚实填写了 `<lastmod>`），
> 它永远看不到*改了什么*。要看到真正的逐行 diff，必须开启详细监控。界面上这两者刻意区分清楚。

详细监控对全站正文做对比，比只盯几页更吃资源、噪音也更大 —— 所以它**默认关闭、按竞品单独开**，
并用 `FC_DETAILED_MAX_PAGES`（默认 500）兜底，避免超大站把自己拖垮。

## 配置

所有配置都是 `FC_` 前缀的环境变量（见 `.env.example`）：

| 变量 | 默认值 | 含义 |
| --- | --- | --- |
| `FC_DB_URL` | `sqlite:///./data/app.db` | 数据库位置 |
| `FC_DEFAULT_INTERVAL_HOURS` | `24` | 默认巡检间隔 |
| `FC_REQUEST_TIMEOUT` | `20` | 单次请求超时（秒） |
| `FC_MAX_SITEMAP_URLS` | `50000` | 单个 sitemap 的抓取上限 |
| `FC_DETAILED_MAX_PAGES` | `500` | 每次详细巡检内容对比的页面上限 |
| `FC_WRITE_BATCH` | `200` | 巡检写库每 N 行提交一次（频繁释放写锁，避免阻塞并发添加） |
| `FC_SNAPSHOT_RETENTION` | `10` | 每个页面保留的内容快照数 |
| `FC_USER_AGENT` | `FuckCompetitors/0.1 …` | 抓取时使用的 User-Agent |
| `FC_RESPECT_ROBOTS` | `true` | 是否遵守目标站 robots.txt（若某站 robots 误挡了 sitemap，可关掉） |
| `FC_CRAWL_DELAY_SECONDS` | `1.0` | 同一域名两次请求的最小间隔（robots 的 Crawl-delay 更大时取大者） |
| `FC_BLOCK_COOLDOWN_SECONDS` | `900` | 遇到 403 / 无 Retry-After 的 429 后，对该域名退避多久 |
| `FC_DEMO_MODE` | `false` | 只读演示站：每天重置为虚构示例数据，不巡检，拒绝一切写操作（**会清空数据库**，勿对真实数据开启） |
| `FC_MY_DOMAIN` / `FC_MY_SITEMAP_URL` | 空 | 我的域名 / 博客 sitemap 的默认值（也可在 Feed 页里改） |
| `FC_DATAFORSEO_LOGIN` / `FC_DATAFORSEO_PASSWORD` | 空 | DataForSEO 账号（推荐竞品 + 关键词数据） |
| `FC_DATAFORSEO_LOCATION_CODE` / `FC_DATAFORSEO_LANGUAGE_CODE` | `2840` / `en` | 关键词数据的国家和语言 |
| `FC_EMBEDDING_BASE_URL` / `FC_EMBEDDING_API_KEY` / `FC_EMBEDDING_MODEL` | OpenRouter / 空 / `qwen/qwen3-embedding-8b` | 相似文章用的嵌入接口 |
| `FC_LLM_BASE_URL` / `FC_LLM_API_KEY` / `FC_LLM_MODEL` | `https://api.deepseek.com/v1` / 空 / `deepseek-chat` | 策略总结用的大模型（任意 OpenAI 格式） |

## 开发

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 9527   # http://localhost:9527

python tests/test_basic.py             # sitemap 解析 + 增删改 diff（纯标准库）
python tests/test_security.py          # 拒绝 XXE / 实体炸弹的 sitemap
python tests/test_feed.py              # 内容 Feed：入库、相似文章、竞品推荐（不联网）
python tests/e2e_local.py              # 全链路：抓取 → diff → 落库（本地 HTTP 服务）
python tests/seed_demo.py              # 灌入演示数据，方便逛界面
```

## 架构

- **FastAPI** 应用，服务端渲染 **Jinja2** 模板（无构建步骤），抽屉 / 设置 / 筛选用原生 `fetch` + 少量 JS。
- **APScheduler** 每个竞品一个进程内定时任务 → **请用单 worker 运行**。
- **SQLite**（经 SQLModel）：`competitors / pages / changes / snapshots` 四张表。
- `app/monitor/` 是零依赖的核心：`sitemap.py`（抓取 + 解析）、`basic.py`（增删改 diff）、
  `detailed.py`（正文抓取 + 提取）、`diff.py`（逐行 diff）、`favicon.py`（站点图标解析）。

```
app/
├── main.py          # FastAPI 入口 + lifespan（启动调度器）
├── web.py           # 页面路由 + 表单/接口端点
├── service.py       # 巡检编排（基础 + 详细）
├── scheduler.py     # APScheduler 定时任务
├── models.py        # SQLModel 表
├── viewmodels.py    # DB 行 → 模板数据
├── config.py · db.py · timeutil.py
├── monitor/         # sitemap.py · basic.py · detailed.py · diff.py · favicon.py
├── templates/       # index.html + partials/(drawer.html · settings.html)
└── static/          # app.css · app.js · favicon.svg
```

## 安全说明

- Sitemap 是不可信输入，XML 用 **defusedxml** 解析（防 XXE / 实体展开攻击），并有回归测试覆盖。
- 抓取时设置了自定义 `User-Agent` 和超时；请做个好公民，用合理的巡检间隔。
- 站点图标（favicon）由用户浏览器直接向竞品站点请求，并带 `no-referrer`，不经任何第三方。

## 许可证

MIT
