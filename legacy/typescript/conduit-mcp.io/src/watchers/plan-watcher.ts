import { BaseWatcher } from "./base";
import { PlanCard } from "../types";
import {
  getPlansGroupedByStatus,
  planRowToPlanCard,
} from "../db";

export class PlanWatcher extends BaseWatcher {
  plans: {
    pending: PlanCard[];
    active: PlanCard[];
    completed: PlanCard[];
    blocked: PlanCard[];
    archived: PlanCard[];
    proposed: PlanCard[];
    planning: PlanCard[];
  } = {
    pending: [],
    active: [],
    completed: [],
    blocked: [],
    archived: [],
    proposed: [],
    planning: [],
  };

  /** Interval handle for periodic DB refresh. Null if not yet started. */
  private refreshTimer: ReturnType<typeof setInterval> | null = null;

  /** Guard flag to prevent overlapping async reloads. */
  private loading = false;

  /** How often (in ms) to re-query the DB for plan changes. */
  private readonly REFRESH_INTERVAL_MS = 30_000;

  constructor(baseDir: string, emit: (event: any) => void) {
    super(baseDir, emit);
  }

  async initialize(): Promise<void> {
    await this.loadFromDb();
    // Periodically reload from DB so in-memory cache stays current
    // when plans are created/updated outside of SSE events (direct DB writes,
    // nebula-mcp integration, etc.).
    this.refreshTimer = setInterval(() => this.loadFromDb(), this.REFRESH_INTERVAL_MS);
  }

  destroy(): void {
    if (this.refreshTimer !== null) {
      clearInterval(this.refreshTimer);
      this.refreshTimer = null;
    }
  }

  /** Reload all plan groups from the database. Clears existing arrays first. */
  async loadFromDb(): Promise<void> {
    if (this.loading) return; // skip overlapping refreshes
    this.loading = true;
    try {
      const grouped = await getPlansGroupedByStatus();
      const ALL_DIRS = [
        "pending",
        "active",
        "completed",
        "blocked",
        "proposed",
        "planning",
      ] as const;
      // Clear all arrays first, then repopulate — prevents stale entries and
      // duplicates when plans move between statuses between refresh cycles.
      for (const dir of ALL_DIRS) {
        this.plans[dir] = [];
      }
      for (const dir of ALL_DIRS) {
        const rows = grouped[dir] || [];
        for (const row of rows) {
          const card = planRowToPlanCard(row);
          this.plans[dir].push(card);
        }
      }
    } catch (err) {
      console.warn(
        "plan-watcher: DB reload failed — plans may be stale.",
        err,
      );
    } finally {
      this.loading = false;
    }
  }
}
