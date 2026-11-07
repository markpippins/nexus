#!/usr/bin/env node
/**
 * check-fetched-assets.mjs — fail the build when a component fetches an
 * `assets/...` URL that the build did not actually emit.
 *
 * Why this exists: an Angular project only serves what its build copies. A
 * component can request `assets/config/health-services.json`, typecheck, and
 * build green while that URL 404s at runtime — nexus-console did exactly that
 * until #910 added the missing mapping. The type system cannot see the bug:
 * the path is just a string.
 *
 * Checking the `assets` entries in angular.json is not enough, because a glob
 * can match nothing, an `input` directory can be missing, and a configuration
 * override can drop the entry. So this reads the *emitted* output directory
 * after the build and asks whether each referenced path is really there.
 *
 * What it does:
 *   1. Resolves the build target/configuration/outputPath from angular.json
 *      (string form, and the `{ base, browser }` object form).
 *   2. Scans the project source for literal URLs under `assets/` — quoted
 *      strings, template literals, and `url(...)` references — in .ts, .tsx,
 *      .js, .jsx, .mjs, .cjs, .html, .css, .scss, .sass, .less. Comments are
 *      ignored, as are *.spec.* / *.test.* files (not part of the bundle).
 *   3. Exits 1, naming source file, line and missing path, for every reference
 *      with no matching file in the output directory.
 *
 * It must run *after* the build, since it reads the build output:
 *
 *   "build": "ng build && node ../tools/check-fetched-assets.mjs"
 *
 * Usage:
 *   node check-fetched-assets.mjs [--project-dir DIR] [--project NAME]
 *        [--configuration NAME] [--dist DIR]
 *
 * Exit codes:
 *   0  every `assets/...` reference is emitted by the build
 *   1  at least one referenced asset was not emitted
 *   2  the check could not run (no angular.json, unknown project/config, no
 *      build output directory) — never reported as a pass
 *
 * What it deliberately does not cover:
 *   - URLs built at runtime (`assets/config/${name}.json`) — not resolvable
 *     statically, and guessing would produce false failures.
 *   - Relative `url(...)` in stylesheets: the bundler resolves those itself
 *     and emits them under a hashed `media/` name, so they are not runtime
 *     `assets/` URLs. Only root-absolute `/assets/...` is checked there.
 *   - Anything served by a backend rather than copied by the build.
 */

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

/** Source files that can carry a runtime URL. */
const SCANNABLE_EXTENSIONS = new Set([
  '.ts',
  '.tsx',
  '.js',
  '.jsx',
  '.mjs',
  '.cjs',
  '.html',
  '.htm',
  '.css',
  '.scss',
  '.sass',
  '.less',
]);

/** Stylesheet languages, where `url()` is rewritten by the bundler. */
const STYLESHEET_EXTENSIONS = new Set(['.css', '.scss', '.sass', '.less']);

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
]);

/** A quoted or template-literal URL that starts at an `assets/` segment. */
const QUOTED_ASSET_URL = /(['"`])((?:\.{0,2}\/)*assets\/[^'"`\r\n]*)\1/g;

/** `url(...)` with optional quoting — unquoted values are common in CSS. */
const CSS_URL = /\burl\(\s*(['"]?)([^)'"\s]+)\1\s*\)/g;

/** Raised for anything that means "the check could not run". */
export class CheckError extends Error {}

/**
 * Strip comments while preserving offsets (so line numbers stay correct).
 * A naive regex would corrupt `'http://...'` and `url(//cdn/...)`, hence the
 * small scanner that understands quotes and escapes.
 */
export function stripComments(source, extension = '') {
  const ext = extension.toLowerCase();
  const lineComments = ['.ts', '.tsx', '.js', '.jsx', '.mjs', '.cjs', '.scss', '.sass', '.less'].includes(ext);
  const blockComments = ext !== '.html' && ext !== '.htm';
  const markupComments = ext === '.html' || ext === '.htm';

  const out = source.split('');
  let i = 0;
  let quote = null;

  while (i < source.length) {
    const char = source[i];
    const next = source[i + 1];

    if (quote) {
      if (char === '\\') {
        i += 2;
        continue;
      }
      if (char === quote) quote = null;
      i += 1;
      continue;
    }

    if (char === '"' || char === "'" || char === '`') {
      quote = char;
      i += 1;
      continue;
    }

    if (markupComments && char === '<' && source.startsWith('<!--', i)) {
      const end = source.indexOf('-->', i + 4);
      const stop = end === -1 ? source.length : end + 3;
      blank(out, source, i, stop);
      i = stop;
      continue;
    }

    if (blockComments && char === '/' && next === '*') {
      const end = source.indexOf('*/', i + 2);
      const stop = end === -1 ? source.length : end + 2;
      blank(out, source, i, stop);
      i = stop;
      continue;
    }

    if (lineComments && char === '/' && next === '/') {
      let stop = source.indexOf('\n', i);
      if (stop === -1) stop = source.length;
      blank(out, source, i, stop);
      i = stop;
      continue;
    }

    i += 1;
  }

  return out.join('');
}

function blank(out, source, start, stop) {
  for (let i = start; i < stop; i += 1) {
    if (source[i] !== '\n') out[i] = ' ';
  }
}

/**
 * Normalise a literal URL to the path the build must have emitted, or return
 * null when the value is not a static `assets/...` URL.
 *
 * @param {string} raw literal as written in source
 * @param {{ requireRootAbsolute?: boolean }} [options] stylesheets only serve
 *   root-absolute URLs from the build; relative ones are bundler-resolved
 */
export function toEmittedPath(raw, { requireRootAbsolute = false } = {}) {
  const value = String(raw ?? '').trim();
  if (!value || value.endsWith('/')) return null;
  if (requireRootAbsolute && !value.startsWith('/')) return null;

  // The file on disk has no query string or fragment.
  const pathPart = value.split('#')[0].split('?')[0];

  // External and protocol-relative URLs are not build output.
  if (/^[a-z][a-z0-9+.-]*:/i.test(pathPart) || pathPart.startsWith('//')) return null;

  // Interpolated or glob-ish values cannot be resolved without guessing.
  if (/[$*{}<>|\s\\]/.test(pathPart)) return null;

  const segments = pathPart.split('/').filter((segment) => segment && segment !== '.');
  if (segments[0] !== 'assets') return null;
  if (segments.some((segment) => segment === '..' || segment === '~')) return null;

  return segments.join('/');
}

/**
 * Find every statically-referenced `assets/...` URL in one source file.
 * @returns {{ file: string, line: number, raw: string, emitted: string }[]}
 */
export function findAssetReferences(source, filePath) {
  const extension = path.extname(filePath).toLowerCase();
  const clean = stripComments(source, extension);
  const requireRootAbsolute = STYLESHEET_EXTENSIONS.has(extension);
  const references = [];
  const seen = new Set();

  const record = (raw, index) => {
    const emitted = toEmittedPath(raw, { requireRootAbsolute });
    if (!emitted) return;
    const line = lineAt(clean, index);
    const key = `${line}\u0000${emitted}`;
    if (seen.has(key)) return;
    seen.add(key);
    references.push({ file: filePath, line, raw: raw.trim(), emitted, index });
  };

  for (const match of clean.matchAll(QUOTED_ASSET_URL)) record(match[2], match.index);
  for (const match of clean.matchAll(CSS_URL)) record(match[2], match.index);

  // Two patterns can hit the same spot (`url('/assets/x.png')`); report in
  // source order so the output reads like the file.
  references.sort((a, b) => a.index - b.index);

  return references.map(({ index: _index, ...reference }) => reference);
}

function lineAt(source, index) {
  let line = 1;
  for (let i = 0; i < index; i += 1) {
    if (source[i] === '\n') line += 1;
  }
  return line;
}

/** Files under `root` that a browser bundle can be built from. */
export function collectSourceFiles(root, { exclude = [] } = {}) {
  const excluded = new Set(exclude.map((entry) => path.resolve(entry)));
  const files = [];
  const stack = [path.resolve(root)];

  while (stack.length > 0) {
    const directory = stack.pop();
    let entries;
    try {
      entries = fs.readdirSync(directory, { withFileTypes: true });
    } catch {
      continue;
    }

    for (const entry of entries) {
      const full = path.join(directory, entry.name);
      if (entry.isDirectory()) {
        if (SKIP_DIRECTORIES.has(entry.name) || entry.name.startsWith('.')) continue;
        if (excluded.has(path.resolve(full))) continue;
        stack.push(full);
        continue;
      }
      if (!entry.isFile()) continue;
      if (entry.name.includes('.spec.') || entry.name.includes('.test.')) continue;
      if (!SCANNABLE_EXTENSIONS.has(path.extname(entry.name).toLowerCase())) continue;
      files.push(full);
    }
  }

  return files.sort();
}

function buildTargetOf(project) {
  return project?.architect?.build ?? project?.targets?.build ?? null;
}

function resolveOutputPath(outputPath, projectDir, projectName) {
  if (typeof outputPath === 'string' && outputPath.trim()) {
    return path.resolve(projectDir, outputPath);
  }
  if (outputPath && typeof outputPath === 'object') {
    const base = outputPath.base || path.join('dist', projectName);
    const browser = outputPath.browser;
    return path.resolve(projectDir, base, browser && browser !== '.' ? browser : '');
  }
  return path.resolve(projectDir, 'dist', projectName);
}

/**
 * Resolve the directory that holds the served output for a project.
 * @returns {{ project: string, configuration: string|null, outputDir: string }}
 */
export function resolveBuildOutput(angularJson, projectDir, { project, configuration } = {}) {
  const projects = angularJson?.projects ?? {};
  const names = Object.keys(projects);
  if (names.length === 0) throw new CheckError('angular.json declares no projects');

  const projectName = project ?? names.find((name) => buildTargetOf(projects[name]));
  if (!projectName) throw new CheckError('angular.json declares no project with a build target');
  if (!projects[projectName]) throw new CheckError(`angular.json has no project "${projectName}"`);

  const target = buildTargetOf(projects[projectName]);
  if (!target) throw new CheckError(`project "${projectName}" has no build target`);

  const configurations = target.configurations ?? {};
  const selected =
    configuration ?? target.defaultConfiguration ?? (configurations.production ? 'production' : null);
  if (selected && !configurations[selected]) {
    throw new CheckError(`project "${projectName}" has no build configuration "${selected}"`);
  }

  // Build configurations are shallow option overrides, same as the CLI.
  const options = { ...(target.options ?? {}), ...(selected ? configurations[selected] : {}) };

  return {
    project: projectName,
    configuration: selected ?? null,
    outputDir: resolveOutputPath(options.outputPath, projectDir, projectName),
  };
}

/**
 * Run the whole check.
 * @returns {{
 *   project: string, configuration: string|null, outputDir: string,
 *   checked: number, references: object[], missing: object[]
 * }}
 */
export function checkProject({ projectDir = process.cwd(), project, configuration, dist } = {}) {
  const root = path.resolve(projectDir);
  const angularJsonPath = path.join(root, 'angular.json');
  if (!fs.existsSync(angularJsonPath)) {
    throw new CheckError(`no angular.json in ${root}`);
  }

  let angularJson;
  try {
    angularJson = JSON.parse(fs.readFileSync(angularJsonPath, 'utf8'));
  } catch (error) {
    throw new CheckError(`could not parse ${angularJsonPath}: ${error.message}`);
  }

  const resolved = resolveBuildOutput(angularJson, root, { project, configuration });
  const outputDir = dist ? path.resolve(root, dist) : resolved.outputDir;

  let outputStat;
  try {
    outputStat = fs.statSync(outputDir);
  } catch {
    outputStat = null;
  }
  if (!outputStat?.isDirectory()) {
    throw new CheckError(
      `build output directory not found: ${outputDir}\n` +
        'Run the build first — this check inspects emitted files, not angular.json.',
    );
  }

  const references = collectSourceFiles(root, { exclude: [outputDir] }).flatMap((file) =>
    findAssetReferences(fs.readFileSync(file, 'utf8'), file),
  );

  const missing = references.filter((reference) => {
    const emitted = path.join(outputDir, reference.emitted);
    try {
      return !fs.statSync(emitted).isFile();
    } catch {
      return true;
    }
  });

  return {
    ...resolved,
    outputDir,
    checked: references.length,
    references,
    missing,
  };
}

function parseArgs(argv) {
  const options = {};
  for (let i = 0; i < argv.length; i += 1) {
    const flag = argv[i];
    const value = argv[i + 1];
    switch (flag) {
      case '--project-dir':
        options.projectDir = requireValue(flag, value);
        i += 1;
        break;
      case '--project':
        options.project = requireValue(flag, value);
        i += 1;
        break;
      case '--configuration':
      case '--config':
        options.configuration = requireValue(flag, value);
        i += 1;
        break;
      case '--dist':
        options.dist = requireValue(flag, value);
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

function requireValue(flag, value) {
  if (!value || value.startsWith('--')) throw new CheckError(`${flag} needs a value`);
  return value;
}

const USAGE = `Usage: node check-fetched-assets.mjs [options]

Fail when a component references an assets/... URL the build did not emit.
Run it after the build.

  --project-dir DIR     Angular project root (default: cwd)
  --project NAME        angular.json project (default: the one with a build)
  --configuration NAME  build configuration (default: defaultConfiguration,
                        else "production" when defined)
  --dist DIR            override the resolved output directory
  -h, --help            show this help`;

export function main(argv = process.argv.slice(2), { cwd = process.cwd(), stdout = console.log, stderr = console.error } = {}) {
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
    result = checkProject({ ...options, projectDir: options.projectDir ?? cwd });
  } catch (error) {
    if (error instanceof CheckError) {
      stderr(`✗ fetched-asset check could not run: ${error.message}`);
      return 2;
    }
    throw error;
  }

  if (result.missing.length > 0) {
    const noun = result.missing.length === 1 ? 'path is' : 'paths are';
    stderr(`✗ ${result.missing.length} fetched asset ${noun} not emitted by the build`);
    stderr(`  project: ${result.project}${result.configuration ? ` (${result.configuration})` : ''}`);
    stderr(`  output:  ${result.outputDir}`);
    for (const reference of result.missing) {
      stderr(`  ${reference.emitted}`);
      stderr(`    referenced at ${path.relative(cwd, reference.file)}:${reference.line} ("${reference.raw}")`);
    }
    stderr('');
    stderr('Angular only serves what the build target\'s "assets" copy emits (angular.json).');
    stderr('Add a mapping that copies the file into the output, or stop requesting the URL.');
    return 1;
  }

  const where = `${result.project}, ${path.relative(cwd, result.outputDir) || '.'}`;
  stdout(
    result.checked === 0
      ? `✓ no assets/... URL references to verify (${where})`
      : `✓ ${result.checked} fetched asset reference${result.checked === 1 ? '' : 's'} emitted by the build (${where})`,
  );
  return 0;
}

const invokedDirectly =
  process.argv[1] && path.resolve(process.argv[1]) === path.resolve(fileURLToPath(import.meta.url));

if (invokedDirectly) {
  process.exitCode = main();
}
