import { Injectable, signal, effect, computed } from '@angular/core';

const CONFIG_STORAGE_KEY = 'file-explorer-local-config';

export interface LocalConfig {
  sessionName: string;
  defaultImageUrl: string;
  logBrokerMessages: boolean;
}

const DEFAULT_CONFIG: LocalConfig = {
  sessionName: 'Local Session',
  defaultImageUrl: 'http://localhost:9081', // A sensible default
  logBrokerMessages: true,
};

@Injectable({
  providedIn: 'root',
})
export class LocalConfigService {
  private config = signal<LocalConfig>(DEFAULT_CONFIG);

  public readonly sessionName = computed(() => this.config().sessionName);
  public readonly defaultImageUrl = computed(() => this.config().defaultImageUrl);
  public readonly currentConfig = this.config.asReadonly();

  constructor() {
    this.loadConfig();
    effect(() => {
      this.saveConfig();
    });
  }

  private loadConfig(): void {
    try {
      const stored = localStorage.getItem(CONFIG_STORAGE_KEY);
      if (stored) {
        const parsed = JSON.parse(stored);
        // Merge with defaults to handle missing properties from older versions
        const mergedConfig = { ...DEFAULT_CONFIG, ...parsed };
        this.config.set(mergedConfig);
        return;
      }
    } catch (e) {
      console.error('Failed to load local config from localStorage', e);
    }
    // Set default if loading fails or nothing is stored
    this.config.set(DEFAULT_CONFIG);
  }

  private saveConfig(): void {
    try {
      localStorage.setItem(CONFIG_STORAGE_KEY, JSON.stringify(this.config()));
    } catch (e) {
      console.error('Failed to save local config to localStorage', e);
    }
  }

  updateConfig(newConfig: Partial<LocalConfig>): void {
    this.config.update(current => ({ ...current, ...newConfig }));
  }
}
