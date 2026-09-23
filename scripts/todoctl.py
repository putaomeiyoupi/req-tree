#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""todoctl — 需求派生树与收口状态的唯一写入口（零第三方依赖，仅标准库）。

真源    <root>/tree.json         扁平节点表，派生关系由 parent 指针表达
        <root>/journal.jsonl     追加式变更日志（按需求线归属 + 字段级 before/after）
        <root>/baselines.json    基线列表（不可变里程碑，只增不改）
        <root>/snapshots/        写入前自动快照（无 git 时的回滚依据）
        <root>/tree.lock         写锁（常驻不删；进程退出即自动释放）
派生物  <root>/TREE.md           全树视图（已全部收口的子树折叠为一行）
        <root>/ACTIVE.md         活跃视图（仅未收口节点 + 其祖先上下文）
        <root>/FOCUS.md          聚焦视图（当前推进分支及其派生）
        <root>/RESUME.md         恢复简报（跨次交接：上次做到哪、下一步、期间变动）
        <root>/BENCH.md          台面（手头全部进行中任务 + 计划下一步 + 搁置天数）
        <root>/BASELINE.md       最新基线摘要与各线进度
        <root>/dashboard.html    单文件看板
派生物一律由 render 覆盖生成，禁止手改。所有写入只走本 CLI。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from contextlib import nullcontext
from datetime import datetime, timedelta
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass


# ------------------------------------------------------------------ 常量

KINDS = ("requirement", "task", "bug", "decision", "blocker")
KIND_CN = {
    "requirement": "需求",
    "task": "任务",
    "bug": "缺陷",
    "decision": "决策",
    "blocker": "阻塞",
}
STATUSES = ("open", "doing", "blocked", "done", "closed", "dropped")
ACTIVE_STATUSES = ("open", "doing", "blocked", "done")
TERMINAL_STATUSES = ("closed", "dropped")
GLYPH = {
    "open": "○",
    "doing": "▶",
    "blocked": "⛔",
    "done": "✔",
    "closed": "✅",
    "dropped": "⏹",
}
ID_RE = re.compile(r"^R-\d{4,}$")

MAX_DEPTH = 3            # 超过即告警：该分支是否应升级为独立子项目
MAX_OPEN_SIBLINGS = 7    # 同一父节点下未收口子节点上限
STALE_DAYS = 14          # open/blocked/done 超过该天数未更新即告警
STALE_DOING_DAYS = 7     # doing 超过该天数未更新即告警（搁置）
NOT_STARTED = ("open", "blocked")   # 「计划中的下一步」若处于这两态，视为尚未开工
SNAPSHOT_DIR = "snapshots"          # 自动快照目录（无 git 时的回滚依据）
SNAPSHOT_KEEP_DAYS = 30             # 按天快照保留天数
READONLY_CMDS = ("check", "report", "show", "log", "bench")
"""**真·不写盘**的命令 —— 不加写锁。

⚠ 判据是「是否写盘」，不是「是否改真源」。`render`（写 7 个视图）与 `export`（写 `public/`）
都不是只读命令：它们必须持锁，否则两个并发的 `render` 会写出**互相撕裂**的视图
（`TREE.md` 来自版本 A、`ACTIVE.md` 来自版本 B），而视图正是人做判断的依据。
原先这两个被误分类为只读 —— 分类错误，不是设计取舍。
"""


class Fail(Exception):
    """被强校验拒绝。"""


# ------------------------------------------------------------------ 时间

def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def now_sec() -> str:
    """秒级时间戳：用于「自上次恢复以来的变动」做精确比较。"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def parse_dt(s):
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def days_since(s):
    d = parse_dt(s)
    return None if d is None else (datetime.now() - d).days


# ------------------------------------------------------------------ 存储定位

def find_git_root(start: Path):
    cur = start.resolve()
    for p in [cur, *cur.parents]:
        if (p / ".git").exists():
            return p
    return None


ROOT_STRICT = ("docs/requirements", ".req-tree/requirements", "requirements")
ROOT_LEGACY = (".workbuddy/requirements",)
"""**旧版宿主专有路径**（WorkBuddy 时代的落点）。保留它只为「迁移兼容」：
该目录下若已有 `tree.json` 仍会被认作真源，升级到中性路径后不会丢数据。
但它**不参与「空目录即生效」**，也不是任何情况的默认落点 ——
否则非 WorkBuddy 环境会被塞进一个与它无关的目录。
"""
"""任一目录下已存在 tree.json 即认定为真源（顺序即优先级）。"""
ROOT_EMPTY = ("docs/requirements", ".req-tree/requirements")
"""仅这两个**自建命名空间**允许「预建空目录即生效」。
裸 `requirements/` 不在其中 —— 它是极易撞名的通用目录名（依赖清单常放这里），
若也允许空目录命中，会在 git 仓库的子目录里静默建出第二棵真源树。
"""


def resolve_root_info(explicit):
    """返回 (真源目录, 判定来源说明)。

    优先级：--root > $TODOCTL_ROOT > 已存在的真源目录 > 已预建的自建命名空间
            > 仓库内 docs/requirements > 工作区 .req-tree/requirements

    ⚠ 判定来源必须与落点同源返回，不能「重新猜一次」—— 否则打印的落点说明会与事实不符。
    """
    if explicit:
        return Path(explicit).expanduser().resolve(), "显式指定（--root）"
    env = os.environ.get("TODOCTL_ROOT")
    if env:
        return Path(env).expanduser().resolve(), "环境变量 TODOCTL_ROOT"
    cwd = Path.cwd()

    for rel in ROOT_STRICT:                  # ① 已是真源（含 tree.json）→ 直接命中
        cand = cwd / rel
        if (cand / "tree.json").exists():
            return cand.resolve(), "当前目录已是真源：%s（含 tree.json）" % rel
    for rel in ROOT_LEGACY:                  # ①′ 旧版路径：仅「已是真源」时兼容命中
        cand = cwd / rel
        if (cand / "tree.json").exists():
            return cand.resolve(), ("当前目录已是真源：%s（含 tree.json）"
                                    " ← 旧版位置，建议迁往 .req-tree/requirements" % rel)
    for rel in ROOT_EMPTY:                   # ② 自建命名空间下的空目录 → 允许预建
        cand = cwd / rel
        if cand.is_dir():
            return cand.resolve(), "当前目录已预建：%s" % rel

    git_root = find_git_root(cwd)
    if git_root:
        return (git_root / "docs" / "requirements"), \
            "仓库内 docs/requirements（检测到 git 仓库：%s）" % git_root
    return (cwd / ".req-tree" / "requirements"), "工作区 .req-tree/requirements（未检测到 git 仓库）"


def resolve_root(explicit):
    return resolve_root_info(explicit)[0]


def store_path(root: Path) -> Path:
    return root / "tree.json"


def legacy_hint(root: Path) -> str:
    """真源若落在旧版宿主专有路径下，返回迁移建议；否则返回空串。

    「迁移兼容」的另一半：旧位置继续被认得（见 ROOT_LEGACY），
    但必须主动提示该搬了 —— 否则用户永远不会知道默认落点已经变了。
    """
    if root.parent.name == ".workbuddy":
        return ("  ⚠ 真源位于旧版宿主专有路径（.workbuddy/requirements），"
                "建议迁往 .req-tree/requirements。\n"
                "    把该目录下的 tree.json / journal.jsonl / baselines.json / "
                "snapshots/ 一起移过去即可；本工具仍认得旧位置，移动过程中不会丢数据。")
    return ""


class WriteLock:
    """跨进程写锁：保证同一时刻只有一个 todoctl 在改真源。

    - 对 `<root>/tree.lock` 取**非阻塞字节锁**（Windows `msvcrt` / POSIX `fcntl`）
    - 进程退出即由系统自动释放；**锁文件常驻不删**（避免触发删除拦截与回收站入站）
    - 只防「同时写」，**不限制「并行推进多条需求」**——后者是允许的
    """

    def __init__(self, root: Path, wait: float = 0.0):
        self.path = root / "tree.lock"
        self.fh = None
        self.wait = max(0.0, float(wait or 0.0))

    def _syslock(self, unlock=False):
        self.fh.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(self.fh.fileno(),
                           msvcrt.LK_UNLCK if unlock else msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(self.fh.fileno(),
                        fcntl.LOCK_UN if unlock else (fcntl.LOCK_EX | fcntl.LOCK_NB))

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fh = open(self.path, "a+b")
        if self.fh.seek(0, os.SEEK_END) == 0:
            self.fh.write(b"\0")
            self.fh.flush()
        deadline = time.time() + self.wait
        while True:
            try:
                self._syslock()
                return self
            except OSError:
                if time.time() >= deadline:
                    self.fh.close()
                    self.fh = None
                    raise Fail(
                        "真源正被另一个进程写入（锁文件 %s）。\n"
                        "  常见原因：同机多开了 todoctl；或上一个进程被强杀。\n"
                        "  处理：稍后重试，或用 `--wait 5` 等待。\n"
                        "  说明：本锁只防「同时写」，不限制「并行推进多条需求」。" % self.path)
                time.sleep(0.15)

    def __exit__(self, *exc):
        if self.fh is None:
            return False
        try:
            self._syslock(unlock=True)
        except Exception:
            pass
        try:
            self.fh.close()
        except Exception:
            pass
        return False


# ------------------------------------------------------------------ 自动快照（无 git 时的回滚依据）

def snapshot_dir(root: Path) -> Path:
    return root / SNAPSHOT_DIR


def take_snapshot(root: Path, data: dict) -> str:
    """写入前留档：`last.json`（上一版）+ `prev.json`（上上版）+ 按天一份。

    没有 git 时，这是「改错了能退回去」的唯一依据。
    """
    d = snapshot_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    notes = []

    last, prev = d / "last.json", d / "prev.json"
    if last.exists():
        try:
            prev.write_text(last.read_text(encoding="utf-8"), encoding="utf-8")
        except Exception:
            pass
    try:
        last.write_text(payload, encoding="utf-8")
    except Exception as exc:
        return "快照写入失败：%s" % exc

    today = datetime.now().strftime("%Y%m%d")
    daily = d / ("tree-%s.json" % today)
    if not daily.exists():
        daily.write_text(payload, encoding="utf-8")
        notes.append("按天快照 %s" % daily.name)
        prune_snapshots(root)
    return "；".join(notes)


def prune_snapshots(root: Path) -> None:
    """按天快照只保留最近 N 天。删除量极小（每天最多 1 个文件）。"""
    d = snapshot_dir(root)
    if not d.is_dir():
        return
    cutoff = datetime.now() - timedelta(days=SNAPSHOT_KEEP_DAYS)
    for f in sorted(d.glob("tree-*.json")):
        m = re.match(r"^tree-(\d{8})\.json$", f.name)
        if not m:
            continue
        try:
            dt = datetime.strptime(m.group(1), "%Y%m%d")
        except ValueError:
            continue
        if dt < cutoff:
            try:
                f.unlink()
            except Exception:
                pass


def list_snapshots(root: Path) -> list:
    d = snapshot_dir(root)
    if not d.is_dir():
        return []
    out = []
    for f in sorted(d.glob("*.json")):
        st = f.stat()
        out.append({"name": f.name, "path": f, "size": st.st_size,
                    "mtime": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S")})
    return out


def load(root: Path) -> dict:
    p = store_path(root)
    if not p.exists():
        return {
            "version": 1,
            "project": root.parent.name,
            "next_seq": 1,
            "focus": None,
            "last_resume": None,
            "resume_count": 0,
            "nodes": [],
            "updated": now_sec(),
        }
    with p.open("r", encoding="utf-8") as fh:
        try:
            data = json.load(fh)
        except ValueError as exc:
            raise Fail(
                "真源 tree.json 无法解析：%s\n"
                "  处理：从 snapshots/last.json（上一版）或 pre-restore-*.json 取回，或先修好再重试。\n"
                "  文件：%s" % (exc, p))
    if not isinstance(data, dict) or not isinstance(data.get("nodes"), list):
        raise Fail("真源 tree.json 结构异常（顶层应为「含 nodes 数组的对象」）。\n  文件：%s" % p)
    data.setdefault("version", 1)
    data.setdefault("nodes", [])
    data.setdefault("focus", None)
    data.setdefault("last_resume", None)
    data.setdefault("resume_count", 0)
    data.setdefault("project", root.parent.name)
    data["next_seq"] = max(int(data.get("next_seq") or 1), len(data["nodes"]) + 1)
    migrate_legacy(data)          # 老数据：close_override 字符串 → 带作用域的 close_override_ids
    return data


AUTO_RENDER = True       # 真源变更后自动刷新派生物（由 --no-render 关闭）
_LAST_NEW_ID = None      # 供变更日志记录「刚新增的是哪个节点」


def write_views(out: Path, data: dict, root: Path = None) -> dict:
    """生成派生物（覆盖写）。真源是 tree.json / journal.jsonl / baselines.json，视图禁止手改。

    七个渲染器**共享同一个 `Ctx`**：索引、校验、位置编号、统计各算一次。
    原先每个渲染器各自重算，一次 `write_views` 会跑 8 遍 `validate`（每遍 O(n²) 起）。
    """
    out.mkdir(parents=True, exist_ok=True)
    src = root or out
    ctx = Ctx(data)
    files = {
        "TREE.md": render_tree(data, ctx),
        "ACTIVE.md": render_active(data, ctx),
        "FOCUS.md": render_focus(data, ctx),
        "RESUME.md": render_resume(data, ctx),
        "BENCH.md": render_bench(data, ctx),
        "BASELINE.md": render_baseline_md(data, read_baselines(src), ctx),
        "dashboard.html": render_dashboard(data, ctx),
    }
    for name, content in files.items():
        (out / name).write_text(content, encoding="utf-8")
    return files


def save(root: Path, data: dict, rerender: bool = True) -> None:
    root.mkdir(parents=True, exist_ok=True)
    data["updated"] = now_sec()
    p = store_path(root)
    if p.exists():                      # 覆盖前留档（无 git 时的回滚依据）
        try:
            take_snapshot(root, json.loads(p.read_text(encoding="utf-8")))
        except Exception as exc:
            print("  ⚠ 快照失败（真源仍会正常写入）：%s" % exc, file=sys.stderr)
    tmp = p.with_name("tree.json.tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.replace(tmp, p)          # 同目录原子替换
    # 视图是派生物，不能依赖人记得手动刷新（否则又变成「记录对不上」的老问题）
    if rerender and AUTO_RENDER and (root / "TREE.md").exists():
        try:
            write_views(root, data)
        except Exception as exc:
            print("  ⚠ 派生物自动刷新失败（真源已保存，可跑 render 补救）：%s" % exc, file=sys.stderr)
    elif rerender and AUTO_RENDER and len(data["nodes"]) == 1:
        # 只在「第一条节点」这一刻提示一次：否则用户会问「TREE.md 在哪」
        print("  提示：派生物尚未生成 —— 跑一次 `todoctl render`，之后视图就会随每次写入自动跟随。")


# ------------------------------------------------------------------ 结构查询

class Tree:
    """一次构建、只读复用的一组索引。

    为什么要它：原实现在 `depth_of` / `children_of` / `summarize` / `aggregate_by_line`
    里**逐节点重建**索引（每次 `by_id()` 都是 O(n)），使渲染随节点数近似 O(n²)——
    实测 200 / 400 / 800 节点分别为 66 / 207 / 771 ms，800 节点时**每次写入要 1.16 s**，
    而本工具的核心用法是「做一步记一句」，写入变慢会直接抑制记录频率。

    ⚠ 它是**只读快照**：必须在最后一次修改 `data` 之后构建；构建后再改 `data` 就会失真。
    所有方法都与原散装实现**逐字节等价**（含成环、父节点缺失等脏数据情形）。
    """

    __slots__ = ("data", "by_id", "_kids", "_depth", "_line")

    def __init__(self, data: dict):
        self.data = data
        self.by_id = {}
        self._kids = {}
        self._depth = {}
        self._line = {}
        for n in data["nodes"]:
            self.by_id[n["id"]] = n
            self._kids.setdefault(n.get("parent") or None, []).append(n)

    def children(self, nid):
        return self._kids.get(nid or None, [])

    def roots(self):
        return self._kids.get(None, [])

    def depth(self, nid: str) -> int:
        """到根的边数（根为 0）。按 nid 记忆化：单次走链是 O(深度)，摊还后近 O(1)。"""
        v = self._depth.get(nid)
        if v is not None:
            return v
        d, cur, seen = 0, nid, set()
        while cur in self.by_id and cur not in seen:
            seen.add(cur)
            par = self.by_id[cur].get("parent") or None
            if not par:
                break
            d += 1
            cur = par
        self._depth[nid] = d
        return d

    def line(self, nid):
        """「线」= 该节点所属的最顶层可及祖先（与 `line_of` 等价）。"""
        if not nid or nid not in self.by_id:
            return None
        if nid in self._line:
            return self._line[nid]
        cur, seen, last = self.by_id[nid].get("parent") or None, {nid}, None
        while cur and cur in self.by_id and cur not in seen:
            seen.add(cur)
            last = cur
            cur = self.by_id[cur].get("parent") or None
        self._line[nid] = last or nid
        return self._line[nid]


class Ctx:
    """一次渲染内共享的只读计算结果：索引 / 校验 / 位置编号 / 统计。

    没有它时，七个渲染器各自重算 `validate`（实测一次 `write_views` 调用 8 次），
    每份都是 O(n²) 起 —— 这是渲染慢的第二个来源。
    """

    __slots__ = ("data", "ix", "errors", "warns", "wm", "pm", "stats")

    def __init__(self, data: dict):
        self.data = data
        self.ix = Tree(data)
        self.errors, self.warns = validate(data, self.ix)
        self.wm = {}
        for w in self.warns:
            if w.get("id"):
                self.wm.setdefault(w["id"], []).append(w["msg"])
        self.pm = path_map(data, self.ix)
        self.stats = summarize(data, self.ix)


def by_id(data: dict, ix=None) -> dict:
    return (ix or Tree(data)).by_id


def children_of(data: dict, nid, ix=None):
    return (ix or Tree(data)).children(nid)


def roots_of(data: dict, ix=None):
    return (ix or Tree(data)).roots()


def path_map(data: dict, ix=None) -> dict:
    """渲染用的位置编号（形如 1.3.2）。⚠ 它只是视图，不是身份。"""
    ix = ix or Tree(data)
    out = {}

    def walk(node, prefix, seen):
        nid = node["id"]
        if nid in seen:               # 成环时截断，避免无限递归
            out[nid] = prefix + "(环)"
            return
        out[nid] = prefix
        branch = seen | {nid}
        for i, ch in enumerate(ix.children(nid), 1):
            walk(ch, "%s.%d" % (prefix, i), branch)

    for i, r in enumerate(ix.roots(), 1):
        walk(r, str(i), set())
    for n in data["nodes"]:           # 因成环/孤儿而不可达的节点单独标注
        out.setdefault(n["id"], "(游离)")
    return out


def depth_of(data: dict, nid: str, ix=None) -> int:
    return (ix or Tree(data)).depth(nid)


def descendants(data: dict, nid: str, ix=None):
    ix = ix or Tree(data)
    out, seen, stack = [], {nid}, [nid]
    while stack:
        cur = stack.pop()
        for ch in ix.children(cur):
            if ch["id"] in seen:      # 防成环时无限循环（脏数据自保）
                continue
            seen.add(ch["id"])
            out.append(ch)
            stack.append(ch["id"])
    return out


def ancestors(data: dict, nid: str, ix=None):
    ix = ix or Tree(data)
    out, cur, seen = [], ix.by_id.get(nid, {}).get("parent") or None, set()
    while cur and cur in ix.by_id and cur not in seen:
        seen.add(cur)
        out.append(cur)
        cur = ix.by_id[cur].get("parent") or None
    return out


def terminal_ancestor(data: dict, nid: str, ix=None):
    """返回最近的「已收口/已放弃」祖先 ID；没有则 None。

    在已收口的祖先下继续推进，本质上就是正在制造一个假收口 —— 所以 `start` 默认拦，
    拦不住（显式 --force）时也要留下 W7 告警，而不是静默。
    """
    ix = ix or Tree(data)
    for aid in ancestors(data, nid, ix):
        if ix.by_id.get(aid, {}).get("status") in TERMINAL_STATUSES:
            return aid
    return None


def migrate_legacy(data: dict) -> list:
    """一次性懒迁移：老数据里 `close_override` 只是一个字符串标记（无作用域）。

    该标记的语义是**永久生效** —— 一旦带 override 收口过，该子树此后永不触发 E12，
    于是「收口后又冒出来的新子项」就永远查不出来（这是 E12 的逃生门被固化成了漏洞）。

    新语义：`close_override_ids` = 收口当时**显式接受**的那批未收口后代。
    E12 只对「不在该集合里」的未收口后代报错 —— 于是历史状态不被误报，
    而**此后新增**的未收口子项会被照旧逮住。

    迁移动作：把「当前仍未收口的后代」视为当时已接受的那批（保留现状，不改判定）。
    """
    migrated = []
    for n in data["nodes"]:
        if n.get("close_override") and n.get("close_override_ids") is None:
            n["close_override_ids"] = sorted(
                d["id"] for d in descendants(data, n["id"])
                if d.get("status") not in TERMINAL_STATUSES)
            migrated.append(n["id"])
        elif n.get("close_override_ids") is None:
            n["close_override_ids"] = []
    return migrated


def subtree_all_terminal(data: dict, nid: str, ix=None) -> bool:
    ix = ix or Tree(data)
    node = ix.by_id.get(nid, {})
    if node.get("status") not in TERMINAL_STATUSES:
        return False
    return all(d.get("status") in TERMINAL_STATUSES for d in descendants(data, nid, ix))


def line_of(data: dict, nid, ix=None):
    """「线」= 该节点所属的最顶层根需求。

    变更按线归属，恢复时才能把「本条线」与「别的需求顺手做的」分开，
    否则一个 commit 里混着几条需求，叙述全是交叉的。
    """
    return (ix or Tree(data)).line(nid)


def journal_path(root: Path) -> Path:
    return root / "journal.jsonl"


def append_journal(root: Path, data: dict, entries) -> None:
    """追加式变更日志（只增不改）。是「按线抽取变更叙述」的唯一依据。"""
    if not entries:
        return
    ts = now_sec()
    with journal_path(root).open("a", encoding="utf-8") as fh:
        for e in entries:
            rec = {
                "ts": ts,
                "op": e.get("op"),
                "id": e.get("id"),
                "line": line_of(data, e.get("id")),
                "detail": (e.get("detail") or "")[:200],
            }
            if e.get("delta"):
                rec["delta"] = e["delta"]
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def read_journal(root: Path):
    p = journal_path(root)
    if not p.exists():
        return []
    out = []
    with p.open("r", encoding="utf-8") as fh:
        for ln in fh:
            ln = ln.strip()
            if not ln:
                continue
            try:
                out.append(json.loads(ln))
            except ValueError:
                continue          # 坏行直接跳过，不让日志阻断主流程
    # 按 ts **稳定**排序：git 合并（`.gitattributes` 里给 journal.jsonl 设 `merge=union`）后
    # 文件内顺序会交错，不排序则 `log` 读起来是乱的。同 ts 的保持文件内原顺序。
    out.sort(key=lambda r: str(r.get("ts") or ""))
    return out


DELTA_KEYS = ("status", "blocked_by", "next", "next_step", "parent", "kind", "title")


def short_index(data: dict) -> dict:
    """命令执行前的轻量字段快照，用于算出 before/after。"""
    return {n["id"]: {k: n.get(k) for k in DELTA_KEYS} for n in data["nodes"]}


def index_delta(before: dict, after: dict) -> dict:
    """字段级差量 —— 让 journal 从「发生了什么操作」升级为「状态怎么变的」。"""
    d = {}
    for nid, a in after.items():
        b = before.get(nid)
        if b is None:
            d[nid] = {"added": True, "status": [None, a.get("status")]}
            continue
        ch = {k: [b.get(k), av] for k, av in a.items() if b.get(k) != av}
        if ch:
            d[nid] = ch
    for nid in before:
        if nid not in after:
            d[nid] = {"removed": True}
    return d


def delta_text(rec: dict) -> str:
    """把一条 journal 记录的差量压成一行可读文字。"""
    d = rec.get("delta") or {}
    nid = rec.get("id")
    ch = d.get(nid)
    if not ch:
        for k, v in d.items():
            ch, nid = v, k
            break
    if not ch:
        return ""
    if ch.get("added"):
        return "新增"
    if ch.get("removed"):
        return "移除"
    parts = []
    if "status" in ch:
        b, a = ch["status"]
        parts.append("%s → %s" % (b or "-", a or "-"))
    for k, (b, a) in ch.items():
        if k == "status":
            continue
        parts.append("%s: %s ⇒ %s" % (k, str(b or "-")[:24], str(a or "-")[:24]))
    return "；".join(parts)[:140]


def summarize(data: dict, ix=None) -> dict:
    ix = ix or Tree(data)
    counts = {s: 0 for s in STATUSES}
    for n in data["nodes"]:
        if n.get("status") in counts:
            counts[n["status"]] += 1
    depths = [ix.depth(n["id"]) for n in data["nodes"]] or [0]
    return {
        "total": len(data["nodes"]),
        "n_closed": counts["closed"],
        "n_unclosed": sum(counts[s] for s in ACTIVE_STATUSES),
        "n_blocked": counts["blocked"],
        "n_doing": counts["doing"],
        "max_depth": max(depths),
        "counts": counts,
    }


# ------------------------------------------------------------------ 校验

def validate(data: dict, ix=None):
    """返回 (errors, warns)，元素为 dict(code,id,msg)。"""
    errors, warns = [], []
    ix = ix or Tree(data)
    idx = ix.by_id
    seen = set()

    for n in data["nodes"]:
        nid = n.get("id") or ""
        if nid in seen:
            errors.append({"code": "E11", "id": nid, "msg": "ID 重复"})
        seen.add(nid)
        if not ID_RE.match(nid):
            errors.append({"code": "E1", "id": nid, "msg": "ID 格式非法（应为 R-0001 形式）"})
        if n.get("status") not in STATUSES:
            errors.append({"code": "E4", "id": nid, "msg": "状态非法：%r" % (n.get("status"),)})
        if n.get("kind") not in KINDS:
            errors.append({"code": "E5", "id": nid, "msg": "类型非法：%r" % (n.get("kind"),)})
        if not (n.get("title") or "").strip():
            errors.append({"code": "E14", "id": nid, "msg": "标题为空"})
        par = n.get("parent")
        if par and par not in idx:
            errors.append({"code": "E2", "id": nid, "msg": "父节点不存在：%s" % par})
        if n.get("status") in ("doing", "done", "closed") and not (n.get("done_when") or "").strip():
            errors.append({"code": "E7", "id": nid, "msg": "%s 状态缺少验收条件 done_when" % n["status"]})
        if n.get("status") == "closed":
            if not (n.get("close_evidence") or []):
                errors.append({"code": "E6", "id": nid, "msg": "closed 缺少收口证据 close_evidence"})
            if not n.get("closed_at"):
                errors.append({"code": "E8", "id": nid, "msg": "closed 缺少 closed_at"})
        if n.get("status") == "blocked" and not (n.get("blocked_by") or "").strip():
            errors.append({"code": "E13", "id": nid, "msg": "blocked 缺少 blocked_by 理由"})
        for dep in (n.get("depends_on") or []):
            if dep not in idx:
                errors.append({"code": "E9", "id": nid, "msg": "依赖指向不存在的节点：%s" % dep})
        nx = n.get("next")
        if nx:
            if nx == nid:
                errors.append({"code": "E16", "id": nid, "msg": "next 不能指向自身"})
            elif nx not in idx:
                errors.append({"code": "E15", "id": nid, "msg": "计划中的下一步指向不存在的节点：%s" % nx})

        cur, chain = nid, set()
        while cur:
            if cur in chain:
                errors.append({"code": "E3", "id": nid, "msg": "父子关系成环"})
                break
            chain.add(cur)
            cur = idx.get(cur, {}).get("parent")

    # 依赖成环
    white, grey, black = set(idx), set(), set()

    def dfs(cur, path):
        if cur in black:
            return
        if cur in grey:
            errors.append({"code": "E10", "id": cur, "msg": "depends_on 成环：%s" % " → ".join(path + [cur])})
            return
        grey.add(cur)
        for dep in (idx[cur].get("depends_on") or []):
            if dep in idx:
                dfs(dep, path + [cur])
        grey.discard(cur)
        black.add(cur)

    for nid in list(white):
        dfs(nid, [])

    # 假收口 / 结构告警
    for n in data["nodes"]:
        nid = n["id"]
        if n.get("status") == "closed":
            unclosed = [d["id"] for d in descendants(data, nid, ix) if d.get("status") not in TERMINAL_STATUSES]
            accepted = set(n.get("close_override_ids") or [])
            fresh = [i for i in unclosed if i not in accepted]
            if fresh:
                extra = ""
                if accepted:
                    extra = ("（另有 %d 个是收口时显式接受的，不计入）" % len(accepted & set(unclosed)))
                errors.append({
                    "code": "E12", "id": nid,
                    "msg": "假收口：已 closed 但仍有 %d 个未收口后代（%s）%s。"
                           "如确认可接受，改用 close %s --override-reason \"理由\"；"
                           "若这些是收口后才新增的，请先为它们建立自己的节点或重新推进父项" % (
                               len(fresh), ", ".join(fresh[:5]), extra, nid),
                })
        d = ix.depth(nid)
        if d > MAX_DEPTH:
            warns.append({"code": "W1", "id": nid,
                          "msg": "深度 %d 层 > %d 层：评估是否应升级为独立子项目，或说明拆分位置错位" % (d, MAX_DEPTH)})
        sib = [c for c in ix.children(nid) if c.get("status") not in TERMINAL_STATUSES]
        if len(sib) > MAX_OPEN_SIBLINGS:
            warns.append({"code": "W2", "id": nid,
                          "msg": "名下未收口子节点 %d 个 > %d：考虑拆分或归并" % (len(sib), MAX_OPEN_SIBLINGS)})
        if n.get("status") == "doing":
            unmet = [d2 for d2 in (n.get("depends_on") or []) if idx.get(d2, {}).get("status") != "closed"]
            if unmet:
                warns.append({"code": "W3", "id": nid, "msg": "正在推进但依赖未收口：%s" % ", ".join(unmet)})
        if n.get("status") in ACTIVE_STATUSES:
            ds = days_since(n.get("updated"))
            if n.get("status") == "doing":
                if ds is not None and ds > STALE_DOING_DAYS:
                    warns.append({"code": "W5", "id": nid,
                                  "msg": "doing 已 %d 天未更新（搁置）—— 推进、block 说明原因，或 drop" % ds})
            elif ds is not None and ds > STALE_DAYS:
                warns.append({"code": "W4", "id": nid, "msg": "已 %d 天未更新（状态 %s）" % (ds, n["status"])})

        # 计划中的下一步被跳过：正在做 P，而 P.next 指向的节点尚未开工
        nx = n.get("next")
        if nx and n.get("status") == "doing" and nx in idx and idx[nx].get("status") in NOT_STARTED:
            warns.append({"code": "W6", "id": nid,
                          "msg": "计划中的下一步 %s（%s）尚未开工 —— 当前正在绕行" % (nx, idx[nx].get("title"))})

        # 计划指向了一个**已经终结**的节点：这个计划已经失效（与「绕行」不同，绕行还能回来）
        if nx and nx in idx and idx[nx].get("status") in TERMINAL_STATUSES:
            warns.append({"code": "W8", "id": nid,
                          "msg": "计划中的下一步 %s（%s）已%s —— 该计划已失效，请更新或清除 `--next`" % (
                              nx, idx[nx].get("title"),
                              "收口" if idx[nx].get("status") == "closed" else "放弃")})

        # 在已收口/已放弃的祖先下继续推进 —— 与已声明的收口相矛盾（start 默认已拦，此处兜底）
        if n.get("status") in ("doing", "done"):
            anc = terminal_ancestor(data, nid, ix)
            if anc:
                warns.append({"code": "W7", "id": nid,
                              "msg": "祖先 %s（%s）已收口/已放弃，但它仍在 %s —— 与已声明的收口矛盾，"
                                     "请确认后续处理方式" % (anc, idx[anc].get("status"), n["status"])})

    return errors, warns


def warn_map(data: dict, ix=None) -> dict:
    _, warns = validate(data, ix)
    out = {}
    for w in warns:
        if w.get("id"):
            out.setdefault(w["id"], []).append(w["msg"])
    return out


def report_issues(errors, warns, quiet: bool = False) -> int:
    if not quiet:
        for w in warns:
            print("  ⚠ %s %s [%s]" % (w["code"], w.get("id") or "-", w["msg"]))
        for e in errors:
            print("  ✖ %s %s [%s]" % (e["code"], e.get("id") or "-", e["msg"]))
    if errors:
        print("\n校验未通过：%d 个错误 / %d 个告警" % (len(errors), len(warns)))
        print("修复后重跑：todoctl check")
        return 1
    if warns and not quiet:
        print("\n校验通过（%d 个告警，不阻断）" % len(warns))
    return 0


# ------------------------------------------------------------------ 渲染共用

def node_line(data: dict, node: dict, pm: dict, indent: int, wm: dict, collapsed_note: str = "") -> str:
    nid = node["id"]
    marks = []
    if data.get("focus") == nid:
        marks.append("★聚焦")
    if nid in wm:
        marks.append("⚠")
    suffix = (" " + " ".join(marks)) if marks else ""
    extra = (" " + collapsed_note) if collapsed_note else ""
    return "%s- %s %s `%s` %s %s · %s%s%s" % (
        "  " * indent,
        pm.get(nid, "?"),
        (node.get("title") or "").strip(),
        nid,
        GLYPH.get(node.get("status"), "?"),
        node.get("status"),
        KIND_CN.get(node.get("kind"), node.get("kind")),
        suffix,
        extra,
    )


def md_header(title: str, data: dict, ctx=None) -> list:
    st = ctx.stats if ctx is not None else summarize(data)
    return [
        "# %s" % title,
        "",
        "> 由 `todoctl render` 生成，**禁止手改**。真源：`tree.json`",
        "> 生成时间 %s · 节点 %d · 已收口 %d · 未收口 %d · 阻塞 %d · 最大深度 %d" % (
            now(), st["total"], st["n_closed"], st["n_unclosed"], st["n_blocked"], st["max_depth"]),
        "",
    ]


def render_tree(data: dict, ctx=None) -> str:
    ctx = ctx or Ctx(data)
    ix, pm, wm = ctx.ix, ctx.pm, ctx.wm
    lines = md_header("需求树 · %s" % (data.get("project") or ""), data, ctx)
    lines.append("缩进即派生层级。已完成全部后代收口的子树折叠为一行。")
    lines.append("")

    def walk(node, indent, seen=None):
        seen = set(seen or ())
        nid = node["id"]
        if nid in seen:               # 成环自保
            lines.append("%s- `%s`（环，请先修数据）" % ("  " * indent, nid))
            return
        branch = seen | {nid}
        kids = ix.children(nid)
        if kids and subtree_all_terminal(data, nid, ix):
            note = "（子树 %d 个节点已全部收口）" % len(descendants(data, nid, ix))
            lines.append(node_line(data, node, pm, indent, wm, note))
            return
        lines.append(node_line(data, node, pm, indent, wm))
        for ch in kids:
            walk(ch, indent + 1, branch)

    for r in ix.roots():
        walk(r, 0)

    if not data["nodes"]:
        lines.append("_（空）用 `todoctl add --title \"...\"` 建立第一个根需求。_")
        lines.append("")

    if ctx.warns:
        lines += ["", "## 告警（不阻断）", ""]
        for w in ctx.warns:
            lines.append("- `%s` %s %s" % (w["code"], w.get("id") or "-", w["msg"]))
    return "\n".join(lines) + "\n"


def render_active(data: dict, ctx=None) -> str:
    ctx = ctx or Ctx(data)
    ix, pm, wm = ctx.ix, ctx.pm, ctx.wm
    active = [n for n in data["nodes"] if n.get("status") in ACTIVE_STATUSES]
    keep = set(n["id"] for n in active)
    for n in active:
        keep.update(ancestors(data, n["id"], ix))
    lines = md_header("活跃视图 · %s" % (data.get("project") or ""), data, ctx)
    lines.append("只列未收口节点（open / doing / blocked / done），已收口的祖先仅作上下文占位。")
    lines.append("")

    def walk(node, indent, seen=None):
        seen = set(seen or ())
        nid = node["id"]
        if nid in seen:               # 成环自保
            return
        branch = seen | {nid}
        allkids = ix.children(nid)
        kids = [c for c in allkids if c["id"] in keep]
        hidden = len(allkids) - len(kids)
        row = node_line(data, node, pm, indent, wm)
        if node.get("status") in TERMINAL_STATUSES:
            row += "（上下文）"
        if hidden:
            row += "  … %d 个已收口子节点已折叠" % hidden
        lines.append(row)
        for ch in kids:
            walk(ch, indent + 1, branch)

    for r in ix.roots():
        if r["id"] in keep:
            walk(r, 0)
    if not active:
        lines.append("_（无未收口节点）_")
    return "\n".join(lines) + "\n"


def render_focus(data: dict, ctx=None) -> str:
    ctx = ctx or Ctx(data)
    ix, pm, wm, idx = ctx.ix, ctx.pm, ctx.wm, ctx.ix.by_id
    fid = data.get("focus")
    lines = md_header("聚焦视图 · %s" % (data.get("project") or ""), data, ctx)
    if not fid or fid not in idx:
        lines.append("当前无聚焦节点。用 `todoctl focus R-0006` 指定。")
        return "\n".join(lines) + "\n"

    node = idx[fid]
    chain = list(reversed(ancestors(data, fid, ix))) + [fid]
    lines.append("## 当前分支")
    lines.append("")
    lines.append(" / ".join("%s %s" % (pm.get(i, "?"), idx[i].get("title") or "") for i in chain))
    lines.append("")
    lines.append("## 在推进")
    lines.append("")
    lines.append("- `%s` %s（%s / %s）" % (fid, node.get("title"), node.get("status"), node.get("kind")))
    if node.get("done_when"):
        lines.append("- 收口条件：%s" % node["done_when"])
    if node.get("blocked_by"):
        lines.append("- 阻塞于：%s" % node["blocked_by"])
    if node.get("depends_on"):
        lines.append("- 依赖于：%s" % ", ".join(
            "`%s` %s" % (d, idx.get(d, {}).get("status", "?")) for d in node["depends_on"]))
    lines.append("")

    kids = ix.children(fid)
    lines.append("## 由它派生（%d）" % len(kids))
    lines.append("")
    if kids:
        for k in kids:
            lines.append(node_line(data, k, pm, 0, wm))
            for gk in ix.children(k["id"]):
                lines.append(node_line(data, gk, pm, 1, wm))
    else:
        lines.append("_（暂无派生）_")
    lines.append("")
    if fid in wm:
        lines.append("## 该节点告警")
        lines.append("")
        for m in wm[fid]:
            lines.append("- ⚠ %s" % m)
        lines.append("")
    return "\n".join(lines) + "\n"


def resume_target(data):
    """确定恢复目标：优先 focus；没有则推断（最近变动的 doing 节点，退而求其次取最近变动的未收口节点）。"""
    idx = by_id(data)
    fid = data.get("focus")
    if fid and fid in idx:
        return fid, False
    cand = [n for n in data["nodes"] if n.get("status") == "doing"]
    if not cand:
        cand = [n for n in data["nodes"] if n.get("status") in ACTIVE_STATUSES]
    if not cand:
        return None, False
    cand.sort(key=lambda n: parse_dt(n.get("updated")) or datetime.min, reverse=True)
    return cand[0]["id"], True


def resume_brief(data: dict, ctx=None) -> dict:
    """跨次交接所需的全部上下文，供终端与 RESUME.md 共用（避免两处渲染漂移）。"""
    ctx = ctx or Ctx(data)
    nid, inferred = resume_target(data)
    ix = ctx.ix
    idx, pm, wm = ix.by_id, ctx.pm, ctx.wm
    since = parse_dt(data.get("last_resume"))
    changed = []
    if since:
        for n in data["nodes"]:
            u = parse_dt(n.get("updated"))
            if u and u > since:
                changed.append(n)
        changed.sort(key=lambda n: parse_dt(n.get("updated")) or datetime.min)

    # 按「线」分流：本条线（聚焦所在需求）进现场；旁支只给计数与入口，不混进现场
    my_line = line_of(data, nid, ix) if nid else None
    same, other = [], {}
    for n in changed:
        ln = line_of(data, n["id"], ix)
        if ln == my_line:
            same.append(n)
        else:
            other.setdefault(ln, []).append(n)

    def item(n):
        return {"id": n["id"], "path": pm.get(n["id"], "?"), "title": n.get("title"),
                "status": n.get("status"), "updated": n.get("updated")}

    blocked = [n for n in data["nodes"] if n.get("status") == "blocked"]

    brief = {
        "project": data.get("project") or "",
        "generated": now_sec(),
        "last_resume": data.get("last_resume"),
        "resume_count": int(data.get("resume_count") or 0),
        "inferred": inferred,
        "stats": ctx.stats,
        "doing": doing_overview(data, ctx),
        "node": None,
        "chain": [],
        "changed": [item(n) for n in changed],
        "changed_same": [item(n) for n in same],
        "changed_other": [
            {"line": ln, "path": pm.get(ln, "?"),
             "title": idx.get(ln, {}).get("title", ln or ""),
             "items": [item(n) for n in v]}
            for ln, v in sorted(other.items(), key=lambda kv: -len(kv[1]))
        ],
        "blocked": [{"id": n["id"], "path": pm.get(n["id"], "?"), "title": n.get("title"),
                     "because": n.get("blocked_by")} for n in blocked],
    }
    if nid and nid in idx:
        n = idx[nid]
        brief["node"] = {
            "id": nid, "path": pm.get(nid, "?"), "title": n.get("title"),
            "status": n.get("status"), "kind": KIND_CN.get(n.get("kind"), n.get("kind")),
            "why": n.get("why") or "", "done_when": n.get("done_when") or "",
            "next_step": n.get("next_step") or "", "blocked_by": n.get("blocked_by") or "",
            "depends_on": [{"id": d, "status": idx.get(d, {}).get("status", "?"),
                            "title": idx.get(d, {}).get("title", "")}
                           for d in (n.get("depends_on") or [])],
            "notes": list(n.get("notes") or [])[-5:],
            "warns": wm.get(nid, []),
            "children": [{"id": c["id"], "path": pm.get(c["id"], "?"), "title": c.get("title"),
                          "status": c.get("status")} for c in ix.children(nid)],
        }
        brief["chain"] = [{"id": i, "path": pm.get(i, "?"), "title": idx[i].get("title")}
                          for i in reversed(ancestors(data, nid, ix))]
        brief["chain"].append({"id": nid, "path": pm.get(nid, "?"), "title": n.get("title")})
    return brief


def resume_md(brief: dict) -> str:
    L = ["# 恢复简报 · %s" % brief["project"], ""]
    L.append("> 由 `todoctl render` / `resume` 生成，**禁止手改**。真源：`tree.json`")
    if brief["last_resume"]:
        L.append("> 上次恢复 %s（第 %d 次）· 本页生成 %s" % (
            brief["last_resume"], brief["resume_count"], brief["generated"]))
    else:
        L.append("> 尚未执行过 `resume`（无基线）· 本页生成 %s" % brief["generated"])
    st = brief["stats"]
    L.append("> 存量 · 节点 %d · 未收口 %d · 阻塞 %d · 最大深度 %d" % (
        st["total"], st["n_unclosed"], st["n_blocked"], st["max_depth"]))
    L.append("")

    n = brief["node"]
    if not n:
        L.append("_真源里还没有节点。用 `todoctl add` 建第一条。_")
        return "\n".join(L) + "\n"

    L.append("## 你在做什么%s" % ("（推断：未设 focus）" if brief["inferred"] else ""))
    L.append("")
    L.append("`%s`" % " / ".join("%s %s" % (c["path"], c["title"]) for c in brief["chain"]))
    L.append("")
    L.append("| 项 | 内容 |")
    L.append("|---|---|")
    L.append("| 节点 | `%s` %s（%s / %s） |" % (n["id"], n["title"], n["status"], n["kind"]))
    L.append("| 为什么做 | %s |" % (n["why"] or "_未记录_"))
    L.append("| 收口条件 | %s |" % (n["done_when"] or "**未记录 → 既不能推进也不能收口**"))
    L.append("| 下一步 | %s |" % (n["next_step"] or
                                "**未记录** → `todoctl update %s --next-step \"...\"`" % n["id"]))
    if n["blocked_by"]:
        L.append("| 阻塞于 | %s |" % n["blocked_by"])
    if n["depends_on"]:
        L.append("| 依赖于 | %s |" % "、".join(
            "`%s` %s（%s）" % (d["id"], d["title"], d["status"]) for d in n["depends_on"]))
    L.append("")

    L.append("## 进度轨迹")
    L.append("")
    if n["notes"]:
        L.append("最近 %d 条：" % len(n["notes"]))
        L.append("")
        for x in n["notes"]:
            L.append("- %s" % x)
    else:
        L.append("_暂无记录。做一步就用 `todoctl note %s \"...\"` 留一句 —— 这是跨次恢复的关键。_" % n["id"])
    L.append("")

    if n["warns"]:
        L.append("## 该节点告警")
        L.append("")
        for w in n["warns"]:
            L.append("- ⚠ %s" % w)
        L.append("")

    if n["children"]:
        L.append("## 由它派生（%d）" % len(n["children"]))
        L.append("")
        for c in n["children"]:
            L.append("- %s %s `%s` %s" % (c["path"], c["title"], c["id"], GLYPH.get(c["status"], "?")))
        L.append("")

    others = [i for i in brief["doing"]["items"] if i["id"] != n["id"]]
    if others:
        L.append("## 手头其他进行中（%d）" % len(others))
        L.append("")
        for it in others:
            flags = _bench_flags(it)
            L.append("- %s %s `%s`%s" % (it["path"], it["title"], it["id"],
                                        ("　" + " ".join(flags)) if flags else ""))
            if it["next_step"]:
                L.append("  - 下一步：%s" % it["next_step"])
            if it["next"]:
                L.append("  - 计划下一步：%s（%s）" % (it["next"], it["next_status"] or "?"))
        L.append("")

    if brief["doing"]["skipped"]:
        L.append("## 计划中的下一步被绕过（%d）" % len(brief["doing"]["skipped"]))
        L.append("")
        for it in brief["doing"]["skipped"]:
            L.append("- %s `%s` → 计划下一步 %s（%s）尚未开工" % (
                it["title"], it["id"], it["next"], it["next_title"] or "?"))
        L.append("")
        L.append("> 绕行本身没问题，但要知道自己在绕。`todoctl bench` 可随时看全。")
        L.append("")

    L.append("## 自上次恢复以来的变动（%d）" % len(brief["changed"]))
    L.append("")
    if not brief["last_resume"]:
        L.append("_首次恢复，无基线。_")
    elif not brief["changed"]:
        L.append("_无。_")
    else:
        if brief["changed_same"]:
            L.append("**本条线（%d 个节点）**" % len(brief["changed_same"]))
            L.append("")
            for c in brief["changed_same"]:
                L.append("- %s %s %s `%s`（%s）" % (
                    c["updated"], GLYPH.get(c["status"], "?"), c["title"], c["id"], c["status"]))
            L.append("")
        if brief["changed_other"]:
            L.append("**旁支：%d 条其他需求线（%d 个节点）** —— 未并入上方现场" % (
                len(brief["changed_other"]),
                sum(len(g["items"]) for g in brief["changed_other"])))
            L.append("")
            for g in brief["changed_other"]:
                heads = "、".join(i["title"] for i in g["items"][:3])
                if len(g["items"]) > 3:
                    heads += "…"
                L.append("- `%s` %s —— %d 个节点：%s" % (g["line"], g["title"], len(g["items"]), heads))
                L.append("  - 要接手这条线：`todoctl resume %s`" % g["line"])
            L.append("")
            L.append("> 旁支成果已经在真源里，**不会丢**；这里只是不把它混进当前现场。")
            L.append("> 按线抽取完整叙述（可当提交信息）：`todoctl log --line <线ID>`")
            L.append("")
    L.append("")

    if brief["blocked"]:
        L.append("## 别忘了：未收口的阻塞项（%d）" % len(brief["blocked"]))
        L.append("")
        for b in brief["blocked"]:
            L.append("- ⛔ %s %s `%s` ← %s" % (b["path"], b["title"], b["id"], b["because"]))
        L.append("")
    return "\n".join(L) + "\n"


def resume_plain(brief: dict) -> str:
    W = 72
    L = ["═" * W, " 恢复简报 · %s" % brief["project"]]
    if brief["last_resume"]:
        L.append(" 上次恢复 %s（第 %d 次）" % (brief["last_resume"], brief["resume_count"]))
    else:
        L.append(" 首次恢复（无基线）")
    st = brief["stats"]
    L.append(" 存量  节点 %d · 未收口 %d · 阻塞 %d · 最大深度 %d" % (
        st["total"], st["n_unclosed"], st["n_blocked"], st["max_depth"]))
    L.append("═" * W)
    n = brief["node"]
    if not n:
        L.append(" 真源里还没有节点。用 todoctl add 建第一条。")
        return "\n".join(L)

    L.append("")
    L.append("▶ 你在做什么%s" % ("（推断，未设 focus）" if brief["inferred"] else ""))
    L.append("  %s" % " / ".join("%s %s" % (c["path"], c["title"]) for c in brief["chain"]))
    L.append("  %s %s（%s / %s）" % (n["id"], n["title"], n["status"], n["kind"]))
    L.append("")
    L.append("  为什么做  %s" % (n["why"] or "未记录"))
    L.append("  收口条件  %s" % (n["done_when"] or "未记录 → 既不能推进也不能收口"))
    L.append("  下一步    %s" % (n["next_step"] or
                              "未记录 → todoctl update %s --next-step \"...\"" % n["id"]))
    if n["blocked_by"]:
        L.append("  阻塞于    %s" % n["blocked_by"])
    if n["depends_on"]:
        L.append("  依赖于    %s" % "、".join("%s(%s)" % (d["id"], d["status"]) for d in n["depends_on"]))

    if n["notes"]:
        L.append("")
        L.append("▶ 进度轨迹（最近 %d 条）" % len(n["notes"]))
        for x in n["notes"]:
            L.append("  · %s" % x)

    if n["warns"]:
        L.append("")
        L.append("▶ 该节点告警")
        for w in n["warns"]:
            L.append("  ⚠ %s" % w)

    if n["children"]:
        L.append("")
        L.append("▶ 由它派生（%d）" % len(n["children"]))
        for c in n["children"]:
            L.append("  %s %s %s `%s`" % (GLYPH.get(c["status"], "?"), c["path"], c["title"], c["id"]))

    others = [i for i in brief["doing"]["items"] if i["id"] != n["id"]]
    if others:
        L.append("")
        L.append("▶ 手头其他进行中（%d）" % len(others))
        for it in others:
            flags = _bench_flags(it)
            L.append("  %s %s `%s`%s" % (it["path"], it["title"], it["id"],
                                        ("  " + " ".join(flags)) if flags else ""))
            if it["next_step"]:
                L.append("     下一步：%s" % it["next_step"])
    if brief["doing"]["skipped"]:
        L.append("")
        L.append("▶ 计划中的下一步被绕过（%d）" % len(brief["doing"]["skipped"]))
        for it in brief["doing"]["skipped"]:
            L.append("  %s `%s` → %s（%s）尚未开工" % (
                it["title"], it["id"], it["next"], it["next_title"] or "?"))
        L.append("  绕行没问题，但要知道自己在绕。走 `todoctl bench` 看全。")

    L.append("")
    L.append("▶ 自上次恢复以来的变动（%d）" % len(brief["changed"]))
    if not brief["last_resume"]:
        L.append("  （首次恢复，无基线）")
    elif not brief["changed"]:
        L.append("  （无）")
    else:
        if brief["changed_same"]:
            L.append("  ── 本条线（%d）──" % len(brief["changed_same"]))
            for c in brief["changed_same"]:
                L.append("  %s %s %s `%s`" % (
                    c["updated"], GLYPH.get(c["status"], "?"), c["title"], c["id"]))
        if brief["changed_other"]:
            L.append("  ── 旁支（%d 条其他需求线 / %d 个节点，未并入现场）──" % (
                len(brief["changed_other"]),
                sum(len(g["items"]) for g in brief["changed_other"])))
            for g in brief["changed_other"]:
                heads = "、".join(i["title"] for i in g["items"][:2])
                if len(g["items"]) > 2:
                    heads += "…"
                L.append("  %s %s（%d 个节点：%s）" % (g["line"], g["title"], len(g["items"]), heads))
                L.append("     接手：todoctl resume %s" % g["line"])
            L.append("  旁支成果已在真源里，不会丢；只是不混进当前现场。")

    if brief["blocked"]:
        L.append("")
        L.append("▶ 别忘了：未收口的阻塞项（%d）" % len(brief["blocked"]))
        for b in brief["blocked"]:
            L.append("  ⛔ %s %s `%s` ← %s" % (b["path"], b["title"], b["id"], b["because"]))

    L.append("")
    L.append("─" * W)
    L.append(" 做一步记一句： todoctl note %s \"...\"" % n["id"])
    L.append(" 收尾：        done %s → 验证 → close %s --evidence \"...\"" % (n["id"], n["id"]))
    return "\n".join(L)


def render_resume(data: dict, ctx=None) -> str:
    return resume_md(resume_brief(data, ctx))


def doing_overview(data: dict, ctx=None) -> dict:
    """手头全部进行中任务的概览 —— resume 与 bench 共用，避免两处渲染漂移。"""
    ctx = ctx or Ctx(data)
    ix = ctx.ix
    idx, pm, wm = ix.by_id, ctx.pm, ctx.wm
    items = []
    for n in data["nodes"]:
        if n.get("status") != "doing":
            continue
        tgt = n.get("next")
        tgt_node = idx.get(tgt) if tgt else None
        ln = ix.line(n["id"])
        items.append({
            "id": n["id"], "path": pm.get(n["id"], "?"), "title": n.get("title"),
            "line": ln, "line_title": idx.get(ln, {}).get("title", ""),
            "next": tgt,
            "next_title": tgt_node.get("title") if tgt_node else None,
            "next_status": tgt_node.get("status") if tgt_node else None,
            "next_skipped": bool(tgt_node and tgt_node.get("status") in NOT_STARTED),
            "next_dead": bool(tgt_node and tgt_node.get("status") in TERMINAL_STATUSES),
            "next_step": n.get("next_step") or "",
            "last_note": (n.get("notes") or [])[-1] if n.get("notes") else "",
            "stale_days": days_since(n.get("updated")),
            "updated": n.get("updated") or "",
            "warns": wm.get(n["id"], []),
        })
    items.sort(key=lambda x: -(x["stale_days"] or 0))
    by_line = {}
    for it in items:
        by_line.setdefault(it["line"], []).append(it)
    return {
        "items": items,
        "by_line": by_line,
        "n_lines": len(by_line),
        "max_stale": max((i["stale_days"] or 0) for i in items) if items else 0,
        "skipped": [i for i in items if i["next_skipped"]],
    }


def _bench_flags(it) -> list:
    flags = []
    if it["stale_days"] is not None and it["stale_days"] > STALE_DOING_DAYS:
        flags.append("⏳搁置 %d 天" % it["stale_days"])
    if it["next_skipped"]:
        flags.append("↷绕行中")
    if it.get("next_dead"):
        flags.append("⌫计划已失效")
    return flags


def render_bench(data: dict, ctx=None) -> str:
    ctx = ctx or Ctx(data)
    ov = doing_overview(data, ctx)
    idx = ctx.ix.by_id
    L = ["# 台面 · %s" % (data.get("project") or ""), ""]
    L.append("> 由 `todoctl render` 生成，**禁止手改**。真源：`tree.json`")
    L.append("> 生成时间 %s · 手头进行中 %d 项 · 涉及 %d 条需求线 · 最久搁置 %d 天" % (
        now(), len(ov["items"]), ov["n_lines"], ov["max_stale"]))
    L.append("")
    if not ov["items"]:
        L.append("_手头没有进行中的任务。_")
        return "\n".join(L) + "\n"

    L.append("## 手头全部进行中（按需求线分组）")
    L.append("")
    for line, its in sorted(ov["by_line"].items(), key=lambda kv: -len(kv[1])):
        L.append("### %s %s" % (idx.get(line, {}).get("title", "（未知线）"), line or "-"))
        L.append("")
        for it in its:
            flags = _bench_flags(it)
            L.append("- %s %s `%s`%s" % (it["path"], it["title"], it["id"],
                                        ("　" + " ".join(flags)) if flags else ""))
            L.append("  - 下一步（做什么）：%s" % (it["next_step"] or "_未记录_"))
            if it["next"]:
                tail = (" ← 尚未开工" if it["next_skipped"]
                        else (" ← 已失效，请更新或清除" if it.get("next_dead") else ""))
                L.append("  - 计划下一步：%s %s（%s）%s" % (
                    it["next"], it["next_title"] or "?", it["next_status"] or "?", tail))
            if it["last_note"]:
                L.append("  - 最近进度：%s" % it["last_note"])
            L.append("  - 最后更新：%s" % (it["updated"] or "-"))
            for w in it["warns"]:
                L.append("  - ⚠ %s" % w)
        L.append("")

    if ov["skipped"]:
        L.append("## 计划中的下一步被绕过（%d）" % len(ov["skipped"]))
        L.append("")
        for it in ov["skipped"]:
            L.append("- %s `%s` → 计划下一步 %s（%s）尚未开工" % (
                it["title"], it["id"], it["next"], it["next_title"] or "?"))
        L.append("")
        L.append("> 绕行本身没问题，但要知道自己在绕。回到计划：`todoctl resume <那条线的节点>`")
        L.append("")
    return "\n".join(L) + "\n"


def bench_plain(data: dict, ctx=None, ov=None) -> str:
    ctx = ctx or Ctx(data)
    ov = ov if ov is not None else doing_overview(data, ctx)
    idx = ctx.ix.by_id
    W = 72
    L = ["═" * W, " 台面 · %s" % (data.get("project") or "")]
    L.append(" 手头进行中 %d 项 · 涉及 %d 条需求线 · 最久搁置 %d 天" % (
        len(ov["items"]), ov["n_lines"], ov["max_stale"]))
    L.append("═" * W)
    if not ov["items"]:
        L.append(" 手头没有进行中的任务。")
        return "\n".join(L)
    for line, its in sorted(ov["by_line"].items(), key=lambda kv: -len(kv[1])):
        L.append("")
        L.append("▍%s  %s" % (idx.get(line, {}).get("title", "（未知线）"), line or "-"))
        for it in its:
            flags = _bench_flags(it)
            L.append("  %s %s `%s`%s" % (it["path"], it["title"], it["id"],
                                        ("  " + " ".join(flags)) if flags else ""))
            L.append("     下一步     %s" % (it["next_step"] or "未记录"))
            if it["next"]:
                tail = (" ← 尚未开工" if it["next_skipped"]
                        else (" ← 已失效，请更新或清除" if it.get("next_dead") else ""))
                L.append("     计划下一步 %s（%s）%s" % (
                    it["next"], it["next_status"] or "?", tail))
            if it["last_note"]:
                L.append("     最近进度   %s" % it["last_note"])
            L.append("     最后更新   %s" % (it["updated"] or "-"))
    if ov["skipped"]:
        L.append("")
        L.append("▶ 计划中的下一步被绕过（%d）" % len(ov["skipped"]))
        for it in ov["skipped"]:
            L.append("  %s `%s` → %s（%s）尚未开工" % (
                it["title"], it["id"], it["next"], it["next_title"] or "?"))
        L.append("  回到计划：todoctl resume <那条线的节点>")
    L.append("")
    L.append("─" * W)
    L.append(" 做一步记一句： todoctl note <ID> \"...\"")
    return "\n".join(L)


def cmd_bench(a, data, root):
    ctx = Ctx(data)
    ov = doing_overview(data, ctx)
    print(bench_plain(data, ctx, ov))
    if not ov["items"]:
        print("")
        print("提示：用 `todoctl start <ID>` 把某条需求推进到 doing，它就会出现在这里。")
    return 0


def baselines_path(root: Path) -> Path:
    return root / "baselines.json"


def read_baselines(root: Path, strict: bool = False) -> dict:
    """读基线列表。

    `strict=False`（渲染用）：解析失败按空处理，视图照常生成，不让脏文件阻断展示。
    `strict=True`（**写入前**用）：解析失败直接拒绝 —— 详见 cmd_baseline。
    """
    p = baselines_path(root)
    if not p.exists():
        return {"version": 1, "items": []}

    def bad(why):
        if not strict:
            return {"version": 1, "items": []}
        raise Fail(
            "baselines.json 无法解析（%s），**拒绝继续**。\n"
            "  它是追加式真源，此时按空处理再写回会**静默抹掉全部历史基线**。\n"
            "  处理：先人工修复，或从 snapshots/baselines-last.json 取回上一版，再重试。\n"
            "  文件：%s" % (why, p))

    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except ValueError as exc:
        return bad(str(exc))
    if not isinstance(d, dict):
        return bad("顶层不是对象")
    if not isinstance(d.get("items"), list):
        return bad("items 不是数组")
    d.setdefault("version", 1)
    return d


def write_baselines(root: Path, d: dict) -> None:
    p = baselines_path(root)
    # 覆盖前把上一版另存到 snapshots/ —— 与 tree.json 的 last.json 同一思路，滚动一份、不增长
    if p.exists():
        try:
            sd = snapshot_dir(root)
            sd.mkdir(parents=True, exist_ok=True)
            (sd / "baselines-last.json").write_text(
                p.read_text(encoding="utf-8"), encoding="utf-8")
        except Exception as exc:
            print("  ⚠ 基线备份失败（基线仍会正常写入）：%s" % exc, file=sys.stderr)
    tmp = p.with_name("baselines.json.tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, p)


def aggregate_by_line(data: dict, ctx=None) -> list:
    """按需求线聚合进度 —— 基线摘要与 BASELINE.md 共用。"""
    ctx = ctx or Ctx(data)
    ix = ctx.ix
    idx, pm = ix.by_id, ctx.pm
    lines = {}
    for n in data["nodes"]:
        ln = ix.line(n["id"])
        g = lines.setdefault(ln, {"id": ln, "title": idx.get(ln, {}).get("title", "（未知线）"),
                                  "path": pm.get(ln, "?"), "total": 0, "closed": 0,
                                  "doing": 0, "blocked": 0, "dropped": 0, "unclosed": 0})
        g["total"] += 1
        st = n.get("status")
        if st == "closed":
            g["closed"] += 1
        elif st == "dropped":
            g["dropped"] += 1
        else:
            g["unclosed"] += 1
            if st == "doing":
                g["doing"] += 1
            elif st == "blocked":
                g["blocked"] += 1
    out = []
    for g in lines.values():
        denom = g["total"] - g["dropped"]
        g["progress"] = "%d/%d" % (g["closed"], denom) if denom else "-"
        g["pct"] = int(round(100.0 * g["closed"] / denom)) if denom else 0
        out.append(g)
    out.sort(key=lambda x: (-x["total"], x["id"] or ""))
    return out


def next_baseline_id(existing) -> str:
    """生成**唯一**基线 ID。

    精确到秒的 `B-YYYYMMDD-HHMMSS` 在同秒内连续封板会撞车（实测 3 次封板得到同一个 ID），
    而基线 ID 是「把所有成果合并成一个」的**唯一可引用句柄** —— 撞车后
    `--list` 分不出彼此、`--diff` 会静默取到最早那条，给出错误的比对基准。
    此处撞车则追加 `-02`、`-03`…，并把**历史遗留的重复 ID** 一并计入占用集合。
    """
    stem = "B-" + datetime.now().strftime("%Y%m%d-%H%M%S")
    used = {b.get("id") for b in (existing or [])}
    if stem not in used:
        return stem
    i = 2
    while "%s-%02d" % (stem, i) in used:
        i += 1
    return "%s-%02d" % (stem, i)


def baseline_stats(data: dict, name: str, note: str, prev: str, bid=None, ctx=None) -> dict:
    ctx = ctx or Ctx(data)
    st = ctx.stats
    return {
        "id": bid or ("B-" + datetime.now().strftime("%Y%m%d-%H%M%S")),
        "name": name or "",
        "note": note or "",
        "created": now_sec(),
        "prev": prev,
        "stats": {"total": st["total"], "closed": st["n_closed"], "unclosed": st["n_unclosed"],
                  "doing": st["n_doing"], "blocked": st["n_blocked"], "max_depth": st["max_depth"]},
        "lines": aggregate_by_line(data, ctx),
        "focus": data.get("focus"),
        "doing": [{"id": i["id"], "path": i["path"], "title": i["title"]}
                  for i in doing_overview(data, ctx)["items"]],
        "journal_upto": now_sec(),
    }


def render_baseline_md(data: dict, bl: dict, ctx=None) -> str:
    items = bl.get("items") or []
    L = ["# 基线 · %s" % (data.get("project") or ""), ""]
    L.append("> 由 `todoctl render` 生成，**禁止手改**。真源：`baselines.json` + `tree.json`")
    L.append("> 生成时间 %s · 已有基线 %d 个" % (now(), len(items)))
    L.append("")
    if not items:
        L.append("_尚未封板。用 `todoctl baseline --name \"v0.3 首版\"` 把当前各线成果封成一个基线。_")
        return "\n".join(L) + "\n"

    last = items[-1]
    s = last.get("stats", {})
    L.append("## 最新基线 `%s`" % last["id"])
    L.append("")
    if last.get("name"):
        L.append("- 名称：**%s**" % last["name"])
    L.append("- 封板于：%s" % (last.get("created") or "-"))
    if last.get("prev"):
        L.append("- 上一基线：`%s`（`todoctl baseline --diff` 可比对）" % last["prev"])
    if last.get("note"):
        L.append("- 说明：%s" % last["note"])
    L.append("- 规模：节点 %d · 已收口 %d · 未收口 %d · 进行中 %d · 阻塞 %d" % (
        s.get("total", 0), s.get("closed", 0), s.get("unclosed", 0),
        s.get("doing", 0), s.get("blocked", 0)))
    L.append("")
    L.append("### 各需求线进度")
    L.append("")
    L.append("| 线 | 需求 | 进度 | 未收口 | 进行中 | 阻塞 |")
    L.append("|---|---|---|---|---|---|")
    for g in last.get("lines", []):
        L.append("| `%s` | %s | %s（%d%%） | %d | %d | %d |" % (
            g["id"] or "-", g["title"], g["progress"], g["pct"],
            g["unclosed"], g["doing"], g["blocked"]))
    L.append("")
    if last.get("doing"):
        L.append("### 封板时正在进行（%d）" % len(last["doing"]))
        L.append("")
        for i in last["doing"]:
            L.append("- %s %s `%s`" % (i["path"], i["title"], i["id"]))
        L.append("")
    L.append("### 基线历史")
    L.append("")
    L.append("| 基线 | 名称 | 时间 | 节点 | 已收口 | 未收口 |")
    L.append("|---|---|---|---|---|---|")
    for b in reversed(items):
        bs = b.get("stats", {})
        L.append("| `%s` | %s | %s | %d | %d | %d |" % (
            b["id"], b.get("name") or "-", b.get("created") or "-",
            bs.get("total", 0), bs.get("closed", 0), bs.get("unclosed", 0)))
    L.append("")
    return "\n".join(L) + "\n"


def baseline_diff_lines(prev: dict, cur: dict) -> list:
    L = ["对比：`%s` → `%s`" % (prev.get("id"), cur.get("id") or "（当前）"), ""]
    ps, cs = prev.get("stats", {}), cur.get("stats", {})
    for k, label in (("total", "节点"), ("closed", "已收口"), ("unclosed", "未收口"),
                     ("doing", "进行中"), ("blocked", "阻塞")):
        d = cs.get(k, 0) - ps.get(k, 0)
        L.append("- %s：%d → %d（%s%d）" % (label, ps.get(k, 0), cs.get(k, 0),
                                          "+" if d >= 0 else "", d))
    L.append("")
    pl = {x["id"]: x for x in prev.get("lines", [])}
    cl = {x["id"]: x for x in cur.get("lines", [])}
    new_lines = sorted([i for i in cl if i not in pl], key=lambda x: x or "")
    if new_lines:
        L.append("**新增需求线**")
        for i in new_lines:
            L.append("- `%s` %s" % (i or "-", cl[i]["title"]))
        L.append("")
    moved = [i for i in cl if i in pl and pl[i]["progress"] != cl[i]["progress"]]
    if moved:
        L.append("**各线进度变化**")
        for i in moved:
            L.append("- `%s` %s：%s → %s" % (i or "-", cl[i]["title"],
                                           pl[i]["progress"], cl[i]["progress"]))
        L.append("")
    if not new_lines and not moved:
        L.append("_各线进度无变化（只有条目增减）。_")
        L.append("")
    return L


def dashboard_payload(data: dict, ctx=None) -> dict:
    ctx = ctx or Ctx(data)
    ix = ctx.ix
    pm, wm = ctx.pm, ctx.wm
    nodes = []
    for n in data["nodes"]:
        nodes.append({
            "id": n["id"],
            "parent": n.get("parent") or "",
            "title": n.get("title") or "",
            "why": n.get("why") or "",
            "status": n.get("status"),
            "kind": n.get("kind"),
            "kindCn": KIND_CN.get(n.get("kind"), n.get("kind") or ""),
            "path": pm.get(n["id"], ""),
            "depth": ix.depth(n["id"]),
            "doneWhen": n.get("done_when") or "",
            "evidence": n.get("close_evidence") or [],
            "blockedBy": n.get("blocked_by") or "",
            "dependsOn": n.get("depends_on") or [],
            "created": n.get("created") or "",
            "updated": n.get("updated") or "",
            "closedAt": n.get("closed_at") or "",
            "warns": wm.get(n["id"], []),
        })
    st = ctx.stats
    return {
        "project": data.get("project") or "",
        "generated": now(),
        "focus": data.get("focus") or "",
        "stats": st,
        "nodes": nodes,
    }


def render_dashboard(data: dict, ctx=None) -> str:
    payload = json.dumps(dashboard_payload(data, ctx), ensure_ascii=False).replace("<", "\\u003c")
    return DASHBOARD_HTML.replace("__PAYLOAD__", payload)


# ------------------------------------------------------------------ 命令实现

def need_node(data, nid):
    idx = by_id(data)
    if nid not in idx:
        raise Fail("节点不存在：%s" % nid)
    return idx[nid]


def touch(node):
    node["updated"] = now_sec()


def cmd_init(a, data, root):
    data["project"] = a.project or data.get("project") or root.parent.name
    save(root, data)
    # 落点来源必须来自**同一次判定**（main 已算好并挂在 a 上），不能在这里重新猜。
    origin = getattr(a, "root_origin", None) or resolve_root_info(getattr(a, "root", None))[1]
    print("真源已就绪：%s" % store_path(root))
    print("  项目：%s" % data["project"])
    print("  根定位方式：%s" % origin)
    print("  当前目录：%s" % Path.cwd())
    print("  真源目录：%s" % root)
    if root != Path.cwd() and "当前目录" not in origin:
        print("  ⚠ 真源**不在**当前目录下 —— 确认这是你要的位置；要改就用 --root 显式指定。")
    return 0


def cmd_add(a, data, root):
    global _LAST_NEW_ID
    if not (a.title or "").strip():
        raise Fail("--title 必填")
    if a.kind not in KINDS:
        raise Fail("--kind 只能是：%s" % ", ".join(KINDS))
    if a.parent:
        need_node(data, a.parent)
    for dep in (a.depends_on or []):
        need_node(data, dep)

    nid = "R-%04d" % int(data["next_seq"])
    while nid in by_id(data):
        data["next_seq"] = int(data["next_seq"]) + 1
        nid = "R-%04d" % int(data["next_seq"])
    data["next_seq"] = int(data["next_seq"]) + 1

    data["nodes"].append({
        "id": nid,
        "parent": a.parent or None,
        "kind": a.kind,
        "title": a.title.strip(),
        "why": (a.why or "").strip(),
        "done_when": (a.done_when or "").strip(),
        "status": "open",
        "created": today(),
        "updated": now_sec(),
        "closed_at": None,
        "close_evidence": [],
        "close_override": None,
        "close_override_ids": [],
        "blocked_by": None,
        "next_step": None,
        "next": None,
        "depends_on": list(a.depends_on or []),
        # `refs` 已废弃：进行中的引用用 `note`（免引号、带时间戳、追加式），
        # 收口证据用 `close_evidence`（强校验 + 视图展示）。该字段没有消费者，
        # 所以不再写入 —— 先有消费者，再埋字段。
        "internal": bool(getattr(a, "internal", False)),
        "notes": [],
    })
    _LAST_NEW_ID = nid
    save(root, data)
    pm = path_map(data)
    print("✔ 新增 %s  %s %s" % (nid, pm.get(nid, ""), a.title.strip()))
    if a.parent:
        print("  派生自 %s（%s）" % (a.parent, by_id(data)[a.parent].get("title")))
    print("  触发理由：%s" % (a.why or "（未填 —— 追溯链会断，建议补上）"))
    if not (a.done_when or "").strip():
        print("  验收条件：未填 → 该节点不能进入 doing（强校验）")
    report_issues(*validate(data))
    return 0


def cmd_start(a, data, root):
    n = need_node(data, a.id)
    if n["status"] not in ("open", "blocked"):
        raise Fail("%s 当前状态 %s，不能进入 doing" % (n["id"], n["status"]))
    if not (n.get("done_when") or "").strip():
        raise Fail("强校验拒绝：%s 没有 done_when。没有验收条件的待办不可推进，也不能收口。" % n["id"])
    idx = by_id(data)
    unmet = [d for d in (n.get("depends_on") or []) if idx.get(d, {}).get("status") != "closed"]
    if unmet and not a.force:
        raise Fail("强校验拒绝：依赖未收口 %s。确认可并行请加 --force。" % ", ".join(unmet))
    anc = terminal_ancestor(data, n["id"])
    if anc and not a.force:
        raise Fail(
            "强校验拒绝：祖先 %s（%s）已收口/已放弃，不能在它下面推进。\n"
            "  在已收口的祖先下开工，等于正在制造一个假收口。三条出路：\n"
            "    ① 若这项工作不该属于它 → 改建为自己的节点（挂到活跃祖先下，或不挂父节点）\n"
            "    ② 若它就是本该继续 → 说明父项收早了，先用 `update %s` 修正父项验收条件\n"
            "    ③ 确认可接受 → 加 --force（会在 check 里留下 W7 告警，不会静默）" % (
                anc, idx[anc].get("status"), anc))
    n["status"] = "doing"
    n["blocked_by"] = None
    touch(n)
    save(root, data)
    print("▶ %s 进入 doing" % n["id"])
    if not (n.get("next_step") or "").strip():
        print("  提示：还没记「下一步」—— `todoctl update %s --next-step \"...\"`。" % n["id"])
        print("        下次恢复时它会直接告诉你从哪继续。")
    # 绕行提示：别的进行中任务，其计划下一步还没开工 —— 提示但不阻断（绕行是常态）
    idx2 = by_id(data)
    pm2 = path_map(data)
    for pnode in data["nodes"]:
        tgt = pnode.get("next")
        if (pnode.get("status") == "doing" and pnode["id"] != n["id"]
                and tgt and tgt in idx2 and tgt != n["id"]
                and idx2[tgt].get("status") in NOT_STARTED):
            print("  绕行提示：%s %s 计划中的下一步是 %s（%s），它尚未开工。" % (
                pm2.get(pnode["id"], "?"), pnode["title"], tgt, idx2[tgt].get("title")))
            print("            回来时：todoctl resume %s" % pnode["id"])
    report_issues(*validate(data))
    return 0


def cmd_block(a, data, root):
    n = need_node(data, a.id)
    if not (a.reason or "").strip():
        raise Fail("强校验拒绝：--reason 必填（阻塞必须记录原因，否则无法追溯）")
    if n["status"] in TERMINAL_STATUSES:
        raise Fail("%s 已收口，不能再标记阻塞" % n["id"])
    n["status"] = "blocked"
    n["blocked_by"] = a.reason.strip()
    touch(n)
    save(root, data)
    print("⛔ %s 标记阻塞：%s" % (n["id"], n["blocked_by"]))
    report_issues(*validate(data))
    return 0


def cmd_unblock(a, data, root):
    n = need_node(data, a.id)
    if n["status"] != "blocked":
        raise Fail("%s 当前不是 blocked" % n["id"])
    if not (n.get("done_when") or "").strip():
        raise Fail("强校验拒绝：%s 没有 done_when，不能恢复推进" % n["id"])
    anc = terminal_ancestor(data, n["id"])
    if anc and not a.force:
        raise Fail("强校验拒绝：祖先 %s（%s）已收口/已放弃，不能在它下面推进。"
                   "确认可接受请加 --force（会留下 W7 告警）。" % (anc, by_id(data)[anc].get("status")))
    n["status"] = "doing"
    n["blocked_by"] = None
    touch(n)
    save(root, data)
    print("▶ %s 解除阻塞，回到 doing" % n["id"])
    report_issues(*validate(data))
    return 0


def cmd_done(a, data, root):
    n = need_node(data, a.id)
    if n["status"] != "doing":
        raise Fail("%s 当前状态 %s，只有 doing 才能标记 done" % (n["id"], n["status"]))
    if a.note:
        n["notes"].append("%s  %s" % (now_sec(), a.note))
    n["status"] = "done"
    touch(n)
    save(root, data)
    print("✔ %s 已完成实现（done）—— 注意：done ≠ 收口，验证通过后再 close" % n["id"])
    report_issues(*validate(data))
    return 0


def cmd_close(a, data, root):
    n = need_node(data, a.id)
    if n.get("status") == "closed":
        raise Fail("%s 已收口" % n["id"])
    if n["status"] != "done" and not a.force:
        raise Fail("强校验拒绝：只有 done 才能收口（当前 %s）。确认直接收口请加 --force。" % n["status"])
    ev = [e for e in (a.evidence or []) if e.strip()]
    if not ev:
        raise Fail("强校验拒绝：收口必须附证据。示例 --evidence \"commit:abc123\" / --evidence \"test:test_x\" / --evidence \"user:alice 确认\"")
    if not (n.get("done_when") or "").strip():
        raise Fail("强校验拒绝：%s 没有 done_when，无法判定是否达成" % n["id"])

    unclosed = [d["id"] for d in descendants(data, n["id"]) if d.get("status") not in TERMINAL_STATUSES]
    if unclosed and not (a.override_reason or "").strip():
        raise Fail(
            "强校验拒绝（假收口）：%s 仍有 %d 个未收口后代：%s\n"
            "  要么先收口这些后代，要么显式确认并记录理由：\n"
            "    todoctl close %s --evidence \"...\" --override-reason \"理由\"" % (
                n["id"], len(unclosed), ", ".join(unclosed), n["id"]))

    if unclosed:
        n["close_override"] = a.override_reason.strip()
        n["close_override_ids"] = sorted(unclosed)      # 钉住"本次显式接受的那批后代"
        n["notes"].append("%s  收口时仍有未收口后代 %s，理由：%s" % (
            now_sec(), ", ".join(unclosed), a.override_reason.strip()))

    n["status"] = "closed"
    n["closed_at"] = today()
    n["close_evidence"] = ev
    touch(n)
    save(root, data)
    print("✅ %s 已收口（%s）" % (n["id"], n.get("title")))
    for e in ev:
        print("   证据：%s" % e)
    if n.get("close_override"):
        print("   ⚠ 带 override 收口：%s" % n["close_override"])
    print("   回写检查：请确认它是否使某个祖先的验收条件失效 —— 若失效，用 `todoctl update <祖先>` 修正")
    report_issues(*validate(data))
    return 0


def cmd_drop(a, data, root):
    n = need_node(data, a.id)
    if not (a.reason or "").strip():
        raise Fail("强校验拒绝：--reason 必填（放弃必须记录理由）")
    if n["status"] in TERMINAL_STATUSES:
        raise Fail("%s 已收口/已放弃" % n["id"])
    unclosed = [d["id"] for d in descendants(data, n["id"]) if d.get("status") not in TERMINAL_STATUSES]
    if unclosed and not a.force:
        raise Fail("强校验拒绝：%s 仍有 %d 个未收口后代（%s）。级联放弃请加 --force。" % (
            n["id"], len(unclosed), ", ".join(unclosed)))
    n["status"] = "dropped"
    n["blocked_by"] = None
    n["notes"].append("%s  放弃：%s" % (now_sec(), a.reason.strip()))
    touch(n)
    save(root, data)
    print("⏹ %s 已放弃：%s" % (n["id"], a.reason.strip()))
    report_issues(*validate(data))
    return 0


def cmd_update(a, data, root):
    n = need_node(data, a.id)
    changed = []
    for field, val in (("title", a.title), ("why", a.why), ("done_when", a.done_when),
                       ("next_step", a.next_step)):
        if val is not None:
            if field == "title" and not val.strip():
                raise Fail("title 不能为空")
            n[field] = val.strip()
            changed.append(field)
    if a.next is not None:
        val = a.next.strip()
        if val:
            if val == n["id"]:
                raise Fail("next 不能指向自身")
            need_node(data, val)
            n["next"] = val
        else:
            n["next"] = None          # 传空串 = 清除计划下一步
        changed.append("next")
    if a.kind:
        if a.kind not in KINDS:
            raise Fail("--kind 只能是：%s" % ", ".join(KINDS))
        n["kind"] = a.kind
        changed.append("kind")
    if a.depends_on is not None:
        for dep in a.depends_on:
            need_node(data, dep)
        n["depends_on"] = list(a.depends_on)
        changed.append("depends_on")
    if getattr(a, "internal", False) or getattr(a, "external", False):
        n["internal"] = bool(getattr(a, "internal", False))
        changed.append("internal")
    if not changed:
        raise Fail("没有需要更新的字段")
    touch(n)
    save(root, data)
    print("✎ %s 已更新：%s" % (n["id"], ", ".join(changed)))
    report_issues(*validate(data))
    return 0


def cmd_note(a, data, root):
    """给节点追加一条进度记录 —— 跨次场景恢复的关键素材。"""
    n = need_node(data, a.id)
    text = " ".join(a.text).strip()
    if not text:
        raise Fail("note 内容不能为空")
    n["notes"].append("%s  %s" % (now_sec(), text))
    if a.next_step is not None:
        n["next_step"] = a.next_step.strip()
    if a.next is not None:
        val = a.next.strip()
        if val:
            if val == n["id"]:
                raise Fail("next 不能指向自身")
            need_node(data, val)
            n["next"] = val
        else:
            n["next"] = None
    touch(n)
    save(root, data)
    print("✎ %s 记下：%s" % (n["id"], text))
    if a.next_step is not None:
        print("  下一步（做什么）：%s" % (n["next_step"] or "（已清空）"))
    if a.next is not None:
        print("  计划下一步（哪个节点）：%s" % (n["next"] or "（已清空）"))
    return 0


def cmd_resume(a, data, root):
    """输出恢复简报并把「现在」记为本次恢复点（锚点前移）。"""
    if a.id:
        need_node(data, a.id)
        data["focus"] = a.id          # 只为让下面的简报指向它，尚未落盘
    nid, _ = resume_target(data)
    if not nid:
        print("真源里还没有节点。先建第一条：")
        print("  todoctl add --title \"...\" --why \"...\" --done-when \"...\"")
        return 0
    brief = resume_brief(data)        # 必须在锚点前移之前算，否则变动清单会被清空
    print(resume_plain(brief))
    if not data.get("focus"):
        data["focus"] = nid
    data["last_resume"] = now_sec()
    data["resume_count"] = int(data.get("resume_count") or 0) + 1
    save(root, data)
    print("")
    print("本次恢复点已记录（第 %d 次，%s）。此后发生的变动会累积到 RESUME.md。" % (
        data["resume_count"], data["last_resume"]))
    if not brief["node"]["next_step"]:
        print("⚠ %s 还没记「下一步」。补一句，下次恢复会直接告诉你从哪继续：" % nid)
        print("    todoctl update %s --next-step \"...\"" % nid)
    return 0


def cmd_focus(a, data, root):
    if a.clear:
        data["focus"] = None
        save(root, data)
        print("已清除聚焦")
        return 0
    n = need_node(data, a.id)
    data["focus"] = n["id"]
    save(root, data)
    pm = path_map(data)
    print("★ 聚焦 %s %s %s" % (n["id"], pm.get(n["id"], ""), n.get("title")))
    if not (n.get("done_when") or "").strip():
        print("  ⚠ 它还没有 done_when，不能进入 doing")
    return 0


def cmd_show(a, data, root):
    n = need_node(data, a.id)
    pm, wm = path_map(data), warn_map(data)
    idx = by_id(data)
    print("%s  %s  [%s / %s]" % (pm.get(n["id"], "?"), n.get("title"), n.get("status"), n.get("kind")))
    print("  id        %s" % n["id"])
    print("  派生自    %s%s" % (n.get("parent") or "（根）",
                            ("（%s）" % idx[n["parent"]].get("title")) if n.get("parent") in idx else ""))
    print("  触发理由  %s" % (n.get("why") or "（未填）"))
    print("  收口条件  %s" % (n.get("done_when") or "（未填）"))
    print("  创建/更新 %s / %s" % (n.get("created"), n.get("updated")))
    if n.get("closed_at"):
        print("  收口于    %s" % n["closed_at"])
        for e in (n.get("close_evidence") or []):
            print("    证据    %s" % e)
    if n.get("blocked_by"):
        print("  阻塞于    %s" % n["blocked_by"])
    if n.get("depends_on"):
        print("  依赖于    %s" % ", ".join(
            "%s(%s)" % (d, idx.get(d, {}).get("status", "?")) for d in n["depends_on"]))
    if n.get("internal"):
        print("  内部节点  是（export 时连同整条子树剔除）")
    kids = children_of(data, n["id"])
    if kids:
        print("  派生（%d）" % len(kids))
        for k in kids:
            print("    %s %s %s" % (pm.get(k["id"], "?"), GLYPH.get(k.get("status"), "?"), k.get("title")))
    if n["id"] in wm:
        for m in wm[n["id"]]:
            print("  ⚠ %s" % m)
    return 0


def cmd_check(a, data, root):
    ix = Tree(data)
    errors, warns = validate(data, ix)
    print("真源：%s" % store_path(root))
    _hint = legacy_hint(root)
    if _hint:
        print(_hint)
    st = summarize(data, ix)
    print("节点 %d · 已收口 %d · 未收口 %d · 阻塞 %d · 最大深度 %d" % (
        st["total"], st["n_closed"], st["n_unclosed"], st["n_blocked"], st["max_depth"]))
    print("")
    if not errors and not warns:
        print("✅ 校验通过，无错误无告警")
        return 0
    return report_issues(errors, warns)


def cmd_report(a, data, root):
    since = datetime.now() - timedelta(days=a.days)
    pm = path_map(data)
    new, done_s, closed_s, blocked_s, dropped_s = [], [], [], [], []
    for n in data["nodes"]:
        c = parse_dt(n.get("created"))
        if c and c >= since and n.get("status") == "open":
            new.append(n)
        u = parse_dt(n.get("updated"))
        if u and u >= since:
            if n.get("status") == "done":
                done_s.append(n)
            elif n.get("status") == "closed":
                closed_s.append(n)
            elif n.get("status") == "blocked":
                blocked_s.append(n)
            elif n.get("status") == "dropped":
                dropped_s.append(n)
    st = summarize(data)
    print("变更摘要（近 %d 天）· %s" % (a.days, data.get("project") or ""))
    print("")
    print("新增未推进  %d" % len(new))
    for n in new:
        print("  %s %s %s" % (pm.get(n["id"], "?"), n["id"], n.get("title")))
    print("完成实现    %d" % len(done_s))
    for n in done_s:
        print("  %s %s %s" % (pm.get(n["id"], "?"), n["id"], n.get("title")))
    print("已收口      %d" % len(closed_s))
    for n in closed_s:
        print("  %s %s %s" % (pm.get(n["id"], "?"), n["id"], n.get("title")))
    print("新增阻塞    %d" % len(blocked_s))
    for n in blocked_s:
        print("  %s %s %s ← %s" % (pm.get(n["id"], "?"), n["id"], n.get("title"), n.get("blocked_by")))
    print("已放弃      %d" % len(dropped_s))
    print("")
    print("存量：未收口 %d · 阻塞 %d · 最大深度 %d" % (st["n_unclosed"], st["n_blocked"], st["max_depth"]))
    errors, warns = validate(data)
    if errors or warns:
        print("")
        print("校验：%d 错误 / %d 告警（跑 todoctl check 看详情）" % (len(errors), len(warns)))
    return 0


def cmd_log(a, data, root):
    """按需求线抽取变更叙述 —— 回答「这次提交是否只包含本条线」，并生成提交信息素材。"""
    recs = read_journal(root)
    if not recs:
        print("还没有变更日志（journal.jsonl）。本版起每次写入都会记录一行。")
        return 0
    idx, pm = by_id(data), path_map(data)
    if a.line:
        if a.line not in idx:
            raise Fail("线不存在：%s" % a.line)
        want = line_of(data, a.line)
        recs = [r for r in recs if r.get("line") == want]
    if a.since:
        s = parse_dt(a.since)
        if s is None:
            raise Fail("--since 无法解析：%s（试 '2026-09-22' 或 '2026-09-22 13:00'）" % a.since)
        recs = [r for r in recs if (parse_dt(r.get("ts")) or datetime.min) >= s]
    if a.limit is not None:
        # `recs[-0:]` 等价于 `recs[0:]`＝**全部** —— 这是个静默的边界陷阱。
        # 语义定为「最后 N 条；N ≤ 0 视为 0 条」。
        n_lim = max(0, int(a.limit))
        recs = recs[-n_lim:] if n_lim else []
    if not recs:
        print("（没有匹配的变更记录）")
        return 0

    groups = {}
    for r in recs:
        groups.setdefault(r.get("line"), []).append(r)
    groups = sorted(groups.items(), key=lambda kv: -len(kv[1]))

    if a.commit_msg:
        print("# 提交信息草稿（由 journal.jsonl 抽取，共 %d 条记录 / %d 条线）" % (len(recs), len(groups)))
        print("")
        for line, rs in groups:
            title = "（全局操作）" if line is None else idx.get(line, {}).get("title", "（线已不存在）")
            print("- %s（%s）" % (title, line or "-"))
            for r in rs:
                dt = delta_text(r)
                print("    %s  %s  %s%s" % (r["ts"][:16], r["op"],
                                            r.get("detail") or r.get("id") or "",
                                            ("   " + dt) if dt else ""))
        print("")
        print("# 只想提交一条线时：加 --line <线ID> 收窄，再用这段写 message。")
        return 0

    print("变更日志 · %d 条记录 · 按需求线分组" % len(recs))
    for line, rs in groups:
        title = "（全局操作）" if line is None else idx.get(line, {}).get("title", "（线已不存在）")
        print("")
        print("▍%s %s `%s`  共 %d 条" % (
            (pm.get(line, "-") if line else "-"), title, line or "-", len(rs)))
        for r in rs:
            dt = delta_text(r)
            print("   %s  %-7s %-8s %s" % (
                r["ts"], r["op"], r["id"] or "-",
                (r.get("detail") or "") + ("   " + dt if dt else "")))
    return 0


def cmd_baseline(a, data, root):
    """把当前各线成果封成一个**不可变**基线 —— 「把所有成果合并成一个」的显式动作。"""
    bl = read_baselines(root, strict=True)     # 解析不了就拒绝，绝不按空处理再覆盖
    items = bl["items"]

    if a.list_baselines:
        if not items:
            print("还没有任何基线。用 `todoctl baseline --name \"...\"` 封第一个。")
            return 0
        print("基线列表（共 %d 个）" % len(items))
        print("")
        print("| 基线 | 名称 | 时间 | 节点 | 已收口 | 未收口 | 进行中 | 阻塞 |")
        print("|---|---|---|---|---|---|---|---|")
        for b in items:
            s = b.get("stats", {})
            print("| `%s` | %s | %s | %d | %d | %d | %d | %d |" % (
                b["id"], b.get("name") or "-", b.get("created") or "-",
                s.get("total", 0), s.get("closed", 0), s.get("unclosed", 0),
                s.get("doing", 0), s.get("blocked", 0)))
        dups = sorted({b["id"] for b in items if [x["id"] for x in items].count(b["id"]) > 1})
        if dups:
            print("")
            print("⚠ 发现 %d 个**重复的基线 ID**（%s）：这是旧版按秒生成 ID 的遗留数据。" % (
                len(dups), ", ".join("`%s`" % d for d in dups)))
            print("  `--diff <ID>` 对重复 ID 只能命中其中一条。此后新封的基线已保证唯一，")
            print("  要严格区分历史基线，可对照本表的「时间 / 节点数」列判断。")
        return 0

    if a.diff is not None:
        if not items:
            print("还没有任何基线，无法比对。")
            return 0
        target = None
        if a.diff:
            # 取**最后一个**匹配项：历史遗留的重复 ID 场景下，用户要的通常是最近那条
            matches = [b for b in items if b["id"] == a.diff]
            target = matches[-1] if matches else None
            if target is None:
                raise Fail("找不到基线：%s（用 `todoctl baseline --list` 查看）" % a.diff)
        else:
            target = items[-2] if len(items) >= 2 else items[-1]
        for ln in baseline_diff_lines(target, baseline_stats(data, "（当前）", "", target["id"])):
            print(ln)
        return 0

    errors, _ = validate(data)
    if errors:
        print("真源存在 %d 个错误 —— 基线必须是干净的时点，先修再封板：" % len(errors))
        for e in errors[:8]:
            print("  ✖ %s %s [%s]" % (e["code"], e.get("id") or "-", e["msg"]))
        if len(errors) > 8:
            print("  … 其余见 `todoctl check`")
        return 1

    ctx = Ctx(data)
    prev_obj = items[-1] if items else None
    prev = prev_obj["id"] if prev_obj else None
    b = baseline_stats(data, a.name, a.note, prev, next_baseline_id(items), ctx)
    items.append(b)
    bl["items"] = items
    write_baselines(root, bl)

    s = b["stats"]
    print("✔ 已封板 `%s`%s" % (b["id"], ("  " + b["name"]) if b["name"] else ""))
    print("  规模：节点 %d · 已收口 %d · 未收口 %d · 进行中 %d · 阻塞 %d" % (
        s["total"], s["closed"], s["unclosed"], s["doing"], s["blocked"]))
    print("")
    print("  覆盖的需求线（%d）：" % len(b["lines"]))
    for g in b["lines"]:
        flags = []
        if g["doing"]:
            flags.append("%d 进行中" % g["doing"])
        if g["blocked"]:
            flags.append("%d 阻塞" % g["blocked"])
        print("    `%s` %-26s %s（%d%%）%s" % (
            g["id"] or "-", g["title"], g["progress"], g["pct"],
            ("  " + " · ".join(flags)) if flags else ""))
    if prev_obj:
        print("")
        for ln in baseline_diff_lines(prev_obj, b):
            print("  " + ln)
    print("")
    print("  基线不可变。派生物 BASELINE.md 已同步；比对用 `todoctl baseline --diff`。")
    try:
        write_views(root, data, root)
    except Exception as exc:
        print("  ⚠ 派生物刷新失败（基线已保存）：%s" % exc, file=sys.stderr)
    return 0


def id_high_water(root: Path, data: dict) -> int:
    """ID 分配水位（= 下一个可用序号）。**ID 永不复用，回滚也不破例。**

    只扫节点表是不够的：被回滚掉的节点已经不在表里，但它用过的 ID
    仍然出现在 journal / baselines 里，必须一并计入，否则同一个 ID 会指过两个不同节点。
    """
    hi = 0
    for n in (data.get("nodes") or []):
        m = re.match(r"^R-(\d+)$", str(n.get("id") or ""))
        if m:
            hi = max(hi, int(m.group(1)))
    for r in read_journal(root):
        for cand in [r.get("id")] + list((r.get("delta") or {}).keys()):
            m = re.match(r"^R-(\d+)$", str(cand or ""))
            if m:
                hi = max(hi, int(m.group(1)))
    for b in (read_baselines(root).get("items") or []):
        for ln in (b.get("lines") or []):
            m = re.match(r"^R-(\d+)$", str(ln.get("id") or ""))
            if m:
                hi = max(hi, int(m.group(1)))
    return hi + 1


def cmd_restore(a, data, root):
    """从自动快照回滚真源 —— 没有 git 时的救命手段。"""
    snaps = list_snapshots(root)

    if a.list_snapshots:
        if not snaps:
            print("还没有任何快照。快照在每次写入前自动生成。")
            return 0
        print("可用快照（共 %d）：" % len(snaps))
        print("")
        for s in snaps:
            print("  %-28s %s  %9d B" % (s["name"], s["mtime"], s["size"]))
        print("")
        print("恢复：`todoctl restore --last --yes` / `--file <名称> --yes` / `--date YYYY-MM-DD --yes`")
        return 0

    avail = {s["name"]: s["path"] for s in snaps}
    target = None
    if a.last:
        for nm in ("last.json", "prev.json"):
            if nm in avail:
                target = avail[nm]
                break
    elif a.file:
        nm = Path(a.file).name
        target = avail.get(nm) or (Path(a.file) if Path(a.file).exists() else None)
    elif a.date:
        target = avail.get("tree-%s.json" % re.sub(r"[^0-9]", "", a.date))
    if target is None:
        raise Fail("找不到可恢复的快照。用 `todoctl restore --list` 查看。")

    target = Path(target)
    def bad(why):
        raise Fail("快照无法解析：%s\n  换一个：todoctl restore --list\n  原因：%s" % (target.name, why))

    try:
        blob = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return bad(exc)
    if not isinstance(blob, dict) or not isinstance(blob.get("nodes"), list):
        return bad("顶层不是含 nodes 数组的对象")
    cur = store_path(root)
    old = {}
    if cur.exists():
        try:
            old = json.loads(cur.read_text(encoding="utf-8"))
        except ValueError:
            old = {}
    cs = summarize(old if old.get("nodes") is not None else {"nodes": []})
    ns = summarize(blob)
    mt = datetime.fromtimestamp(target.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")

    print("即将用快照覆盖真源：")
    print("  快照    %s（%s，%d 字节）" % (target.name, mt, target.stat().st_size))
    print("  真源    %s" % cur)
    print("  现状    节点 %d · 已收口 %d · 未收口 %d" % (cs["total"], cs["n_closed"], cs["n_unclosed"]))
    print("  将变成  节点 %d · 已收口 %d · 未收口 %d" % (ns["total"], ns["n_closed"], ns["n_unclosed"]))
    print("")
    if not a.yes:
        flag = "--last" if a.last else ("--file %s" % Path(a.file).name if a.file else "--date %s" % (a.date or ""))
        print("这是**不可逆操作**：当前真源会被替换。确认无误后重跑并加 --yes：")
        print("  todoctl restore %s --yes" % flag)
        return 2

    d = snapshot_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    rescue = d / ("pre-restore-%s.json" % datetime.now().strftime("%Y%m%d-%H%M%S"))
    if cur.exists():
        rescue.write_text(cur.read_text(encoding="utf-8"), encoding="utf-8")

    floor = max(id_high_water(root, blob), id_high_water(root, old))
    if int(blob.get("next_seq") or 1) < floor:
        print("  ID 水位 %d → %d（ID 永不复用，回滚也不破例）" % (
            int(blob.get("next_seq") or 1), floor))
        blob["next_seq"] = floor

    tmp = cur.with_name("tree.json.tmp")
    tmp.write_text(json.dumps(blob, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, cur)
    print("✔ 已回滚到 %s" % target.name)
    if rescue.exists():
        print("  回滚前的状态另存为 %s（要再退回来就用它）" % rescue.name)
    try:
        write_views(root, load(root), root)
    except Exception as exc:
        print("  ⚠ 派生物刷新失败（真源已回滚）：%s" % exc, file=sys.stderr)
    return 0


def cmd_render(a, data, root):
    out = Path(a.out).expanduser().resolve() if a.out else root
    errors, _ = validate(data)
    files = write_views(out, data, root)
    for name, content in files.items():
        print("  生成 %s（%d 字节）" % (out / name, len(content.encode("utf-8"))))
    if errors:
        print("")
        print("⚠ 真源存在 %d 个错误：视图可能不完整（异常分支会被标为「游离」或截断成 `(环)`）。" % len(errors))
        for e in errors[:10]:
            print("  ✖ %s %s [%s]" % (e["code"], e.get("id") or "-", e["msg"]))
        if len(errors) > 10:
            print("  … 其余 %d 个见 `todoctl check`" % (len(errors) - 10))
        print("先修完真源再 render —— 不要拿错误状态下的视图做判断。")
        return 1
    print("✔ 派生物已覆盖生成。真源始终是 %s，视图禁止手改。" % store_path(root))
    return 0


def cmd_export(a, data, root):
    """导出**公开副本**：恒脱敏。

    为什么没有「不脱敏」模式：本命令的全部意义就是产出可公开的副本。
    加一个 `--full` 只会给一个以安全为目的的命令添一个**危险开关**，
    而「要一份完整副本」并不需要它 —— 真源目录本身就可读。
    少一个开关，就少一次不可逆泄露的可能。

    `internal` 节点**连同整条子树**剔除（fail-safe：宁可多删不可漏）——
    因为唯一无法自动脱敏的字段是 `title`，清空 `why`/`notes` 救不了它。
    """
    out = Path(a.out).expanduser().resolve() if a.out else (root / "public")
    out.mkdir(parents=True, exist_ok=True)
    clone = json.loads(json.dumps(data))
    ix = Tree(clone)

    marked, drop = [], set()
    for n in clone["nodes"]:
        if n.get("internal"):
            marked.append(n["id"])
            drop.add(n["id"])
            for d in descendants(clone, n["id"], ix):   # 级联：整条子树一起走
                drop.add(d["id"])

    keep = []
    for n in clone["nodes"]:
        if n["id"] in drop:
            continue
        n["why"] = "[已脱敏]" if n.get("why") else ""
        n["notes"] = []
        n.pop("refs", None)          # 历史字段（已废弃、不再写入），顺手清掉不留残渣
        n.pop("internal", None)
        keep.append(n)
    clone["nodes"] = keep

    (out / "tree.json").write_text(
        json.dumps(clone, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out / "TREE.md").write_text(render_tree(clone), encoding="utf-8")
    (out / "ACTIVE.md").write_text(render_active(clone), encoding="utf-8")
    (out / "dashboard.html").write_text(render_dashboard(clone), encoding="utf-8")

    print("✔ 脱敏导出至 %s" % out)
    print("  恒脱敏：本命令没有「不脱敏」模式（少一个能被误用的危险开关）")
    if marked:
        print("  按 internal 剔除：%d 个子树根（%s），连带后代共剔除 %d 个节点" % (
            len(marked), "、".join(marked[:5]), len(drop)))
    else:
        print("  未标记任何 internal 节点 —— 若公开版不该包含某些子树，先 `update <ID> --internal` 标注")
    print("  已清空：why（替换为占位）、notes")
    print("  未导出：RESUME.md / FOCUS.md（含内部进度轨迹与工作现场）")
    print("  ⚠ **标题**无法自动脱敏 —— 本次将导出 %d 个节点，公开前请人工复核标题" % len(keep))
    return 0


# ------------------------------------------------------------------ 看板模板

DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>需求树看板</title>
<style>
:root{--bg:#1b1b1a;--panel:#242423;--line:#3a3a38;--fg:#e9e7e2;--dim:#a3a19b;
--open:#b4b2a9;--doing:#ef9f27;--blocked:#f09595;--done:#85b7eb;--closed:#5dcaa5;--dropped:#888780}
*{box-sizing:border-box}
html,body{margin:0}
body{background:var(--bg);color:var(--fg);font:14px/1.6 system-ui,-apple-system,"Segoe UI",sans-serif}
header{padding:20px 24px 12px;border-bottom:1px solid var(--line)}
h1{margin:0 0 4px;font-size:16px;font-weight:500}
.meta{color:var(--dim);font-size:12px;font-family:ui-monospace,Consolas,monospace}
.stats{display:flex;flex-wrap:wrap;gap:20px;padding:12px 24px;border-bottom:1px solid var(--line);font-size:12px;color:var(--dim)}
.stats b{display:block;font-size:17px;font-weight:500;color:var(--fg)}
.bar{display:flex;flex-wrap:wrap;gap:8px;align-items:center;padding:12px 24px;border-bottom:1px solid var(--line)}
.chip{border:1px solid var(--line);border-radius:999px;padding:2px 10px;font-size:12px;color:var(--dim);cursor:pointer;background:transparent}
.chip[data-on="1"]{color:#1b1b1a;background:var(--fg);border-color:var(--fg)}
input[type=search]{background:var(--panel);border:1px solid var(--line);border-radius:8px;color:var(--fg);padding:4px 10px;font-size:12px;min-width:220px}
main{padding:16px 24px 90px}
ul{list-style:none;margin:0;padding-left:20px}
ul.root{padding-left:0}
li{margin:2px 0}
.row{display:flex;gap:8px;align-items:baseline;padding:3px 8px;border-radius:6px}
.row:hover{background:var(--panel)}
.tw{width:14px;flex:none;color:var(--dim);cursor:pointer;text-align:center;font-size:11px;user-select:none}
.pid{font-family:ui-monospace,Consolas,monospace;font-size:12px;color:var(--dim);flex:none}
.ttl{flex:1 1 auto;min-width:0}
.st{font-size:11px;flex:none;padding:0 8px;border-radius:999px;border:1px solid currentColor;white-space:nowrap}
.kd{font-size:11px;color:var(--dim);flex:none}
.why{color:var(--dim);font-size:12px;margin:0 0 6px 54px;padding-left:10px;border-left:2px solid var(--line)}
.warn{color:var(--doing)}
.hide{display:none}
.empty{color:var(--dim);padding:44px 0;text-align:center}
/* 仅为保持层级而显示的祖先：本身不符合当前筛选条件 ⇒ 视觉变淡，悬停恢复 */
li.anc>.row{opacity:.5}
li.anc>.row:hover{opacity:1}
/* ---------- 标签页 ---------- */
.tabs{display:flex;gap:4px;padding:0 24px;border-bottom:1px solid var(--line)}
.tab{padding:8px 14px;font-size:13px;color:var(--dim);cursor:pointer;border-bottom:2px solid transparent;user-select:none}
.tab:hover{color:var(--fg)}
.tab[data-on="1"]{color:var(--fg);border-bottom-color:var(--fg)}
/* ---------- 使用说明页 ---------- */
.doc{padding:18px 24px 90px;max-width:1200px}
.doc h2{font-size:14px;font-weight:500;margin:24px 0 10px}
.doc h2:first-child{margin-top:0}
.doc p{color:var(--dim);font-size:12.5px;margin:8px 0}
.doc table{width:100%;border-collapse:collapse;font-size:12.5px;margin:10px 0 6px}
.doc th,.doc td{border:1px solid var(--line);padding:7px 10px;text-align:left;vertical-align:top}
.doc th{color:var(--dim);font-weight:500;background:var(--panel)}
.doc td.grp{font-weight:500;white-space:nowrap}
.doc code{font-family:ui-monospace,Consolas,monospace;font-size:12px;background:var(--panel);
border:1px solid var(--line);border-radius:5px;padding:1px 5px;white-space:nowrap}
/* 非命令的代码标识（如状态名）——刻意不用 <code>，避免被 L18「命令必须真实存在」的正则误判 */
.doc .mono{font-family:ui-monospace,Consolas,monospace;font-size:12px;background:var(--panel);
border:1px solid var(--line);border-radius:5px;padding:1px 5px;white-space:nowrap;color:var(--dim)}
.doc pre{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:12px 14px;
overflow:auto;font-family:ui-monospace,Consolas,monospace;font-size:12.5px;line-height:1.8;margin:8px 0}
.doc pre .cm{color:var(--dim)}
.doc .note{color:var(--dim);font-size:12px;margin-top:6px;padding-left:10px;border-left:2px solid var(--line)}
</style>
</head>
<body>
<header>
  <h1 id="title">需求树看板</h1>
  <div class="meta" id="meta"></div>
</header>
<nav class="tabs">
  <span class="tab" data-pane="tree" data-on="1">需求树</span>
  <span class="tab" data-pane="help">使用说明</span>
</nav>
<section id="pane-tree">
<div class="stats" id="stats"></div>
<div class="bar">
  <span class="chip" data-st="open" data-on="1">未开始</span>
  <span class="chip" data-st="doing" data-on="1">进行中</span>
  <span class="chip" data-st="blocked" data-on="1">阻塞</span>
  <span class="chip" data-st="done" data-on="1">已完成</span>
  <span class="chip" data-st="closed" data-on="1">已收口</span>
  <span class="chip" data-st="dropped" data-on="1">已放弃</span>
  <span class="chip" id="only-active">仅未收口</span>
  <span class="chip" id="warn-only">仅看告警</span>
  <span class="chip" id="leaf-only" data-on="1" title="统计口径：默认只算没有子节点的节点（实际施工项）。关掉则连需求级容器/根一起算。">仅统计叶子</span>
  <span class="chip" id="reset" title="恢复默认：所有状态都显示，并清空搜索框">重置筛选</span>
  <input type="search" id="q" placeholder="搜索 标题 / ID / 编号 / 派生理由">
</div>
<main><ul class="root" id="tree"></ul></main>
</section>
<section id="pane-help" hidden>
<div class="doc">

<h2>一、这工具能做什么（按能力分组）</h2>
<table>
<tr><th style="width:64px">组</th><th>能力</th><th style="width:330px">命令</th></tr>
<tr><td class="grp">建</td><td>给项目立唯一真源</td><td><code>init</code></td></tr>
<tr><td class="grp">写</td><td>记新需求 / 派生需求、改标题理由验收条件、随手记进度</td><td><code>add</code> <code>update</code> <code>note</code></td></tr>
<tr><td class="grp">状态</td><td>开始、标记阻塞、解除阻塞、实现完成、收口、放弃</td><td><code>start</code> <code>block</code> <code>unblock</code> <code>done</code> <code>close</code> <code>drop</code></td></tr>
<tr><td class="grp">恢复</td><td>拿回上次现场、看手头同时几件事</td><td><code>resume</code> <code>bench</code></td></tr>
<tr><td class="grp">查看</td><td>查单条细节、全量体检、按线抽变更、周报、盯一条分支</td><td><code>show</code> <code>check</code> <code>log</code> <code>report</code> <code>focus</code></td></tr>
<tr><td class="grp">治理</td><td>封板成不可变时点、按快照回滚、跨进程写锁</td><td><code>baseline</code> <code>restore</code> <code>tree.lock</code></td></tr>
<tr><td class="grp">导出</td><td>出 7 个视图、导出脱敏公开副本</td><td><code>render</code> <code>export</code></td></tr>
</table>

<h2>二、怎么用（最常用的一条路径）</h2>
<pre>resume                                     <span class="cm">← ◎ 开工第一件事：拿回上次现场（RESUME.md）</span>
add --parent R-0006 --title "…" --why "…" --done-when "…"   <span class="cm">← ◎ 发现新问题当场落库</span>
start R-0006                               <span class="cm">← ◎ 开始做（需 done_when；祖先不得已收口）</span>
note R-0006 做到哪 发现了什么                 <span class="cm">← ◎ 做一步记一句（免引号）</span>
bench                                      <span class="cm">← ◎ 随时看手头同时有几件事（BENCH.md）</span>
done R-0006 --note "接口已实现"              <span class="cm">← ◎ 实现完成（≠ 收口）</span>
close R-0006 --evidence "commit:9f2c1ab" --evidence "test:…"   <span class="cm">← ◎ 验证通过才算收口</span>
check                                      <span class="cm">← ◎ 体检（有错返回 1）</span></pre>
<p class="note">五个完整工作流（首次建真源 / 派生新需求 / 推进与收口 / 出视图与巡检 / 每周 rebalance）见 skill 内
<code>references/entry-map.md</code> 第五节。</p>

<h2>三、全部子命令（21 个）与主要选项</h2>
<table>
<tr><th style="width:64px">组</th><th style="width:210px">命令</th><th>主要选项</th></tr>
<tr><td class="grp">建</td><td><code>init</code></td><td><code>--project</code></td></tr>
<tr><td class="grp">写</td><td><code>add</code></td><td><code>--parent</code> <code>--title</code> <code>--why</code> <code>--done-when</code> <code>--kind</code> <code>--depends-on</code> <code>--internal</code></td></tr>
<tr><td class="grp">写</td><td><code>update</code></td><td><code>--title</code> <code>--why</code> <code>--done-when</code> <code>--next-step</code> <code>--next</code> <code>--kind</code> <code>--depends-on</code> <code>--internal</code> <code>--external</code></td></tr>
<tr><td class="grp">写</td><td><code>note</code></td><td><code>--next-step</code> <code>--next</code></td></tr>
<tr><td class="grp">状态</td><td><code>start</code></td><td><code>--force</code></td></tr>
<tr><td class="grp">状态</td><td><code>block</code></td><td><code>--reason</code></td></tr>
<tr><td class="grp">状态</td><td><code>unblock</code></td><td><code>--force</code></td></tr>
<tr><td class="grp">状态</td><td><code>done</code></td><td><code>--note</code></td></tr>
<tr><td class="grp">状态</td><td><code>close</code></td><td><code>--evidence</code>（可重复 / 必填）<code>--override-reason</code> <code>--force</code></td></tr>
<tr><td class="grp">状态</td><td><code>drop</code></td><td><code>--reason</code> <code>--force</code></td></tr>
<tr><td class="grp">恢复</td><td><code>resume</code> / <code>bench</code></td><td>—</td></tr>
<tr><td class="grp">查看</td><td><code>show</code> / <code>check</code> / <code>focus</code></td><td>—</td></tr>
<tr><td class="grp">查看</td><td><code>log</code></td><td><code>--line</code> <code>--since</code> <code>--limit</code> <code>--commit-msg</code></td></tr>
<tr><td class="grp">治理</td><td><code>baseline</code></td><td><code>--name</code> <code>--note</code> <code>--list</code> <code>--diff [ID]</code></td></tr>
<tr><td class="grp">治理</td><td><code>restore</code></td><td><code>--list</code> <code>--last</code> <code>--file</code> <code>--date</code> <code>--yes</code>（必填）</td></tr>
<tr><td class="grp">巡检</td><td><code>report</code></td><td><code>--days</code></td></tr>
<tr><td class="grp">导出</td><td><code>render</code> / <code>export</code></td><td><code>--out</code></td></tr>
</table>

<h2>四、状态定义（6 个）</h2>
<table>
<tr><th style="width:86px">状态</th><th style="width:96px">看板标签</th><th>含义</th><th style="width:300px">进入条件</th></tr>
<tr><td><span class="mono">open</span></td><td>未开始</td><td>已记录，未推进</td><td><code>add</code></td></tr>
<tr><td><span class="mono">doing</span></td><td>进行中</td><td>正在处理</td><td>需 <code>done_when</code>；依赖已收口；<b>祖先不得是已收口 / 已放弃</b>（可用 <code>--force</code> 放行，但留 <code>W7</code> 告警）</td></tr>
<tr><td><span class="mono">blocked</span></td><td>阻塞</td><td>受阻停滞</td><td>必须给 <code>--reason</code></td></tr>
<tr><td><span class="mono">done</span></td><td>已完成</td><td><b>实现完成，待验证</b></td><td>从 <span class="mono">doing</span> 转来</td></tr>
<tr><td><span class="mono">closed</span></td><td>已收口</td><td><b>已验证收口</b></td><td>从 <span class="mono">done</span>；≥1 条证据；未被显式接受的未收口后代为 0</td></tr>
<tr><td><span class="mono">dropped</span></td><td>已放弃</td><td>主动放弃</td><td>必须给 <code>--reason</code></td></tr>
</table>
<p class="note"><b>两个关键区分</b>：① <span class="mono">done</span> ≠ <span class="mono">closed</span> ——「我改完了」不等于「这件事结束了」。
② <b>未收口 = open + doing + blocked + done</b>（即除 <span class="mono">closed</span> / <span class="mono">dropped</span> 外的全部），
所以统计栏的「未收口」通常<b>大于</b>「未开始」——它们不是同一个数。</p>

<h2>五、筛选口径（数字为什么和直觉不一致）</h2>
<table>
<tr><th style="width:160px">项</th><th>规则</th></tr>
<tr><td class="grp">状态标签</td><td>默认<b>全部显示</b>；点一下 = <b>隐藏该状态</b>（<b>不是</b>「只看该状态」）。全关会得到空树，用「重置筛选」一键恢复。</td></tr>
<tr><td class="grp">多条件是「且」</td><td>状态标签 × 「仅未收口」 × 「仅看告警」 × 搜索框，四者同时生效。</td></tr>
<tr><td class="grp">搜索范围</td><td>只搜 <b>ID / 位置编号 / 标题 / 派生理由 / 收口条件</b>（<b>不含</b>进度记录与收口证据）。</td></tr>
<tr><td class="grp">⭐ 统计口径</td><td><b>默认只统计叶子节点</b>（没有子节点的 = 实际施工项）。需求级容器与根节点的「收口」回答的是另一个问题（「这条线整体达成了吗」），与「还有几件事要做」混在同一个数里会让后者失真 ⇒ 需要看全量时关掉「仅统计叶子」。</td></tr>
<tr><td class="grp">⭐ 祖先占位</td><td>为不让树断裂，匹配节点的<b>上层节点会被一并显示</b>——它们本身<b>不符合</b>当前筛选，故用<b>变淡</b>样式标出；统计栏也据此拆成「匹配筛选」与「祖先占位」两个数，而不是一个含糊的合计。</td></tr>
</table>

<h2>六、约定与退出码</h2>
<table>
<tr><th style="width:190px">项</th><th>规则</th></tr>
<tr><td class="grp">状态流转</td><td><code>open → doing → done → closed</code>；<code>doing ⇄ blocked</code>；任意非终态 <code>→ dropped</code>。<b>done（改完了）≠ closed（已验证收口）</b>。</td></tr>
<tr><td class="grp">前置要求</td><td><code>start</code> / <code>unblock</code> 需 <code>done_when</code>，且祖先不得已收口 / 已放弃；<code>block</code> / <code>drop</code> 需 <code>--reason</code>；<code>close</code> 需 <code>--evidence</code>。</td></tr>
<tr><td class="grp">两个确认别混用</td><td><code>--force</code> 只跳过「未 done 不能收口」的状态检查，<b>跳不过假收口检查</b>；<code>--override-reason</code> 才放行「仍有未收口后代」。两个都踩到时要<b>同时给</b>。</td></tr>
<tr><td class="grp">退出码</td><td><code>0</code> 成功 ｜ <code>1</code> <code>check</code>/<code>render</code> 发现真源有错 ｜ <code>2</code> 被强校验或参数校验拒绝。<code>render</code> 在真源有错时<b>不会静默产出空树</b>，别拿错误状态下的视图做判断。</td></tr>
<tr><td class="grp">全局参数</td><td><code>--root &lt;真源目录&gt;</code>（写在子命令前后都可以）｜ <code>--no-render</code> 关闭自动刷新 ｜ <code>--wait SEC</code> 写锁等待秒数。</td></tr>
<tr><td class="grp">真源 / 派生物</td><td><b>真源</b>：<code>tree.json</code> <code>journal.jsonl</code> <code>baselines.json</code> <code>snapshots/</code> <code>tree.lock</code> —— 写入只走 CLI。<br><b>派生物</b>：本页等 7 个视图 —— <code>render</code> 覆盖生成，<b>禁止手改</b>。</td></tr>
</table>

</div>
</section>
<script>
const DATA = __PAYLOAD__;
const LABEL = {open:"未开始",doing:"进行中",blocked:"阻塞",done:"已完成",closed:"已收口",dropped:"已放弃"};
const COLOR = {open:"var(--open)",doing:"var(--doing)",blocked:"var(--blocked)",done:"var(--done)",closed:"var(--closed)",dropped:"var(--dropped)"};
const TERMINAL = {closed:1,dropped:1};
const S = {status:{},q:"",onlyActive:false,warnOnly:false,leafOnly:true,collapsed:{}};
["open","doing","blocked","done","closed","dropped"].forEach(function(k){S.status[k]=true});
const byId={},kids={};
DATA.nodes.forEach(function(n){byId[n.id]=n;kids[n.parent||""]=kids[n.parent||""]||[];kids[n.parent||""].push(n.id)});

function matches(n){
  if(!S.status[n.status]) return false;
  if(S.onlyActive && TERMINAL[n.status]) return false;
  if(S.warnOnly && !(n.warns&&n.warns.length)) return false;
  if(S.q){
    var hay=(n.id+" "+n.path+" "+n.title+" "+(n.why||"")+" "+(n.doneWhen||"")).toLowerCase();
    if(hay.indexOf(S.q)<0) return false;
  }
  return true;
}
function visibleSet(){
  var vis={};
  DATA.nodes.forEach(function(n){ if(matches(n)){ vis[n.id]=1; var p=n.parent; var guard=0; while(p&&guard++<200){ vis[p]=1; p=byId[p]?byId[p].parent:""; } } });
  return vis;
}
function el(tag,cls,txt){var e=document.createElement(tag);if(cls)e.className=cls;if(txt!=null)e.textContent=txt;return e;}
function render(){
  var vis=visibleSet(), host=document.getElementById("tree");
  host.innerHTML="";var count=0,matched=0;
  function walk(id,depth){
    if(!vis[id]) return null;
    var n=byId[id], hasKids=(kids[id]||[]).length>0;
    var li=el("li");
    var row=el("div","row");
    var tw=el("span","tw",hasKids?(S.collapsed[id]?"▸":"▾"):"");
    if(hasKids) tw.onclick=function(){S.collapsed[id]=!S.collapsed[id];render();};
    row.appendChild(tw);
    row.appendChild(el("span","pid",n.path+"  "+n.id));
    var t=el("span","ttl",n.title);row.appendChild(t);
    if(n.warns&&n.warns.length){var w=el("span","kd warn","⚠");w.title=n.warns.join("\n");row.appendChild(w);}
    row.appendChild(el("span","kd",n.kindCn));
    var st=el("span","st",LABEL[n.status]||n.status);st.style.color=COLOR[n.status];row.appendChild(st);
    if(DATA.focus===n.id){var f=el("span","kd","★聚焦");row.appendChild(f);}
    li.appendChild(row);
    if(n.why){li.appendChild(el("div","why","派生理由："+n.why));}
    if(n.status==="blocked"&&n.blockedBy){li.appendChild(el("div","why","阻塞于："+n.blockedBy));}
    if(n.status==="closed"&&n.evidence&&n.evidence.length){li.appendChild(el("div","why","收口证据："+n.evidence.join(" · ")));}
    if(n.doneWhen){li.appendChild(el("div","why","收口条件："+n.doneWhen));}
    // 区分「真匹配」与「仅为保持树结构而带上来的祖先」——后者本身不符合当前筛选条件，
    // 若不标出来，用户会看到「筛掉了某状态、树里却还有该状态」的错觉（2026-09-23 实测反馈）。
    if(matches(n)){matched++;}
    else{li.className="anc";row.title="仅为保持层级而显示的上层节点（本身不符合当前筛选条件）";}
    count++;
    if(hasKids&&!S.collapsed[id]){
      var ul=el("ul");
      // ⚠️ 必须滤掉未匹配的子节点：walk 被过滤时返回 null，
      //    appendChild(null) 会抛 "parameter 1 is not of type 'Node'" 并**中断整个 render**
      //    ⇒ 树直接消失（2026-09-22 实测：点一次状态标签、或搜任意词即触发）。
      (kids[id]||[]).forEach(function(c){var x=walk(c,depth+1);if(x)ul.appendChild(x);});
      if(ul.children.length) li.appendChild(ul);
    }
    return li;
  }
  (kids[""]||[]).forEach(function(r){var x=walk(r,0);if(x)host.appendChild(x);});
  if(!count){var e=el("div","empty","没有匹配的节点 —— 检查上方状态标签是否被全部关掉，或清空搜索框");host.appendChild(e);}
  var s=DATA.stats;
  // 统计口径：默认只算**叶子**（无子节点 = 实际施工项）。需求级容器 / 根的「收口」回答的是另一个
  // 问题（「这条线整体达成了吗」），与「还有几件事要做」混在一个数里会让后者失真
  // （2026-09-23 用户反馈：中间节点和根无意义）⇒ 拆成可切换的口径。
  var sc={total:0,unclosed:0,doing:0,blocked:0,closed:0};
  DATA.nodes.forEach(function(n){
    if(S.leafOnly && (kids[n.id]||[]).length) return;
    sc.total++;
    if(TERMINAL[n.status]) sc.closed++; else sc.unclosed++;
    if(n.status==="doing") sc.doing++;
    if(n.status==="blocked") sc.blocked++;
  });
  document.getElementById("stats").innerHTML=
    "<div><b>"+sc.total+"</b>"+(S.leafOnly?"叶子节点":"全部节点")+"</div>"+
    "<div><b>"+sc.unclosed+"</b>未收口</div>"+
    "<div><b>"+sc.doing+"</b>进行中</div><div><b>"+sc.blocked+"</b>阻塞</div>"+
    "<div><b>"+sc.closed+"</b>已收口</div><div><b>"+s.max_depth+"</b>最大深度</div>"+
    "<div><b>"+matched+"</b>匹配筛选</div>"+
    (count>matched
      ? "<div title=\"这些上层节点本身不符合当前筛选，仅为保持树的层级而显示\"><b>+"+(count-matched)+"</b>祖先占位</div>"
      : "");
  document.getElementById("title").textContent="需求树看板"+(DATA.project?" · "+DATA.project:"");
  document.getElementById("meta").textContent="生成于 "+DATA.generated+"　·　真源 tree.json（本页为派生物，禁止手改）";
}
document.querySelectorAll(".chip[data-st]").forEach(function(c){
  c.title="点击可隐藏 / 显示「"+c.textContent.trim()+"」的节点（默认全显示）";
  c.onclick=function(){
    var k=c.getAttribute("data-st");S.status[k]=!S.status[k];
    c.setAttribute("data-on",S.status[k]?"1":"0");render();
  };
});
document.getElementById("only-active").onclick=function(){
  S.onlyActive=!S.onlyActive;this.setAttribute("data-on",S.onlyActive?"1":"0");render();};
document.getElementById("warn-only").onclick=function(){
  S.warnOnly=!S.warnOnly;this.setAttribute("data-on",S.warnOnly?"1":"0");render();};
document.getElementById("leaf-only").onclick=function(){
  S.leafOnly=!S.leafOnly;this.setAttribute("data-on",S.leafOnly?"1":"0");render();};
document.getElementById("q").oninput=function(){S.q=this.value.trim().toLowerCase();render();};
document.getElementById("reset").onclick=function(){
  ["open","doing","blocked","done","closed","dropped"].forEach(function(k){S.status[k]=true;});
  S.onlyActive=false;S.warnOnly=false;S.leafOnly=true;S.q="";
  document.getElementById("q").value="";
  document.querySelectorAll(".chip[data-st]").forEach(function(c){c.setAttribute("data-on","1");});
  document.getElementById("only-active").setAttribute("data-on","0");
  document.getElementById("warn-only").setAttribute("data-on","0");
  document.getElementById("leaf-only").setAttribute("data-on","1");
  render();
};
document.querySelectorAll(".tab").forEach(function(t){
  t.onclick=function(){
    var k=t.getAttribute("data-pane");
    document.querySelectorAll(".tab").forEach(function(x){x.setAttribute("data-on", x===t?"1":"0");});
    document.getElementById("pane-tree").hidden=(k!=="tree");
    document.getElementById("pane-help").hidden=(k!=="help");
  };
});
render();
</script>
</body>
</html>
"""


# ------------------------------------------------------------------ CLI

def build_parser():
    p = argparse.ArgumentParser(
        prog="todoctl",
        description="需求派生树与收口状态的唯一写入口（真源 tree.json，视图由 render 生成）",
    )
    p.add_argument("--root", help="真源目录（写在子命令前后均可）。默认依序判：$TODOCTL_ROOT > 当前目录已是真源(含 tree.json) > 当前目录已预建 docs/requirements 或 .req-tree/requirements > 仓库内 docs/requirements > 工作区 .req-tree/requirements")
    p.add_argument("--no-render", dest="no_render", action="store_true",
                   help="真源变更后不自动刷新派生物（默认会自动刷新）")
    p.add_argument("--wait", type=float, default=0.0, metavar="SEC",
                   help="写锁等待秒数，默认 0（立即失败）。只有写入命令受锁保护")
    sub = p.add_subparsers(dest="cmd")

    s = sub.add_parser("init", help="初始化真源")
    s.add_argument("--project")
    s.set_defaults(func=cmd_init)

    s = sub.add_parser("add", help="新增节点（派生一条需求/任务/缺陷/决策/阻塞）")
    s.add_argument("--parent", help="派生来源节点 ID；建立根需求时不填")
    s.add_argument("--title", required=True)
    s.add_argument("--why", help="是什么触发了它（追溯链的关键，务必填）")
    s.add_argument("--done-when", dest="done_when", help="怎样才算收口（进入 doing 前必填）")
    s.add_argument("--kind", default="requirement", choices=KINDS)
    s.add_argument("--depends-on", dest="depends_on", action="append", metavar="ID")
    s.add_argument("--internal", action="store_true",
                   help="标记为内部节点：export 时**连同整条子树**剔除（用于「这条线不能公开」）")
    s.set_defaults(func=cmd_add)

    s = sub.add_parser("start", help="open/blocked → doing")
    s.add_argument("id"); s.add_argument("--force", action="store_true")
    s.set_defaults(func=cmd_start)

    s = sub.add_parser("block", help="标记阻塞（必须给理由）")
    s.add_argument("id"); s.add_argument("--reason", required=True)
    s.set_defaults(func=cmd_block)

    s = sub.add_parser("unblock", help="解除阻塞 → doing")
    s.add_argument("id")
    s.add_argument("--force", action="store_true", help="祖先已收口/已放弃时仍要推进")
    s.set_defaults(func=cmd_unblock)

    s = sub.add_parser("done", help="doing → done（实现完成，尚未收口）")
    s.add_argument("id"); s.add_argument("--note")
    s.set_defaults(func=cmd_done)

    s = sub.add_parser("close", help="done → closed（必须附证据）")
    s.add_argument("id")
    s.add_argument("--evidence", action="append", required=True,
                   help="可重复。如 commit:abc123 / test:test_x / file:path / user:<谁>确认")
    s.add_argument("--override-reason", dest="override_reason",
                   help="仍有未收口后代时，显式确认并记录理由")
    s.add_argument("--force", action="store_true", help="跳过 done 前置状态检查")
    s.set_defaults(func=cmd_close)

    s = sub.add_parser("drop", help="放弃（必须给理由）")
    s.add_argument("id"); s.add_argument("--reason", required=True)
    s.add_argument("--force", action="store_true")
    s.set_defaults(func=cmd_drop)

    s = sub.add_parser("update", help="修正标题/理由/验收条件/类型/依赖/下一步")
    s.add_argument("id")
    s.add_argument("--title"); s.add_argument("--why")
    s.add_argument("--done-when", dest="done_when")
    s.add_argument("--next-step", dest="next_step", help="下一步要做什么（文字说明）")
    s.add_argument("--next", metavar="ID", help="计划中的下一步是哪个节点（传空串清除）")
    s.add_argument("--kind", choices=KINDS)
    s.add_argument("--depends-on", dest="depends_on", action="append", metavar="ID")
    g = s.add_mutually_exclusive_group()
    g.add_argument("--internal", action="store_true",
                   help="标记为内部节点：export 时连同整条子树剔除")
    g.add_argument("--external", action="store_true", help="取消内部标记（该节点可公开）")
    s.set_defaults(func=cmd_update)

    s = sub.add_parser("focus", help="设置/清除当前聚焦分支")
    s.add_argument("id", nargs="?"); s.add_argument("--clear", action="store_true")
    s.set_defaults(func=cmd_focus)

    s = sub.add_parser("note", help="给节点追加一条进度记录（跨次恢复的关键素材）")
    s.add_argument("id")
    s.add_argument("text", nargs="+", help="这次做到哪 / 发现了什么，可直接不引号连写")
    s.add_argument("--next-step", dest="next_step", help="同时更新「下一步要做什么」")
    s.add_argument("--next", metavar="ID", help="同时更新「计划中的下一步」指向的节点")
    s.set_defaults(func=cmd_note)

    s = sub.add_parser("bench", help="台面：手头全部进行中任务（按线分组 + 下一步 + 搁置天数）")
    s.set_defaults(func=cmd_bench)

    s = sub.add_parser("resume", help="场景恢复：输出恢复简报，并把「现在」记为本次恢复点")
    s.add_argument("id", nargs="?", help="指定恢复目标；不填则用当前 focus，再不行自动推断")
    s.set_defaults(func=cmd_resume)

    s = sub.add_parser("show", help="查看单节点详情")
    s.add_argument("id")
    s.set_defaults(func=cmd_show)

    s = sub.add_parser("check", help="强校验：ID/父/环/状态与证据一致性/假收口")
    s.set_defaults(func=cmd_check)

    s = sub.add_parser("log", help="按需求线抽取变更叙述（提交信息素材 / 分线核对）")
    s.add_argument("--line", metavar="ID", help="只要某条线（可给该线上任意节点）")
    s.add_argument("--since", help="起始时间，如 2026-09-22 或 '2026-09-22 13:00'")
    s.add_argument("--limit", type=int,
                   help="只看最后 N 条（N ≤ 0 视为 0 条，即空）")
    s.add_argument("--commit-msg", dest="commit_msg", action="store_true",
                   help="输出提交信息草稿")
    s.set_defaults(func=cmd_log)

    s = sub.add_parser("baseline", help="封板：把当前各线成果封成一个不可变基线")
    s.add_argument("--name", help="基线名称，如 \"v0.3 首版\"")
    s.add_argument("--note", help="说明")
    s.add_argument("--list", dest="list_baselines", action="store_true", help="列出所有基线")
    s.add_argument("--diff", nargs="?", const="", default=None, metavar="ID",
                   help="与指定基线比对；不带值则与上一个比")
    s.set_defaults(func=cmd_baseline)

    s = sub.add_parser("restore", help="从自动快照回滚真源（不可逆，需 --yes）")
    s.add_argument("--list", dest="list_snapshots", action="store_true", help="列出可用快照")
    s.add_argument("--last", action="store_true", help="回退到上一次写入之前")
    s.add_argument("--file", help="指定快照文件名")
    s.add_argument("--date", help="回退到该日首写之前，如 2026-09-22")
    s.add_argument("--yes", action="store_true", help="确认执行（必填）")
    s.set_defaults(func=cmd_restore)

    s = sub.add_parser("render", help="生成 TREE.md / ACTIVE.md / FOCUS.md / RESUME.md / BENCH.md / BASELINE.md / dashboard.html")
    s.add_argument("--out", help="输出目录（默认真源目录）")
    s.set_defaults(func=cmd_render)

    s = sub.add_parser("export", help="导出**公开副本**（恒脱敏，无其它模式）")
    s.add_argument("--out", help="输出目录（默认真源目录下的 public/）")
    s.set_defaults(func=cmd_export)

    s = sub.add_parser("report", help="变更摘要")
    s.add_argument("--days", type=int, default=7)
    s.set_defaults(func=cmd_report)

    return p


def normalize_argv(argv):
    """把 --root / --no-render / --wait 统一提到最前。

    argparse 只接受全局选项出现在子命令之前，但用户几乎必然写成
    `todoctl add --title x --root D:\\proj`。此处统一搬家，两种写法都生效。
    """
    argv = list(argv)
    moved, rest, i = [], [], 0
    while i < len(argv):
        tok = argv[i]
        if tok in ("--root", "--wait") and i + 1 < len(argv):
            moved += [tok, argv[i + 1]]
            i += 2
            continue
        if tok == "--no-render" or tok.startswith("--root=") or tok.startswith("--wait="):
            moved.append(tok)
            i += 1
            continue
        rest.append(tok)
        i += 1
    return moved + rest


NO_JOURNAL = ("init", "check", "render", "export", "report", "show", "log", "bench")


def journal_entry_for(a, data):
    """把一次成功写入归到某条需求线上，供 `log` 分线抽取。"""
    cmd = a.cmd
    nid = getattr(a, "id", None)
    if cmd == "add":
        nid = _LAST_NEW_ID
    elif cmd in ("focus", "resume"):
        nid = data.get("focus")
    detail = ""
    if cmd == "note":
        detail = " ".join(getattr(a, "text", []) or [])
    elif cmd in ("block", "drop"):
        detail = getattr(a, "reason", "") or ""
    elif cmd == "close":
        detail = " / ".join(getattr(a, "evidence", []) or [])
    return {"op": cmd, "id": nid, "detail": detail}


def main(argv=None):
    global AUTO_RENDER
    parser = build_parser()
    a = parser.parse_args(normalize_argv(sys.argv[1:] if argv is None else argv))
    if not getattr(a, "cmd", None):
        parser.print_help()
        return 2
    if getattr(a, "no_render", False):
        AUTO_RENDER = False
    root, origin = resolve_root_info(getattr(a, "root", None))
    a.root_origin = origin          # 供 init 打印「落点来源」；同一次判定，避免两处结论不一致

    # 真源不存在时直接拒绝：`init`（负责创建）与 `restore`（从快照恢复）例外。
    # ⚠️ 必须在**拿写锁之前**拦 —— 否则「--root 指错目录」不仅会让读/渲染命令
    #    静默产出空视图（把「参数指错」伪装成「树是空的」），还会在那个错误位置
    #    留下 tree.lock 等残留（2026-09-22 实测：仓库根被误建 8 个空派生物）。
    if a.cmd not in ("init", "restore") and not store_path(root).exists():
        print("✖ 真源不存在：%s" % store_path(root), file=sys.stderr)
        print("  落点判定：%s" % origin, file=sys.stderr)
        print("  处理：① `--root` 要指向【真源目录】（含 tree.json，"
              "如 <repo>/docs/requirements），不是仓库根；", file=sys.stderr)
        print("        ② 若确实尚未初始化，先跑 `todoctl init`。", file=sys.stderr)
        return 2

    readonly = a.cmd in READONLY_CMDS
    ctx = nullcontext() if readonly else WriteLock(root, wait=getattr(a, "wait", 0) or 0.0)
    try:
        with ctx:
            try:
                data = load(root)
            except Fail as exc:
                # 真源本身读不出来 —— 这不是「拒绝写入」，别把前缀写歪让人误解
                print("✖ %s" % exc, file=sys.stderr)
                return 2
            pre = short_index(data)
            rc = a.func(a, data, root)
            if rc == 0 and a.cmd not in NO_JOURNAL:
                try:
                    entry = journal_entry_for(a, data)
                    entry["delta"] = index_delta(pre, short_index(data))
                    append_journal(root, data, [entry])
                except Exception as exc:
                    print("  ⚠ 变更日志写入失败（真源已保存）：%s" % exc, file=sys.stderr)
            return rc
    except Fail as exc:
        print("✖ 拒绝写入：%s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
