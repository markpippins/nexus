import React from 'react';
import {
  Layers,
  Server,
  BookOpen,
  Terminal,
  ShieldAlert,
  Play,
  FileText,
  Activity,
  X
} from 'lucide-react';

interface SidebarProps {
  currentTab: string;
  setCurrentTab: (tab: string) => void;
  isOpenMobile?: boolean;
  onCloseMobile?: () => void;
}

export const Sidebar: React.FC<SidebarProps> = ({
  currentTab,
  setCurrentTab,
  isOpenMobile = false,
  onCloseMobile
}) => {
  const navGroups = [
    {
      title: 'Configuration',
      items: [
        { id: 'registry', label: 'AI Registry', icon: Server },
        { id: 'overview', label: 'Resolver & Overview', icon: Layers },
      ]
    },
    {
      title: 'Role Registry',
      items: [
        { id: 'roles-tasks', label: 'Roles & Prompts', icon: BookOpen },
        { id: 'memory', label: 'Memory & Procedures', icon: Terminal },
      ]
    },
    {
      title: 'Runtime & Resilience',
      items: [
        { id: 'circuit-sched', label: 'Circuit & Scheduler', icon: ShieldAlert },
        { id: 'tasks', label: 'Task Registry', icon: FileText },
        { id: 'sessions-playground', label: 'Sessions & Playground', icon: Play },
        { id: 'system-logs', label: 'System Logs', icon: FileText },
        { id: 'audit-trail', label: 'Audit Trail', icon: FileText },
        { id: 'system-insights', label: 'System Insights (D3)', icon: Activity },
      ]
    }
  ];

  const handleSelect = (tabId: string) => {
    setCurrentTab(tabId);
    if (onCloseMobile) {
      onCloseMobile();
    }
  };

  const navContent = (
    <div className="flex flex-col h-full bg-[var(--bg-tertiary)] border-r border-[var(--border-color)] p-4 select-none">
      {/* Mobile Header Close Button */}
      <div className="flex items-center justify-between lg:hidden mb-4 pb-2 border-b border-[var(--border-subtle)]">
        <span className="text-sm font-bold uppercase tracking-wider text-[var(--text-muted)]">Navigation</span>
        <button
          onClick={onCloseMobile}
          className="p-1 rounded text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-hover)]"
        >
          <X className="w-5 h-5" />
        </button>
      </div>

      <div className="flex-1 space-y-6 overflow-y-auto pr-1">
        {navGroups.map((group, idx) => (
          <div key={idx}>
            <p className="text-[10px] uppercase tracking-widest text-[var(--text-muted)] font-bold mb-3 px-2">
              {group.title}
            </p>
            <ul className="space-y-1">
              {group.items.map(item => {
                const Icon = item.icon;
                const isActive = currentTab === item.id;
                return (
                  <li key={item.id}>
                    <button
                      onClick={() => handleSelect(item.id)}
                      className={`w-full flex items-center px-3 py-2 text-sm font-medium rounded-md transition-all duration-150 cursor-pointer ${
                        isActive
                          ? 'bg-[var(--badge-bg)] text-[var(--accent-color)] border border-[var(--accent-color)]/30 font-semibold shadow-xs'
                          : 'text-[var(--text-secondary)] hover:bg-[var(--bg-hover)] hover:text-[var(--text-primary)]'
                      }`}
                    >
                      <Icon className={`w-4 h-4 mr-3 shrink-0 ${isActive ? 'text-[var(--accent-color)]' : 'text-[var(--text-muted)]'}`} />
                      <span className="truncate">{item.label}</span>
                    </button>
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
      </div>

      {/* Footer System Info */}
      <div className="mt-auto border-t border-[var(--border-color)] pt-4">
        <div className="flex items-center justify-between text-[11px] text-[var(--text-muted)] font-mono">
          <span>VER 3.4.10</span>
          <span className="text-[var(--accent-color)] font-semibold flex items-center gap-1">
            <span className="w-1.5 h-1.5 rounded-full bg-emerald-400" />
            STABLE
          </span>
        </div>
      </div>
    </div>
  );

  return (
    <>
      {/* Desktop Sidebar */}
      <aside className="hidden lg:block w-64 shrink-0 h-[calc(100vh-3.5rem)] sticky top-14">
        {navContent}
      </aside>

      {/* Mobile Drawer Overlay */}
      {isOpenMobile && (
        <div className="fixed inset-0 z-50 lg:hidden flex">
          <div
            className="fixed inset-0 bg-slate-950/70 backdrop-blur-xs transition-opacity"
            onClick={onCloseMobile}
          />
          <div className="relative w-64 max-w-xs h-full bg-[var(--bg-tertiary)] z-10 shadow-xl">
            {navContent}
          </div>
        </div>
      )}
    </>
  );
};
