import { UserPresence, FileItem, VersionSnapshot } from '../types';

export type WSMessageType =
  | 'join-room'
  | 'room-state'
  | 'user-joined'
  | 'user-left'
  | 'file-update'
  | 'file-updated'
  | 'cursor-move'
  | 'cursor-updated'
  | 'version-commit'
  | 'version-added'
  | 'ping'
  | 'pong';

export interface WSMessage {
  type: WSMessageType;
  roomId: string;
  senderId?: string;
  user?: UserPresence;
  users?: UserPresence[];
  fileId?: string;
  content?: string;
  cursor?: { line: number; column: number };
  selection?: { startLine: number; startColumn: number; endLine: number; endColumn: number };
  version?: VersionSnapshot;
  timestamp?: number;
}

export type WSCallback = (msg: WSMessage) => void;

class RealtimeSocket {
  private socket: WebSocket | null = null;
  private listeners: Map<string, Set<WSCallback>> = new Map();
  private isConnected = false;
  private currentRoom = 'default-workspace';
  private currentUser: UserPresence | null = null;
  private reconnectAttempts = 0;
  private maxReconnectAttempts = 5;

  constructor() {
    this.initUser();
  }

  private initUser() {
    const savedUser = localStorage.getItem('vsc_studio_user');
    if (savedUser) {
      try {
        this.currentUser = JSON.parse(savedUser);
      } catch (e) {
        /* ignore */
      }
    }

    if (!this.currentUser) {
      const colors = ['#f87171', '#60a5fa', '#34d399', '#fbbf24', '#a78bfa', '#f472b6'];
      const randomColor = colors[Math.floor(Math.random() * colors.length)];
      const randomId = 'usr_' + Math.floor(Math.random() * 10000);
      const names = ['Alex', 'DevMaya', 'CodeNinja', 'PixelSam', 'Jordan', 'Taylor', 'Morgan'];
      const randomName = names[Math.floor(Math.random() * names.length)];

      this.currentUser = {
        id: randomId,
        name: randomName,
        color: randomColor,
        joinedAt: Date.now(),
      };
      localStorage.setItem('vsc_studio_user', JSON.stringify(this.currentUser));
    }
  }

  public getCurrentUser(): UserPresence {
    if (!this.currentUser) this.initUser();
    return this.currentUser!;
  }

  public updateUserInfo(name: string, color: string) {
    if (this.currentUser) {
      this.currentUser.name = name;
      this.currentUser.color = color;
      localStorage.setItem('vsc_studio_user', JSON.stringify(this.currentUser));
      if (this.isConnected) {
        this.joinRoom(this.currentRoom);
      }
    }
  }

  public connect(roomId: string = 'default-workspace') {
    this.currentRoom = roomId;
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}`;

    try {
      this.socket = new WebSocket(wsUrl);

      this.socket.onopen = () => {
        this.isConnected = true;
        this.reconnectAttempts = 0;
        this.joinRoom(roomId);
        this.notifyListeners('connection', { type: 'room-state', roomId });
      };

      this.socket.onmessage = (event) => {
        try {
          const data: WSMessage = JSON.parse(event.data);
          this.notifyListeners(data.type, data);
          this.notifyListeners('*', data);
        } catch (err) {
          console.error('Failed to parse WS message:', err);
        }
      };

      this.socket.onclose = () => {
        this.isConnected = false;
        this.notifyListeners('connection', { type: 'room-state', roomId, users: [] });
        this.attemptReconnect();
      };

      this.socket.onerror = (err) => {
        console.warn('WebSocket connection error:', err);
      };
    } catch (e) {
      console.error('WebSocket initialization failed:', e);
    }
  }

  private attemptReconnect() {
    if (this.reconnectAttempts < this.maxReconnectAttempts) {
      this.reconnectAttempts++;
      setTimeout(() => {
        if (!this.isConnected) {
          this.connect(this.currentRoom);
        }
      }, 2000 * this.reconnectAttempts);
    }
  }

  public joinRoom(roomId: string) {
    this.currentRoom = roomId;
    if (this.socket && this.socket.readyState === WebSocket.OPEN) {
      const msg: WSMessage = {
        type: 'join-room',
        roomId,
        user: this.getCurrentUser(),
      };
      this.socket.send(JSON.stringify(msg));
    }
  }

  public sendFileUpdate(fileId: string, content: string) {
    if (this.socket && this.socket.readyState === WebSocket.OPEN) {
      const msg: WSMessage = {
        type: 'file-update',
        roomId: this.currentRoom,
        senderId: this.getCurrentUser().id,
        fileId,
        content,
        timestamp: Date.now(),
      };
      this.socket.send(JSON.stringify(msg));
    }
  }

  public sendCursorMove(
    fileId: string,
    cursor: { line: number; column: number },
    selection?: { startLine: number; startColumn: number; endLine: number; endColumn: number }
  ) {
    if (this.socket && this.socket.readyState === WebSocket.OPEN) {
      const msg: WSMessage = {
        type: 'cursor-move',
        roomId: this.currentRoom,
        senderId: this.getCurrentUser().id,
        fileId,
        cursor,
        selection,
      };
      this.socket.send(JSON.stringify(msg));
    }
  }

  public sendVersionCommit(version: VersionSnapshot) {
    if (this.socket && this.socket.readyState === WebSocket.OPEN) {
      const msg: WSMessage = {
        type: 'version-commit',
        roomId: this.currentRoom,
        senderId: this.getCurrentUser().id,
        version,
      };
      this.socket.send(JSON.stringify(msg));
    }
  }

  public on(eventType: string, callback: WSCallback) {
    if (!this.listeners.has(eventType)) {
      this.listeners.set(eventType, new Set());
    }
    this.listeners.get(eventType)!.add(callback);

    return () => {
      this.listeners.get(eventType)?.delete(callback);
    };
  }

  private notifyListeners(eventType: string, message: WSMessage) {
    const callbacks = this.listeners.get(eventType);
    if (callbacks) {
      callbacks.forEach((cb) => cb(message));
    }
  }

  public getIsConnected() {
    return this.isConnected;
  }
}

export const realtimeSocket = new RealtimeSocket();
