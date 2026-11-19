import React from 'react';
import Editor, { DiffEditor } from '@monaco-editor/react';
import { X, FileCode, Users, Layers, AlertCircle } from 'lucide-react';
import { FileItem, EditorTheme, UserPresence, VersionSnapshot } from '../types';

interface EditorAreaProps {
  openFiles: FileItem[];
  activeFileId: string | null;
  onSelectTab: (fileId: string) => void;
  onCloseTab: (fileId: string) => void;
  onContentChange: (fileId: string, content: string) => void;
  onCursorChange: (cursor: { line: number; column: number }) => void;
  theme: EditorTheme;
  activeUsers: UserPresence[];
  diffVersion: VersionSnapshot | null;
  onCloseDiff: () => void;
}

export const EditorArea: React.FC<EditorAreaProps> = ({
  openFiles,
  activeFileId,
  onSelectTab,
  onCloseTab,
  onContentChange,
  onCursorChange,
  theme,
  activeUsers,
  diffVersion,
  onCloseDiff,
}) => {
  const activeFile = openFiles.find((f) => f.id === activeFileId);

  // Map theme name to Monaco theme string
  const getMonacoTheme = (t: EditorTheme) => {
    switch (t) {
      case 'vs-light':
      case 'github-light':
        return 'vs';
      case 'hc-black':
        return 'hc-black';
      default:
        return 'vs-dark';
    }
  };

  // Find original version file for diff comparison
  const diffOriginalContent = React.useMemo(() => {
    if (!diffVersion || !activeFile) return '';
    const findInFiles = (items: FileItem[], path: string): FileItem | null => {
      for (const item of items) {
        if (item.type === 'file' && item.path === path) return item;
        if (item.children) {
          const res = findInFiles(item.children, path);
          if (res) return res;
        }
      }
      return null;
    };
    const orig = findInFiles(diffVersion.files, activeFile.path);
    return orig?.content || '';
  }, [diffVersion, activeFile]);

  return (
    <div className="flex-1 flex flex-col h-full bg-[#1e1e1e] overflow-hidden select-none">
      {/* File Tabs Bar */}
      <div className="flex items-center bg-[#252526] text-[#cccccc] text-sm border-b border-[#2d2d2d] overflow-x-auto custom-scrollbar shrink-0">
        {openFiles.map((file) => {
          const isActive = file.id === activeFileId && !diffVersion;
          return (
            <div
              key={file.id}
              onClick={() => {
                if (diffVersion) onCloseDiff();
                onSelectTab(file.id);
              }}
              className={`group flex items-center gap-2 px-3 py-2 cursor-pointer border-r border-[#2d2d2d] min-w-[120px] max-w-[200px] transition-colors shrink-0 ${
                isActive
                  ? 'bg-[#1e1e1e] text-white font-medium border-t-2 border-t-[#007acc]'
                  : 'hover:bg-[#2a2d2e] text-[#969696]'
              }`}
            >
              <FileCode className="w-3.5 h-3.5 text-blue-400 shrink-0" />
              <span className="truncate flex-1">{file.name}</span>
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  onCloseTab(file.id);
                }}
                className="p-0.5 rounded hover:bg-[#383838] opacity-0 group-hover:opacity-100 transition-opacity"
              >
                <X className="w-3 h-3 text-[#aaaaaa] hover:text-white" />
              </button>
            </div>
          );
        })}

        {diffVersion && (
          <div className="flex items-center gap-2 px-3 py-2 bg-[#007acc]/20 text-white font-medium border-r border-[#2d2d2d] shrink-0 border-t-2 border-t-amber-400">
            <Layers className="w-3.5 h-3.5 text-amber-400" />
            <span className="text-sm">Diff vs Hash ({diffVersion.hash})</span>
            <button
              onClick={onCloseDiff}
              className="p-0.5 rounded hover:bg-[#383838]"
              title="Close Diff"
            >
              <X className="w-3 h-3 text-amber-300 hover:text-white" />
            </button>
          </div>
        )}
      </div>

      {/* Editor Active Header Presence Bar */}
      {activeFile && (
        <div className="flex items-center justify-between px-4 py-1.5 bg-[#181818] border-b border-[#2d2d2d] text-sm text-[#858585]">
          <div className="flex items-center gap-2">
            <span className="text-[#007acc] font-mono">{activeFile.path}</span>
            <span className="text-[10px] bg-[#2a2d2e] px-1.5 py-0.2 rounded text-[#aaaaaa]">
              {activeFile.language || 'plaintext'}
            </span>
          </div>

          {/* Connected User Cursors Header */}
          <div className="flex items-center gap-2">
            {activeUsers
              .filter((u) => u.activeFileId === activeFile.id)
              .map((usr) => (
                <div
                  key={usr.id}
                  className="flex items-center gap-1 text-[11px] font-medium px-1.5 py-0.5 rounded border border-[#3c3c3c]"
                  style={{ backgroundColor: `${usr.color}22`, borderColor: usr.color, color: usr.color }}
                >
                  <span className="w-2 h-2 rounded-full" style={{ backgroundColor: usr.color }} />
                  <span>{usr.name}</span>
                  {usr.cursorPosition && (
                    <span className="font-mono text-[10px] opacity-80">
                      (L{usr.cursorPosition.line})
                    </span>
                  )}
                </div>
              ))}
          </div>
        </div>
      )}

      {/* Monaco Editor / Diff Editor Container */}
      <div className="flex-1 relative overflow-hidden">
        {diffVersion && activeFile ? (
          <DiffEditor
            height="100%"
            language={activeFile.language || 'javascript'}
            original={diffOriginalContent}
            modified={activeFile.content || ''}
            theme={getMonacoTheme(theme)}
            options={{
              readOnly: true,
              renderSideBySide: true,
              minimap: { enabled: false },
              fontSize: 13,
              fontFamily: "'Fira Code', 'Cascadia Code', Consolas, monospace",
            }}
          />
        ) : activeFile ? (
          <Editor
            height="100%"
            language={activeFile.language || 'javascript'}
            value={activeFile.content || ''}
            onChange={(val) => onContentChange(activeFile.id, val || '')}
            theme={getMonacoTheme(theme)}
            onMount={(editor) => {
              editor.onDidChangeCursorPosition((e) => {
                onCursorChange({
                  line: e.position.lineNumber,
                  column: e.position.column,
                });
              });
            }}
            options={{
              minimap: { enabled: true },
              fontSize: 13,
              fontFamily: "'Fira Code', 'Cascadia Code', Consolas, monospace",
              automaticLayout: true,
              wordWrap: 'on',
              tabSize: 2,
              scrollBeyondLastLine: false,
              cursorBlinking: 'smooth',
              smoothScrolling: true,
              lineNumbers: 'on',
              glyphMargin: true,
              folding: true,
            }}
          />
        ) : (
          <div className="flex flex-col items-center justify-center h-full text-[#858585] space-y-3 bg-[#1e1e1e]">
            <div className="p-4 rounded-full bg-[#252526] border border-[#333333]">
              <FileCode className="w-10 h-10 text-[#007acc]" />
            </div>
            <p className="text-sm font-medium">No file open in editor</p>
            <p className="text-sm text-[#666666]">
              Select a file from the Explorer sidebar or create a new file
            </p>
          </div>
        )}
      </div>
    </div>
  );
};
