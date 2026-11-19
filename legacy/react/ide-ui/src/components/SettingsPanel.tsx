import React from 'react';
import { Settings, Moon, Sun, Palette, Cpu, Database, RefreshCw, Download, HardDrive } from 'lucide-react';
import { EditorTheme } from '../types';

interface SettingsPanelProps {
  theme: EditorTheme;
  setTheme: (t: EditorTheme) => void;
  onExportProject: () => void;
  onResetProject: () => void;
}

export const SettingsPanel: React.FC<SettingsPanelProps> = ({
  theme,
  setTheme,
  onExportProject,
  onResetProject,
}) => {
  const themes: { id: EditorTheme; name: string }[] = [
    { id: 'vs-dark', name: 'VS Code Dark+' },
    { id: 'vs-light', name: 'VS Code Light' },
    { id: 'hc-black', name: 'High Contrast Dark' },
    { id: 'github-light', name: 'GitHub Light' },
  ];

  return (
    <div className="flex flex-col h-full bg-[#252526] text-[#cccccc] text-sm">
      <div className="flex items-center px-3 py-2 border-b border-[#333333] tracking-wide font-semibold text-[11px] uppercase text-[#bbbbbb]">
        <span className="flex items-center gap-1.5">
          <Settings className="w-4 h-4 text-[#007acc]" />
          STUDIO SETTINGS
        </span>
      </div>

      <div className="flex-1 overflow-y-auto p-3 space-y-4 custom-scrollbar">
        {/* Editor Theme Section */}
        <div className="bg-[#1e1e1e] p-3 rounded border border-[#333333] space-y-2">
          <div className="flex items-center gap-2 font-semibold text-white text-sm mb-1">
            <Palette className="w-4 h-4 text-purple-400" />
            <span>Monaco Syntax Highlight Theme</span>
          </div>

          <div className="grid grid-cols-1 gap-1.5">
            {themes.map((t) => (
              <button
                key={t.id}
                onClick={() => setTheme(t.id)}
                className={`flex items-center justify-between p-2 rounded text-sm font-medium transition-colors ${
                  theme === t.id
                    ? 'bg-[#007acc] text-white'
                    : 'bg-[#252526] hover:bg-[#2d2d2d] text-[#cccccc]'
                }`}
              >
                <span>{t.name}</span>
                {theme === t.id && <span className="text-[10px] font-bold">ACTIVE</span>}
              </button>
            ))}
          </div>
        </div>

        {/* Cloud Persistence Section */}
        <div className="bg-[#1e1e1e] p-3 rounded border border-[#333333] space-y-2">
          <div className="flex items-center gap-2 font-semibold text-white text-sm mb-1">
            <HardDrive className="w-4 h-4 text-emerald-400" />
            <span>Cloud Project Persistence</span>
          </div>
          <p className="text-[11px] text-[#858585] leading-relaxed">
            Your project files are automatically synchronized to persistent cloud storage in{' '}
            <code className="text-emerald-300">.data/projects.json</code> and accessible across sessions.
          </p>

          <button
            onClick={onExportProject}
            className="w-full py-1.5 bg-[#2b2b2b] hover:bg-[#383838] border border-[#3c3c3c] text-white rounded font-medium flex items-center justify-center gap-2 transition-colors"
          >
            <Download className="w-4 h-4 text-sky-400" />
            Export Project (JSON Backup)
          </button>
        </div>

        {/* Reset Workspace Section */}
        <div className="bg-[#1e1e1e] p-3 rounded border border-[#333333] space-y-2">
          <div className="flex items-center gap-2 font-semibold text-white text-sm mb-1">
            <RefreshCw className="w-4 h-4 text-amber-400" />
            <span>Workspace Reset</span>
          </div>
          <p className="text-[11px] text-[#858585]">
            Reset project files to default starter templates (TypeScript, Python, C++, HTML).
          </p>

          <button
            onClick={onResetProject}
            className="w-full py-1.5 bg-amber-500/20 hover:bg-amber-500/30 text-amber-300 border border-amber-500/30 rounded font-medium transition-colors"
          >
            Reset Starter Files
          </button>
        </div>
      </div>
    </div>
  );
};
