# 功能与入口索引

> 本页回答三个问题：**这工具能做什么 / 我要做的事打哪条命令 / 结果去哪儿看。**
> 真源与派生物的**权威定义**在 `SKILL.md` 第二节；本页只讲「入口」。
> 命令面由 `scripts/selftest.py` 守护：新增或改名子命令而不更新本页，自检会失败。

---

## 一、按意图找入口（最常用的一张表）

| 我想… | 命令 | 结果去哪儿看 |
|---|---|---|
| 给项目立真源 | `init --project <名>` | 该目录下新生成的 `tree.json` |
| 记一条新需求 / 派生需求 | `add --parent <ID> --title … --why … --done-when …` | `TREE.md`、`ACTIVE.md` |
| 说「这条线不能公开」 | `add --internal` / `update <ID> --internal` | `show <ID>` 会显示；`export` 输出里列出被剔的子树根 |
| 开始做某条 | `start <ID>` | `ACTIVE.md`、`BENCH.md` |
| 随手记一句进度 | `note <ID> 做到哪 发现了什么` | `RESUME.md` 的「进度轨迹」 |
| 声明「下一步要做什么」（文字） | `update <ID> --next-step "…"` | `BENCH.md`、`RESUME.md` |
| 声明「下一步是哪个节点」（指针） | `update <ID> --next <ID>` | `BENCH.md`（带 `↷绕行中` / `⌫计划已失效` 标记） |
| 卡住了 | `block <ID> --reason "…"` | `BENCH.md`、`resume` 的「别忘了：未收口的阻塞项」 |
| 实现完成（待验证） | `done <ID> [--note "…"]` | `ACTIVE.md` |
| 验证通过、正式收口 | `close <ID> --evidence "commit:…"` | `TREE.md`（`✅`）、`BASELINE.md` |
| 放弃某条 | `drop <ID> --reason "…"` | `TREE.md`（`⏹`） |
| **开工前拿回现场** | **`resume [<ID>]`** | `RESUME.md` |
| **看手头同时有几件事** | **`bench`** | `BENCH.md` |
| 查某一条的全部细节 | `show <ID>` | 终端 |
| 追「这条为什么存在」 | `show <ID>` 看 `why` / 看 `TREE.md` 的派生层级 | 终端 / `TREE.md` |
| 体检（真源有没有坏） | `check` | 终端 |
| 出全部视图 | `render` | 7 个派生物文件（见第四节） |
| 把各线成果封成一个时点 | `baseline --name "v0.3 首版"` | `BASELINE.md`、`baselines.json` |
| 退回上一次写入前 | `restore --last --yes` | `tree.json` |
| 本次提交到底写了什么 | `log --since "<上次提交时间>"` / `--line <线ID>` | 终端 / `--commit-msg` |
| 这一周的进出 | `report --days 7` | 终端 |
| 导出可公开副本 | `export` | `public/` 目录 |

---

## 二、查看入口总表（「我想知道 X」→ 看哪里）

| 想知道 | 入口 | 性质 |
|---|---|---|
| 现在有哪些事没做完 | `ACTIVE.md` | 派生 |
| **我上次在做什么、下一步** | `RESUME.md`（或直接打 `resume`） | 派生 / 命令 |
| **手头同时有几件事、各自搁置几天** | `BENCH.md`（或直接打 `bench`） | 派生 / 命令 |
| 这条需求为什么存在 | `TREE.md` 的层级 + `show <ID>` 的 `why` | 派生 / 命令 |
| 盯着一条分支看它的派生 | `FOCUS.md`（先 `focus <ID>`） | 派生 |
| 各需求线进度、封板时点 | `BASELINE.md` | 派生 |
| 按状态 / 关键字筛选 | `dashboard.html` | 派生 |
| 某条线从头到尾的变更叙述 | `log --line <线ID>` | 命令（读 `journal.jsonl`） |
| **某个字段什么时候被谁改成这样** | `log` 显示的字段级 `delta`（如 `open → doing`） | 命令（读 `journal.jsonl`） |
| 真源长什么样 | `tree.json` | **真源（禁手改）** |

---

## 三、真源 vs 派生物：一句话判别

| | 有哪些 | 规矩 |
|---|---|---|
| **真源（5）** | `tree.json`、`journal.jsonl`、`baselines.json`、`snapshots/`、`tree.lock` | **所有写入只走 CLI**，禁止手改 |
| **派生物（7）** | `TREE.md`、`ACTIVE.md`、`FOCUS.md`、`RESUME.md`、`BENCH.md`、`BASELINE.md`、`dashboard.html` | `render` 覆盖生成，**禁止手改** |

**判别口诀**：手改会被覆盖的 → 派生物；手改会破坏追溯链的 → 真源。

> 权威定义（每个文件的字段与语义）见 `SKILL.md` 第二节，本页不重复定义以免两处漂移。

---

## 四、全部命令与选项

三个入口完全等价（都只从 `PATH` 找 Python，不含任何写死的安装路径）：

```
# macOS / Linux / WSL / Git Bash
alias todoctl="python3 /path/to/req-tree/scripts/todoctl.py"

# Windows 命令行：把 req-tree/scripts 加进 PATH 后直接用 todoctl.cmd
todoctl --root C:/path/to/repo init --project my-project
todoctl add --title "..." --why "..." --done-when "..."
todoctl add --parent R-0006 --title "..." --why "..." --done-when "..."
todoctl add --title "内部线..." --done-when "..." --internal   ← 这条线不公开（export 时连同子树剔除）
todoctl start R-0007 | done R-0007 | close R-0007 --evidence "commit:abc123"
todoctl block R-0007 --reason "..." | unblock R-0007
todoctl drop R-0007 --reason "..."
todoctl update R-0007 --done-when "..." | focus R-0007 | show R-0007
todoctl update R-0007 --next-step "下一步要做什么"          ← 文字说明
todoctl update R-0007 --next R-0008                       ← 计划中的下一步是哪个节点
todoctl note R-0007 这次做到哪 发现了什么                   ← 直接连写，不必加引号
todoctl note R-0007 一句话 --next-step "下一步动作" --next R-0008
todoctl resume                                            ← 开工第一件事：恢复上次现场
todoctl bench                                             ← 手头有哪几件事在同时推
todoctl baseline --name "v0.3 首版"                        ← 把当前各线成果封成一个基线
todoctl baseline --list | --diff [B-xxx]                  ← 列基线 / 与上一基线比对
todoctl restore --list                                    ← 看可用快照
todoctl check | render | report --days 7
```

### 全部子命令（21 个）

| 组 | 命令 | 主要选项 |
|---|---|---|
| 建 | `init` | `--project` |
| 写 | `add` | `--parent` `--title` `--why` `--done-when` `--kind` `--depends-on` `--internal` |
| 写 | `update` | `--title` `--why` `--done-when` `--next-step` `--next` `--kind` `--depends-on` `--internal` `--external` |
| 写 | `note` | `--next-step` `--next` |
| 状态 | `start` | `--force` |
| 状态 | `block` | `--reason` |
| 状态 | `unblock` | `--force` |
| 状态 | `done` | `--note` |
| 状态 | `close` | `--evidence`（可重复 / 必填）`--override-reason` `--force` |
| 状态 | `drop` | `--reason` `--force` |
| 恢复 | `resume` / `bench` | — |
| 查看 | `show` / `check` | — |
| 查看 | `log` | `--line` `--since` `--limit` `--commit-msg` |
| 治理 | `baseline` | `--name` `--note` `--list` `--diff [ID]` |
| 治理 | `restore` | `--list` `--last` `--file` `--date` `--yes`（必填） |
| 导出 | `render` | `--out` |
| 导出 | `export` | `--out` |
| 巡检 | `report` | `--days` |

### 治理类（封板 / 回滚 / 并发）详解

| 命令 | 作用 |
|---|---|
| `baseline [--name X] [--note Y]` | 封板：把当前各线成果封成**不可变**基线（真源有错时拒绝；`baselines.json` 损坏时**拒绝**而非覆盖） |
| `baseline --list` / `--diff [ID]` | 列出基线 / 与指定（默认上一个）基线比对。`--diff` 命中**最后一条**匹配项 |
| `restore --list` | 列出可用快照 |
| `restore --last \| --file X \| --date YYYY-MM-DD` | 回滚真源，**必须加 `--yes`**；执行前会把当前状态另存 |
| `--wait SEC`（全局） | 写锁等待秒数，默认 0（立即失败） |

### 恢复类（跨次交接）详解

| 命令 | 作用 |
|---|---|
| `resume [<id>]` | 输出恢复简报（你在做什么 / 进度轨迹 / 期间变动 / 手头其他进行中），并把「现在」记为恢复锚点 |
| `bench` | **台面**：手头全部 `doing` 按需求线分组 + 各自下一步 + 最近进度 + 搁置天数 + 绕行标记 |
| `note <id> <文本...>` | 追加一条带时间戳的进度记录 |
| `update <id> --next-step "..."` | 「下一步要做什么」的文字说明 |
| `update <id> --next <ID>` | **「计划中的下一步」是哪个节点**（传空串清除）—— 绕行检测的前提 |
| `log [--line <ID>] [--since <时间>] [--limit N]` | 按需求线抽取变更叙述（提交信息素材 / 分线核对）。`--limit N` = 最后 N 条，**N ≤ 0 视为 0 条** |
| `log --commit-msg` | 输出提交信息草稿（按线分组） |

### 全局约定

- `--root` 写在**子命令前后都可以**（如 `todoctl add --title x --root C:\path\to\repo`），不必记顺序
- 中文参数在 bash / cmd 里都正常；三个启动入口等价
- 状态流转全集：`open → doing → done → closed`；`doing ⇄ blocked`；任意非终态 `→ dropped`
- `start` / `unblock` 需要 `done_when`，且**祖先不得已收口/已放弃**；`block` / `drop` 需要 `--reason`；`close` 需要 `--evidence`
- ⚠️ **`--force` 与 `--override-reason` 是两个独立的确认，不要混用**：
  - `--force` 只跳过「未 `done` 不能收口」的前置状态检查，**跳不过假收口检查**
  - `--override-reason` 才放行「仍有未收口后代」
  - 两个不规则同时踩到时（状态不是 `done` + 还有开着的子项），**两个都要给**
- **退出码**：`0` 成功 / `1` `check` 或 `render` 发现真源有错 / `2` 被强校验或参数校验拒绝。
  `render` 在真源有错时**不会静默产出空树** —— 它返回 `1` 并打印错误清单，别拿错误状态下的视图做判断。

---

## 五、五个工作流

### 1. 首次为项目建立真源
```
todoctl --root <项目根> init --project <项目名>
todoctl add --title "<根需求 1>" --why "初始需求" --done-when "<怎样算完成>"
...
todoctl render && todoctl check
```
> 全新真源上**不会**自动生成视图（判据是「该目录已存在 `TREE.md`」）——
> 跑一次 `render` 之后，视图就会随每次写入自动跟随。

### 2. 派生一条新需求（最高频，必须即时）
发现新问题/新边界/新约束的**当下**就落库，不要攒着「回头一起整理」——一旦攒，追溯链就断了。
```
todoctl add --parent R-0006 \
  --title "按供应商维度筛选" \
  --why "推进 R-0006 时发现现有查询接口无供应商维度，需新增聚合入口" \
  --done-when "筛选结果与手工核对一致，接口测试通过"
```
- `--kind` 可选 `requirement / task / bug / decision / blocker`（默认 requirement）
- 跨分支依赖用 `--depends-on R-0009`（可重复）
- 派生出来的**决策**也要落库：`--kind decision`，`why` 写清楚「在什么约束下做了这个取舍」

### 3. 推进与收口（与开发节奏绑定）
```
todoctl resume                        # 开工第一件事：拿回上次现场
todoctl start R-0006                  # 需要 done_when；且祖先不得是已收口/已放弃（否则被拒）
todoctl note R-0006 做到哪 发现了什么    # 过程中随手留痕，可直接连写不加引号
todoctl update R-0006 --next-step "明天先跑一遍 XX 用例"
todoctl done  R-0006 --note "接口已实现"  # done 的 --note 也记入进度轨迹
todoctl close R-0006 --evidence "commit:9f2c1ab" --evidence "test:test_filter_by_supplier"
```
`close` 之后必做一件事：**回写祖先**。检查本次收口是否让某个祖先的验收条件失效或已达成 —— 失效用 `update` 修正，达成则继续向上收口。这是防止「子树全做完了、父需求其实没达成」的唯一手段。

### 4. 出视图与巡检
```
todoctl render        # 覆盖生成 7 个派生物（TREE/ACTIVE/FOCUS/RESUME/BENCH/BASELINE/dashboard）
todoctl check         # 强校验，有错误返回码 1
todoctl report --days 7
```
- 日常看 `ACTIVE.md`；**每次开工先 `resume`**；评审/考古看 `TREE.md`；盯着一件事时看 `FOCUS.md`；需要筛选时开 `dashboard.html`

### 5. 定期 rebalance（每周一次）
1. `todoctl check` → 先清错误
2. 看告警：`W1 深度 > 3`、`W2 同父未收口 > 7` → 决定升级为独立子项目、拆分或归并
3. `todoctl report --days 7` → 核对阻塞与停滞（`W4` 超过 14 天未更新）
4. `todoctl render` → 已收口子树自动折叠，主视图恢复清爽
