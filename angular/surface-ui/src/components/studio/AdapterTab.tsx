import * as React from "react";
import { CapabilityId } from "@/core/types/designIR";
import { Adapter, AdapterOp, TransformStep } from "@/core/adapter/types";
import { AdapterRuntime } from "@/core/adapter/runtime";
import { ENDPOINT_PRESETS, AVAILABLE_OPS, EndpointPreset } from "@/lib/adapter-presets";
import { CAPABILITY_REGISTRY } from "@/lib/capabilities-registry";
import type { Widget } from "@/lib/widget-types";
import {
  ArrowRight,
  Database,
  Plus,
  Trash2,
  Play,
  Sparkles,
  CheckCircle2,
  AlertCircle,
  FileCode2,
  Save,
} from "lucide-react";
import { toast } from "sonner";

interface AdapterTabProps {
  widget: Widget;
  selectedCapability: CapabilityId;
}

export function AdapterTab({ widget, selectedCapability }: AdapterTabProps) {
  // Select initial endpoint preset based on capability or first preset
  const [selectedEndpointIndex, setSelectedEndpointIndex] = React.useState(0);
  const endpoint = ENDPOINT_PRESETS[selectedEndpointIndex] || ENDPOINT_PRESETS[0];

  // Pipeline steps
  const initialSteps: TransformStep[] = React.useMemo(() => {
    const suggested = endpoint?.suggestedProjections.find(
      (p) => p.targetContract === selectedCapability,
    );
    if (suggested) return suggested.steps;
    return [
      { op: "select", args: { path: "data" } },
      { op: "default", args: { value: [] } },
    ];
  }, [endpoint, selectedCapability]);

  const [steps, setSteps] = React.useState<TransformStep[]>(initialSteps);
  const [customPayloadText, setCustomPayloadText] = React.useState("");
  const [useCustomPayload, setUseCustomPayload] = React.useState(false);
  const [projectionResult, setProjectionResult] = React.useState<unknown>(null);
  const [projectionError, setProjectionError] = React.useState<string | null>(null);

  // Update steps when endpoint changes
  React.useEffect(() => {
    const suggested = endpoint?.suggestedProjections.find(
      (p) => p.targetContract === selectedCapability,
    );
    if (suggested) {
      setSteps(suggested.steps);
    }
  }, [selectedEndpointIndex, selectedCapability]); // eslint-disable-line react-hooks/exhaustive-deps

  // Execute projection through AdapterRuntime
  React.useEffect(() => {
    const runtime = new AdapterRuntime();
    let sourceData = endpoint?.samplePayload;

    if (useCustomPayload && customPayloadText.trim()) {
      try {
        sourceData = JSON.parse(customPayloadText);
      } catch (err) {
        setProjectionError("Invalid custom JSON syntax: " + (err as Error).message);
        return;
      }
    }

    const adapter: Adapter = {
      id: "preview-adapter",
      source: { type: "mock", mock: sourceData },
      steps,
      outputContract: selectedCapability,
    };

    runtime.register(adapter);

    runtime
      .execute("preview-adapter", sourceData)
      .then((res) => {
        setProjectionResult(res);
        setProjectionError(null);
      })
      .catch((err) => {
        setProjectionError((err as Error).message);
        setProjectionResult(null);
      });
  }, [steps, endpoint, useCustomPayload, customPayloadText, selectedCapability]);

  const handleAddStep = (op: AdapterOp) => {
    const meta = AVAILABLE_OPS.find((o) => o.op === op);
    setSteps((prev) => [
      ...prev,
      { op, args: meta ? JSON.parse(JSON.stringify(meta.defaultArgs)) : {} },
    ]);
  };

  const handleRemoveStep = (index: number) => {
    setSteps((prev) => prev.filter((_, i) => i !== index));
  };

  const handleUpdateStepArgs = (index: number, newArgsJson: string) => {
    try {
      const parsed = JSON.parse(newArgsJson);
      setSteps((prev) => prev.map((step, i) => (i === index ? { ...step, args: parsed } : step)));
      setProjectionError(null);
    } catch {
      // Invalid json while typing - keep state, will validate on complete
    }
  };

  const handleSaveAdapter = () => {
    toast.success(`Saved declarative projection adapter for ${widget.name}`);
  };

  return (
    <div className="space-y-6">
      {/* Header Info & Source Selector */}
      <div className="panel p-5 space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border/40 pb-3">
          <div>
            <h3 className="text-base font-semibold text-foreground flex items-center gap-2">
              <Database className="h-4 w-4 text-accent" />
              <span>Declarative Data Adapter / Projection Pipeline</span>
            </h3>
            <p className="text-xs text-muted-foreground mt-0.5">
              Transforms raw REST payloads into the <strong>{selectedCapability}</strong> contract
              schema using the bounded operator vocabulary.
            </p>
          </div>
          <button
            type="button"
            onClick={handleSaveAdapter}
            className="flex items-center gap-1.5 rounded-md bg-primary px-3 py-1.5 text-xs font-mono text-primary-foreground hover:bg-primary/90 transition-colors shadow-xs"
          >
            <Save className="h-3.5 w-3.5" />
            <span>Save Adapter</span>
          </button>
        </div>

        {/* Source Preset Selector */}
        <div className="space-y-2">
          <label className="label-mono text-muted-foreground text-[11px]">
            Select Source REST Payload:
          </label>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-2">
            {ENDPOINT_PRESETS.map((ep, idx) => {
              const isSelected = selectedEndpointIndex === idx && !useCustomPayload;
              return (
                <button
                  key={ep.path}
                  type="button"
                  onClick={() => {
                    setSelectedEndpointIndex(idx);
                    setUseCustomPayload(false);
                  }}
                  className={`p-3 rounded-lg border text-left font-mono text-xs transition-all ${
                    isSelected
                      ? "border-accent bg-accent/15 text-accent ring-1 ring-accent/40"
                      : "border-border/60 bg-surface/60 text-muted-foreground hover:bg-surface hover:text-foreground"
                  }`}
                >
                  <div className="flex items-center justify-between">
                    <span className="font-bold text-foreground text-[11px] truncate">
                      {ep.name}
                    </span>
                    <span className="text-[10px] text-accent">{ep.method}</span>
                  </div>
                  <span className="text-[10px] opacity-70 block truncate mt-1">{ep.path}</span>
                </button>
              );
            })}
          </div>
        </div>
      </div>

      {/* 3-Column Visual Flow: Source ➔ Transformation Pipeline ➔ Projected Contract */}
      <div className="grid grid-cols-1 xl:grid-cols-3 gap-6 items-start">
        {/* Column 1: Source Payload */}
        <div className="panel p-4 space-y-3 flex flex-col">
          <div className="flex items-center justify-between border-b border-border/40 pb-2">
            <span className="label-mono text-xs text-foreground">1. Source REST Payload</span>
            <span className="font-mono text-[10px] text-muted-foreground">JSON Input</span>
          </div>

          <pre className="max-h-[500px] overflow-auto rounded-lg border border-border/60 bg-background/80 p-3 font-mono text-[11px] text-muted-foreground leading-relaxed custom-scrollbar">
            {JSON.stringify(endpoint?.samplePayload, null, 2)}
          </pre>
        </div>

        {/* Column 2: Transformation Pipeline Builder */}
        <div className="panel p-4 space-y-4">
          <div className="flex items-center justify-between border-b border-border/40 pb-2">
            <div className="flex items-center gap-1.5">
              <Sparkles className="h-3.5 w-3.5 text-accent" />
              <span className="label-mono text-xs text-foreground">2. Bounded Transform Steps</span>
            </div>
            <span className="font-mono text-[10px] text-accent">{steps.length} ops</span>
          </div>

          {/* Steps List */}
          <div className="space-y-3">
            {steps.map((step, idx) => {
              const opMeta = AVAILABLE_OPS.find((o) => o.op === step.op);
              return (
                <div
                  key={idx}
                  className="rounded-lg border border-border bg-surface/80 p-3 space-y-2 font-mono text-xs relative group"
                >
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <span className="flex h-5 w-5 items-center justify-center rounded-full bg-accent/20 text-accent text-[10px] font-bold">
                        {idx + 1}
                      </span>
                      <span className="font-bold text-foreground">{step.op}</span>
                      <span className="text-[9px] uppercase px-1.5 py-0.2 rounded bg-muted text-muted-foreground">
                        {opMeta?.category || "Transform"}
                      </span>
                    </div>
                    <button
                      type="button"
                      onClick={() => handleRemoveStep(idx)}
                      className="text-muted-foreground hover:text-destructive p-1 transition-colors"
                      title="Remove step"
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  </div>

                  <p className="text-[11px] font-sans text-muted-foreground">
                    {opMeta?.description}
                  </p>

                  <div className="space-y-1">
                    <label className="text-[10px] text-muted-foreground">
                      Step Arguments (JSON):
                    </label>
                    <textarea
                      rows={step.op === "semanticMap" || step.op === "map" ? 4 : 2}
                      defaultValue={JSON.stringify(step.args, null, 2)}
                      onBlur={(e) => handleUpdateStepArgs(idx, e.target.value)}
                      className="w-full rounded border border-input bg-background px-2.5 py-1.5 font-mono text-[11px] text-foreground focus:border-accent outline-none"
                    />
                  </div>
                </div>
              );
            })}
          </div>

          {/* Add Operation Dropdown / Buttons */}
          <div className="pt-2 border-t border-border/40 space-y-2">
            <span className="label-mono text-[10px] text-muted-foreground">
              Add Bounded Operator:
            </span>
            <div className="flex flex-wrap gap-1.5">
              {AVAILABLE_OPS.map((op) => (
                <button
                  key={op.op}
                  type="button"
                  onClick={() => handleAddStep(op.op)}
                  className="inline-flex items-center gap-1 rounded border border-border/70 bg-background/60 px-2 py-1 font-mono text-[10px] text-muted-foreground hover:border-accent hover:text-accent transition-colors"
                >
                  <Plus className="h-2.5 w-2.5" />
                  <span>{op.op}</span>
                </button>
              ))}
            </div>
          </div>
        </div>

        {/* Column 3: Projected Contract Output */}
        <div className="panel p-4 space-y-3 flex flex-col">
          <div className="flex items-center justify-between border-b border-border/40 pb-2">
            <span className="label-mono text-xs text-foreground">3. Projected Contract Output</span>
            <div className="flex items-center gap-1.5">
              {projectionError ? (
                <span className="rounded bg-destructive/15 px-1.5 py-0.2 text-[9px] font-bold text-destructive flex items-center gap-1">
                  <AlertCircle className="h-3 w-3" /> ERROR
                </span>
              ) : (
                <span className="rounded bg-signal/15 px-1.5 py-0.2 text-[9px] font-bold text-signal flex items-center gap-1">
                  <CheckCircle2 className="h-3 w-3" /> VALID
                </span>
              )}
            </div>
          </div>

          {projectionError ? (
            <div className="rounded-lg border border-destructive/40 bg-destructive/10 p-3 text-xs font-mono text-destructive">
              {projectionError}
            </div>
          ) : (
            <pre className="max-h-[500px] overflow-auto rounded-lg border border-border/60 bg-background/80 p-3 font-mono text-[11px] text-foreground/90 leading-relaxed custom-scrollbar">
              {JSON.stringify(projectionResult, null, 2)}
            </pre>
          )}
        </div>
      </div>
    </div>
  );
}
