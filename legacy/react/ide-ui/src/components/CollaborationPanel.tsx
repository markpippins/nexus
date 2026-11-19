import React, { useState } from 'react';
import { Users, Link2, Check, UserCheck, ShieldCheck, Palette, Sparkles, RefreshCw } from 'lucide-react';
import { UserPresence } from '../types';

interface CollaborationPanelProps {
  currentRoom: string;
  onJoinRoom: (roomId: string) => void;
  activeUsers: UserPresence[];
  currentUser: UserPresence;
  onUpdateUser: (name: string, color: string) => void;
  isConnected: boolean;
}

export const CollaborationPanel: React.FC<CollaborationPanelProps> = ({
  currentRoom,
  onJoinRoom,
  activeUsers,
  currentUser,
  onUpdateUser,
  isConnected,
}) => {
  const [roomInput, setRoomInput] = useState(currentRoom);
  const [copied, setCopied] = useState(false);
  const [nameInput, setNameInput] = useState(currentUser.name);
  const [colorInput, setColorInput] = useState(currentUser.color);
  const [isEditingUser, setIsEditingUser] = useState(false);

  const colors = ['#f87171', '#60a5fa', '#34d399', '#fbbf24', '#a78bfa', '#f472b6', '#38bdf8', '#c084fc'];

  const handleCopyLink = () => {
    const url = `${window.location.origin}${window.location.pathname}?room=${currentRoom}`;
    navigator.clipboard.writeText(url);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const handleRoomSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (roomInput.trim()) {
      onJoinRoom(roomInput.trim());
    }
  };

  const handleUserSave = (e: React.FormEvent) => {
    e.preventDefault();
    if (nameInput.trim()) {
      onUpdateUser(nameInput.trim(), colorInput);
      setIsEditingUser(false);
    }
  };

  return (
    <div className="flex flex-col h-full bg-[#252526] text-[#cccccc] text-sm">
      <div className="flex items-center justify-between px-3 py-2 border-b border-[#333333] tracking-wide font-semibold text-[11px] uppercase text-[#bbbbbb]">
        <span className="flex items-center gap-1.5 text-blue-400">
          <Users className="w-4 h-4" />
          REAL-TIME COLLABORATION
        </span>
        <span
          className={`w-2 h-2 rounded-full ${
            isConnected ? 'bg-emerald-400 animate-ping' : 'bg-amber-400'
          }`}
        />
      </div>

      <div className="p-3 border-b border-[#333333] space-y-3 bg-[#1e1e1e]">
        {/* Room Joiner */}
        <form onSubmit={handleRoomSubmit}>
          <label className="block text-[#858585] text-[11px] font-medium mb-1">
            Active Workspace Room ID:
          </label>
          <div className="flex items-center gap-1.5">
            <input
              type="text"
              value={roomInput}
              onChange={(e) => setRoomInput(e.target.value)}
              placeholder="e.g. dev-team-room"
              className="flex-1 bg-[#252526] border border-[#3c3c3c] focus:border-[#007acc] text-white text-sm px-2 py-1.5 rounded outline-none font-mono"
            />
            <button
              type="submit"
              className="px-3 py-1.5 bg-[#007acc] hover:bg-[#0062a3] text-white rounded font-medium transition-colors"
            >
              Join
            </button>
          </div>
        </form>

        {/* Share Room Link */}
        <button
          onClick={handleCopyLink}
          className="w-full py-1.5 bg-[#2a2d2e] hover:bg-[#333333] border border-[#3c3c3c] text-white rounded flex items-center justify-center gap-2 font-medium transition-colors cursor-pointer"
        >
          {copied ? (
            <>
              <Check className="w-4 h-4 text-emerald-400" />
              <span>Link Copied!</span>
            </>
          ) : (
            <>
              <Link2 className="w-4 h-4 text-sky-400" />
              <span>Copy Shareable Invitation Link</span>
            </>
          )}
        </button>
      </div>

      {/* User Profile Editor */}
      <div className="p-3 border-b border-[#333333] bg-[#1e1e1e]">
        <div className="flex items-center justify-between mb-2">
          <span className="text-[11px] font-semibold text-[#858585]">My Developer Presence:</span>
          <button
            onClick={() => setIsEditingUser(!isEditingUser)}
            className="text-[11px] text-[#007acc] hover:underline"
          >
            {isEditingUser ? 'Close' : 'Edit Profile'}
          </button>
        </div>

        {isEditingUser ? (
          <form onSubmit={handleUserSave} className="space-y-2">
            <input
              type="text"
              value={nameInput}
              onChange={(e) => setNameInput(e.target.value)}
              placeholder="Your Name"
              className="w-full bg-[#252526] border border-[#007acc] text-white text-sm px-2 py-1 rounded outline-none"
            />
            <div>
              <div className="text-[10px] text-[#858585] mb-1">Cursor & Avatar Color:</div>
              <div className="flex items-center gap-1.5">
                {colors.map((c) => (
                  <button
                    key={c}
                    type="button"
                    onClick={() => setColorInput(c)}
                    className={`w-5 h-5 rounded-full transition-transform ${
                      colorInput === c ? 'scale-125 ring-2 ring-white' : ''
                    }`}
                    style={{ backgroundColor: c }}
                  />
                ))}
              </div>
            </div>
            <button
              type="submit"
              className="w-full py-1 bg-emerald-600 hover:bg-emerald-500 text-white rounded text-sm font-semibold"
            >
              Save Presence Profile
            </button>
          </form>
        ) : (
          <div className="flex items-center gap-2 bg-[#252526] p-2 rounded border border-[#2d2d2d]">
            <div
              className="w-7 h-7 rounded-full flex items-center justify-center font-bold text-white text-sm shrink-0"
              style={{ backgroundColor: currentUser.color }}
            >
              {currentUser.name.substring(0, 2).toUpperCase()}
            </div>
            <div className="flex-1 min-w-0">
              <div className="font-semibold text-white truncate">{currentUser.name} (You)</div>
              <div className="text-[10px] text-emerald-400 flex items-center gap-1">
                <span className="w-1.5 h-1.5 rounded-full bg-emerald-400" />
                Active in {currentRoom}
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Active Users Roster */}
      <div className="flex-1 overflow-y-auto p-3 space-y-2 custom-scrollbar">
        <div className="text-[11px] font-semibold text-[#858585] uppercase tracking-wider mb-2">
          Connected Developers ({activeUsers.length}):
        </div>

        {activeUsers.map((usr) => (
          <div
            key={usr.id}
            className="flex items-center gap-2.5 p-2 bg-[#1e1e1e] rounded border border-[#2d2d2d] hover:border-[#3c3c3c] transition-all"
          >
            <div
              className="w-6 h-6 rounded-full flex items-center justify-center font-bold text-white text-[11px] shrink-0"
              style={{ backgroundColor: usr.color }}
            >
              {usr.name.substring(0, 2).toUpperCase()}
            </div>

            <div className="flex-1 min-w-0">
              <div className="flex items-center justify-between">
                <span className="font-medium text-white truncate">
                  {usr.name} {usr.id === currentUser.id ? '(You)' : ''}
                </span>
                {usr.cursorPosition && (
                  <span className="text-[10px] font-mono text-[#007acc] bg-[#2a2d2e] px-1 py-0.2 rounded">
                    L{usr.cursorPosition.line}:C{usr.cursorPosition.column}
                  </span>
                )}
              </div>
              <div className="text-[10px] text-[#858585] truncate">
                {usr.activeFileId ? `Editing: ${usr.activeFileId}` : 'Viewing workspace'}
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
};
