import { Injectable, signal, computed, effect, inject } from '@angular/core';
import { ServerProfile } from '../models/server-profile.model.js';
import { DbService } from './db.service.js';

const PROFILES_STORAGE_KEY = 'file-explorer-server-profiles';
const ACTIVE_PROFILE_ID_STORAGE_KEY = 'file-explorer-active-profile-id';

const DEFAULT_PROFILES: ServerProfile[] = [
  {
    id: 'default-local',
    name: 'Local (Debug)',
    brokerUrl: 'localhost:8080',
    imageUrl: 'http://localhost:9081',
    searchUrl: 'http://localhost:8082/search',
  },
];

@Injectable({
  providedIn: 'root',
})
export class ServerProfileService {
  private dbService = inject(DbService);
  profiles = signal<ServerProfile[]>([]);
  activeProfileId = signal<string | null>(null);

  activeProfile = computed<ServerProfile | null>(() => {
    const profiles = this.profiles();
    const activeId = this.activeProfileId();
    if (!activeId) return null;
    return profiles.find(p => p.id === activeId) ?? null;
  });

  activeConfig = computed<{ brokerUrl: string, imageUrl: string }>(() => {
    const active = this.activeProfile();
    if (active) {
      return { brokerUrl: active.brokerUrl, imageUrl: active.imageUrl };
    }
    // Fallback to default if no active profile is found (should not happen after init)
    return {
      brokerUrl: DEFAULT_PROFILES[0].brokerUrl,
      imageUrl: DEFAULT_PROFILES[0].imageUrl
    };
  });

  constructor() {
    this.loadProfiles();
    effect(() => {
      // This effect now ONLY persists the active profile ID to localStorage.
      try {
        const activeId = this.activeProfileId();
        if (activeId) {
          localStorage.setItem(ACTIVE_PROFILE_ID_STORAGE_KEY, activeId);
        } else {
          localStorage.removeItem(ACTIVE_PROFILE_ID_STORAGE_KEY);
        }
      } catch (e) {
        console.error('Failed to save active profile ID to localStorage', e);
      }
    });
  }

  private sortProfiles(profiles: ServerProfile[]): ServerProfile[] {
    return profiles.sort((a, b) => a.name.localeCompare(b.name, undefined, { sensitivity: 'base' }));
  }

  private async loadProfiles(): Promise<void> {
    try {
      let profiles = await this.dbService.getAllProfiles();

      if (profiles.length === 0) {
        // One-time migration from localStorage or use defaults
        const profilesJson = localStorage.getItem(PROFILES_STORAGE_KEY);
        const storedProfiles = profilesJson ? JSON.parse(profilesJson) : [];

        if (storedProfiles.length > 0) {
          profiles = storedProfiles;
          // Clean up old storage key after migration
          localStorage.removeItem(PROFILES_STORAGE_KEY);
        } else {
          profiles = DEFAULT_PROFILES;
        }

        // Populate IndexedDB with the determined profiles
        for (const profile of profiles) {
          await this.dbService.addProfile(profile);
        }
      }

      this.profiles.set(this.sortProfiles(profiles));

      const activeId = localStorage.getItem(ACTIVE_PROFILE_ID_STORAGE_KEY);
      if (activeId && this.profiles().some(p => p.id === activeId)) {
        this.activeProfileId.set(activeId);
      } else {
        // Set first profile as active if none is set or the stored one is invalid
        this.activeProfileId.set(this.profiles()[0]?.id ?? null);
      }
    } catch (e) {
      console.error('Failed to load profiles from IndexedDB', e);
      this.profiles.set(this.sortProfiles([...DEFAULT_PROFILES]));
      this.activeProfileId.set(DEFAULT_PROFILES[0]?.id ?? null);
    }
  }

  private generateId(): string {
    return `profile-${Date.now()}-${Math.random().toString(36).substring(2, 9)}`;
  }

  async addProfile(profileData: Omit<ServerProfile, 'id'>): Promise<void> {
    const newProfile: ServerProfile = { ...profileData, id: this.generateId() };
    await this.dbService.addProfile(newProfile);
    this.profiles.update(profiles => this.sortProfiles([...profiles, newProfile]));
  }

  async updateProfile(updatedProfile: ServerProfile): Promise<void> {
    await this.dbService.updateProfile(updatedProfile);
    this.profiles.update(profiles =>
      this.sortProfiles(profiles.map(p => p.id === updatedProfile.id ? updatedProfile : p))
    );
  }

  async deleteProfile(id: string): Promise<void> {
    await this.dbService.deleteProfile(id);
    this.profiles.update(profiles => profiles.filter(p => p.id !== id));
    if (this.activeProfileId() === id) {
      // If the active profile was deleted, set the first one as active
      this.activeProfileId.set(this.profiles()[0]?.id ?? null);
    }
  }

  setActiveProfile(id: string): void {
    if (this.profiles().some(p => p.id === id)) {
      this.activeProfileId.set(id);
    }
  }
}
