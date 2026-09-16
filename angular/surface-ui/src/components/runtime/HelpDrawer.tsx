import * as React from "react";
import {
  BookOpen,
  X,
  Play,
  FileCode,
  Layers,
  Sparkles,
  ExternalLink,
  CheckCircle2,
  HelpCircle,
  Copy,
  Check,
} from "lucide-react";
import { DocumentationEntry } from "@/core/runtime/documentationRegistry";
import { toast } from "sonner";

interface HelpDrawerProps {
  isOpen: boolean;
  onClose: () => void;
  entry: DocumentationEntry;
  onOpenVideo?: () => void;
  onDispatchAction?: (verb: string) => void;
}

export const HelpDrawer: React.FC<HelpDrawerProps> = ({
  isOpen,
  onClose,
  entry,
  onOpenVideo,
  onDispatchAction,
}) => {
  const [copied, setCopied] = React.useState(false);

  if (!isOpen) return null;

  const handleCopyJson = () => {
    if (entry.examplePayload) {
      navigator.clipboard.writeText(JSON.stringify(entry.examplePayload, null, 2));
      setCopied(true);
      toast.success("Example payload copied to clipboard");
      setTimeout(() => setCopied(false), 2000);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-background/60 backdrop-blur-xs transition-opacity animate-in fade-in">
      <div
        className="w-full max-w-xl bg-surface border-l border-border shadow-2xl flex flex-col h-full overflow-hidden animate-in slide-in-from-right duration-200"
        role="dialog"
        aria-modal="true"
      >
        {/* Header */}
        <div className="p-5 border-b border-border/60 bg-surface/90 flex items-start justify-between gap-3">
          <div className="space-y-1">
            <div className="flex items-center gap-2">
              <span className="rounded bg-primary/15 border border-primary/30 px-2 py-0.5 text-[10px] font-mono font-bold text-primary uppercase">
                {entry.category} Manual
              </span>
              {entry.capabilityContract && (
                <span className="rounded bg-accent/15 border border-accent/30 px-2 py-0.5 text-[10px] font-mono font-semibold text-accent">
                  Contract: {entry.capabilityContract}
                </span>
              )}
            </div>
            <h2 className="text-xl font-bold text-foreground tracking-tight">{entry.title}</h2>
            {entry.subtitle && <p className="text-xs text-muted-foreground">{entry.subtitle}</p>}
          </div>

          <button
            type="button"
            onClick={onClose}
            className="p-1.5 rounded-md text-muted-foreground hover:text-foreground hover:bg-surface/80 transition-colors"
            aria-label="Close manual drawer"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        {/* Content Body */}
        <div className="flex-1 overflow-y-auto p-5 space-y-6 custom-scrollbar text-xs">
          {/* Summary Callout */}
          <div className="p-4 rounded-lg bg-primary/10 border border-primary/20 space-y-2">
            <div className="flex items-center gap-2 font-mono text-primary font-bold text-[11px]">
              <Sparkles className="h-4 w-4" />
              <span>Operator Synopsis</span>
            </div>
            <p className="text-foreground/90 text-xs leading-relaxed">{entry.summary}</p>
          </div>

          {/* Video Walkthrough CTA */}
          {entry.videoTitle && onOpenVideo && (
            <div className="p-4 rounded-lg bg-surface/80 border border-border flex items-center justify-between gap-3">
              <div className="space-y-0.5">
                <div className="font-semibold text-foreground flex items-center gap-1.5 text-xs">
                  <Play className="h-3.5 w-3.5 text-signal fill-signal" />
                  <span>{entry.videoTitle}</span>
                </div>
                <div className="text-[10px] text-muted-foreground">
                  Interactive Video Walkthrough · {entry.videoDuration || "2m"}
                </div>
              </div>
              <button
                type="button"
                onClick={onOpenVideo}
                className="px-3 py-1.5 rounded-md bg-signal text-signal-foreground font-mono text-[11px] font-semibold hover:bg-signal/90 transition-all shadow-xs"
              >
                Watch Guide
              </button>
            </div>
          )}

          {/* Key Actions & Verbs */}
          {entry.keyActions && entry.keyActions.length > 0 && (
            <div className="space-y-2.5">
              <div className="label-mono text-[11px] text-muted-foreground">
                Canonical Interaction Verbs & Actions:
              </div>
              <div className="space-y-2">
                {entry.keyActions.map((act, i) => (
                  <div
                    key={i}
                    className="p-3 rounded-lg border border-border/60 bg-background/50 flex items-center justify-between gap-3"
                  >
                    <div className="space-y-0.5">
                      <div className="font-bold text-foreground flex items-center gap-2">
                        {act.verb && (
                          <span className="font-mono text-[10px] text-primary bg-primary/10 px-1.5 py-0.5 rounded border border-primary/20">
                            {act.verb}
                          </span>
                        )}
                        <span>{act.label}</span>
                      </div>
                      <div className="text-[11px] text-muted-foreground">{act.description}</div>
                    </div>

                    {act.verb && onDispatchAction && (
                      <button
                        type="button"
                        onClick={() => onDispatchAction(act.verb!)}
                        className="px-2.5 py-1 rounded bg-surface hover:bg-surface/80 border border-border/80 text-[10px] font-mono font-semibold text-foreground transition-all"
                      >
                        Dispatch {act.verb}
                      </button>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Schema Fields */}
          {entry.schemaFields && entry.schemaFields.length > 0 && (
            <div className="space-y-2.5">
              <div className="label-mono text-[11px] text-muted-foreground">
                Capability Contract Schema Fields:
              </div>
              <div className="rounded-lg border border-border overflow-hidden">
                <table className="w-full text-left font-mono text-[11px]">
                  <thead className="bg-surface/90 border-b border-border text-muted-foreground text-[10px]">
                    <tr>
                      <th className="p-2.5">Field</th>
                      <th className="p-2.5">Type</th>
                      <th className="p-2.5">Description</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border/60">
                    {entry.schemaFields.map((f, i) => (
                      <tr key={i} className="hover:bg-surface/40">
                        <td className="p-2.5 font-bold text-primary">{f.name}</td>
                        <td className="p-2.5 text-accent">{f.type}</td>
                        <td className="p-2.5 text-muted-foreground font-sans">{f.description}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* Markdown Details */}
          {entry.detailsMarkdown && (
            <div className="space-y-2">
              <div className="label-mono text-[11px] text-muted-foreground">Manual Details:</div>
              <div className="p-4 rounded-lg border border-border/60 bg-background/50 font-sans text-xs text-foreground/90 space-y-2 leading-relaxed whitespace-pre-line">
                {entry.detailsMarkdown}
              </div>
            </div>
          )}

          {/* Example JSON Payload */}
          {entry.examplePayload && (
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <div className="label-mono text-[11px] text-muted-foreground">
                  ContractStateStore Initial Payload:
                </div>
                <button
                  type="button"
                  onClick={handleCopyJson}
                  className="flex items-center gap-1 text-[10px] font-mono text-primary hover:underline"
                >
                  {copied ? <Check className="h-3 w-3" /> : <Copy className="h-3 w-3" />}
                  <span>{copied ? "Copied" : "Copy Payload"}</span>
                </button>
              </div>
              <pre className="p-3.5 rounded-lg border border-border/60 bg-background font-mono text-[10px] text-foreground custom-scrollbar max-h-48 overflow-auto">
                {JSON.stringify(entry.examplePayload, null, 2)}
              </pre>
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="p-4 border-t border-border/60 bg-surface/90 flex items-center justify-between text-xs">
          <span className="font-mono text-[10px] text-muted-foreground">
            Nexus Vision VM · Ephemeral Manual Overlay
          </span>
          <button
            type="button"
            onClick={onClose}
            className="px-4 py-1.5 rounded-md bg-surface border border-border hover:bg-surface/80 text-foreground font-semibold text-xs"
          >
            Close Manual
          </button>
        </div>
      </div>
    </div>
  );
};
