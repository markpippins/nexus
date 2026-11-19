import React from 'react';
import { SidebarTab, FileItem, VersionSnapshot, Judge0Result, UserPresence, EditorTheme } from '../types';
import { FileTree } from './FileTree';
import { SearchPanel } from './SearchPanel';
import { VersionHistoryPanel } from './VersionHistoryPanel';
import { Judge0Panel } from './Judge0Panel';
import { CollaborationPanel } from './CollaborationPanel';
import { SettingsPanel } from './SettingsPanel';

interface SidebarProps {
  activeTab: SidebarTab | null;
  files: FileItem[];
  activeFileId: string | null;
  onSelectFile: (file: FileItem) => void;
  onCreateFile: (parentId: string | null, name: string, isFolder: boolean) => void;
  onDeleteFile: (fileId: string) => void;
  onRenameFile: (fileId: string, newName: string) => void;
  versions: VersionSnapshot[];
  onSaveSnapshot: (message: string) => void;
  onRestoreSnapshot: (version: VersionSnapshot) => void;
  onCompareDiff: (version: VersionSnapshot) => void;
  activeDiffVersionId: string | null;
  selectedLanguageId: number;
  setSelectedLanguageId: (id: number) => void;
  stdin: string;
  setStdin: (val: string) => void;
  onExecute: () => void;
  isExecuting: boolean;
  lastResult: Judge0Result | null;
  activeFileName: string;
  currentRoom: string;
  onJoinRoom: (roomId: string) => void;
  activeUsers: UserPresence[];
  currentUser: UserPresence;
  onUpdateUser: (name: string, color: string) => void;
  isConnected: boolean;
  theme: EditorTheme;
  setTheme: (t: EditorTheme) => void;
  onExportProject: () => void;
  onResetProject: () => void;
}

export const Sidebar: React.FC<SidebarProps> = ({
  activeTab,
  files,
  activeFileId,
  onSelectFile,
  onCreateFile,
  onDeleteFile,
  onRenameFile,
  versions,
  onSaveSnapshot,
  onRestoreSnapshot,
  onCompareDiff,
  activeDiffVersionId,
  selectedLanguageId,
  setSelectedLanguageId,
  stdin,
  setStdin,
  onExecute,
  isExecuting,
  lastResult,
  activeFileName,
  currentRoom,
  onJoinRoom,
  activeUsers,
  currentUser,
  onUpdateUser,
  isConnected,
  theme,
  setTheme,
  onExportProject,
  onResetProject,
}) => {
  if (!activeTab || activeTab === 'terminal') return null;

  return (
    <div className="w-64 sm:w-72 md:w-80 h-full bg-[#252526] border-r border-[#2d2d2d] flex flex-col z-10 shrink-0 select-none overflow-hidden">
      {activeTab === 'explorer' && (
        <FileTree
          files={files}
          activeFileId={activeFileId}
          onSelectFile={onSelectFile}
          onCreateFile={onCreateFile}
          onDeleteFile={onDeleteFile}
          onRenameFile={onRenameFile}
        />
      )}

      {activeTab === 'search' && (
        <SearchPanel files={files} onSelectFile={onSelectFile} />
      )}

      {activeTab === 'git' && (
        <VersionHistoryPanel
          versions={versions}
          onSaveSnapshot={onSaveSnapshot}
          onRestoreSnapshot={onRestoreSnapshot}
          onCompareDiff={onCompareDiff}
          activeDiffVersionId={activeDiffVersionId}
        />
      )}

      {activeTab === 'judge0' && (
        <Judge0Panel
          selectedLanguageId={selectedLanguageId}
          setSelectedLanguageId={setSelectedLanguageId}
          stdin={stdin}
          setStdin={setStdin}
          onExecute={onExecute}
          isExecuting={isExecuting}
          lastResult={lastResult}
          activeFileName={activeFileName}
        />
      )}

      {activeTab === 'collaboration' && (
        <CollaborationPanel
          currentRoom={currentRoom}
          onJoinRoom={onJoinRoom}
          activeUsers={activeUsers}
          currentUser={currentUser}
          onUpdateUser={onUpdateUser}
          isConnected={isConnected}
        />
      )}

      {activeTab === 'settings' && (
        <SettingsPanel
          theme={theme}
          setTheme={setTheme}
          onExportProject={onExportProject}
          onResetProject={onResetProject}
        />
      )}
    </div>
  );
};
