/**
 * req-tree for DeepSeek Harness — host half.
 *
 * A **read-only** viewer over a req-tree source of truth (`tree.json`). It
 * mounts one browser route so the right-sidebar tab can render the current
 * workspace's requirement tree without shelling out to Python and without
 * ever writing to the source of truth.
 *
 * Why read-only: req-tree's first invariant is that every write goes through
 * its CLI (`todoctl`), because a hand-edited `tree.json` manufactures a second
 * source of truth — the single biggest threat to the system. A panel that can
 * mutate state would have to re-implement `close`'s evidence gate and
 * `--override-reason` confirmation in a browser widget, where it would be
 * strictly weaker than the CLI. So this plugin observes; `todoctl` decides.
 *
 * Route resolution mirrors `todoctl.resolve_root_info()` exactly, in the same
 * order, so the panel and the CLI can never disagree about which directory is
 * the source of truth. `TODOCTL_ROOT` is honoured because the CLI honours it.
 *
 * @module dsh-req-tree
 */
import { readFile, stat } from 'node:fs/promises';
import { join } from 'node:path';
import z from '@deepseek-ai/schemastery';
import { projectTree } from './project.js';

export { projectTree } from './project.js';

/** Web-server service key candidates, newest first. */
const WEB_SERVER_KEYS = ['webServer', 'httpServer'];
/** Workspace registry service key candidates, newest first. */
const WORKSPACE_KEYS = ['workspaceRegistry', 'workspace'];

/** Directories that are a source of truth when they already hold a `tree.json`. */
const ROOT_STRICT = ['docs/requirements', '.req-tree/requirements', 'requirements'];
/** Legacy host-specific location: recognised when it *is* a source of truth, never created. */
const ROOT_LEGACY = ['.workbuddy/requirements'];
/** Pre-created (even empty) directories that seed the default location. */
const ROOT_EMPTY = ['docs/requirements', '.req-tree/requirements'];

export const name = 'req-tree';
// The panel's route needs the web server and the workspace registry, but a
// headless profile has neither. Declaring a required service would make such a
// composition wait forever; declaring an optional one is redundant because both
// are resolved lazily inside apply() (see registerWebSurface). So inject is
// omitted entirely: the plugin mounts, and simply never gains a panel where those
// services do not exist.
export const Config = z.object({
  /** Seconds between client polls. The tree changes only when someone runs `todoctl`. */
  pollSeconds: z.natural().default(3),
  /** Cap on nodes returned per workspace, to keep a 10k-node tree from stalling the poll. */
  maxNodes: z.natural().min(1).default(5000),
});

// --- filesystem helpers -------------------------------------------------------

async function isFile(path) {
  try {
    return (await stat(path)).isFile();
  } catch {
    return false;
  }
}

async function isDir(path) {
  try {
    return (await stat(path)).isDirectory();
  } catch {
    return false;
  }
}

/** Walk up from `cwd` looking for a `.git` directory (or file, for worktrees). */
async function findGitRoot(cwd) {
  let current = cwd;
  for (;;) {
    if (await isDir(join(current, '.git'))) return current;
    const parent = join(current, '..');
    if (parent === current) return undefined;
    current = parent;
  }
}

/**
 * Locate a req-tree source of truth for one workspace.
 *
 * Deliberately a port, not a re-invention: the order and the human-readable
 * `origin` strings track `todoctl.resolve_root_info()` so a panel that says
 * "workspace root, git-detected" never contradicts the CLI's own banner.
 */
export async function resolveRoot(cwd, explicit, env) {
  if (explicit !== undefined && explicit !== '') {
    return { dir: explicit, origin: 'configured explicitRoot' };
  }
  const fromEnv = env.TODOCTL_ROOT;
  if (fromEnv !== undefined && fromEnv !== '') {
    return { dir: fromEnv, origin: 'TODOCTL_ROOT environment variable' };
  }

  for (const rel of ROOT_STRICT) {
    const candidate = join(cwd, rel);
    if (await isFile(join(candidate, 'tree.json'))) return { dir: candidate, origin: `workspace is already a source of truth: ${rel}` };
  }
  for (const rel of ROOT_LEGACY) {
    const candidate = join(cwd, rel);
    if (await isFile(join(candidate, 'tree.json'))) {
      return { dir: candidate, origin: `legacy location still in use: ${rel} (consider migrating to .req-tree/requirements)` };
    }
  }
  for (const rel of ROOT_EMPTY) {
    const candidate = join(cwd, rel);
    if (await isDir(candidate)) return { dir: candidate, origin: `pre-created directory: ${rel}` };
  }
  const gitRoot = await findGitRoot(cwd);
  if (gitRoot !== undefined) {
    return { dir: join(gitRoot, 'docs', 'requirements'), origin: `git repository detected: ${gitRoot}/docs/requirements` };
  }
  return { dir: join(cwd, '.req-tree', 'requirements'), origin: 'no git repository detected: .req-tree/requirements' };
}

/** Read `tree.json`, or report why it cannot be read. Never throws. */
async function readTree(root) {
  const path = join(root, 'tree.json');
  let raw;
  try {
    raw = await readFile(path, 'utf8');
  } catch (error) {
    return { ok: false, error: { code: 'unreadable', message: `cannot read tree.json: ${error instanceof Error ? error.message : String(error)}` } };
  }
  let data;
  try {
    data = JSON.parse(raw);
  } catch (error) {
    // A corrupt source of truth must surface as a visible error, never as "0 nodes".
    return { ok: false, error: { code: 'invalid-json', message: `tree.json is not valid JSON: ${error instanceof Error ? error.message : String(error)}` } };
  }
  if (typeof data !== 'object' || data === null || !Array.isArray(data.nodes)) {
    return { ok: false, error: { code: 'invalid-shape', message: 'tree.json has no `nodes` array' } };
  }
  return { ok: true, data };
}

/** Recent journal entries, newest first — the panel's "what changed" strip. */
async function readJournal(root, limit) {
  let raw;
  try {
    raw = await readFile(join(root, 'journal.jsonl'), 'utf8');
  } catch {
    return [];
  }
  const entries = [];
  for (const line of raw.split('\n')) {
    if (line.trim() === '') continue;
    try {
      const value = JSON.parse(line);
      if (typeof value === 'object' && value !== null && typeof value.ts === 'string') entries.push(value);
    } catch {
      // A damaged line is skipped, exactly as `todoctl log` skips it.
    }
  }
  return entries.slice(-limit).reverse();
}

/** Assemble the full payload for one workspace. */
async function loadWorkspace(workspace, options) {
  const cwd = workspace.path ?? process.cwd();
  const root = await resolveRoot(cwd, options.explicitRoot, options.env);
  const tree = await readTree(root.dir);
  const payload = {
    workspace: { id: workspace.id, title: workspace.title ?? workspace.path ?? '', path: cwd },
    root: root.dir,
    origin: root.origin,
    legacy: root.origin.startsWith('legacy'),
  };
  if (!tree.ok) {
    return { ...payload, ok: false, error: tree.error };
  }
  const projection = projectTree(tree.data, { maxNodes: options.maxNodes });
  return {
    ...payload,
    ok: true,
    ...projection,
    journal: await readJournal(root.dir, 12),
  };
}

// --- change detection ---------------------------------------------------------

/**
 * A cheap fingerprint of the files the panel reads.
 *
 * The panel polls, so the expensive work must not happen on a poll where nothing
 * changed. `tree.json` is the only thing that alters the rendered tree; the
 * journal adds the "recent changes" strip at the bottom, so it is watched too.
 *
 * mtime alone is not enough on every filesystem (one-second granularity on some,
 * and a rewrite within the same tick is invisible), so the byte length is mixed
 * in. A same-length in-place rewrite that also preserves mtime is the only case
 * this misses, and `todoctl` writes through snapshot-then-rename, which always
 * changes mtime.
 */
async function fingerprint(root) {
  const parts = [];
  for (const file of ['tree.json', 'journal.jsonl']) {
    try {
      const info = await stat(join(root, file));
      parts.push(`${file}:${info.mtimeMs}:${info.size}`);
    } catch {
      // A missing file is itself part of the state: "no tree.json" must differ
      // from "an empty tree.json", or the panel would keep showing a stale tree.
      parts.push(`${file}:-`);
    }
  }
  return parts.join('|');
}

/**
 * Per-workspace memo of the last served projection.
 *
 * A poll that finds an unchanged fingerprint is answered from this cache without
 * re-reading or re-projecting, which is what makes an idle panel cost roughly one
 * `stat` per watched file instead of a full parse plus projection.
 */
function createCache() {
  const entries = new Map();
  return {
    /** @returns the cached payload when the files are unchanged, else undefined. */
    get(key, revision) {
      const hit = entries.get(key);
      return hit !== undefined && hit.revision === revision ? hit.payload : undefined;
    },
    set(key, revision, payload) {
      entries.set(key, { revision, payload });
      // One workspace per tab in practice; the cap only bounds a pathological
      // workspace-switching loop.
      if (entries.size > 32) entries.delete(entries.keys().next().value);
    },
  };
}

// --- browser route ------------------------------------------------------------

/**
 * The raw WebServer does not inherit Connection's authentication fence, so
 * every route is wrapped in the same request-rejection gate the host plugins
 * use. A missing Connection is an assembly failure, never an invitation to
 * expose workspace state.
 */
function authenticatedWebRoutes(server, connection) {
  return {
    register(route) {
      return server.register({
        ...route,
        async handler(req, res) {
          const gate = connection();
          const rejection = gate === undefined ? 503 : gate.requestRejection(req);
          if (rejection !== undefined) {
            res.writeHead(rejection, {
              'content-type': 'application/json; charset=utf-8',
              'cache-control': 'no-store',
            });
            res.end(JSON.stringify({ error: rejection === 503 ? 'authentication unavailable' : rejection === 401 ? 'unauthorized' : 'forbidden' }));
            return;
          }
          await route.handler(req, res);
        },
      });
    },
  };
}

const JSON_HEADERS = { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store' };

export function apply(ctx, config) {
  const options = {
    pollSeconds: config.pollSeconds ?? 3,
    maxNodes: config.maxNodes ?? 5000,
    explicitRoot: config.root,
    env: process.env,
  };
  const cache = createCache();

  let webRegistered = false;
  const registerWebSurface = () => {
    if (webRegistered) return;
    const rawWebServer = ctx.get(WEB_SERVER_KEYS[0]) ?? ctx.get(WEB_SERVER_KEYS[1]);
    const workspaceRegistry = ctx.get(WORKSPACE_KEYS[0]) ?? ctx.get(WORKSPACE_KEYS[1]);
    // Headless profiles mount neither; the plugin then stays inert rather than
    // blocking boot on a service that will never appear.
    if (rawWebServer === undefined || workspaceRegistry === undefined) return;
    const webServer = authenticatedWebRoutes(rawWebServer, () => ctx.get('connection'));
    webRegistered = true;

    ctx.effect(() => webServer.register({
      kind: 'exact',
      path: '/plugins/dsh-req-tree/tree',
      handler: async (req, res) => {
        try {
          const url = new URL(req.url ?? '/', 'http://x');
          // `?workspace=<id>` is optional; without it the first registered
          // workspace answers, which is the common single-project case.
          const wanted = url.searchParams.get('workspace');
          const workspaces = workspaceRegistry.list();
          const selected = wanted === null ? workspaces[0] : workspaces.find((workspace) => workspace.id === wanted);
          if (selected === undefined) {
            res.writeHead(200, JSON_HEADERS);
            res.end(JSON.stringify({ ok: false, error: { code: 'no-workspace', message: 'no workspace is registered' }, workspaces: [] }));
            return;
          }
          // Resolve the source of truth first, then fingerprint it: the
          // fingerprint must describe the directory actually read, not the
          // workspace root, or a `TODOCTL_ROOT` override would never invalidate.
          const cwd = selected.path ?? process.cwd();
          const root = await resolveRoot(cwd, options.explicitRoot, options.env);
          const revision = await fingerprint(root.dir);
          const cached = cache.get(selected.id, revision);
          if (cached !== undefined) {
            res.writeHead(200, { ...JSON_HEADERS, 'x-req-tree-revision': revision, 'x-req-tree-cached': '1' });
            res.end(JSON.stringify({ pollSeconds: options.pollSeconds, revision, ...cached }));
            return;
          }
          const payload = await loadWorkspace(selected, options);
          cache.set(selected.id, revision, payload);
          res.writeHead(200, { ...JSON_HEADERS, 'x-req-tree-revision': revision });
          res.end(JSON.stringify({ pollSeconds: options.pollSeconds, revision, ...payload }));
        } catch (error) {
          ctx.logger?.warn?.(`req-tree: tree route failed: ${String(error)}`);
          res.writeHead(500, JSON_HEADERS);
          res.end(JSON.stringify({ error: 'failed to read the requirement tree' }));
        }
      },
    }), 'req-tree: tree route');

    ctx.effect(() => webServer.register({
      kind: 'exact',
      path: '/plugins/dsh-req-tree/workspaces',
      handler: async (req, res) => {
        res.writeHead(200, JSON_HEADERS);
        res.end(JSON.stringify({
          pollSeconds: options.pollSeconds,
          workspaces: workspaceRegistry.list().map((workspace) => ({
            id: workspace.id,
            title: workspace.title ?? workspace.path ?? '',
            path: workspace.path ?? '',
          })),
        }));
      },
    }), 'req-tree: workspaces route');
  };

  registerWebSurface();
  ctx.on('internal/service', (serviceName) => {
    if (WEB_SERVER_KEYS.includes(serviceName) || WORKSPACE_KEYS.includes(serviceName)) registerWebSurface();
  });
}
