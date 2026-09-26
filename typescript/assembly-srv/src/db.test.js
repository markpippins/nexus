import { describe, it, expect } from 'vitest';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { splitSqlStatements } from './db.js';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const MIGRATION = path.resolve(__dirname, '..', 'assembly-migration.sql');

describe('splitSqlStatements (incident: supervisor assembly.users seed silently dropped)', () => {
  it('does not split on a semicolon inside a -- comment', () => {
    const sql = [
      '-- administration only; it receives no WorkRequest execution role.',
      "INSERT INTO assembly.users (alias) VALUES ('supervisor');",
    ].join('\n');
    const stmts = splitSqlStatements(sql);
    expect(stmts).toHaveLength(1);
    expect(stmts[0].startsWith('INSERT INTO')).toBe(true);
  });

  it('does not split on a semicolon inside a string literal', () => {
    const stmts = splitSqlStatements(
      "INSERT INTO t (a) VALUES ('x; y');\nINSERT INTO t (a) VALUES ('z');"
    );
    expect(stmts).toHaveLength(2);
    expect(stmts[0]).toContain("'x; y'");
  });

  it('preserves escaped single quotes inside literals', () => {
    const stmts = splitSqlStatements("INSERT INTO t (a) VALUES ('it''s; fine');");
    expect(stmts).toHaveLength(1);
    expect(stmts[0]).toContain("it''s; fine");
  });

  it('handles quoted identifiers containing semicolons', () => {
    const stmts = splitSqlStatements('ALTER TABLE t ADD COLUMN "we;ird" text;');
    expect(stmts).toHaveLength(1);
  });

  it('drops empty fragments and trims whitespace', () => {
    const stmts = splitSqlStatements("  SELECT 1 ;  ;\n\n SELECT 2;   ");
    expect(stmts).toEqual(['SELECT 1', 'SELECT 2']);
  });

  it('every statement in the real migration begins with SQL, not prose', () => {
    const sql = fs.readFileSync(MIGRATION, 'utf-8');
    const stmts = splitSqlStatements(sql);
    expect(stmts.length).toBeGreaterThan(0);
    for (const stmt of stmts) {
      const first = stmt.split('\n')[0].trim();
      expect(first, `bad fragment: ${first}`).toMatch(
        /^(SELECT|INSERT|UPDATE|DELETE|ALTER|CREATE|DROP|BEGIN|COMMIT|WITH|DO|SET|GRANT|COMMENT|VALUES|\\)/
      );
    }
  });

  it('the supervisor seed survives splitting as its own executable statement', () => {
    const sql = fs.readFileSync(MIGRATION, 'utf-8');
    const stmts = splitSqlStatements(sql);
    const seeds = stmts.filter(
      (s) => /INSERT INTO assembly\.users/i.test(s) && /'supervisor'/i.test(s)
    );
    expect(seeds).toHaveLength(1);
    expect(seeds[0]).toMatch(/ON CONFLICT \(alias\) DO NOTHING/i);
  });
});
