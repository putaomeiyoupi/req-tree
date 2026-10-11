/**
 * The pure projection: raw `tree.json` -> the render model the panel draws.
 *
 * Kept dependency-free (no schemastery, no filesystem, no host services) so it
 * can be unit-tested directly and so the browser half never has to re-derive
 * req-tree's rules. Every constant below is a deliberate port of `todoctl.py`;
 * if the CLI changes a threshold, this file must change with it, because a
 * panel that disagrees with its own CLI is worse than no panel.
 */

/** todoctl.py STATUSES / ACTIVE_STATUSES / TERMINAL_STATUSES. */
export const STATUSES = ['open', 'doing', 'blocked', 'done', 'closed', 'dropped'];
const TERMINAL_STATUSES = new Set(['closed', 'dropped']);

/** todoctl.py GLYPH — the panel speaks the same visual dialect as the CLI views. */
export const GLYPH = {
  open: '○',
  doing: '▶',
  blocked: '⛔',
  done: '✔',
  closed: '✅',
  dropped: '⏹',
};

/** todoctl.py MAX_DEPTH / MAX_OPEN_SIBLINGS / STALE_DAYS / STALE_DOING_DAYS. */
const MAX_DEPTH = 3;
const MAX_OPEN_SIBLINGS = 7;
const STALE_DAYS = 14;
const STALE_DOING_DAYS = 7;

/** A `doing` node whose declared next is not started is *detoured* (W6), not blocked. */
const NOT_STARTED = new Set(['open', 'blocked']);

/**
 * Whole days since a naive local `YYYY-MM-DD HH:MM:SS` stamp, or undefined when
 * the stamp is absent/unparseable. req-tree keeps all timestamps in local time
 * at second precision, so they are parsed as local time here too — parsing them
 * as UTC would silently shift staleness by a day.
 */
export function daysSince(stamp, now = Date.now()) {
  if (typeof stamp !== 'string' || stamp === '') return undefined;
  const parsed = Date.parse(stamp.replace(' ', 'T'));
  if (Number.isNaN(parsed)) return undefined;
  return Math.floor((now - parsed) / 86_400_000);
}

/**
 * Build a render model from a parsed source of truth.
 *
 * A missing, foreign or self-referential `parent` degrades to "top level"
 * rather than dropping the node: a panel must never hide work just because one
 * edge in the data is broken. Parent cycles are walked cycle-safely.
 *
 * @param data - the parsed `tree.json`.
 * @param options.maxNodes - cap on returned nodes; the cap is announced, and
 *   the headline counts stay whole-tree so they never imply a smaller tree.
 */
export function projectTree(data, { maxNodes = 5000, now = Date.now() } = {}) {
  const all = (Array.isArray(data?.nodes) ? data.nodes : []).filter((node) => typeof node?.id === 'string');
  const creationOrder = new Map(all.map((node, index) => [node.id, index]));

  // First definition wins on a duplicate ID (E11): showing one of them beats
  // showing the same node twice and double-counting its subtree.
  const byId = new Map();
  for (const node of all) if (!byId.has(node.id)) byId.set(node.id, node);

  const childrenOf = new Map();
  const roots = [];
  const childrenOfNode = (node) => childrenOf.get(node.id) ?? [];
  for (const node of byId.values()) {
    const parent = node.parent;
    if (typeof parent === 'string' && parent !== '' && parent !== node.id && byId.has(parent)) {
      const bucket = childrenOf.get(parent);
      if (bucket === undefined) childrenOf.set(parent, [node]);
      else bucket.push(node);
    } else {
      roots.push(node);
    }
  }
  // Creation order is the documented sibling order (references/schema.md §4).
  const byCreation = (left, right) => creationOrder.get(left.id) - creationOrder.get(right.id);
  for (const bucket of childrenOf.values()) bucket.sort(byCreation);
  roots.sort(byCreation);

  /** Ancestors root-first, cycle-safe. */
  const ancestorsOf = (node) => {
    const chain = [];
    const seen = new Set([node.id]);
    let cursor = node.parent;
    while (typeof cursor === 'string' && cursor !== '' && byId.has(cursor) && !seen.has(cursor)) {
      seen.add(cursor);
      const parent = byId.get(cursor);
      chain.push(parent);
      cursor = parent.parent;
    }
    return chain.reverse();
  };

  // Position numbers are a *view*, recomputed every render — the ID stays the
  // identity, so inserting a node shifts `1.3.2` and nothing else breaks.
  const positions = new Map();
  const assign = (node, prefix) => {
    if (prefix !== '') positions.set(node.id, prefix);
    childrenOfNode(node).forEach((child, index) => {
      assign(child, prefix === '' ? String(index + 1) : `${prefix}.${index + 1}`);
    });
  };
  roots.forEach((root, index) => assign(root, String(index + 1)));

  // --- subtree rollups --------------------------------------------------------
  const emptyCounts = () => ({ total: 0, closed: 0, dropped: 0, open: 0, doing: 0, blocked: 0, done: 0 });
  const statusOf = (node) => (STATUSES.includes(node.status) ? node.status : 'open');

  const ownCounts = new Map();
  const rollup = new Map();
  // Post-order (children before parents) so a parent's own entry is complete
  // before it is folded into *its* parent.
  const fold = (node) => {
    for (const child of childrenOfNode(node)) fold(child);
    const total = { ...emptyCounts() };
    total.total += 1;
    total[statusOf(node)] += 1;
    for (const child of childrenOfNode(node)) {
      const childCounts = ownCounts.get(child.id);
      for (const key of Object.keys(total)) total[key] += childCounts[key];
    }
    ownCounts.set(node.id, total);
    const aggregate = { ...total };
    rollup.set(node.id, aggregate);
  };
  for (const root of roots) fold(root);

  // --- warnings (todoctl `check`: W-codes never block, E-codes do) ------------
  const warnings = [];
  const push = (code, node, message) => warnings.push({ code, id: node.id, path: positions.get(node.id) ?? '', message });

  for (const node of byId.values()) {
    // An internal node is stripped from exports along with its whole subtree;
    // mirroring that here keeps the panel from surfacing a line the project
    // itself considers non-shareable.
    if (node.internal === true) continue;

    const chain = ancestorsOf(node);
    const depth = chain.length;
    const live = !TERMINAL_STATUSES.has(node.status);

    if (live && depth > MAX_DEPTH) push('W1', node, `depth ${depth} exceeds the ${MAX_DEPTH}-level navigation guidance`);

    const openSiblings = childrenOfNode(node).filter((child) => !TERMINAL_STATUSES.has(child.status));
    if (live && openSiblings.length > MAX_OPEN_SIBLINGS) {
      push('W2', node, `${openSiblings.length} unclosed siblings (guidance limit ${MAX_OPEN_SIBLINGS})`);
    }

    const unfinishedDeps = (Array.isArray(node.depends_on) ? node.depends_on : [])
      .filter((id) => byId.has(id) && !TERMINAL_STATUSES.has(byId.get(id).status));
    if (node.status === 'doing' && unfinishedDeps.length > 0) {
      push('W3', node, `working while a dependency is unclosed: ${unfinishedDeps.join(', ')}`);
    }

    const stale = daysSince(node.updated, now);
    if (node.status === 'doing') {
      if (stale !== undefined && stale > STALE_DOING_DAYS) push('W5', node, `in progress but untouched for ${stale} days`);
    } else if (live && stale !== undefined && stale > STALE_DAYS) {
      push('W4', node, `not updated for ${stale} days`);
    }

    const nextId = typeof node.next === 'string' ? node.next : '';
    if (nextId !== '') {
      const next = byId.get(nextId);
      if (next === undefined) push('E15', node, `declared next ${nextId} does not exist`);
      else if (nextId === node.id) push('E16', node, '`next` points at itself');
      else if (TERMINAL_STATUSES.has(next.status)) {
        if (live) push('W8', node, `declared next ${nextId} is already ${next.status} — the plan no longer holds`);
      } else if (node.status === 'doing' && NOT_STARTED.has(next.status)) {
        push('W6', node, `detoured: declared next ${nextId} has not started`);
      }
    }

    // W7: an ancestor finished while this node is still in flight. `close` was
    // blocked from this moment on; the tree just cannot show it as an error.
    if (node.status === 'doing' || node.status === 'done') {
      const finished = chain.filter((ancestor) => TERMINAL_STATUSES.has(ancestor.status));
      const last = finished[finished.length - 1];
      if (last !== undefined) push('W7', node, `ancestor ${last.id} is ${last.status} while this is still ${node.status}`);
    }

    // W9: `drop` does not cascade, so a dropped parent can still hold live work.
    if (node.status === 'dropped') {
      const orphans = childrenOfNode(node).filter((child) => !TERMINAL_STATUSES.has(child.status));
      if (orphans.length > 0) push('W9', node, `${orphans.length} unclosed children hang under a dropped parent — ownership undefined`);
    }
  }

  // --- the renderable node list ------------------------------------------------
  const truncated = all.length > maxNodes;
  const visible = truncated ? all.slice(0, maxNodes) : all;
  const visibleIds = new Set(visible.map((node) => node.id));

  const nodes = visible.map((node) => ({
    id: node.id,
    parent: typeof node.parent === 'string' && visibleIds.has(node.parent) ? node.parent : null,
    position: positions.get(node.id) ?? '',
    depth: ancestorsOf(node).length,
    kind: typeof node.kind === 'string' ? node.kind : 'task',
    title: typeof node.title === 'string' ? node.title : node.id,
    status: statusOf(node),
    glyph: GLYPH[statusOf(node)],
    why: typeof node.why === 'string' ? node.why : '',
    doneWhen: typeof node.done_when === 'string' ? node.done_when : '',
    nextStep: typeof node.next_step === 'string' ? node.next_step : '',
    blockedBy: typeof node.blocked_by === 'string' ? node.blocked_by : '',
    evidence: Array.isArray(node.close_evidence) ? node.close_evidence.filter((item) => typeof item === 'string') : [],
    updated: typeof node.updated === 'string' ? node.updated : '',
    staleDays: daysSince(node.updated, now) ?? 0,
    focus: data.focus === node.id,
    internal: node.internal === true,
    rollup: rollup.get(node.id) ?? emptyCounts(),
  }));

  // --- progress per top-level requirement line ---------------------------------
  // `progress = closed / (total - dropped)`: a dropped node must not advance a
  // line, because "we decided not to build it" is not progress.
  const lines = roots
    .filter((root) => visibleIds.has(root.id))
    .map((root) => {
      const counts = rollup.get(root.id) ?? emptyCounts();
      const denominator = counts.total - counts.dropped;
      return {
        id: root.id,
        title: typeof root.title === 'string' ? root.title : root.id,
        total: counts.total,
        closed: counts.closed,
        dropped: counts.dropped,
        unclosed: counts.open + counts.doing + counts.blocked + counts.done,
        denominator,
        pct: denominator <= 0 ? 100 : Math.round((counts.closed / denominator) * 100),
      };
    });

  const counts = emptyCounts();
  for (const node of byId.values()) {
    counts.total += 1;
    counts[statusOf(node)] += 1;
  }
  const denominator = counts.total - counts.dropped;

  return {
    project: typeof data.project === 'string' ? data.project : '',
    focus: typeof data.focus === 'string' ? data.focus : '',
    lastResume: typeof data.last_resume === 'string' ? data.last_resume : '',
    updated: typeof data.updated === 'string' ? data.updated : '',
    counts,
    unclosed: counts.open + counts.doing + counts.blocked + counts.done,
    pct: denominator <= 0 ? 100 : Math.round((counts.closed / denominator) * 100),
    lines,
    warnings: warnings.filter((warning) => visibleIds.has(warning.id)),
    nodes,
    truncated,
    maxNodes,
  };
}
