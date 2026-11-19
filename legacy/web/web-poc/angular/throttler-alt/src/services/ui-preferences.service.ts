import { Injectable, signal, effect, computed } from '@angular/core';

const PREFERENCES_STORAGE_KEY = 'file-explorer-ui-preferences';

export type Theme = 'theme-light' | 'theme-steel' | 'theme-dark';
export type FontSize = 'sm' | 'base' | 'lg';

export interface UiPreferences {
  isSidebarVisible: boolean;
  isTreeVisible: boolean;
  isChatVisible: boolean;
  isNotesVisible: boolean;
  isDetailPaneOpen: boolean;
  isSavedItemsVisible: boolean;
  isRssFeedVisible: boolean;
  isStreamVisible: boolean;
  isConsoleCollapsed: boolean;
  isStreamPaneCollapsed: boolean;
  sidebarWidth: number | null;
  sidebarTreeHeight: number | null;
  sidebarChatHeight: number | null;
  splitViewPaneWidth: number | null;
  explorerStreamHeight: number | null;
  explorerConsoleHeight: number | null;
  detailPaneWidth: number | null;
  detailPaneSavedHeight: number | null;
  theme: Theme;
  fontSize: FontSize;
}

const DEFAULT_PREFERENCES: UiPreferences = {
  isSidebarVisible: true,
  isTreeVisible: true,
  isChatVisible: true,
  isNotesVisible: true,
  isDetailPaneOpen: true,
  isSavedItemsVisible: true,
  isRssFeedVisible: true,
  isStreamVisible: true,
  isConsoleCollapsed: false,
  isStreamPaneCollapsed: false,
  sidebarWidth: null,
  sidebarTreeHeight: null,
  sidebarChatHeight: null,
  splitViewPaneWidth: null,
  explorerStreamHeight: null,
  explorerConsoleHeight: 20,
  detailPaneWidth: null,
  detailPaneSavedHeight: null,
  theme: 'theme-light',
  fontSize: 'base',
};

@Injectable({
  providedIn: 'root',
})
export class UiPreferencesService {
  private preferences = signal<UiPreferences>(DEFAULT_PREFERENCES);

  // Public readonly signals for consumers
  public readonly isSidebarVisible = computed(() => this.preferences().isSidebarVisible);
  public readonly isTreeVisible = computed(() => this.preferences().isTreeVisible);
  public readonly isChatVisible = computed(() => this.preferences().isChatVisible);
  public readonly isNotesVisible = computed(() => this.preferences().isNotesVisible);
  public readonly isDetailPaneOpen = computed(() => this.preferences().isDetailPaneOpen);
  public readonly isSavedItemsVisible = computed(() => this.preferences().isSavedItemsVisible);
  public readonly isRssFeedVisible = computed(() => this.preferences().isRssFeedVisible);
  public readonly isStreamVisible = computed(() => this.preferences().isStreamVisible);
  public readonly isConsoleCollapsed = computed(() => this.preferences().isConsoleCollapsed);
  public readonly isStreamPaneCollapsed = computed(() => this.preferences().isStreamPaneCollapsed);
  public readonly sidebarWidth = computed(() => this.preferences().sidebarWidth);
  public readonly sidebarTreeHeight = computed(() => this.preferences().sidebarTreeHeight);
  public readonly sidebarChatHeight = computed(() => this.preferences().sidebarChatHeight);
  public readonly splitViewPaneWidth = computed(() => this.preferences().splitViewPaneWidth);
  public readonly explorerStreamHeight = computed(() => this.preferences().explorerStreamHeight);
  public readonly explorerConsoleHeight = computed(() => this.preferences().explorerConsoleHeight);
  public readonly detailPaneWidth = computed(() => this.preferences().detailPaneWidth);
  public readonly detailPaneSavedHeight = computed(() => this.preferences().detailPaneSavedHeight);
  public readonly theme = computed(() => this.preferences().theme);
  public readonly fontSize = computed(() => this.preferences().fontSize);
  public readonly currentPreferences = this.preferences.asReadonly();

  constructor() {
    this.loadPreferences();
    effect(() => {
      this.savePreferences();
    });
  }

  private loadPreferences(): void {
    try {
      const stored = localStorage.getItem(PREFERENCES_STORAGE_KEY);
      if (stored) {
        const parsed = JSON.parse(stored);
        // Basic validation to merge with defaults, preventing crashes if new keys are added
        const mergedPreferences = { ...DEFAULT_PREFERENCES, ...parsed };
        this.preferences.set(mergedPreferences);
        return;
      }
    } catch (e) {
      console.error('Failed to load UI preferences from localStorage', e);
    }
    // Set default if loading fails or nothing is stored
    this.preferences.set(DEFAULT_PREFERENCES);
  }

  private savePreferences(): void {
    try {
      localStorage.setItem(PREFERENCES_STORAGE_KEY, JSON.stringify(this.preferences()));
    } catch (e) {
      console.error('Failed to save UI preferences to localStorage', e);
    }
  }

  // --- Public methods to toggle visibility state ---
  toggleSidebar(): void {
    this.preferences.update(p => ({ ...p, isSidebarVisible: !p.isSidebarVisible }));
  }

  toggleTree(): void {
    this.preferences.update(p => ({ ...p, isTreeVisible: !p.isTreeVisible }));
  }

  toggleChat(): void {
    this.preferences.update(p => ({ ...p, isChatVisible: !p.isChatVisible }));
  }

  toggleNotes(): void {
    this.preferences.update(p => ({ ...p, isNotesVisible: !p.isNotesVisible }));
  }

  toggleDetailPane(): void {
    this.preferences.update(p => ({ ...p, isDetailPaneOpen: !p.isDetailPaneOpen }));
  }

  toggleSavedItems(): void {
    this.preferences.update(p => ({ ...p, isSavedItemsVisible: !p.isSavedItemsVisible }));
  }

  toggleRssFeed(): void {
    this.preferences.update(p => ({ ...p, isRssFeedVisible: !p.isRssFeedVisible }));
  }
  
  toggleStream(): void {
    this.preferences.update(p => ({ ...p, isStreamVisible: !p.isStreamVisible }));
  }

  toggleConsole(): void {
    this.preferences.update(p => ({ ...p, isConsoleCollapsed: !p.isConsoleCollapsed }));
  }

  toggleStreamPaneCollapse(): void {
    this.preferences.update(p => ({ ...p, isStreamPaneCollapsed: !p.isStreamPaneCollapsed }));
  }

  // --- Public methods to set pane dimensions ---
  setSidebarWidth(width: number): void {
    this.preferences.update(p => ({ ...p, sidebarWidth: width }));
  }

  setSidebarTreeHeight(height: number): void {
    this.preferences.update(p => ({ ...p, sidebarTreeHeight: height }));
  }

  setSidebarChatHeight(height: number): void {
    this.preferences.update(p => ({ ...p, sidebarChatHeight: height }));
  }

  setSplitViewPaneWidth(width: number): void {
    this.preferences.update(p => ({ ...p, splitViewPaneWidth: width }));
  }
  
  setExplorerStreamHeight(height: number): void {
    this.preferences.update(p => ({ ...p, explorerStreamHeight: height }));
  }
  
  setExplorerConsoleHeight(height: number): void {
    this.preferences.update(p => ({ ...p, explorerConsoleHeight: height }));
  }

  setDetailPaneWidth(width: number): void {
    this.preferences.update(p => ({ ...p, detailPaneWidth: width }));
  }

  setDetailPaneSavedHeight(height: number): void {
    this.preferences.update(p => ({ ...p, detailPaneSavedHeight: height }));
  }

  setTheme(theme: Theme): void {
    this.preferences.update(p => ({ ...p, theme }));
  }

  // --- Bulk update method for preferences dialog ---
  saveAllPreferences(newPrefs: Partial<UiPreferences>): void {
    this.preferences.update(current => ({ ...current, ...newPrefs }));
  }
}