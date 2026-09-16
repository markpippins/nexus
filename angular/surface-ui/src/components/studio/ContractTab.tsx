import * as React from "react";
import { CapabilityId } from "@/core/types/designIR";
import { CAPABILITY_REGISTRY, getCapabilityForWidget } from "@/lib/capabilities-registry";
import type { Widget } from "@/lib/widget-types";
import { CheckCircle2, ShieldCheck, Sparkles, Zap } from "lucide-react";

interface ContractTabProps {
  widget: Widget;
  selectedCapability: CapabilityId;
  onSelectCapability: (cap: CapabilityId) => void;
}

export function ContractTab({ widget, selectedCapability, onSelectCapability }: ContractTabProps) {
  const capMeta = CAPABILITY_REGISTRY[selectedCapability] || CAPABILITY_REGISTRY.EntityCollection;
  const capabilitiesList = Object.values(CAPABILITY_REGISTRY);

  return (
    <div className="space-y-6">
      {/* Capability Contract Selector */}
      <div className="panel p-5 space-y-4">
        <div className="flex items-center justify-between">
          <div>
            <h3 className="text-base font-semibold text-foreground flex items-center gap-2">
              <ShieldCheck className="h-4 w-4 text-primary" />
              <span>Assigned Capability Contract</span>
            </h3>
            <p className="text-xs text-muted-foreground mt-0.5">
              The presentation capability contract this widget fulfills. DesignIR targets this
              contract, not the widget code.
            </p>
          </div>
          <span className="rounded border border-primary/30 bg-primary/10 px-2 py-0.5 font-mono text-xs text-primary font-bold">
            {selectedCapability}
          </span>
        </div>

        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-2 pt-2">
          {capabilitiesList.map((cap) => {
            const isSelected = cap.id === selectedCapability;
            return (
              <button
                key={cap.id}
                type="button"
                onClick={() => onSelectCapability(cap.id)}
                className={`flex flex-col items-start p-3 rounded-lg border text-left transition-all text-xs ${
                  isSelected
                    ? "border-primary bg-primary/15 text-primary shadow-xs ring-1 ring-primary/40"
                    : "border-border/60 bg-surface/60 text-muted-foreground hover:bg-surface hover:text-foreground hover:border-border"
                }`}
              >
                <div className="flex items-center justify-between w-full">
                  <span className="font-semibold text-[13px] text-foreground">{cap.name}</span>
                  {isSelected && <CheckCircle2 className="h-3.5 w-3.5 text-primary" />}
                </div>
                <span className="font-mono text-[10px] opacity-70 mt-1">{cap.id}</span>
                <span className="text-[11px] text-muted-foreground line-clamp-2 mt-1.5 leading-snug">
                  {cap.description}
                </span>
              </button>
            );
          })}
        </div>
      </div>

      {/* Contract Input Schema & Events */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Input Schema Definition */}
        <div className="panel p-5 space-y-4">
          <div className="flex items-center justify-between border-b border-border/40 pb-2">
            <div>
              <h4 className="text-sm font-semibold text-foreground">Typed Input Schema Contract</h4>
              <p className="text-[11px] text-muted-foreground">
                Properties expected by {capMeta.name}
              </p>
            </div>
            <span className="font-mono text-[10px] text-accent">READ-ONLY CONTRACT</span>
          </div>

          <div className="space-y-2.5">
            {Object.entries(capMeta.inputSchema).map(([fieldName, spec]) => (
              <div
                key={fieldName}
                className="rounded-md border border-border/50 bg-background/50 p-2.5 space-y-1 font-mono text-xs"
              >
                <div className="flex items-center justify-between">
                  <span className="font-bold text-foreground">{fieldName}</span>
                  <div className="flex items-center gap-2">
                    <span className="text-accent text-[11px]">{spec.type}</span>
                    {spec.required ? (
                      <span className="rounded bg-destructive/15 px-1.5 py-0.2 text-[9px] text-destructive border border-destructive/30">
                        REQUIRED
                      </span>
                    ) : (
                      <span className="rounded bg-muted px-1.5 py-0.2 text-[9px] text-muted-foreground">
                        OPTIONAL
                      </span>
                    )}
                  </div>
                </div>
                <p className="text-[11px] text-muted-foreground font-sans">{spec.description}</p>
              </div>
            ))}
          </div>
        </div>

        {/* Emitted Output Events */}
        <div className="panel p-5 space-y-4">
          <div className="flex items-center justify-between border-b border-border/40 pb-2">
            <div>
              <h4 className="text-sm font-semibold text-foreground">Interactive Output Events</h4>
              <p className="text-[11px] text-muted-foreground">
                Verbs and payloads emitted to the ViewSpec event bus
              </p>
            </div>
            <span className="font-mono text-[10px] text-signal">EVENT BUS READY</span>
          </div>

          {capMeta.outputEvents.length === 0 ? (
            <div className="p-6 text-center text-xs text-muted-foreground">
              This contract is purely passive presentation with no interactive output events.
            </div>
          ) : (
            <div className="space-y-2.5">
              {capMeta.outputEvents.map((evt) => (
                <div
                  key={evt.name}
                  className="rounded-md border border-border/50 bg-background/50 p-2.5 space-y-1 font-mono text-xs"
                >
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <Zap className="h-3.5 w-3.5 text-signal" />
                      <span className="font-bold text-foreground">
                        on{evt.name[0].toUpperCase() + evt.name.slice(1)}
                      </span>
                    </div>
                    <span className="rounded bg-signal/15 px-1.5 py-0.2 text-[10px] font-bold text-signal border border-signal/30">
                      verb: {evt.name}
                    </span>
                  </div>
                  <div className="text-[11px] text-muted-foreground">
                    Payload: <code className="text-accent">{evt.payloadType}</code>
                  </div>
                  <p className="text-[11px] text-muted-foreground font-sans">{evt.description}</p>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Contract JSON Sample */}
      <div className="panel p-5 space-y-3">
        <div className="flex items-center justify-between">
          <span className="text-xs font-semibold text-foreground flex items-center gap-1.5">
            <Sparkles className="h-3.5 w-3.5 text-accent" />
            <span>Canonical Contract JSON Blueprint</span>
          </span>
          <span className="font-mono text-[10px] text-muted-foreground">
            {Object.keys(capMeta.sampleInput).length} contract fields
          </span>
        </div>
        <pre className="max-h-64 overflow-auto rounded-lg border border-border/60 bg-background/80 p-3.5 font-mono text-xs text-foreground/90 leading-relaxed custom-scrollbar">
          {JSON.stringify(capMeta.sampleInput, null, 2)}
        </pre>
      </div>
    </div>
  );
}
