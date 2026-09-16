import { useState } from "react";
import { AlertTriangle, CheckCircle2, Copy, Info, ShieldAlert, XCircle } from "lucide-react";

type Mode = "DEMO" | "FIXTURE" | "LIVE READ" | "LIVE GOVERNED";
type Status = "pending" | "admitted" | "refused" | "unknown" | "stale" | "drift" | "error";

export interface GovernanceResult {
  status: Status;
  reason: string;
  envelopeId?: string;
  evaluationFingerprint?: string;
  admissionReceiptId?: string;
  transitionReceiptId?: string;
  evidenceIds?: string[];
  replayStatus?: string;
}

const statusMeta: Record<Status, { label: string; icon: typeof Info; className: string }> = {
  pending: { label: "Assessment pending", icon: Info, className: "border-primary/40 text-primary" },
  admitted: { label: "Admitted by authority", icon: CheckCircle2, className: "border-accent/50 text-accent" },
  refused: { label: "Refused", icon: XCircle, className: "border-destructive/50 text-destructive" },
  unknown: { label: "Unknown context", icon: Info, className: "border-primary/40 text-primary" },
  stale: { label: "Stale doctrine", icon: AlertTriangle, className: "border-primary/50 text-primary" },
  drift: { label: "Drift detected", icon: ShieldAlert, className: "border-destructive/50 text-destructive" },
  error: { label: "Assessment error", icon: ShieldAlert, className: "border-destructive/50 text-destructive" },
};

export function ModeBanner({ mode }: { mode: Mode }) {
  const synthetic = mode === "DEMO" || mode === "FIXTURE";
  return (
    <div className={`panel flex flex-wrap items-center justify-between gap-3 px-4 py-3 ${synthetic ? "border-primary/40" : "border-accent/40"}`}>
      <div className="flex items-center gap-3">
        <span className={`h-2 w-2 rounded-full ${synthetic ? "bg-primary" : "bg-accent"}`} aria-hidden="true" />
        <div>
          <p className="label-mono text-foreground">Runtime mode · {mode}</p>
          <p className="mt-1 text-xs text-muted-foreground">
            {synthetic ? "Synthetic data — never evidence or authority." : mode === "LIVE READ" ? "Server projection, read-only." : "Governed actions await authoritative admission."}
          </p>
        </div>
      </div>
      <span className="rounded border border-border px-2 py-1 font-mono text-[10px] uppercase text-muted-foreground">
        {synthetic ? "non-authoritative" : mode === "LIVE READ" ? "read-only" : "authority-gated"}
      </span>
    </div>
  );
}

export function ContractStrip({ contractId, version, digest, subject }: { contractId: string; version: number; digest: string; subject: string }) {
  return (
    <section className="panel p-4">
      <div className="mb-3 flex items-center justify-between">
        <div><p className="label-mono">Subject & contract</p><h2 className="mt-1 text-sm font-semibold text-foreground">Governance identity</h2></div>
        <span className="rounded bg-muted px-2 py-1 font-mono text-[10px] text-accent">VERSION {version}</span>
      </div>
      <div className="grid gap-3 text-xs sm:grid-cols-3">
        <IdentityField label="Subject" value={subject} />
        <IdentityField label="Contract" value={contractId} />
        <IdentityField label="Artifact digest" value={digest} copyable />
      </div>
    </section>
  );
}

function IdentityField({ label, value, copyable }: { label: string; value: string; copyable?: boolean }) {
  const copyValue = async () => { await navigator.clipboard?.writeText(value); };
  return <div className="min-w-0"><p className="label-mono text-[10px]">{label}</p><div className="mt-1 flex items-center gap-2"><code className="truncate text-muted-foreground" title={value}>{value}</code>{copyable && <button aria-label={`Copy ${label}`} onClick={copyValue} className="shrink-0 text-muted-foreground hover:text-primary"><Copy className="h-3 w-3" /></button>}</div></div>;
}

export function AssessmentPanel({ result }: { result: GovernanceResult }) {
  const meta = statusMeta[result.status];
  const Icon = meta.icon;
  return <section className={`panel border p-4 ${meta.className}`} aria-live="polite"><div className="flex items-start gap-3"><Icon className="mt-0.5 h-5 w-5 shrink-0" aria-hidden="true" /><div className="min-w-0"><p className="label-mono">Assessment · SOL/SOLScript</p><h2 className="mt-1 text-base font-semibold">{meta.label}</h2><p className="mt-2 text-sm text-muted-foreground">{result.reason}</p>{result.evaluationFingerprint && <IdentityField label="Evaluation fingerprint" value={result.evaluationFingerprint} copyable />}</div></div></section>;
}

export function AuthorityPanel({ result }: { result: GovernanceResult }) {
  const admitted = result.status === "admitted";
  return <section className={`panel border p-4 ${admitted ? "border-accent/50" : "border-border"}`}><div className="flex items-center justify-between"><div><p className="label-mono">Authority · PEB / Conduit</p><h2 className="mt-1 text-base font-semibold">{admitted ? "Authority result: admitted" : "Authority result: not granted"}</h2></div><span className={`rounded px-2 py-1 font-mono text-[10px] uppercase ${admitted ? "bg-accent/15 text-accent" : "bg-muted text-muted-foreground"}`}>{admitted ? "receipt-backed" : "assessment only"}</span></div><div className="mt-4 grid gap-3 text-xs sm:grid-cols-2"><IdentityField label="PEB admission receipt" value={result.admissionReceiptId ?? "Not available"} copyable={!!result.admissionReceiptId} /><IdentityField label="Conduit transition receipt" value={result.transitionReceiptId ?? "Not available"} copyable={!!result.transitionReceiptId} /></div>{!admitted && <p className="mt-4 rounded border border-border bg-background/40 p-3 text-xs text-muted-foreground">No local state was committed. Retry, inspect context, or escalate through the server workflow.</p>}</section>;
}

export function ProvenancePanel({ result }: { result: GovernanceResult }) {
  return <details className="panel group p-4"><summary className="cursor-pointer list-none"><span className="label-mono text-foreground">Provenance & replay</span><span className="float-right text-xs text-muted-foreground group-open:rotate-180">⌄</span></summary><div className="mt-4 grid gap-3 text-xs sm:grid-cols-2"><IdentityField label="Envelope ID" value={result.envelopeId ?? "Missing lineage"} copyable={!!result.envelopeId} /><IdentityField label="Replay status" value={result.replayStatus ?? "Not available"} /><IdentityField label="Evidence IDs" value={result.evidenceIds?.join(", ") ?? "Missing lineage"} copyable={!!result.evidenceIds?.length} /><IdentityField label="Lineage rule" value="UI context is not evidence" /></div></details>;
}

export function GovernedActionDrawer({ open, onClose, onSubmit, result }: { open: boolean; onClose: () => void; onSubmit: () => void; result?: GovernanceResult }) {
  if (!open) return null;
  return <div className="fixed inset-0 z-50 flex justify-end bg-background/70 backdrop-blur-sm" role="dialog" aria-modal="true" aria-labelledby="governed-action-title"><div className="h-full w-full max-w-lg overflow-y-auto border-l border-border bg-surface p-6 shadow-2xl"><div className="flex items-start justify-between"><div><p className="label-mono">Governed action review</p><h2 id="governed-action-title" className="mt-1 text-xl font-semibold">Review proposed change</h2></div><button onClick={onClose} className="text-muted-foreground hover:text-foreground" aria-label="Close review">×</button></div><div className="mt-6 space-y-4"><div className="rounded border border-primary/30 bg-primary/5 p-4"><p className="label-mono text-primary">Proposed state</p><p className="mt-2 text-sm text-foreground">This proposal is not committed. It will be submitted to the governed admission path.</p></div>{result && <><AssessmentPanel result={result} /><AuthorityPanel result={result} /></>}<div className="flex gap-3"><button onClick={onSubmit} className="rounded-md bg-primary px-4 py-2 font-mono text-xs font-semibold uppercase text-primary-foreground">Submit for assessment</button><button onClick={onClose} className="rounded-md border border-border px-4 py-2 font-mono text-xs uppercase text-muted-foreground">Cancel</button></div></div></div></div>;
}

export function GovernanceDemo() {
  const [drawerOpen, setDrawerOpen] = useState(false);
  const result: GovernanceResult = { status: "admitted", reason: "Assessment passed; authority receipt returned by the server.", envelopeId: "env-demo-0007", evaluationFingerprint: `sha256:${"a".repeat(64)}`, admissionReceiptId: "peb-receipt-demo-0007", transitionReceiptId: "conduit-receipt-demo-0007", evidenceIds: ["evidence-demo-0007"], replayStatus: "replay_ok" };
  return <div className="space-y-4"><ModeBanner mode="LIVE GOVERNED" /><ContractStrip contractId="governance.admission" version={3} digest={result.evaluationFingerprint} subject="work-request:0007" /><div className="grid gap-4 lg:grid-cols-2"><AssessmentPanel result={result} /><AuthorityPanel result={result} /></div><ProvenancePanel result={result} /><button onClick={() => setDrawerOpen(true)} className="rounded-md bg-primary px-4 py-2 font-mono text-xs font-semibold uppercase text-primary-foreground">Review governed action</button><GovernedActionDrawer open={drawerOpen} onClose={() => setDrawerOpen(false)} onSubmit={() => setDrawerOpen(false)} result={result} /></div>;
}
