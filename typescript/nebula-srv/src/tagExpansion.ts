/**
 * Phase C write-side alias expansion — Ruling 26 (7898aa0f) §§1-§5, ratified
 * Ruling 27 (3d021bb6). Companion to `tagNormalizer.ts` (card §4).
 *
 * ── Division of labour with the normalizer ──────────────────────────────────
 *
 * `normalizeTags` REPAIRS. This module EXPANDS. The split matters because the
 * two have different failure postures:
 *
 *   - a repair that cannot recover a tag still keeps it (warn, never drop);
 *   - an expansion that cannot resolve a target must NOT invent a recipient.
 *
 * Ruling 26 §0 named the silent drop as the hazard to design against: an
 * address accepted at write and expanded to nothing reads, downstream, exactly
 * like an address nobody ever sent. So every unresolvable case here ends in a
 * named WARN and a retained tag — never in an empty recipient set.
 *
 * ── WHY THIS BRANCHES ON MODE, NOT ON `targets` ──────────────────────────────
 *
 * The obvious implementation is "if the alias has `targets`, replace the tag
 * with them". That is wrong, and wrong in a way that looks right:
 *
 *   - `all` has NO `targets` key, yet it is the most consequential address in
 *     the system (it fans out to every role). A targets-presence test would
 *     leave `to:all` unexpanded — a silent drop wearing a green tick.
 *   - `self` has no targets and never will: its target is the writer, known
 *     only at write time.
 *   - `assembly` and `user` deliberately have no targets, because fanning them
 *     out is actively harmful (Ruling 26 §2/§3).
 *
 * So the branch key is `expansion.mode`, from a closed six-value vocabulary.
 * Each mode carries its own invariants and its own failure mode. `bin/
 * check_address_expansion_modes.py` enforces the registry half of this same
 * vocabulary; that guard and this module must not drift apart, so the mode
 * strings are asserted against the guard's expectations in
 * `tagExpansion.test.ts`.
 *
 * ── MODE-BY-MODE WRITE-SIDE BEHAVIOUR ───────────────────────────────────────
 *
 *   fan-out-all-roles   §1  expand-and-replace over every roles.json
 *                             `kind: "role"` key. Single-hop, snapshot-at-
 *                             write, deduped against explicit tags.
 *   forum-broadcast     §2  NO fan-out, NO pointer movement. The tag is
 *                             RETAINED as provenance and a delivery intent is
 *                             returned for the caller to act on out-of-band.
 *   operator-notification §3 NO fan-out. Tag retained; single recipient,
 *                             non-blocking, absence observable.
 *   writer-role         §4  resolves to the creating record's own role.
 *                             Unresolvable => WARN `unresolvable-self`. It
 *                             NEVER falls back to `to:all` or `to:user`.
 *   targets-expand      §5  explicit role targets. Empty/absent => WARN
 *                             `alias-unmapped`, tag retained, never reject.
 *   none                R19 no delivery obligation (model identity). Tag
 *                             retained, nothing derived.
 *
 * ── SINGLE-HOP, AND WHY IT IS NOT A LIMITATION HERE ─────────────────────────
 *
 * Ruling 26 §1 makes fan-out single-hop by construction: expansion sources are
 * `kind: "role"` keys only, never aliases. `all-roles` therefore does NOT
 * expand to `all` plus 28 roles — it expands to the same 28 roles as `all`.
 * That is why `all-roles` is harmless rather than an exponential blow-up, and
 * why alias cycles are inexpressible rather than merely guarded against.
 *
 * `expandAddresses` therefore never recurses. It walks the tag list once. A
 * target that is itself a registered alias is a REGISTRY defect (the guard
 * reports it as `alias-as-target`), and this module refuses to expand it
 * rather than trusting the registry to be correct.
 *
 * ── SNAPSHOT-AT-Write ───────────────────────────────────────────────────────
 *
 * The role set is read from the registry once per write and frozen into the
 * stored tags. Registering role #29 therefore does NOT retroactively deliver
 * historical `to:all` records — they carry the tag set that was true when
 * they were written. Consumers must not re-derive recipients from the alias
 * at read time.
 *
 * ── THE FAILURE THIS GUARDS AGAINST MOST CAREFULLY ──────────────────────────
 *
 * `roles.json` on `main` carries 30 keys and NO `kind` field; only the
 * #712/#715 lineage marks all 28 as `kind: "role"`. An expander that trusted
 * `kind: "role"` against the wrong base would resolve `to:all` to an EMPTY
 * recipient set and expand-and-replace it away — turning the loudest address
 * in the system into a total delivery loss, silently, in production.
 *
 * So `loadRegistry` REFUSES an empty role vocabulary. The caller catches,
 * warns, and degrades to pass-through: tags are preserved unexpanded and the
 * defect is loud. That is the same posture as Ruling 26 §5's `alias-unmapped`
 * — visible, never a drop, never a reject.
 */

import * as fs from 'fs';
import * as path from 'path';

/** The closed expansion-mode vocabulary. Anything else is unknown. */
export const EXPANSION_MODES = [
  'fan-out-all-roles',
  'forum-broadcast',
  'operator-notification',
  'writer-role',
  'targets-expand',
  'none',
] as const;

export type ExpansionMode = (typeof EXPANSION_MODES)[number];

/** Modes that deliver via a non-tag channel; the tag is retained, not replaced. */
const MODES_RETAINING_TAG: ReadonlySet<string> = new Set<ExpansionMode>([
  'forum-broadcast',
  'operator-notification',
  'writer-role',
  'none',
]);

/** A delivery intent the write path must act on OUT OF BAND of the tag list. */
export type DeliveryIntent =
  /** §2 — one Assembly thread. The forum write IS the delivery. */
  | { kind: 'forum-broadcast'; address: string }
  /** §3 — single operator recipient, non-blocking. */
  | { kind: 'operator-notification'; address: string }
  /** §4 — the writer's own role, once resolved. */
  | { kind: 'writer-role'; address: string; role: string };

export type ExpansionWarningReason =
  /** §5 — declared, sanctioned: `targets-expand` with no targets. */
  | 'alias-unmapped'
  /** §4 — `to:self` from a writer with no resolvable role. */
  | 'unresolvable-self'
  /** The registry could not be loaded; expansion degraded to pass-through. */
  | 'registry-unavailable'
  /** `roles.json` yielded zero `kind: "role"` keys — refusing to fan out to nothing. */
  | 'empty-role-vocabulary'
  /** The registry declares a mode outside the closed vocabulary. */
  | 'unknown-expansion-mode'
  /** A target is itself a registered alias; single-hop refuses to expand it. */
  | 'alias-as-target'
  /** A target is absent from the roles.json `kind: "role"` vocabulary. */
  | 'target-not-a-role';

export interface ExpansionWarning {
  tag: string;
  reason: ExpansionWarningReason;
  detail: string;
}

export interface ExpandResult {
  /** Tags to store. Class V tags pass through untouched. */
  tags: string[];
  /** Delivery the caller must perform outside the tag list. */
  intents: DeliveryIntent[];
  warnings: ExpansionWarning[];
  /**
   * True when the registry was unavailable or unusable and the tag list was
   * passed through UNEXPANDED. The write is still valid; delivery is the
   * caller's problem to notice. Surfaced so the write site can log loudly
   * rather than treat a no-op expansion as a success.
   */
  degraded: boolean;
}

interface AliasEntry {
  mode: ExpansionMode;
  targets: string[];
}

export interface Registry {
  /** Address name (without the `to:` prefix) -> declared expansion. */
  aliases: Map<string, AliasEntry>;
  /** roles.json keys carrying `kind: "role"`, sorted for deterministic output. */
  roleKeys: string[];
  roleKeySet: Set<string>;
  aliasNames: Set<string>;
}

/**
 * Resolve `config/roles/` from the compiled module location.
 *
 * Compiled, this file lives at `<repo>/typescript/nebula-srv/dist/tagExpansion.js`,
 * so three levels up is the repo root. Overridable by env because the CI
 * integration harness runs the built output from a different cwd.
 */
export function defaultConfigDir(): string {
  const override = process.env.NEXUS_CONFIG_ROLES_DIR;
  if (override) return override;
  return path.resolve(__dirname, '..', '..', '..', 'config', 'roles');
}

/**
 * Read `expansion.mode`, tolerating the bare-string legacy form
 * (`"targets-expand"`). Returns null for anything outside the vocabulary —
 * notably the free-text strings such as `"pending-architect (Phase C)"` that
 * predate Ruling 26 — so an unrecognised mode is an explicit, warned state
 * rather than a silent pass-through.
 */
function readMode(entry: Record<string, unknown>): ExpansionMode | null {
  const exp = entry.expansion;
  const raw =
    typeof exp === 'string'
      ? exp
      : exp && typeof exp === 'object'
        ? (exp as Record<string, unknown>).mode
        : undefined;
  return typeof raw === 'string' && (EXPANSION_MODES as readonly string[]).includes(raw)
    ? (raw as ExpansionMode)
    : null;
}

/**
 * Load both registries and build the expansion index.
 *
 * THROWS rather than returning a partial registry: a caller that cannot tell
 * "registry absent" from "registry says expand to nothing" will eventually
 * choose wrong in the direction that loses mail. `expandTags` catches, warns,
 * and degrades to pass-through.
 *
 * Refuses a zero-role vocabulary outright — see the module docstring: an
 * empty fan-out target set is indistinguishable from a silent drop.
 */
export function loadRegistry(configDir: string = defaultConfigDir()): Registry {
  const kindsPath = path.join(configDir, 'address-kinds.json');
  const rolesPath = path.join(configDir, 'roles.json');

  const kinds = JSON.parse(fs.readFileSync(kindsPath, 'utf8')) as {
    addresses?: Record<string, Record<string, unknown>>;
  };
  const roles = JSON.parse(fs.readFileSync(rolesPath, 'utf8')) as {
    roles?: Record<string, Record<string, unknown>>;
  };

  const addresses = kinds.addresses;
  if (!addresses || typeof addresses !== 'object' || Object.keys(addresses).length === 0) {
    throw new Error(`no \`addresses\` in ${kindsPath}`);
  }

  const roleKeys = Object.entries(roles.roles ?? {})
    .filter(([, v]) => v && typeof v === 'object' && (v as { kind?: unknown }).kind === 'role')
    .map(([k]) => k)
    .sort();
  if (roleKeys.length === 0) {
    throw new Error(
      `no \`kind: "role"\` keys in ${rolesPath}. Refusing to load: a registry ` +
        `with an empty role vocabulary expands \`to:all\` to zero recipients, ` +
        `which is the silent drop Ruling 26 §0 exists to prevent. This is the ` +
        `expected failure on a branch where roles.json predates the kind field.`,
    );
  }

  const aliases = new Map<string, AliasEntry>();
  for (const [name, entry] of Object.entries(addresses)) {
    if (!entry || entry.kind !== 'alias') continue;
    const mode = readMode(entry);
    if (mode === null) continue; // reported at expansion time, per-address
    const rawTargets = entry.targets;
    const targets = Array.isArray(rawTargets) ? rawTargets.filter((t): t is string => typeof t === 'string') : [];
    aliases.set(name, { mode, targets });
  }

  return {
    aliases,
    roleKeys,
    roleKeySet: new Set(roleKeys),
    aliasNames: new Set(aliases.keys()),
  };
}

export interface ExpandOptions {
  /** The creating record's own role — the §4 `to:self` resolution input. */
  writerRole?: string | null;
  /**
   * Pre-loaded registry. Omit to load from disk. Tests inject; production
   * omits. An injected value is used verbatim — including one that reports
   * zero roles, which `loadRegistry` would have refused.
   */
  registry?: Registry | null;
  /** Directory to load from when `registry` is not supplied. */
  configDir?: string;
}

/**
 * Expand every alias address in an already-normalized tag list.
 *
 * Input MUST be the output of `normalizeTags` — this module assumes addresses
 * are already lowercased and split, and does not re-derive that. Order is
 * preserved: expanded recipients are emitted at the position of the alias that
 * produced them, so the stored tag list stays readable and diffable.
 */
export function expandTags(tags: string[], opts: ExpandOptions = {}): ExpandResult {
  const warnings: ExpansionWarning[] = [];
  const intents: DeliveryIntent[] = [];

  let registry = opts.registry;
  let degraded = false;
  if (registry === undefined) {
    try {
      registry = loadRegistry(opts.configDir);
    } catch (err) {
      // Pass through UNEXPANDED. Every alias keeps its tag, so a downstream
      // consumer still sees `to:all` rather than an empty recipient list.
      degraded = true;
      warnings.push({
        tag: '*',
        reason: 'registry-unavailable',
        detail: `${(err as Error).message} — tags stored unexpanded`,
      });
      return { tags: [...tags], intents, warnings, degraded };
    }
  }

  const reg = registry!;
  const seen = new Set<string>(tags);
  const out: string[] = [];

  /** Emit a tag once, preserving first-occurrence order (dedupe). */
  const push = (tag: string): void => {
    if (seen.has(tag)) return;
    seen.add(tag);
    out.push(tag);
  };

  /** Explicit, already-written tags stay exactly where they were written. */
  for (const t of tags) out.push(t);

  for (const tag of tags) {
    if (!tag.toLowerCase().startsWith('to:')) continue;
    const address = tag.slice(3).toLowerCase();

    const alias = reg.aliases.get(address);
    if (!alias) continue; // a role address, or unregistered — normalizer's business

    switch (alias.mode) {
      case 'fan-out-all-roles': {
        // §1 expand-and-replace. The alias tag is DROPPED (replaced by its
        // recipients); dedupe against explicit tags is handled by `seen`.
        const idx = out.indexOf(tag);
        if (idx !== -1) out.splice(idx, 1);
        for (const role of reg.roleKeys) push(`to:${role}`);
        break;
      }

      case 'targets-expand': {
        // §5. Empty/absent targets is the DECLARED unmapped state: retain the
        // tag, warn loudly, never reject and never drop.
        if (alias.targets.length === 0) {
          warnings.push({
            tag,
            reason: 'alias-unmapped',
            detail: `mode='targets-expand' with no targets — ${address} stored unexpanded (Ruling 26 §5)`,
          });
          break;
        }
        const idx = out.indexOf(tag);
        if (idx !== -1) out.splice(idx, 1);
        for (const target of alias.targets) {
          if (reg.aliasNames.has(target)) {
            warnings.push({
              tag: `to:${target}`,
              reason: 'alias-as-target',
              detail: `target of ${address} is itself a registered alias; single-hop refuses to expand it (Ruling 26 §1)`,
            });
            continue;
          }
          if (!reg.roleKeySet.has(target)) {
            warnings.push({
              tag: `to:${target}`,
              reason: 'target-not-a-role',
              detail: `target of ${address} is not a roles.json kind:"role" key; registry is out of date`,
            });
            continue;
          }
          push(`to:${target}`);
        }
        break;
      }

      case 'writer-role': {
        // §4. Deterministic is not the same as always resolvable: a service, a
        // harness, or a direct-DB writer has no role. Never fall back.
        const writer = (opts.writerRole ?? '').trim().toLowerCase();
        if (!writer) {
          warnings.push({
            tag,
            reason: 'unresolvable-self',
            detail: `${address} written by a role-less writer — no expansion, no fallback (Ruling 26 §4)`,
          });
          break;
        }
        const idx = out.indexOf(tag);
        if (idx !== -1) out.splice(idx, 1);
        push(`to:${writer}`);
        intents.push({ kind: 'writer-role', address, role: writer });
        break;
      }

      case 'none': {
        // R19: a model identity carries NO delivery obligation. The tag is
        // retained (it is the historical record of who was addressed) but
        // there is nothing to deliver, so NO intent is emitted. Emitting one
        // here would have a caller post an Assembly thread or notify the
        // operator about an address that by definition has no recipient.
        break;
      }

      case 'forum-broadcast':
      case 'operator-notification': {
        // Tag RETAINED as provenance. §2/§3 forbid fan-out and pointer
        // movement. The delivery is returned as an intent for the caller to
        // perform out-of-band — the normalizer must not synthesize per-role
        // tags to fake it.
        intents.push({ kind: alias.mode, address });
        break;
      }

      default: {
        // Unreachable while the registry only stores validated modes, but a
        // hand-edited registry must not silently pass through.
        warnings.push({
          tag,
          reason: 'unknown-expansion-mode',
          detail: `mode ${(alias.mode as string) ?? 'undefined'} is outside the closed vocabulary — ${address} stored unexpanded`,
        });
        break;
      }
    }
  }

  return { tags: out, intents, warnings, degraded };
}

/** True when the mode retains its tag rather than expanding in place. */
export function modeRetainsTag(mode: ExpansionMode): boolean {
  return MODES_RETAINING_TAG.has(mode);
}