import React, { useState, useRef, useEffect } from 'react';
import {
  Terminal as TerminalIcon,
  Play,
  Trash2,
  Maximize2,
  Minimize2,
  X,
  ChevronRight,
  CheckCircle2,
  AlertTriangle,
} from 'lucide-react';
import { TerminalLog, Judge0Result } from '../types';

interface TerminalPanelProps {
  logs: TerminalLog[];
  onClearLogs: () => void;
  onExecuteCode: () => void;
  lastExecutionResult: Judge0Result | null;
  onRunCommand: (command: string) => void;
  isOpen: boolean;
  onClose: () => void;
}

export const TerminalPanel: React.FC<TerminalPanelProps> = ({
  logs,
  onClearLogs,
  onExecuteCode,
  lastExecutionResult,
  onRunCommand,
  isOpen,
  onClose,
}) => {
  const [activeTab, setActiveTab] = useState<'terminal' | 'output' | 'problems'>('terminal');
  const [commandInput, setCommandInput] = useState('');
  const [commandHistory, setCommandHistory] = useState<string[]>([]);
  const [historyIndex, setHistoryIndex] = useState(-1);
  const [isMaximized, setIsMaximized] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [logs, lastExecutionResult]);

  if (!isOpen) return null;

  const handleCommandSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (commandInput.trim()) {
      const cmd = commandInput.trim();
      setCommandHistory((prev) => [...prev, cmd]);
      setHistoryIndex(-1);
      onRunCommand(cmd);
      setCommandInput('');
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'ArrowUp') {
      e.preventDefault();
      if (commandHistory.length > 0) {
        const nextIdx = historyIndex < commandHistory.length - 1 ? historyIndex + 1 : historyIndex;
        setHistoryIndex(nextIdx);
        setCommandInput(commandHistory[commandHistory.length - 1 - nextIdx] || '');
      }
    } else if (e.key === 'ArrowDown') {
      e.preventDefault();
      if (historyIndex > 0) {
        const nextIdx = historyIndex - 1;
        setHistoryIndex(nextIdx);
        setCommandInput(commandHistory[commandHistory.length - 1 - nextIdx] || '');
      } else if (historyIndex === 0) {
        setHistoryIndex(-1);
        setCommandInput('');
      }
    }
  };

  return (
    <div
      className={`bg-[#1e1e1e] border-t border-[#333333] flex flex-col transition-all z-20 ${
        isMaximized ? 'h-full' : 'h-64 md:h-72'
      }`}
    >
      {/* Terminal Header */}
      <div className="flex items-center justify-between px-3 py-1.5 bg-[#252526] border-b border-[#2d2d2d] text-sm text-[#cccccc] select-none shrink-0">
        <div className="flex items-center gap-4">
          <button
            onClick={() => setActiveTab('terminal')}
            className={`flex items-center gap-1.5 font-medium py-1 px-2 border-b-2 transition-colors ${
              activeTab === 'terminal'
                ? 'border-[#007acc] text-white'
                : 'border-transparent text-[#858585] hover:text-white'
            }`}
          >
            <TerminalIcon className="w-3.5 h-3.5" />
            <span>TERMINAL</span>
          </button>

          <button
            onClick={() => setActiveTab('output')}
            className={`flex items-center gap-1.5 font-medium py-1 px-2 border-b-2 transition-colors ${
              activeTab === 'output'
                ? 'border-[#007acc] text-white'
                : 'border-transparent text-[#858585] hover:text-white'
            }`}
          >
            <Play className="w-3.5 h-3.5 text-emerald-400" />
            <span>JUDGE0 OUTPUT</span>
            {lastExecutionResult && (
              <span className="w-2 h-2 rounded-full bg-emerald-400" />
            )}
          </button>
        </div>

        <div className="flex items-center gap-2">
          <button
            onClick={onExecuteCode}
            title="Run Code on Judge0 (Ctrl+Enter)"
            className="flex items-center gap-1 px-2 py-0.5 bg-emerald-600 hover:bg-emerald-500 text-white rounded text-sm font-medium cursor-pointer"
          >
            <Play className="w-3 h-3 fill-current" />
            <span>Run Code</span>
          </button>

          <button
            onClick={onClearLogs}
            title="Clear Terminal Output"
            className="p-1 hover:bg-[#333333] rounded text-[#858585] hover:text-white"
          >
            <Trash2 className="w-3.5 h-3.5" />
          </button>

          <button
            onClick={() => setIsMaximized(!isMaximized)}
            title={isMaximized ? 'Restore Down' : 'Maximize Panel'}
            className="p-1 hover:bg-[#333333] rounded text-[#858585] hover:text-white"
          >
            {isMaximized ? (
              <Minimize2 className="w-3.5 h-3.5" />
            ) : (
              <Maximize2 className="w-3.5 h-3.5" />
            )}
          </button>

          <button
            onClick={onClose}
            title="Close Panel"
            className="p-1 hover:bg-[#333333] rounded text-[#858585] hover:text-white"
          >
            <X className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>

      {/* Terminal Tab Content */}
      {activeTab === 'terminal' && (
        <div className="flex-1 overflow-y-auto p-3 font-mono text-sm bg-[#181818] text-[#cccccc] space-y-1.5 custom-scrollbar">
          {logs.map((log) => (
            <div key={log.id} className="flex items-start gap-2">
              <span className="text-[#666666] shrink-0 text-[10px] select-none">
                [{log.timestamp}]
              </span>
              <span
                className={`whitespace-pre-wrap break-all ${
                  log.type === 'command'
                    ? 'text-sky-300 font-semibold'
                    : log.type === 'error'
                    ? 'text-red-400'
                    : log.type === 'success'
                    ? 'text-emerald-300'
                    : log.type === 'system'
                    ? 'text-amber-300'
                    : 'text-[#cccccc]'
                }`}
              >
                {log.text}
              </span>
            </div>
          ))}

          {/* Input Prompt */}
          <form onSubmit={handleCommandSubmit} className="flex items-center gap-2 pt-1">
            <span className="text-emerald-400 font-bold flex items-center">
              dev@vscode-studio:~$ <ChevronRight className="w-3.5 h-3.5 text-blue-400 ml-1" />
            </span>
            <input
              type="text"
              value={commandInput}
              onChange={(e) => setCommandInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="Type command (help, run, ls, cat, theme, clear)..."
              className="flex-1 bg-transparent text-white font-mono text-sm outline-none border-none"
            />
          </form>
          <div ref={bottomRef} />
        </div>
      )}

      {/* Judge0 Output Tab Content */}
      {activeTab === 'output' && (
        <div className="flex-1 overflow-y-auto p-3 font-mono text-sm bg-[#181818] text-[#cccccc] custom-scrollbar">
          {lastExecutionResult ? (
            <div className="space-y-2">
              <div className="flex items-center justify-between pb-2 border-b border-[#2d2d2d]">
                <div className="flex items-center gap-2">
                  <CheckCircle2 className="w-4 h-4 text-emerald-400" />
                  <span className="font-bold text-white">
                    Status: {lastExecutionResult.status.description}
                  </span>
                </div>
                <div className="text-[10px] text-[#858585] flex gap-3">
                  <span>Time: {lastExecutionResult.time}</span>
                  <span>
                    Memory:{' '}
                    {lastExecutionResult.memory
                      ? `${(lastExecutionResult.memory / 1024).toFixed(1)} MB`
                      : 'N/A'}
                  </span>
                </div>
              </div>

              <div>
                <div className="text-[11px] font-semibold text-[#858585] mb-1">STDOUT:</div>
                <pre className="bg-[#111111] p-3 rounded border border-[#2d2d2d] text-emerald-300 whitespace-pre-wrap">
                  {lastExecutionResult.stdout || '(No output)'}
                </pre>
              </div>

              {lastExecutionResult.stderr && (
                <div>
                  <div className="text-[11px] font-semibold text-red-400 mb-1">STDERR:</div>
                  <pre className="bg-[#111111] p-3 rounded border border-[#2d2d2d] text-red-300 whitespace-pre-wrap">
                    {lastExecutionResult.stderr}
                  </pre>
                </div>
              )}
            </div>
          ) : (
            <div className="text-center p-8 text-[#858585]">
              No Judge0 code execution output available yet. Click <strong>Run Code</strong> above.
            </div>
          )}
        </div>
      )}
    </div>
  );
};
