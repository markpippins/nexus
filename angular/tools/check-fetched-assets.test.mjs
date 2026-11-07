import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

import {
  CheckError,
  checkProject,
  collectSourceFiles,
  findAssetReferences,
  resolveBuildOutput,
  stripComments,
  toEmittedPath,
} from './check-fetched-assets.mjs';

const TOOL = path.join(path.dirname(fileURLToPath(import.meta.url)), 'check-fetched-assets.mjs');

function tempProject(tree) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'fetched-assets-'));
  for (const [relative, contents] of Object.entries(tree)) {
    const target = path.join(root, relative);
    if (contents === null) {
      fs.mkdirSync(target, { recursive: true });
      continue;
    }
    fs.mkdirSync(path.dirname(target), { recursive: true });
    fs.writeFileSync(target, contents);
  }
  return root;
}

function angularJson(overrides = {}) {
  return JSON.stringify({
    version: 1,
    projects: {
      app: {
        root: '',
        architect: {
          build: {
            builder: '@angular/build:application',
            options: {
              outputPath: 'dist/app',
              browser: 'src/main.ts',
              assets: [{ glob: '**/*', input: 'src/assets', output: 'assets' }],
              ...overrides.options,
            },
            configurations: {
              production: { outputHashing: 'all', ...overrides.production },
              development: { optimization: false },
            },
          },
        },
      },
    },
  });
}

function component(source = "this.http.get('assets/config/health-services.json');") {
  return `import { Injectable } from '@angular/core';\n\n@Injectable()\nexport class Monitor {\n  load() {\n    ${source}\n  }\n}\n`;
}

function runTool(args) {
  return spawnSync(process.execPath, [TOOL, ...args], { encoding: 'utf8' });
}

// ── URL normalisation ─────────────────────────────────────────────────

test('toEmittedPath normalises the URL forms the build actually emits', () => {
  assert.equal(toEmittedPath('assets/config/health-services.json'), 'assets/config/health-services.json');
  assert.equal(toEmittedPath('/assets/img/logo.svg'), 'assets/img/logo.svg');
  assert.equal(toEmittedPath('./assets/img/logo.svg'), 'assets/img/logo.svg');
  assert.equal(toEmittedPath('assets/fonts/a.woff2?v=2#iefix'), 'assets/fonts/a.woff2');
});

test('toEmittedPath rejects what is not a build-emitted asset path', () => {
  assert.equal(toEmittedPath('https://cdn.example.com/assets/logo.svg'), null);
  assert.equal(toEmittedPath('//cdn.example.com/assets/logo.svg'), null);
  assert.equal(toEmittedPath('data:image/png;base64,AAAA'), null);
  assert.equal(toEmittedPath('assets/config/${name}.json'), null);
  assert.equal(toEmittedPath('assets/img/*.png'), null);
  assert.equal(toEmittedPath('src/assets/logo.svg'), null);
  assert.equal(toEmittedPath('assets/../secret.json'), null);
  assert.equal(toEmittedPath('assets/images/'), null);
});

test('toEmittedPath can require root-absolute URLs for stylesheets', () => {
  assert.equal(toEmittedPath('assets/bg.png', { requireRootAbsolute: true }), null);
  assert.equal(toEmittedPath('/assets/bg.png', { requireRootAbsolute: true }), 'assets/bg.png');
});

// ── Reference discovery ───────────────────────────────────────────────

test('findAssetReferences reports quoted, template-literal and url() references with lines', () => {
  const source = [
    "const a = 'assets/config/health-services.json';",
    'const b = "/assets/img/logo.svg";',
    'const c = `./assets/data/tree.json`;',
    'const style = "background: url(/assets/bg.png)";',
    "fetch('assets/config/preferences.json');",
  ].join('\n');

  const references = findAssetReferences(source, 'src/app/thing.ts');
  assert.deepEqual(
    references.map((reference) => [reference.line, reference.emitted]),
    [
      [1, 'assets/config/health-services.json'],
      [2, 'assets/img/logo.svg'],
      [3, 'assets/data/tree.json'],
      [4, 'assets/bg.png'],
      [5, 'assets/config/preferences.json'],
    ],
  );
});

test('findAssetReferences ignores comments and non-asset URLs', () => {
  const source = [
    "// this.http.get('assets/config/retired.json');",
    "/* fetch('assets/config/also-retired.json') */",
    "const live = 'assets/config/live.json';",
    "const api = 'api/health.json';",
    "const remote = 'https://example.com/assets/remote.svg';",
  ].join('\n');

  const references = findAssetReferences(source, 'src/app/thing.ts');
  assert.deepEqual(
    references.map((reference) => reference.emitted),
    ['assets/config/live.json'],
  );
});

test('findAssetReferences keeps line numbers correct across stripped comments', () => {
  const source = ["/* one", '   two */', "fetch('assets/nope.json');"].join('\n');
  const [reference] = findAssetReferences(source, 'src/app/thing.ts');
  assert.equal(reference.line, 3);
});

test('findAssetReferences only accepts root-absolute URLs in stylesheets', () => {
  const scss = [
    '.local { background: url(assets/local.png); }',
    '.served { background: url(/assets/served.png); }',
    ".quoted { background: url('/assets/quoted.png'); }",
  ].join('\n');

  const references = findAssetReferences(scss, 'src/app/thing.component.scss');
  assert.deepEqual(
    references.map((reference) => reference.emitted).sort(),
    ['assets/quoted.png', 'assets/served.png'],
  );
});

test('stripComments leaves strings and urls intact', () => {
  const source = "const u = 'http://example.com//x'; const v = url(//cdn/x); // gone";
  const stripped = stripComments(source, '.ts');
  assert.ok(stripped.includes("'http://example.com//x'"));
  assert.ok(!stripped.includes('gone'));
  assert.equal(stripped.length, source.length);
});

test('collectSourceFiles skips dependencies, output, caches and test files', () => {
  const root = tempProject({
    'src/app/thing.ts': '',
    'src/app/thing.spec.ts': '',
    'src/app/thing.component.html': '',
    'node_modules/pkg/index.js': '',
    'dist/app/index.html': '',
    '.angular/cache/x.js': '',
  });

  const files = collectSourceFiles(root, { exclude: [path.join(root, 'dist')] }).map((file) =>
    path.relative(root, file),
  );
  assert.deepEqual(files, ['src/app/thing.component.html', 'src/app/thing.ts']);
});

// ── angular.json resolution ───────────────────────────────────────────

test('resolveBuildOutput reads a string outputPath and the default configuration', () => {
  const projectDir = path.resolve('/tmp/project');
  const resolved = resolveBuildOutput(JSON.parse(angularJson()), projectDir);
  assert.equal(resolved.project, 'app');
  assert.equal(resolved.configuration, 'production');
  assert.equal(resolved.outputDir, path.join(projectDir, 'dist/app'));
});

test('resolveBuildOutput honours the { base, browser } object form', () => {
  const projectDir = path.resolve('/tmp/project');
  const withBrowser = resolveBuildOutput(
    JSON.parse(angularJson({ options: { outputPath: { base: './out', browser: 'browser' } } })),
    projectDir,
  );
  assert.equal(withBrowser.outputDir, path.join(projectDir, 'out/browser'));

  const dotBrowser = resolveBuildOutput(
    JSON.parse(angularJson({ options: { outputPath: { base: './out', browser: '.' } } })),
    projectDir,
  );
  assert.equal(dotBrowser.outputDir, path.join(projectDir, 'out'));
});

test('resolveBuildOutput applies an explicit configuration override', () => {
  const projectDir = path.resolve('/tmp/project');
  const resolved = resolveBuildOutput(
    JSON.parse(angularJson({ options: { outputPath: 'dist/app' }, production: { outputPath: 'dist/prod' } })),
    projectDir,
    { configuration: 'production' },
  );
  assert.equal(resolved.outputDir, path.join(projectDir, 'dist/prod'));
});

test('resolveBuildOutput fails loudly on an unknown configuration', () => {
  assert.throws(
    () => resolveBuildOutput(JSON.parse(angularJson()), '/tmp/project', { configuration: 'staging' }),
    (error) => error instanceof CheckError && /no build configuration "staging"/.test(error.message),
  );
});

// ── End to end ────────────────────────────────────────────────────────

test('passes when the referenced asset was emitted by the build', () => {
  const root = tempProject({
    'angular.json': angularJson(),
    'src/main.ts': 'export {};',
    'src/app/service-poll-monitor.component.ts': component(),
    'dist/app/assets/config/health-services.json': '{"services":[]}',
  });

  const result = runTool(['--project-dir', root]);
  assert.equal(result.status, 0, result.stderr);
  assert.match(result.stdout, /1 fetched asset reference emitted by the build/);
});

test('fails, naming the source line and missing path, when the asset was not emitted', () => {
  const root = tempProject({
    'angular.json': angularJson(),
    'src/main.ts': 'export {};',
    'src/app/service-poll-monitor.component.ts': component(),
    // The build ran, but the assets mapping did not copy the config.
    'dist/app/index.html': '<html></html>',
  });

  const result = runTool(['--project-dir', root]);
  assert.equal(result.status, 1, result.stdout);
  assert.match(result.stderr, /1 fetched asset path is not emitted by the build/);
  assert.match(result.stderr, /assets\/config\/health-services\.json/);
  assert.match(result.stderr, /service-poll-monitor\.component\.ts:6/);
  assert.match(result.stderr, /"assets\/config\/health-services\.json"/);
});

test('reports only the references the build did not emit', () => {
  const root = tempProject({
    'angular.json': angularJson(),
    'src/main.ts': component(
      "this.http.get('assets/config/health-services.json');\n    this.http.get('assets/config/preferences.json');",
    ),
    'dist/app/assets/config/health-services.json': '{}',
  });

  const result = runTool(['--project-dir', root]);
  assert.equal(result.status, 1, result.stdout);
  assert.match(result.stderr, /1 fetched asset path is not emitted/);
  assert.match(result.stderr, /assets\/config\/preferences\.json/);
  assert.doesNotMatch(result.stderr, /^\s+assets\/config\/health-services\.json$/m);
});

test('exits 2 rather than passing when the build output is missing', () => {
  const root = tempProject({
    'angular.json': angularJson(),
    'src/main.ts': component(),
  });

  const result = runTool(['--project-dir', root]);
  assert.equal(result.status, 2, result.stdout);
  assert.match(result.stderr, /build output directory not found/);
});

test('exits 2 when there is no angular.json to read', () => {
  const root = tempProject({ 'src/main.ts': component() });
  const result = runTool(['--project-dir', root]);
  assert.equal(result.status, 2, result.stdout);
  assert.match(result.stderr, /no angular\.json/);
});

test('--dist overrides the resolved output directory', () => {
  const root = tempProject({
    'angular.json': angularJson(),
    'src/main.ts': component(),
    'elsewhere/assets/config/health-services.json': '{}',
  });

  const result = runTool(['--project-dir', root, '--dist', 'elsewhere']);
  assert.equal(result.status, 0, result.stderr);
});

test('checkProject reports the reference count and missing entries', () => {
  const root = tempProject({
    'angular.json': angularJson(),
    'src/main.ts': component(),
    'dist/app/index.html': '<html></html>',
  });

  const result = checkProject({ projectDir: root, cwd: root });
  assert.equal(result.checked, 1);
  assert.equal(result.missing.length, 1);
  assert.equal(result.missing[0].emitted, 'assets/config/health-services.json');
  assert.equal(result.missing[0].line, 6);
});
