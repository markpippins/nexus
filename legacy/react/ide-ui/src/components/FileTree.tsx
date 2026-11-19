import React, { useState } from 'react';
import {
  Folder,
  FolderOpen,
  FileCode,
  FileText,
  FileJson,
  File,
  Plus,
  FolderPlus,
  Trash2,
  Edit2,
  ChevronRight,
  ChevronDown,
  Download,
} from 'lucide-react';
import { FileItem } from '../types';

interface FileTreeProps {
  files: FileItem[];
  activeFileId: string | null;
  onSelectFile: (file: FileItem) => void;
  onCreateFile: (parentId: string | null, name: string, isFolder: boolean) => void;
  onDeleteFile: (fileId: string) => void;
  onRenameFile: (fileId: string, newName: string) => void;
}

export const FileTree: React.FC<FileTreeProps> = ({
  files,
  activeFileId,
  onSelectFile,
  onCreateFile,
  onDeleteFile,
  onRenameFile,
}) => {
  const [expandedFolders, setExpandedFolders] = useState<Record<string, boolean>>({
    'f-1': true, // src folder expanded by default
  });
  const [creatingItem, setCreatingItem] = useState<{ parentId: string | null; isFolder: boolean } | null>(null);
  const [newItemName, setNewItemName] = useState('');
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState('');

  const toggleFolder = (folderId: string, e: React.MouseEvent) => {
    e.stopPropagation();
    setExpandedFolders((prev) => ({
      ...prev,
      [folderId]: !prev[folderId],
    }));
  };

  const getFileIcon = (fileName: string, type: string) => {
    if (type === 'folder') {
      return null;
    }
    const ext = fileName.split('.').pop()?.toLowerCase();
    switch (ext) {
      case 'ts':
      case 'tsx':
        return <FileCode className="w-4 h-4 text-blue-400 shrink-0" />;
      case 'js':
      case 'jsx':
        return <FileCode className="w-4 h-4 text-yellow-400 shrink-0" />;
      case 'py':
        return <FileCode className="w-4 h-4 text-emerald-400 shrink-0" />;
      case 'cpp':
      case 'c':
      case 'h':
        return <FileCode className="w-4 h-4 text-purple-400 shrink-0" />;
      case 'json':
        return <FileJson className="w-4 h-4 text-amber-300 shrink-0" />;
      case 'html':
      case 'css':
        return <FileCode className="w-4 h-4 text-orange-400 shrink-0" />;
      case 'md':
        return <FileText className="w-4 h-4 text-sky-300 shrink-0" />;
      default:
        return <File className="w-4 h-4 text-slate-400 shrink-0" />;
    }
  };

  const handleCreateSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (newItemName.trim() && creatingItem) {
      onCreateFile(creatingItem.parentId, newItemName.trim(), creatingItem.isFolder);
      if (creatingItem.parentId) {
        setExpandedFolders((prev) => ({ ...prev, [creatingItem.parentId!]: true }));
      }
      setCreatingItem(null);
      setNewItemName('');
    }
  };

  const handleRenameSubmit = (fileId: string, e: React.FormEvent) => {
    e.preventDefault();
    if (renameValue.trim()) {
      onRenameFile(fileId, renameValue.trim());
      setRenamingId(null);
    }
  };

  const renderTree = (items: FileItem[], level = 0) => {
    return items.map((item) => {
      const isFolder = item.type === 'folder';
      const isExpanded = expandedFolders[item.id] || false;
      const isActive = item.id === activeFileId;

      return (
        <div key={item.id} className="select-none">
          <div
            onClick={(e) => {
              if (isFolder) {
                toggleFolder(item.id, e);
              } else {
                onSelectFile(item);
              }
            }}
            style={{ paddingLeft: `${level * 12 + 12}px` }}
            className={`group flex items-center justify-between py-1 pr-2 text-sm cursor-pointer rounded-sm hover:bg-[#2a2d2e] transition-colors ${
              isActive ? 'bg-[#37373d] text-white font-medium' : 'text-[#cccccc]'
            }`}
          >
            <div className="flex items-center gap-1.5 min-w-0 overflow-hidden">
              {isFolder ? (
                <>
                  <span className="text-slate-400 hover:text-white">
                    {isExpanded ? (
                      <ChevronDown className="w-3.5 h-3.5" />
                    ) : (
                      <ChevronRight className="w-3.5 h-3.5" />
                    )}
                  </span>
                  {isExpanded ? (
                    <FolderOpen className="w-4 h-4 text-amber-400 shrink-0" />
                  ) : (
                    <Folder className="w-4 h-4 text-amber-400 shrink-0" />
                  )}
                </>
              ) : (
                <>
                  <span className="w-3.5" />
                  {getFileIcon(item.name, item.type)}
                </>
              )}

              {renamingId === item.id ? (
                <form
                  onSubmit={(e) => handleRenameSubmit(item.id, e)}
                  onClick={(e) => e.stopPropagation()}
                  className="flex-1"
                >
                  <input
                    type="text"
                    value={renameValue}
                    onChange={(e) => setRenameValue(e.target.value)}
                    autoFocus
                    onBlur={() => setRenamingId(null)}
                    className="w-full bg-[#1e1e1e] border border-[#007acc] text-white text-sm px-1 rounded outline-none"
                  />
                </form>
              ) : (
                <span className="truncate">{item.name}</span>
              )}
            </div>

            {/* Quick action buttons on hover */}
            <div className="hidden group-hover:flex items-center gap-1 opacity-80 hover:opacity-100">
              {isFolder && (
                <>
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      setCreatingItem({ parentId: item.id, isFolder: false });
                    }}
                    title="New File in Folder"
                    className="p-0.5 hover:bg-[#383838] rounded text-[#aaaaaa] hover:text-white"
                  >
                    <Plus className="w-3 h-3" />
                  </button>
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      setCreatingItem({ parentId: item.id, isFolder: true });
                    }}
                    title="New Folder in Folder"
                    className="p-0.5 hover:bg-[#383838] rounded text-[#aaaaaa] hover:text-white"
                  >
                    <FolderPlus className="w-3 h-3" />
                  </button>
                </>
              )}
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  setRenamingId(item.id);
                  setRenameValue(item.name);
                }}
                title="Rename"
                className="p-0.5 hover:bg-[#383838] rounded text-[#aaaaaa] hover:text-white"
              >
                <Edit2 className="w-3 h-3" />
              </button>
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  onDeleteFile(item.id);
                }}
                title="Delete"
                className="p-0.5 hover:bg-[#383838] rounded text-red-400 hover:text-red-300"
              >
                <Trash2 className="w-3 h-3" />
              </button>
            </div>
          </div>

          {/* New Item inline input inside expanded folder */}
          {isFolder &&
            isExpanded &&
            creatingItem &&
            creatingItem.parentId === item.id && (
              <div
                style={{ paddingLeft: `${(level + 1) * 12 + 20}px` }}
                className="py-1 pr-2"
              >
                <form onSubmit={handleCreateSubmit} className="flex items-center gap-1">
                  {creatingItem.isFolder ? (
                    <Folder className="w-3.5 h-3.5 text-amber-400 shrink-0" />
                  ) : (
                    <File className="w-3.5 h-3.5 text-slate-400 shrink-0" />
                  )}
                  <input
                    type="text"
                    value={newItemName}
                    onChange={(e) => setNewItemName(e.target.value)}
                    placeholder={creatingItem.isFolder ? 'folder_name' : 'filename.ts'}
                    autoFocus
                    onKeyDown={(e) => {
                      if (e.key === 'Escape') setCreatingItem(null);
                    }}
                    className="w-full bg-[#1e1e1e] border border-[#007acc] text-white text-sm px-1 py-0.5 rounded outline-none"
                  />
                </form>
              </div>
            )}

          {/* Render Children */}
          {isFolder &&
            isExpanded &&
            item.children &&
            item.children.length > 0 &&
            renderTree(item.children, level + 1)}
        </div>
      );
    });
  };

  return (
    <div className="flex flex-col h-full bg-[#252526] text-[#cccccc] text-sm">
      {/* Header bar */}
      <div className="flex items-center justify-between px-3 py-2 border-b border-[#333333] tracking-wide font-semibold text-[11px] uppercase text-[#bbbbbb]">
        <span>EXPLORER</span>
        <div className="flex items-center gap-1">
          <button
            onClick={() => setCreatingItem({ parentId: null, isFolder: false })}
            title="New File at Root"
            className="p-1 hover:bg-[#333333] rounded text-[#cccccc] hover:text-white"
          >
            <Plus className="w-3.5 h-3.5" />
          </button>
          <button
            onClick={() => setCreatingItem({ parentId: null, isFolder: true })}
            title="New Folder at Root"
            className="p-1 hover:bg-[#333333] rounded text-[#cccccc] hover:text-white"
          >
            <FolderPlus className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>

      {/* Root creation input */}
      {creatingItem && creatingItem.parentId === null && (
        <div className="px-3 py-1.5 border-b border-[#333333]">
          <form onSubmit={handleCreateSubmit} className="flex items-center gap-1.5">
            {creatingItem.isFolder ? (
              <Folder className="w-3.5 h-3.5 text-amber-400" />
            ) : (
              <File className="w-3.5 h-3.5 text-slate-400" />
            )}
            <input
              type="text"
              value={newItemName}
              onChange={(e) => setNewItemName(e.target.value)}
              placeholder={creatingItem.isFolder ? 'Folder Name' : 'File Name (e.g. app.js)'}
              autoFocus
              onKeyDown={(e) => {
                if (e.key === 'Escape') setCreatingItem(null);
              }}
              className="w-full bg-[#1e1e1e] border border-[#007acc] text-white text-sm px-1.5 py-0.5 rounded outline-none"
            />
          </form>
        </div>
      )}

      {/* File List */}
      <div className="flex-1 overflow-y-auto py-1 custom-scrollbar">
        {files.length === 0 ? (
          <div className="p-4 text-center text-[#858585] italic">
            No files in workspace. Click + to create a file.
          </div>
        ) : (
          renderTree(files)
        )}
      </div>
    </div>
  );
};
