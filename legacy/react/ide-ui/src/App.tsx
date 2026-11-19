import React, { useState, useEffect, useCallback } from 'react';
import {
  SidebarTab,
  FileItem,
  Project,
  VersionSnapshot,
  Judge0Result,
  UserPresence,
  EditorTheme,
  TerminalLog,
} from './types';
import { DEFAULT_PROJECT, JUDGE0_LANGUAGES } from './data/defaultProject';
import { ActivityBar } from './components/ActivityBar';
import { Sidebar } from './components/Sidebar';
import { EditorArea } from './components/EditorArea';
import { TerminalPanel } from './components/TerminalPanel';
import { StatusBar } from './components/StatusBar';
import { realtimeSocket } from './services/websocket';
import { executeCode } from './services/judge0';
import { Play, Code2, Users, Menu, Sparkles } from 'lucide-react';

export default function App() {
  // Project & File System State
  const [project, setProject] = useState<Project>(DEFAULT_PROJECT);
  const [openFileIds, setOpenFileIds] = useState<string[]>(['f-1-1', 'f-1-2']);
  const [activeFileId, setActiveFileId] = useState<string | null>('f-1-1');

  // UI Panels State
  const [activeTab, setActiveTab] = useState<SidebarTab | null>('explorer');
  const [isTerminalOpen, setIsTerminalOpen] = useState(true);
  const [theme, setTheme] = useState<EditorTheme>('vs-dark');
  const [isMobileSidebarOpen, setIsMobileSidebarOpen] = useState(false);

  // Judge0 State
  const [selectedLanguageId, setSelectedLanguageId] = useState<number>(74); // TS default
  const [stdin, setStdin] = useState<string>('');
  const [isExecuting, setIsExecuting] = useState<boolean>(false);
  const [lastResult, setLastResult] = useState<Judge0Result | null>(null);

  // Collaboration State
  const [currentRoom, setCurrentRoom] = useState<string>('default-workspace');
  const [activeUsers, setActiveUsers] = useState<UserPresence[]>([]);
  const [isConnected, setIsConnected] = useState<boolean>(false);
  const currentUser = realtimeSocket.getCurrentUser();

  // Version Control & Diffs
  const [versions, setVersions] = useState<VersionSnapshot[]>([]);
  const [activeDiffVersion, setActiveDiffVersion] = useState<VersionSnapshot | null>(null);

  // Terminal Logs
  const [terminalLogs, setTerminalLogs] = useState<TerminalLog[]>([
    {
      id: 'l-1',
      text: '🚀 VS Code Collaborative Studio Initialized [Node.js v20.x, Judge0 CE v1.13.1]',
      type: 'system',
      timestamp: new Date().toLocaleTimeString(),
    },
    {
      id: 'l-2',
      text: 'WebSocket Sync Server ready. Type "help" in terminal for available shell commands.',
      type: 'info',
      timestamp: new Date().toLocaleTimeString(),
    },
  ]);

  // Load project & room from query params or backend
  useEffect(() => {
    // Check URL room param
    const params = new URLSearchParams(window.location.search);
    const roomParam = params.get('room');
    if (roomParam) {
      setCurrentRoom(roomParam);
    }

    // Connect WebSocket
    realtimeSocket.connect(roomParam || 'default-workspace');

    // Fetch cloud project state from server
    fetch('/api/projects')
      .then((res) => res.json())
      .then((data: Project[]) => {
        if (Array.isArray(data) && data.length > 0) {
          setProject(data[0]);
        } else {
          // Save default project to cloud storage
          fetch('/api/projects', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(DEFAULT_PROJECT),
          });
        }
      })
      .catch(() => {
        /* fallback to default project */
      });

    // Fetch initial version history
    fetch(`/api/projects/${DEFAULT_PROJECT.id}/versions`)
      .then((res) => res.json())
      .then((data: VersionSnapshot[]) => {
        if (Array.isArray(data)) setVersions(data);
      })
      .catch(() => {});
  }, []);

  // Listen for WebSocket Events
  useEffect(() => {
    const unsubRoom = realtimeSocket.on('room-state', (msg) => {
      setIsConnected(true);
      if (msg.users) setActiveUsers(msg.users);
    });

    const unsubJoined = realtimeSocket.on('user-joined', (msg) => {
      if (msg.users) setActiveUsers(msg.users);
      if (msg.user && msg.user.id !== currentUser.id) {
        addLog(`User ${msg.user.name} joined room ${currentRoom}`, 'info');
      }
    });

    const unsubLeft = realtimeSocket.on('user-left', (msg) => {
      if (msg.users) setActiveUsers(msg.users);
      if (msg.user) {
        addLog(`User ${msg.user.name} left room`, 'info');
      }
    });

    const unsubFileUpdate = realtimeSocket.on('file-updated', (msg) => {
      if (msg.fileId && msg.content !== undefined && msg.senderId !== currentUser.id) {
        updateFileContentLocally(msg.fileId, msg.content);
      }
    });

    const unsubCursorUpdate = realtimeSocket.on('cursor-updated', (msg) => {
      if (msg.user) {
        setActiveUsers((prev) => {
          const idx = prev.findIndex((u) => u.id === msg.user!.id);
          if (idx >= 0) {
            const updated = [...prev];
            updated[idx] = msg.user!;
            return updated;
          }
          return [...prev, msg.user!];
        });
      }
    });

    const unsubVersion = realtimeSocket.on('version-added', (msg) => {
      if (msg.version) {
        setVersions((prev) => [msg.version!, ...prev]);
        addLog(`New version snapshot committed by ${msg.version.author}: "${msg.version.message}"`, 'system');
      }
    });

    return () => {
      unsubRoom();
      unsubJoined();
      unsubLeft();
      unsubFileUpdate();
      unsubCursorUpdate();
      unsubVersion();
    };
  }, [currentUser.id, currentRoom]);

  // Terminal logging helper
  const addLog = (text: string, type: 'info' | 'error' | 'success' | 'command' | 'system' = 'info') => {
    setTerminalLogs((prev) => [
      ...prev,
      {
        id: 'l-' + Date.now() + Math.random(),
        text,
        type,
        timestamp: new Date().toLocaleTimeString(),
      },
    ]);
  };

  // Cloud Persistence Sync
  const saveProjectToCloud = useCallback((updatedProject: Project) => {
    fetch('/api/projects', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(updatedProject),
    }).catch((e) => console.warn('Failed to persist project to cloud:', e));
  }, []);

  // File Operations
  const updateFileContentLocally = (fileId: string, content: string) => {
    setProject((prev) => {
      const updateRecursive = (items: FileItem[]): FileItem[] => {
        return items.map((item) => {
          if (item.id === fileId) {
            return { ...item, content, updatedAt: Date.now() };
          }
          if (item.children) {
            return { ...item, children: updateRecursive(item.children) };
          }
          return item;
        });
      };
      const newFiles = updateRecursive(prev.files);
      const updated = { ...prev, files: newFiles, updatedAt: Date.now() };
      saveProjectToCloud(updated);
      return updated;
    });
  };

  const handleContentChange = (fileId: string, newContent: string) => {
    updateFileContentLocally(fileId, newContent);
    realtimeSocket.sendFileUpdate(fileId, newContent);
  };

  const handleCursorChange = (cursor: { line: number; column: number }) => {
    if (activeFileId) {
      realtimeSocket.sendCursorMove(activeFileId, cursor);
    }
  };

  const findFileById = (items: FileItem[], id: string): FileItem | null => {
    for (const item of items) {
      if (item.id === id) return item;
      if (item.children) {
        const found = findFileById(item.children, id);
        if (found) return found;
      }
    }
    return null;
  };

  const activeFile = activeFileId ? findFileById(project.files, activeFileId) : null;

  // Sync Language with File Extension
  useEffect(() => {
    if (activeFile && activeFile.name) {
      const ext = activeFile.name.split('.').pop()?.toLowerCase();
      const match = JUDGE0_LANGUAGES.find((l) => l.ext === `.${ext}` || l.monacoLang === ext);
      if (match) {
        setSelectedLanguageId(match.id);
      }
    }
  }, [activeFileId, activeFile]);

  const openFiles = openFileIds
    .map((id) => findFileById(project.files, id))
    .filter(Boolean) as FileItem[];

  const handleSelectFile = (file: FileItem) => {
    if (file.type === 'file') {
      if (!openFileIds.includes(file.id)) {
        setOpenFileIds((prev) => [...prev, file.id]);
      }
      setActiveFileId(file.id);
      setActiveDiffVersion(null);
    }
  };

  const handleCloseTab = (fileId: string) => {
    const nextTabs = openFileIds.filter((id) => id !== fileId);
    setOpenFileIds(nextTabs);
    if (activeFileId === fileId) {
      setActiveFileId(nextTabs.length > 0 ? nextTabs[nextTabs.length - 1] : null);
    }
  };

  const handleCreateFile = (parentId: string | null, name: string, isFolder: boolean) => {
    const newId = (isFolder ? 'folder-' : 'file-') + Date.now();
    const ext = name.split('.').pop()?.toLowerCase();
    const langMap: Record<string, string> = {
      ts: 'typescript',
      js: 'javascript',
      py: 'python',
      cpp: 'cpp',
      html: 'html',
      css: 'css',
      json: 'json',
      md: 'markdown',
    };

    const parentPath = parentId ? findFileById(project.files, parentId)?.path : '';
    const itemPath = parentPath ? `${parentPath}/${name}` : name;

    const newItem: FileItem = {
      id: newId,
      name,
      path: itemPath,
      type: isFolder ? 'folder' : 'file',
      parentId,
      language: isFolder ? undefined : langMap[ext || ''] || 'plaintext',
      content: isFolder
        ? undefined
        : `// ${name}\n// Created in VS Code Collaborative Studio\n\nconsole.log("Hello from ${name}!");\n`,
      children: isFolder ? [] : undefined,
    };

    setProject((prev) => {
      let newFiles: FileItem[];
      if (!parentId) {
        newFiles = [...prev.files, newItem];
      } else {
        const addToParent = (items: FileItem[]): FileItem[] => {
          return items.map((item) => {
            if (item.id === parentId) {
              return {
                ...item,
                children: [...(item.children || []), newItem],
              };
            }
            if (item.children) {
              return { ...item, children: addToParent(item.children) };
            }
            return item;
          });
        };
        newFiles = addToParent(prev.files);
      }
      const updated = { ...prev, files: newFiles, updatedAt: Date.now() };
      saveProjectToCloud(updated);
      return updated;
    });

    if (!isFolder) {
      setOpenFileIds((prev) => [...prev, newId]);
      setActiveFileId(newId);
    }
    addLog(`Created ${isFolder ? 'folder' : 'file'}: "${itemPath}"`, 'success');
  };

  const handleDeleteFile = (fileId: string) => {
    const file = findFileById(project.files, fileId);
    setProject((prev) => {
      const deleteRecursive = (items: FileItem[]): FileItem[] => {
        return items
          .filter((item) => item.id !== fileId)
          .map((item) => {
            if (item.children) {
              return { ...item, children: deleteRecursive(item.children) };
            }
            return item;
          });
      };
      const newFiles = deleteRecursive(prev.files);
      const updated = { ...prev, files: newFiles, updatedAt: Date.now() };
      saveProjectToCloud(updated);
      return updated;
    });

    handleCloseTab(fileId);
    if (file) addLog(`Deleted item: "${file.name}"`, 'info');
  };

  const handleRenameFile = (fileId: string, newName: string) => {
    setProject((prev) => {
      const renameRecursive = (items: FileItem[]): FileItem[] => {
        return items.map((item) => {
          if (item.id === fileId) {
            return { ...item, name: newName };
          }
          if (item.children) {
            return { ...item, children: renameRecursive(item.children) };
          }
          return item;
        });
      };
      const newFiles = renameRecursive(prev.files);
      const updated = { ...prev, files: newFiles, updatedAt: Date.now() };
      saveProjectToCloud(updated);
      return updated;
    });
  };

  // Judge0 Code Execution
  const handleExecuteCode = async () => {
    if (!activeFile) {
      addLog('No active file open to execute.', 'error');
      return;
    }

    setIsExecuting(true);
    addLog(`Running ${activeFile.name} on Judge0 Engine (Lang ID: ${selectedLanguageId})...`, 'command');

    try {
      const result = await executeCode({
        sourceCode: activeFile.content || '',
        languageId: selectedLanguageId,
        languageName: activeFile.language,
        stdin,
      });

      setLastResult(result);
      setIsTerminalOpen(true);

      if (result.stdout) {
        addLog(`[STDOUT]\n${result.stdout}`, 'success');
      }
      if (result.stderr) {
        addLog(`[STDERR]\n${result.stderr}`, 'error');
      }

      addLog(
        `Execution finished with status: "${result.status.description}" in ${result.time || '0.03s'}`,
        result.status.id === 3 ? 'success' : 'error'
      );
    } catch (err: any) {
      addLog(`Execution error: ${err.message}`, 'error');
    } finally {
      setIsExecuting(false);
    }
  };

  // Keyboard shortcut listener (Ctrl+Enter / Cmd+Enter to execute)
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
        e.preventDefault();
        handleExecuteCode();
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [activeFile, selectedLanguageId, stdin]);

  // Version Control & Diffs
  const handleSaveSnapshot = (message: string) => {
    const newVersion: VersionSnapshot = {
      id: 'ver-' + Date.now(),
      projectId: project.id,
      message,
      author: currentUser.name,
      timestamp: Date.now(),
      files: project.files,
      hash: Math.random().toString(36).substring(2, 8),
    };

    fetch(`/api/projects/${project.id}/versions`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(newVersion),
    })
      .then((res) => res.json())
      .then((saved) => {
        setVersions((prev) => [saved, ...prev]);
        realtimeSocket.sendVersionCommit(saved);
        addLog(`Created version commit: "${message}" (${saved.hash})`, 'success');
      });
  };

  const handleRestoreSnapshot = (version: VersionSnapshot) => {
    setProject((prev) => {
      const updated = { ...prev, files: version.files, updatedAt: Date.now() };
      saveProjectToCloud(updated);
      return updated;
    });
    addLog(`Restored workspace to version: "${version.message}" (${version.hash})`, 'system');
  };

  const handleCompareDiff = (version: VersionSnapshot) => {
    if (activeDiffVersion?.id === version.id) {
      setActiveDiffVersion(null);
    } else {
      setActiveDiffVersion(version);
    }
  };

  // Terminal Built-in Command Interpreter
  const handleRunCommand = (cmdStr: string) => {
    addLog(`dev@vscode-studio:~$ ${cmdStr}`, 'command');
    const args = cmdStr.split(' ');
    const cmd = args[0].toLowerCase();

    switch (cmd) {
      case 'help':
        addLog(
          `Available Terminal Commands:
- run             : Execute current open file on Judge0
- ls              : List all files in workspace
- cat <filename>  : Output content of specified file
- node <filename> : Execute JS file in node sandbox
- clear           : Clear terminal screen
- git status      : Show version history status
- git commit <m>  : Commit snapshot of current workspace
- theme <name>    : Switch editor theme (vs-dark, vs-light, hc-black, github-light)
- judge0 status   : Display Judge0 execution engine status
- date / echo     : Standard utility tools`,
          'info'
        );
        break;

      case 'run':
        handleExecuteCode();
        break;

      case 'ls':
        const listFiles = (items: FileItem[], indent = ''): string => {
          return items
            .map(
              (i) =>
                `${indent}${i.type === 'folder' ? '📁 ' + i.name : '📄 ' + i.name}` +
                (i.children ? '\n' + listFiles(i.children, indent + '  ') : '')
            )
            .join('\n');
        };
        addLog(`Workspace Files:\n${listFiles(project.files)}`, 'info');
        break;

      case 'cat':
        if (!args[1]) {
          addLog('Usage: cat <filename>', 'error');
        } else {
          const targetName = args[1];
          const getAllFiles = (items: FileItem[]): FileItem[] => {
            let res: FileItem[] = [];
            items.forEach((i) => {
              if (i.type === 'file') res.push(i);
              if (i.children) res = res.concat(getAllFiles(i.children));
            });
            return res;
          };
          const f = getAllFiles(project.files).find((i) => i.name === targetName || i.path === targetName);
          if (f) {
            addLog(`--- ${f.path} ---\n${f.content}`, 'info');
          } else {
            addLog(`File not found: ${targetName}`, 'error');
          }
        }
        break;

      case 'clear':
        setTerminalLogs([]);
        break;

      case 'git':
        if (args[1] === 'status') {
          addLog(
            `On branch main\nWorkspace clean. ${versions.length} total version commits logged.`,
            'info'
          );
        } else if (args[1] === 'commit') {
          const msg = args.slice(2).join(' ') || 'Manual terminal commit';
          handleSaveSnapshot(msg);
        } else {
          addLog('Git commands supported: git status, git commit <message>', 'info');
        }
        break;

      case 'theme':
        if (args[1]) {
          const tName = args[1] as EditorTheme;
          setTheme(tName);
          addLog(`Theme set to ${tName}`, 'success');
        } else {
          addLog('Available themes: vs-dark, vs-light, hc-black, github-light', 'info');
        }
        break;

      case 'judge0':
        addLog('Judge0 Engine: ONLINE (CE v1.13.1 + Server Sandbox Fallback)', 'success');
        break;

      case 'date':
        addLog(new Date().toString(), 'info');
        break;

      case 'echo':
        addLog(args.slice(1).join(' '), 'info');
        break;

      default:
        addLog(`Command not recognized: "${cmd}". Type "help" for command list.`, 'error');
        break;
    }
  };

  // Export Project JSON Backup
  const handleExportProject = () => {
    const dataStr = 'data:text/json;charset=utf-8,' + encodeURIComponent(JSON.stringify(project, null, 2));
    const downloadAnchor = document.createElement('a');
    downloadAnchor.setAttribute('href', dataStr);
    downloadAnchor.setAttribute('download', `${project.name.toLowerCase().replace(/\s+/g, '-')}-backup.json`);
    document.body.appendChild(downloadAnchor);
    downloadAnchor.click();
    downloadAnchor.remove();
    addLog('Exported workspace backup file.', 'success');
  };

  const handleResetProject = () => {
    if (confirm('Reset workspace to default starter files? Current unsaved changes will be cleared.')) {
      setProject(DEFAULT_PROJECT);
      setOpenFileIds(['f-1-1', 'f-1-2']);
      setActiveFileId('f-1-1');
      saveProjectToCloud(DEFAULT_PROJECT);
      addLog('Reset workspace files to starter templates.', 'system');
    }
  };

  const handleJoinRoom = (roomId: string) => {
    setCurrentRoom(roomId);
    realtimeSocket.joinRoom(roomId);
    const newUrl = `${window.location.pathname}?room=${roomId}`;
    window.history.pushState({ path: newUrl }, '', newUrl);
    addLog(`Joined collaborative room: "${roomId}"`, 'system');
  };

  const handleUpdateUser = (name: string, color: string) => {
    realtimeSocket.updateUserInfo(name, color);
    addLog(`Updated developer presence profile to: ${name}`, 'info');
  };

  return (
    <div className="flex flex-col h-screen w-screen overflow-hidden bg-[#1e1e1e] font-sans antialiased text-[#cccccc]">
      {/* Mobile Header Bar */}
      <div className="md:hidden flex items-center justify-between px-3 py-2 bg-[#252526] border-b border-[#2d2d2d] z-30">
        <div className="flex items-center gap-2 text-white font-semibold text-sm">
          <Code2 className="w-5 h-5 text-[#007acc]" />
          <span>VS Code Studio</span>
        </div>

        <div className="flex items-center gap-2">
          <button
            onClick={handleExecuteCode}
            disabled={isExecuting}
            className="px-2.5 py-1 bg-emerald-600 hover:bg-emerald-500 text-white rounded text-sm font-semibold flex items-center gap-1"
          >
            <Play className="w-3 h-3 fill-current" />
            <span>Run</span>
          </button>

          <button
            onClick={() => setIsMobileSidebarOpen(!isMobileSidebarOpen)}
            className="p-1.5 bg-[#333333] text-white rounded"
          >
            <Menu className="w-4 h-4" />
          </button>
        </div>
      </div>

      {/* Main Workspace Body */}
      <div className="flex-1 flex overflow-hidden relative">
        {/* Left Activity Bar */}
        <div className={`${isMobileSidebarOpen ? 'block' : 'hidden md:block'}`}>
          <ActivityBar
            activeTab={activeTab}
            setActiveTab={(tab) => {
              setActiveTab(tab);
              if (tab === 'terminal') {
                setIsTerminalOpen(true);
              }
            }}
            userCount={activeUsers.length}
            isConnected={isConnected}
            currentUserColor={currentUser.color}
            currentUserName={currentUser.name}
          />
        </div>

        {/* Collapsible Sidebar */}
        <Sidebar
          activeTab={activeTab}
          files={project.files}
          activeFileId={activeFileId}
          onSelectFile={handleSelectFile}
          onCreateFile={handleCreateFile}
          onDeleteFile={handleDeleteFile}
          onRenameFile={handleRenameFile}
          versions={versions}
          onSaveSnapshot={handleSaveSnapshot}
          onRestoreSnapshot={handleRestoreSnapshot}
          onCompareDiff={handleCompareDiff}
          activeDiffVersionId={activeDiffVersion?.id || null}
          selectedLanguageId={selectedLanguageId}
          setSelectedLanguageId={setSelectedLanguageId}
          stdin={stdin}
          setStdin={setStdin}
          onExecute={handleExecuteCode}
          isExecuting={isExecuting}
          lastResult={lastResult}
          activeFileName={activeFile?.name || ''}
          currentRoom={currentRoom}
          onJoinRoom={handleJoinRoom}
          activeUsers={activeUsers}
          currentUser={currentUser}
          onUpdateUser={handleUpdateUser}
          isConnected={isConnected}
          theme={theme}
          setTheme={setTheme}
          onExportProject={handleExportProject}
          onResetProject={handleResetProject}
        />

        {/* Central Editor + Bottom Terminal Workspace */}
        <div className="flex-1 flex flex-col h-full overflow-hidden">
          {/* Top Editor Area */}
          <EditorArea
            openFiles={openFiles}
            activeFileId={activeFileId}
            onSelectTab={setActiveFileId}
            onCloseTab={handleCloseTab}
            onContentChange={handleContentChange}
            onCursorChange={handleCursorChange}
            theme={theme}
            activeUsers={activeUsers}
            diffVersion={activeDiffVersion}
            onCloseDiff={() => setActiveDiffVersion(null)}
          />

          {/* Bottom Integrated Terminal */}
          <TerminalPanel
            logs={terminalLogs}
            onClearLogs={() => setTerminalLogs([])}
            onExecuteCode={handleExecuteCode}
            lastExecutionResult={lastResult}
            onRunCommand={handleRunCommand}
            isOpen={isTerminalOpen}
            onClose={() => setIsTerminalOpen(false)}
          />
        </div>
      </div>

      {/* Bottom VS Code Status Bar */}
      <StatusBar
        cursorPosition={{ line: 1, column: 1 }}
        language={activeFile?.language || 'typescript'}
        theme={theme}
        onToggleTheme={() => setTheme(theme.includes('light') ? 'vs-dark' : 'vs-light')}
        userCount={activeUsers.length}
        isConnected={isConnected}
        onToggleTerminal={() => setIsTerminalOpen(!isTerminalOpen)}
        onToggleMobileSidebar={() => setIsMobileSidebarOpen(!isMobileSidebarOpen)}
        currentRoom={currentRoom}
      />
    </div>
  );
}
