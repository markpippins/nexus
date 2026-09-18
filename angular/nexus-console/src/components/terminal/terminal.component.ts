import {
  AfterViewInit,
  ChangeDetectionStrategy,
  ChangeDetectorRef,
  Component,
  ElementRef,
  input,
  OnDestroy,
  QueryList,
  ViewChildren,
  effect,
  inject,
  output,
  signal,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { FitAddon } from '@xterm/addon-fit';
import { Terminal } from 'xterm';
import 'xterm/css/xterm.css';
import { Bash, defineCommand, type BashExecResult } from 'just-bash/browser';
import { UiPreferencesService } from '../../services/ui-preferences.service.js';

// ── Custom commands ────────────────────────────────────────────────────

const helpCommand = defineCommand('help', async (_args, _ctx) => {
  const lines = [
    'Welcome to Nexus Console Terminal',
    '',
    '  Built-in commands:',
    '    ls, cd, pwd, cat, echo, mkdir, rm, rmdir, cp, mv, grep, find,',
    '    head, tail, sort, wc, tee, sed, awk, cut, tr, uniq,',
    '    ps, kill, sleep, echo, printf, date, env, export, source,',
    '    chmod, chown, ln, readlink, stat, du, df, touch,',
    '    which, type, command, hash, alias, unalias,',
    '    help, man, info, whatis, apropos,',
    '    ping, curl, wget, ssh, scp, rsync,',
    '    git, tar, gzip, gunzip, zip, unzip,',
    '    python3, node, npx,',
    '    clear, history, logout, exit',
    '',
    '  Custom commands:',
    '    help       - Show this message',
    '',
    '  Tips:',
    '    Use up/down arrows to navigate command history',
    '    Ctrl+C to interrupt a running command',
    '    Ctrl+L to clear the screen',
    '    Type start-logging to stream broker traffic logs',
    '    Type stop-logging to stop log streaming',
    '',
  ];
  return { stdout: lines.join('\n'), stderr: '', exitCode: 0 };
});

interface TerminalSession {
  id: string;
  label: string;
  bash: Bash | null;
  terminal: Terminal;
  fitAddon: FitAddon;
  currentCwd: string;
  currentEnv: Record<string, string>;
  commandHistory: string[];
  inputBuffer: string;
  historyIndex: number;
  isExecuting: boolean;
  // Remote shell mode
  ws: WebSocket | null;
  isRemote: boolean;
  // Reconnection state
  reconnectAttempts: number;
  reconnectTimer: ReturnType<typeof setTimeout> | null;
}

@Component({
  selector: 'app-terminal',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './terminal.component.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
  styles: [
    `
      :host {
        display: block;
        height: 100%;
        min-height: 0;
      }
      .tab-bar {
        display: flex;
        align-items: center;
        gap: 2px;
        background: rgb(var(--color-surface-base));
        border-bottom: 1px solid rgb(var(--color-border-base));
        padding: 2px 4px 0 4px;
        overflow-x: auto;
        min-height: 30px;
      }
      .tab {
        display: flex;
        align-items: center;
        gap: 4px;
        padding: 2px 8px;
        border-radius: 4px 4px 0 0;
        cursor: pointer;
        user-select: none;
        font-size: 12px;
        color: rgb(var(--color-text-muted));
        border: 1px solid transparent;
        border-bottom: none;
        white-space: nowrap;
      }
      .tab:hover {
        background: rgb(var(--color-surface-hover));
      }
      .tab.active {
        background: rgb(var(--color-surface-muted));
        color: rgb(var(--color-text-base));
        border-color: rgb(var(--color-border-base));
      }
      .tab .tab-close {
        width: 16px;
        height: 16px;
        display: flex;
        align-items: center;
        justify-content: center;
        border-radius: 3px;
        border: none;
        background: transparent;
        color: inherit;
        cursor: pointer;
        font-size: 14px;
        line-height: 1;
        padding: 0;
      }
      .tab .tab-close:hover {
        background: rgb(var(--color-surface-hover));
        color: rgb(var(--color-text-base));
      }
      .tab .mode-toggle {
        width: 16px;
        height: 16px;
        display: flex;
        align-items: center;
        justify-content: center;
        border-radius: 3px;
        border: none;
        background: transparent;
        color: inherit;
        cursor: pointer;
        font-size: 11px;
        line-height: 1;
        padding: 0;
        opacity: 0.5;
        transition: opacity 0.15s;
      }
      .tab .mode-toggle:hover {
        opacity: 1;
        background: rgb(var(--color-surface-hover));
      }
      .tab .mode-toggle.remote {
        color: rgb(var(--color-accent-text));
        opacity: 0.8;
      }
      .tab .tab-label {
        max-width: 120px;
        overflow: hidden;
        text-overflow: ellipsis;
      }
      .tab .rename-input {
        width: 100px;
        background: rgb(var(--color-surface-elevated));
        border: 1px solid rgb(var(--color-accent-ring));
        border-radius: 2px;
        color: rgb(var(--color-text-base));
        font-size: 12px;
        padding: 1px 4px;
        outline: none;
      }
      .add-tab-btn {
        width: 24px;
        height: 24px;
        display: flex;
        align-items: center;
        justify-content: center;
        border-radius: 3px;
        border: none;
        background: transparent;
        color: rgb(var(--color-text-muted));
        cursor: pointer;
        font-size: 16px;
        line-height: 1;
        padding: 0;
        margin-left: 2px;
        flex-shrink: 0;
      }
      .add-tab-btn:hover {
        background: rgb(var(--color-surface-hover));
        color: rgb(var(--color-text-base));
      }
      .terminal-area {
        flex: 1;
        min-height: 0;
        position: relative;
        overflow: hidden;
      }
      .terminal-container {
        width: 100%;
        height: 100%;
      }
      .terminal-container.hidden {
        display: none;
      }
      .collapse-btn {
        width: 24px;
        height: 24px;
        display: flex;
        align-items: center;
        justify-content: center;
        border-radius: 3px;
        border: none;
        background: transparent;
        color: rgb(var(--color-text-muted));
        cursor: pointer;
        padding: 0;
        flex-shrink: 0;
      }
      .collapse-btn:hover,
      .maximize-btn:hover {
        background: rgb(var(--color-surface-hover));
        color: rgb(var(--color-text-base));
      }
      .maximize-btn {
        width: 24px;
        height: 24px;
        display: flex;
        align-items: center;
        justify-content: center;
        border-radius: 3px;
        border: none;
        background: transparent;
        color: rgb(var(--color-text-muted));
        cursor: pointer;
        padding: 0;
        flex-shrink: 0;
      }
    `,
  ],
})
export class   TerminalComponent implements AfterViewInit, OnDestroy {
  @ViewChildren('terminalContainer') terminals!: QueryList<ElementRef<HTMLDivElement>>;
  collapse = output<void>();
  maximizeToggle = output<void>();

  /** Parent-driven: whether the terminal is currently maximized (controls the button icon). */
  isMaximized = input(false);

  toggleMaximize(): void {
    this.maximizeToggle.emit();
  }

  /**
   * When set to a WebSocket URL (e.g. 'ws://localhost:3130'), the terminal
   * connects to a real shell process on the host via the Moleculer PTY
   * transport backend (worker.pty-transport).
   * When empty/undefined, the terminal uses the local just-bash emulator.
   */
  remoteShellUrl = input<string | undefined>(undefined);

  private static readonly LOCAL_STORAGE_KEY = 'nexus-console-remote-sessions';
  private readonly initialCwd = '/home/user';
  private resizeObserver: ResizeObserver | null = null;
  private hostEl = inject(ElementRef);
  private uiPreferencesService = inject(UiPreferencesService);
  private cdr = inject(ChangeDetectorRef);
  private logEventSource: EventSource | null = null;
  private readonly MAX_RECONNECT_ATTEMPTS = 5;
  private readonly RECONNECT_DELAY_MS = 2000;

  sessions = signal<TerminalSession[]>([]);
  activeIndex = signal(0);
  editingIndex = signal<number | null>(null);
  editLabelInput = signal('');

  constructor() {
    effect(() => {
      // Rerun this logic whenever the theme signal changes.
      this.uiPreferencesService.theme();
      for (const session of this.sessions()) {
        this.applyThemeToTerminal(session.terminal);
      }
    });
  }

  ngAfterViewInit(): void {
    // Restore persisted remote sessions from localStorage
    const savedIds = TerminalComponent.loadSessionIds();
    if (savedIds.length > 0 && !!this.remoteShellUrl()) {
      // Create remote session tabs for each saved ID — use the saved ID directly
      const sessions = savedIds.map((savedId, i) =>
        this.createSession(`Terminal ${i + 1}`, savedId),
      );
      this.sessions.set(sessions);
      this.activeIndex.set(0);

      setTimeout(() => {
        const divs = this.terminals.toArray();
        sessions.forEach((s, i) => {
          if (divs.length > i) {
            s.terminal.open(divs[i].nativeElement);
            s.fitAddon.fit();
            if (i === 0) {
              this.writeWelcomeAndPrompt(s);
            }
          }
        });
      });
    } else {
      const initialSession = this.createSession('Terminal 1');
      this.sessions.set([initialSession]);

      setTimeout(() => {
        const divs = this.terminals.toArray();
        if (divs.length > 0) {
          initialSession.terminal.open(divs[0].nativeElement);
          initialSession.fitAddon.fit();
          this.writeWelcomeAndPrompt(initialSession);
        }
      }, 0);
    }

    this.resizeObserver = new ResizeObserver(() => {
      try {
        setTimeout(() => {
          const session = this.activeSession();
          if (session) session.fitAddon.fit();
        }, 0);
      } catch (e) {
        console.warn('FitAddon resize failed. This can happen during rapid resizing.', e);
      }
    });

    this.resizeObserver.observe(this.hostEl.nativeElement);
  }

  private createSession(label: string, restoredId?: string): TerminalSession {
    const isRemote = !!this.remoteShellUrl();
    const sessionId = restoredId || `term-${Date.now()}-${Math.random().toString(36).substring(2, 9)}`;

    const bash: Bash | null = isRemote
      ? null
      : new Bash({
          cwd: this.initialCwd,
          env: { TERM: 'xterm-256color' },
          customCommands: [helpCommand],
        });

    const terminal = new Terminal({
      cursorBlink: true,
      convertEol: true,
      fontFamily: `ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace`,
      fontSize: 13,
    });
    this.applyThemeToTerminal(terminal);
    const fitAddon = new FitAddon();
    terminal.loadAddon(fitAddon);

    const id = sessionId;
    const session: TerminalSession = {
      id,
      label,
      bash,
      terminal,
      fitAddon,
      currentCwd: this.initialCwd,
      currentEnv: { TERM: 'xterm-256color' },
      commandHistory: [],
      inputBuffer: '',
      historyIndex: -1,
      isExecuting: false,
      ws: null,
      isRemote,
      reconnectAttempts: 0,
      reconnectTimer: null,
    };

    // Wire up input handling (both local and remote)
    terminal.onData((data: string) => {
      void this.handleSessionInput(session, data);
    });

    if (isRemote) {
      this.saveSessionId(id);
      this.connectRemoteSession(session);

      // Resize forwarding for remote sessions
      terminal.onResize(({ cols, rows }) => {
        if (session.ws && session.ws.readyState === WebSocket.OPEN) {
          session.ws.send(JSON.stringify({ type: 'resize', cols, rows }));
        }
      });
    }

    return session;
  }

  private connectRemoteSession(session: TerminalSession): void {
    const baseUrl = this.remoteShellUrl();
    if (!baseUrl) return;

    // Include session ID for tmux reconnection
    const url = `${baseUrl}?session=${encodeURIComponent(session.id)}`;
    session.reconnectAttempts = 0;

    this.doConnect(session, url);
  }

  private doConnect(session: TerminalSession, url: string): void {
    const ws = new WebSocket(url);
    session.ws = ws;

    ws.onopen = () => {
      session.reconnectAttempts = 0;
      const dims = session.fitAddon.proposeDimensions();
      if (dims) {
        ws.send(JSON.stringify({ type: 'resize', cols: dims.cols, rows: dims.rows }));
      }
    };

    ws.onmessage = (ev: MessageEvent) => {
      session.terminal.write(ev.data);
      const osc7Match = ev.data.match(/\x1b]7;file:\/\/[^\/]+(.+)\x07/);
      if (osc7Match) {
        session.currentCwd = osc7Match[1] || session.currentCwd;
      }
    };

    ws.onclose = () => {
      session.ws = null;
      if (session.reconnectAttempts < this.MAX_RECONNECT_ATTEMPTS) {
        session.reconnectAttempts++;
        session.terminal.writeln(
          `\r\n\x1b[33m[disconnected — reconnecting in ${this.RECONNECT_DELAY_MS / 1000}s (attempt ${session.reconnectAttempts}/${this.MAX_RECONNECT_ATTEMPTS})]\x1b[0m`,
        );
        session.reconnectTimer = setTimeout(() => {
          this.doConnect(session, url);
        }, this.RECONNECT_DELAY_MS);
      } else {
        session.terminal.writeln('\r\n\x1b[31m[connection lost — max reconnect attempts reached]\x1b[0m');
      }
    };

    ws.onerror = () => {
      // onclose will fire after onerror, so reconnect logic is there
    };
  }

  addTab(): void {
    const label = `Terminal ${this.sessions().length + 1}`;
    const session = this.createSession(label);
    const newIndex = this.sessions().length;
    this.sessions.update(s => [...s, session]);
    this.activeIndex.set(newIndex);

    this.cdr.detectChanges();
    setTimeout(() => {
      const divs = this.terminals.toArray();
      if (divs.length > newIndex) {
        session.terminal.open(divs[newIndex].nativeElement);
        session.fitAddon.fit();
        this.writeWelcomeAndPrompt(session);
      }
    });
  }

  removeTab(index: number): void {
    const currentSessions = this.sessions();
    const session = currentSessions[index];

    // Close WebSocket for remote sessions
    if (session.ws) {
      try { session.ws.close(); } catch { /* ignore */ }
      session.ws = null;
    }
    // Cancel any reconnect timer
    if (session.reconnectTimer) {
      clearTimeout(session.reconnectTimer);
      session.reconnectTimer = null;
    }
    // Remove from persisted sessions
    this.removeSessionId(session.id);

    session.terminal.dispose();

    if (currentSessions.length <= 1) {
      const fresh = this.createSession('Terminal 1');
      this.sessions.set([fresh]);
      this.activeIndex.set(0);
      this.cdr.detectChanges();
      setTimeout(() => {
        const divs = this.terminals.toArray();
        if (divs.length > 0) {
          fresh.terminal.open(divs[0].nativeElement);
          fresh.fitAddon.fit();
          this.writeWelcomeAndPrompt(fresh);
        }
      });
      return;
    }

    this.sessions.update(s => s.filter((_, i) => i !== index));

    const currentActive = this.activeIndex();
    if (currentActive >= index) {
      const newActive = Math.max(0, currentActive - 1);
      if (newActive < this.sessions().length) {
        this.activeIndex.set(newActive);
      }
    }
    if (this.activeIndex() >= this.sessions().length) {
      this.activeIndex.set(this.sessions().length - 1);
    }
  }

  switchTab(index: number): void {
    if (index === this.activeIndex()) return;
    this.activeIndex.set(index);
    setTimeout(() => {
      const session = this.activeSession();
      if (session) session.fitAddon.fit();
    });
  }

  startRename(index: number): void {
    this.editLabelInput.set(this.sessions()[index].label);
    this.editingIndex.set(index);
  }

  commitRename(index: number): void {
    const newLabel = this.editLabelInput().trim();
    if (newLabel) {
      this.sessions.update(s =>
        s.map((sess, i) => (i === index ? { ...sess, label: newLabel } : sess)),
      );
    }
    this.editingIndex.set(null);
  }

  cancelRename(): void {
    this.editingIndex.set(null);
  }

  // ─── Per-tab mode toggle ───

  /**
   * Toggles a tab between local (just-bash) and remote (Moleculer PTY
   * transport WebSocket) mode.
   * Disposes the current terminal and creates a fresh one with the new backend.
   */
  toggleMode(index: number): void {
    const sessions = this.sessions();
    const session = sessions[index];
    if (!session) return;

    // Clean up current backend
    if (session.ws) {
      try { session.ws.close(); } catch { /* ignore */ }
      session.ws = null;
    }
    session.terminal.dispose();

    // Toggle
    const newIsRemote = !session.isRemote;
    const isActive = index === this.activeIndex();

    // Create new session with opposite mode
    const newBash: Bash | null = newIsRemote
      ? null
      : new Bash({
          cwd: this.initialCwd,
          env: { TERM: 'xterm-256color' },
          customCommands: [helpCommand],
        });

    const newTerminal = new Terminal({
      cursorBlink: true,
      convertEol: true,
      fontFamily: `ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace`,
      fontSize: 13,
    });
    this.applyThemeToTerminal(newTerminal);
    const newFitAddon = new FitAddon();
    newTerminal.loadAddon(newFitAddon);

    const updatedSession: TerminalSession = {
      ...session,
      bash: newBash,
      terminal: newTerminal,
      fitAddon: newFitAddon,
      isRemote: newIsRemote,
      ws: null,
      currentCwd: this.initialCwd,
      currentEnv: { TERM: 'xterm-256color' },
      commandHistory: [],
      inputBuffer: '',
      historyIndex: -1,
      isExecuting: false,
    };

    // Replace in sessions array
    this.sessions.update(s => s.map((s, i) => (i === index ? updatedSession : s)));

    // Wire up input
    newTerminal.onData((data: string) => {
      void this.handleSessionInput(updatedSession, data);
    });

    if (newIsRemote) {
      this.saveSessionId(updatedSession.id);
      this.connectRemoteSession(updatedSession);
      newTerminal.onResize(({ cols, rows }) => {
        if (updatedSession.ws && updatedSession.ws.readyState === WebSocket.OPEN) {
          updatedSession.ws.send(JSON.stringify({ type: 'resize', cols, rows }));
        }
      });
    } else {
      this.removeSessionId(session.id);
    }

    // Always open in the DOM (the div always exists, just hidden by CSS)
    this.cdr.detectChanges();
    setTimeout(() => {
      const divs = this.terminals.toArray();
      if (divs.length > index) {
        newTerminal.open(divs[index].nativeElement);
        newFitAddon.fit();
        if (isActive) {
          this.writeWelcomeAndPrompt(updatedSession);
        }
      }
    });
  }

  // ─── LocalStorage persistence for remote session IDs ───

  private saveSessionId(id: string): void {
    const ids = TerminalComponent.loadSessionIds();
    if (!ids.includes(id)) {
      ids.push(id);
      localStorage.setItem(TerminalComponent.LOCAL_STORAGE_KEY, JSON.stringify(ids));
    }
  }

  private removeSessionId(id: string): void {
    const ids = TerminalComponent.loadSessionIds().filter(i => i !== id);
    if (ids.length > 0) {
      localStorage.setItem(TerminalComponent.LOCAL_STORAGE_KEY, JSON.stringify(ids));
    } else {
      localStorage.removeItem(TerminalComponent.LOCAL_STORAGE_KEY);
    }
  }

  private static loadSessionIds(): string[] {
    try {
      const raw = localStorage.getItem(TerminalComponent.LOCAL_STORAGE_KEY);
      if (!raw) return [];
      const parsed = JSON.parse(raw);
      return Array.isArray(parsed) ? parsed : [];
    } catch {
      return [];
    }
  }

  private activeSession(): TerminalSession | undefined {
    const idx = this.activeIndex();
    const all = this.sessions();
    return idx >= 0 && idx < all.length ? all[idx] : undefined;
  }

  // ─── Input handling ───

  private async handleSessionInput(session: TerminalSession, data: string): Promise<void> {
    // Remote sessions: forward all input directly to the PTY
    if (session.isRemote) {
      if (session.ws && session.ws.readyState === WebSocket.OPEN) {
        session.ws.send(JSON.stringify({ type: 'input', data }));
      }
      return;
    }

    if (session.isExecuting) {
      if (data === '\u0003') {
        session.terminal.write('^C');
      }
      return;
    }

    switch (data) {
      case '\r':
        await this.executeSessionCommand(session);
        return;
      case '\u007F':
        this.handleSessionBackspace(session);
        return;
      case '\u0003':
        this.handleSessionInterrupt(session);
        return;
      case '\u000C':
        session.terminal.clear();
        this.writeSessionPrompt(session);
        return;
      case '\u001b[A':
        this.navigateSessionHistory(session, -1);
        return;
      case '\u001b[B':
        this.navigateSessionHistory(session, 1);
        return;
      case '\u001b[C':
      case '\u001b[D':
      case '\t':
        return;
      default:
        if (this.isPrintableInput(data)) {
          session.inputBuffer += data;
          session.terminal.write(data);
        }
    }
  }

  private async executeSessionCommand(session: TerminalSession): Promise<void> {
    // Remote sessions: execution is handled by the PTY — nothing to do here
    if (session.isRemote) return;

    if (!session.bash) return;

    const command = session.inputBuffer;
    session.terminal.write('\r\n');

    if (!command.trim()) {
      session.inputBuffer = '';
      session.historyIndex = -1;
      this.writeSessionPrompt(session);
      return;
    }

    const trimmedCmd = command.trim();
    if (trimmedCmd === 'start-logging') {
      this.startLogging(session);
      session.inputBuffer = '';
      this.writeSessionPrompt(session);
      return;
    }
    if (trimmedCmd === 'stop-logging') {
      this.stopLogging(session);
      session.inputBuffer = '';
      this.writeSessionPrompt(session);
      return;
    }

    if (command.trim() === 'clear') {
      session.terminal.clear();
      session.inputBuffer = '';
      session.historyIndex = -1;
      this.writeSessionPrompt(session);
      return;
    }

    session.commandHistory.push(command);
    session.historyIndex = -1;
    session.inputBuffer = '';
    session.isExecuting = true;

    try {
      const result = await session.bash.exec(command, {
        cwd: session.currentCwd,
        env: session.currentEnv,
      });

      this.applySessionExecutionState(session, result);
      this.writeSessionResult(session, result);
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      session.terminal.writeln(`bash: ${message}`);
    } finally {
      session.isExecuting = false;
      this.writeSessionPrompt(session);
    }
  }

  private applySessionExecutionState(session: TerminalSession, result: BashExecResult): void {
    session.currentEnv = result.env;
    session.currentCwd = result.env.PWD || session.currentCwd;
  }

  private writeSessionResult(session: TerminalSession, result: BashExecResult): void {
    const stdout = this.normalizeOutput(result.stdout);
    const stderr = this.normalizeOutput(result.stderr);

    if (stdout) {
      session.terminal.write(stdout);
      if (!stdout.endsWith('\r\n')) {
        session.terminal.write('\r\n');
      }
    }

    if (stderr) {
      session.terminal.write(`\x1b[31m${stderr}\x1b[0m`);
      if (!stderr.endsWith('\r\n')) {
        session.terminal.write('\r\n');
      }
    }
  }

  private handleSessionBackspace(session: TerminalSession): void {
    if (session.inputBuffer.length === 0) {
      return;
    }
    session.inputBuffer = session.inputBuffer.slice(0, -1);
    session.terminal.write('\b \b');
  }

  private handleSessionInterrupt(session: TerminalSession): void {
    session.inputBuffer = '';
    session.historyIndex = -1;
    session.terminal.write('^C\r\n');
    this.writeSessionPrompt(session);
  }

  private navigateSessionHistory(session: TerminalSession, direction: -1 | 1): void {
    if (session.commandHistory.length === 0) {
      return;
    }

    if (direction === -1) {
      session.historyIndex =
        session.historyIndex === -1
          ? session.commandHistory.length - 1
          : Math.max(0, session.historyIndex - 1);
    } else if (session.historyIndex !== -1) {
      session.historyIndex += 1;
      if (session.historyIndex >= session.commandHistory.length) {
        session.historyIndex = -1;
      }
    } else {
      return;
    }

    session.inputBuffer =
      session.historyIndex === -1 ? '' : session.commandHistory[session.historyIndex];
    this.renderSessionInputBuffer(session);
  }

  private renderSessionInputBuffer(session: TerminalSession): void {
    session.terminal.write('\r\x1b[2K');
    this.writeSessionPrompt(session, false);
    session.terminal.write(session.inputBuffer);
  }

  private writeSessionPrompt(session: TerminalSession, includeBuffer = true): void {
    session.terminal.write(`\x1b[1;32m${this.getSessionPrompt(session)}\x1b[0m`);
    if (includeBuffer && session.inputBuffer) {
      session.terminal.write(session.inputBuffer);
    }
  }

  private getSessionPrompt(session: TerminalSession): string {
    return `user@nexus:${this.formatPromptPath(session.currentCwd)}$ `;
  }

  private writeWelcomeAndPrompt(session: TerminalSession): void {
    if (session.isRemote) {
      session.terminal.writeln('\x1B[1;3;34mNexus Console — Remote Shell\x1B[0m');
      session.terminal.writeln('Connected to Moleculer PTY transport');
      session.terminal.writeln('');
      return;
    }
    session.terminal.writeln('\x1B[1;3;34mWelcome to the Nexus Console!\x1B[0m');
    session.terminal.writeln('Powered by xterm.js + just-bash');
    session.terminal.writeln('----------------------------------');
    this.writeSessionPrompt(session);
  }

  // ─── Shared utilities ───

  private formatPromptPath(path: string): string {
    return path === this.initialCwd ? '~' : path.replace(`${this.initialCwd}/`, '~/');
  }

  private normalizeOutput(output: string): string {
    return output.replace(/\r?\n/g, '\r\n');
  }

  private isPrintableInput(data: string): boolean {
    return data >= ' ' && data !== '\u007F' && !data.startsWith('\u001b');
  }

  // ─── Theme ───

  private applyThemeToTerminal(term: Terminal): void {
    const computedStyle = getComputedStyle(document.body);
    term.options.theme = {
      background: `rgb(${computedStyle.getPropertyValue('--color-surface-muted').trim()})`,
      foreground: `rgb(${computedStyle.getPropertyValue('--color-text-muted').trim()})`,
      cursor: `rgb(${computedStyle.getPropertyValue('--color-accent-text').trim()})`,
      selectionBackground: `rgba(${computedStyle.getPropertyValue('--color-accent-bg').trim()}, 0.5)`,
      selectionForeground: `rgb(${computedStyle.getPropertyValue('--color-text-base').trim()})`,
    };
  }

  // ─── Cleanup ───

  ngOnDestroy(): void {
    if (this.resizeObserver) {
      this.resizeObserver.disconnect();
    }
    for (const session of this.sessions()) {
      session.terminal.dispose();
      // Close WebSocket for remote sessions
      if (session.ws) {
        try { session.ws.close(); } catch { /* ignore */ }
        session.ws = null;
      }
      // Cancel reconnect timer
      if (session.reconnectTimer) {
        clearTimeout(session.reconnectTimer);
        session.reconnectTimer = null;
      }
    }
    if (this.logEventSource) {
      try {
        this.logEventSource.close();
      } catch {
        // ignore
      }
      this.logEventSource = null;
    }
  }

  // ─── Log streaming (active session) ───

  private getLogStreamUrl(): string {
    return 'http://localhost:8081/api/v1/broker/logs/stream';
  }

  private startLogging(session: TerminalSession): void {
    if (this.logEventSource) {
      try {
        this.logEventSource.close();
      } catch {
        // ignore
      }
      this.logEventSource = null;
    }
    const url = this.getLogStreamUrl();
    this.logEventSource = new EventSource(url);
    this.logEventSource.addEventListener('broker-traffic', (ev: MessageEvent) => {
      const raw = ev.data ?? '';
      try {
        const payload = JSON.parse(raw);
        session.terminal.writeln(`broker-traffic: ${JSON.stringify(payload)}\n`);
      } catch {
        session.terminal.writeln(`broker-traffic: ${raw}\n`);
      }
    });
    this.logEventSource.addEventListener('ping', (ev: MessageEvent) => {
      const ts = ev.data ?? '';
      session.terminal.writeln(`ping: ${ts}`);
    });
    this.logEventSource.onerror = () => {
      session.terminal.writeln('[broker-stream-log-error]');
      try {
        this.logEventSource?.close();
      } catch {
        // ignore
      }
      this.logEventSource = null;
    };
    session.terminal.writeln('[broker-logs-stream] started');
  }

  private stopLogging(session: TerminalSession): void {
    if (this.logEventSource) {
      try {
        this.logEventSource.close();
      } catch {
        // ignore
      }
      this.logEventSource = null;
      session.terminal.writeln('[broker-logs-stream] stopped');
    }
  }
}
