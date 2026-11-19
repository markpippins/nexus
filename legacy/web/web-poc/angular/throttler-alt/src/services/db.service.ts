import { Injectable } from '@angular/core';
import { openDB, DBSchema, IDBPDatabase } from 'idb';
import { ServerProfile } from '../models/server-profile.model.js';
import { RssFeed } from '../models/rss-feed.model.js';
import { FolderProperties } from '../models/folder-properties.model.js';

const DB_NAME = 'file-explorer-db';
const DB_VERSION = 4; // Incremented version
const PROFILES_STORE = 'server-profiles';
const FEEDS_STORE = 'rss-feeds';
const FOLDER_PROPERTIES_STORE = 'folder-properties';
const NOTES_STORE = 'notes'; // New store for notes

export interface Note {
  path: string; // The key, e.g., "Local Session/Documents"
  content: string;
}

interface FileExplorerDB extends DBSchema {
  [PROFILES_STORE]: {
    key: string;
    value: ServerProfile;
  };
  [FEEDS_STORE]: {
    key: string;
    value: RssFeed;
  };
  [FOLDER_PROPERTIES_STORE]: {
    key: string;
    value: FolderProperties;
    indexes: { 'by-path': string };
  };
  [NOTES_STORE]: { // Schema for the new store
    key: string;
    value: Note;
  };
}

@Injectable({
  providedIn: 'root',
})
export class DbService {
  private dbPromise: Promise<IDBPDatabase<FileExplorerDB>>;

  constructor() {
    this.dbPromise = openDB<FileExplorerDB>(DB_NAME, DB_VERSION, {
      upgrade(db: IDBPDatabase<FileExplorerDB>, oldVersion) {
        if (oldVersion < 2) {
          if (!db.objectStoreNames.contains(PROFILES_STORE)) {
            db.createObjectStore(PROFILES_STORE, { keyPath: 'id' });
          }
          if (!db.objectStoreNames.contains(FEEDS_STORE)) {
            db.createObjectStore(FEEDS_STORE, { keyPath: 'id' });
          }
        }
        if (oldVersion < 3) {
           if (!db.objectStoreNames.contains(FOLDER_PROPERTIES_STORE)) {
            db.createObjectStore(FOLDER_PROPERTIES_STORE, { keyPath: 'path' });
          }
        }
        if (oldVersion < 4) {
          if (!db.objectStoreNames.contains(NOTES_STORE)) {
            db.createObjectStore(NOTES_STORE, { keyPath: 'path' });
          }
        }
      },
    });
  }

  // --- Server Profile Methods ---

  async getAllProfiles(): Promise<ServerProfile[]> {
    const db = await this.dbPromise;
    return db.getAll(PROFILES_STORE);
  }

  async addProfile(profile: ServerProfile): Promise<void> {
    const db = await this.dbPromise;
    await db.put(PROFILES_STORE, profile);
  }
  
  async updateProfile(profile: ServerProfile): Promise<void> {
    const db = await this.dbPromise;
    await db.put(PROFILES_STORE, profile);
  }

  async deleteProfile(id: string): Promise<void> {
    const db = await this.dbPromise;
    await db.delete(PROFILES_STORE, id);
  }

  // --- RSS Feed Methods ---

  async getAllFeeds(): Promise<RssFeed[]> {
    const db = await this.dbPromise;
    return db.getAll(FEEDS_STORE);
  }

  async addFeed(feed: RssFeed): Promise<void> {
    const db = await this.dbPromise;
    await db.put(FEEDS_STORE, feed);
  }
  
  async updateFeed(feed: RssFeed): Promise<void> {
    const db = await this.dbPromise;
    await db.put(FEEDS_STORE, feed);
  }

  async deleteFeed(id: string): Promise<void> {
    const db = await this.dbPromise;
    await db.delete(FEEDS_STORE, id);
  }
  
  // --- Folder Properties Methods ---

  async getAllFolderProperties(): Promise<FolderProperties[]> {
    const db = await this.dbPromise;
    return db.getAll(FOLDER_PROPERTIES_STORE);
  }

  async updateFolderProperties(properties: FolderProperties): Promise<void> {
    const db = await this.dbPromise;
    await db.put(FOLDER_PROPERTIES_STORE, properties);
  }

  async deleteFolderProperties(path: string): Promise<void> {
    const db = await this.dbPromise;
    await db.delete(FOLDER_PROPERTIES_STORE, path);
  }

  // --- Note Methods ---

  async getNote(path: string): Promise<Note | undefined> {
    const db = await this.dbPromise;
    return db.get(NOTES_STORE, path);
  }

  async saveNote(note: Note): Promise<void> {
    const db = await this.dbPromise;
    await db.put(NOTES_STORE, note);
  }

  async deleteNote(path: string): Promise<void> {
    const db = await this.dbPromise;
    await db.delete(NOTES_STORE, path);
  }

  async getAllNotes(): Promise<Note[]> {
    const db = await this.dbPromise;
    return db.getAll(NOTES_STORE);
  }
}
