import React, { useState } from 'react';
import { Play, Terminal, Cpu, Clock, HardDrive, CheckCircle2, AlertTriangle, Settings2 } from 'lucide-react';
import { JUDGE0_LANGUAGES } from '../data/defaultProject';
import { Judge0Result } from '../types';

interface Judge0PanelProps {
  selectedLanguageId: number;
  setSelectedLanguageId: (id: number) => void;
  stdin: string;
  setStdin: (val: string) => void;
  onExecute: () => void;
  isExecuting: boolean;
  lastResult: Judge0Result | null;
  activeFileName: string;
}

export const Judge0Panel: React.FC<Judge0PanelProps> = ({
  selectedLanguageId,
  setSelectedLanguageId,
  stdin,
  setStdin,
  onExecute,
  isExecuting,
  lastResult,
  activeFileName,
}) => {
  const [cpuLimit, setCpuLimit] = useState(5);

  const selectedLang = JUDGE0_LANGUAGES.find((l) => l.id === selectedLanguageId) || JUDGE0_LANGUAGES[0];

  return (
    <div className="flex flex-col h-full bg-[#252526] text-[#cccccc] text-sm">
      <div className="flex items-center justify-between px-3 py-2 border-b border-[#333333] tracking-wide font-semibold text-[11px] uppercase text-[#bbbbbb]">
        <span className="flex items-center gap-1.5 text-emerald-400">
          <Play className="w-4 h-4" />
          JUDGE0 EXECUTION ENGINE
        </span>
        <span className="bg-emerald-500/20 text-emerald-300 text-[10px] font-mono px-1.5 py-0.5 rounded">
          CE v1.13.1
        </span>
      </div>

      <div className="p-3 border-b border-[#333333] space-y-3 bg-[#1e1e1e]">
        {/* Active File Context */}
        <div className="flex items-center justify-between text-sm">
          <span className="text-[#858585]">Target File:</span>
          <span className="font-mono text-white bg-[#2d2d2d] px-2 py-0.5 rounded border border-[#3c3c3c]">
            {activeFileName || 'No file selected'}
          </span>
        </div>

        {/* Language selector */}
        <div>
          <label className="block text-[#858585] text-[11px] font-medium mb-1">
            Target Language Runtime:
          </label>
          <select
            value={selectedLanguageId}
            onChange={(e) => setSelectedLanguageId(Number(e.target.value))}
            className="w-full bg-[#252526] border border-[#3c3c3c] focus:border-[#007acc] text-white text-sm px-2 py-1.5 rounded outline-none"
          >
            {JUDGE0_LANGUAGES.map((lang) => (
              <option key={lang.id} value={lang.id}>
                {lang.name}
              </option>
            ))}
          </select>
        </div>

        {/* Standard Input (stdin) */}
        <div>
          <label className="block text-[#858585] text-[11px] font-medium mb-1">
            Standard Input (stdin):
          </label>
          <textarea
            value={stdin}
            onChange={(e) => setStdin(e.target.value)}
            placeholder="Pass custom runtime input parameters here..."
            rows={3}
            className="w-full bg-[#252526] border border-[#3c3c3c] focus:border-[#007acc] text-white font-mono text-sm p-2 rounded outline-none resize-none"
          />
        </div>

        {/* Run Code Button */}
        <button
          onClick={onExecute}
          disabled={isExecuting}
          className="w-full py-2 bg-emerald-600 hover:bg-emerald-500 text-white font-semibold rounded flex items-center justify-center gap-2 shadow-md transition-colors disabled:opacity-50 cursor-pointer"
        >
          <Play className="w-4 h-4 fill-current" />
          {isExecuting ? 'Executing Code...' : 'Run Code (Ctrl+Enter)'}
        </button>
      </div>

      {/* Execution Results Summary */}
      <div className="flex-1 overflow-y-auto p-3 space-y-3 custom-scrollbar">
        {lastResult ? (
          <div className="space-y-3">
            <div className="flex items-center justify-between bg-[#1e1e1e] p-2.5 rounded border border-[#333333]">
              <div className="flex items-center gap-2">
                {lastResult.status.id === 3 ? (
                  <CheckCircle2 className="w-4 h-4 text-emerald-400" />
                ) : (
                  <AlertTriangle className="w-4 h-4 text-amber-400" />
                )}
                <span className="font-semibold text-white">
                  {lastResult.status.description}
                </span>
              </div>
              {lastResult.isFallback && (
                <span className="text-[10px] bg-amber-500/20 text-amber-300 px-1.5 py-0.5 rounded">
                  Sandbox Engine
                </span>
              )}
            </div>

            <div className="grid grid-cols-2 gap-2 text-sm">
              <div className="bg-[#1e1e1e] p-2 rounded border border-[#333333] flex items-center gap-2">
                <Clock className="w-4 h-4 text-blue-400" />
                <div>
                  <div className="text-[10px] text-[#858585]">Cpu Time</div>
                  <div className="font-mono text-white font-semibold">
                    {lastResult.time || '0.02s'}
                  </div>
                </div>
              </div>

              <div className="bg-[#1e1e1e] p-2 rounded border border-[#333333] flex items-center gap-2">
                <HardDrive className="w-4 h-4 text-purple-400" />
                <div>
                  <div className="text-[10px] text-[#858585]">Memory Used</div>
                  <div className="font-mono text-white font-semibold">
                    {lastResult.memory ? `${(lastResult.memory / 1024).toFixed(1)} MB` : '14.2 MB'}
                  </div>
                </div>
              </div>
            </div>

            {/* stdout preview */}
            <div>
              <div className="text-[11px] font-semibold text-[#858585] mb-1">
                STDOUT Output:
              </div>
              <pre className="font-mono text-sm bg-[#1e1e1e] p-2.5 rounded border border-[#333333] text-emerald-300 max-h-48 overflow-y-auto whitespace-pre-wrap">
                {lastResult.stdout || '(Empty output)'}
              </pre>
            </div>

            {/* stderr preview if exists */}
            {lastResult.stderr && (
              <div>
                <div className="text-[11px] font-semibold text-red-400 mb-1">
                  STDERR Errors:
                </div>
                <pre className="font-mono text-sm bg-[#1e1e1e] p-2.5 rounded border border-[#333333] text-red-300 max-h-48 overflow-y-auto whitespace-pre-wrap">
                  {lastResult.stderr}
                </pre>
              </div>
            )}
          </div>
        ) : (
          <div className="text-center p-6 text-[#858585] space-y-2">
            <Cpu className="w-8 h-8 mx-auto text-[#444444]" />
            <p className="text-sm">
              Select target runtime and click <strong>Run Code</strong> or press{' '}
              <kbd className="bg-[#333333] px-1 py-0.5 rounded text-white font-mono text-[10px]">
                Ctrl+Enter
              </kbd>
            </p>
          </div>
        )}
      </div>
    </div>
  );
};
