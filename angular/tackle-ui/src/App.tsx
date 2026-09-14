import React, { useState, useEffect } from 'react';
import { Header } from './components/Header';
import { Sidebar } from './components/Sidebar';
import { OverviewTab } from './components/OverviewTab';
import { AIRegistryTab } from './components/AIRegistryTab';
import { RolesTasksTab } from './components/RolesTasksTab';
import { MemoryContextTab } from './components/MemoryContextTab';
import { CircuitSchedulerTab } from './components/CircuitSchedulerTab';
import { TasksTab } from './components/TasksTab';
import { SessionsPlaygroundTab } from './components/SessionsPlaygroundTab';
import { SystemLogsTab } from './components/SystemLogsTab';
import { AuditTrailTab } from './components/AuditTrailTab';
import { SystemInsightsTab } from './components/SystemInsightsTab';
import { ErrorBoundary } from './components/ErrorBoundary';
import { ToastContainer } from './components/Toast';
import { ConfirmDialog } from './components/ConfirmDialog';
import { showToast } from './components/Toast';
import { showConfirm } from './components/ConfirmDialog';
import {
  ThemeMode,
  Provider,
  Harness,
  AIModel,
  ConfigBundle,
  SystemRole,
  PromptTemplate,
  TaskDefinition,
  InspectorTaskDispatch,
  FailureRecoveryConfig,
  AgentScheduleEntry,
  SessionLedger,
  ValidationReport} from './types';
import { friendlyFetchError } from './utils/network-errors';
import { unwrapErrorMessage, unwrapList } from './utils/response';

export default function App() {
  const [theme, setTheme] = useState<ThemeMode>('steel');
  const [currentTab, setCurrentTab] = useState<string>('overview');
  const [isMobileMenuOpen, setIsMobileMenuOpen] = useState<boolean>(false);

  // Subsystem States
  const [isOnline, setIsOnline] = useState<boolean>(true);
  const [healthStatus, setHealthStatus] = useState<any>(null);
  const [providers, setProviders] = useState<Provider[]>([]);
  const [harnesses, setHarnesses] = useState<Harness[]>([]);
  const [models, setModels] = useState<AIModel[]>([]);
  const [bundles, setBundles] = useState<ConfigBundle[]>([]);
  const [roles, setRoles] = useState<SystemRole[]>([]);
  const [prompts, setPrompts] = useState<PromptTemplate[]>([]);
  const [tasks, setTasks] = useState<TaskDefinition[]>([]);
  const [inspectorDispatch, setInspectorDispatch] = useState<InspectorTaskDispatch[]>([]);
  const [failureConfig, setFailureConfig] = useState<FailureRecoveryConfig>({
    max_retries_per_model: 3,
    retry_delay_seconds: 5,
    max_fallbacks: 2,
    push_back_to_pending: true,
    circuit_breaker_retry_after: 60
  });
  const [schedules, setSchedules] = useState<AgentScheduleEntry[]>([]);
  const [sessions, setSessions] = useState<SessionLedger[]>([]);
  const [validationReport, setValidationReport] = useState<ValidationReport | null>(null);

  const [loadingInitial, setLoadingInitial] = useState<boolean>(true);
  const [isRefreshingMemory, setIsRefreshingMemory] = useState<boolean>(false);

  // Synchronize theme attribute on html root
  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme);
  }, [theme]);

  // Initial Data Fetch
  useEffect(() => {
    fetchAllData();
  }, []);

  const fetchAllData = async () => {
    try {
      // 1. Health check
      fetch('/health')
        .then(r => r.json())
        .then(d => {
          setIsOnline(true);
          setHealthStatus(d);
        })
        .catch(() => setIsOnline(false));

      // 2. Parallel fetches
      const [
        resProv,
        resHarn,
        resMod,
        resBund,
        resRoles,
        resPrompts,
        resTasks,
        resDisp,
        resFail,
        resSched,
        resSess,
        resVal
      ] = await Promise.all([
        fetch('/config/ai/providers').then(r => r.json()).catch(() => []),
        fetch('/config/ai/harnesses').then(r => r.json()).catch(() => []),
        fetch('/config/ai/models').then(r => r.json()).catch(() => []),
        fetch('/config/ai/bundles').then(r => r.json()).catch(() => []),
        fetch('/roles').then(r => r.json()).catch(() => []),
        fetch('/prompts').then(r => r.json()).catch(() => []),
        fetch('/tasks').then(r => r.json()).catch(() => []),
        fetch('/tasks/inspector/dispatch').then(r => r.json()).catch(() => []),
        fetch('/config/failure-recovery').then(r => r.json()).catch(() => null),
        fetch('/scheduler').then(r => r.json()).catch(() => []),
        fetch('/sessions').then(r => r.json()).catch(() => []),
        fetch('/config/ai/validate').then(r => r.json()).catch(() => null)
      ]);

      setProviders(unwrapList(resProv));
      setHarnesses(unwrapList(resHarn));
      setModels(unwrapList(resMod));
      setBundles(unwrapList(resBund));
      setRoles(unwrapList(resRoles, 'roles'));
      setPrompts(unwrapList(resPrompts, 'prompts'));
      setTasks(unwrapList(resTasks, 'tasks'));
      setInspectorDispatch(unwrapList(resDisp, 'tasks'));
      if (resFail) setFailureConfig(resFail);
      setSchedules(unwrapList(resSched, 'entries'));
      setSessions(unwrapList(resSess));
      if (resVal) setValidationReport(resVal);
    } catch (e) {
      console.error('Error loading tackle state:', e);
    } finally {
      setLoadingInitial(false);
    }
  };

  // Re-validate integrity report
  const handleValidateIntegrity = async () => {
    try {
      const res = await fetch('/config/ai/validate');
      if (res.ok) {
        const report = await res.json();
        setValidationReport(report);
      }
    } catch (e) {
      console.error('Validation error:', e);
    }
  };

  // Memory Refresh Proxy
  const handleRefreshMemory = async () => {
    setIsRefreshingMemory(true);
    try {
      await fetch('/memory/refresh', { method: 'POST' });
    } catch (e) {
      console.error('Memory sync error:', e);
    } finally {
      setTimeout(() => setIsRefreshingMemory(false), 800);
    }
  };

  // Seed Defaults
  const handleSeedDefaults = async () => {
    if (await showConfirm('Reset and re-seed default tackle configurations?')) {
      try {
        await fetch('/config/ai/seed-defaults', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ force: false }) });
        await fetchAllData();
      } catch (e) {
        showToast('Seed error');
      }
    }
  };

  // Unwrap both flat ({ error: "msg" }) and nested ({ error: { message } })
  // error envelopes so alerts always show the real backend message.
  const extractErrorMessage = async (res: Response): Promise<string> => {
    const err = await res.json().catch(() => null);
    return unwrapErrorMessage(err, `Request failed (HTTP ${res.status})`);
  };

  // Map a failed fetch (browser throws TypeError "Failed to fetch") to a
  // friendlier message so alerts don't show a cryptic browser string when the
  // backend is unreachable. Other errors pass through unchanged.
  // Shared request helper — surfaces backend errors instead of silently failing.
  // Every save/toggle/delete handler below uses it so a failed mutation shows
  // the backend error message to the user (via the caller's alert) instead of
  // quietly doing nothing.
  const requestOrThrow = async (url: string, method: string, payload?: unknown) => {
    let res: Response;
    try {
      res = await fetch(url, {
        method,
        headers: { 'Content-Type': 'application/json' },
        body: payload !== undefined ? JSON.stringify(payload) : undefined
      });
    } catch (e) {
      throw friendlyFetchError(e);
    }
    if (!res.ok) {
      throw new Error(await extractErrorMessage(res));
    }
  };

  // Bundle CRUD Actions
  const handleSaveBundle = async (bundle: Partial<ConfigBundle>) => {
    await requestOrThrow('/config/ai/bundle', 'POST', bundle);
    const updatedBundles = await fetch('/config/ai/bundles').then(r => r.json());
    setBundles(updatedBundles);
    handleValidateIntegrity();
  };

  const handleDeleteBundle = async (id: string) => {
    await requestOrThrow(`/config/ai/bundle/${id}`, 'DELETE');
    setBundles(prev => prev.filter(b => b.id !== id));
    handleValidateIntegrity();
  };

  const handleReorderPriority = async (role: string, bundleId: string, direction: 'up' | 'down') => {
    const roleBundles = bundles.filter(b => b.role === role).sort((a, b) => a.priority - b.priority);
    const index = roleBundles.findIndex(b => b.id === bundleId);
    if (index === -1) return;

    const targetIndex = direction === 'up' ? index - 1 : index + 1;
    if (targetIndex < 0 || targetIndex >= roleBundles.length) return;

    // Swap priorities
    const currentBundle = { ...roleBundles[index] };
    const targetBundle = { ...roleBundles[targetIndex] };

    const tempPriority = currentBundle.priority;
    currentBundle.priority = targetBundle.priority;
    targetBundle.priority = tempPriority;

    await Promise.all([
      requestOrThrow('/config/ai/bundle', 'POST', currentBundle),
      requestOrThrow('/config/ai/bundle', 'POST', targetBundle)
    ]);

    const updatedBundles = await fetch('/config/ai/bundles').then(r => r.json());
    setBundles(updatedBundles);
  };

  // Provider CRUD
  const handleSaveProvider = async (prov: Partial<Provider>) => {
    await requestOrThrow('/config/ai/provider', 'POST', prov);
    const updated = await fetch('/config/ai/providers').then(r => r.json());
    setProviders(updated);
  };

  const handleDeleteProvider = async (id: string) => {
    await requestOrThrow(`/config/ai/provider/${id}`, 'DELETE');
    setProviders(prev => prev.filter(p => p.id !== id));
  };

  // Harness CRUD
  const handleSaveHarness = async (harn: Partial<Harness>) => {
    await requestOrThrow('/config/ai/harness', 'POST', harn);
    const updated = await fetch('/config/ai/harnesses').then(r => r.json());
    setHarnesses(updated);
  };

  const handleDeleteHarness = async (id: string) => {
    await requestOrThrow(`/config/ai/harness/${id}`, 'DELETE');
    setHarnesses(prev => prev.filter(h => h.id !== id));
  };

  // Model CRUD
  const handleSaveModel = async (mod: Partial<AIModel>) => {
    await requestOrThrow('/config/ai/model', 'POST', mod);
    const updated = await fetch('/config/ai/models').then(r => r.json());
    setModels(unwrapList(updated));
  };

  const handleDeleteModel = async (id: string) => {
    await requestOrThrow(`/config/ai/model/${id}`, 'DELETE');
    setModels(prev => prev.filter(m => m.id !== id));
  };

  // Role CRUD
  const handleSaveRole = async (role: Partial<SystemRole>) => {
    await requestOrThrow('/roles', 'POST', role);
    const updated = await fetch('/roles').then(r => r.json());
    setRoles(unwrapList(updated, 'roles'));
  };

  const handleDeleteRole = async (id: string) => {
    await requestOrThrow(`/roles/${id}`, 'DELETE');
    setRoles(prev => prev.filter(r => r.id !== id));
  };

  // Prompt & Task CRUD
  const handleSavePrompt = async (prompt: Partial<PromptTemplate>) => {
    await requestOrThrow('/prompts', 'POST', prompt);
    const updated = await fetch('/prompts').then(r => r.json());
    setPrompts(unwrapList(updated, 'prompts'));
  };

  const handleSaveTask = async (task: Partial<TaskDefinition>) => {
    await requestOrThrow('/tasks', 'POST', task);
    const [updatedTasks, updatedDisp] = await Promise.all([
      fetch('/tasks').then(r => r.json()),
      fetch('/tasks/inspector/dispatch').then(r => r.json())
    ]);
    setTasks(unwrapList(updatedTasks, 'tasks'));
    setInspectorDispatch(unwrapList(updatedDisp, 'tasks'));
  };

  const handleDeleteTask = async (task: TaskDefinition) => {
    await requestOrThrow(`/tasks/${encodeURIComponent(task.task_slug)}?role=${encodeURIComponent(task.role)}`, 'DELETE');
    const [updatedTasks, updatedDisp, updatedSched] = await Promise.all([
      fetch('/tasks').then(r => r.json()),
      fetch('/tasks/inspector/dispatch').then(r => r.json()),
      fetch('/scheduler').then(r => r.json())
    ]);
    setTasks(unwrapList(updatedTasks, 'tasks'));
    setInspectorDispatch(unwrapList(updatedDisp, 'tasks'));
    setSchedules(unwrapList(updatedSched, 'entries'));
  };

  // Failure Recovery
  const handleSaveFailureConfig = async (config: FailureRecoveryConfig) => {
    await requestOrThrow('/config/failure-recovery', 'POST', config);
    setFailureConfig(config);
  };

  // Schedule CRUD — with an id this updates the existing entry (PATCH),
  // otherwise it creates a new one (POST). The mock and real backends both
  // treat PATCH /scheduler/:id as a partial update of the stored row.
  const handleSaveSchedule = async (sched: Partial<AgentScheduleEntry>) => {
    if (sched.id) {
      await requestOrThrow(`/scheduler/${sched.id}`, 'PATCH', sched);
    } else {
      await requestOrThrow('/scheduler', 'POST', sched);
    }
    const updated = await fetch('/scheduler').then(r => r.json());
    setSchedules(unwrapList(updated, 'entries'));
  };

  const handleToggleSchedule = async (id: string, enabled: boolean) => {
    await requestOrThrow(`/scheduler/${id}`, 'PATCH', { enabled });
    setSchedules(prev => prev.map(s => (s.id === id ? { ...s, enabled } : s)));
  };

  const handleDeleteSchedule = async (id: string) => {
    await requestOrThrow(`/scheduler/${id}`, 'DELETE');
    setSchedules(prev => prev.filter(s => s.id !== id));
  };

  // Kill Session
  const handleKillSession = async (sessionId: string) => {
    await requestOrThrow(`/sessions/${sessionId}/kill`, 'POST');
    const updated = await fetch('/sessions').then(r => r.json());
    setSessions(unwrapList(updated));
  };

  // Run Test Payload
  const handleRunTest = async (role: string, modelId: string, promptText: string) => {
    let res: Response;
    try {
      res = await fetch('/config/ai/test', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          model_id: modelId,
          test_prompt: promptText
        })
      });
    } catch (e) {
      throw friendlyFetchError(e);
    }
    if (!res.ok) {
      throw new Error(await extractErrorMessage(res));
    }
    return await res.json();
  };

  const handleVerifyModel = async (modelId: string, prompt?: string) => {
    let res: Response;
    try {
      res = await fetch('/config/ai/verify', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          model_id: modelId,
          test_prompt: prompt
        })
      });
    } catch (e) {
      throw friendlyFetchError(e);
    }
    if (!res.ok) {
      throw new Error(await extractErrorMessage(res));
    }
    return await res.json();
  };

  const handleVerifyStatus = async (sessionId: string) => {
    let res: Response;
    try {
      res = await fetch(`/config/ai/verify/${sessionId}`);
    } catch (e) {
      throw friendlyFetchError(e);
    }
    if (!res.ok) {
      throw new Error(await extractErrorMessage(res));
    }
    return await res.json();
  };

  // Purge unverified models from the inference chain: force-deactivate every
  // config bundle (across all roles) whose model is unverified.
  const handlePurgeUnverified = async () => {
    try {
      const res = await fetch('/config/ai/verify/purge-unverified', { method: 'POST' });
      if (!res.ok) throw new Error(await extractErrorMessage(res));
      const data = await res.json();
      const roleCounts = (data.roles || []).map((r: any) => `${r.role}:${r.bundleIds.length}`).join(', ');
      showToast(`Purged ${data.purged ?? 0} unverified bundle(s) from the inference chain${roleCounts ? ` — ${roleCounts}` : ''}`);
      await fetchAllData();
      handleValidateIntegrity();
    } catch (e) {
      showToast(`Purge error: ${e instanceof Error ? e.message : String(e)}`);
    }
  };

  // Verify a specific config bundle's model through a fresh inference run,
  // polling the verify session status until it settles, then refresh so the
  // model's verified state + bundle re-arm reflect the outcome.
  const handleVerifyBundle = async (bundleId: string, modelId: string) => {
    let res: Response;
    try {
      res = await fetch('/config/ai/verify', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ model_id: modelId })
      });
    } catch (e) {
      throw friendlyFetchError(e);
    }
    if (!res.ok) throw new Error(await extractErrorMessage(res));
    const started = await res.json();

    if (started?.alreadyVerified) return started;
    if (!started?.sessionId) return started;

    const deadline = Date.now() + 180_000;
    let status: any = null;
    do {
      await new Promise(r => setTimeout(r, 1500));
      status = await handleVerifyStatus(started.sessionId);
    } while (status?.running && Date.now() < deadline);

    if (status?.running) {
      return { ...started, verified: false, message: 'Verification timed out after 180s — see session log' };
    }
    const ok = status?.exit_code === 0;
    await fetchAllData();
    return {
      ...started,
      verified: ok,
      message: ok
        ? 'Model verified — bundle re-armed'
        : 'Verify failed — model marked UNVERIFIED; its bundles were forced inactive. Update the role(s) that referenced it.',
    };
  };

  const activeSessionsCount = sessions.filter(s => s.status === 'running').length;

  if (loadingInitial) {
    return (
      <div className="min-h-screen bg-[var(--bg-primary)] text-[var(--text-primary)] flex items-center justify-center p-6 font-mono text-sm">
        <div className="space-y-4 text-center">
          <div className="w-10 h-10 border-2 border-[var(--accent-color)] border-t-transparent animate-spin rounded-full mx-auto" />
          <div className="text-sm font-bold tracking-tight animate-pulse">
            Bootstrapping Tackle Subsystem State & REST REST API...
          </div>
          <p className="text-[var(--text-muted)] text-[11px]">
            Connecting to PostgreSQL schema on port 3410
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="h-screen overflow-hidden bg-[var(--bg-primary)] text-[var(--text-primary)] transition-colors duration-200 flex flex-col">
      <Header
        theme={theme}
        setTheme={setTheme}
        isOnline={isOnline}
        activeSessionCount={activeSessionsCount}
        healthStatus={healthStatus}
        onRefreshMemory={handleRefreshMemory}
        isRefreshingMemory={isRefreshingMemory}
        onToggleMobileMenu={() => setIsMobileMenuOpen(!isMobileMenuOpen)}
      />

      <div className="flex flex-1 min-h-0 overflow-hidden">
        <Sidebar
          currentTab={currentTab}
          setCurrentTab={setCurrentTab}
          isOpenMobile={isMobileMenuOpen}
          onCloseMobile={() => setIsMobileMenuOpen(false)}
        />

        <div className="flex-1 flex flex-col min-w-0 overflow-y-auto">
          <main className="flex-1 p-4 sm:p-6 lg:p-8">
            <ErrorBoundary key={currentTab} label={`Tab '${currentTab}'`}>
            {currentTab === 'overview' && (
              <OverviewTab
                roles={roles}
                models={models}
                providers={providers}
                harnesses={harnesses}
                bundles={bundles}
                validationReport={validationReport}
                onValidate={handleValidateIntegrity}
                onRunTest={handleRunTest}
                onPurgeUnverified={handlePurgeUnverified}
                onVerifyBundle={handleVerifyBundle}
                onSeedDefaults={handleSeedDefaults}
                onNavigateToTab={setCurrentTab}
                onSaveBundle={handleSaveBundle}
                onDeleteBundle={handleDeleteBundle}
                onReorderPriority={handleReorderPriority}
              />
            )}

            {currentTab === 'registry' && (
              <AIRegistryTab
                providers={providers}
                harnesses={harnesses}
                models={models}
                roles={roles}
                bundles={bundles}
                onSaveProvider={handleSaveProvider}
                onDeleteProvider={handleDeleteProvider}
                onSaveHarness={handleSaveHarness}
                onDeleteHarness={handleDeleteHarness}
                onSaveModel={handleSaveModel}
                onDeleteModel={handleDeleteModel}
                onSaveBundle={handleSaveBundle}
                onVerifyModel={handleVerifyModel}
                onVerifyStatus={handleVerifyStatus}
                onRefresh={fetchAllData}
              />
            )}

            {currentTab === 'roles-tasks' && (
              <RolesTasksTab
                roles={roles}
                prompts={prompts}
                tasks={tasks}
                inspectorDispatch={inspectorDispatch}
                onSaveRole={handleSaveRole}
                onDeleteRole={handleDeleteRole}
                onSavePrompt={handleSavePrompt}
                onSaveTask={handleSaveTask}
                onRefresh={fetchAllData}
              />
            )}

            {currentTab === 'memory' && (
              <MemoryContextTab
                roles={roles}
                onRefreshMemory={handleRefreshMemory}
                isRefreshingMemory={isRefreshingMemory}
              />
            )}

            {currentTab === 'circuit-sched' && (
              <CircuitSchedulerTab
                failureConfig={failureConfig}
                onSaveFailureConfig={handleSaveFailureConfig}
                schedules={schedules}
                roles={roles}
                models={models}
                tasks={tasks}
                prompts={prompts}
                onSaveSchedule={handleSaveSchedule}
                onToggleSchedule={handleToggleSchedule}
                onDeleteSchedule={handleDeleteSchedule}
              />
            )}

            {currentTab === 'tasks' && (
              <TasksTab
                tasks={tasks}
                prompts={prompts}
                roles={roles}
                onSaveTask={handleSaveTask}
                onDeleteTask={handleDeleteTask}
              />
            )}

            {currentTab === 'sessions-playground' && (
              <SessionsPlaygroundTab
                sessions={sessions}
                roles={roles}
                models={models}
                onKillSession={handleKillSession}
                onRunTest={handleRunTest}
              />
            )}

            {currentTab === 'system-logs' && (
              <SystemLogsTab />
            )}

            {currentTab === 'audit-trail' && (
              <AuditTrailTab />
            )}

            {currentTab === 'system-insights' && (
              <SystemInsightsTab initialHealthStatus={healthStatus} />
            )}
            </ErrorBoundary>
          </main>

          <footer className="border-t border-[var(--border-subtle)] bg-[var(--bg-secondary)] py-4 mt-auto">
            <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 flex flex-col sm:flex-row items-center justify-between text-sm font-mono text-[var(--text-muted)] gap-2">
              <div className="flex items-center gap-2">
                <span className="w-2 h-2 rounded-full bg-emerald-400" />
                <span>Tackle Subsystem REST Server :3410</span>
                <span>•</span>
                <span>Role Memory Cache :3500</span>
              </div>
              <div>
                <span>PostgreSQL `tackle` Schema • Gemini Server SDK Proxy</span>
              </div>
            </div>
          </footer>
        </div>
      </div>
      <ToastContainer />
      <ConfirmDialog />
    </div>
  );
}
