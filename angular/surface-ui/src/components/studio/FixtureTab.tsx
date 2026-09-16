import * as React from "react";
import { CapabilityId } from "@/core/types/designIR";
import { FIXTURE_PRESETS, FixtureScenario, generateCapabilityFixture } from "@/lib/fixtures-engine";
import { WidgetSandbox } from "@/components/WidgetSandbox";
import type { Widget } from "@/lib/widget-types";
import { Activity, Copy, Check, Play, RefreshCw, Terminal, Sliders } from "lucide-react";
import { toast } from "sonner";

interface FixtureTabProps {
  widget: Widget;
  selectedCapability: CapabilityId;
}

export function FixtureTab({ widget, selectedCapability }: FixtureTabProps) {
  const [activeScenario, setActiveScenario] = React.useState<FixtureScenario>("nominal");
  const [seed, setSeed] = React.useState(Date.now());
  const [logs, setLogs] = React.useState<string[]>([]);
  const [copied, setCopied] = React.useState(false);

  // Generate current fixture payload
  const fixtureData = React.useMemo(() => {
    return generateCapabilityFixture(selectedCapability, activeScenario, seed);
  }, [selectedCapability, activeScenario, seed]);

  const handleCopyFixture = () => {
    navigator.clipboard.writeText(JSON.stringify(fixtureData, null, 2));
    setCopied(true);
    toast.success("Copied fixture JSON to clipboard");
    setTimeout(() => setCopied(false), 2000);
  };

  const handleTriggerFuzz = () => {
    setSeed(Date.now());
    setActiveScenario("fuzz");
    setLogs((prev) => [
      ...prev.slice(-8),
      `[FIXTURE-FUZZ] Generated stochastic noise seed: ${Date.now()}`,
    ]);
  };

  return (
    <div className="space-y-6">
      {/* Scenario Presets Selector */}
      <div className="panel p-4 space-y-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Activity className="h-4 w-4 text-accent" />
            <h3 className="text-sm font-semibold text-foreground">Synthetic Fixture Scenarios</h3>
          </div>
          <button
            type="button"
            onClick={handleTriggerFuzz}
            className="flex items-center gap-1.5 rounded-md border border-accent/40 bg-accent/10 px-2.5 py-1 text-xs font-mono text-accent hover:bg-accent/20 transition-colors"
          >
            <RefreshCw className="h-3 w-3" />
            <span>Generate Fuzz Seed</span>
          </button>
        </div>

        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-2">
          {FIXTURE_PRESETS.map((preset) => {
            const isActive = activeScenario === preset.id;
            return (
              <button
                key={preset.id}
                type="button"
                onClick={() => {
                  setActiveScenario(preset.id);
                  setLogs((prev) => [
                    ...prev.slice(-8),
                    `[FIXTURE] Switched scenario to: ${preset.id.toUpperCase()}`,
                  ]);
                }}
                className={`flex flex-col items-start p-3 rounded-lg border text-left transition-all text-xs ${
                  isActive
                    ? "border-accent bg-accent/15 text-accent ring-1 ring-accent/40 shadow-xs"
                    : "border-border/60 bg-surface/60 text-muted-foreground hover:bg-surface hover:text-foreground hover:border-border"
                }`}
              >
                <div className="flex items-center justify-between w-full">
                  <span className="font-semibold text-foreground">{preset.name.split("/")[0]}</span>
                  <span
                    className={`rounded px-1.5 py-0.2 text-[9px] font-mono font-bold ${
                      isActive
                        ? "bg-accent text-accent-foreground"
                        : "bg-muted text-muted-foreground"
                    }`}
                  >
                    {preset.badge}
                  </span>
                </div>
                <span className="text-[11px] text-muted-foreground line-clamp-2 mt-1 leading-snug">
                  {preset.description}
                </span>
              </button>
            );
          })}
        </div>
      </div>

      {/* Interactive Sandbox with Fixture Data & Event Stream */}
      <div className="grid grid-cols-1 lg:grid-cols-[1.3fr_1fr] gap-6">
        {/* Sandbox Canvas */}
        <section className="panel overflow-hidden flex flex-col">
          <div className="flex items-center justify-between border-b border-border px-4 py-2.5 bg-surface/50">
            <div className="flex items-center gap-2">
              <span className="live-dot" />
              <span className="label-mono">sandbox render // {activeScenario} fixture</span>
            </div>
            <span className="font-mono text-[10px] text-muted-foreground">
              Capability: {selectedCapability}
            </span>
          </div>

          <div className="flex min-h-80 flex-1 items-center justify-center bg-background/50 p-6 overflow-auto">
            <WidgetSandbox
              widget={widget}
              props={fixtureData}
              onLog={(line) => setLogs((prev) => [...prev.slice(-8), line])}
              className="w-full max-w-xl"
            />
          </div>

          {/* Event Stream Terminal */}
          <div className="border-t border-border bg-background/80 p-4 space-y-2 font-mono">
            <div className="flex items-center justify-between text-[11px]">
              <span className="flex items-center gap-1.5 text-muted-foreground">
                <Terminal className="h-3.5 w-3.5 text-accent" />
                <span>Intercepted Output Events & Logs</span>
              </span>
              <button
                type="button"
                onClick={() => setLogs([])}
                className="text-[10px] text-muted-foreground hover:text-foreground"
              >
                Clear Stream
              </button>
            </div>
            <div className="min-h-20 max-h-28 overflow-y-auto space-y-1 rounded bg-black/40 p-2.5 text-xs text-accent custom-scrollbar">
              {logs.length > 0 ? (
                logs.map((log, idx) => (
                  <div key={idx} className="leading-tight flex items-start gap-1.5">
                    <span className="text-muted-foreground select-none">&gt;</span>
                    <span>{log}</span>
                  </div>
                ))
              ) : (
                <div className="text-muted-foreground text-[11px] italic">
                  Waiting for widget interactions or event emissions...
                </div>
              )}
            </div>
          </div>
        </section>

        {/* Fixture JSON Inspector */}
        <section className="panel p-5 space-y-3 flex flex-col">
          <div className="flex items-center justify-between border-b border-border/40 pb-2.5">
            <div>
              <h4 className="text-sm font-semibold text-foreground">Current Fixture Payload</h4>
              <p className="text-[11px] text-muted-foreground">
                Passed directly as props to {widget.componentName}
              </p>
            </div>
            <button
              type="button"
              onClick={handleCopyFixture}
              className="flex items-center gap-1.5 rounded border border-border px-2 py-1 font-mono text-[11px] text-muted-foreground hover:text-foreground transition-colors"
            >
              {copied ? <Check className="h-3 w-3 text-signal" /> : <Copy className="h-3 w-3" />}
              <span>{copied ? "Copied" : "Copy JSON"}</span>
            </button>
          </div>

          <pre className="flex-1 max-h-96 overflow-auto rounded-lg border border-border/60 bg-background/80 p-3.5 font-mono text-[11px] text-foreground/90 leading-relaxed custom-scrollbar">
            {JSON.stringify(fixtureData, null, 2)}
          </pre>
        </section>
      </div>
    </div>
  );
}
