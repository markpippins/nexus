import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useMemo, useState } from "react";
import { toast } from "sonner";
import { SiteHeader } from "@/components/SiteHeader";
import { WidgetSandbox } from "@/components/WidgetSandbox";
import { analyze } from "@/lib/analyze";
import { inferSchema, generateMock, schemaFields } from "@/lib/schema";
import { useCatalog } from "@/lib/storage";
import { defaultProps } from "@/lib/widget-props";
import { useHydrated } from "@/hooks/use-hydrated";
import type { Widget } from "@/lib/widget-types";

export const Route = createFileRoute("/new")({
  head: () => ({
    meta: [
      { title: "Add a Widget — Widget Bench" },
      {
        name: "description",
        content:
          "Paste a React component, detect its REST endpoints, and convert a sample response into animated mock data.",
      },
      { property: "og:title", content: "Add a Widget — Widget Bench" },
      {
        property: "og:description",
        content: "Turn a REST-backed React component into a live catalog entry with mocked data.",
      },
    ],
  }),
  component: NewWidgetPage,
});

const STARTER = `function ActiveUsers({ team }) {
  const [data, setData] = React.useState(null);

  React.useEffect(() => {
    const load = () =>
      fetch("https://api.acme.dev/v1/teams/" + team + "/active")
        .then((r) => r.json())
        .then(setData);
    load();
    const id = setInterval(load, 1000);
    return () => clearInterval(id);
  }, [team]);

  if (!data) return <div style={{ opacity: 0.5 }}>loading…</div>;
  return (
    <div>
      <div style={{ fontSize: 40, fontWeight: 700 }}>{data.active}</div>
      <div style={{ fontSize: 12, opacity: 0.6 }}>active in {data.team}</div>
    </div>
  );
}`;

const STARTER_JSON = `{ "team": "platform", "active": 128, "peak": 210 }`;

function NewWidgetPage() {
  const navigate = useNavigate();
  const hydrated = useHydrated();
  const { save } = useCatalog();

  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [tags, setTags] = useState("");
  const [code, setCode] = useState(STARTER);
  const [sampleText, setSampleText] = useState(STARTER_JSON);

  const detected = useMemo(() => analyze(code), [code]);

  const parsedSample = useMemo(() => {
    if (!sampleText.trim()) return { value: null as unknown, error: null as string | null };
    try {
      return { value: JSON.parse(sampleText) as unknown, error: null };
    } catch (e) {
      return { value: null as unknown, error: e instanceof Error ? e.message : "Invalid JSON" };
    }
  }, [sampleText]);

  const schema = useMemo(
    () => (parsedSample.value === null ? null : inferSchema(parsedSample.value)),
    [parsedSample.value],
  );

  const draft: Widget = useMemo(() => {
    const mocks: Widget["mocks"] = {};
    for (const e of detected.endpoints) {
      mocks[e.signature] = { schema, sample: parsedSample.value };
    }
    return {
      id: "draft",
      name: name || detected.componentName,
      description,
      tags: tags
        .split(",")
        .map((t) => t.trim())
        .filter(Boolean),
      code,
      componentName: detected.componentName,
      inputs: detected.inputs,
      endpoints: detected.endpoints,
      mocks,
      createdAt: Date.now(),
    };
  }, [code, description, detected, name, parsedSample.value, schema, tags]);

  const onSave = () => {
    if (!detected.endpoints.length) {
      toast.error("No REST call detected — the widget needs a fetch/axios endpoint.");
      return;
    }
    if (!schema) {
      toast.error("Add a sample JSON response so mock data can be generated.");
      return;
    }
    const id = `${detected.componentName.toLowerCase()}-${Math.random().toString(36).slice(2, 8)}`;
    save({ ...draft, id });
    toast.success("Widget added to the catalog");
    navigate({ to: "/widget/$id", params: { id } });
  };

  return (
    <div className="mx-auto grid max-w-7xl gap-6 px-4 py-8 sm:px-6 lg:grid-cols-2">
      <div className="space-y-6">
        <section className="panel p-5">
          <p className="label-mono">step 1 · metadata</p>
          <div className="mt-4 grid gap-3">
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Widget name"
              className="rounded-md border border-input bg-background px-3 py-2 text-sm outline-none focus:border-primary"
            />
            <input
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Short description"
              className="rounded-md border border-input bg-background px-3 py-2 text-sm outline-none focus:border-primary"
            />
            <input
              value={tags}
              onChange={(e) => setTags(e.target.value)}
              placeholder="tags, comma, separated"
              className="rounded-md border border-input bg-background px-3 py-2 font-mono text-xs outline-none focus:border-primary"
            />
          </div>
        </section>

        <section className="panel p-5">
          <p className="label-mono">step 2 · paste the react component</p>
          <textarea
            value={code}
            onChange={(e) => setCode(e.target.value)}
            spellCheck={false}
            rows={18}
            className="mt-4 w-full resize-y rounded-md border border-input bg-background p-3 font-mono text-xs leading-relaxed outline-none focus:border-primary"
          />
          <p className="mt-2 text-xs text-muted-foreground">
            JSX or TSX. <code className="font-mono">React</code>, hooks,{" "}
            <code className="font-mono">fetch</code> and <code className="font-mono">axios</code>{" "}
            are provided by the sandbox; imports are stripped.
          </p>
        </section>

        <section className="panel p-5">
          <p className="label-mono">step 3 · sample json response (optional refinement)</p>
          <textarea
            value={sampleText}
            onChange={(e) => setSampleText(e.target.value)}
            spellCheck={false}
            rows={8}
            className="mt-4 w-full resize-y rounded-md border border-input bg-background p-3 font-mono text-xs outline-none focus:border-primary"
          />
          {parsedSample.error && (
            <p className="mt-2 font-mono text-xs text-destructive">{parsedSample.error}</p>
          )}
        </section>

        <button
          onClick={onSave}
          className="w-full rounded-md bg-primary px-4 py-3 font-mono text-xs font-semibold tracking-widest uppercase text-primary-foreground transition-opacity hover:opacity-90"
        >
          Add to catalog
        </button>
      </div>

      <div className="space-y-6 lg:sticky lg:top-24 lg:self-start">
        <section className="panel overflow-hidden">
          <div className="flex items-center justify-between border-b border-border px-4 py-2">
            <span className="label-mono">live sandbox preview</span>
            <span className="live-dot" aria-hidden />
          </div>
          <div className="flex min-h-56 items-center justify-center bg-background/40 p-6">
            {hydrated ? (
              <WidgetSandbox widget={draft} props={defaultProps(draft)} className="w-full" />
            ) : (
              <span className="label-mono">booting sandbox…</span>
            )}
          </div>
        </section>

        <section className="panel p-5">
          <p className="label-mono">detected</p>
          <dl className="mt-4 space-y-4 text-sm">
            <div>
              <dt className="label-mono">component</dt>
              <dd className="font-mono text-primary">{detected.componentName}</dd>
            </div>
            <div>
              <dt className="label-mono">input scheme</dt>
              <dd className="mt-1 flex flex-wrap gap-1.5">
                {detected.inputs.length ? (
                  detected.inputs.map((i) => (
                    <span
                      key={i.name}
                      className="rounded border border-border bg-muted px-1.5 py-0.5 font-mono text-[11px]"
                    >
                      {i.name}: {i.type}
                    </span>
                  ))
                ) : (
                  <span className="text-muted-foreground">none</span>
                )}
              </dd>
            </div>
            <div>
              <dt className="label-mono">rest endpoints</dt>
              <dd className="mt-1 space-y-1 font-mono text-xs">
                {detected.endpoints.length ? (
                  detected.endpoints.map((e) => (
                    <div key={e.signature} className="text-accent">
                      <span className="text-primary">{e.method}</span> {e.signature}
                    </div>
                  ))
                ) : (
                  <span className="text-muted-foreground">none detected</span>
                )}
              </dd>
            </div>
            <div>
              <dt className="label-mono">mock schema</dt>
              <dd className="mt-1 space-y-0.5 font-mono text-xs text-muted-foreground">
                {schemaFields(schema).map((f) => (
                  <div key={f}>{f}</div>
                ))}
              </dd>
            </div>
            <div>
              <dt className="label-mono">mock frame</dt>
              <dd>
                <pre className="mt-1 max-h-56 overflow-auto rounded-md border border-border bg-background/60 p-3 font-mono text-[11px] text-muted-foreground">
                  {JSON.stringify(generateMock(schema, 3), null, 2)}
                </pre>
              </dd>
            </div>
          </dl>
        </section>
      </div>
    </div>
  );
}
