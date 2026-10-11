/**
 * req-tree for DeepSeek Harness — client half (right-sidebar tab body).
 *
 * A read-only projection of one workspace's req-tree source of truth. It polls
 * the plugin's own host route and renders the tree, per-line progress and the
 * non-blocking warnings that `todoctl check` would print.
 *
 * Two rules shape every decision here:
 *
 * 1. **It never writes.** req-tree's invariant is that all writes go through its
 *    CLI; a browser widget that could "just close this node" would be a weaker
 *    re-implementation of an evidence gate. So there are no action buttons that
 *    mutate anything — only links that take the user back to the CLI's own docs.
 * 2. **A 200 is not a promise.** The route can answer 200 with an error body
 *    (no tree yet, corrupt JSON, missing workspace). Those states render as
 *    their own views; they never degrade into "0 nodes, all clear", which would
 *    be a lie about the project's state.
 *
 * @module dsh-req-tree/client
 */
import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from 'react/jsx-runtime';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

export const LOCALE_NAMESPACE = 'dsh-req-tree';
/** Tab kind + body key. `id` must be unique across all tab-type registrations. */
export const TAB_KIND = 'req-tree';
export const TAB_ID = 'dsh-req-tree/tree';

/** Host routes served by this plugin's own host half. */
const TREE_PATH = '/plugins/dsh-req-tree/tree';
const WORKSPACES_PATH = '/plugins/dsh-req-tree/workspaces';

const DEFAULT_POLL_MS = 3000;
const MIN_POLL_MS = 1000;

// --- dictionaries --------------------------------------------------------------

/** Exported so the locale-completeness test can assert both sets stay in step. */
export const en = {
  'tab.title': 'Requirements',
  'tab.guide': 'Open the requirement tree for this workspace',
  'summary.unclosed': 'unclosed',
  'summary.closed': 'closed',
  'summary.dropped': 'dropped',
  'summary.total': 'total',
  'summary.progress': '{pct}% closed',
  'lines.title': 'Requirement lines',
  'lines.empty': 'No requirement lines yet.',
  'tree.title': 'Tree',
  'tree.empty': 'This tree has no nodes yet. Run `todoctl add` to record the first requirement.',
  'tree.showClosed': 'Show closed and dropped',
  'tree.showTerminal': 'Hide closed and dropped',
  'tree.search': 'Filter by title or ID',
  'tree.searchClear': 'Clear filter',
  'tree.focus': 'Current focus',
  'tree.internal': 'internal',
  'tree.truncated': 'Showing the first {shown} of {total} nodes. Raise `maxNodes` in the plugin config to see more.',
  'warnings.title': 'Warnings',
  'warnings.empty': 'No warnings.',
  'warnings.hidden': '{count} hidden by the current filter',
  'detail.why': 'Why it exists',
  'detail.doneWhen': 'Done when',
  'detail.nextStep': 'Next step',
  'detail.blockedBy': 'Blocked by',
  'detail.evidence': 'Closure evidence',
  'detail.updated': 'Updated',
  'detail.stale': 'untouched for {days} days',
  'detail.close': 'Close',
  'journal.title': 'Recent changes',
  'journal.empty': 'No recorded changes yet.',
  'source.title': 'Source of truth',
  'source.legacy': 'This source of truth still sits at the legacy `.workbuddy/requirements` location. Move it to `.req-tree/requirements` or `docs/requirements`.',
  'state.loading': 'Reading tree.json…',
  'state.error': 'Cannot read the requirement tree',
  'state.retry': 'Retry',
  'state.noWorkspace': 'No workspace is open.',
  'state.offline': 'Lost contact with the host. Showing the last snapshot.',
  'state.readonly': 'Read-only — writes go through the `todoctl` CLI.',
  'switcher.label': 'Workspace',
  'status.open': 'not started',
  'status.doing': 'in progress',
  'status.blocked': 'blocked',
  'status.done': 'implemented, not verified',
  'status.closed': 'closed',
  'status.dropped': 'dropped',
  'op.add': 'added',
  'op.start': 'started',
  'op.block': 'blocked',
  'op.unblock': 'unblocked',
  'op.done': 'implemented',
  'op.close': 'closed',
  'op.drop': 'dropped',
  'op.update': 'updated',
  'op.note': 'noted',
  'op.focus': 'focused',
  'op.resume': 'resumed',
  'op.baseline': 'baselined',
  'op.restore': 'restored',
};

export const zh = {
  'tab.title': '需求树',
  'tab.guide': '打开当前工作区的需求树',
  'summary.unclosed': '未收口',
  'summary.closed': '已收口',
  'summary.dropped': '已放弃',
  'summary.total': '共',
  'summary.progress': '已收口 {pct}%',
  'lines.title': '需求线',
  'lines.empty': '还没有需求线。',
  'tree.title': '树',
  'tree.empty': '这棵树还没有节点。用 `todoctl add` 记下第一条需求。',
  'tree.showClosed': '显示已收口与已放弃',
  'tree.showTerminal': '隐藏已收口与已放弃',
  'tree.search': '按标题或 ID 过滤',
  'tree.searchClear': '清除过滤',
  'tree.focus': '当前聚焦',
  'tree.internal': '内部',
  'tree.truncated': '只显示前 {shown} / {total} 个节点。要看更多请调大插件配置里的 `maxNodes`。',
  'warnings.title': '告警',
  'warnings.empty': '没有告警。',
  'warnings.hidden': '当前过滤掉了 {count} 条',
  'detail.why': '为什么会有它',
  'detail.doneWhen': '怎样才算收口',
  'detail.nextStep': '下一步',
  'detail.blockedBy': '阻塞原因',
  'detail.evidence': '收口证据',
  'detail.updated': '更新于',
  'detail.stale': '已 {days} 天没动',
  'detail.close': '关闭',
  'journal.title': '最近变动',
  'journal.empty': '还没有记录到的变动。',
  'source.title': '真源位置',
  'source.legacy': '真源还在旧版 `.workbuddy/requirements` 位置，建议搬到 `.req-tree/requirements` 或 `docs/requirements`。',
  'state.loading': '正在读取 tree.json…',
  'state.error': '读不到需求树',
  'state.retry': '重试',
  'state.noWorkspace': '没有打开任何工作区。',
  'state.offline': '与宿主失去联系，下面是上一次读到的快照。',
  'state.readonly': '只读 —— 写入一律走 `todoctl` 命令行。',
  'switcher.label': '工作区',
  'status.open': '未开始',
  'status.doing': '进行中',
  'status.blocked': '阻塞',
  'status.done': '已实现，未验证',
  'status.closed': '已收口',
  'status.dropped': '已放弃',
  'op.add': '新增',
  'op.start': '开始',
  'op.block': '阻塞',
  'op.unblock': '解除阻塞',
  'op.done': '实现完成',
  'op.close': '收口',
  'op.drop': '放弃',
  'op.update': '更新',
  'op.note': '记录',
  'op.focus': '聚焦',
  'op.resume': '恢复现场',
  'op.baseline': '封板',
  'op.restore': '回滚',
};

// --- plugin-owned styling -----------------------------------------------------
// Uses only host theme tokens, so the panel follows light/dark automatically.

const CSS = `
.rt-root{box-sizing:border-box;display:flex;flex-direction:column;gap:10px;min-height:0;height:100%;padding:10px 12px;overflow-y:auto;color:var(--dsw-alias-label-secondary);font-size:12px;line-height:1.5}
.rt-head{display:flex;flex-direction:column;gap:6px}
.rt-headRow{display:flex;align-items:center;gap:8px;min-width:0}
.rt-project{color:var(--dsw-alias-label-primary);font-weight:600;font-size:13px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1 1 auto;min-width:0}
.rt-readonly{margin-left:auto;flex:none;border:1px solid var(--dsw-alias-border-l2);border-radius:999px;padding:1px 7px;color:var(--dsw-alias-label-tertiary,var(--dsw-alias-label-secondary));font-size:10.5px;white-space:nowrap}
.rt-spacer{flex:1 1 auto}
.rt-refresh{flex:none;border:1px solid var(--dsw-alias-border-l2);background:transparent;color:inherit;font:inherit;font-size:10.5px;cursor:pointer;border-radius:999px;padding:1px 8px}
.rt-refresh:hover{border-color:var(--dsw-alias-state-business-primary,var(--dsw-alias-brand-primary));color:var(--dsw-alias-state-business-primary,var(--dsw-alias-brand-primary))}
.rt-refresh:focus-visible,.rt-node:focus-visible,.rt-toggle:focus-visible,.rt-warning:focus-visible{outline:2px solid var(--dsw-alias-state-business-primary,var(--dsw-alias-brand-primary));outline-offset:2px}
.rt-metrics{display:flex;flex-wrap:wrap;gap:6px;align-items:center}
.rt-metric{border:1px solid var(--dsw-alias-border-l2);border-radius:999px;padding:1px 8px;font-size:11px;white-space:nowrap;font-variant-numeric:tabular-nums}
.rt-metric b{color:var(--dsw-alias-label-primary);font-weight:600}
.rt-meter{height:6px;border-radius:999px;background:var(--dsw-alias-bg-layer-2);overflow:hidden}
.rt-meter i{display:block;height:100%;background:var(--dsw-alias-state-success-primary);border-radius:999px}
.rt-section{display:flex;flex-direction:column;gap:6px}
.rt-sectionTitle{color:var(--dsw-alias-label-primary);font-size:11px;font-weight:600;letter-spacing:.04em;text-transform:uppercase}
.rt-line{display:flex;flex-direction:column;gap:3px}
.rt-lineHead{display:flex;align-items:baseline;gap:6px;min-width:0}
.rt-lineTitle{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1 1 auto;min-width:0}
.rt-lineCount{flex:none;color:var(--dsw-alias-label-tertiary,var(--dsw-alias-label-secondary));font-size:10.5px;font-variant-numeric:tabular-nums}
.rt-source{border:1px solid var(--dsw-alias-border-l1);border-radius:8px;padding:6px 8px;font-size:10.5px;word-break:break-all;color:var(--dsw-alias-label-tertiary,var(--dsw-alias-label-secondary))}
.rt-source code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;color:var(--dsw-alias-label-secondary)}
.rt-note{border-left:2px solid var(--dsw-alias-state-warn-primary);padding-left:8px;color:var(--dsw-alias-label-secondary)}
.rt-tree{display:flex;flex-direction:column;gap:1px}
.rt-node{display:flex;align-items:flex-start;gap:6px;width:100%;border:1px solid transparent;border-radius:6px;background:transparent;color:inherit;font:inherit;text-align:left;cursor:pointer;padding:3px 6px}
.rt-node:hover{background:var(--dsw-alias-bg-layer-1);border-color:var(--dsw-alias-border-l1)}
.rt-node[data-open=true]{background:var(--dsw-alias-bg-layer-1);border-color:var(--dsw-alias-border-l2)}
.rt-node[data-focus=true]{border-color:var(--dsw-alias-state-business-primary,var(--dsw-alias-brand-primary))}
.rt-pos{flex:none;color:var(--dsw-alias-label-tertiary,var(--dsw-alias-label-secondary));font-size:10.5px;font-variant-numeric:tabular-nums;min-width:2.6em;text-align:right;font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
.rt-glyph{flex:none;width:1.1em;text-align:center}
.rt-title{flex:1 1 auto;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.rt-chip{flex:none;font-size:10px;border:1px solid var(--dsw-alias-border-l2);border-radius:999px;padding:0 6px;white-space:nowrap}
.rt-chip[data-status=doing]{color:var(--dsw-alias-state-business-primary,var(--dsw-alias-brand-primary))}
.rt-chip[data-status=blocked]{color:var(--dsw-alias-state-error-primary)}
.rt-chip[data-status=done]{color:var(--dsw-alias-state-warn-primary)}
.rt-chip[data-status=closed]{color:var(--dsw-alias-state-success-primary)}
.rt-detail{display:flex;flex-direction:column;gap:4px;border:1px solid var(--dsw-alias-border-l1);border-radius:8px;padding:8px;background:var(--dsw-alias-bg-layer-1);margin:2px 0 6px}
.rt-detailRow{display:flex;gap:6px;min-width:0}
.rt-detailKey{flex:none;width:5.5em;color:var(--dsw-alias-label-tertiary,var(--dsw-alias-label-secondary));font-size:10.5px}
.rt-detailVal{flex:1 1 auto;min-width:0;word-break:break-word}
.rt-evidence{margin:0;padding-left:1em}
.rt-warning{display:flex;gap:6px;align-items:flex-start;border:1px solid var(--dsw-alias-border-l1);border-radius:6px;padding:4px 7px;font-size:11px;cursor:pointer;background:transparent;color:inherit;font-family:inherit;text-align:left;width:100%}
.rt-warning:hover{border-color:var(--dsw-alias-state-warn-primary)}
.rt-wcode{flex:none;font-weight:600;color:var(--dsw-alias-state-warn-primary);font-variant-numeric:tabular-nums}
.rt-wmsg{flex:1 1 auto;min-width:0}
.rt-journal{display:flex;flex-direction:column;gap:3px}
.rt-jrow{display:flex;gap:6px;min-width:0;font-size:11px}
.rt-jts{flex:none;color:var(--dsw-alias-label-tertiary,var(--dsw-alias-label-secondary));font-variant-numeric:tabular-nums;font-size:10.5px}
.rt-jop{flex:none;border:1px solid var(--dsw-alias-border-l1);border-radius:999px;padding:0 5px;font-size:10px}
.rt-jdetail{flex:1 1 auto;min-width:0;color:var(--dsw-alias-label-tertiary,var(--dsw-alias-label-secondary));overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.rt-toggle{align-self:flex-start;border:1px solid var(--dsw-alias-border-l2);background:transparent;color:inherit;font:inherit;font-size:10.5px;cursor:pointer;border-radius:999px;padding:1px 8px}
.rt-toggle:hover{border-color:var(--dsw-alias-state-business-primary,var(--dsw-alias-brand-primary))}
.rt-search{flex:1 1 auto;min-width:0;border:1px solid var(--dsw-alias-border-l2);border-radius:999px;background:var(--dsw-alias-bg-layer-1);color:var(--dsw-alias-label-primary);font:inherit;font-size:11px;padding:2px 9px}
.rt-search:focus{outline:2px solid var(--dsw-alias-state-business-primary,var(--dsw-alias-brand-primary));outline-offset:1px}
.rt-empty{color:var(--dsw-alias-label-tertiary,var(--dsw-alias-label-secondary));padding:8px 2px}
.rt-error{border:1px solid var(--dsw-alias-state-error-primary);border-radius:8px;padding:8px;color:var(--dsw-alias-state-error-primary)}
.rt-error button{margin-top:6px;border:1px solid currentColor;background:transparent;color:inherit;font:inherit;font-size:11px;cursor:pointer;border-radius:999px;padding:1px 9px}
.rt-skeleton{display:flex;flex-direction:column;gap:6px;padding:8px 0}
.rt-skeleton i{display:block;height:10px;border-radius:5px;background:var(--dsw-alias-bg-layer-2);animation:rtPulse 1.3s ease-in-out infinite}
@keyframes rtPulse{0%,100%{opacity:.45}50%{opacity:.9}}
@media (prefers-reduced-motion:reduce){.rt-skeleton i{animation:none}}
`;

function ensureStyle() {
  if (typeof document === 'undefined') return;
  const id = 'dsh-req-tree/panel.css';
  if (document.querySelector(`style[data-plugin-css=${JSON.stringify(id)}]`) !== null) return;
  const tag = document.createElement('style');
  tag.dataset.plugin = 'dsh-req-tree';
  tag.dataset.pluginCss = id;
  tag.textContent = CSS;
  document.head.appendChild(tag);
}

// --- fetch + polling -----------------------------------------------------------

/**
 * Whether a parsed body is a usable snapshot.
 *
 * A 200 can carry an error body, so the shape is checked rather than trusted.
 * Anything unrecognised is treated as "no data yet" so the panel shows its
 * empty/error view instead of rendering `undefined` as zero nodes.
 */
function isTreePayload(value) {
  return typeof value === 'object' && value !== null && !Array.isArray(value) && Array.isArray(value.nodes);
}

/**
 * Whether a poll result should replace what is already on screen.
 *
 * Pure, so the cost rule is testable without waiting on a real timer: a poll
 * whose revision matches the last applied one is a no-op, and — crucially — a
 * body with *no* revision is always applied, because an unversioned response
 * carries no evidence that it is unchanged. Treating "unknown" as "unchanged"
 * would freeze the panel forever against a host that omits the field.
 *
 * @param lastRevision - the revision currently applied, or undefined.
 * @param payload - the freshly received body.
 */
export function shouldApply(lastRevision, payload) {
  if (!isTreePayload(payload)) return true;
  const next = payload.revision;
  if (typeof next !== 'string') return true;
  return next !== lastRevision;
}

/**
 * Poll the host route, but only while `active` is true.
 *
 * Two decisions keep an idle panel from doing any work:
 *
 * - **Visibility gating.** The right sidebar tells a tab whether its body is
 *   actually on screen (`tab.visible`), which is false when the column is
 *   collapsed, when another tab is selected, and for every background Session.
 *   While false there is no timer and no request at all — and because the last
 *   snapshot is kept, re-opening the tab shows the tree immediately instead of
 *   flashing a skeleton.
 * - **Revision gating.** The host answers with a fingerprint of the files it
 *   read. When it matches the last one applied, the response is dropped without
 *   a `setState`, so a poll that finds nothing new costs one fetch and no render.
 */
function useTreeSnapshot(workspaceId, active) {
  const [state, setState] = useState({ status: 'loading', payload: undefined, offline: false });
  const [attempt, setAttempt] = useState(0);
  // Keep the last good snapshot so a transient network failure degrades to a
  // stale view rather than an empty one — an empty view reads as "no work".
  const lastGoodRef = useRef(undefined);
  const revisionRef = useRef(undefined);

  const refresh = useCallback(() => setAttempt((value) => value + 1), []);

  useEffect(() => {
    // Suspended: no controller, no timer, no request. Whatever is already on
    // screen stays there untouched.
    if (active !== true) return undefined;
    let cancelled = false;
    let timer;
    const controller = new AbortController();

    const query = workspaceId === undefined ? '' : `?workspace=${encodeURIComponent(workspaceId)}`;

    const tick = async () => {
      let payload;
      try {
        const response = await fetch(`${TREE_PATH}${query}`, { cache: 'no-store', signal: controller.signal });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        payload = await response.json();
      } catch (error) {
        if (controller.signal.aborted || cancelled) return;
        setState((current) => ({
          // Keep whatever is on screen; only mark it as possibly stale.
          status: lastGoodRef.current === undefined ? 'error' : current.status,
          payload: lastGoodRef.current,
          offline: true,
          error: lastGoodRef.current === undefined ? String(error instanceof Error ? error.message : error) : undefined,
        }));
        timer = setTimeout(tick, Math.max(MIN_POLL_MS, DEFAULT_POLL_MS));
        return;
      }
      if (cancelled) return;
      const usable = isTreePayload(payload);
      if (usable && payload.ok !== false) lastGoodRef.current = payload;

      // Nothing changed since the last applied snapshot: keep the exact object
      // already in state so React skips the render entirely.
      if (usable && !shouldApply(revisionRef.current, payload)) {
        // Still clear a stale "offline" banner without replacing the payload.
        setState((current) => (current.offline === false ? current : { ...current, offline: false }));
        reschedule(payload);
        return;
      }
      if (usable && typeof payload.revision === 'string') revisionRef.current = payload.revision;

      setState({
        status: usable && payload.ok === false ? 'error' : 'ready',
        payload,
        offline: false,
        error: undefined,
      });
      reschedule(payload);
    };

    /** Read the cadence defensively: a body that failed the shape check has no
     * `pollSeconds`, and trusting that would throw inside the poller. */
    const reschedule = (payload) => {
      const seconds = typeof payload?.pollSeconds === 'number' && payload.pollSeconds > 0 ? payload.pollSeconds : 3;
      timer = setTimeout(tick, Math.max(MIN_POLL_MS, seconds * 1000));
    };

    void tick();
    return () => {
      cancelled = true;
      clearTimeout(timer);
      controller.abort();
    };
  }, [workspaceId, attempt, active]);

  return { ...state, refresh };
}

/**
 * Workspace list for the picker; a failure just means "no switcher".
 *
 * Read once per visibility window rather than per poll: the set of workspaces
 * changes only when the user opens a folder, so re-reading it on every tick
 * would be pure waste. Gated on `active` for the same reason as the tree itself.
 */
function useWorkspaces(active) {
  const [workspaces, setWorkspaces] = useState([]);
  useEffect(() => {
    if (active !== true) return undefined;
    const controller = new AbortController();
    void (async () => {
      try {
        const response = await fetch(WORKSPACES_PATH, { cache: 'no-store', signal: controller.signal });
        if (!response.ok) return;
        const body = await response.json();
        if (Array.isArray(body.workspaces)) setWorkspaces(body.workspaces);
      } catch {
        // Non-fatal: the current workspace still renders.
      }
    })();
    return () => controller.abort();
  }, [active]);
  return workspaces;
}

// --- derived views -------------------------------------------------------------

/** Terminal statuses hidden by default: req-tree's own "to-do horizon". */
const TERMINAL = new Set(['closed', 'dropped']);

/**
 * Flatten the render tree into indented rows.
 *
 * The render tree is walked depth-first in position order, and collapsed
 * subtrees are pruned entirely rather than rendered as a dead end. `match` is
 * applied to the whole subtree: a filter that matches one deep node keeps the
 * path that leads to it, which is the only way a filter is useful on a tree.
 */
function buildRows(nodes, { match, showTerminal }) {
  const present = new Set(nodes.map((node) => node.id));
  const childrenOf = new Map();
  const roots = [];
  for (const node of nodes) {
    // A parent that was truncated away leaves its child orphaned; showing it as
    // a root is better than silently dropping the node.
    if (node.parent === null || !present.has(node.parent)) {
      roots.push(node);
      continue;
    }
    const bucket = childrenOf.get(node.parent);
    if (bucket === undefined) childrenOf.set(node.parent, [node]);
    else bucket.push(node);
  }
  const childrenOfNode = (node) => childrenOf.get(node.id) ?? [];

  const rows = [];
  const walk = (node, depth, keepSelf) => {
    const selfMatches = keepSelf || match(node);
    // Look one level down before deciding to hide a terminal node: a closed or
    // dropped parent with unclosed work under it must still be visible, or the
    // panel would hide outstanding work behind its own finished ancestor. This
    // mirrors `todoctl`'s "a fully-closed subtree folds to one line" rule,
    // which folds on the *whole* subtree being terminal — not on the root alone.
    const kids = childrenOfNode(node);
    const keepsLiveDescendant = kids.some((child) => !TERMINAL.has(child.status) || selfMatches);
    const hiddenByDefaultView = !showTerminal && !selfMatches && TERMINAL.has(node.status) && !keepsLiveDescendant;
    if (hiddenByDefaultView) return;
    rows.push({ node, depth, folded: TERMINAL.has(node.status) && !selfMatches });
    for (const child of kids.filter((child) => selfMatches || match(child) || child.id === node.focus || keepsLiveDescendant)) {
      walk(child, depth + 1, selfMatches || match(child));
    }
  };
  for (const root of roots) walk(root, 0, false);
  return rows;
}

function ProgressMeter({ pct }) {
  return _jsx('div', { className: 'rt-meter', role: 'img', 'aria-label': `${pct}%`, children: _jsx('i', { style: { width: `${Math.max(0, Math.min(100, pct))}%` } }) });
}

/**
 * `jsx` with children, where the key is passed as its own argument.
 *
 * `jsx(type, { key, ...props })` works but React warns, because a key inside a
 * spread props object is not recognised as a real key. Passing it as the third
 * argument keeps the code warning-free while staying readable as plain JSX calls.
 */
const kid = (key, type, props, ...children) => _jsx(type, { key, ...props, children: children.length === 1 ? children[0] : children });

function NodeRow({ row, expandedId, onToggle, t }) {
  const { node, depth } = row;
  const open = expandedId === node.id;
  // `jsx(type, props, ...children)` silently drops siblings passed as extra
  // arguments — the button renders, its content does not. Collect them explicitly.
  return kid(
    node.id,
    'button',
    {
      type: 'button',
      className: 'rt-node',
      style: { paddingLeft: `${6 + depth * 12}px` },
      'data-req-tree-node': node.id,
      'data-open': open,
      'data-focus': node.focus,
      'aria-expanded': open,
      onClick: () => onToggle(node.id),
    },
    _jsx('span', { key: 'pos', className: 'rt-pos', children: node.position === '' ? '' : node.position }),
    _jsx('span', { key: 'glyph', className: 'rt-glyph', title: t(`status.${node.status}`), 'aria-hidden': 'true', children: node.glyph }),
    _jsx('span', { key: 'title', className: 'rt-title', title: node.title, children: node.title }),
    node.internal ? _jsx('span', { key: 'internal', className: 'rt-chip', children: t('tree.internal') }) : null,
    node.focus ? _jsx('span', { key: 'focus', className: 'rt-chip', children: t('tree.focus') }) : null,
    _jsx('span', { key: 'status', className: 'rt-chip', 'data-status': node.status, children: t(`status.${node.status}`) }),
  );
}

function NodeDetail({ node, t }) {
  const rows = [
    node.why === '' ? null : _jsx('div', { className: 'rt-detailRow', key: 'why', children: [_jsx('span', { className: 'rt-detailKey', children: t('detail.why') }), _jsx('span', { className: 'rt-detailVal', children: node.why })] }),
    node.doneWhen === '' ? null : _jsx('div', { className: 'rt-detailRow', key: 'doneWhen', children: [_jsx('span', { className: 'rt-detailKey', children: t('detail.doneWhen') }), _jsx('span', { className: 'rt-detailVal', children: node.doneWhen })] }),
    node.nextStep === '' ? null : _jsx('div', { className: 'rt-detailRow', key: 'nextStep', children: [_jsx('span', { className: 'rt-detailKey', children: t('detail.nextStep') }), _jsx('span', { className: 'rt-detailVal', children: node.nextStep })] }),
    node.blockedBy === '' ? null : _jsx('div', { className: 'rt-detailRow', key: 'blockedBy', children: [_jsx('span', { className: 'rt-detailKey', children: t('detail.blockedBy') }), _jsx('span', { className: 'rt-detailVal', children: node.blockedBy })] }),
    node.evidence.length === 0
      ? null
      : _jsx('div', { className: 'rt-detailRow', key: 'evidence', children: [
          _jsx('span', { className: 'rt-detailKey', children: t('detail.evidence') }),
          _jsx('ul', { className: 'rt-evidence rt-detailVal', children: node.evidence.map((item) => _jsx('li', { key: item, children: item })) }),
        ] }),
    _jsx('div', { className: 'rt-detailRow', key: 'updated', children: [
      _jsx('span', { className: 'rt-detailKey', children: t('detail.updated') }),
      _jsx('span', { className: 'rt-detailVal', children: `${node.updated}${node.staleDays > 0 ? ` · ${t('detail.stale', { days: node.staleDays })}` : ''}` }),
    ] }),
  ].filter(Boolean);

  return _jsx('div', { className: 'rt-detail', children: [
    _jsx('div', { className: 'rt-detailRow', children: [_jsx('span', { className: 'rt-detailKey', children: 'ID' }), _jsx('span', { className: 'rt-detailVal', children: `${node.id}${node.position === '' ? '' : ` (${node.position})`}` })] }),
    ...rows,
  ] });
}

function Summary({ payload, t }) {
  const { counts, unclosed, pct } = payload;
  // One metric pill per label; `null` entries are filtered so the array index
  // never becomes a React key.
  const metrics = [
    _jsx('span', { className: 'rt-metric', key: 'unclosed', children: [_jsx('b', { children: String(unclosed) }), ' ', t('summary.unclosed')] }),
    _jsx('span', { className: 'rt-metric', key: 'closed', children: [_jsx('b', { children: String(counts.closed) }), ' ', t('summary.closed')] }),
    counts.dropped > 0 ? _jsx('span', { className: 'rt-metric', key: 'dropped', children: [_jsx('b', { children: String(counts.dropped) }), ' ', t('summary.dropped')] }) : null,
    _jsx('span', { className: 'rt-metric', key: 'total', children: [_jsx('b', { children: String(counts.total) }), ' ', t('summary.total')] }),
    _jsx('span', { className: 'rt-metric', key: 'pct', children: t('summary.progress', { pct }) }),
  ].filter(Boolean);
  return _jsx('div', { className: 'rt-head', children: [
    _jsx('div', { className: 'rt-headRow', children: [
      _jsx('span', { className: 'rt-project', title: payload.project === '' ? payload.workspace?.path : payload.project, children: payload.project === '' ? (payload.workspace?.title ?? t('tab.title')) : payload.project }),
      _jsx('span', { className: 'rt-readonly', title: t('state.readonly'), children: t('state.readonly') }),
    ] }),
    _jsx('div', { className: 'rt-metrics', children: metrics }),
    _jsx(ProgressMeter, { pct }),
  ] });
}

function Lines({ payload, t }) {
  if (payload.lines.length === 0) return null;
  return _jsx('section', { className: 'rt-section', children: [
    _jsx('h3', { className: 'rt-sectionTitle', children: t('lines.title') }),
    ...payload.lines.map((line) => _jsx('div', { className: 'rt-line', key: line.id, children: [
      _jsx('div', { className: 'rt-lineHead', children: [
        _jsx('span', { className: 'rt-lineTitle', title: `${line.id} ${line.title}`, children: line.title }),
        _jsx('span', { className: 'rt-lineCount', children: `${line.closed}/${line.denominator}` }),
      ] }),
      _jsx(ProgressMeter, { pct: line.pct }),
    ] })),
  ] });
}
function Warnings({ payload, visibleIds, t }) {
  const warnings = payload.warnings ?? [];
  const shown = visibleIds === undefined ? warnings : warnings.filter((warning) => visibleIds.has(warning.id));
  const hidden = warnings.length - shown.length;
  return _jsx('section', { className: 'rt-section', children: [
    _jsx('h3', { className: 'rt-sectionTitle', children: `${t('warnings.title')} (${shown.length})` }),
    shown.length === 0
      ? _jsx('p', { className: 'rt-empty', children: hidden > 0 ? t('warnings.hidden', { count: hidden }) : t('warnings.empty') })
      : _jsx('div', { className: 'rt-tree', children: shown.map((warning) => kid(
          `${warning.code}:${warning.id}`,
          'button',
          {
            type: 'button',
            className: 'rt-warning',
            title: `${warning.id}${warning.path === '' ? '' : ` (${warning.path})`}`,
            onClick: () => document.dispatchEvent(new CustomEvent('req-tree:reveal', { detail: { id: warning.id } })),
          },
          _jsx('span', { key: 'code', className: 'rt-wcode', children: warning.code }),
          _jsx('span', { key: 'msg', className: 'rt-wmsg', children: warning.message }),
        )) }),
  ] });
}

function Journal({ payload, t }) {
  const entries = Array.isArray(payload.journal) ? payload.journal : [];
  if (entries.length === 0) return null;
  return _jsx('section', { className: 'rt-section', children: [
    _jsx('h3', { className: 'rt-sectionTitle', children: t('journal.title') }),
    _jsx('div', { className: 'rt-journal', children: entries.map((entry, index) => _jsx('div', { className: 'rt-jrow', key: `${entry.ts}:${index}`, children: [
      _jsx('span', { className: 'rt-jts', children: entry.ts }),
      _jsx('span', { className: 'rt-jop', children: typeof entry.id === 'string' ? `${entry.id} ${t(`op.${entry.op}`) ?? entry.op}` : t(`op.${entry.op}`) ?? entry.op }),
      typeof entry.detail === 'string' && entry.detail !== '' ? _jsx('span', { className: 'rt-jdetail', title: entry.detail, children: entry.detail }) : null,
    ] })) }),
  ] });
}

function Source({ payload, t }) {
  if (typeof payload.root !== 'string') return null;
  return _jsx('section', { className: 'rt-section', children: [
    _jsx('h3', { className: 'rt-sectionTitle', children: t('source.title') }),
    _jsx('div', { className: 'rt-source', children: [_jsx('code', { children: payload.root })] }),
    payload.legacy === true ? _jsx('p', { className: 'rt-note', children: t('source.legacy') }) : null,
  ] });
}

/**
 * Decide which view the panel shows. Pure, so the "never lie" rules are
 * testable without a browser or a running event loop.
 *
 * The critical distinction is between "we do not know yet" and "there is
 * nothing here". A missing tree, a corrupt one, and an empty one are three
 * different facts; collapsing them into one empty view would tell the user their
 * project is finished when in fact the tool could not read it.
 *
 * @returns `loading` | `offline` | `failed` | `empty` | `ready`.
 */
export function selectView(state) {
  if (state.payload === undefined) return state.status === 'loading' ? 'loading' : 'offline';
  if (state.payload.ok === false) return 'failed';
  if (!Array.isArray(state.payload.nodes) || state.payload.nodes.length === 0) return 'empty';
  return 'ready';
}

/** The tab body. Registered against `sidebar.right.pane.tab`. */
export function ReqTreeTab(props = {}) {
  ensureStyle();
  // The host injects the translated function under several names depending on
  // the slot's prop contract; accept whichever one it used, and fall back to the
  // bundled `en` dictionary so the panel is never rendered with raw keys.
  const boundTranslate = props.t ?? props.translate ?? props.tr;
  const useTabInfo = props.useTabInfo;
  const t = typeof boundTranslate === 'function'
    ? boundTranslate
    : (key, params) => {
        const value = en[key];
        if (typeof value !== 'string') return key;
        return value.replace(/\{(\w+)\}/g, (_match, name) => String(params?.[name] ?? `{${name}}`));
      };

  // Read the adopted session's workspace so the panel follows the tab it is
  // docked on, instead of always showing whatever workspace was registered first.
  const tabInfo = useTabInfo?.();
  // The sidebar reports whether this tab's body is actually on screen. It is
  // false when the column is collapsed, when a sibling tab is selected, and for
  // every background Session — so it is the gate that keeps an unwatched panel
  // from polling at all. A host that does not report it is treated as visible,
  // because guessing "hidden" would leave the panel permanently blank.
  const visible = tabInfo?.tab?.visible !== false;
  const [workspaceId, setWorkspaceId] = useState(undefined);
  const workspaces = useWorkspaces(visible);
  const { status, payload, offline, refresh } = useTreeSnapshot(workspaceId, visible);
  const [query, setQuery] = useState('');
  const [showTerminal, setShowTerminal] = useState(false);
  const [expandedId, setExpandedId] = useState(undefined);

  // Default the picker to the tab's own workspace once it is known.
  useEffect(() => {
    if (workspaceId !== undefined || workspaces.length === 0) return;
    setWorkspaceId(tabInfo?.sidebar?.mounted?.getSnapshot?.()?.workspaceId ?? workspaces[0].id);
  }, [workspaceId, workspaces, tabInfo]);

  // A warning chip can ask the tree to reveal a node it points at.
  useEffect(() => {
    const reveal = (event) => {
      const id = event.detail?.id;
      if (typeof id !== 'string') return;
      setExpandedId(id);
      setShowTerminal(true);
      document.querySelector(`[data-req-tree-node="${CSS.escape(id)}"]`)?.scrollIntoView({ block: 'nearest' });
    };
    document.addEventListener('req-tree:reveal', reveal);
    return () => document.removeEventListener('req-tree:reveal', reveal);
  }, []);

  const onToggle = useCallback((id) => setExpandedId((current) => (current === id ? undefined : id)), []);

  const needle = query.trim().toLowerCase();
  const match = useMemo(() => {
    if (needle === '') return () => true;
    return (node) => node.title.toLowerCase().includes(needle) || node.id.toLowerCase().includes(needle) || node.position.includes(needle);
  }, [needle]);

  const rows = useMemo(
    () => (payload && payload.ok !== false ? buildRows(payload.nodes, { match, showTerminal }) : []),
    [payload, match, showTerminal],
  );
  const visibleIds = useMemo(() => new Set(rows.map((row) => row.node.id)), [rows]);

  const view = selectView({ status, payload });

  if (view === 'loading') {
    return _jsx('div', { className: 'rt-root', children: _jsx('div', {
      className: 'rt-skeleton',
      'aria-label': t('state.loading'),
      role: 'status',
      children: [_jsx('i', { key: 'a' }), _jsx('i', { key: 'b' }), _jsx('i', { key: 'c' })],
    }) });
  }

  if (view === 'offline') {
    return _jsx('div', { className: 'rt-root', children: [
      _jsx('div', { className: 'rt-error', role: 'alert', children: [
        _jsx('strong', { children: t('state.error') }),
        _jsx('p', { children: t('state.offline') }),
        _jsx('button', { type: 'button', onClick: refresh, children: t('state.retry') }),
      ] }),
    ] });
  }

  if (view === 'failed' || view === 'empty') {
    const failed = view === 'failed';
    const code = payload.error?.code;
    return _jsx('div', { className: 'rt-root', children: [
      failed
        ? _jsx('div', { className: 'rt-error', role: 'alert', children: [
            _jsx('strong', { children: code === 'no-workspace' ? t('state.noWorkspace') : t('state.error') }),
            _jsx('p', { children: payload.error?.message ?? '' }),
            _jsx('button', { type: 'button', onClick: refresh, children: t('state.retry') }),
          ] })
        : _jsx('p', { className: 'rt-empty', children: t('tree.empty') }),
      _jsx(Source, { payload, t }),
    ] });
  }

  return _jsx('div', { className: 'rt-root', 'data-req-tree': true, children: [
    _jsx(Summary, { payload, t }),
    offline ? _jsx('p', { className: 'rt-note', role: 'status', children: t('state.offline') }) : null,
    workspaces.length > 1
      ? _jsx('label', { className: 'rt-headRow', children: [
          _jsx('span', { className: 'rt-detailKey', children: t('switcher.label') }),
          _jsx('select', {
            className: 'rt-search',
            value: workspaceId ?? '',
            onChange: (event) => { setWorkspaceId(event.target.value); setExpandedId(undefined); },
          }, workspaces.map((workspace) => _jsx('option', { value: workspace.id, key: workspace.id, children: workspace.title === '' ? workspace.path : workspace.title }))),
        ] })
      : null,
    _jsx(Lines, { payload, t }),
    _jsx('section', { className: 'rt-section', children: [
      _jsx('h3', { className: 'rt-sectionTitle', children: t('tree.title') }),
      _jsx('div', { className: 'rt-headRow', children: [
        _jsx('input', {
          className: 'rt-search',
          type: 'search',
          value: query,
          placeholder: t('tree.search'),
          'aria-label': t('tree.search'),
          onChange: (event) => setQuery(event.target.value),
        }),
        _jsx('button', { type: 'button', className: 'rt-toggle', onClick: () => setShowTerminal((value) => !value), children: showTerminal ? t('tree.showTerminal') : t('tree.showClosed') }),
      ] }),
      payload.truncated ? _jsx('p', { className: 'rt-note', children: t('tree.truncated', { shown: rows.length, total: payload.counts.total }) }) : null,
      rows.length === 0
        ? _jsx('p', { className: 'rt-empty', children: needle === '' ? t('tree.empty') : t('tree.search') })
        : _jsx('div', { className: 'rt-tree', children: rows.map((row) => kid(
            row.node.id,
            _Fragment,
            {},
            _jsx(NodeRow, { key: 'row', row, expandedId, onToggle, t }),
            expandedId === row.node.id ? _jsx(NodeDetail, { key: 'detail', node: row.node, t }) : null,
          )) }),
    ] }),
    _jsx(Warnings, { payload, visibleIds, t }),
    _jsx(Journal, { payload, t }),
    _jsx(Source, { payload, t }),
  ] });
}

/**
 * Client entry point: mounts the req-tree tab into the right sidebar.
 *
 * The tab *type* (which row appears in the sidebar's tab strip) is registered
 * through `ctx.sidebarRightTabs`; the tab *body* is registered separately
 * against `sidebar.right.pane.tab` under the same id. Both are optional-scope
 * so a host without the right sidebar keeps booting — the plugin simply never
 * gains a tab, instead of blocking on a service that will never arrive.
 *
 * @module dsh-req-tree/client-entry
 */

/** Services this half uses when the host provides them. */
/**
 * Client-side service dependencies.
 *
 * These are *service* names, not package names: locale and slots are Cordis
 * services every host provides, while the right-sidebar pair is resolved
 * optionally in pply so a host without a right sidebar still boots.
 */
export const inject = ['slots', 'locale'];

export function apply(ctx) {
  ctx.effect(() => ctx.locale.register(LOCALE_NAMESPACE, { zh, en }), 'req-tree: dictionaries');

  ctx.inject(['sidebarRight', 'sidebarRightTabs'], (native) => {
    const t = native.locale.bind(LOCALE_NAMESPACE);
    native.effect(() => native.sidebarRightTabs.register({
      id: TAB_ID,
      kind: TAB_KIND,
      title: () => t('tab.title'),
      guide: [{ id: 'req-tree-open', title: () => t('tab.title'), description: () => t('tab.guide') }],
    }), 'req-tree: tab type');

    native.slots.inject('sidebar.right.pane.tab', () => native.slots.register({
      name: 'sidebar.right.pane.tab',
      key: TAB_ID,
      locale: LOCALE_NAMESPACE,
    }, ReqTreeTab));
  });
}


