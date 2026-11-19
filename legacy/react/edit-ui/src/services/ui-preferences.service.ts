import { Injectable, signal, computed } from '@angular/core';

const PREFERENCES_STORAGE_KEY = 'edit-ui-preferences';

export type Theme = 'theme-light' | 'theme-steel' | 'theme-dark';

export interface UiPreferences {
  theme: Theme;
  isFileTreeCollapsed: boolean;
  fileTreeWidth: number;
}

const DEFAULT_PREFERENCES: UiPreferences = {
  theme: 'theme-steel',
  isFileTreeCollapsed: false,
  fileTreeWidth: 280,
};

export interface FileTreeNode {
  name: string;
  path: string;
  type: 'file' | 'directory';
  children?: FileTreeNode[];
  childrenLoaded?: boolean;
}

@Injectable({ providedIn: 'root' })
export class UiPreferencesService {
  private preferences = signal<UiPreferences>(DEFAULT_PREFERENCES);

  readonly theme = computed(() => this.preferences().theme);
  readonly isFileTreeCollapsed = computed(() => this.preferences().isFileTreeCollapsed);
  readonly fileTreeWidth = computed(() => this.preferences().fileTreeWidth);

  constructor() {
    this.loadPreferences();
  }

  private loadPreferences(): void {
    try {
      const stored = localStorage.getItem(PREFERENCES_STORAGE_KEY);
      if (stored) {
        this.preferences.set({ ...DEFAULT_PREFERENCES, ...JSON.parse(stored) });
      }
    } catch (e) {
      console.error('Failed to load preferences', e);
    }
  }

  private savePreferences(): void {
    localStorage.setItem(PREFERENCES_STORAGE_KEY, JSON.stringify(this.preferences()));
  }

  setTheme(theme: Theme): void {
    this.preferences.update(p => ({ ...p, theme }));
    this.savePreferences();
  }

  toggleFileTree(): void {
    this.preferences.update(p => ({ ...p, isFileTreeCollapsed: !p.isFileTreeCollapsed }));
    this.savePreferences();
  }

  setFileTreeWidth(width: number): void {
    this.preferences.update(p => ({ ...p, fileTreeWidth: width }));
    this.savePreferences();
  }
}
