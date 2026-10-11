/**
 * Panel tests: does the tab render the tree, and does it refuse to lie?
 *
 * The interesting cases are the ones where a naive viewer would mislead: a
 * missing tree must not look like a clean one, a 200 carrying an error body must
 * not render as "0 nodes", and the default view must never hide live work
 * behind its own finished ancestor.
 *
 * The panel calls the jsx runtime directly instead of using JSX, so it can be
 * imported and rendered here without a bundler step.
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
import { LOCALE_NAMESPACE, ReqTreeTab, TAB_ID, TAB_KIND, en, zh } from '../lib/client.js';

const here = dirname(fileURLToPath(import.meta.url));
const fixture = JSON.parse(await readFile(join(here, 'fixtures', 'tree.json'), 'utf8'));

/** A translate function with the same shape the host's `locale.bind` returns. */
const t = (key, params) => {
  const value = zh[key];
  if (typeof value !== 'string') return key;
  return value.replace(/\{(\w+)\}/g, (_match, name) => String(params?.[name] ?? `{${name}}`));
};

const useTabInfo = () => ({ sidebar: { mounted: { getSnapshot: () => undefined } } });

const shell = (nodes) => ({
  pollSeconds: 3,
  ok: true,
  workspace: { id: 'w1', title: 'demo', path: '/tmp/demo' },
  root: '/tmp/demo/docs/requirements',
  origin: 'git repository detected',
  legacy: false,
  journal: [],
  ...projectTree(nodes ?? fixture),
});

/**
 * Swap a global for the duration of `run`, restoring it afterwards.
 *
 * `navigator` is an accessor-only property on modern Node, so plain assignment
 * throws; `defineProperty` replaces the descriptor instead.
 */
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

/** Let the mocked fetch promise chain settle inside `act`. */
async function settle() {
  for (let turn = 0; turn < 5; turn += 1) {
    await act(async () => { await new Promise((resolve) => setImmediate(resolve)); });
  }
}

async function mount(responseBody, { fetchImpl } = {}) {
  const dom = new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>', { url: 'http://localhost/' });
  // A timer that never fires keeps the poll from rescheduling during the test.
  const timer = dom.window.setInterval(() => {}, 1e9);
  try {
    return await withGlobals(
      dom,
      { fetch: fetchImpl ?? (async () => ({ ok: true, json: async () => responseBody })) },
      async () => {
        const container = dom.window.document.getElementById('root');
        const root = createRoot(container);
        await act(async () => { root.render(createElement(ReqTreeTab, { t, useTabInfo })); });
        await settle();
        const html = container.innerHTML;
        await act(async () => { root.unmount(); });
        return { html, dom };
      },
    );
  } finally {
    dom.window.clearInterval(timer);
    dom.window.close();
  }
}

test('exported identities are stable and namespaced', () => {
  // The tab `id` doubles as the body slot key; a mismatch means the sidebar
  // would render its "nothing can view this" notice instead of the panel.
  assert.equal(TAB_KIND, 'req-tree');
  assert.equal(TAB_ID, 'dsh-req-tree/tree');
  assert.equal(LOCALE_NAMESPACE, 'dsh-req-tree');
  assert.equal(typeof ReqTreeTab, 'function');
});

test('every dictionary key the panel uses exists in both locales', async () => {
  const source = await readFile(join(here, '..', 'lib', 'client.js'), 'utf8');
  // Keys used in a literal `t('...')` call, plus the two dynamic families the
  // panel interpolates from data (`status.<status>` and `op.<journal op>`).
  const literal = new Set([...source.matchAll(/\bt\('([a-z]+\.[A-Za-z0-9]+)'/g)].map((match) => match[1]));
  for (const status of ['open', 'doing', 'blocked', 'done', 'closed', 'dropped']) literal.add(`status.${status}`);
  for (const op of ['add', 'start', 'block', 'unblock', 'done', 'close', 'drop', 'update', 'note', 'focus', 'resume', 'baseline', 'restore']) {
    literal.add(`op.${op}`);
  }

  const english = new Set(Object.keys(en));
  const chinese = new Set(Object.keys(zh));
  for (const key of literal) {
    assert.ok(english.has(key), `en is missing ${key}`);
    assert.ok(chinese.has(key), `zh is missing ${key}`);
  }
  // A key present in one locale but not the other renders as a raw key string in
  // one language, which is worse than an untranslated human-readable string.
  for (const key of english) assert.ok(chinese.has(key), `zh is missing ${key} (present in en)`);
  for (const key of chinese) assert.ok(english.has(key), `en is missing ${key} (present in zh)`);
  assert.ok(literal.size > 25, 'expected the panel to use a meaningful number of keys');
});

test('a real tree renders its headline, lines, nodes and warnings', async () => {
  const { html } = await mount(shell());
  assert.ok(html.includes('data-req-tree'), 'the ready view must be marked');
  assert.ok(html.includes(zh['summary.unclosed']), 'missing the unclosed metric');
  assert.ok(html.includes(zh['summary.closed']), 'missing the closed metric');
  assert.ok(html.includes('33%'), 'expected the fixture\'s 33% progress');
  assert.ok(html.includes('计价模块'));
  assert.ok(html.includes('文档库'));
  assert.ok(html.includes('W6'), 'the detour warning must be visible');
  assert.ok(html.includes('W9'), 'the orphaned-children warning must be visible');
  assert.ok(html.includes('/tmp/demo/docs/requirements'));
  // Read-only is stated, because it changes what the user should expect to do.
  assert.ok(html.includes('只读'));
});

test('every rendered node row actually shows its title and status', async () => {
  // `jsx(type, props, ...children)` silently drops siblings passed as extra
  // arguments — the button renders, its content does not. Assert on content,
  // never just on the container's presence.
  const { html } = await mount(shell());
  const rows = [...html.matchAll(/data-req-tree-node="([^"]+)"/g)].map((match) => match[1]);
  assert.equal(rows.length, 7, 'every node must render a row');
  for (const title of ['计价模块', '供货合同', '供应商维度筛选', '导入模板截断', '已收口子项', '文档库', '全文检索子项']) {
    assert.ok(html.includes(title), `title missing from the DOM: ${title}`);
  }
  for (const status of ['未开始', '进行中', '阻塞', '已收口', '已放弃']) {
    assert.ok(html.includes(`>${status}</span>`), `status chip missing: ${status}`);
  }
});

test('the default view keeps a finished parent visible when live work hangs beneath it', async () => {
  // R-0001 is closed but R-0002/R-0003/R-0004 under it are not, and R-0005 is
  // dropped but R-0006 under it is open. Hiding the parent would hide
  // outstanding work behind its own finished ancestor — the one mistake this
  // panel must never make, because it reads as "that work is done".
  const { html } = await mount(shell());
  assert.ok(html.includes('计价模块'), 'a closed root with live descendants must stay visible');
  assert.ok(html.includes('供货合同'), 'the live subtree must render');
  assert.ok(html.includes('供应商维度筛选'));
  assert.ok(html.includes('文档库'));
  assert.ok(html.includes('全文检索子项'));
});

test('a tree with no nodes reads as empty, not as broken', async () => {
  const { html } = await mount(shell({ nodes: [] }));
  assert.ok(html.includes(zh['tree.empty']));
  // Critically: not an error, and not a fake "0 nodes, all clear".
  assert.ok(!html.includes('rt-error'), 'an empty tree is not an error');
});

test('a 200 carrying an error body renders an error, never an empty tree', async () => {
  // This is the failure mode that would make a viewer actively harmful: the
  // route answers 200 with `{ok:false}` and a naive panel draws "nothing to do".
  const { html } = await mount({ ok: false, error: { code: 'invalid-json', message: 'tree.json is not valid JSON: Unexpected token' } });
  assert.ok(html.includes('rt-error'), 'an unreadable tree must render the error view');
  assert.ok(html.includes('tree.json is not valid JSON'), 'the host message must reach the user');
  assert.ok(!html.includes(zh['tree.empty']), 'it must not claim the tree is empty');
});

test('a missing tree.json explains itself and offers a retry', async () => {
  const { html } = await mount({ ok: false, error: { code: 'unreadable', message: 'cannot read tree.json: ENOENT' } });
  assert.ok(html.includes(zh['state.error']));
  assert.ok(html.includes(zh['state.retry']));
});

test('no workspace is distinguished from no tree', async () => {
  const { html } = await mount({ ok: false, error: { code: 'no-workspace', message: 'no workspace is registered' } });
  assert.ok(html.includes(zh['state.noWorkspace']));
});

test('a legacy source of truth location is flagged for migration', async () => {
  const body = shell();
  body.legacy = true;
  body.root = '/tmp/demo/.workbuddy/requirements';
  const { html } = await mount(body);
  assert.ok(html.includes(zh['source.legacy']), 'the migration hint must be shown');
  assert.ok(html.includes('.workbuddy/requirements'));
});

test('a host that cannot be reached shows an error, not a stale lie', async () => {
  const { html } = await mount(undefined, {
    fetchImpl: async () => { throw new Error('network down'); },
  });
  assert.ok(html.includes('rt-error'));
  assert.ok(html.includes(zh['state.retry']));
});

test('the loading skeleton renders its placeholder rows', async () => {
  const dom = new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>', { url: 'http://localhost/' });
  try {
    const html = await withGlobals(dom, { fetch: () => new Promise(() => {}) }, async () => {
      const container = dom.window.document.getElementById('root');
      const root = createRoot(container);
      await act(async () => { root.render(createElement(ReqTreeTab, { t, useTabInfo })); });
      const markup = container.innerHTML;
      await act(async () => { root.unmount(); });
      return markup;
    });
    assert.ok(html.includes('rt-skeleton'));
    assert.equal((html.match(/<i><\/i>/g) ?? []).length, 3);
  } finally {
    dom.window.close();
  }
});

test('the panel injects its stylesheet once per document', async () => {
  const body = shell();
  // Each mount gets a fresh document; re-rendering into the *same* document is
  // the case that must not stack duplicate <style> tags.
  const dom = new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>', { url: 'http://localhost/' });
  const timer = dom.window.setInterval(() => {}, 1e9);
  try {
    await withGlobals(dom, { fetch: async () => ({ ok: true, json: async () => body }) }, async () => {
      for (let pass = 0; pass < 2; pass += 1) {
        const container = dom.window.document.getElementById('root');
        const root = createRoot(container);
        await act(async () => { root.render(createElement(ReqTreeTab, { t, useTabInfo })); });
        await settle();
        await act(async () => { root.unmount(); });
      }
    });
    assert.equal(dom.window.document.querySelectorAll('style[data-plugin-css]').length, 1);
  } finally {
    dom.window.clearInterval(timer);
    dom.window.close();
  }
});

test('the panel never renders a control that writes to the source of truth', async () => {
  const { html } = await mount(shell());
  // The only interactive controls are: expand a node, filter, toggle the
  // default horizon, switch workspace, retry. None of them may mutate.
  const buttons = [...html.matchAll(/<button[^>]*>/g)].map((match) => match[0]);
  assert.ok(buttons.length > 0, 'expected the panel to have controls');
  for (const button of buttons) {
    assert.ok(!/type="submit"/.test(button), 'no form submissions');
  }
  assert.ok(!/data-action=/.test(html), 'no mutating action hooks');
});