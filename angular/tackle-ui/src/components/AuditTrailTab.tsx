import React, { useState, useEffect, useCallback, useMemo } from 'react';
import {
  Search,
  RefreshCw,
  ShieldCheck,
  Filter,
  ChevronDown,
  ChevronRight,
  Copy,
  Check,
  Download,
  Clock,
  Activity,
  Info,
  EyeOff,
  Database,
  ArrowDownNarrowWide,
  ArrowUpNarrowWide
} from 'lucide-react';
import { friendlyFetchError } from '../utils/network-errors';

export interface AuditTrailEntry {
  timestamp: string;
  category: string;
  audited_table: string | null;
  operation: string;
  row_count: number;
  keys: string | null;
  application_name: string | null;
  client_addr: string | null;
  txid: number | null;
  message: string;
}

const AUDIT_CATEGORIES = ['REGISTRY_AUDIT', 'NEBULA_AUDIT'];

const OP_STYLES: Record<string, string> = {
  INSERT: 'bg-emerald-500/15 text-emerald-400 border border-emerald-500/30',
  UPDATE: 'bg-amber-500/15 text-amber-400 border border-amber-500/30',
  DELETE: 'bg-rose-500/15 text-rose-400 border border-rose-500/30'
};

const CATEGORY_STYLES: Record<string, string> = {
  REGISTRY_AUDIT: 'bg-violet-500/15 text-violet-300 border border-violet-500/30',
  NEBULA_AUDIT: 'bg-sky-500/15 text-sky-300 border border-sky-500/30'
};

export const AuditTrailTab: React.FC = () => {
  const [entries, setEntries] = useState<AuditTrailEntry[]>([]);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [isRefreshing, setIsRefreshing] = useState<boolean>(false);
  const [endpointAvailable, setEndpointAvailable] = useState<boolean>(true);
  const [fetchError, setFetchError] = useState<string | null>(null);
  const [autoRefresh, setAutoRefresh] = useState<boolean>(true);
  const [pollIntervalMs, setPollIntervalMs] = useState<number>(10000);
  const [lastPolledAt, setLastPolledAt] = useState<string | null>(null);

  // Filters
  const [searchQuery, setSearchQuery] = useState<string>('');
  const [selectedOp, setSelectedOp] = useState<string>('ALL');
  const [selectedTable, setSelectedTable] = useState<string>('ALL');
  const [selectedCategory, setSelectedCategory] = useState<string>('ALL');
  const [timeWindow, setTimeWindow] = useState<string>('ALL');
  const [sortOrder, setSortOrder] = useState<'desc' | 'asc'>('desc');

  // Interactive
  const [expandedIds, setExpandedIds] = useState<Set<string>>(new Set());
  const [copiedId, setCopiedId] = useState<string | null>(null);

  const fetchTrail = useCallback(async (isSilent = false) => {
    if (!isSilent) setIsRefreshing(true);
    try {
      const queryParams = new URLSearchParams();
      queryParams.append('limit', '250');
      const res = await fetch(`/audit-trail?${queryParams.toString()}`);
      if (res.status === 404) {
        setEndpointAvailable(false);
        return;
      }
      if (res.ok) {
        const data = await res.json();
        setEntries(data.entries || []);
        setEndpointAvailable(true);
        setFetchError(null);
        setLastPolledAt(new Date().toISOString());
      } else {
        setFetchError(`HTTP ${res.status}`);
      }
    } catch (err: any) {
      setFetchError(friendlyFetchError ? friendlyFetchError(err) : (err?.message || 'network error'));
      console.error('Failed to fetch audit trail:', err);
    } finally {
      setIsLoading(false);
      setIsRefreshing(false);
    }
  }, []);

  useEffect(() => {
    fetchTrail();
  }, [fetchTrail]);

  useEffect(() => {
    if (!autoRefresh) return;
    const interval = setInterval(() => fetchTrail(true), pollIntervalMs);
    return () => clearInterval(interval);
  }, [autoRefresh, pollIntervalMs, fetchTrail]);

  const toggleExpand = (id: string) => {
    setExpandedIds(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const handleCopy = (entry: AuditTrailEntry) => {
    const text = `[${entry.timestamp}] [${entry.category}] ${entry.message} | app=${entry.application_name || 'n/a'} client=${entry.client_addr || 'n/a'} txid=${entry.txid ?? 'n/a'}`;
    navigator.clipboard.writeText(text);
    setCopiedId(entry.message + entry.timestamp);
    setTimeout(() => setCopiedId(null), 2000);
  };

  const handleExportJSON = () => {
    const dataStr = 'data:text/json;charset=utf-8,' + encodeURIComponent(JSON.stringify(filteredEntries, null, 2));
    const downloadAnchor = document.createElement('a');
    downloadAnchor.setAttribute('href', dataStr);
    downloadAnchor.setAttribute('download', `tackle-audit-trail-${new Date().toISOString().slice(0, 19)}.json`);
    document.body.appendChild(downloadAnchor);
    downloadAnchor.click();
    downloadAnchor.remove();
  };

  // Distinct tables present in the data (for the table filter dropdown)
  const knownTables = useMemo(() => {
    const set = new Set<string>();
    entries.forEach(e => { if (e.audited_table) set.add(e.audited_table); });
    return Array.from(set).sort();
  }, [entries]);

  const filteredEntries = useMemo(() => entries.filter(e => {
    if (selectedOp !== 'ALL' && e.operation !== selectedOp) return false;
    if (selectedTable !== 'ALL' && e.audited_table !== selectedTable) return false;
    if (selectedCategory !== 'ALL' && e.category !== selectedCategory) return false;
    if (timeWindow !== 'ALL') {
      const ageMs = Date.now() - new Date(e.timestamp).getTime();
      if (timeWindow === '1H' && ageMs > 3600_000) return false;
      if (timeWindow === '24H' && ageMs > 24 * 3600_000) return false;
      if (timeWindow === '7D' && ageMs > 7 * 24 * 3600_000) return false;
    }
    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase();
      const hay = `${e.message} ${e.keys || ''} ${e.audited_table || ''} ${e.client_addr || ''} ${e.application_name || ''}`.toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  }).sort((a, b) => {
    const ta = new Date(a.timestamp).getTime();
    const tb = new Date(b.timestamp).getTime();
    return sortOrder === 'desc' ? tb - ta : ta - tb;
  }), [entries, selectedOp, selectedTable, selectedCategory, timeWindow, searchQuery, sortOrder]);

  const insertCount = entries.filter(e => e.operation === 'INSERT').length;
  const updateCount = entries.filter(e => e.operation === 'UPDATE').length;
  const deleteCount = entries.filter(e => e.operation === 'DELETE').length;

  const formatRelativeTime = (isoString: string) => {
    const diffSec = Math.floor((Date.now() - new Date(isoString).getTime()) / 1000);
    if (diffSec < 5) return 'just now';
    if (diffSec < 60) return `${diffSec}s ago`;
    if (diffSec < 3600) return `${Math.floor(diffSec / 60)}m ago`;
    if (diffSec < 86400) return `${Math.floor(diffSec / 3600)}h ago`;
    return `${Math.floor(diffSec / 86400)}d ago`;
  };

  return (
    <div className="space-y-6">
      {/* Header Banner */}
      <div className="bg-[var(--bg-card)] border border-[var(--border-color)] rounded-xl p-5 shadow-xs">
        <div className="flex flex-col lg:flex-row lg:items-center justify-between gap-4">
          <div>
            <div className="flex items-center gap-2 mb-1">
              <span className="text-[10px] font-mono font-bold uppercase tracking-wider text-[var(--accent-color)] bg-[var(--badge-bg)] px-2 py-0.5 rounded border border-[var(--border-color)]">
                Governance
              </span>
              <span className="flex items-center gap-1 text-[11px] font-mono text-[var(--text-muted)]">
                <EyeOff className="w-3 h-3" />
                Read-only by design
              </span>
            </div>
            <h2 className="text-xl font-bold tracking-tight text-[var(--text-primary)]">
              Audit Trail — Canonical Store Mutations
            </h2>
            <p className="text-sm text-[var(--text-secondary)] mt-1">
              Who touched what, from where, when. Statement-level trail over{' '}
              <code className="font-mono text-violet-400">tackle.memory</code>,{' '}
              <code className="font-mono text-violet-400">tackle.role_memory</code>,{' '}
              <code className="font-mono text-sky-400">nebula.agent_records_history</code> and{' '}
              <code className="font-mono text-sky-400">nebula.harvests_history</code> (V155/V156). Audit rows
              cannot be deleted without a deliberate DB-level opt-in (V157 erase guard).
            </p>
          </div>

          <div className="flex items-center gap-3 flex-wrap shrink-0">
            <div className="flex items-center gap-2 bg-[var(--bg-tertiary)] border border-[var(--border-color)] rounded-lg p-1.5 px-3">
              <button
                onClick={() => setAutoRefresh(!autoRefresh)}
                className={`flex items-center gap-1.5 text-sm font-semibold px-2.5 py-1 rounded transition cursor-pointer ${
                  autoRefresh
                    ? 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/30'
                    : 'bg-amber-500/20 text-amber-400 border border-amber-500/30'
                }`}
              >
                <Activity className="w-3 h-3" />
                <span>{autoRefresh ? 'LIVE POLLING' : 'PAUSED'}</span>
              </button>
              {autoRefresh && (
                <select
                  value={pollIntervalMs}
                  onChange={e => setPollIntervalMs(Number(e.target.value))}
                  className="bg-[var(--bg-card)] border border-[var(--border-color)] text-[var(--text-secondary)] text-sm rounded px-2 py-1 outline-none font-mono"
                >
                  <option value={5000}>5s interval</option>
                  <option value={10000}>10s interval</option>
                  <option value={30000}>30s interval</option>
                  <option value={60000}>60s interval</option>
                </select>
              )}
            </div>

            <button
              onClick={() => fetchTrail()}
              disabled={isRefreshing}
              className="flex items-center gap-1.5 px-3 py-2 bg-[var(--bg-tertiary)] hover:bg-[var(--bg-hover)] border border-[var(--border-color)] rounded-lg text-sm font-medium text-[var(--text-primary)] transition cursor-pointer disabled:opacity-50"
              title="Refresh audit trail"
            >
              <RefreshCw className={`w-3.5 h-3.5 text-[var(--accent-color)] ${isRefreshing ? 'animate-spin' : ''}`} />
              <span>Sync</span>
            </button>

            <button
              onClick={handleExportJSON}
              className="p-2 bg-[var(--bg-tertiary)] hover:bg-[var(--bg-hover)] border border-[var(--border-color)] rounded-lg text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition cursor-pointer"
              title="Export filtered audit entries (JSON)"
            >
              <Download className="w-4 h-4" />
            </button>
          </div>
        </div>

        {/* Stats Metrics Bar */}
        <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-5 gap-3 mt-4 pt-4 border-t border-[var(--border-subtle)]">
          <div className="bg-[var(--bg-tertiary)] border border-[var(--border-subtle)] rounded-lg p-2.5 px-3">
            <span className="text-[10px] font-mono text-[var(--text-muted)] uppercase block">Audit Entries</span>
            <span className="text-base font-bold text-[var(--text-primary)] font-mono">{entries.length}</span>
          </div>
          <div className="bg-[var(--bg-tertiary)] border border-emerald-500/20 rounded-lg p-2.5 px-3">
            <span className="text-[10px] font-mono text-emerald-400 uppercase block">Inserts</span>
            <span className="text-base font-bold text-emerald-400 font-mono">{insertCount}</span>
          </div>
          <div className="bg-[var(--bg-tertiary)] border border-amber-500/20 rounded-lg p-2.5 px-3">
            <span className="text-[10px] font-mono text-amber-400 uppercase block">Updates</span>
            <span className="text-base font-bold text-amber-400 font-mono">{updateCount}</span>
          </div>
          <div className="bg-[var(--bg-tertiary)] border border-rose-500/20 rounded-lg p-2.5 px-3">
            <span className="text-[10px] font-mono text-rose-400 uppercase block">Deletes</span>
            <span className="text-base font-bold text-rose-400 font-mono">{deleteCount}</span>
          </div>
          <div className="col-span-2 sm:col-span-4 lg:col-span-1 bg-[var(--bg-tertiary)] border border-[var(--border-subtle)] rounded-lg p-2.5 px-3 flex items-center justify-between">
            <div>
              <span className="text-[10px] font-mono text-[var(--text-muted)] uppercase block">Last Polled</span>
              <span className="text-sm font-mono text-[var(--text-secondary)]">
                {lastPolledAt ? new Date(lastPolledAt).toLocaleTimeString() : 'Never'}
              </span>
            </div>
            <Activity className={`w-4 h-4 ${autoRefresh ? 'text-emerald-400 animate-pulse' : 'text-[var(--text-muted)]'}`} />
          </div>
        </div>
      </div>

      {/* Filters */}
      <div className="bg-[var(--bg-card)] border border-[var(--border-color)] rounded-xl p-4">
        <div className="flex flex-col md:flex-row gap-3 items-stretch md:items-center justify-between">
          <div className="relative flex-1">
            <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-[var(--text-muted)]" />
            <input
              type="text"
              value={searchQuery}
              onChange={e => setSearchQuery(e.target.value)}
              placeholder="Search keys, table, client, application..."
              className="w-full bg-[var(--bg-tertiary)] border border-[var(--border-color)] text-[var(--text-primary)] rounded-lg pl-9 pr-3 py-2 text-sm focus:ring-1 focus:ring-[var(--accent-color)] outline-none transition"
            />
          </div>

          <div className="flex items-center gap-2 flex-wrap">
            {/* Operation pills */}
            <div className="flex items-center bg-[var(--bg-tertiary)] border border-[var(--border-color)] rounded-lg p-1 text-sm">
              {['ALL', 'INSERT', 'UPDATE', 'DELETE'].map(op => (
                <button
                  key={op}
                  onClick={() => setSelectedOp(op)}
                  className={`px-2.5 py-1 rounded-md font-mono font-medium text-[11px] transition cursor-pointer ${
                    selectedOp === op
                      ? 'bg-[var(--accent-color)] text-white font-bold shadow-xs'
                      : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
                  }`}
                >
                  {op}
                </button>
              ))}
            </div>

            {/* Table dropdown */}
            <div className="flex items-center gap-1.5 bg-[var(--bg-tertiary)] border border-[var(--border-color)] rounded-lg px-2.5 py-1.5 text-sm">
              <Database className="w-3.5 h-3.5 text-[var(--text-muted)]" />
              <select
                value={selectedTable}
                onChange={e => setSelectedTable(e.target.value)}
                className="bg-transparent text-[var(--text-primary)] font-mono text-sm outline-none cursor-pointer"
              >
                <option value="ALL">All Tables</option>
                {knownTables.map(t => (
                  <option key={t} value={t}>{t}</option>
                ))}
              </select>
            </div>

            {/* Category dropdown */}
            <div className="flex items-center gap-1.5 bg-[var(--bg-tertiary)] border border-[var(--border-color)] rounded-lg px-2.5 py-1.5 text-sm">
              <Filter className="w-3.5 h-3.5 text-[var(--text-muted)]" />
              <select
                value={selectedCategory}
                onChange={e => setSelectedCategory(e.target.value)}
                className="bg-transparent text-[var(--text-primary)] font-mono text-sm outline-none cursor-pointer"
              >
                <option value="ALL">All Categories</option>
                {AUDIT_CATEGORIES.map(c => (
                  <option key={c} value={c}>{c}</option>
                ))}
              </select>
            </div>

            {/* Time window */}
            <select
              value={timeWindow}
              onChange={e => setTimeWindow(e.target.value)}
              className="bg-[var(--bg-tertiary)] border border-[var(--border-color)] text-[var(--text-primary)] font-mono text-sm rounded-lg px-2.5 py-2 outline-none cursor-pointer"
            >
              <option value="ALL">All Time</option>
              <option value="1H">Last hour</option>
              <option value="24H">Last 24h</option>
              <option value="7D">Last 7 days</option>
            </select>

            {/* Sort toggle */}
            <button
              onClick={() => setSortOrder(sortOrder === 'desc' ? 'asc' : 'desc')}
              className="px-2.5 py-2 bg-[var(--bg-tertiary)] border border-[var(--border-color)] rounded-lg text-sm font-mono text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition cursor-pointer"
              title="Toggle sort order"
            >
              {sortOrder === 'desc'
                ? <ArrowDownNarrowWide className="w-4 h-4 inline" />
                : <ArrowUpNarrowWide className="w-4 h-4 inline" />}
              {sortOrder === 'desc' ? ' Newest First' : ' Oldest First'}
            </button>
          </div>
        </div>
      </div>

      {/* Entries */}
      <div className="bg-[var(--bg-card)] border border-[var(--border-color)] rounded-xl overflow-hidden shadow-xs">
        <div className="p-3 px-4 bg-[var(--bg-tertiary)] border-b border-[var(--border-color)] flex items-center justify-between text-sm font-mono text-[var(--text-muted)]">
          <div className="flex items-center gap-2">
            <ShieldCheck className="w-4 h-4 text-[var(--accent-color)]" />
            <span>AUDIT TRAIL ({filteredEntries.length} matching)</span>
          </div>
          <span className="flex items-center gap-1">
            <Clock className="w-3 h-3" />
            append-only · erase-guarded
          </span>
        </div>

        {isLoading ? (
          <div className="p-12 text-center text-sm text-[var(--text-muted)] font-mono flex flex-col items-center justify-center gap-2">
            <RefreshCw className="w-6 h-6 animate-spin text-[var(--accent-color)]" />
            <span>Loading audit trail...</span>
          </div>
        ) : !endpointAvailable ? (
          <div className="p-12 text-center text-sm text-[var(--text-muted)] font-mono flex flex-col items-center justify-center gap-2">
            <Info className="w-6 h-6 text-amber-400" />
            <span>GET /audit-trail not available on this backend.</span>
            <span className="text-[11px]">Requires nexus#241 (V157 audit surface) deployed to tackle-srv.</span>
            <span className="text-[11px]">SQL fallback: SELECT * FROM tackle.audit_trail;</span>
          </div>
        ) : fetchError ? (
          <div className="p-12 text-center text-sm text-[var(--text-muted)] font-mono flex flex-col items-center justify-center gap-2">
            <Info className="w-6 h-6 text-rose-400" />
            <span>Fetch failed: {fetchError}</span>
            <button onClick={() => fetchTrail()} className="text-[var(--accent-color)] underline cursor-pointer hover:opacity-80 mt-1">
              Retry
            </button>
          </div>
        ) : filteredEntries.length === 0 ? (
          <div className="p-12 text-center text-sm text-[var(--text-muted)] font-mono flex flex-col items-center justify-center gap-2">
            <Info className="w-6 h-6 text-[var(--text-muted)]" />
            <span>No audit entries match current filters.</span>
            <button
              onClick={() => {
                setSearchQuery('');
                setSelectedOp('ALL');
                setSelectedTable('ALL');
                setSelectedCategory('ALL');
                setTimeWindow('ALL');
              }}
              className="text-[var(--accent-color)] underline cursor-pointer hover:opacity-80 mt-1"
            >
              Reset all filters
            </button>
          </div>
        ) : (
          <div className="divide-y divide-[var(--border-subtle)] font-mono text-sm">
            {filteredEntries.map(entry => {
              const id = `${entry.txid}-${entry.timestamp}`;
              const isExpanded = expandedIds.has(id);
              const opStyle = OP_STYLES[entry.operation] || 'bg-[var(--bg-tertiary)] text-[var(--text-secondary)] border border-[var(--border-subtle)]';
              const catStyle = CATEGORY_STYLES[entry.category] || 'bg-[var(--bg-tertiary)] text-[var(--text-secondary)] border border-[var(--border-subtle)]';

              return (
                <div key={id} className="hover:bg-[var(--bg-hover)] transition-colors">
                  <div
                    onClick={() => toggleExpand(id)}
                    className="p-3 px-4 flex flex-col md:flex-row md:items-center gap-2 cursor-pointer select-none"
                  >
                    <div className="flex items-center gap-2 shrink-0">
                      <button className="text-[var(--text-muted)]">
                        {isExpanded ? <ChevronDown className="w-3.5 h-3.5 text-[var(--accent-color)]" /> : <ChevronRight className="w-3.5 h-3.5" />}
                      </button>
                      <span className="text-[11px] text-[var(--text-muted)] font-mono w-24 shrink-0" title={entry.timestamp}>
                        {formatRelativeTime(entry.timestamp)}
                      </span>
                      <span className={`px-2 py-0.5 rounded text-[10px] font-mono font-bold ${opStyle}`}>
                        {entry.operation}
                      </span>
                      <span className={`px-2 py-0.5 rounded text-[10px] font-mono font-medium uppercase ${catStyle}`}>
                        {entry.category}
                      </span>
                    </div>

                    <div className="flex-1 min-w-0 font-mono truncate">
                      <span className="text-[var(--text-primary)]">{entry.audited_table || '?'}</span>
                      <span className="text-[var(--text-muted)]"> · {entry.message}</span>
                    </div>

                    <div className="flex items-center gap-3 text-[11px] text-[var(--text-muted)] shrink-0 self-end md:self-auto">
                      {entry.client_addr && (
                        <span className="bg-[var(--bg-tertiary)] px-2 py-0.5 rounded border border-[var(--border-subtle)]" title="client address">
                          {entry.client_addr}
                        </span>
                      )}
                      {entry.application_name && (
                        <span className="bg-[var(--bg-tertiary)] px-2 py-0.5 rounded border border-[var(--border-subtle)]" title="application_name">
                          {entry.application_name}
                        </span>
                      )}
                      <button
                        onClick={e => { e.stopPropagation(); handleCopy(entry); }}
                        className="p-1 hover:text-[var(--text-primary)] rounded cursor-pointer"
                        title="Copy audit entry"
                      >
                        {copiedId === id ? <Check className="w-3.5 h-3.5 text-emerald-400" /> : <Copy className="w-3.5 h-3.5" />}
                      </button>
                    </div>
                  </div>

                  {isExpanded && (
                    <div className="p-4 bg-slate-950/80 border-t border-[var(--border-subtle)] text-sm font-mono space-y-3">
                      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-[11px]">
                        <div>
                          <span className="text-[10px] uppercase font-bold text-[var(--accent-color)] block">Timestamp</span>
                          <span className="text-slate-300">{entry.timestamp}</span>
                        </div>
                        <div>
                          <span className="text-[10px] uppercase font-bold text-[var(--accent-color)] block">Row Count</span>
                          <span className="text-slate-300">{entry.row_count}</span>
                        </div>
                        <div>
                          <span className="text-[10px] uppercase font-bold text-[var(--accent-color)] block">Affected Keys</span>
                          <span className="text-slate-300">{entry.keys || '—'}</span>
                        </div>
                        <div>
                          <span className="text-[10px] uppercase font-bold text-[var(--accent-color)] block">TxID</span>
                          <span className="text-slate-300">{entry.txid ?? '—'}</span>
                        </div>
                        <div>
                          <span className="text-[10px] uppercase font-bold text-[var(--accent-color)] block">Application</span>
                          <span className="text-slate-300">{entry.application_name || '—'}</span>
                        </div>
                        <div>
                          <span className="text-[10px] uppercase font-bold text-[var(--accent-color)] block">Client</span>
                          <span className="text-slate-300">{entry.client_addr || '—'}</span>
                        </div>
                        <div className="col-span-2">
                          <span className="text-[10px] uppercase font-bold text-[var(--accent-color)] block">Raw Message</span>
                          <span className="text-slate-300 break-all">{entry.message}</span>
                        </div>
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
};
