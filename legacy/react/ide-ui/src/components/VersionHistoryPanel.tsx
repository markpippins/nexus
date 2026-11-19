import React, { useState } from 'react';
import { GitCommit, GitPullRequest, RotateCcw, Clock, Plus, Layers, FileDiff } from 'lucide-react';
import { VersionSnapshot, FileItem } from '../types';

interface VersionHistoryPanelProps {
  versions: VersionSnapshot[];
  onSaveSnapshot: (message: string) => void;
  onRestoreSnapshot: (version: VersionSnapshot) => void;
  onCompareDiff: (version: VersionSnapshot) => void;
  activeDiffVersionId: string | null;
}

export const VersionHistoryPanel: React.FC<VersionHistoryPanelProps> = ({
  versions,
  onSaveSnapshot,
  onRestoreSnapshot,
  onCompareDiff,
  activeDiffVersionId,
}) => {
  const [commitMessage, setCommitMessage] = useState('');
  const [isCreating, setIsCreating] = useState(false);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (commitMessage.trim()) {
      onSaveSnapshot(commitMessage.trim());
      setCommitMessage('');
      setIsCreating(false);
    }
  };

  return (
    <div className="flex flex-col h-full bg-[#252526] text-[#cccccc] text-sm">
      <div className="flex items-center justify-between px-3 py-2 border-b border-[#333333] tracking-wide font-semibold text-[11px] uppercase text-[#bbbbbb]">
        <span className="flex items-center gap-1.5">
          <GitCommit className="w-4 h-4 text-[#007acc]" />
          VERSION HISTORY & DIFFS
        </span>
        <button
          onClick={() => setIsCreating(!isCreating)}
          className="p-1 hover:bg-[#333333] rounded text-[#cccccc] hover:text-white"
          title="Create Version Snapshot"
        >
          <Plus className="w-3.5 h-3.5" />
        </button>
      </div>

      {isCreating && (
        <form onSubmit={handleSubmit} className="p-3 border-b border-[#333333] bg-[#1e1e1e]">
          <div className="font-semibold text-white mb-1">Create Version Snapshot</div>
          <input
            type="text"
            value={commitMessage}
            onChange={(e) => setCommitMessage(e.target.value)}
            placeholder="e.g., Added fibonacci algorithm & fixed types"
            autoFocus
            className="w-full bg-[#252526] border border-[#007acc] text-white text-sm px-2 py-1.5 rounded outline-none mb-2"
          />
          <div className="flex justify-end gap-2">
            <button
              type="button"
              onClick={() => setIsCreating(false)}
              className="px-2.5 py-1 bg-[#333333] hover:bg-[#444444] text-white rounded text-sm"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={!commitMessage.trim()}
              className="px-2.5 py-1 bg-[#007acc] hover:bg-[#0062a3] text-white rounded text-sm disabled:opacity-50"
            >
              Commit
            </button>
          </div>
        </form>
      )}

      <div className="flex-1 overflow-y-auto p-2 custom-scrollbar">
        {versions.length === 0 ? (
          <div className="p-4 text-center text-[#858585] italic">
            No version snapshots recorded yet. Click + to commit a snapshot.
          </div>
        ) : (
          versions.map((ver) => {
            const isComparing = ver.id === activeDiffVersionId;
            const formattedDate = new Date(ver.timestamp).toLocaleString(undefined, {
              month: 'short',
              day: 'numeric',
              hour: '2-digit',
              minute: '2-digit',
            });

            return (
              <div
                key={ver.id}
                className={`p-2.5 mb-2 rounded border transition-all ${
                  isComparing
                    ? 'bg-[#1e293b] border-[#007acc] text-white'
                    : 'bg-[#1e1e1e] border-[#333333] hover:border-[#444444]'
                }`}
              >
                <div className="flex items-start justify-between gap-1 mb-1">
                  <span className="font-semibold text-white text-sm leading-snug">
                    {ver.message}
                  </span>
                  <span className="font-mono text-[10px] bg-[#2a2d2e] px-1.5 py-0.5 rounded text-[#007acc] shrink-0">
                    {ver.hash}
                  </span>
                </div>

                <div className="flex items-center justify-between text-[11px] text-[#858585] mb-2">
                  <div className="flex items-center gap-1">
                    <Clock className="w-3 h-3 text-slate-400" />
                    <span>{formattedDate}</span>
                  </div>
                  <span className="text-slate-300">by {ver.author || 'Dev'}</span>
                </div>

                <div className="flex items-center justify-between border-t border-[#2b2b2b] pt-2 mt-1">
                  <button
                    onClick={() => onCompareDiff(ver)}
                    className={`flex items-center gap-1 px-2 py-1 rounded text-[11px] font-medium transition-colors ${
                      isComparing
                        ? 'bg-[#007acc] text-white'
                        : 'bg-[#2a2d2e] hover:bg-[#333333] text-[#cccccc] hover:text-white'
                    }`}
                  >
                    <FileDiff className="w-3.5 h-3.5" />
                    {isComparing ? 'Close Diff' : 'View Diff'}
                  </button>

                  <button
                    onClick={() => onRestoreSnapshot(ver)}
                    title="Restore workspace to this version snapshot"
                    className="flex items-center gap-1 px-2 py-1 bg-amber-500/20 hover:bg-amber-500/30 text-amber-300 rounded text-[11px] font-medium transition-colors"
                  >
                    <RotateCcw className="w-3.5 h-3.5" />
                    Restore
                  </button>
                </div>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
};
