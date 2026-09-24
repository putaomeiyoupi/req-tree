# req-tree

**项目推进中的需求派生与收口的唯一真源。**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.8%2B-blue.svg)](https://www.python.org/downloads/)
![Dependencies](https://img.shields.io/badge/dependencies-0-brightgreen.svg)
![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey.svg)

- **零依赖** —— 单个 Python 文件，仅用标准库
- **跨平台** —— Windows / macOS / Linux
- **离线** —— 数据就是一份 JSON，存在你自己的仓库里
- **无服务** —— 没有后端、没有账号、没有网络请求

---

## 它解决什么问题

需求是长出来的，不是一次性列出来的：

```
1. 计价模块            ← 最开始只有这三条
2. 供货合同
3. 文档库
   │
   └─ 做「1」的时候发现 ↓
      1.1 供应商维度筛选
      1.2 导入模板
      1.3 联合索引      ← 推进 1.3 时又发现 ↓
          1.3.1 索引写入放大
          1.3.2 权限过滤会先砍候选集
```

表格记不下这个结构，于是出现两个典型症状：

- **说不清「这条为什么存在」** —— 三个月后看到「索引写入放大」，没人记得它是从哪条需求、做什么动作时冒出来的
- **说不清「哪些还没收口」** —— 收口发生在对话里、在脑子里，表格里只剩一个「状态：完成」。而「完成」到底是「我改完了」还是「已验证」，没人分得清

req-tree 把**需求之间的派生关系变成一等公民**，并强制每一次收口都留下证据。

---

## 五条不变量

这是它和「又一个 todo 列表」的分界：

**1. 身份与路径分离。**
节点身份是不可变的 `R-0007`；`1.3.2` 只是渲染时算出来的编号。
引用一律用 ID —— 一旦插入或移动节点，编号会整体错位，所有历史引用同时失效。

**2. `done` ≠ `closed`。**
`done` = 我改完了；`closed` = 已验证、已确认收口。两步分开，防止自己给自己盖章。

**3. 收口必须附证据。**
`close` 强制要求至少一条 `--evidence`（`commit:` / `test:` / `doc:` / `file:` / `user:`）。
没有证据的「已关闭」是假关闭，会直接污染真源。

**4. 假收口会被拦下。**
父需求还有未收口的子项时，`close` 直接拒绝。要强行收口必须显式写出理由，
而且**只接受收口当时存在的那批** —— 收口之后新增的子项照样报错。

**5. 只有一个真源，且只能通过 CLI 写。**
`tree.json` 是唯一的事实来源，其余视图都是 `render` 的派生物、会被覆盖。
手改视图 = 制造第二个真源，这是这套系统最大的威胁。

---

## 快速开始

```bash
git clone <本仓库地址> req-tree

cd /path/to/your-project

# 用一个别名省去长路径（Windows 见下）
alias todoctl="python /path/to/req-tree/scripts/todoctl.py"

todoctl init --project my-project

todoctl add --title "支持按供应商维度筛选" \
            --why "现有查询接口没有这个维度，业务侧每次都要手工拼" \
            --done-when "筛选结果与手工核对一致，接口测试通过"

todoctl render
todoctl check
```

打开生成的 `TREE.md`，树就在里面。

`init` 会自动判定真源落点，并在输出里明确告诉你落在哪个目录。

### 真源放在哪

默认规则（`--root` 或环境变量 `TODOCTL_ROOT` 可覆盖）：

| 顺序 | 规则 |
|---|---|
| 1 | 显式 `--root` |
| 2 | 环境变量 `TODOCTL_ROOT` |
| 3 | 当前目录**已是真源**（`docs/requirements/` / `.req-tree/requirements/` / `requirements/` 下有 `tree.json`） |
| 4 | **兼容**：旧版宿主专有路径 `.workbuddy/requirements/` 下已有 `tree.json`（**仅识别，不再新建**，且 `check` 会提示你搬走） |
| 5 | 当前目录**已预建** `docs/requirements/` 或 `.req-tree/requirements/` |
| 6 | 检测到 git 仓库 → `<仓库根>/docs/requirements/` ← **推荐** |
| 7 | 以上都不是 → `<当前目录>/.req-tree/requirements/` |

推荐第 6 条：需求树与代码同版本，`git log` 就是免费的审计日志。

> 裸 `requirements/` 必须**已经是真源**才会被优先命中 ——
> 否则一个存放依赖清单的同名目录会把真源劫持到仓库子目录里，静默产生第二棵树。

---

## 日常节奏

```
todoctl resume                     # ① 开工第一件事：拿回上次现场
todoctl add --parent R-0006 ...    # ② 发现新问题的当下就落库，不要攒
todoctl start R-0006               # ③ 开始做（需要 done_when）
todoctl note R-0006 做到哪 发现了什么   # ④ 做一步记一句，不必加引号
todoctl bench                      # ⑤ 随时看手头同时有几件事
todoctl done R-0006                # ⑥ 实现完成
todoctl close R-0006 --evidence "commit:9f2c1ab"   # ⑦ 验证通过才算收口
todoctl check                      # ⑧ 体检
```

这套系统的成败不在设计，而在**你记不记得记**。所以触发时机被做进了命令里：
`resume` 一条命令拿回现场、`note` 不需要引号、真源写入后视图自动刷新 ——
你不需要记得手动同步任何东西。

### 交错推进是被支持的

真实工作几乎不会按派生顺序走：做 1.1.2 时下一步该做 1.1.3，但你被拉去做 2.1，然后才回头。

这本身没有数据风险（单人顺序操作不会写坏真源）。风险是三个「看不见」，各有对策：

| 风险 | 对策 |
|---|---|
| 忘了回来 | `bench` 显示搁置天数；`W5` 告警（`doing` 超过 7 天未动） |
| 不知道自己在绕 | `update <id> --next R-0008` 声明计划中的下一步 → `start` 别处时**当场提示绕行**，`bench` 标 `↷绕行中` |
| 看不到手头有几件事 | `bench` / `BENCH.md`：全部进行中任务按需求线分组 |

> `--next` 是**节点指针**（可校验、能算出绕行），`--next-step` 是**文字说明**。两者可并存、互不替代。

跳过了计划下一步**不阻断** —— 绕行是正常决策，工具只负责让你「知道自己在绕」，不替你判断该不该绕。

### 退出码

| 码 | 含义 |
|---|---|
| `0` | 成功 |
| `1` | `check` 或 `render` 发现真源有错 |
| `2` | 被强校验或参数校验拒绝 |

> ⚠️ 容易混的一对：`--force` 只跳过「未 `done` 不能收口」的前置检查，**跳不过假收口检查**；
> 放行假收口要用 `--override-reason`。两个都踩到时（状态不是 `done` 且还有开着的子项）**两个都要给**。

---

## 视图：真源与派生物

| 文件 | 性质 | 什么时候看 |
|---|---|---|
| `tree.json` | **真源** | 不要手改 |
| `journal.jsonl` | **真源（追加式）** | 变更日志，`log` 的唯一依据 |
| `baselines.json` | **真源（追加式）** | 基线 —— 不可变里程碑 |
| `snapshots/` | **真源（自动留档）** | 写入前自动快照，无 git 时的回滚依据 |
| `TREE.md` | 派生 | 全树；已全部收口的子树折叠成一行 |
| `ACTIVE.md` | 派生 | 只列未收口项 ← **日常首选** |
| `FOCUS.md` | 派生 | 当前聚焦分支及其派生 |
| `RESUME.md` | 派生 | 上次做到哪、下一步、期间变动 |
| `BENCH.md` | 派生 | 手头全部进行中任务 |
| `BASELINE.md` | 派生 | 最新基线摘要 + 各线进度 |
| `dashboard.html` | 派生 | 单文件看板，可按状态/关键字筛选 |

**判别口诀**：手改会被覆盖的 → 派生物；手改会破坏追溯链的 → 真源。

**派生物会自动刷新** —— 真源任何一次写入成功后，七个视图会被重新生成（`--no-render` 可关）。
视图过期是「记录对不上」的老根源，所以它不由人负责。
（全新真源要先手动跑一次 `render`，之后才自动跟随。）

---

## 命令速览

| 组 | 命令 |
|---|---|
| 建 | `init` |
| 写 | `add` `update` `note` |
| 状态 | `start` `block` `unblock` `done` `close` `drop` |
| 恢复 | `resume` `bench` |
| 查看 | `show` `check` `log` `report` |
| 治理 | `baseline` `restore` |
| 导出 | `render` `export` |

**全部子命令、选项与五个完整工作流**见 [`references/entry-map.md`](references/entry-map.md)。

几个值得单独知道的：

```bash
todoctl bench                              # 台面：手头有哪几件事在同时推
todoctl baseline --name "v0.3 首版"        # 把当前各线成果封成一个不可变时点
todoctl restore --last --yes               # 退回上一次写入之前
todoctl log --line R-0031 --commit-msg     # 按需求线抽提交信息草稿
todoctl export                             # 导出可公开副本（恒脱敏）
```

---

## 安装

### 方式一：作为独立 CLI（任何平台）

```bash
git clone <本仓库地址> req-tree

# macOS / Linux / WSL / Git Bash
alias todoctl="/path/to/req-tree/scripts/todoctl"

# 或直接调 python（最通用，不依赖脚本的可执行位）
alias todoctl="python3 /path/to/req-tree/scripts/todoctl.py"
```

**Windows**：把 `req-tree\scripts` 加进 `PATH`，直接用 `todoctl.cmd`。

三个入口 —— `scripts/todoctl`（POSIX）、`scripts/todoctl.cmd`（Windows）、`scripts/todoctl.py`（通用）——
**都只从 `PATH` 里找 Python，不含任何写死的安装路径**：换机器、换平台、换用户都不用改。

**要求**：Python 3.8 或更高（仅标准库，无需 `pip install`）。

### 方式二：接入你的 AI 宿主

本仓库只带**一份** `SKILL.md`（带 frontmatter 的标准 skill 定义）。

各宿主认的"规则文件"名字并不统一，但**内容本质相同**。所以不要为每个宿主写一份 ——
那是多个真源，必然漂移。**正确做法是让同一个 `SKILL.md` 暴露成各宿主认的名字**：

| 宿主 | 认的文件 / 位置 | 接入方式 |
|---|---|---|
| WorkBuddy | `<skills>/req-tree/SKILL.md` | 整个目录放进 skills 目录 |
| Claude Code | `~/.claude/skills/req-tree/` | 整个目录放进 skills 目录 |
| Codex CLI（OpenAI） | 项目根的 `AGENTS.md` | `ln -s /path/to/req-tree/SKILL.md AGENTS.md` |
| Cursor | `.cursor/rules/` 下的规则文件 | 软链或复制 `SKILL.md` 进去 |
| Gemini CLI | `GEMINI.md` | 同上 |
| 其他 | 任何「项目/工具说明」文件 | 认哪个名字，就链到哪个名字 |

**用符号链接（Windows 用 `mklink`）**：一份内容、多个入口，改一处到处生效。

之后对 agent 说「记一下这个需求」「哪些还没收口」「这条为什么会有」，它会按这套流程走：
先 `resume` 拿回现场，发现新需求当场 `add`，收尾时 `close` 附证据。

---

## 常见问题

**需要数据库吗？**
不需要。数据就是一份 JSON 加一份追加式日志。

**会污染我的仓库吗？**
全是纯文本文件。不想要视图就在 `.gitignore` 里排除那几个 `.md`，或用 `--no-render` 关掉自动刷新。
真源建议提交 —— 那是 `git log` 作为审计日志的前提。

**能多人协作吗？**
设计是**单写者**。写入有跨进程锁保护（并发写会被拒绝并给出提示），但没有合并机制。
多人请各写各自项目的真源，或串行操作。

**树会越长越乱吗？**
深度和宽度是**如实记录**出来的，不是「乱」：层级反映需求之间真实的派生关系，所以工具**刻意不提供
改父（reparent）通道**，也不建议为了好看凭空插中间层或另立新根 —— 那样是伪造逻辑关系。
阅读成本另想办法消化：深度 > 3 层、同父未收口 > 7 个各给一条提醒，并告诉你**它在哪**
（`check` / `resume` / 各视图页眉都有一行**结构概览**：最深 · 最宽层 · 最大的一棵树 + 位置编号；
`TREE.md` 与看板顶部另有完整区块）。外加已全部收口的子树自动折叠成一行、每周 `report` 巡检停滞项。

**能导出给外部看吗？**
`todoctl export` **恒脱敏**（清空 `why`/`notes`，把标了 `internal` 的节点连同整条子树剔除）。
唯一无法自动脱敏的是**标题**，所以导出时会提醒你人工复核。
（`export` 这个命令的全部意义就是「产出可公开副本」，因此它没有「不脱敏」模式。）

**改错了怎么退回去？**
每次写入前自动快照。`todoctl restore --list` 看有什么，`todoctl restore --last --yes` 退回上一次写入之前。
有 git 的话 git 也是退路。

**能当看板用吗？**
可以。`dashboard.html` 是单文件、零依赖，双击就在浏览器里打开，可按状态/关键字/告警筛选。

**数据坏了会怎样？**
`check` 会给出带错误码的清单（ID 重复、成环、假收口、缺证据……），
`render` 在真源有错时**不会静默产出空树** —— 它返回 `1` 并打印错误清单。

---

## 设计取舍与已知限制

诚实列几条：

- **单写者设计。** 有写锁防并发覆盖，但不做多写者合并。这是刻意的 —— 一旦引入合并，
  「唯一真源」就会变成一场谈判，而那正是这套工具要消灭的东西。
- **真源是单个文件。** 好处是一次读取拿到全貌、diff 清晰；代价是同一文件里多条需求线的改动
  在 `git diff` 层面无法自动切分。（`journal.jsonl` + `log --line` 解决的是**叙述**的交叉，
  不是 **diff** 的交叉。）
- **不做关键路径与甘特图。** `depends_on` 记录了跨分支依赖，但不做关键路径计算。
  这套工具的目标是「记得住、收得口、看得清」，不是排期。
- **真源落点已中性化。** 非 git 环境下降级到 `.req-tree/requirements`，不绑定任何宿主的私有目录；
  旧版的 `.workbuddy/requirements` **仍会被识别**（迁移兼容），`check` 会提示你把它搬走。
- **规模实测**：800 节点时单次写入约 0.3 秒，其中约 0.24 秒是 Python 进程启动本身 ——
  几百节点的日常规模完全无感。

---

## 自检

```bash
todoctl check        # 强校验：ID 重复 / 成环 / 假收口 / 缺证据 / 依赖失效 …
```

退出码 `0` 表示真源干净；`1` 表示发现问题（会逐条列出错误码）。
`render` 在真源有错时**不会静默产出空图** —— 它返回 `1` 并打印错误清单。

---

## 项目结构

```
req-tree/
├── SKILL.md              # 给 AI agent 的入口：工作流与纪律
├── README.md             # 你正在读的这份
├── references/
│   ├── entry-map.md      # 功能与入口索引：「我要做 X → 打哪条命令」
│   ├── schema.md         # 数据模型与状态机
│   └── discipline.md     # 触发时机与写入纪律
└── scripts/
    ├── todoctl.py        # 工具本体（零依赖，仅标准库）
    ├── todoctl           # macOS / Linux / WSL / Git Bash 启动
    └── todoctl.cmd       # Windows 启动
```

---

## 开发与发布（dev / prod 分离）

改这个 skill 时，**别让改动直接落在宿主正在索引的那份副本上**。那等于「项目正在调用的版本随你每一次编辑而变」，而多个会话并行时还会在同一个 working tree 上互相干扰（实测过一次：他方未提交的改动在工作树里横跨 14 小时没人发现）。

两份目录，角色分明：

| 角色 | 位置 | 干什么 |
|---|---|---|
| **dev** | 宿主 skills 根**之外**的任意位置（本机实例：`F:\Workbuddy\_skills-dev\req-tree`） | 改代码、跑 `selftest.py`、`git commit` —— **唯一修改入口** |
| **prod** | 宿主 skills 根之内，如 `<skills 根>/req-tree` | 只读。只接受 dev 的单向推送 |

> 位置只有两条硬要求：① 在**宿主索引的那个根之外**（放进去会被扫成两个 skill）；② **能真实落盘**（有些环境的沙箱会把工作区外写入落进隔离层：报成功但没生效）。换位置前先用探针实测，见宿主记忆 `sandbox-fs-delete.md` §1.3。

发布用**仓库根之外**的 `publish-req-tree.py`（本机脚本，不随本体发布）。四道门，任一不过即中止且**不落任何改动**：

```
[1/4] dev 工作树干净     未提交的东西不允许发布
[2/4] dev selftest 全绿   回归没跑过就不许出门
[3/4] prod 工作树干净     有人绕过脚本直接改过 prod 就报警
[4/4] 可快进              prod 不得存在 dev 没有的提交（防分叉）
```

```bash
python publish-req-tree.py --status     # 看两侧版本与工作树状态
python publish-req-tree.py --dry-run    # 预演：跑全部门禁，但不落地
python publish-req-tree.py              # 发布（快进合并 + 回读校验）
python publish-req-tree.py --push       # 发布后推 GitHub（对外备份，需显式指定）
```

发布走 `git merge --ff-only`，**不做强推、不产生分叉**；落地后回读两侧 HEAD 是否一致。

> **为什么不是 vendor 进项目**（把 `todoctl.py` 复制到项目内）：那样工具就有了两份，每次修改都要手工同步 —— 正是这套分离机制要避免的问题。skill 应被视为**从 git 安装的外部依赖**，项目侧只留一个薄入口。

---

## 许可

MIT，见 [`LICENSE`](LICENSE)。
