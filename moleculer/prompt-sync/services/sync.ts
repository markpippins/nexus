import { fetchLatestPrompts, fetchActiveTasks, PromptRow, TaskRow } from "./store";
import {
  getRedis,
  PROC_KEY,
  IDX_KEY,
  META_UPDATED_KEY,
  TASK_IDX_KEY,
} from "./store";

export interface PromptIndexEntry {
  slug: string;
  title: string;
  version: number;
  tags: string[];
  // We deliberately don't cache body_md in the index — it can be large
  // (the operator system-prompt BASE is ~2KB, persona cards run 5-15KB).
  // The index is for "which prompts exist for this role?" lookups at
  // launch time; the full body lives under prompt:proc:{role}::{slug}.
  updated_at: string;
}

export interface TaskIndexEntry {
  task_slug: string;
  scope: string;
  acceptance_criteria: string[];
  prompt_id: string;
  // We cache prompt_id so a launching agent can resolve the task's prompt
  // template in a single Redis GET(prompt:proc:{role}::{slug}) without a
  // second round-trip to PG to look up which prompt the task references.
  // The prompt slug is NOT stored on the task row — it's dereferenced via
  // prompt_id → tackle.prompts.slug. We resolve that join here at sync
  // time and stash the slug in `prompt_slug` for convenience.
  prompt_slug: string;
  updated_at: string;
}

export interface PromptCard {
  id: string;
  role: string;
  slug: string;
  version: number;
  title: string;
  body_md: string;
  parameter_schema: Record<string, any>;
  tags: string[];
  created_at: string;
  updated_at: string;
}

/**
 * Full sync: read the latest prompt for each (role, slug) and all active
 * tasks from PG, write to Redis. Called on startup and on POST /refresh.
 *
 * Mirror of role-memory-srv/sync.ts but for the prompts/tasks registry.
 * The key space (`prompt:*`, `task:*`) is disjoint from the procedure
 * registry's `mem:*` space so the two sync servers can run concurrently
 * without colliding.
 */
export async function syncAll(): Promise<{
  prompts: number;
  rolePromptIndices: number;
  tasks: number;
  roleTaskIndices: number;
  timestamp: string;
}> {
  const redis = getRedis();
  const [prompts, tasks] = await Promise.all([
    fetchLatestPrompts(),
    fetchActiveTasks(),
  ]);

  const now = new Date().toISOString();
  const pipeline = redis.pipeline();

  // ── 1. Per-prompt cards + per-role prompt indices ──────────────────
  const rolePromptIdx = new Map<string, PromptIndexEntry[]>();

  for (const p of prompts) {
    const card: PromptCard = {
      id: p.id,
      role: p.role,
      slug: p.slug,
      version: p.version,
      title: p.title,
      body_md: p.body_md,
      parameter_schema: p.parameter_schema,
      tags: p.tags,
      created_at: p.created_at,
      updated_at: p.updated_at,
    };

    // prompt:proc:{role}::{slug}
    pipeline.set(PROC_KEY(p.role, p.slug), JSON.stringify(card));

    const idx = rolePromptIdx.get(p.role) || [];
    idx.push({
      slug: p.slug,
      title: p.title,
      version: p.version,
      tags: p.tags,
      updated_at: p.updated_at,
    });
    rolePromptIdx.set(p.role, idx);
  }

  // Write per-role prompt indices
  for (const [role, entries] of rolePromptIdx) {
    pipeline.set(IDX_KEY(role), JSON.stringify(entries));
  }

  // ── 2. Per-role task indices ──────────────────────────────────────
  // Resolve prompt_id → slug via a lookup map built from the prompts
  // we already fetched. This avoids an extra PG round-trip per task.
  const promptIdToSlug = new Map<string, string>();
  for (const p of prompts) {
    promptIdToSlug.set(p.id, p.slug);
  }

  const roleTaskIdx = new Map<string, TaskIndexEntry[]>();

  for (const t of tasks) {
    const promptSlug = promptIdToSlug.get(t.prompt_id) ?? "";

    const entry: TaskIndexEntry = {
      task_slug: t.task_slug,
      scope: t.scope,
      acceptance_criteria: t.acceptance_criteria,
      prompt_id: t.prompt_id,
      prompt_slug: promptSlug,
      updated_at: t.updated_at,
    };

    const idx = roleTaskIdx.get(t.role) || [];
    idx.push(entry);
    roleTaskIdx.set(t.role, idx);
  }

  for (const [role, entries] of roleTaskIdx) {
    pipeline.set(TASK_IDX_KEY(role), JSON.stringify(entries));
  }

  // ── 3. Global last-sync timestamp ──────────────────────────────────
  pipeline.set(META_UPDATED_KEY, now);

  // ioredis pipeline.exec() resolves with [error, reply][] and does NOT
  // throw on per-command failure. Surface write failures so /refresh
  // returns HTTP 500 instead of fake success with PG-read counts.
  // (Same defensive pattern as role-memory-srv — keeps a single broken
  // SET from looking like a healthy sync.)
  const results = await pipeline.exec();
  if (results) {
    const failures = results.filter(([err]) => err !== null);
    if (failures.length > 0) {
      const first = failures[0][0] as Error;
      throw new Error(
        `Redis pipeline write failed: ${failures.length}/${results.length} commands failed. First error: ${first.message}`
      );
    }
  }

  return {
    prompts: prompts.length,
    rolePromptIndices: rolePromptIdx.size,
    tasks: tasks.length,
    roleTaskIndices: roleTaskIdx.size,
    timestamp: now,
  };
}
