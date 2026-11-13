import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

import {
  CheckError,
  checkHtmlFile,
  checkProject,
  findHtmlEntries,
  entryRoot,
  findScriptRefs,
  resolveRef,
  stripHtmlComments,
  toLocalPath,
} from './check-local-script-refs.mjs';

const TOOL = path.join(path.dirname(fileURLToPath(import.meta.url)), 'check-local-script-refs.mjs');

function tempProject(tree) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'local-scripts-'));
  for (const [relative, contents] of Object.entries(tree)) {
    const target = path.join(root, relative);
    fs.mkdirSync(path.dirname(target), { recursive: true });
    fs.writeFileSync(target, contents);
  }
  return root;
}

function document(scripts) {
  return `<!doctype html>\n<html>\n  <head>\n${scripts.map((s) => `    ${s}\n`).join('')}  </head>\n  <body></body>\n</html>\n`;
}

function runTool(args) {
  return spawnSync(process.execPath, [TOOL, ...args], { encoding: 'utf8' });
}

// ── Path classification ───────────────────────────────────────────────

test('toLocalPath keeps paths a build could be expected to emit', () => {
  assert.equal(toLocalPath('./index.js'), './index.js');
  assert.equal(toLocalPath('main.js'), 'main.js');
  assert.equal(toLocalPath('/main.js'), '/main.js');
  assert.equal(toLocalPath('../shared/boot.js'), '../shared/boot.js');
  assert.equal(toLocalPath('main.js?v=2#x'), 'main.js');
});

test('toLocalPath rejects URLs and values it cannot resolve', () => {
  assert.equal(toLocalPath('https://cdn.example.com/x.js'), null);
  assert.equal(toLocalPath('http://localhost:4200/x.js'), null);
  assert.equal(toLocalPath('//cdn.example.com/x.js'), null);
  assert.equal(toLocalPath('data:text/javascript,console.log(1)'), null);
  assert.equal(toLocalPath('blob:http://localhost/abc'), null);
  assert.equal(toLocalPath('javascript:void(0)'), null);
  assert.equal(toLocalPath('assets/${name}.js'), null);
  assert.equal(toLocalPath('%PUBLIC_URL%/x.js'), null);
  assert.equal(toLocalPath('   '), null);
});

// ── Parsing ───────────────────────────────────────────────────────────

test('findScriptRefs reads quoted and bare src attributes with line numbers', () => {
  const source = [
    '<html>',
    '  <script src="main.js"></script>',
    "  <script src='./app.js'></script>",
    '  <script type="module" src=/bare.js></script>',
    '  <script>inline()</script>',
  ].join('\n');

  assert.deepEqual(
    findScriptRefs(source).map((ref) => [ref.line, ref.src]),
    [
      [2, 'main.js'],
      [3, './app.js'],
      [4, '/bare.js'],
    ],
  );
});

test('findScriptRefs ignores commented-out tags but keeps line numbers', () => {
  const source = [
    '<html>',
    '<!-- <script src="./retired.js"></script> -->',
    '<!--',
    '  <script src="./also-retired.js"></script>',
    '-->',
    '<!--> <script src="kept.js"></script>',
  ].join('\n');

  const refs = findScriptRefs(source);
  assert.deepEqual(
    refs.map((ref) => [ref.line, ref.src]),
    [[6, 'kept.js']],
  );
});

test('stripHtmlComments preserves length so offsets stay aligned', () => {
  const source = '<a>\n<!-- gone\nstill gone -->\n<script src="x.js"></script>';
  const stripped = stripHtmlComments(source);
  assert.equal(stripped.length, source.length);
  assert.ok(!stripped.includes('still gone'));
  assert.ok(stripped.includes('src="x.js"'));
});

// ── Resolution ────────────────────────────────────────────────────────

test('resolveRef anchors root-absolute paths at the project and relative ones at the file', () => {
  const projectDir = path.resolve('/tmp/app');
  const htmlPath = path.join(projectDir, 'src', 'index.html');

  assert.equal(resolveRef(htmlPath, '/main.js', projectDir), path.join(projectDir, 'main.js'));
  assert.equal(resolveRef(htmlPath, './index.js', projectDir), path.join(projectDir, 'src', 'index.js'));
  assert.equal(resolveRef(htmlPath, 'main.js', projectDir), path.join(projectDir, 'src', 'main.js'));
  assert.equal(resolveRef(htmlPath, 'https://cdn/x.js', projectDir), null);
});

test('a nested entry resolves root-absolute paths against its own package root', () => {
  // throttler-ui hosts widget apps as separate Vite projects; each serves
  // /src/main.tsx from its own directory, not from throttler-ui's root.
  const root = tempProject({
    'index.html': document(['<script src="/src/main.tsx"></script>']),
    'package.json': '{}',
    'src/main.tsx': '',
    'widgets/peb-ui/index.html': document(['<script src="/src/main.tsx"></script>']),
    'widgets/peb-ui/package.json': '{}',
    'widgets/peb-ui/src/main.tsx': '',
  });

  const result = runTool(['--project-dir', root]);
  assert.equal(result.status, 0, result.stderr);
  assert.match(result.stdout, /2 local script references in 2 index\.html files all exist/);
});

test('entryRoot stops at the project directory', () => {
  const root = tempProject({ 'index.html': '', 'package.json': '{}', 'nested/index.html': '' });
  assert.equal(
    resolveRef(path.join(root, 'nested', 'index.html'), '/x.js', root),
    path.join(root, 'x.js'),
  );
});

test('findHtmlEntries skips dependencies, output and caches', () => {
  const root = tempProject({
    'index.html': '',
    'src/index.html': '',
    'node_modules/pkg/index.html': '',
    'dist/index.html': '',
    '.angular/cache/index.html': '',
    'src/app/thing.component.html': '',
  });

  assert.deepEqual(
    findHtmlEntries(root).map((file) => path.relative(root, file)),
    ['index.html', path.join('src', 'index.html')],
  );
});

// ── End to end ────────────────────────────────────────────────────────

test('passes when every local script reference exists', () => {
  const root = tempProject({
    'index.html': document(['<script src="main.js"></script>', '<script src="https://cdn/x.js"></script>']),
    'main.js': 'console.log(1);',
  });

  const result = runTool(['--project-dir', root]);
  assert.equal(result.status, 0, result.stderr);
  assert.match(result.stdout, /1 local script reference in 1 index\.html file all exist/);
});

test('fails, naming file:line and the missing src, when a local script does not exist', () => {
  const root = tempProject({
    'index.html': document(['<script type="module" src="./index.js"></script>']),
  });

  const result = runTool(['--project-dir', root]);
  assert.equal(result.status, 1, result.stdout);
  assert.match(result.stderr, /1 local script reference does not exist/);
  assert.match(result.stderr, /index\.html:4 → \.\/index\.js/);
});

test('reports only the references that are missing', () => {
  const root = tempProject({
    'index.html': document([
      '<script src="present.js"></script>',
      '<script src="absent.js"></script>',
    ]),
    'present.js': '',
  });

  const result = runTool(['--project-dir', root]);
  assert.equal(result.status, 1, result.stdout);
  assert.match(result.stderr, /absent\.js/);
  assert.doesNotMatch(result.stderr, /present\.js/);
});

test('exits 2 rather than passing when there is no index.html to check', () => {
  const root = tempProject({ 'readme.txt': '' });
  const result = runTool(['--project-dir', root]);
  assert.equal(result.status, 2, result.stdout);
  assert.match(result.stderr, /no index\.html/);
});

test('exits 2 on an unknown argument instead of silently skipping', () => {
  const result = spawnSync(process.execPath, [TOOL, '--nope'], { encoding: 'utf8' });
  assert.equal(result.status, 2);
  assert.match(result.stderr, /unknown argument: --nope/);
});

// ── Regression: the bug this check exists for (#990) ──────────────────

test('catches the nexus-console dead tag: ./index.js with no such file', () => {
  const root = tempProject({
    // Shape of the real file: CDN scripts plus a module tag for a file that was
    // never in the repository. The app boots from the builder-injected entry,
    // so nothing else fails.
    'index.html': document([
      '<script src="https://cdn.tailwindcss.com"></script>',
      '<script type="module" src="./index.js"></script>',
    ]),
  });

  const result = runTool(['--project-dir', root]);
  assert.equal(result.status, 1, result.stdout);
  assert.match(result.stderr, /→ \.\/index\.js/);
});

test('passes once the dead tag is removed, with CDN scripts still present', () => {
  const root = tempProject({
    'index.html': document(['<script src="https://cdn.tailwindcss.com"></script>']),
  });

  const result = runTool(['--project-dir', root]);
  assert.equal(result.status, 0, result.stderr);
});

test('checkProject and checkHtmlFile agree on counts', () => {
  const root = tempProject({
    'index.html': document(['<script src="./missing.js"></script>']),
    'src/index.html': document(['<script src="./also-missing.js"></script>']),
  });

  const project = checkProject({ projectDir: root });
  assert.equal(project.entries.length, 2);
  assert.equal(project.checked, 2);
  assert.equal(project.missing.length, 2);

  const single = checkHtmlFile(path.join(root, 'index.html'), root);
  assert.equal(single.checked, 1);
  assert.equal(single.missing[0].src, './missing.js');
});

test('a directory that does not exist is a CheckError, not a pass', () => {
  assert.throws(
    () => checkProject({ projectDir: path.join(os.tmpdir(), 'definitely-absent-app') }),
    (error) => error instanceof CheckError && /not a directory/.test(error.message),
  );
});
