/**
 * Contract tests for the host half's projection.
 *
 * `projectTree` is pure, so the whole render model is testable without a
 * workspace, a filesystem, or a browser. These cases pin the two things that
 * must never silently drift from req-tree: the numbering (positions are a view,
 * IDs are identity) and the per-line progress denominator (a dropped node must
 * not advance a line).
 */
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { test } from 'node:test';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { projectTree } from '../lib/project.js';

const here = dirname(fileURLToPath(import.meta.url));
const fixture = JSON.parse(await readFile(join(here, 'fixtures', 'tree.json'), 'utf8'));
const project = (data = fixture, options) => projectTree(data, options);

test('positions follow rendered order, and IDs stay the identity', () => {
  const { nodes } = project();
  const position = (id) => nodes.find((node) => node.id === id).position;
  assert.equal(position('R-0001'), '1');
  assert.equal(position('R-0002'), '1.1');
  assert.equal(position('R-0003'), '1.1.1');
  assert.equal(position('R-0004'), '1.1.2');
  assert.equal(position('R-0007'), '1.1.3');
  assert.equal(position('R-0005'), '2');
  assert.equal(position('R-0006'), '2.1');
});

test('a node whose parent is missing becomes a root instead of vanishing', () => {
  const { nodes } = project({
    nodes: [
      { id: 'R-0001', parent: 'R-9999', title: '孤儿', status: 'open', kind: 'task' },
      { id: 'R-0002', parent: null, title: '正常根', status: 'open', kind: 'task' },
    ],
  });
  assert.equal(nodes.length, 2);
  assert.equal(nodes[0].parent, null);
});

test('a parent cycle does not hang the projection', () => {
  const { nodes } = project({
    nodes: [
      { id: 'R-0001', parent: 'R-0002', title: 'A', status: 'open', kind: 'task' },
      { id: 'R-0002', parent: 'R-0001', title: 'B', status: 'open', kind: 'task' },
    ],
  });
  assert.equal(nodes.length, 2);
});

test('per-line progress counts closed over (total - dropped)', () => {
  const { lines, pct, counts } = project();
  const contract = lines.find((line) => line.id === 'R-0001');
  // R-0001 subtree: R-0001 closed, R-0002 doing, R-0003 blocked, R-0004 open, R-0007 closed
  assert.equal(contract.total, 5);
  assert.equal(contract.closed, 2);
  assert.equal(contract.denominator, 5);
  assert.equal(contract.pct, 40);

  // R-0005 subtree: R-0005 dropped, R-0006 open -> 0 closed over (2 - 1) = 1
  const docs = lines.find((line) => line.id === 'R-0005');
  assert.equal(docs.denominator, 1);
  assert.equal(docs.pct, 0);

  // Whole tree: 7 nodes, 2 closed, 1 dropped -> 2 / 6
  assert.equal(counts.total, 7);
  assert.equal(counts.closed, 2);
  assert.equal(counts.dropped, 1);
  assert.equal(pct, 33);
});

test('unclosed excludes terminal states so the headline stays actionable', () => {
  const { unclosed, counts } = project();
  assert.equal(unclosed, counts.open + counts.doing + counts.blocked + counts.done);
  assert.equal(unclosed, 4);
});

test('a dropped parent holding unclosed children raises W9', () => {
  const codes = project().warnings.map((warning) => warning.code);
  assert.ok(codes.includes('W9'), `expected W9, got ${codes.join(',')}`);
});

test('a doing node whose declared next has not started raises W6', () => {
  // R-0002 is `doing` and declares `next: R-0004` (still `open` -> not started).
  const detour = project().warnings.filter((warning) => warning.id === 'R-0002');
  assert.ok(detour.some((warning) => warning.code === 'W6'), `expected W6, got ${detour.map((w) => w.code).join(',')}`);
});

test('a doing node whose declared next is already terminal raises W8, not W6', () => {
  // R-0003 is `blocked` and declares `next: R-0005`, which was dropped: the plan
  // is void rather than merely detoured, and the two must not be conflated.
  const codes = project().warnings.filter((warning) => warning.id === 'R-0003').map((warning) => warning.code);
  assert.ok(codes.includes('W8'), `expected W8, got ${codes.join(',')}`);
  assert.ok(!codes.includes('W6'));
});

test('a doing node idle past the doing threshold raises W5, not W4', () => {
  const warnings = project().warnings.filter((warning) => warning.id === 'R-0002');
  assert.ok(warnings.some((warning) => warning.code === 'W5'));
  assert.ok(!warnings.some((warning) => warning.code === 'W4'));
});

test('a terminal node with unclosed children raises W7 while still in flight', () => {
  const warnings = project().warnings.filter((warning) => warning.id === 'R-0002');
  assert.ok(warnings.some((warning) => warning.code === 'W7'));
});

test('truncation is announced rather than silently shrinking the tree', () => {
  const many = Array.from({ length: 12 }, (_, index) => ({
    id: `R-${String(index + 1).padStart(4, '0')}`,
    parent: null,
    title: `n${index}`,
    status: 'open',
    kind: 'task',
  }));
  const view = projectTree({ nodes: many }, { maxNodes: 5 });
  assert.equal(view.truncated, true);
  assert.equal(view.nodes.length, 5);
  // Counts stay whole-tree counts, so the headline never implies "only 5 exist".
  assert.equal(view.counts.total, 12);
});

test('unknown statuses degrade to open instead of breaking the counts', () => {
  const { nodes, counts } = project({
    nodes: [{ id: 'R-0001', parent: null, title: 'x', status: 'nonsense', kind: 'task' }],
  });
  assert.equal(nodes[0].status, 'open');
  assert.equal(counts.open, 1);
});

test('a missing notes/evidence field does not become an empty-string lie', () => {
  const { nodes } = project({
    nodes: [{ id: 'R-0001', parent: null, title: 'x', status: 'closed', kind: 'task' }],
  });
  assert.deepEqual(nodes[0].evidence, []);
  assert.equal(nodes[0].why, '');
});