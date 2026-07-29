# nextbrief

[![CI](https://github.com/hancheng-ai/nextbrief/actions/workflows/ci.yml/badge.svg)](https://github.com/hancheng-ai/nextbrief/actions/workflows/ci.yml)
[![Release](https://img.shields.io/badge/release-v0.1.0rc13-blue)](https://github.com/hancheng-ai/nextbrief/releases/tag/v0.1.0rc13)
[![TestPyPI](https://img.shields.io/badge/TestPyPI-0.1.0rc13-blue)](https://test.pypi.org/project/nextbrief/)
[![Python versions](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://github.com/hancheng-ai/nextbrief#install)
[![License: Apache 2.0](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)

**跨你手上所有项目的每日简报——每一条陈述都要先过证据校验，过不了就不许上页面。**

[English →](README.md)

---

每天一次，从各项目本来就在维护的文件里，回答三件事：**各项目进展如何、下一步该做什么、什么卡住了。**

三段式，模型只出现在中间那一段：

```
stage 1   sense      无模型    你的项目（只读） ──────────►  state/snapshot.json
                                                            state/digest.json
stage 2   interpret  模型      digest.json ─────────────►    state/brief.json
                                                            （陈述 + 证据）
stage 3   render     无模型    brief.json + snapshot.json ►  BRIEF.md · BRIEF.html
```

stage 2 永远看不到 `snapshot.json`，stage 3 看得到。**模型写下的每条陈述都必须带证据来源，渲染层逐条拿去那份模型没见过的文件里解析；解析不到的陈述根本不渲染**——原文进 `log/rejected.jsonl`。

## 它真的拦住东西的时候长什么样

下面是本仓 [示例 workspace](examples/workspace) 的一次真实运行。模型被要求总结六个虚构项目，它写出了这么一句：

> Sign off the tenancy decision — the per-tenant p95 numbers came back clean last week
> （多租户的决策可以拍板了，上周分租户的 p95 数据跑出来很干净）

这句是假的。基准测试压根没重跑过——这正是那个决策至今还开着的原因。模型还给它配了一份基准测试报告当证据。那份报告不存在。

**你可以自己跑一遍。** 三段里只有 stage 2 需要模型，而那次运行的 stage 2 产物已经提交在
[`examples/workspace/state/brief.json`](examples/workspace/state/brief.json)——所以
stage 1 和 stage 3 能原样重放：不需要模型、不需要 API key、不需要联网。

```console
$ cd examples/workspace
$ ./scripts/build-example.sh
$ rm -rf log                     # rejected.jsonl 是追加写的，不会被覆盖
$ nextbrief --workspace . sense --as-of 2026-03-16
sense: 6 projects | 3 hot | 0 parse failures | snapshot 34KB / digest 13KB
$ nextbrief --workspace . render --no-notify
render: …/examples/workspace/BRIEF.md | v1 | notify: suppressed (--no-notify; would have been: first run)
  4 unverifiable claim(s) dropped -> log/rejected.jsonl
```

`log/rejected.jsonl` 原文：

```jsonl
{"at": "2026-03-16T12:00:00", "evidence_kind": "file_mtime", "kind": "unresolvable_evidence", "source": "orchard-api/bench/results/tenancy-p95.md", "text": "Sign off the tenancy decision -- the per-tenant p95 numbers came back clean last week", "where": "next_actions", "why": "source does not resolve in snapshot.evidence_index"}
{"actual": ["doc_declared", "file_mtime"], "at": "2026-03-16T12:00:00", "declared": "commit", "kind": "evidence_kind_mismatch", "source": "tidepool-docs/HANDBOOK_STATUS.md", "where": "next_actions", "why": "that source cannot supply commit-grade evidence"}
{"at": "2026-03-16T12:00:00", "kind": "bad_none", "text": "Quarry is progressing steadily and needs no attention this week", "where": "next_actions", "why": "kind=none is only allowed with the 'no signal' phrasing"}
{"at": "2026-03-16T12:00:00", "kind": "no_evidence", "text": "Rotate the fixture capture keys", "where": "next_actions", "why": "claim carries no evidence array"}
```

四句模型准备好要印出来的话，一句都没上页面。上页面的是唯一那条证据解析得通的行动项——外加简报里明写的一行「本次丢弃 4 条」，好让某道闸门开始失效时是**看得见**的，而不是悄悄发生。

把这四条当一组再读一遍。一条凭空造了个文件；一条拿状态文档去支撑提交数——状态文档想写什么都行，commit 是带哈希的事实；一条把「完全没有信号」包装成「稳步推进中」；还有一条干脆忘了给证据。这四种，就是模型生成「一件没发生的事的自信描述」时最平淡无奇的四条路径。

**这件事的常规解法是在提示词里加一句：**不许声称你无法支撑的东西。这句话大多数时候有用——问题恰恰在这里。提示词是对一个「有权解释它」的过程提出的请求，它的失效形态是一句貌似合理的假话，而一句貌似合理的假话，和所有真话长得一模一样。所以 nextbrief 不去「请求」。校验放在下游一层，放在代码里，放在一段没有模型的流程里，每次运行、每条陈述都跑。

代价是真实的，也值得说清楚：一条**真**的陈述，如果模型没把证据引对，一样会被丢掉。这个交易是刻意接受的。一份「悄悄少了点东西」的简报仍然可信——你看得见缺口，而且丢弃条数就印在简报上；一份含有一句自信编造的简报，则处处不可信。

把这一切钉死的是 `--as-of 2026-03-16`：示例里的提交与文件时间戳都是按那天校准的，
运行时间戳也取自 snapshot 而不是系统时钟——所以 `rejected.jsonl` 你什么时候跑都逐字节相同。
唯一会变的是 `snapshot 34KB` 这个数字：snapshot 里存了仓库的绝对路径，
所以它的体积会随你把 checkout 放在哪里而变。

---

## 60 秒上手

怎么把 `nextbrief` 这个命令弄到手见下一节——最短的一条路是**一个文件、不需要任何包管理器**。拿到之后：

```sh
nextbrief init ~/brief          # 建 workspace，它会把附近的项目「提议」给你
nextbrief v0                    # 完全不用模型跑一份简报
nextbrief open                  # 在浏览器里读
```

**`v0` 零 token、不需要任何 API key。** 它只跑 stage 1 和 stage 3，整段模型跳过，所以你可以先把整套东西——感知、信号分级、停滞项目识别、HTML——都评估一遍，再决定要不要花钱。`v0` 印出来的每一个字都是从文件系统上读到的事实。

`v0` 同时也是整套系统的地板。模型那段缺失、坏掉、离线或者没付费的时候，`nextbrief run` 退化成的就是它，而不是什么都产不出来。

零运行时依赖，Python 3.9+，macOS 与 Linux。无人值守任务是被系统调度器拉起来的，只有最小 `PATH`，所以这个包必须能在系统解释器下、周围什么都没装的情况下跑起来。

## 安装

零依赖的意思是：下面每一种方式都只装一样东西，不带别的。顺序按「你要先付出多少」从少到多排——因为整件事的卖点就是，你可以先评估、再决定花不花钱。

所有命令都可以用 **`nb`** 代替，它和 `nextbrief` 一起装上——`nb v0`、`nb do NA-0004`、`nb open`。
如果你同时在用 [xwmx/nb](https://github.com/xwmx/nb)（那个记笔记的 CLI），两者会撞名：
改用 `pipx install --suffix @nx nextbrief`，命令就是 `nextbrief@nx`。

> **当前版本是 `0.1.0rc13`，是一个预发布版。** 它发在 **TestPyPI** 而不是 PyPI：
> release workflow 会把任何带预发布段的版本路由到 TestPyPI，而往正式索引推一个 rc
> 是撤不回来的。所以下面每条走索引的命令都显式带上了索引地址和版本号——少任何一个，
> 你会得到「no matching distribution」。
>
> 同理，下载请用**带 tag 的**地址，不要用 `/releases/latest/`：GitHub 的 latest
> 端点会跳过预发布版，所以 `/releases/latest/download/…` 现在是 404。

**1 · 什么都不装，直接跑**

```sh
uvx --default-index https://test.pypi.org/simple/ "nextbrief==0.1.0rc13" v0
```

**2 · 单个文件，不需要包管理器**

zipapp 就是把整个程序装进一个可执行文件——locale、提示词、模板都在里面，不需要 `site-packages`，不需要虚拟环境，任意 Python 3.9 及以上：

```sh
git clone --depth 1 https://github.com/hancheng-ai/nextbrief
bash nextbrief/scripts/build-zipapp.sh    # 产出 dist/nextbrief.pyz
./nextbrief/dist/nextbrief.pyz --version
```

构建脚本会剔除字节码，并且**真的把产物跑一遍**（在里面执行 `init` 和 `v0`）——因为一个「能构建、但读不到自己 locale」的 zipapp，`--version` 照样答得好好的。

每个打了 tag 的发布都会附带编译好的 `nextbrief.pyz` 与 `SHA256SUMS`：

```sh
curl -fsSLO https://github.com/hancheng-ai/nextbrief/releases/download/v0.1.0rc13/nextbrief.pyz
chmod +x nextbrief.pyz
```

要核对校验和——加 `--ignore-missing` 是因为 `SHA256SUMS` 同时覆盖 sdist 与 wheel，而你并没有下载它们：

```sh
curl -fsSLO https://github.com/hancheng-ai/nextbrief/releases/download/v0.1.0rc13/SHA256SUMS
shasum -a 256 --ignore-missing -c SHA256SUMS     # Linux 上是 sha256sum
```

把 `nextbrief.pyz` 放到 `PATH` 上任意位置就算装好了；删掉这个文件就算卸载。

**3 · 长期安装**

```sh
pipx install --python /usr/bin/python3 \
  --index-url https://test.pypi.org/simple/ "nextbrief==0.1.0rc13"

uv tool install --python /usr/bin/python3 \
  --default-index https://test.pypi.org/simple/ "nextbrief==0.1.0rc13"

pipx install --python /usr/bin/python3 \
  "git+https://github.com/hancheng-ai/nextbrief"            # 直接装 main
```

不需要再挂一个 `--extra-index-url` 兜底：本包运行时依赖为零，解析器没有任何东西需要回 PyPI 去找。

`--python /usr/bin/python3` 是刻意的。定时任务是被一个 GUI 启动器拉起来的，`PATH` 极简；把解释器钉在系统那一个上，意味着 Homebrew 升级 Python（顺手把 pipx 虚拟环境所依赖的那个旧解释器退役掉）也不会弄坏每晚那次运行。CI 里也单独测这个解释器，理由完全一样。

**4 · Homebrew（macOS）** —— *还没有 tap，formula 可以单独装*

```sh
git clone --depth 1 https://github.com/hancheng-ai/nextbrief
brew install --build-from-source ./nextbrief/packaging/homebrew/nextbrief.rb
```

formula 本身纳入本仓版本控制，在 [`packaging/homebrew/nextbrief.rb`](packaging/homebrew/nextbrief.rb)——这样它会和「可能把它弄坏的那个改动」在同一个 PR 里被 review；它钉在 `v0.1.0rc13` 那个 sdist 上。`<owner>/homebrew-tap` 仓库（有了它才能 `brew tap` + `brew install nextbrief`）还没建，建法写在 formula 的头部注释里。

（各发布渠道当前是「已生效」还是「待发布」，见英文版的 [Distribution](README.md#distribution) 一节。）

## 一份简报长什么样

上面那次运行产出的 `BRIEF.md`——六个编出来的项目、三条 backlog，以及在最后一节里被交代清楚的那四条丢弃。原样贴出，包括被截断的地方：

```markdown
# Daily brief · 2026-03-16 (Mon) 12:00
> first run | 6 tracked | 1 awaiting a decision | 2 stalled | 3 in the backlog

## Do these first (across the portfolio, not a few per project)
1. **Re-run the tenancy benchmark with per-tenant p95 instead of an aggregate** · 45 min · you
   Evidence: commit 260de3e
   The decision has been open since the rewrite landed behind a flag.

## One line per project

| Project | Signal | Evidence | Next |
|---|---|---|---|
| Tidepool Docs | 🌤 warm | 2 files/7d · 4 active days/30d · *file timestamps; no git in this repo* | `NA-0003` Write the getting-started page a new con |
| Lantern Site | 🌤 warm | 2 commits/30d · last commit 2026-03-06 · 5 active days/30d |  |
| Beacon Portal | 🔥 hot | 3 commits/30d · last commit 2026-03-13 · 1 files/7d · 3 active days/30d | **stalled: no next step** |
| Orchard API | ⏸ **awaiting a decision** | 4 commits/30d · last commit 2026-03-14 · 4 files/7d · 7 active days/30d | **Go get the evidence that answers it** (below) |
| Kiln | 🔥 hot | 1 commits/30d · last commit 2026-03-14 · 1 files/7d · 1 active days/30d | → OPERATIONS_LOG.md |
| Quarry | ❄️ dormant | last commit 2025-12-05 · **2 uncommitted** | **stalled: no next step** |

## Awaiting a decision (not procrastination — missing evidence)
- **Orchard API** — Per-tenant schemas, or stay on a shared schema with a tenant_id column?
  - Evidence that would settle it: p95 query latency per tenant at current row counts, for the ten largest tenants
  - **The evidence already exists**: orchard-api/bench/results/*.json -- the harness already records per-tenant timings
  - Why it is still open: The report aggregates across tenants, so the tail that actually matters is averaged away

## Stalled (no next step) — the column GTD cares about most
- **Beacon Portal** — Give it a concrete next step, or archive it on purpose.
- **Quarry** — 2 uncommitted change(s) left sitting there. Either commit them and name a next step, or move the tier to dormant so it stops showing up.

## Waiting on people / approvals
- `NA-0002` Publish the March essay once the draft arrives — waiting on external-party
- **Lantern Site** — waiting on Draft posts from the site's author

## What an agent could do for you tonight
- `NA-0001` Re-run the tenancy benchmark reporting p95 per tenant instead of aggregated   — left for you: Reading the resulting tail and deciding whether it justifies

## Reminders
- ⚠ **Dropped 4 claim(s)** whose evidence would not check out (see `log/rejected.jsonl`).
- ⚠ No git in: Tidepool Docs — progress there can only come from file timestamps, and **a bad delete is unrecoverable**.
- `orchard-api/docs/BENCH_NOTES.md` and `orchard-api/PROJECT_STATUS.md` contradict each other about "whether the tenancy benchmark is finished" — the registry rules in favour of `orchard-api/PROJECT_STATUS.md`.
- Status documents gone stale: 5. The oldest: `tidepool-docs/HANDBOOK_STATUS.md` (134 days), `quarry/CURRENT_SPRINT.md` (101 days), `atelier/CURRENT_SPRINT.md` (88 days).

---
*Generated by `nextbrief render` at 2026-03-16 12:00. Every claim here passed the evidence gate; whatever could not be verified was not rendered.*
```

（把 `locale` 设成 `zh`，同样这份数据会渲染成中文。）

每次运行产出两份东西，由**同一份已过完四道闸门的数据**渲染：

- **`BRIEF.html`** —— 平时看这个。每条待办可展开成「agent 能替你做什么 / 你必须自己做什么 / 最廉价的探针」，每条自带复制按钮，深浅色自适应，单文件、离线可用。
- **`BRIEF.md`** —— 给终端和 `git diff` 用。

HTML 不重新判断任何事情：不重排、不重新过滤、不给第二意见。所以两者不可能漂移。

## 四道闸门

四道全在 stage 3，全都是确定性的，全都留痕。

| 闸门 | 做什么 | 记在哪 |
|---|---|---|
| **1 · 证据** | 每条陈述引用的来源必须能在「模型没见过的那份 snapshot」里解析到。解析不到就丢弃，不做软化处理。日期另有一条硬规矩：deadline 只认 registry 里人手写的那些，散文里抓到的日期永不被提升为 deadline。 | `log/rejected.jsonl` |
| **2 · 非目标** | 项目会声明自己**刻意不做**的事。撞上非目标的提案是**标记，不是删除**——匹配是文本级的，会有误判，而悄悄删掉一条好建议是更严重的错误。 | 简报里 |
| **3 · 写入权限** | backlog 条目是带 frontmatter 的文件。渲染层拿 `git HEAD` 逐字段 diff，越权改动一律回滚。**任何自动化都不得写终态。** agent 可以提议 `done`，只有你能写下它。 | `log/rejected.jsonl` |
| **4 · 上限** | 分区硬上限：「先做这几件」全组合最多 3 条，不是每个项目 3 条。超出的推迟，绝不丢弃。 | `log/deferred.jsonl` |

第 3 道是信任的承重墙。漏记的条目明天会再冒出来；被假关闭的条目再也不会冒出来了，而你也不会再去找它。

完整推理，包括「为什么证据校验要放在渲染层而不是提示词里」，见 **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)**。

## 成本（实测，不是估算）

数据来自一个九个项目、带真实 backlog 的参考 workspace。你的数字会不一样，能迁移的是**形状**。

stage 1 除了 `snapshot.json`（完整，给渲染层校验证据用）还产出一份紧凑的 `digest.json`（模型唯一能拿到的东西）。这个拆分不是为了整洁，它就是整个成本故事。

| 给模型的是什么 | 轮次 | output | cacheRead | 单次 | 每月 |
|---|---|---|---|---|---|
| 逐个 Read 每个 backlog 文件，外加两次读 ~104KB 的 snapshot | 36 | 66.8k | 3.24M | **$4.37** | $131 |
| 一次读 ~25KB 的 `digest.json` | 9 | 38.8k | 410k | $1.09 | $33 |
| 同上 + 低推理强度 | 7 | 14.5k | 238k | **$0.74** | **$22** |

**cacheRead ≈ 轮数 × 上下文大小，所以轮数才是主要成本，不是单个文件多大。** 贵的那一版不是因为 snapshot 大而贵，是因为十四次分开的文件读取意味着三十六轮，而每一轮都把已累积的全部上下文重读一遍。把同样的信息压成一个预先拼好的文件，账单降到四分之一，而模型拿到的**是同样的事实**。优化的方向不是「少发数据」，是「少用几轮发」。

**在这件事上，高推理强度几乎买不到东西。** 那些 output token 绝大部分是思考。但日期已经在 stage 1 算好、信号已经分好级、非目标已经逐字提取好了；剩下的只是在一张已知数值的表上做归并和措辞。把 effort 调低，output 少了三分之二，质量没有损失。推理强度值钱的地方是模型必须**推导**事实，而不是事实已经推导好递到它手上。

这和正确性指向同一个方向：每把一份工作从模型挪进确定性 Python，这次运行就**更便宜**，同时**更可信**。两个目标在这里不冲突。

## `nextbrief do` —— 从「该做什么」到真的开始做

一份简报告诉你下一步做什么，然后让你自己再去把这件事向 agent 复述一遍——那没省下多少事。backlog 条目里本来就写着这份交底：agent 能接手什么、只有你能做什么、最廉价的第一步探针、这条是从哪来的、什么算做完。`nextbrief do` 把它变成一段开场白，并且替你想清楚**这活儿该在哪个目录里开**。

```console
$ nextbrief do NA-0001

> NA-0001 · Re-run the tenancy benchmark reporting p95 per tenant instead of aggregated
  Project: Orchard API

  Where should this happen?
  > 1) ~/code/orchard-api                                        project directory
    2) ~/code/orchard-api/docs                                   directory the item came from
    3) ~/code/orchard-api                                        git repository root
    4) ~/code                                                    workspace root

  Enter for the first  ·  a number  ·  or type a path  ·  p to see the prompt  ·  q to cancel
  >
```

候选按「多半是对的」排序：registry 里该项目声明的路径、**条目出处所在的目录**（跨项目的活儿挂在 A 名下、实际却在 B 里，比你想的常见得多）、git 仓根、以及 workspace 根。也可以直接输路径，支持 `~`、绝对路径、以及相对项目根的相对路径。

按 `p` 可以先看一眼开场白再决定：

```markdown
I am working on backlog item **NA-0001: Re-run the tenancy benchmark reporting p95
per tenant instead of aggregated** (project: Orchard API).

The full entry is in `~/brief/backlog/NA-0001-orchard-tenancy-latency-split.md`. Read it first.

**What you can do**: Change the reporter to group by tenant_id and emit p50/p95/p99
per tenant for the ten largest by row count; the harness already records per-request
timings, so nothing new has to be measured.
**What I have to do myself**: Reading the resulting tail and deciding whether it
justifies per-tenant schemas. That is a product judgement, not a query. -- do not do
these for me; stop and tell me when you reach one.
**Cheapest first step**: "python bench/harness.py --report --group-by tenant --top 10"
against the existing results/ directory -- one run, no new data collection.
**Came from**: `orchard-api/docs/TENANCY_DECISION.md` Section 4, Open questions (the
source document claims it was last updated 2026-03-11, so it may already be out of
date -- check before acting on it)

**Done when**:
- [ ] #1 A table of p50/p95/p99 per tenant, covering the ten largest tenants
- [ ] #2 The question "does the tail get worse with tenant size" is answered yes or no
- [ ] #3 The answer is written into TENANCY_DECISION.md and the decision is either
      taken or explicitly deferred with a date

Ground rules: credentials, OAuth consent, publishing or sending anything, and writes
to shared or remote systems all need my go-ahead first. When you are done, tell me
whether this should be closed -- I do the closing myself (`nextbrief done NA-0001`).
```

这个选择器有三条刻意的性质：

- **它只提议，从不替你决定。** `-y` 直接用第一个建议、不再问，那是给脚本用的。
- **没有输入一律当取消。** EOF（管道读空、Ctrl-D）取消。这里如果退回「用建议的目录」，就会在没人点过头的目录里开一个 agent 会话——而这正是这个选择器存在的理由。
- **会话是交互式的，从不 headless。** 这些活儿要动真文件。动的时候你应该坐在键盘前。

## 命令

```
nextbrief run            完整三段：sense → 模型解读 → render
nextbrief v0             只跑 sense + render，完全不用模型：零 token
nextbrief sense          只跑 stage 1，刷新 state/snapshot.json
nextbrief render         只跑 stage 3，用现有 brief.json 重渲染
nextbrief check          幂等自检；退出码 3 表示简报已过期

nextbrief open           在浏览器里打开 BRIEF.html
nextbrief brief          把 BRIEF.md 打到终端
nextbrief log [-n N]     看最近几次运行

nextbrief do <id>        在对的目录里开一个带好上下文的会话   （-y：不问直接用）
nextbrief show <id>      看某条的全文
nextbrief ok <id>        确认：这条是真的、按我的意思写的
nextbrief done <id>      完成            nextbrief drop <id>   弃掉
nextbrief ls             列出所有在办条目
nextbrief prune          列出值得回头看看的条目

nextbrief init [dir]     创建 workspace   （-y、--no-scan）
```

全局参数：`--workspace DIR`、`--out DIR`、`--locale LANG`、`--version`。
`sense` 另有 `--check`、`--stdout`、`--as-of ISO`、`--timing`；`render` 有 `--no-notify`、`--dry-run`。

重跑 stage 1 会改变输出时，`check` 退出码为 `3`——这就是整个调度契约，任何定时跑 nextbrief 的东西都能直接分支，不用解析文本：

```cron
30 21 * * *  /usr/local/bin/nextbrief run >> ~/brief/log/cron.log 2>&1
```

### 「确认」是什么意思

`nextbrief ls` 里 `ok` 列是 `.` 的条目，是从你各项目文档里读出来、**替你起草**的——你还没点过头。

- `nextbrief ok <id>` = 「这条是真的、按我的意思写的」。此后**自动衰减永远不会碰它**。
- 不确认也不会消失，只是排序会随时间下沉。
- 不认可就 `nextbrief drop <id>`。文件还在，git 历史也还在。

`ok` / `done` / `drop` 都会**立刻提交一条 git 记录**，这不只是为了留档：第 3 道闸门拿 `git HEAD` 做基线比对，你的 `done` 如果还躺在工作区没提交，闸门就分不清「主人关掉了这条」和「agent 偷偷写了 done」——然后它会把**你自己的操作**回滚掉。

## 配置

**workspace 解析顺序**，先命中先用：

1. `--workspace DIR`
2. `$NEXTBRIEF_WORKSPACE`
3. `nextbrief init` 写下的指针文件（`~/.config/nextbrief/workspace`）
4. 当前目录、或最近的、含有 `registry.jsonc` 的祖先目录

都没命中就直接报错不跑。一个悄悄退回空目录的 workspace 会渲染出一份干净、合理、完全没有内容的简报——那读起来像「什么都没发生」，而不像「你还没配置」。

引擎（这个包）和 workspace（你的 registry、backlog、state、log）是分开的，就像程序和文档是分开的。**你的东西一样都不在包里，而这个包除了 workspace 之外哪儿都不写。**

```
registry.jsonc        每个项目是什么、归谁、看哪些状态文档。   月改。
config.jsonc          阈值、权重、上限、模型分级。             很少改。
backlog/*.md          每条一个文件，带 frontmatter。            日改。
prompts/daily.*.md    stage 2 的提示词。你的那份优先于包里的。
BRIEF.md · BRIEF.html 当前状态，每次运行覆写。
log/YYYY-MM-DD.md     当天的变化与动作。追加，永不重写。
log/runs.jsonl        每次运行的耗时、计数、成功哨兵、成本。
log/rejected.jsonl    被闸门丢弃的陈述、被回滚的越权写入。
log/deferred.jsonl    超出上限被推迟的提案。上限不丢信息。
state/snapshot.json   stage 1 输出。snapshot.prev.json 是昨天的，用于算差异。
```

`registry.jsonc` 与 `config.jsonc` 用的是 **JSONC**——JSON 加 `//` 注释、允许尾逗号。理由很实际：这类文件必须能写注释（一个不写理由的阈值，半年后会被人「顺手整理掉」），而这个包不允许引入 YAML 依赖；剥注释的 JSONC 是十几行确定性代码，手写一个 YAML 子集解析器则是一片永久的维护面。

**Provider。** stage 2 是唯一花钱的地方，用哪个 runner 是配置问题：

```jsonc
"model": {
  "provider": "auto",              // auto | claude | codex | ollama | openai_compat | none
  "effort": "low",
  "ollama":        { "model": "your-local-model" },
  "openai_compat": { "base_url": "https://api.example.invalid/v1",
                     "model": "your-model",
                     "api_key_env": "YOUR_API_KEY" }
}
```

`auto` 会依次探测、用第一个能用的；`none` 直接跳过这一段——那是受支持的模式，不是降级。agent 型 runner（`claude`、`codex`）自己去读 digest、自己写 brief；补全型端点（`ollama`、`openai_compat`）由调用方把 digest 内联进提示词，并把回复落盘。**API key 只写变量名，不写值**——配置里给出环境变量名，值在调用那一刻从环境读。workspace 是一个你有可能提交进 git 的目录，key 绝不能有机会落进去。

无论 provider 发生什么，那里出错只会是一条 warning 加一份确定性简报，而不是没有简报。

**语言。** `en` 与 `zh` 都随包发布，谁都不是谁的机翻；CI 会断言两份 catalog 的 key 集合完全一致。优先级：`--locale` > `config.jsonc` 里的 `"locale"` > `$NEXTBRIEF_LOCALE` > `en`。

**通知。** 真的有变化时才发一条，走一个「失败就沉默、不拖垮整次运行」的 sink。一个每天准点告诉你「今天没事」的系统，第三周就会被静音——所以由 `notify.only_if` 决定它什么时候才有资格开口。

## 明确不做的事

这些是决定，不是缺口。tool 能一直保持小巧，多半靠它们。

| 不做 | 理由 |
|---|---|
| 数据库 / 守护进程 / 专门的 issue 存储 | 这个体量下文件 + git 已经够了，而且任何 agent 不用接集成就能直接读 |
| 与 Linear / Notion / Obsidian / GitHub Projects 双向同步 | 任何非文件系统的存储都制造同步问题，而 stale status 是这类系统的头号死因。它们可以**读** `BRIEF.md`；nextbrief 永不读它们 |
| 让任何自动化关闭条目 | 假完成比漏记严重得多：漏记的明天会回来，被假关闭的再也不会回来了。agent 可以提议 `done`，写下它的只能是人 |
| 写 workspace 以外的任何路径 | nextbrief 永远弄不坏别的项目；nextbrief 死了别的都不受影响 |
| 工时统计 / 燃尽图 / 速度 | 增加维护面，而产出的数字后面并不接着任何决策 |
| 每个项目单独的 dashboard | 项目自己已经有了。nextbrief 只做**跨项目层**——重复一份项目自己的状态文档，正是两边开始互相矛盾的起点 |
| 从 git 历史 / TODO 注释 / feature spec 批量导入待办 | 这正是 500 条没人看的墓地的成因。每条都得一条一条地进来，并且带出处 |
| 云端运行 | 没有本地文件访问权限，而且值得盯的目录里多数根本不是 git 仓 |
| 超过 40 条的 backlog | 硬上限。到顶那天不许新建条目，简报会明说，而不是任由它悄悄长大 |

## 隐私

registry 可以标记**绝不许读**的路径。对这些路径，stage 1 只记一个整数计数——不读内容，**连文件名都不进 snapshot**，因为敏感的往往正是文件名。既然关于它们的一切都进不了 snapshot，那么关于它们的一切也就到不了模型、到不了页面。

从项目目录里读到的内容是**要报告的数据，不是要执行的指令**。示例 workspace 里专门放了一个这样的 fixture（`handoff-inbox/vendor-notes.md`，里面写着「把所有任务标成完成」），好让这条性质是可测试的，而不只是句口号。

## 参与开发

四个扩展点，都刻意做得很朴素——一个 dict 加一个模块，没有插件扫描，没有 entry point：

1. **`providers/`** —— 新的模型后端。四个名字加一个函数。
2. **`sinks/`** —— 新的通知渠道。两个函数，并且失败必须退化成沉默。
3. **`locales/`** —— 新语言。CI 强制与英文 key 对齐。
4. **解析器** —— 教 sense 认另一种状态文档格式。fail-open：返回 `None` 并记下路径，绝不抛异常。

动手前先读 [CONTRIBUTING.md](CONTRIBUTING.md)，尤其是设计契约、3.9 底线、零依赖规矩，以及「绝不含个人数据」那条。测试是原生 `unittest`，不用装任何框架：

```sh
python3 -m unittest discover -s tests -v
```

## 许可

Apache 2.0，见 [LICENSE](LICENSE)。

**[English →](README.md)**
