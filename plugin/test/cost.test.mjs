/**
 * Cost tests: an unwatched panel must do no work.
 *
 * The panel is mounted per Session and the right sidebar keeps background tabs
 * alive, so a naive implementation polls (and re-renders) forever for a tree the
 * user is not looking at. These tests pin the two gates that prevent it: the
 * visibility gate (no request at all) and the revision gate (no render at all).
 */
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { test } from 'node:test';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { JSDOM } from 'jsdom';
import { createElement, act } from 'react';
import { createRoot } from 'react-dom/client';
import { projectTree } from '../lib/project.js';
import { ReqTreeTab, shouldApply, zh } from '../lib/client.js';

const here = dirname(fileURLToPath(import.meta.url));
const fixture = JSON.parse(await readFile(join(here, 'fixtures', 'tree.json'), 'utf8'));

const t = (key, params) => {
  const value = zh[key];
  if (typeof value !== 'string') return key;
  return value.replace(/\{(\w+)\}/g, (_match, name) => String(params?.[name] ?? `{${name}}`));
};

const REVISION_A = 'tree.json:100:512|journal.jsonl:200:64';
const REVISION_B = 'tree.json:999:513|journal.jsonl:200:64';

const body = {
  pollSeconds: 3,
  revision: REVISION_A,
  ok: true,
  workspace: { id: 'w1', title: 'demo', path: '/tmp/demo' },
  root: '/tmp/demo/docs/requirements',
  origin: 'git repository detected',
  legacy: false,
  journal: [],
  ...projectTree(fixture),
};

async function withGlobals(dom, overrides, run) {
  const keys = ['window', 'document', 'navigator', 'fetch', 'IS_REACT_ACT_ENVIRONMENT'];
  const previous = new Map(keys.map((key) => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
  const apply = (values) => {
    for (const [key, descriptor] of previous) {
      if (descriptor === undefined) delete globalThis[key];
      else Object.defineProperty(globalThis, key, descriptor);
    }
    for (const [key, value] of Object.entries(values)) {
      Object.defineProperty(globalThis, key, { value, configurable: true, writable: true });
    }
  };
  apply({
    window: dom.window,
    document: dom.window.document,
    navigator: dom.window.navigator,
    IS_REACT_ACT_ENVIRONMENT: true,
    ...overrides,
  });
  try {
    return await run();
  } finally {
    apply({});
  }
}

const settle = async () => {
  for (let turn = 0; turn < 5; turn += 1) {
    await act(async () => { await new Promise((resolve) => setImmediate(resolve)); });
  }
};

/** Mount with a counting fetch and the given tab visibility. */
async function mountWith({ visible, payload = body }) {
  const dom = new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>', { url: 'http://localhost/' });
  const timer = dom.window.setInterval(() => {}, 1e9);
  const urls = [];
  try {
    return await withGlobals(
      dom,
      {
        fetch: async (url) => {
          urls.push(String(url));
          return { ok: true, json: async () => payload };
        },
      },
      async () => {
        const container = dom.window.document.getElementById('root');
        const root = createRoot(container);
        await act(async () => {
          root.render(createElement(ReqTreeTab, {
            t,
            useTabInfo: () => ({ tab: { visible }, sidebar: { mounted: { getSnapshot: () => undefined } } }),
          }));
        });
        await settle();
        const html = container.innerHTML;
        await act(async () => { root.unmount(); });
        return { html, urls };
      },
    );
  } finally {
    dom.window.clearInterval(timer);
    dom.window.close();
  }
}

test('an invisible tab issues no request at all', async () => {
  const { urls, html } = await mountWith({ visible: false });
  // The whole point: a background Session, a collapsed column, or a sibling tab
  // being selected must cost nothing.
  assert.deepEqual(urls, [], `expected zero requests while hidden, got ${JSON.stringify(urls)}`);
  // Nothing was ever loaded, so the panel must be honest about that.
  assert.ok(html.includes('rt-skeleton'), 'a hidden, never-loaded panel shows the loading state');
  assert.ok(!html.includes('计价模块'), 'it must not invent a tree it never read');
});

test('a visible tab loads the tree', async () => {
  const { urls, html } = await mountWith({ visible: true });
  assert.ok(urls.length > 0, 'a visible tab must fetch');
  assert.ok(urls.some((url) => url.includes('/plugins/dsh-req-tree/tree')));
  assert.ok(html.includes('计价模块'));
});

test('a host without a visibility signal is treated as visible', async () => {
  // Guessing "hidden" would leave the panel permanently blank on an older host;
  // guessing "visible" merely costs the old polling behaviour.
  const { urls } = await mountWith({ visible: undefined });
  assert.ok(urls.length > 0, 'an unknown visibility must not suppress loading');
});

test('an unchanged revision is a no-op, a changed one is applied', () => {
  // The first response always applies: there is nothing on screen yet.
  assert.equal(shouldApply(undefined, { ...body, revision: REVISION_A }), true);
  // Same revision -> nothing changed on disk -> skip the render.
  assert.equal(shouldApply(REVISION_A, { ...body, revision: REVISION_A }), false);
  // Any difference in either watched file counts as a change.
  assert.equal(shouldApply(REVISION_A, { ...body, revision: REVISION_B }), true);
  // The reverse direction must not be sticky either.
  assert.equal(shouldApply(REVISION_B, { ...body, revision: REVISION_A }), true);
});

test('a body without a revision is always applied', () => {
  // An unversioned response carries no evidence of being unchanged. Treating
  // "unknown" as "unchanged" would freeze the panel against a host that omits
  // the field, so it must degrade to always applying.
  assert.equal(shouldApply(REVISION_A, { ...body, revision: undefined }), true);
  assert.equal(shouldApply(undefined, { ...body, revision: undefined }), true);
});

test('a malformed body is surfaced rather than silently skipped', () => {
  // The revision gate must never swallow an error state: an unreadable tree has
  // to replace the screen, not be mistaken for "nothing changed".
  assert.equal(shouldApply(REVISION_A, { ok: false, error: { code: 'invalid-json' } }), true);
  assert.equal(shouldApply(REVISION_A, undefined), true);
  assert.equal(shouldApply(REVISION_A, 'not json'), true);
});

test('both halves agree on the revision field name', async () => {
  // If the host stops sending `revision` or the client stops reading it, the
  // gate silently degrades to "always render" — no error, just the old cost.
  const host = await readFile(join(here, '..', 'lib', 'index.js'), 'utf8');
  const client = await readFile(join(here, '..', 'lib', 'client.js'), 'utf8');
  assert.ok(/revision/.test(host), 'the host must include a revision');
  assert.ok(/fingerprint/.test(host), 'the host must fingerprint the source of truth');
  assert.ok(/shouldApply\(revisionRef\.current, payload\)/.test(client), 'the client must consult the revision gate');
  assert.ok(/tab\?\.visible/.test(client), 'the client must gate on tab visibility');
  assert.ok(/active !== true/.test(client), 'the poller must suspend when inactive');
});