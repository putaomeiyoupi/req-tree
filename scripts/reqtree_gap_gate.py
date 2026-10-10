#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
reqtree_gap_gate.py — 需求树「漏记 / 漏收口」机械门禁（**通用 · 跨项目 · 零依赖**）

为什么存在
----------
实测到三类同类失效，全部是「人（agent）没主动调用 req-tree」：

  ① 一整天的增量**做完了才补录** —— `--why` 的价值在于「当刻的判断」，事后补只能靠提交反推；
  ② 某需求**实现早已落地**，节点却停在 `open`，直到数周后核查才发现；
  ③ 某需求做完十几天**漏了最后一步 close**。

⇒ 结论：**靠「记得调用」不可靠**（那正是失效的地方）。本脚本把「该问的问题」变成机械比对，
   在**提交那一刻**摆到眼前。

两条判据（互不重叠，覆盖两类失效）
----------------------------------
`gate`（提交门禁）：本次提交含代码改动、但**需求树真源未同步** ⇒ 打印自查清单
    （新需求？顺带挖出的缺陷？推进已有节点？）。
    ⚠️ **只覆盖第 ① 类**（漏记）—— 它只看本次提交，**拦不住 ②③**（历史漏收口）。

`audit`（全树巡检）：状态非 closed、且 `done_when` 的物证 token **在代码里已大量命中**
    ⇒ 列为「疑似做完了没收口」。
    ⚠️ **只报疑似、不自动改** —— 判据是否「已达成」要逐条实跑或读代码确认，
       词频命中只能当线索（实测教训：grep 关键词找覆盖会大量假命中）。

⚠️ **退出码恒 0（故意不阻断）**
   —— 价值是「把问题摆到眼前」，不是替人判断；阻断只会变成「来不及登记就提交不了」
      的新摩擦，反而更容易被绕开（那等于把门禁变回「靠自觉」，只是换个形式）。

四种触发方式（按可靠性递增，都不需要「记得调用」）
--------------------------------------------------
1. **仓库自带 hook（推荐）**：`todoctl gate install-hook` 写 `.git/hooks/pre-commit`，
   或用 `core.hooksPath`（见 README「安装门禁」）—— 提交是必经之路，绕不过去。
2. 提交时手动跑：`python reqtree_gap_gate.py gate`
3. 隔夜巡检：`python reqtree_gap_gate.py audit`（配成定时任务）
4. 会话开始时：仓库 `AGENTS.md` 写一句点名（见 README）

用法：
    python reqtree_gap_gate.py gate [--repo .] [--root <真源目录>]
    python reqtree_gap_gate.py audit [--repo .] [--min-days 7]
    python reqtree_gap_gate.py install-hook [--repo .]
    python reqtree_gap_gate.py print-hook        # 只打印 hook，不落盘（便于人工审阅）

真源定位沿用 req-tree 规则：`--root` / `$TODOCTL_ROOT` / `docs/requirements/` 等。
⚠️ 本脚本**不依赖 req-tree skill 本身**（不 import todoctl），单独拷贝也能跑 ——
   这样它可以随项目仓库分发，不受「skill 装在用户目录」的限制。
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import subprocess
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# ---- 真源定位（与 req-tree 一致：显式 > 环境变量 > 项目内常见命名空间）----
CANDIDATE_DIRS = (
    "docs/requirements",
    ".req-tree/requirements",
    ".workbuddy/requirements",
)


def find_repo_root(start: Path) -> Path:
    p = start.resolve()
    while True:
        if (p / ".git").exists():
            return p
        if p.parent == p:
            return start.resolve()
        p = p.parent


def find_src_root(repo: Path, explicit: str | None) -> Path | None:
    cands = []
    if explicit:
        cands.append(Path(explicit))
    if os.environ.get("TODOCTL_ROOT"):
        cands.append(Path(os.environ["TODOCTL_ROOT"]))
    cands += [repo / d for d in CANDIDATE_DIRS]
    for c in cands:
        if (c / "tree.json").exists():
            return c.resolve()
    return None


def run(cmd, cwd):
    try:
        r = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=60)
        return r.stdout or ""
    except Exception:
        return ""


# ---- gate 模式的路径口径 ----
CODE_EXT = {".py", ".js", ".ts", ".tsx", ".jsx", ".vue", ".css", ".scss", ".html",
            ".sql", ".go", ".java", ".rs", ".rb", ".php", ".c", ".cpp", ".h", ".sh"}
IGNORE_PREFIX = (
    "scripts/", "tests/", "test/", "spec/", "docs/", "doc/", "backups/", "logs/",
    "migrations/", "node_modules/", "dist/", "build/", "out/", "target/",
    ".workbuddy/", ".git/", ".venv/", "venv/", "__pycache__/",
)
EXEMPT_FILES = (
    "CHANGELOG.md", "LICENSE", "LICENSE.md", "LICENCE", "NOTICE.md", "README.md",
    "CONTRIBUTING.md", "CODE_OF_CONDUCT.md", ".gitignore", ".gitattributes",
    ".env.example", "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
    "poetry.lock", "uv.lock", "Cargo.lock", "go.sum",
)


def code_files_of(paths: list, src_rel: str) -> list:
    """挑出「算代码改动」的文件。

    ⚠️ 这里刻意**不区分业务代码与测试**：测试同样是「实现已落地」的物证。
    早期版本把 tests/ 整段忽略，结果「补了测试却没登记需求」这类漏记反而抓不到。
    """
    out = []
    for p in paths:
        pn = p.replace("\\", "/")
        if pn.startswith(src_rel + "/"):
            continue
        if os.path.basename(pn) in EXEMPT_FILES:
            continue
        if any(pn.startswith(x) for x in IGNORE_PREFIX):
            continue
        if os.path.splitext(pn)[1] in CODE_EXT:
            out.append(pn)
    return out


def _is_dir(p) -> bool:
    """`Path.is_dir` 的容错版（Windows 上 Path 与 str 混用时曾抛 AttributeError）。"""
    try:
        return Path(p).is_dir()
    except Exception:
        return False


def cmd_gate(args, repo: Path, src_root: Path | None) -> int:
    print("🔗 需求树同步门禁（reqtree_gap_gate gate）")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    if src_root is None:
        print("ℹ️  本项目未见需求树真源（docs/requirements/tree.json 等）⇒ 跳过")
        print("    （若项目不用 req-tree，这是正常的）")
        return 0

    src_rel = os.path.relpath(src_root, repo).replace("\\", "/")
    staged = [x.strip() for x in run(["git", "diff", "--cached", "--name-only"], repo).splitlines() if x.strip()]
    unstaged = [] if args.staged else [x.strip() for x in run(["git", "diff", "--name-only"], repo).splitlines() if x.strip()]
    # ⚠️ **未跟踪文件也必须纳入**（实测漏过）：`git diff` 只报已跟踪文件，
    #    而「新建一个模块」恰好是最典型的漏记场景 —— 那时文件还没进索引，
    #    门禁会报「没有检出变更」直接跳过，等于放过了最该问的那一次。
    untracked = [x.strip() for x in run(
        ["git", "ls-files", "--others", "--exclude-standard"], repo).splitlines() if x.strip()]
    seen, files = set(), []
    for p in staged + unstaged + untracked:
        if p and p not in seen:
            seen.add(p)
            files.append(p)
    n_untracked = len([p for p in untracked if p not in set(staged) | set(unstaged)])

    if not files:
        print("ℹ️  没有检出变更，跳过")
        return 0

    src_touched = any(p.startswith(src_rel + "/") for p in files)
    code = code_files_of(files, src_rel)
    print(f"   变更 {len(files)} 个文件（未跟踪 {n_untracked}）· 代码类 {len(code)} 个 · 真源({'已同步 ✓' if src_touched else '未同步 ✗'})")

    if src_touched:
        print("✅ 真源与代码同批提交 —— 通过")
        return 0
    if not code:
        print("✅ 无代码改动（纯文档/运维）—— 通过")
        return 0

    print()
    print("⚠️  有代码改动但真源未同步。逐条自查：")
    print()
    print("   ① 实现了**新需求 / 派生需求**？⇒ 立刻登记，别等做完：")
    print("        <repo>/scripts/reqtodo.sh add --parent <ID> --title \"...\" --why \"...\" --done-when \"...\"")
    print("      ⚠️ 长文本含反引号/$/中文双引号会被 shell 静默吃掉 ⇒ 用 `--file` 走文件或 stdin。")
    print()
    print("   ② 修的是**缺陷**而非需求？⇒ 顺带挖出的其它缺陷要**另立节点**，")
    print("      别塞进本条 reason/evidence（混在一起关掉本条就把新缺陷一起埋了）。")
    print()
    print("   ③ 推进了**已有节点**？⇒ start → done → close 三连；")
    print("      ⚠️ **done ≠ closed**（实测有做完十几天没 close 的）。")
    print()
    print("   ④ 都不是（纯重构/格式化/typo）⇒ 在 commit message 写「无需求变更」即可。")
    print()
    print("   ⚠️ 本门禁**只管本次提交**，拦不住「历史漏收口」⇒ 那由 `audit` 模式 / 定时巡检负责。")
    return 0


# ---- audit 模式：找「疑似做完了没收口」----
# 语料目录：按项目实际结构猜，取存在的那些。找不到就退化成「全仓库（排除依赖目录）」。
CORPUS_CANDIDATES = (
    "app", "src", "server", "api", "lib", "pkg", "internal", "cmd",
    "frontend", "web", "client", "miniprogram", "migrations",
)
CORPUS_SKIP_DIRS = ("__pycache__", "node_modules", ".venv", "venv", "dist", "build",
                    ".git", "out", "target", ".next", "coverage")

# ⚠️ 通用停用词：只放**任何项目都可能出现**的技术/组织类词。
#    ⚠️ 这里刻意**不硬编码任何具体项目的业务模块名** —— 那会让本脚本在别的项目里
#    把该项目的业务名当通用词过滤掉（上一版曾含某个项目的模块名，属脱敏事故）。
GENERIC_STOPWORDS = frozenset({
    "requirements", "requirement", "migration", "migrations", "permission",
    "permissions", "database", "frontend", "backend", "config", "settings",
    "utility", "helper", "helpers", "constant", "constants", "service",
    "services", "handler", "handlers", "request", "response", "session",
    "sessions", "message", "messages", "content", "contents", "detail",
    "details", "summary", "metadata", "resource", "resources", "support",
})


def _iter_code_files(repo: Path):
    """产出仓库里的代码文件路径（尽力而为：找不到候选目录就全仓库扫，跳过依赖目录）。"""
    roots = [repo / d for d in CORPUS_CANDIDATES]
    roots = [p for p in roots if _is_dir(p)]
    if not roots:
        roots = [repo]
    seen = set()
    for r in roots:
        for dp, dns, fns in os.walk(r):
            dns[:] = [d for d in dns if d not in CORPUS_SKIP_DIRS]
            for fn in fns:
                if not fn.endswith((".py", ".js", ".ts", ".tsx", ".jsx", ".vue",
                                    ".html", ".css", ".scss", ".sql", ".go", ".rb", ".java")):
                    continue
                fp = Path(dp) / fn
                try:
                    key = str(fp.resolve())
                except Exception:
                    continue
                if key in seen:
                    continue
                seen.add(key)
                yield fp


def cmd_audit(args, repo: Path, src_root: Path | None) -> int:
    print("🔎 需求树全树巡检（audit）：找「疑似做完了没收口」")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    if src_root is None:
        print("ℹ️  本项目未见需求树真源，跳过")
        return 0

    try:
        d = json.load(io.open(src_root / "tree.json", encoding="utf-8"))
    except Exception as e:
        print(f"ℹ️  真源解析失败（{e}），跳过")
        return 0
    ns = d["nodes"] if isinstance(d.get("nodes"), list) else d
    if not isinstance(ns, list):
        print("ℹ️  真源结构无法识别，跳过")
        return 0
    open_nodes = [n for n in ns if isinstance(n, dict)
                  and n.get("status") in ("open", "doing", "done", "blocked")]
    print(f"   未收口 {len(open_nodes)} / 总 {len(ns)}")

    # ⚠️ 语料只取运行时真源代码（教训：含 docs/ 的**历史快照**会把判据救活）
    corpus = {}
    for fp in _iter_code_files(repo):
        try:
            corpus[str(fp)] = io.open(fp, encoding="utf-8", errors="ignore").read()
        except Exception:
            pass
    blob = "\n".join(corpus.values())
    scope = "候选目录" if any(_is_dir(repo / d) for d in CORPUS_CANDIDATES) else "全仓库"
    print(f"   代码语料 {len(corpus)} 文件 / {len(blob)} 字符（{scope}）"
          + (f"（已限定 >= {args.min_days} 天未更新）" if args.min_days else ""))

    if not blob:
        print("ℹ️  未扫到代码文件，跳过词频判定")
        return 0

    suspect = []
    for n in sorted(open_nodes, key=lambda x: str(x.get("id", ""))):
        dw = n.get("done_when") or ""
        if not dw or not isinstance(dw, str):
            continue
        toks = {t for t in re.findall(r"\b([a-z][a-z0-9]*(?:_[a-z0-9]+){1,4})\b", dw)
                if len(t) >= 6 and t not in GENERIC_STOPWORDS}
        if len(toks) < args.min_tokens:
            continue
        hit = sum(1 for t in toks if t in blob)
        if not hit:
            continue
        ratio = hit / len(toks)
        # ⚠️ **判定用「命中数」而非「命中率」**（实测踩过：先按 `toks>=3 且 ratio>=0.4`
        #   写，结果那种「done_when 只有 2 个 token 但命中 2/2」的最强信号反被
        #   门槛挡掉 ⇒ **假绿**，与「断言存在 ≠ 断言被执行」同族）。
        #   少样本全命中比多样本半命中**更可疑**（名字具体 ⇒ 只可能来自真实现）。
        #   故：命中 ≥4 或（命中 ≥2 且命中率 ≥0.5）即报。
        if hit >= 4 or (hit >= 2 and ratio >= 0.5):
            suspect.append((n.get("id"), n.get("status"), (n.get("title") or "")[:46],
                           hit, len(toks), ratio))

    if not suspect:
        print("✅ 未发现疑似漏收口节点")
        print("   ⚠️ 这**不等于**全都没问题：词频判据只覆盖「物证 token 已大量出现」这一种形态。")
        return 0
    print(f"⚠️  {len(suspect)} 条**疑似**（线索，非判定）：")
    for nid, st, title, hit, tot, ratio in suspect:
        print(f"   {nid} [{st}] {title}  —— done_when 物证命中 {hit}/{tot}（{ratio:.0%}）")
    print()
    print("   ⚠️ 词频命中**只能当线索**（实测 grep 关键词找测试覆盖会大量假命中）。")
    print("   逐条实跑或读代码确认后走：start → done → close（证据挂 commit:<sha> + test:<用例名>）。")
    return 0


# ---- git hook ----
# ⚠️ **绝不把本机绝对路径写进 hook**：写死后别人 clone 下来只会静默失败
#    （实测：路径不存在时脚本被 if 判断跳过，**不报错**，门禁形同虚设）。
#    ⇒ hook 用「相对仓库根」的定位：先试仓库内脚本，再退到 skill 目录。
HOOK = """#!/bin/sh
# 需求树同步门禁（由 reqtree_gap_gate.py install-hook 生成）
# ⚠️ 只打印提示，**不阻断提交**；任何失败都静默跳过，绝不卡住别人的提交。
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"

GATE=""
# ① 优先用**仓库内**的脚本（跟着仓库走 ⇒ 别人 clone 下来即可用）
for cand in "$REPO_ROOT/scripts/reqtree_gap_gate.py" \\
            "$REPO_ROOT/.req-tree/reqtree_gap_gate.py" \\
            "$HOME/.workbuddy/skills/req-tree/scripts/reqtree_gap_gate.py"; do
  if [ -f "$cand" ]; then GATE="$cand"; break; fi
done

if [ -n "$GATE" ]; then
  PY=""
  for c in python3 python; do
    if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
  done
  if [ -n "$PY" ]; then
    "$PY" "$GATE" gate --repo "$REPO_ROOT" || true
  fi
fi
exit 0
"""


def cmd_install_hook(args, repo: Path) -> int:
    hooks = repo / ".git" / "hooks"
    if not _is_dir(hooks):
        print(f"ℹ️  非 git 仓库或无 .git/hooks（{repo}），跳过")
        print("    （若用 worktree / 裸仓库，可用 `git config core.hooksPath <dir>` 替代）")
        return 0
    hp = hooks / "pre-commit"
    existed = hp.exists()
    body = HOOK
    if existed and "reqtree_gap_gate" not in hp.read_text(encoding="utf-8", errors="ignore"):
        # ⚠️ 已有其它 pre-commit ⇒ **不覆盖**，改为追加（尊重既有钩子）
        cur = hp.read_text(encoding="utf-8", errors="ignore")
        hp.write_text(cur.rstrip() + "\n\n# --- 需求树同步门禁（reqtree_gap_gate.py 追加）---\n"
                      + body.split("\n", 2)[2], encoding="utf-8")
        print(f"✓ 已**追加**到既有 {hp}（未覆盖原内容）")
    else:
        hp.write_text(body, encoding="utf-8")
        try:
            os.chmod(hp, 0o755)
        except Exception:
            pass
        print(f"✓ 已写入 {hp}")
    print("   （门禁**只打印提示、不阻断提交**；未装也不影响任何功能）")
    print()
    print("💡 想让 hook **跟着仓库走**（别人 clone 下来就自动生效）：")
    print("     1) 把本脚本放进仓库，如 scripts/reqtree_gap_gate.py")
    print("     2) mkdir -p .githooks && 把 hook 内容存为 .githooks/pre-commit")
    print("     3) git config core.hooksPath .githooks   ← 提交这一行，clone 的人执行一次即可")
    return 0


def cmd_print_hook(args) -> int:
    print(HOOK)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="需求树漏记/漏收口机械门禁（通用 · 零依赖）")
    ap.add_argument("mode", choices=["gate", "audit", "install-hook", "print-hook"])
    ap.add_argument("--repo", default=None, help="仓库根（默认自动向上找 .git）")
    ap.add_argument("--root", default=None, help="需求树真源目录（默认自动找）")
    ap.add_argument("--staged", action="store_true", help="gate：只看已暂存")
    ap.add_argument("--min-days", type=int, default=0, help="audit：只看 N 天以上未更新的节点")
    ap.add_argument("--min-tokens", type=int, default=2,
                    help="audit：done_when 至少几个物证 token 才纳入判定（默认 2，"
                         "⚠️ 别调高：少样本全命中是最强信号，见代码内注释）")
    args = ap.parse_args()

    try:
        if args.mode == "print-hook":
            return cmd_print_hook(args)
        start = Path(args.repo) if args.repo else Path.cwd()
        repo = start.resolve() if (start / ".git").exists() else find_repo_root(start)
        src_root = find_src_root(repo, args.root)
        if args.mode == "gate":
            return cmd_gate(args, repo, src_root)
        if args.mode == "audit":
            return cmd_audit(args, repo, src_root)
        return cmd_install_hook(args, repo)
    except Exception as e:
        print(f"ℹ️  门禁自身异常（{e}），跳过（不阻断提交）")
        return 0


if __name__ == "__main__":
    sys.exit(main())
