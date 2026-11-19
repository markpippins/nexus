import React from 'react';
import {
  GitBranch,
  AlertCircle,
  AlertTriangle,
  Users,
  Play,
  Terminal,
  Sun,
  Moon,
  Menu,
} from 'lucide-react';
import { EditorTheme, UserPresence } from '../types';

interface StatusBarProps {
  cursorPosition: { line: number; column: number };
  language: string;
  theme: EditorTheme;
  onToggleTheme: () => void;
  userCount: number;
  isConnected: boolean;
  onToggleTerminal: () => void;
  onToggleMobileSidebar: () => void;
  currentRoom: string;
}

export const StatusBar: React.FC<StatusBarProps> = ({
  cursorPosition,
  language,
  theme,
  onToggleTheme,
  userCount,
  isConnected,
  onToggleTerminal,
  onToggleMobileSidebar,
  currentRoom,
}) => {
  return (
    <div className="h-6 bg-[#007acc] text-white text-[11px] flex items-center justify-between px-2 select-none z-30 shrink-0 font-sans">
      {/* Left side items */}
      <div className="flex items-center gap-3">
        <button
          onClick={onToggleMobileSidebar}
          className="md:hidden p-0.5 hover:bg-white/20 rounded"
          title="Toggle Sidebar"
        >
          <Menu className="w-3.5 h-3.5" />
        </button>

        <div className="flex items-center gap-1 hover:bg-white/10 px-1 py-0.5 rounded cursor-pointer">
          <GitBranch className="w-3 h-3" />
          <span className="font-semibold">main</span>
        </div>

        <div className="hidden sm:flex items-center gap-2 hover:bg-white/10 px-1 py-0.5 rounded cursor-pointer">
          <span className="flex items-center gap-0.5">
            <AlertCircle className="w-3 h-3" /> 0
          </span>
          <span className="flex items-center gap-0.5">
            <AlertTriangle className="w-3 h-3" /> 0
          </span>
        </div>

        <div className="hidden md:flex items-center gap-1.5 opacity-90">
          <span className="w-1.5 h-1.5 rounded-full bg-emerald-300" />
          <span>Room: {currentRoom}</span>
        </div>
      </div>

      {/* Right side items */}
      <div className="flex items-center gap-3">
        <div className="hover:bg-white/10 px-1 py-0.5 rounded cursor-pointer font-mono">
          Ln {cursorPosition.line}, Col {cursorPosition.column}
        </div>

        <div className="hidden sm:block hover:bg-white/10 px-1 py-0.5 rounded cursor-pointer">
          Spaces: 2
        </div>

        <div className="hidden sm:block hover:bg-white/10 px-1 py-0.5 rounded cursor-pointer">
          UTF-8
        </div>

        <div className="hover:bg-white/10 px-1 py-0.5 rounded cursor-pointer font-medium uppercase">
          {language || 'typescript'}
        </div>

        <button
          onClick={onToggleTerminal}
          className="flex items-center gap-1 hover:bg-white/10 px-1.5 py-0.5 rounded cursor-pointer"
          title="Toggle Terminal"
        >
          <Terminal className="w-3 h-3" />
        </button>

        <div
          className="flex items-center gap-1 hover:bg-white/10 px-1.5 py-0.5 rounded cursor-pointer"
          title={`${userCount} active developers connected`}
        >
          <Users className="w-3 h-3" />
          <span>{userCount}</span>
        </div>

        <button
          onClick={onToggleTheme}
          className="p-1 hover:bg-white/20 rounded cursor-pointer"
          title={`Current Theme: ${theme}. Click to toggle light/dark mode`}
        >
          {theme.includes('light') ? (
            <Sun className="w-3 h-3 text-amber-200" />
          ) : (
            <Moon className="w-3 h-3" />
          )}
        </button>
      </div>
    </div>
  );
};
