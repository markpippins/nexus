import React from 'react';
import {
  Files,
  Search,
  GitBranch,
  Play,
  Users,
  Terminal,
  Settings,
  Circle,
  Wifi,
  WifiOff,
} from 'lucide-react';
import { SidebarTab } from '../types';

interface ActivityBarProps {
  activeTab: SidebarTab | null;
  setActiveTab: (tab: SidebarTab | null) => void;
  userCount: number;
  isConnected: boolean;
  currentUserColor: string;
  currentUserName: string;
}

export const ActivityBar: React.FC<ActivityBarProps> = ({
  activeTab,
  setActiveTab,
  userCount,
  isConnected,
  currentUserColor,
  currentUserName,
}) => {
  const toggleTab = (tab: SidebarTab) => {
    if (activeTab === tab) {
      setActiveTab(null);
    } else {
      setActiveTab(tab);
    }
  };

  return (
    <div className="w-12 md:w-14 bg-[#333333] dark:bg-[#181818] text-[#cccccc] flex flex-col justify-between items-center py-2 z-20 border-r border-[#2b2b2b] select-none shrink-0">
      <div className="flex flex-col items-center gap-1 w-full">
        {/* VS Code Logo */}
        <div className="mb-3 text-[#007acc] p-1 hover:scale-105 transition-transform cursor-pointer" title="VS Code Studio">
          <svg className="w-6 h-6 fill-current" viewBox="0 0 24 24">
            <path d="M23.15 2.587L18.21.21a1.494 1.494 0 0 0-1.705.29l-9.46 8.63-4.12-3.12a.75.75 0 0 0-.96.06L.25 7.63a.75.75 0 0 0 .02 1.09l4.5 4.1-4.5 4.1a.75.75 0 0 0-.02 1.09l1.71 1.56a.75.75 0 0 0 .96.06l4.12-3.12 9.46 8.63c.48.44 1.18.55 1.705.29l4.94-2.377A1.5 1.5 0 0 0 24 21.843V3.911a1.5 1.5 0 0 0-.85-1.324zM18 17.586l-6.5-5.586L18 6.414v11.172z" />
          </svg>
        </div>

        {/* Action Tabs */}
        <button
          onClick={() => toggleTab('explorer')}
          title="Explorer (Ctrl+Shift+E)"
          className={`w-full py-3 flex justify-center items-center relative transition-colors ${
            activeTab === 'explorer'
              ? 'text-white border-l-2 border-[#007acc] bg-[#2a2d2e]'
              : 'text-[#858585] hover:text-white'
          }`}
        >
          <Files className="w-5 h-5" />
        </button>

        <button
          onClick={() => toggleTab('search')}
          title="Search (Ctrl+Shift+F)"
          className={`w-full py-3 flex justify-center items-center relative transition-colors ${
            activeTab === 'search'
              ? 'text-white border-l-2 border-[#007acc] bg-[#2a2d2e]'
              : 'text-[#858585] hover:text-white'
          }`}
        >
          <Search className="w-5 h-5" />
        </button>

        <button
          onClick={() => toggleTab('git')}
          title="Source Control / History"
          className={`w-full py-3 flex justify-center items-center relative transition-colors ${
            activeTab === 'git'
              ? 'text-white border-l-2 border-[#007acc] bg-[#2a2d2e]'
              : 'text-[#858585] hover:text-white'
          }`}
        >
          <GitBranch className="w-5 h-5" />
        </button>

        <button
          onClick={() => toggleTab('judge0')}
          title="Judge0 Code Execution"
          className={`w-full py-3 flex justify-center items-center relative transition-colors ${
            activeTab === 'judge0'
              ? 'text-white border-l-2 border-[#007acc] bg-[#2a2d2e]'
              : 'text-[#858585] hover:text-white'
          }`}
        >
          <Play className="w-5 h-5 text-emerald-400" />
        </button>

        <button
          onClick={() => toggleTab('collaboration')}
          title="Real-time Collaboration"
          className={`w-full py-3 flex justify-center items-center relative transition-colors ${
            activeTab === 'collaboration'
              ? 'text-white border-l-2 border-[#007acc] bg-[#2a2d2e]'
              : 'text-[#858585] hover:text-white'
          }`}
        >
          <Users className="w-5 h-5" />
          {userCount > 1 && (
            <span className="absolute top-2 right-2 bg-[#007acc] text-white text-[10px] font-bold px-1.5 py-0.2 rounded-full">
              {userCount}
            </span>
          )}
        </button>

        <button
          onClick={() => toggleTab('terminal')}
          title="Integrated Terminal"
          className={`w-full py-3 flex justify-center items-center relative transition-colors ${
            activeTab === 'terminal'
              ? 'text-white border-l-2 border-[#007acc] bg-[#2a2d2e]'
              : 'text-[#858585] hover:text-white'
          }`}
        >
          <Terminal className="w-5 h-5" />
        </button>
      </div>

      <div className="flex flex-col items-center gap-3 w-full mb-1">
        {/* Connection status */}
        <div
          title={isConnected ? 'Connected to WebSocket Sync Server' : 'Connecting to Server...'}
          className="flex items-center justify-center p-1"
        >
          {isConnected ? (
            <Wifi className="w-4 h-4 text-emerald-400 animate-pulse" />
          ) : (
            <WifiOff className="w-4 h-4 text-amber-500" />
          )}
        </div>

        {/* Settings button */}
        <button
          onClick={() => toggleTab('settings')}
          title="Settings"
          className={`w-full py-2.5 flex justify-center items-center transition-colors ${
            activeTab === 'settings'
              ? 'text-white border-l-2 border-[#007acc] bg-[#2a2d2e]'
              : 'text-[#858585] hover:text-white'
          }`}
        >
          <Settings className="w-5 h-5" />
        </button>

        {/* User avatar badge */}
        <div
          onClick={() => toggleTab('collaboration')}
          className="w-7 h-7 rounded-full flex items-center justify-center text-sm font-bold text-white shadow-sm cursor-pointer hover:ring-2 ring-white/50 transition-all mb-1"
          style={{ backgroundColor: currentUserColor }}
          title={`Active User: ${currentUserName}`}
        >
          {currentUserName.substring(0, 2).toUpperCase()}
        </div>
      </div>
    </div>
  );
};
