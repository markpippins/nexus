import React, { useState, useEffect, useRef, useCallback } from 'react';
import {
  CheckCircle2,
  AlertTriangle,
  RefreshCw,
  Zap,
  Shield,
  Layers,
  Cpu,
  Server,
  Clock,
  Sparkles,
  Plus,
  Edit2,
  Trash2,
  ArrowUp,
  ArrowDown,
  Terminal,
  Send,
  Calendar,
  X,
  Package
} from 'lucide-react';
import { showConfirm } from '../components/ConfirmDialog';
import { SystemRole, ValidationReport, AIModel, Provider, Harness, ConfigBundle } from '../types';
import { BundleModal } from './BundleModal';

// Badge colors per invocation channel (matches the tackle.config_bundle
// vocabulary: CLI | HTTP | SDK | MCP | INTERACTIVE).
const INVOCATION_MODE_COLORS: Record<ConfigBundle['invocation_mode'], string> = {
  CLI: 'bg-cyan-950/50 text-cyan-300 border-cyan-800/40',
  HTTP: 'bg-indigo-950/50 text-indigo-300 border-indigo-800/40',
  SDK: 'bg-emerald-950/50 text-emerald-300 border-emerald-800/40',
  MCP: 'bg-purple-950/50 text-purple-300 border-purple-800/40',
  INTERACTIVE: 'bg-amber-950/50 text-amber-300 border-amber-800/40',
};

interface OverviewTabProps {
  roles: SystemRole[];
  models: AIModel[];
  providers: Provider[];
  harnesses: Harness[];
  bundles: ConfigBundle[];
  validationReport: ValidationReport | null;
  onValidate: () => void;
  onRunTest: (role: string, modelId: string, prompt: string) => void | Promise<unknown>;
  onPurgeUnverified: () => void | Promise<unknown>;
  onVerifyBundle: (bundleId: string, modelId: string) => void | Promise<unknown>;
  onSeedDefaults: () => void;
  onNavigateToTab: (tab: string) => void;
  onSaveBundle: (bundle: Partial<ConfigBundle>) => Promise<void>;
  onDeleteBundle: (id: string) => Promise<void>;
  onReorderPriority: (role: string, bundleId: string, direction: 'up' | 'down') => Promise<void>;
}

export const OverviewTab: React.FC<OverviewTabProps> = ({
  roles,
  models,
  providers,
  harnesses,
  bundles,
  validationReport,
  onValidate,
  onRunTest,
  onPurgeUnverified,
  onVerifyBundle,
  onSeedDefaults,
  onNavigateToTab,
  onSaveBundle,
  onDeleteBundle,
  onReorderPriority
}) => {
  const [selectedRole, setSelectedRole] = useState<string>(roles[0]?.name || 'operator');

  // --- Sandbox test state ---
  const [testBundleId, setTestBundleId] = useState<string>('');
  const [testPrompt, setTestPrompt] = useState<string>('Verify system status and inspect active inference config.');
  const [isRunningTest, setIsRunningTest] = useState<boolean>(false);
  const [testResult, setTestResult] = useState<any>(null);
  const [testError, setTestError] = useState<string | null>(null);
  const [testLogLines, setTestLogLines] = useState<string[]>([]);
  const [bundleError, setBundleError] = useState<string | null>(null);

  // --- Bundle verify state (Live Model Execution Sandbox) ---
  const [verifyingBundleId, setVerifyingBundleId] = useState<string | null>(null);
  const [bundleVerifyMsg, setBundleVerifyMsg] = useState<{ ok: boolean; message: string } | null>(null);

  // --- Bundle management state ---
  const [bundleModalOpen, setBundleModalOpen] = useState<boolean>(false);
  const [editingBundle, setEditingBundle] = useState<Partial<ConfigBundle> | null>(null);
  const [modalKey, setModalKey] = useState<number>(0);
  const [createDefaultRole, setCreateDefaultRole] = useState<string | undefined>(undefined);

  // When role changes, default test bundle to the first active bundle for that role
  useEffect(() => {
    const roleBundles = bundles
      .filter(b => b.role === selectedRole && b.is_active)
      .sort((a, b) => a.priority - b.priority);
    if (roleBundles.length > 0 && !roleBundles.find(b => b.id === testBundleId)) {
      setTestBundleId(roleBundles[0].id);
    }
    setTestResult(null);
    setTestError(null);
    setTestLogLines([]);
    setBundleError(null);
    stopLogPolling();
  }, [selectedRole, bundles]);

  // --- Bundle management handlers ---
  const openCreateModal = (roleDefault?: string) => {
    setEditingBundle(null);
    setCreateDefaultRole(roleDefault);
    setModalKey(k => k + 1);
    setBundleModalOpen(true);
  };

  const openEditModal = (bundle: ConfigBundle) => {
    setEditingBundle(bundle);
    setCreateDefaultRole(undefined);
    setModalKey(k => k + 1);
    setBundleModalOpen(true);
  };

  const handleToggleActive = async (bundle: ConfigBundle) => {
    try {
      await onSaveBundle({
        ...bundle,
        is_active: !bundle.is_active
      });
    } catch (err) {
      setBundleError(`Error saving bundle: ${err instanceof Error ? err.message : String(err)}`);
    }
  };

  const handleReorder = async (role: string, bundleId: string, direction: 'up' | 'down') => {
    try {
      await onReorderPriority(role, bundleId, direction);
    } catch (err) {
      setBundleError(`Error reordering bundles: ${err instanceof Error ? err.message : String(err)}`);
    }
  };

  // --- Log polling ref ---
  const logPollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const stopLogPolling = useCallback(() => {
    if (logPollRef.current) {
      clearInterval(logPollRef.current);
      logPollRef.current = null;
    }
  }, []);

  const pollLogContent = useCallback((sessionId: string) => {
    let lastSize = 0;
    const poll = async () => {
      try {
        const res = await fetch(`/log/${sessionId}`);
        if (!res.ok || !res.body) return;
        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        // Read what's available now (non-blocking poll)
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
        }
        buffer += decoder.decode();
        // Parse SSE lines
        const lines: string[] = [];
        for (const chunk of buffer.split('\n')) {
          if (chunk.startsWith('data: ')) {
            try {
              const evt = JSON.parse(chunk.slice(6));
              if (evt.type === 'session_log' && evt.data?.line) {
                lines.push(evt.data.line);
              }
            } catch { /* skip malformed */ }
          }
        }
        if (lines.length > lastSize) {
          setTestLogLines(prev => [...prev, ...lines.slice(lastSize)]);
          lastSize = lines.length;
        }
      } catch { /* poll failed — silently retry */ }
    };
    poll(); // initial poll
    logPollRef.current = setInterval(poll, 2000);
  }, []);

  // Cleanup polling on unmount
  useEffect(() => () => stopLogPolling(), [stopLogPolling]);

  // --- Sandbox test handler ---
  const handleExecuteTest = async () => {
    if (!testPrompt.trim()) return;

    setIsRunningTest(true);
    setTestResult(null);
    setTestError(null);
    setTestLogLines([]);
    stopLogPolling();

    const selectedBundle = bundles.find(b => b.id === testBundleId);
    const testModelId = selectedBundle?.model_id || models[0]?.id || 'mod-gemini-3.6-flash';

    try {
      const resData = await onRunTest(selectedRole, testModelId, testPrompt);
      setTestResult(resData);
      // Start polling log content if the backend returned a session
      if (resData?.sessionId) {
        pollLogContent(resData.sessionId);
      }
    } catch (err: any) {
      setTestError(err instanceof Error ? err.message : String(err));
    } finally {
      setIsRunningTest(false);
    }
  };

  // --- Bundle verify handler (Live Model Execution Sandbox) ---
  // Verifies the selected bundle's model through a fresh inference run. The
  // App-level onVerifyBundle runs the verify + status polling + refresh; on a
  // failed reverify the backend marks the model unverified and its bundles are
  // forced inactive, so the operator sees which bundles need updating.
  const handleVerifyBundle = async (bundle: ConfigBundle) => {
    if (!bundle.model_id) {
      setBundleVerifyMsg({ ok: false, message: 'This bundle has no model assigned.' });
      return;
    }
    setVerifyingBundleId(bundle.id);
    setBundleVerifyMsg(null);
    try {
      const res = await onVerifyBundle(bundle.id, bundle.model_id);
      setBundleVerifyMsg({
        ok: !!(res && (res.verified || res.alreadyVerified)),
        message: res?.message || (res?.verified ? 'Bundle verified' : 'Verify finished — see model status'),
      });
    } catch (err: any) {
      setBundleVerifyMsg({ ok: false, message: `Verify failed: ${err instanceof Error ? err.message : String(err)}` });
    } finally {
      setVerifyingBundleId(null);
    }
  };

  // --- Computed values ---
  const roleBundles = bundles
    .filter(b => b.role === selectedRole)
    .sort((a, b) => a.priority - b.priority);

  const selectedTestBundle = bundles.find(b => b.id === testBundleId);
  const testModelObj = models.find(m => m.id === selectedTestBundle?.model_id);
  // Verified-model gate: the backend refuses test invocations for unverified
  // models (400) — surface that up front and stop the user from firing a test
  // that would just fail.
  const testModelUnverified = !!testModelObj && !testModelObj.verified;

  return (
    <div className="space-y-4">
      {/* Top Banner & Quick Actions */}
      <div className="bg-[var(--bg-secondary)] border border-[var(--border-color)] rounded-xl p-4 shadow-sm">
        <div className="flex flex-col md:flex-row items-start md:items-center justify-between gap-3">
          <div>
            <div className="flex items-center gap-2">
              <Sparkles className="w-5 h-5 text-[var(--accent-color)]" />
              <h2 className="text-base font-bold text-[var(--text-primary)]">
                AI Configuration Subsystem Overview
              </h2>
            </div>
            <p className="text-sm text-[var(--text-secondary)] mt-1">
              Live resolution of role config bundles, model bindings, providers & failure recovery parameters.
            </p>
          </div>

          <div className="flex items-center gap-2 flex-wrap">
            <button
              onClick={onValidate}
              className="px-3 py-1.5 rounded-lg text-sm font-medium bg-[var(--bg-tertiary)] hover:bg-[var(--bg-hover)] border border-[var(--border-color)] text-[var(--text-primary)] transition flex items-center gap-1.5 cursor-pointer"
            >
              <RefreshCw className="w-3.5 h-3.5 text-[var(--accent-color)]" />
              <span>Validate Integrity</span>
            </button>
            <button
              onClick={async () => {
                if (await showConfirm('Remove all unverified models from the inference chain? This force-deactivates every config bundle (across all roles) whose model is not verified.')) {
                  await onPurgeUnverified();
                }
              }}
              className="px-3 py-1.5 rounded-lg text-sm font-medium bg-[var(--bg-tertiary)] hover:bg-[var(--bg-hover)] border border-[var(--border-color)] text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition flex items-center gap-1.5 cursor-pointer"
              title="Force-deactivate every config bundle whose model is unverified, so no role's resolver queue selects them"
            >
              <Shield className="w-3.5 h-3.5 text-rose-400" />
              <span>Purge Unverified</span>
            </button>
            <button
              onClick={onSeedDefaults}
              className="px-3 py-1.5 rounded-lg text-sm font-medium bg-[var(--bg-tertiary)] hover:bg-[var(--bg-hover)] border border-[var(--border-color)] text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition flex items-center gap-1.5 cursor-pointer"
            >
              <span>Seed Defaults</span>
            </button>
          </div>
        </div>

        {/* Validation Status Box */}
        {validationReport && (
          <div
            className={`mt-4 p-3 rounded-lg border text-sm flex items-start gap-3 ${
              validationReport.valid
                ? 'bg-emerald-950/20 border-emerald-800/40 text-emerald-200'
                : 'bg-amber-950/20 border-amber-800/40 text-amber-200'
            }`}
          >
            {validationReport.valid ? (
              <CheckCircle2 className="w-4 h-4 text-emerald-400 shrink-0 mt-0.5" />
            ) : (
              <AlertTriangle className="w-4 h-4 text-amber-400 shrink-0 mt-0.5" />
            )}
            <div className="space-y-1 flex-1">
              <div className="font-semibold flex items-center justify-between">
                <span>
                  Configuration Integrity Status:{' '}
                  {validationReport.valid ? 'PASSED & HEALTHY' : 'WARNINGS DETECTED'}
                </span>
                <span className="font-mono text-[10px] opacity-70">
                  {validationReport.check_timestamp
                    ? `Checked: ${new Date(validationReport.check_timestamp).toLocaleTimeString()}`
                    : ''}
                </span>
              </div>
              {validationReport.warnings.length > 0 && (
                <ul className="list-disc list-inside space-y-0.5 opacity-90 text-[11px]">
                  {validationReport.warnings.map((w, idx) => (
                    <li key={idx}>
                      <span className="font-mono text-[10px] text-[var(--text-muted)]">[{w.role}]</span>{' '}
                      {w.message}
                      {w.severity === 'error' && (
                        <span className="ml-1 text-[10px] font-bold text-rose-400 uppercase">ERROR</span>
                      )}
                    </li>
                  ))}
                </ul>
              )}
              {validationReport.errors && validationReport.errors.length > 0 && (
                <ul className="list-disc list-inside space-y-0.5 font-mono text-[11px] text-rose-300">
                  {validationReport.errors.map((e, idx) => (
                    <li key={idx}>{e}</li>
                  ))}
                </ul>
              )}
            </div>
          </div>
        )}
      </div>

      {/* ── Role Selector + Three Right Panels ── */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {/* Left Column: Role Selection — sticky, height-capped so the role
            list scrolls independently of the rest of the screen while the
            right-hand content scrolls normally. */}
        <div className="bg-[var(--bg-secondary)] border border-[var(--border-color)] rounded-xl p-4 flex flex-col lg:sticky lg:top-0 lg:self-start lg:max-h-[calc(100vh-6rem)]">
          <div className="shrink-0">
            <h3 className="text-sm font-bold text-[var(--text-primary)] mb-1 flex items-center gap-2">
              <Shield className="w-4 h-4 text-[var(--accent-color)]" />
              <span>Select System Role</span>
            </h3>
            <p className="text-sm text-[var(--text-secondary)] mb-3">
              Select a role to manage its config bundles.
            </p>
          </div>

          <div className="flex-1 min-h-0 overflow-y-auto space-y-2 pr-1 -mr-1">
              {roles.map(r => {
                const isSelected = selectedRole === r.name;
                const roleActive = bundles.filter(b => b.role === r.name && b.is_active);
                return (
                  <button
                    key={r.id}
                    onClick={() => setSelectedRole(r.name)}
                    className={`w-full text-left p-3 rounded-lg border transition cursor-pointer flex items-center justify-between ${
                      isSelected
                        ? 'bg-[var(--bg-tertiary)] border-[var(--accent-color)] text-[var(--text-primary)] shadow-sm'
                        : 'bg-[var(--bg-card)] border-[var(--border-subtle)] text-[var(--text-secondary)] hover:border-[var(--border-color)]'
                    }`}
                  >
                    <div>
                      <div className="font-mono font-bold text-sm flex items-center gap-2">
                        <span>{r.name}</span>
                        {isSelected && (
                          <span className="w-1.5 h-1.5 rounded-full bg-[var(--accent-color)] animate-ping" />
                        )}
                      </div>
                      <div className="text-[11px] text-[var(--text-muted)] line-clamp-1 mt-0.5">
                        {r.description || 'System agent role'}
                      </div>
                    </div>
                    <div className="text-right shrink-0">
                      <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-[var(--badge-bg)] text-[var(--accent-color)] border border-[var(--border-subtle)]">
                        {roleActive.length} active
                      </span>
                    </div>
                  </button>
                );
              })}
            </div>
        </div>

        {/* Right Column (2 cols): three stacked panels */}
        <div className="lg:col-span-2 space-y-4">
          {/* Panel 1: Config Bundles */}
          <div className="bg-[var(--bg-secondary)] border border-[var(--border-color)] rounded-xl overflow-hidden shadow-sm">
            <div className="bg-[var(--bg-tertiary)] px-4 py-2.5 border-b border-[var(--border-subtle)] flex items-center justify-between">
              <div className="flex items-center gap-2">
                <Package className="w-4 h-4 text-[var(--accent-color)]" />
                <span className="font-mono font-bold text-sm text-[var(--accent-color)]">
                  Config Bundles for {selectedRole}
                </span>
                <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-[var(--bg-card)] text-[var(--text-secondary)] border border-[var(--border-subtle)]">
                  {roleBundles.length} registered
                </span>
              </div>

              <button
                onClick={() => openCreateModal(selectedRole)}
                className="text-sm font-medium text-[var(--text-secondary)] hover:text-[var(--text-primary)] flex items-center gap-1 cursor-pointer"
              >
                <Plus className="w-3.5 h-3.5 text-[var(--accent-color)]" />
                <span>Add bundle for {selectedRole}</span>
              </button>
            </div>

            {/* Bundle error banner */}
            {bundleError && (
              <div className="mx-4 mt-3 p-3 rounded-lg bg-rose-950/30 border border-rose-800/40 text-sm flex items-start justify-between gap-3">
                <div className="flex items-start gap-2">
                  <AlertTriangle className="w-4 h-4 text-rose-400 shrink-0 mt-0.5" />
                  <span className="text-rose-200">{bundleError}</span>
                </div>
                <button
                  onClick={() => setBundleError(null)}
                  className="text-rose-400 hover:text-rose-300 shrink-0 cursor-pointer"
                >
                  <X className="w-4 h-4" />
                </button>
              </div>
            )}

            {roleBundles.length === 0 ? (
              <div className="p-6 text-center text-sm text-[var(--text-muted)]">
                No bundles configured for role '{selectedRole}'. Click "Add bundle" to create one.
              </div>
            ) : (
              <div className="divide-y divide-[var(--border-subtle)]">
                {roleBundles.map((bundle, idx) => {
                  const modelObj = models.find(m => m.id === bundle.model_id);
                  const providerObj = providers.find(p => p.id === (bundle.provider_id || modelObj?.provider_id));
                  const harnessObj = harnesses.find(h => h.id === (bundle.harness_id || modelObj?.harness_id));

                  return (
                    <div
                      key={bundle.id}
                      className={`p-3 transition flex flex-col md:flex-row items-start md:items-center justify-between gap-3 ${
                        bundle.is_active ? 'bg-[var(--bg-card)]' : 'bg-[var(--bg-secondary)] opacity-60'
                      }`}
                    >
                      <div className="flex items-start gap-3 flex-1 min-w-0">
                        <div className="flex flex-col items-center justify-center shrink-0">
                          <span
                            className={`w-8 h-8 rounded-lg flex items-center justify-center text-sm font-mono font-bold border ${
                              idx === 0 && bundle.is_active
                                ? 'bg-emerald-950/60 text-emerald-300 border-emerald-800/60'
                                : 'bg-[var(--bg-tertiary)] text-[var(--text-secondary)] border-[var(--border-color)]'
                            }`}
                          >
                            #{bundle.priority}
                          </span>
                          <div className="flex items-center gap-0.5 mt-1">
                            <button disabled={idx === 0} onClick={() => handleReorder(selectedRole, bundle.id, 'up')} className="p-0.5 text-[var(--text-muted)] hover:text-[var(--text-primary)] disabled:opacity-20 cursor-pointer" title="Increase priority"><ArrowUp className="w-3 h-3" /></button>
                            <button disabled={idx === roleBundles.length - 1} onClick={() => handleReorder(selectedRole, bundle.id, 'down')} className="p-0.5 text-[var(--text-muted)] hover:text-[var(--text-primary)] disabled:opacity-20 cursor-pointer" title="Decrease priority"><ArrowDown className="w-3 h-3" /></button>
                          </div>
                        </div>

                        <div className="space-y-1 min-w-0">
                          <div className="flex items-center gap-2 flex-wrap">
                            <span className="font-bold text-sm text-[var(--text-primary)]">{bundle.name}</span>
                            <span className={`text-[10px] font-mono px-2 py-0.5 rounded border uppercase font-semibold ${INVOCATION_MODE_COLORS[bundle.invocation_mode] ?? 'bg-slate-800 text-slate-300 border-slate-700'}`}>{bundle.invocation_mode}</span>
                            <button onClick={() => handleToggleActive(bundle)} className={`text-[10px] font-mono px-2 py-0.5 rounded border font-semibold cursor-pointer transition ${bundle.is_active ? 'bg-emerald-950/40 text-emerald-400 border-emerald-800/50' : 'bg-slate-800 text-slate-400 border-slate-700'}`}>{bundle.is_active ? 'ACTIVE' : 'INACTIVE'}</button>
                          </div>
                          <div className="text-sm text-[var(--text-secondary)] font-mono flex items-center gap-3 flex-wrap">
                            <span className="flex items-center gap-1 text-[var(--text-primary)] font-semibold"><Cpu className="w-3 h-3 text-[var(--accent-color)]" />{modelObj?.name || bundle.model_id}</span>
                            <span>•</span>
                            <span>Provider: {providerObj?.name || 'Default'}</span>
                            <span>•</span>
                            <span>Harness: {harnessObj?.name || 'Standard'}</span>
                          </div>
                          {(bundle.valid_from || bundle.valid_to) && (
                            <div className="text-[11px] text-[var(--text-muted)] font-mono flex items-center gap-1"><Calendar className="w-3 h-3" /><span>Valid: {bundle.valid_from ? new Date(bundle.valid_from).toLocaleDateString() : 'Now'} →{' '}{bundle.valid_to ? new Date(bundle.valid_to).toLocaleDateString() : 'Forever'}</span></div>
                          )}
                        </div>
                      </div>

                      <div className="flex items-center gap-3 shrink-0 self-end md:self-center">
                        <div className="text-right font-mono text-[11px] text-[var(--text-muted)] hidden sm:block">
                          <div className="flex items-center gap-1 justify-end"><Clock className="w-3 h-3" /><span>{bundle.timeout_ms || 30000}ms</span></div>
                          <div className="text-[10px] text-[var(--text-muted)]">{Object.keys(bundle.metadata || {}).length} meta keys</div>
                        </div>
                        <div className="flex items-center gap-1 bg-[var(--bg-tertiary)] p-1 rounded-lg border border-[var(--border-subtle)]">
                          <button onClick={() => openEditModal(bundle)} className="p-1.5 text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-hover)] rounded transition cursor-pointer" title="Edit Bundle"><Edit2 className="w-3.5 h-3.5" /></button>
                          <button onClick={async () => { if (await showConfirm(`Delete config bundle '${bundle.name}'?`)) { try { await onDeleteBundle(bundle.id); } catch (err) { setBundleError(`Error deleting bundle: ${err instanceof Error ? err.message : String(err)}`); } } }} className="p-1.5 text-rose-400 hover:text-rose-300 hover:bg-rose-950/30 rounded transition cursor-pointer" title="Delete Bundle"><Trash2 className="w-3.5 h-3.5" /></button>
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>

          {/* Panel 2: Sandbox Controls */}
          <div className="bg-[var(--bg-secondary)] border border-[var(--border-color)] rounded-xl p-4 space-y-4 shadow-sm">
            <div className="flex items-center gap-2 pb-2.5 border-b border-[var(--border-subtle)]">
              <Zap className="w-5 h-5 text-emerald-400" />
              <div>
                <h3 className="text-sm font-bold text-[var(--text-primary)]">Live Model Execution Sandbox</h3>
                <p className="text-sm text-[var(--text-secondary)]">Send live test payloads to `/config/ai/test` using the selected config bundle.</p>
              </div>
            </div>

            <div className="space-y-4 text-sm">
              <div>
                <label className="block text-[var(--text-secondary)] mb-1 font-semibold">Target Config Bundle</label>
                <div className="flex items-start gap-2">
                  <select
                    value={testBundleId}
                    onChange={e => { setTestBundleId(e.target.value); setTestResult(null); setTestError(null); setTestLogLines([]); stopLogPolling(); setBundleVerifyMsg(null); }}
                    className="flex-1 bg-[var(--bg-tertiary)] border border-[var(--border-color)] rounded-lg px-3 py-2 font-mono text-[var(--text-primary)] focus:outline-none focus:border-[var(--accent-color)]"
                  >
                    {roleBundles.length === 0 && (<option value="">No bundles for this role</option>)}
                    {roleBundles.map(b => { const m = models.find(mm => mm.id === b.model_id); return (<option key={b.id} value={b.id}>#{b.priority} {b.name} — {m?.name || b.model_id} ({b.invocation_mode})</option>); })}
                  </select>
                  <button
                    onClick={() => selectedTestBundle && handleVerifyBundle(selectedTestBundle)}
                    disabled={!selectedTestBundle || !!verifyingBundleId || !testModelObj}
                    title={!selectedTestBundle
                      ? 'Select a config bundle to verify its model'
                      : testModelObj
                        ? `Verify this bundle's model (${testModelObj.name}) through a fresh inference run`
                        : 'Bundle model not found'}
                    className="shrink-0 flex items-center justify-center gap-1.5 px-3 py-2 rounded-lg text-[11px] font-bold bg-[var(--bg-tertiary)] border border-[var(--border-color)] text-[var(--text-primary)] hover:border-[var(--accent-color)] hover:text-[var(--accent-color)] transition cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed"
                  >
                    {verifyingBundleId === selectedTestBundle?.id ? (
                      <><Shield className="w-3.5 h-3.5 animate-pulse text-[var(--accent-color)]" /><span>Verifying…</span></>
                    ) : (
                      <><Shield className="w-3.5 h-3.5" /><span>Verify Bundle</span></>
                    )}
                  </button>
                </div>
                {bundleVerifyMsg && (
                  <div className={`mt-1 text-[11px] font-mono px-2 py-1 rounded border flex items-start gap-1.5 ${
                    bundleVerifyMsg.ok
                      ? 'bg-emerald-950/30 border-emerald-800/40 text-emerald-300'
                      : 'bg-rose-950/30 border-rose-800/40 text-rose-300'
                  }`}>
                    {bundleVerifyMsg.ok
                      ? <CheckCircle2 className="w-3 h-3 shrink-0 mt-0.5" />
                      : <AlertTriangle className="w-3 h-3 shrink-0 mt-0.5" />}
                    <span>{bundleVerifyMsg.message}</span>
                  </div>
                )}
                {selectedTestBundle && (
                  <div className="text-[10px] font-mono text-[var(--text-muted)] mt-1 flex items-center gap-2">
                    <span>
                      Model: {testModelObj?.model_identifier || selectedTestBundle.model_id}
                      {selectedTestBundle.provider_id && ` • Provider override: ${selectedTestBundle.provider_id}`}
                      {selectedTestBundle.harness_id && ` • Harness override: ${selectedTestBundle.harness_id}`}
                      {!selectedTestBundle.is_active && (<span className="ml-1 text-amber-400 font-bold">(INACTIVE)</span>)}
                      {testModelUnverified && (<span className="ml-1 text-rose-400 font-bold">(model unverified — test refused)</span>)}
                    </span>
                    <button
                      onClick={() => openEditModal(selectedTestBundle)}
                      className="text-[var(--text-muted)] hover:text-[var(--accent-color)] transition cursor-pointer shrink-0"
                      title="Edit this bundle"
                    >
                      <Edit2 className="w-3 h-3" />
                    </button>
                  </div>
                )}
              </div>

              <div>
                <label className="block text-[var(--text-secondary)] mb-1 font-semibold">Test Prompt Payload</label>
                <textarea rows={6} value={testPrompt} onChange={e => setTestPrompt(e.target.value)} placeholder="Enter prompt instructions..." className="w-full bg-[var(--bg-tertiary)] border border-[var(--border-color)] rounded-lg p-3 font-mono text-sm text-[var(--text-primary)] leading-relaxed focus:outline-none focus:border-[var(--accent-color)]" />
              </div>

              {testModelUnverified && (
                <div className="p-2.5 rounded-lg bg-amber-950/30 border border-amber-800/40 text-[11px] font-mono text-amber-300">
                  ⚠ This bundle's model ({testModelObj?.name}) is unverified — the backend refuses test
                  invocations for unverified models. Verify the model first, then run the test.
                </div>
              )}
              <button
                onClick={handleExecuteTest}
                disabled={isRunningTest || roleBundles.length === 0 || !testBundleId || testModelUnverified}
                title={testModelUnverified ? 'Model is unverified — verify it before testing' : undefined}
                className="w-full py-2.5 rounded-lg font-bold text-sm bg-[var(--accent-color)] text-slate-950 hover:bg-[var(--accent-hover)] transition flex items-center justify-center gap-2 shadow-sm cursor-pointer disabled:opacity-50"
              >
                {isRunningTest ? (<><Zap className="w-4 h-4 animate-spin text-slate-950" /><span>Executing Inference in Server...</span></>) : (<><Send className="w-4 h-4" /><span>Run Inference Test</span></>)}
              </button>
            </div>
          </div>

          {/* Panel 3: Inference Output & Trace */}
          <div className="bg-[var(--bg-secondary)] border border-[var(--border-color)] rounded-xl p-4 shadow-sm">
            <div className="flex items-center justify-between pb-2.5 border-b border-[var(--border-subtle)]">
              <div className="flex items-center gap-2">
                <Terminal className="w-5 h-5 text-cyan-400" />
                <h3 className="text-sm font-bold text-[var(--text-primary)]">Inference Output & Trace</h3>
              </div>
              {testResult?.sessionId && (
                <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-emerald-950/60 text-emerald-300 font-bold border border-emerald-800/40">
                  {testResult.sessionId}
                </span>
              )}
            </div>

            {isRunningTest ? (
              <div className="py-20 text-center space-y-3">
                <div className="w-8 h-8 rounded-full border-2 border-[var(--accent-color)] border-t-transparent animate-spin mx-auto" />
                <div className="text-sm font-mono text-[var(--accent-color)] animate-pulse">Dispatching stream roundtrip to backend server...</div>
              </div>
            ) : testError ? (
              <div className="py-12 text-center space-y-3">
                <AlertTriangle className="w-8 h-8 text-rose-400 mx-auto" />
                <div className="text-sm font-semibold text-rose-300">Test Failed</div>
                <p className="text-sm text-[var(--text-secondary)] font-mono max-w-md mx-auto px-4">{testError}</p>
                <button onClick={() => setTestError(null)} className="mt-2 px-3 py-1.5 rounded-lg text-sm font-medium bg-[var(--bg-tertiary)] border border-[var(--border-color)] text-[var(--text-primary)] hover:bg-[var(--bg-hover)] transition cursor-pointer">Dismiss</button>
              </div>
            ) : testResult ? (
              <div className="space-y-3 mt-3">
                {/* Session info banner */}
                <div className="grid grid-cols-4 gap-2 text-sm font-mono">
                  <div className="p-2 rounded bg-[var(--bg-tertiary)] border border-[var(--border-subtle)]">
                    <div className="text-[10px] text-[var(--text-muted)]">Harness</div>
                    <div className="font-bold text-[var(--text-primary)] text-xs">{testResult.harness || 'opencode'}</div>
                  </div>
                  <div className="p-2 rounded bg-[var(--bg-tertiary)] border border-[var(--border-subtle)]">
                    <div className="text-[10px] text-[var(--text-muted)]">Model</div>
                    <div className="font-bold text-[var(--text-primary)] text-xs truncate">{testResult.model_identifier || testResult.model_name || '—'}</div>
                  </div>
                  <div className="p-2 rounded bg-[var(--bg-tertiary)] border border-[var(--border-subtle)]">
                    <div className="text-[10px] text-[var(--text-muted)]">Bundle</div>
                    <div className="font-bold text-[var(--accent-color)] text-xs truncate">{selectedTestBundle?.name || '—'}</div>
                  </div>
                  <div className="p-2 rounded bg-[var(--bg-tertiary)] border border-[var(--border-subtle)]">
                    <div className="text-[10px] text-[var(--text-muted)]">Status</div>
                    <div className="font-bold text-emerald-400 text-xs">RUNNING</div>
                  </div>
                </div>

                {/* Log output */}
                <div className="space-y-1">
                  <div className="flex items-center justify-between">
                    <span className="text-[10px] font-mono uppercase text-[var(--text-muted)]">Live Output</span>
                    <span className="text-[10px] font-mono text-[var(--text-muted)]">{testLogLines.length} lines</span>
                  </div>
                  <div className="p-3.5 rounded-lg bg-[var(--bg-tertiary)] border border-[var(--border-subtle)] font-mono text-xs text-[var(--text-primary)] leading-relaxed max-h-80 overflow-y-auto whitespace-pre-wrap">
                    {testLogLines.length > 0
                      ? testLogLines.join('\n')
                      : 'Waiting for output from harness...'}
                  </div>
                </div>

                {/* Also show raw response if mock-mode gave output */}
                {(testResult.output || testResult.text) && (
                  <div className="space-y-1">
                    <span className="text-[10px] font-mono uppercase text-[var(--text-muted)]">Response Output</span>
                    <div className="p-3.5 rounded-lg bg-[var(--bg-tertiary)] border border-[var(--border-subtle)] font-mono text-sm text-[var(--text-primary)] leading-relaxed max-h-48 overflow-y-auto whitespace-pre-wrap">{testResult.output || testResult.text}</div>
                  </div>
                )}

                {/* Trace if available */}
                {testResult.trace && (
                  <div className="space-y-1">
                    <span className="text-[10px] font-mono uppercase text-[var(--text-muted)]">Subsystem Resolved Trace</span>
                    <pre className="p-2.5 rounded bg-[var(--bg-card)] border border-[var(--border-subtle)] font-mono text-[10px] text-[var(--text-secondary)] overflow-x-auto">{JSON.stringify(testResult.trace, null, 2)}</pre>
                  </div>
                )}
              </div>
            ) : (
              <div className="py-20 text-center text-sm text-[var(--text-muted)] font-mono space-y-2">
                <Sparkles className="w-6 h-6 text-[var(--text-muted)] mx-auto opacity-40" />
                <div>No test execution output yet.</div>
                <p className="text-[11px] opacity-70">Select a config bundle and click 'Run Inference Test' to trigger execution.</p>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* ── Subsystem Architecture Topology Grid ── */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        <div
          onClick={() => onNavigateToTab('registry')}
          className="p-4 rounded-xl bg-[var(--bg-secondary)] border border-[var(--border-color)] hover:border-[var(--accent-color)] transition cursor-pointer space-y-2 group"
        >
          <div className="flex items-center justify-between">
            <span className="text-sm font-mono uppercase text-[var(--text-muted)]">01. Providers</span>
            <Server className="w-4 h-4 text-cyan-400 group-hover:scale-110 transition" />
          </div>
          <div className="text-2xl font-bold font-mono text-[var(--text-primary)]">{providers.length}</div>
          <div className="text-sm text-[var(--text-secondary)]">Google Gemini, OpenAI, Anthropic, Ollama, vLLM</div>
        </div>

        <div
          onClick={() => onNavigateToTab('registry')}
          className="p-4 rounded-xl bg-[var(--bg-secondary)] border border-[var(--border-color)] hover:border-[var(--accent-color)] transition cursor-pointer space-y-2 group"
        >
          <div className="flex items-center justify-between">
            <span className="text-sm font-mono uppercase text-[var(--text-muted)]">02. Harnesses</span>
            <Layers className="w-4 h-4 text-indigo-400 group-hover:scale-110 transition" />
          </div>
          <div className="text-2xl font-bold font-mono text-[var(--text-primary)]">{harnesses.length}</div>
          <div className="text-sm text-[var(--text-secondary)]">Gemini SDK, OpenAI Direct, Anthropic Messages</div>
        </div>

        <div
          onClick={() => onNavigateToTab('registry')}
          className="p-4 rounded-xl bg-[var(--bg-secondary)] border border-[var(--border-color)] hover:border-[var(--accent-color)] transition cursor-pointer space-y-2 group"
        >
          <div className="flex items-center justify-between">
            <span className="text-sm font-mono uppercase text-[var(--text-muted)]">03. Models</span>
            <Cpu className="w-4 h-4 text-[var(--accent-color)] group-hover:scale-110 transition" />
          </div>
          <div className="text-2xl font-bold font-mono text-[var(--text-primary)]">{models.length}</div>
          <div className="text-sm text-[var(--text-secondary)]">gemini-3.6-flash, gpt-4o, claude-3-7-sonnet</div>
        </div>

        <div
          className="p-4 rounded-xl bg-[var(--bg-secondary)] border border-[var(--border-color)] space-y-2 group"
        >
          <div className="flex items-center justify-between">
            <span className="text-sm font-mono uppercase text-[var(--text-muted)]">04. Bundles</span>
            <Zap className="w-4 h-4 text-emerald-400 group-hover:scale-110 transition" />
          </div>
          <div className="text-2xl font-bold font-mono text-[var(--text-primary)]">{bundles.length}</div>
          <div className="text-sm text-[var(--text-secondary)]">Priority ordering, invocation modes & fallback rules</div>
        </div>
      </div>

      {/* ── Bundle Create / Edit Modal ── */}
      {bundleModalOpen && (
        <BundleModal
          key={modalKey}
          initial={editingBundle}
          defaultRole={createDefaultRole}
          onClose={() => setBundleModalOpen(false)}
          models={models}
          providers={providers}
          harnesses={harnesses}
          roles={roles}
          onSaveBundle={onSaveBundle}
        />
      )}
    </div>
  );
};
