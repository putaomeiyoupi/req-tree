# dsh-req-tree

把 [req-tree](https://github.com/putaomeiyoupi/req-tree) 的需求派生树搬进 DeepSeek Harness：
**右侧栏多一个只读 Tab**，不用切窗口就能看到当前工作区的需求树、各条需求线的收口进度，以及
`todoctl check` 会报的那些告警。

---

## 它做什么 / 不做什么

| 做 | 不做 |
|---|---|
| 读取工作区的 `tree.json`，渲染成树 | **不写**任何东西 —— 没有 start / done / close 按钮 |
| 显示位置编号（`1.3.2`）与不可变 ID（`R-0007`） | 不改 `tree.json`，不碰 `journal.jsonl` |
| 按需求线算收口进度（`closed / (total - dropped)`） | 不代替 `todoctl check` 的强校验（面板只显示 W 类告警） |
| 显示 W1~W9 告警，点一下定位到对应节点 | 不重新发明 req-tree 的规则 —— 阈值全部从 `todoctl.py` 移植 |
| 轮询真源，变了就刷新（默认 3 秒） | 不缓存、不落盘、不联网 |

### 为什么是只读

req-tree 的第一条铁律是**所有写入只走 CLI**：手改真源会制造第二个真源，而那是这套系统最大的威胁。

面板如果能做 `close`，就必须在浏览器里重写一遍 `close` 的证据门和 `--override-reason` 确认流程 ——
而浏览器里的那一份一定比命令行弱。所以这里只**看**，`todoctl` 负责**判断**。

---

## 安装

```bash
# 从本仓库目录
dsh plugin --profile <你的 profile> add D:/Documents/deepseek-harness/dsh-req-tree/plugin

# 或者先发布再装
npm publish    # 然后
dsh plugin --profile <你的 profile> add dsh-req-tree
```

装完在右侧栏点开 **需求树** Tab（或从引导页的入口进）。右侧栏是按会话停靠的，所以这个 Tab
会跟着当前会话走。

### 前提

面板需要一个已经存在的真源。先用 CLI 建一个：

```bash
cd /path/to/your-project
python /path/to/req-tree/scripts/todoctl.py init --project my-project
python /path/to/req-tree/scripts/todoctl.py add --title "第一条需求" --why "为什么会有它" --done-when "怎样才算收口"
python /path/to/req-tree/scripts/todoctl.py render
```

真源位置由面板**按 `todoctl` 完全相同的优先级**自动判定：

```
--root（插件配置 root）> $TODOCTL_ROOT > 当前目录已是真源（含 tree.json）
  > 已预建 docs/requirements 或 .req-tree/requirements
  > <git 仓库根>/docs/requirements
  > <当前目录>/.req-tree/requirements
```

面板底部会显示它实际读的是哪个目录、以及判定依据 —— 落点和 CLI 不一致时能立刻看出来。

---

## 开销：面板不看着的时候，它什么也不做

右侧栏会为**每个会话**保留 Tab，后台会话的 Tab 也在。所以一个天真实现会永远轮询、永远重渲染
一棵没人看的树。这里有两道闸：

| 闸 | 行为 | 效果 |
|---|---|---|
| **可见性** | `tab.visible` 为 false 时（侧栏收起 / 选中了别的 Tab / 后台会话），**不发请求、不起定时器** | 完全归零 |
| **版本号** | 宿主半返回 `revision`（`tree.json` + `journal.jsonl` 的 mtime+size 指纹）。与上次相同就**丢弃响应**，不做 `setState` | 一次 fetch + 零渲染 |

于是：

- 你没在看这个 Tab → **一次请求都没有**
- 你在看，但树没变 → 一个请求，**不重渲染**
- 你在看，树变了 → 解析 + 投影 + 渲染（这才是真正该花的那次）

宿主半还有一层：版本号没变时直接命中内存缓存，连 `tree.json` 都不重新读。
所以「没变化」那一轮的完整成本 ≈ 两个 `stat`。

> 代价是多了一个 `revision` 字段。**两端必须同时认这个字段** —— 宿主不发或客户端不读，
> 闸门就会静默退化成「永远重渲染」，不报错、只是回到原来的开销。
> `test/cost.test.mjs` 里有这条契约的断言。

## 配置

在 profile 的 `cordis.patch.yml` 里覆盖：

```yaml
- id: req-tree
  name: 'dsh-req-tree'
  config:
    pollSeconds: 3      # 面板轮询间隔（秒）
    maxNodes: 5000      # 单个工作区最多返回多少节点（超出会在面板里明说，不会静默截断）
    root: ''            # 留空 = 自动判定；填绝对路径 = 钉死真源
```

---

## 面板里能看到什么

- **总览**：未收口 / 已收口 / 已放弃 / 共 N 条，整体收口百分比
- **需求线**：每条顶层需求一根进度条（`closed / (total - dropped)`）
  —— `dropped` 既不进分子也不进分母，因为「判定不做」不是进度
- **树**：位置编号 + 状态字形（`○ ▶ ⛔ ✔ ✅ ⏹`，和 CLI 同一套符号），点开看
  `why` / `done_when` / `next_step` / `blocked_by` / 收口证据
- **告警**：W1~W9，点一下展开并滚动到对应节点
- **最近变动**：读 `journal.jsonl` 的最近 12 条（坏行跳过，和 `log` 一样）
- **真源位置**：实际读的是哪个目录、怎么判定到的

默认视图是 req-tree 看板的**「待办视野」**：已收口 / 已放弃的叶子节点折叠掉。
但**一个已收口/已放弃的父节点，只要底下还有未收口的子项，就仍然显示** ——
否则未完成的工作会被它自己那个「已完成」的祖先藏起来，而那看起来就像「这件事做完了」。

---

## 目录结构

```
plugin/
├── cordis.patch.yml      # bundle patch：把插件挂进 profile
├── package.json
├── lib/
│   ├── index.js          # 宿主半：读真源、注册 /plugins/dsh-req-tree/* 路由
│   ├── project.js        # 纯投影：tree.json → 面板要画的模型（无依赖，可单测）
│   ├── client.js         # 浏览器半：Tab 正文（手写 JSX，可读）
│   └── client.bundle.js  # 生成物：`window.__ModuleLoader__` 格式（勿手改）
├── scripts/
│   └── build-client.mjs  # 把 client.js 打包成 loader 认的格式
└── test/
    ├── fixtures/tree.json
    ├── project.test.mjs  # 投影契约测试
    └── panel.test.mjs    # 真 DOM 渲染测试
```

`lib/project.js` 与 `lib/index.js` 分开，是因为宿主半依赖 `@deepseek-ai/schemastery`
（只有宿主解析得到），而投影是纯函数，拆开后就能脱离宿主单测。

---

## 开发

```bash
npm install        # 只装 React / jsdom 用于测试
npm run verify     # = build + test
```

`npm run build` 会把 `lib/client.js` 包成 `lib/client.bundle.js`。
浏览器侧的模块加载器只认**裸模块名**（`react`、`@deepseek-ai/...`），
相对路径 `./client.js` 在浏览器里根本不存在 —— 所以产物必须自包含，
这也是这个脚本存在的唯一原因（`lib/client.js` 本身保持可读 ESM）。

改完 `lib/client.js` 别忘了重新 `npm run build`。脚本会用 `vm.Script`
真的解析一遍产物，重写有 bug 就在这里炸，而不是变成一个空白 Tab。

### 测试覆盖什么

- **投影**：位置编号按渲染顺序算、ID 才是身份；每线进度用 `closed/(total-dropped)`；
  W6（绕行）与 W8（计划失效）不混淆；成环 / 断链 / 未知状态都不崩；
  截断会明说且总数仍是全树计数
- **面板**：真 DOM 挂载。断言「读不到 ≠ 空树」—— 200 带 `ok:false` 必须渲染成错误，
  绝不能退化成「0 节点，一切正常」；两个语言字典必须键完全对齐

---

## 许可

MIT。上游 req-tree 亦为 MIT。
