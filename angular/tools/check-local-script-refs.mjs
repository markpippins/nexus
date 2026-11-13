#!/usr/bin/env node
/**
 * check-local-script-refs.mjs — fail the build when an `index.html` loads a
 * script from a local path that does not exist.
 *
 * Why this exists: nexus-console's `index.html` carried
 * `<script type="module" src="./index.js">` for a file that had never been in
 * the repository. The dev server served the tag, the browser requested it, and
 * every page load logged `Failed to load url /index.js` — 575 times in one
 * evening. Nothing caught it: the tag is an HTML attribute, so the type checker
 * cannot see it, the build succeeds because the builder only injects its own
 * entry script, and the app still works because that entry is what actually
 * boots it. A dangling reference is therefore invisible until you read the
 * journal — and even then it reads like noise rather than a bug.
 *
 * What it does:
 *   1. Finds every `index.html` under the project directory (skipping build
 *      output, dependencies and caches).
 *   2. Extracts each `<script src="...">`, ignoring HTML comments.
 *   3. Keeps only *local* references — external URLs (`http:`, `https:`,
 *      protocol-relative), `data:`, `blob:` and `javascript:` are not files the
 *      build can be expected to emit, and templated values cannot be resolved
 *      statically without guessing.
 *   4. Resolves each local path — root-absolute against the entry's package
 *      root, relative against the HTML file's own directory — and exits 1
 *      naming `file:line` for every one with no such file.
 *
 * Usage:
 *   node check-local-script-refs.mjs [--project-dir DIR]
 *
 * Exit codes:
 *   0  every local script reference exists
 *   1  at least one local script reference does not exist
 *   2  the check could not run (no index.html found, unreadable directory) —
 *      never reported as a pass
 *
 * What it deliberately does not cover:
 *   - Component templates (`*.component.html`): Angular does not execute
 *     scripts in templates, so a tag there is inert rather than a broken
 *     request. Only `index.html` is a document entry.
 *   - Files the dev server injects or the builder rewrites (`main.js`,
 *     `main-<hash>.js`): those are produced by the toolchain, not author-written,
 *     and are not present in source at all.
 *   - `<link href>` and `<img src>`: a different class of reference, with its
 *     own build rules. Keeping the check narrow keeps its failures believable.
 */

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

/** Never walk into these: build output, dependencies, caches. */
const SKIP_DIRECTORIES = new Set([
  'node_modules',
  'dist',
  'coverage',
  '.git',
  '.angular',
  '.cache',
  '.nx',
  '.turbo',
  'build',
]);

/** A `<script>` open tag; the src is read from its attributes. */
const SCRIPT_TAG = /<script\b[^>]*>/gi;

/** `src=`, quoted or bare. */
const SRC_ATTRIBUTE = /\bsrc\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'=<>`]+))/i;

/** Raised for anything that means "the check could not run". */
export class CheckError extends Error {}

/**
 * Blank out `<!-- ... -->` while preserving offsets, so reported line numbers
 * match the file the author sees.
 */
export function stripHtmlComments(source) {
  const out = source.split('');
  let index = 0;

  while (index < source.length) {
    if (source.startsWith('<!--', index)) {
      // `<!-->` and `<!--->` are empty comments (the HTML spec's "abrupt
      // closing of empty comment"): browsers end them immediately. Without
      // this, the scan would blank the rest of the document and hide every
      // later script tag — a false pass, which is worse than a false failure.
      if (source.startsWith('<!-->', index)) {
        blank(out, source, index, index + 5);
        index += 5;
        continue;
      }
      if (source.startsWith('<!--->', index)) {
        blank(out, source, index, index + 6);
        index += 6;
        continue;
      }
      const end = source.indexOf('-->', index + 4);
      const stop = end === -1 ? source.length : end + 3;
      for (let i = index; i < stop; i += 1) {
        if (out[i] !== '\n') out[i] = ' ';
      }
      index = stop;
      continue;
    }
    index += 1;
  }

  return out.join('');
}

/** Overwrite a span with spaces, keeping newlines so offsets and lines hold. */
function blank(out, source, start, stop) {
  for (let i = start; i < stop; i += 1) {
    if (source[i] !== '\n') out[i] = ' ';
  }
}

function lineAt(source, index) {
  let line = 1;
  for (let i = 0; i < index; i += 1) {
    if (source[i] === '\n') line += 1;
  }
  return line;
}

/**
 * Every `<script src>` in a document, with its line, in source order.
 * External and templated values are included here and filtered by
 * `toLocalPath`, so this function stays a pure parser.
 *
 * @returns {{ src: string, line: number }[]}
 */
export function findScriptRefs(source) {
  const clean = stripHtmlComments(source);
  const refs = [];

  for (const tag of clean.matchAll(SCRIPT_TAG)) {
    const attribute = SRC_ATTRIBUTE.exec(tag[0]);
    if (!attribute) continue;
    const src = (attribute[1] ?? attribute[2] ?? attribute[3] ?? '').trim();
    refs.push({ src, line: lineAt(clean, tag.index) });
  }

  return refs;
}

/**
 * The local path a `src` points at, or null when the value is not a file the
 * build could emit.
 *
 * Query strings and fragments are dropped: the file on disk has neither.
 */
export function toLocalPath(src) {
  const value = String(src ?? '').trim();
  if (!value) return null;

  // Absolute URLs, protocol-relative URLs and non-fetchable schemes.
  if (/^[a-z][a-z0-9+.-]*:/i.test(value) || value.startsWith('//')) return null;

  // Template syntax (server-side tokens, Angular interpolation), placeholders
  // such as %PUBLIC_URL%, and glob-ish characters cannot be resolved without
  // guessing.
  if (/[{}<>*$]/.test(value)) return null;
  if (/%[A-Za-z_][A-Za-z0-9_]*%/.test(value)) return null;

  const withoutQuery = value.split('#')[0].split('?')[0];
  if (!withoutQuery) return null;

  return withoutQuery;
}

/**
 * The package root an entry belongs to: the nearest directory at or above the
 * HTML file that has a `package.json`, bounded by the project directory.
 *
 * A dev-server root-absolute path (`/src/main.tsx`) means "the root of the
 * project being served", which is not always the directory the scan started
 * from — `throttler-ui/widgets/peb-ui/index.html` is its own Vite project with
 * its own `package.json`, and its `/src/main.tsx` lives under that widget, not
 * under `throttler-ui`. Resolving against the scan root would report every
 * nested entry as broken.
 */
export function entryRoot(htmlPath, projectDir = process.cwd()) {
  const root = path.resolve(projectDir);
  const candidates = [];

  let directory = path.dirname(path.resolve(htmlPath));
  for (;;) {
    candidates.push(directory);
    if (directory === root) break;
    const parent = path.dirname(directory);
    const insideRoot = parent === root || parent.startsWith(root + path.sep);
    if (parent === directory || !insideRoot) break;
    directory = parent;
  }

  const withPackageJson = candidates.find((candidate) =>
    isFile(path.join(candidate, 'package.json')),
  );
  return withPackageJson ?? root;
}

/**
 * Resolve a local `src` to an absolute path.
 * Root-absolute paths belong to the entry's package root; relative ones to the
 * directory holding the HTML file.
 */
export function resolveRef(htmlPath, src, projectDir) {
  const local = toLocalPath(src);
  if (local === null) return null;
  const base = local.startsWith('/')
    ? entryRoot(htmlPath, projectDir)
    : path.dirname(htmlPath);
  return path.resolve(base, local.replace(/^\/+/, ''));
}

function isFile(target) {
  try {
    return fs.statSync(target).isFile();
  } catch {
    return false;
  }
}

/**
 * Check one HTML file.
 * @returns {{ file: string, checked: number, missing: {src: string, line: number, resolved: string}[] }}
 */
export function checkHtmlFile(htmlPath, projectDir = path.dirname(htmlPath)) {
  const source = fs.readFileSync(htmlPath, 'utf8');
  const refs = findScriptRefs(source);

  const missing = [];
  let checked = 0;
  for (const ref of refs) {
    const resolved = resolveRef(htmlPath, ref.src, projectDir);
    if (resolved === null) continue;
    checked += 1;
    if (!isFile(resolved)) missing.push({ ...ref, file: htmlPath, resolved });
  }

  return { file: htmlPath, checked, missing };
}

/** Every `index.html` under `root`, skipping build output, dependencies, caches. */
export function findHtmlEntries(root) {
  const entries = [];
  const stack = [path.resolve(root)];

  while (stack.length > 0) {
    const directory = stack.pop();
    let contents;
    try {
      contents = fs.readdirSync(directory, { withFileTypes: true });
    } catch {
      continue;
    }

    for (const entry of contents) {
      const full = path.join(directory, entry.name);
      if (entry.isDirectory()) {
        if (SKIP_DIRECTORIES.has(entry.name) || entry.name.startsWith('.')) continue;
        stack.push(full);
        continue;
      }
      if (entry.isFile() && entry.name === 'index.html') entries.push(full);
    }
  }

  return entries.sort();
}

/**
 * Run the whole check.
 * @returns {{ entries: string[], checked: number, missing: object[] }}
 */
export function checkProject({ projectDir = process.cwd() } = {}) {
  const root = path.resolve(projectDir);

  let stat;
  try {
    stat = fs.statSync(root);
  } catch {
    stat = null;
  }
  if (!stat?.isDirectory()) throw new CheckError(`not a directory: ${root}`);

  const entries = findHtmlEntries(root);
  if (entries.length === 0) {
    throw new CheckError(`no index.html under ${root} — nothing to check`);
  }

  const results = entries.map((entry) => checkHtmlFile(entry, root));
  return {
    entries,
    checked: results.reduce((total, result) => total + result.checked, 0),
    missing: results.flatMap((result) => result.missing),
  };
}

function parseArgs(argv) {
  const options = {};
  for (let i = 0; i < argv.length; i += 1) {
    const flag = argv[i];
    switch (flag) {
      case '--project-dir':
        if (!argv[i + 1] || argv[i + 1].startsWith('--')) {
          throw new CheckError('--project-dir needs a value');
        }
        options.projectDir = argv[i + 1];
        i += 1;
        break;
      case '--help':
      case '-h':
        options.help = true;
        break;
      default:
        throw new CheckError(`unknown argument: ${flag}`);
    }
  }
  return options;
}

const USAGE = `Usage: node check-local-script-refs.mjs [options]

Fail when an index.html loads a script from a local path that does not exist.

  --project-dir DIR   project root to scan (default: cwd)
  -h, --help          show this help`;

export function main(
  argv = process.argv.slice(2),
  { cwd = process.cwd(), stdout = console.log, stderr = console.error } = {},
) {
  let options;
  try {
    options = parseArgs(argv);
  } catch (error) {
    stderr(`✗ ${error.message}\n\n${USAGE}`);
    return 2;
  }

  if (options.help) {
    stdout(USAGE);
    return 0;
  }

  let result;
  try {
    result = checkProject({ projectDir: options.projectDir ?? cwd });
  } catch (error) {
    if (error instanceof CheckError) {
      stderr(`✗ local-script check could not run: ${error.message}`);
      return 2;
    }
    throw error;
  }

  if (result.missing.length > 0) {
    const noun = result.missing.length === 1 ? 'reference does' : 'references do';
    stderr(`✗ ${result.missing.length} local script ${noun} not exist`);
    for (const reference of result.missing) {
      stderr(`  ${path.relative(cwd, reference.file) || reference.file}:${reference.line} → ${reference.src}`);
      stderr(`    resolved to ${reference.resolved}`);
    }
    stderr('');
    stderr('A <script src> that names a missing file 404s on every page load and the');
    stderr('app still boots, so the failure never surfaces as an error. Fix the path,');
    stderr('add the file, or remove the tag.');
    return 1;
  }

  stdout(
    `✓ ${result.checked} local script reference${result.checked === 1 ? '' : 's'} in ` +
      `${result.entries.length} index.html file${result.entries.length === 1 ? '' : 's'} ` +
      `all exist (${path.relative(cwd, path.resolve(options.projectDir ?? cwd)) || '.'})`,
  );
  return 0;
}

const invokedDirectly =
  process.argv[1] && path.resolve(process.argv[1]) === path.resolve(fileURLToPath(import.meta.url));

if (invokedDirectly) {
  process.exitCode = main();
}
