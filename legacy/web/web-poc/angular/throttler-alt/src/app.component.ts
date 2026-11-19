import { Component, ChangeDetectionStrategy, signal, computed, inject, effect, Renderer2, ElementRef, OnDestroy, Injector, OnInit, ViewChild } from '@angular/core';
import { CommonModule, DOCUMENT } from '@angular/common';
import { FileExplorerComponent } from './components/file-explorer/file-explorer.component.js';
import { SidebarComponent } from './components/sidebar/sidebar.component.js';
import { FileSystemNode } from './models/file-system.model.js';
import { FileSystemProvider, ItemReference } from './services/file-system-provider.js';
import { ServerProfilesDialogComponent } from './components/server-profiles-dialog/server-profiles-dialog.component.js';
import { ServerProfileService } from './services/server-profile.service.js';
import { DetailPaneComponent } from './components/detail-pane/detail-pane.component.js';
import { SessionService } from './services/in-memory-file-system.service.js';
import { ServerProfile } from './models/server-profile.model.js';
import { RemoteFileSystemService } from './services/remote-file-system.service.js';
import { FsService } from './services/fs.service.js';
import { ImageService } from './services/image.service.js';
import { ImageClientService } from './services/image-client.service.js';
import { LoginService } from './services/login.service.js';
import { User } from './models/user.model.js';
import { PreferencesService } from './services/preferences.service.js';
import { DragDropPayload } from './services/drag-drop.service.js';
import { ToolbarComponent, SortCriteria } from './components/toolbar/toolbar.component.js';
import { ClipboardService } from './services/clipboard.service.js';
import { BookmarkService } from './services/bookmark.service.js';
import { NewBookmark, Bookmark } from './models/bookmark.model.js';
import { ToastsComponent } from './components/toasts/toasts.component.js';
import { ToastService } from './services/toast.service.js';
import { WebviewDialogComponent } from './components/webview-dialog/webview-dialog.component.js';
import { WebviewService } from './services/webview.service.js';
import { LocalConfigDialogComponent } from './components/local-config-dialog/local-config-dialog.component.js';
import { LocalConfig, LocalConfigService } from './services/local-config.service.js';
import { LoginDialogComponent } from './components/login-dialog/login-dialog.component.js';
import { Theme, UiPreferences, UiPreferencesService } from './services/ui-preferences.service.js';
import { RssFeedsDialogComponent } from './components/rss-feeds-dialog/rss-feeds-dialog.component.js';
import { ImportDialogComponent } from './components/import-dialog/import-dialog.component.js';
import { ExportDialogComponent } from './components/export-dialog/export-dialog.component.js';
import { FolderPropertiesService } from './services/folder-properties.service.js';
import { TextEditorService } from './services/note-dialog.service.js';
import { TextEditorDialogComponent } from './components/note-view-dialog/note-view-dialog.component.js';
import { DbService } from './services/db.service.js';
import { GoogleSearchService } from './services/google-search.service.js';
import { UnsplashService } from './services/unsplash.service.js';
import { GeminiService } from './services/gemini.service.js';
import { YoutubeSearchService } from './services/youtube-search.service.js';
import { AcademicSearchService } from './services/academic-search.service.js';
import { GoogleSearchResult } from './models/google-search-result.model.js';
import { ImageSearchResult } from './models/image-search-result.model.js';
import { YoutubeSearchResult } from './models/youtube-search-result.model.js';
import { AcademicSearchResult } from './models/academic-search-result.model.js';
import { WebResultCardComponent } from './components/stream-cards/web-result-card.component.js';
import { ImageResultCardComponent } from './components/stream-cards/image-result-card.component.js';
import { GeminiResultCardComponent } from './components/stream-cards/gemini-result-card.component.js';
import { YoutubeResultCardComponent } from './components/stream-cards/youtube-result-card.component.js';
import { AcademicResultCardComponent } from './components/stream-cards/academic-result-card.component.js';
import { WebResultListItemComponent } from './components/stream-list-items/web-result-list-item.component.js';
import { ImageResultListItemComponent } from './components/stream-list-items/image-result-list-item.component.js';
import { GeminiResultListItemComponent } from './components/stream-list-items/gemini-result-list-item.component.js';
import { YoutubeResultListItemComponent } from './components/stream-list-items/youtube-result-list-item.component.js';
import { AcademicResultListItemComponent } from './components/stream-list-items/academic-result-list-item.component.js';
import { PreferencesDialogComponent } from './components/preferences-dialog/preferences-dialog.component.js';
import { TerminalComponent } from './components/terminal/terminal.component.js';
import { SearchService } from './services/search.service.js';

interface PanePath {
  id: number;
  path: string[];
}
interface PaneStatus {
  selectedItemsCount: number;
  totalItemsCount: number;
  filteredItemsCount: number | null;
}
type ConnectionStatus = 'connecting' | 'connected' | 'disconnected';

// Type definitions for items in the unified stream
type GeminiResult = { query: string; text: string; publishedAt: string; };

type StreamItem = 
  | (GoogleSearchResult & { type: 'web' })
  | (ImageSearchResult & { type: 'image' })
  | (YoutubeSearchResult & { type: 'youtube' })
  | (AcademicSearchResult & { type: 'academic' })
  | (GeminiResult & { type: 'gemini' });

type StreamItemType = 'web' | 'image' | 'youtube' | 'academic' | 'gemini';
type StreamSortKey = 'relevance' | 'title' | 'source' | 'date';
interface StreamSortCriteria {
  key: StreamSortKey;
  direction: 'asc' | 'desc';
}

const readOnlyProviderOps = {
  createDirectory: () => Promise.reject(new Error('Operation not supported.')),
  removeDirectory: () => Promise.reject(new Error('Operation not supported.')),
  createFile: () => Promise.reject(new Error('Operation not supported.')),
  deleteFile: () => Promise.reject(new Error('Operation not supported.')),
  rename: () => Promise.reject(new Error('Operation not supported.')),
  uploadFile: () => Promise.reject(new Error('Operation not supported.')),
  move: () => Promise.reject(new Error('Operation not supported.')),
  copy: () => Promise.reject(new Error('Operation not supported.')),
  importTree: () => Promise.reject(new Error('Operation not supported.')),
  getFileContent: () => Promise.reject(new Error('Operation not supported.')),
  saveFileContent: () => Promise.reject(new Error('Operation not supported.')),
  hasFile: () => Promise.resolve(false),
  hasFolder: () => Promise.resolve(false),
};

@Component({
  selector: 'app-root',
  templateUrl: './app.component.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [CommonModule, FileExplorerComponent, SidebarComponent, ServerProfilesDialogComponent, DetailPaneComponent, ToolbarComponent, ToastsComponent, WebviewDialogComponent, LocalConfigDialogComponent, LoginDialogComponent, RssFeedsDialogComponent, ImportDialogComponent, ExportDialogComponent, TextEditorDialogComponent, WebResultCardComponent, ImageResultCardComponent, GeminiResultCardComponent, YoutubeResultCardComponent, AcademicResultCardComponent, WebResultListItemComponent, ImageResultListItemComponent, GeminiResultListItemComponent, YoutubeResultListItemComponent, AcademicResultListItemComponent, PreferencesDialogComponent, TerminalComponent],
  host: {
    '(document:keydown)': 'onKeyDown($event)',
    '(document:click)': 'onDocumentClick($event)',
  }
})
export class AppComponent implements OnInit, OnDestroy {
  private sessionFs = inject(SessionService);
  private profileService = inject(ServerProfileService);
  private localConfigService = inject(LocalConfigService);
  private fsService = inject(FsService);
  private imageClientService = inject(ImageClientService);
  private loginService = inject(LoginService);
  private preferencesService = inject(PreferencesService);
  private clipboardService = inject(ClipboardService);
  private bookmarkService = inject(BookmarkService);
  private toastService = inject(ToastService);
  private webviewService = inject(WebviewService);
  private textEditorService = inject(TextEditorService);
  private folderPropertiesService = inject(FolderPropertiesService);
  private injector = inject(Injector);
  private document: Document = inject(DOCUMENT);
  private renderer = inject(Renderer2);
  private elementRef = inject(ElementRef);
  private dbService = inject(DbService);
  private uiPreferencesService = inject(UiPreferencesService);
  private homeProvider: FileSystemProvider;
  private googleSearchService = inject(GoogleSearchService);
  private unsplashService = inject(UnsplashService);
  private geminiService = inject(GeminiService);
  private youtubeSearchService = inject(YoutubeSearchService);
  private academicSearchService = inject(AcademicSearchService);
  private searchService = inject(SearchService);

  // --- State Management ---
  isSplitView = signal(false);
  activePaneId = signal(1);
  folderTree = signal<FileSystemNode | null>(null);
  isServerProfilesDialogOpen = signal(false);
  isLocalConfigDialogOpen = signal(false);
  isRssFeedsDialogOpen = signal(false);
  isImportDialogOpen = signal(false);
  isExportDialogOpen = signal(false);
  isPreferencesDialogOpen = signal(false);
  selectedDetailItem = signal<FileSystemNode | null>(null);
  connectionStatus = signal<ConnectionStatus>('disconnected');
  refreshPanes = signal(0);
  
  // --- Pane Visibility State (from service) ---
  isSidebarVisible = this.uiPreferencesService.isSidebarVisible;
  isTreeVisible = this.uiPreferencesService.isTreeVisible;
  isChatVisible = this.uiPreferencesService.isChatVisible;
  isNotesVisible = this.uiPreferencesService.isNotesVisible;
  isDetailPaneOpen = this.uiPreferencesService.isDetailPaneOpen;
  isSavedItemsVisible = this.uiPreferencesService.isSavedItemsVisible;
  isRssFeedVisible = this.uiPreferencesService.isRssFeedVisible;
  isStreamVisible = this.uiPreferencesService.isStreamVisible;
  isConsoleCollapsed = this.uiPreferencesService.isConsoleCollapsed;
  isStreamPaneCollapsed = this.uiPreferencesService.isStreamPaneCollapsed;
  
  // Keep track of each pane's path
  private panePaths = signal<PanePath[]>([{ id: 1, path: [] }]);

  // --- Dialog Control State ---
  profileForLogin = signal<ServerProfile | null>(null);
  profileForEdit = signal<ServerProfile | null>(null);

  // --- Mounted Profile State ---
  mountedProfiles = signal<ServerProfile[]>([]);
  mountedProfileUsers = signal<Map<string, User>>(new Map());
  mountedProfileIds = computed(() => this.mountedProfiles().map(p => p.id));
  private remoteProviders = signal<Map<string, RemoteFileSystemService>>(new Map());
  private remoteImageServices = signal<Map<string, ImageService>>(new Map());
  
  // --- Status Bar State ---
  private pane1Status = signal<PaneStatus>({ selectedItemsCount: 0, totalItemsCount: 0, filteredItemsCount: null });
  private pane2Status = signal<PaneStatus>({ selectedItemsCount: 0, totalItemsCount: 0, filteredItemsCount: null });
  
  activePaneStatus = computed<PaneStatus>(() => {
    const activeId = this.activePaneId();
    if (activeId === 1) {
      return this.pane1Status();
    }
    return this.pane2Status();
  });
  
  statusBarSelectionInfo = computed(() => {
    const item = this.selectedDetailItem();
    if (!item) {
        return 'Ready';
    }

    if (item.isServerRoot) {
        const profile = this.profileService.profiles().find(p => p.name === item.name);
        if (profile) {
            return `Server Profile: ${profile.name} | Broker: ${profile.brokerUrl}`;
        }
    }
    
    const itemType = item.type.charAt(0).toUpperCase() + item.type.slice(1);
    return `${itemType}: ${item.name}`;
  });

  // --- Theme Management ---
  currentTheme = this.uiPreferencesService.theme;
  themes: {id: Theme, name: string}[] = [
    { id: 'theme-light', name: 'Light' },
    { id: 'theme-steel', name: 'Steel' },
    { id: 'theme-dark', name: 'Dark' },
  ];
  isThemeDropdownOpen = signal(false);
  themeMenuPosition = signal({ top: '0px', left: '0px' });

  activePanePath = computed(() => {
    const activeId = this.activePaneId();
    const activePane = this.panePaths().find(p => p.id === activeId);
    return activePane ? activePane.path : [];
  });

  // --- Address Bar State ---
  activeRootName = signal('...');
  activeDisplayPath = computed(() => {
    const p = this.activePanePath();
    const providerPath = p.length > 0 ? p.slice(1) : [];
    return providerPath.map(segment => 
      segment.endsWith('.magnet') ? segment.slice(0, -7) : segment
    );
  });
  canGoUpActivePane = computed(() => this.activePanePath().length > 1);
  
  pane1Path = computed(() => this.panePaths().find(p => p.id === 1)?.path ?? []);
  pane2Path = computed(() => this.panePaths().find(p => p.id === 2)?.path ?? []);

  // --- Pane Resizing State ---
  pane1Width = signal(this.uiPreferencesService.splitViewPaneWidth() ?? 50);
  isResizingPanes = signal(false);
  
  @ViewChild('paneContainer') paneContainerEl!: ElementRef<HTMLDivElement>;
  @ViewChild('mainContentWrapper') mainContentWrapperEl!: ElementRef<HTMLDivElement>;

  // --- Resizer Event Listeners ---
  private unlistenPaneMouseMove: (() => void) | null = null;
  private unlistenBottomPaneMouseMove: (() => void) | null = null;
  private unlistenConsolePaneMouseMove: (() => void) | null = null;
  private unlistenGlobalMouseUp: (() => void) | null = null;

  // --- Computed Per-Pane Services ---
  public getProviderForPath(path: string[]): FileSystemProvider {
    if (path.length === 0) return this.homeProvider;
    const root = path[0];
    if (root === this.localConfigService.sessionName()) return this.sessionFs;
    const remoteProvider = this.remoteProviders().get(root);
    if (remoteProvider) return remoteProvider;
    // For unmounted providers, we can return a dummy read-only one.
    // This allows operations like getting the name to succeed without being connected.
    if (this.profileService.profiles().some(p => p.name === root)) {
      return {
        ...readOnlyProviderOps,
        getFolderTree: () => Promise.resolve({ name: root, type: 'folder' }),
        getContents: () => Promise.resolve([])
      };
    }
    throw new Error(`No provider found for path: ${path.join('/')}`);
  }
  public getProvider: (path: string[]) => FileSystemProvider;
  
  public getImageServiceForPath(path: string[]): ImageService {
    if (path.length === 0) {
      const homeProfileStub = {
          id: 'home-view',
          name: 'Home',
          brokerUrl: '',
          imageUrl: this.localConfigService.defaultImageUrl()
      };
      return new ImageService(homeProfileStub, this.imageClientService, this.preferencesService);
    }
    const root = path[0];
    const remoteService = this.remoteImageServices().get(root);
    if (remoteService) return remoteService;
    
    // For local session
    if (root === this.localConfigService.sessionName()) {
      const localProfileStub = {
          id: 'local-session',
          name: this.localConfigService.sessionName(),
          brokerUrl: '',
          imageUrl: this.localConfigService.defaultImageUrl()
      };
      return new ImageService(localProfileStub, this.imageClientService, this.preferencesService);
    }

    // For unmounted remote profiles, create a temporary image service with fallback.
    const profile = this.profileService.profiles().find(p => p.name === root);
    if (profile) {
        const imageUrl = profile.imageUrl || this.localConfigService.defaultImageUrl();
        const tempProfileWithImage = { ...profile, imageUrl };
        return new ImageService(tempProfileWithImage, this.imageClientService, this.preferencesService);
    }
    return this.defaultImageService;
  }
  public getImageService: (path: string[]) => ImageService;

  private defaultImageService = new ImageService({ id: 'temp', name: 'Temp', brokerUrl: '', imageUrl: '' }, this.imageClientService, this.preferencesService);

  pane1Provider = computed(() => this.getProviderForPath(this.pane1Path()));
  pane2Provider = computed(() => this.getProviderForPath(this.pane2Path()));
  pane1ImageService = computed(() => this.getImageServiceForPath(this.pane1Path()));
  pane2ImageService = computed(() => this.getImageServiceForPath(this.pane2Path()));
  
  activeProvider = computed(() => {
    const path = this.activePanePath();
    return this.getProviderForPath(path);
  });

  activeProviderPath = computed(() => {
    const path = this.activePanePath();
    return path.length > 0 ? path.slice(1) : [];
  });

  // --- Global Toolbar State ---
  toolbarAction = signal<{ name: string, payload?: any, id: number } | null>(null);
  
  pane1SortCriteria = signal<SortCriteria>({ key: 'name', direction: 'asc' });
  pane2SortCriteria = signal<SortCriteria>({ key: 'name', direction: 'asc' });
  activeSortCriteria = computed(() => this.activePaneId() === 1 ? this.pane1SortCriteria() : this.pane2SortCriteria());

  pane1DisplayMode = signal<'grid' | 'list'>('grid');
  pane2DisplayMode = signal<'grid' | 'list'>('grid');
  activeDisplayMode = computed(() => this.activePaneId() === 1 ? this.pane1DisplayMode() : this.pane2DisplayMode());
  
  pane1FilterQuery = signal('');
  pane2FilterQuery = signal('');
  activeFilterQuery = computed(() => this.activePaneId() === 1 ? this.pane1FilterQuery() : this.pane2FilterQuery());

  canCutCopyShareDelete = computed(() => this.activePaneStatus().selectedItemsCount > 0);
  canRename = computed(() => this.activePaneStatus().selectedItemsCount === 1);
  canPaste = computed(() => !!this.clipboardService.clipboard());

  // --- Webview Dialog State ---
  webviewContent = this.webviewService.viewRequest;

  // --- Text Editor Dialog State ---
  textEditorContent = this.textEditorService.viewRequest;

  // --- Specific node for import/export dialogs ---
  localSessionNode = computed(() => {
    const sessionName = this.localConfigService.sessionName();
    return this.folderTree()?.children?.find(c => c.name === sessionName) ?? null;
  });

  // --- Idea Stream State ---
  bottomPaneHeight = signal(this.uiPreferencesService.explorerStreamHeight() ?? 40);
  isResizingBottomPane = signal(false);
  private streamResizeInitialConsoleHeight = 0;

  // --- Console Pane State ---
  consolePaneHeight = signal(this.uiPreferencesService.explorerConsoleHeight() ?? 20);
  isResizingConsolePane = signal(false);
  
  private streamResults1 = signal<StreamItem[]>([]);
  private streamResults2 = signal<StreamItem[]>([]);
  streamSourceToggle = signal<'all' | 'active' | 'left' | 'right'>('active');
  streamSearchQuery = signal('');
  streamSortCriteria = signal<StreamSortCriteria>({ key: 'relevance', direction: 'asc' });
  activeStreamFilters = signal<Set<StreamItemType>>(new Set(['web', 'image', 'youtube', 'academic', 'gemini']));
  isStreamSortDropdownOpen = signal(false);
  streamDisplayMode = signal<'grid' | 'list'>('grid');

  streamFilterTypes: { type: StreamItemType, label: string, iconPath: string }[] = [
    { type: 'web', label: 'Web', iconPath: 'M10 2a8 8 0 100 16 8 8 0 000-16zM2 10a8 8 0 0113.167-5.321l-1.334.667A6 6 0 1010 16a6 6 0 005.321-3.167l.667-1.334A8.001 8.001 0 0110 18 8 8 0 012 10z' },
    { type: 'image', label: 'Images', iconPath: 'M3 4a1 1 0 011-1h12a1 1 0 011 1v12a1 1 0 01-1 1H4a1 1 0 01-1-1V4zM3 3a2 2 0 00-2 2v12a2 2 0 002 2h12a2 2 0 002-2V5a2 2 0 00-2-2H3zm13.5 11.5l-3-3a1 1 0 00-1.414 0l-1.586 1.586-1.086-1.086a1 1 0 00-1.414 0l-3 3a1 1 0 001.414 1.414L8 14.414l1.086 1.086a1 1 0 001.414 0l1.586-1.586 2.586 2.586a1 1 0 001.414-1.414zM8 8a1 1 0 100-2 1 1 0 000 2z' },
    { type: 'youtube', label: 'Videos', iconPath: 'M10 18a8 8 0 100-16 8 8 0 000 16zM9.555 7.168A1 1 0 008 8v4a1 1 0 001.555.832l3-2a1 1 0 000-1.664l-3-2z' },
    { type: 'academic', label: 'Academic', iconPath: 'M9 4h6a2 2 0 012 2v12l-8-4-8 4V6a2 2 0 012-2h6zM9 4v12l6-3V6a1 1 0 00-1-1H9z' },
    { type: 'gemini', label: 'Gemini', iconPath: 'M11.3 1.046A1 1 0 0112 2v5h4a1 1 0 01.82 1.573l-7 10A1 1 0 018 18v-5H4a1 1 0 01-.82-1.573l7-10a1 1 0 011.12-.38z' }
  ];
  
  bookmarkedLinks = this.bookmarkService.bookmarkedLinks;

  private visibleStreamResults = computed<StreamItem[]>(() => {
    if (!this.isSplitView()) {
      return this.streamResults1();
    }
    
    switch (this.streamSourceToggle()) {
      case 'active':
        return this.activePaneId() === 1 ? this.streamResults1() : this.streamResults2();
      case 'left':
        return this.streamResults1();
      case 'right':
        return this.streamResults2();
      case 'all':
        const all = [...this.streamResults1(), ...this.streamResults2()];
        const seen = new Set<string>();
        return all.filter(item => {
          const link = this.getStreamItemLink(item);
          if (seen.has(link)) {
            return false;
          } else {
            seen.add(link);
            return true;
          }
        });
      default:
        return [];
    }
  });

  processedStreamResults = computed(() => {
    let items = [...this.visibleStreamResults()];
    const query = this.streamSearchQuery().toLowerCase().trim();
    const filters = this.activeStreamFilters();
    const { key: sortKey, direction } = this.streamSortCriteria();

    // 1. Filter by type
    if (filters.size < this.streamFilterTypes.length) {
      items = items.filter(item => filters.has(item.type));
    }
    
    // 2. Filter by search query
    if (query) {
      items = items.filter(item => {
        const title = 'title' in item ? item.title : ('query' in item ? item.query : '');
        const content = 'snippet' in item ? item.snippet : ('description' in item ? item.description : ('text' in item ? item.text : ''));
        return title.toLowerCase().includes(query) || (content && content.toLowerCase().includes(query));
      });
    }

    // 3. Sort
    if (sortKey !== 'relevance') {
      const sortDirection = direction === 'asc' ? 1 : -1;
      items.sort((a, b) => {
        if (sortKey === 'date') {
          const dateA = new Date('publishedAt' in a && a.publishedAt ? a.publishedAt : 0).getTime();
          const dateB = new Date('publishedAt' in b && b.publishedAt ? b.publishedAt : 0).getTime();
          // For date, asc is oldest first, desc is newest first
          return (dateA - dateB) * sortDirection;
        } else {
          const valA = this.getSortableValue(a, sortKey);
          const valB = this.getSortableValue(b, sortKey);
          if (valA < valB) return -1 * sortDirection;
          if (valA > valB) return 1 * sortDirection;
          return 0;
        }
      });
    }
    
    return items;
  });

  private getSortableValue(item: StreamItem, key: 'title' | 'source'): string {
    const lowerKey = key.toLowerCase();
    switch (lowerKey) {
        case 'title':
            if ('title' in item) return item.title.toLowerCase();
            if ('query' in item) return `Gemini: ${item.query}`.toLowerCase();
            if ('description' in item) return item.description.toLowerCase();
            return '';
        case 'source':
            if ('source' in item) return item.source.toLowerCase();
            if ('channelTitle' in item) return item.channelTitle.toLowerCase();
            if ('publication' in item) return item.publication.toLowerCase();
            return 'gemini';
        default:
            return '';
    }
  }

  constructor() {
    this.getProvider = this.getProviderForPath.bind(this);
    this.getImageService = this.getImageServiceForPath.bind(this);

    const homeNotePath = '__HOME_NOTE__';

    this.homeProvider = {
      getContents: async (path: string[]) => {
        if (path.length > 0) return [];

        const localRoot = await this.sessionFs.getFolderTree();
        const serverProfileNodes: FileSystemNode[] = this.profileService.profiles().map(p => ({
            name: p.name,
            type: 'folder',
            children: [],
            childrenLoaded: false,
            isServerRoot: true,
            profileId: p.id,
            connected: this.mountedProfileIds().includes(p.id)
        }));
        
        return [localRoot, ...serverProfileNodes];
      },
      getFolderTree: async () => {
        const children = await this.homeProvider.getContents([]);
        return { name: 'Home', type: 'folder', children };
      },
      ...readOnlyProviderOps,
      hasFile: (path: string[], fileName: string) => Promise.resolve(false),
      hasFolder: async (path: string[], folderName: string) => {
        if (path.length > 0) return false;
        const localRoot = await this.sessionFs.getFolderTree();
        if (localRoot.name === folderName) return true;
        
        const serverProfileExists = this.profileService.profiles().some(p => p.name === folderName);
        return serverProfileExists;
      },
      getNote: async (path: string[]): Promise<string | undefined> => {
        if (path.length > 0) return undefined; 
        const note = await this.dbService.getNote(homeNotePath);
        return note?.content;
      },
      saveNote: async (path: string[], content: string): Promise<void> => {
        if (path.length > 0) {
          throw new Error('Operation not supported.');
        }
        await this.dbService.saveNote({ path: homeNotePath, content });
      }
    };
    
    effect(() => {
      const provider = this.activeProvider();
      if (provider) {
        provider.getFolderTree()
          .then(root => this.activeRootName.set(root.name))
          .catch(err => {
            console.error('Failed to get root name for active pane', err);
            this.activeRootName.set('Error');
          });
      }
    }, { allowSignalWrites: true });

    effect(() => {
      const theme = this.currentTheme();
      // This is more robust as it doesn't wipe out other potential body classes.
      this.themes.forEach(t => this.renderer.removeClass(this.document.body, t.id));
      this.renderer.addClass(this.document.body, theme);
    });

    effect(() => {
      const fontSize = this.uiPreferencesService.fontSize();
      // Remove any existing font size classes
      this.renderer.removeClass(this.document.body, 'text-sm');
      this.renderer.removeClass(this.document.body, 'text-base');
      this.renderer.removeClass(this.document.body, 'text-lg');
      // Add the new one
      this.renderer.addClass(this.document.body, `text-${fontSize}`);
    });
    
    // When profiles change, or local config name changes, reload the tree
    effect(() => {
        this.profileService.profiles();
        this.localConfigService.sessionName();
        this.mountedProfileIds();
        this.loadFolderTree();
    });

    effect(() => {
      // Sync the stream's display mode with the main display mode from the toolbar.
      this.streamDisplayMode.set(this.activeDisplayMode());
    }, { allowSignalWrites: true });

    // Set up a single, global mouseup listener to handle all resize cleanup.
    this.unlistenGlobalMouseUp = this.renderer.listen('document', 'mouseup', this.stopAllResizing.bind(this));
  }
  
  ngOnInit(): void {
    this.loadFolderTree();
    this.autoMountProfiles();
    this.loadAllStreamResults('Angular', 'TypeScript');
  }

  ngOnDestroy(): void {
    this.renderer.removeClass(this.document.body, this.currentTheme());
    this.stopAllResizing();
    if (this.unlistenGlobalMouseUp) {
      this.unlistenGlobalMouseUp();
    }
  }

  private async loadAllStreamResults(query1: string, query2: string): Promise<void> {
    const imageQuery1 = 'Technology';
    const imageQuery2 = 'Nature';

    const [google1, images1, gemini1, youtube1, academic1] = await Promise.all([
      this.googleSearchService.search(query1),
      this.unsplashService.search(imageQuery1),
      this.geminiService.search(query1),
      this.youtubeSearchService.search(query1),
      this.academicSearchService.search(query1)
    ]);
    this.streamResults1.set(this.interleaveResults(google1, images1, gemini1, youtube1, academic1, query1));

    const [google2, images2, gemini2, youtube2, academic2] = await Promise.all([
      this.googleSearchService.search(query2),
      this.unsplashService.search(imageQuery2),
      this.geminiService.search(query2),
      this.youtubeSearchService.search(query2),
      this.academicSearchService.search(query2)
    ]);
    this.streamResults2.set(this.interleaveResults(google2, images2, gemini2, youtube2, academic2, query2));
  }

  private interleaveResults(google: GoogleSearchResult[], images: ImageSearchResult[], gemini: string, youtube: YoutubeSearchResult[], academic: AcademicSearchResult[], query: string): StreamItem[] {
    const webResults: StreamItem[] = google.map(r => ({ ...r, type: 'web' }));
    const imageResults: StreamItem[] = images.map(r => ({ ...r, type: 'image' }));
    const youtubeResults: StreamItem[] = youtube.map(r => ({ ...r, type: 'youtube' }));
    const academicResults: StreamItem[] = academic.map(r => ({ ...r, type: 'academic' }));
    const geminiResult: StreamItem = { query, text: gemini, type: 'gemini', publishedAt: new Date().toISOString() };

    // Interleave results for a mixed stream
    const interleaved: StreamItem[] = [];
    const maxLength = Math.max(webResults.length, imageResults.length, youtubeResults.length, academicResults.length);

    // Start with Gemini as a hero item
    interleaved.push(geminiResult);
    
    for (let i = 0; i < maxLength; i++) {
        if (webResults[i]) interleaved.push(webResults[i]);
        if (imageResults[i]) interleaved.push(imageResults[i]);
        if (youtubeResults[i]) interleaved.push(youtubeResults[i]);
        if (academicResults[i]) interleaved.push(academicResults[i]);
    }
    
    return interleaved;
  }

  setTheme(theme: Theme): void {
    this.uiPreferencesService.setTheme(theme);
    this.isThemeDropdownOpen.set(false);
  }

  openThemeMenu(event: MouseEvent): void {
    event.stopPropagation();
    const target = event.currentTarget as HTMLElement;
    const rect = target.getBoundingClientRect();
    this.themeMenuPosition.set({
        top: `${rect.bottom + 4}px`,
        left: `${rect.left}px`,
    });
    this.isThemeDropdownOpen.set(true);
  }
  
  onDocumentClick(event: Event): void {
    const target = event.target as Node;
    if (this.isThemeDropdownOpen()) {
        const themeMenu = this.elementRef.nativeElement.querySelector('.theme-menu');
        if (themeMenu && !themeMenu.contains(target)) {
            this.isThemeDropdownOpen.set(false);
        }
    }
    if (this.isStreamSortDropdownOpen()) {
        const streamSortMenu = this.elementRef.nativeElement.querySelector('.stream-sort-menu');
        if (streamSortMenu && !streamSortMenu.contains(target)) {
            this.isStreamSortDropdownOpen.set(false);
        }
    }
  }

  async loadFolderTree(): Promise<void> {
    this.folderTree.set(null);
    
    try {
      const homeRoot = await this.homeProvider.getFolderTree();
      this.folderTree.set(homeRoot);
      this.triggerRefresh();
    } catch (e) {
      console.error('Failed to load a complete folder tree', e);
    }
  }
  
  private async autoMountProfiles(): Promise<void> {
    const profilesToMount = this.profileService.profiles().filter(p => p.autoConnect);
    if (profilesToMount.length === 0) return;

    this.connectionStatus.set('connecting');
    const mountPromises = profilesToMount.map(p => this.mountProfile(p));
    const results = await Promise.allSettled(mountPromises);
    const hasSuccessfulMount = results.some(r => r.status === 'fulfilled');

    if (hasSuccessfulMount) {
      this.connectionStatus.set('connected');
    } else {
      this.connectionStatus.set('disconnected');
    }
  }
  
  private async mountProfile(profile: ServerProfile, user: User | null = null): Promise<void> {
    if (this.mountedProfiles().some(p => p.id === profile.id)) return;

    try {
      const userForProfile = user ?? this.mountedProfileUsers().get(profile.id);
      const provider = new RemoteFileSystemService(profile, this.fsService, this.searchService, userForProfile?.alias ?? null);
      const imageUrl = profile.imageUrl || this.localConfigService.defaultImageUrl();
      const profileForImageService = { ...profile, imageUrl };
      const imageService = new ImageService(profileForImageService, this.imageClientService, this.preferencesService);

      await provider.getFolderTree();

      this.remoteProviders.update(map => new Map(map).set(profile.name, provider));
      this.remoteImageServices.update(map => new Map(map).set(profile.name, imageService));
      this.mountedProfiles.update(profiles => [...profiles, profile]);
      this.profileService.setActiveProfile(profile.id);
    } catch (e) {
      console.error(`Failed to mount profile "${profile.name}":`, e);
      throw e;
    }
  }
  
  async onLoginAndMount({ profile, username, password }: { profile: ServerProfile, username: string, password: string }): Promise<void> {
    this.connectionStatus.set('connecting');
    try {
      const user = await this.loginService.login(profile, username, password);
      this.mountedProfileUsers.update(map => new Map(map).set(profile.id, user));
      await this.mountProfile(profile, user);
      this.connectionStatus.set('connected');
    } catch (e) {
      const errorMessage = `Login failed for "${profile.name}": ${(e as Error).message}`;
      this.toastService.show(errorMessage, 'error', 8000);
      if (this.mountedProfiles().length === 0) {
        this.connectionStatus.set('disconnected');
      } else {
        this.connectionStatus.set('connected');
      }
    }
  }

  onLoginSubmittedFromSidebar({ username, password }: { username: string, password: string }): void {
    const profile = this.profileForLogin();
    if (profile) {
      this.onLoginAndMount({ profile, username, password });
    }
    this.profileForLogin.set(null); // Close dialog on submit
  }

  onUnmountProfile(profile: ServerProfile): void {
    this.remoteProviders.update(map => {
      const newMap = new Map(map);
      newMap.delete(profile.name);
      return newMap;
    });
    this.remoteImageServices.update(map => {
        const newMap = new Map(map);
        newMap.delete(profile.name);
        return newMap;
    });
    this.mountedProfiles.update(profiles => {
      const remainingProfiles = profiles.filter(p => p.id !== profile.id);
      if (remainingProfiles.length === 0) {
        this.connectionStatus.set('disconnected');
      }
      return remainingProfiles;
    });
    this.mountedProfileUsers.update(map => {
      const newMap = new Map(map);
      newMap.delete(profile.id);
      return newMap;
    });
  }
  
  toggleSplitView(): void {
    this.isSplitView.update(isSplit => {
      if (isSplit) {
        this.panePaths.update(paths => paths.slice(0, 1));
        this.activePaneId.set(1);
        return false;
      } else {
        const currentPath = this.panePaths()[0]?.path ?? [];
        this.panePaths.update(paths => [...paths, { id: 2, path: currentPath }]);
        this.activePaneId.set(2);
        return true;
      }
    });
  }
  
  // --- Pane Visibility Toggles ---
  toggleSidebar(): void {
    this.uiPreferencesService.toggleSidebar();
  }

  toggleTree(): void {
    this.uiPreferencesService.toggleTree();
  }

  toggleChat(): void {
    this.uiPreferencesService.toggleChat();
  }

  toggleNotes(): void {
    this.uiPreferencesService.toggleNotes();
  }

  toggleDetailPane(): void {
    this.uiPreferencesService.toggleDetailPane();
  }

  toggleSavedItems(): void {
    this.uiPreferencesService.toggleSavedItems();
  }

  toggleRssFeed(): void {
    this.uiPreferencesService.toggleRssFeed();
  }

  toggleStream(): void {
    this.uiPreferencesService.toggleStream();
  }

  toggleConsole(): void {
    this.uiPreferencesService.toggleConsole();
  }

  onItemSelectedInPane(item: FileSystemNode | null): void {
    this.selectedDetailItem.set(item);
  }

  setActivePane(id: number): void {
    this.activePaneId.set(id);
  }
  
  onPane1PathChanged(path: string[]): void {
    this.updatePanePath(1, path);
  }

  onPane2PathChanged(path: string[]): void {
    this.updatePanePath(2, path);
  }
  
  onPane1StatusChanged(status: PaneStatus): void {
    this.pane1Status.set(status);
  }

  onPane2StatusChanged(status: PaneStatus): void {
    this.pane2Status.set(status);
  }

  private updatePanePath(id: number, path: string[]): void {
    this.panePaths.update(paths => {
      const index = paths.findIndex(p => p.id === id);
      if (index > -1) {
        const newPaths = [...paths];
        newPaths[index] = { ...newPaths[index], path: path };
        return newPaths;
      }
      return paths;
    });
  }
  
  goUpActivePane(): void {
    if (this.canGoUpActivePane()) {
      const newPath = this.activePanePath().slice(0, -1);
      this.updatePanePath(this.activePaneId(), newPath);
    }
  }

  navigatePathActivePane(displayIndex: number): void {
    // For root, index is -1. `slice(0, 1)` gives the first segment.
    // For first segment, index is 0. `slice(0, 2)` gives first two segments.
    const newPath = this.activePanePath().slice(0, displayIndex + 2);
    this.updatePanePath(this.activePaneId(), newPath);
  }

  triggerRefresh(): void {
    this.refreshPanes.update(v => v + 1);
  }
  
  onSidebarNavigation(path: string[]): void {
    this.updatePanePath(this.activePaneId(), path);
  }

  openServerProfilesDialog(): void {
    this.isServerProfilesDialogOpen.set(true);
  }

  closeServerProfilesDialog(): void {
    this.isServerProfilesDialogOpen.set(false);
    this.profileForEdit.set(null); // Reset after closing
  }
  
  openLocalConfigDialog(): void {
    this.isLocalConfigDialogOpen.set(true);
  }

  closeLocalConfigDialog(): void {
    this.isLocalConfigDialogOpen.set(false);
  }
  
  openRssFeedsDialog(): void {
    this.isRssFeedsDialogOpen.set(true);
  }

  closeRssFeedsDialog(): void {
    this.isRssFeedsDialogOpen.set(false);
  }

  openPreferencesDialog(): void {
    this.isPreferencesDialogOpen.set(true);
  }

  closePreferencesDialog(): void {
    this.isPreferencesDialogOpen.set(false);
  }
  
  onPreferencesSaved(prefs: Partial<UiPreferences>): void {
    this.uiPreferencesService.saveAllPreferences(prefs);
    this.closePreferencesDialog();
    this.toastService.show('Preferences saved.');
  }
  
  onLocalConfigSaved(config: LocalConfig): void {
    this.localConfigService.updateConfig(config);
    this.toastService.show('Local configuration saved.');
    this.closeLocalConfigDialog();
  }

  async onLoadChildren(path: string[]): Promise<void> {
    const rootNode = this.folderTree();
    if (!rootNode) return;

    // Find the node that was expanded in the tree
    let targetNode: FileSystemNode | undefined = rootNode;
    for (const segment of path) {
        targetNode = targetNode?.children?.find(c => c.name === segment);
    }

    if (targetNode?.isServerRoot && !targetNode.connected) {
      this.toastService.show(`"${targetNode.name}" is not connected.`, 'info');
      return;
    }

    const provider = this.getProviderForPath(path);
    const providerPath = path.slice(1);

    try {
      const children = await provider.getContents(providerPath);
      this.folderTree.update(currentRoot => {
        if (!currentRoot) return null;
        
        const findAndUpdate = (node: FileSystemNode, currentPath: string[]): FileSystemNode => {
          if (currentPath.join('/') === path.join('/')) {
             return { ...node, childrenLoaded: true, children: children.map(grandchild => grandchild.type === 'folder' ? { ...grandchild, children: [], childrenLoaded: false } : grandchild) };
          }
          if (!node.children) {
            return node;
          }
          const newChildren = node.children.map(child => {
            const childPath = [...currentPath, child.name];
            if (path.join('/').startsWith(childPath.join('/'))) {
              return findAndUpdate(child, childPath);
            }
            return child;
          });
          return { ...node, children: newChildren };
        };
        
        return findAndUpdate(currentRoot, []);
      });

    } catch (e) {
      console.error(`Failed to load children for path ${path.join('/')}`, e);
    }
  }

  async onSidebarItemsMoved({ destPath, payload }: { destPath: string[]; payload: DragDropPayload }): Promise<void> {
    if (payload.type !== 'filesystem') return;
    const { sourceProvider, sourcePath, items } = payload.payload;
    const itemRefs = items.map(i => ({ name: i.name, type: i.type }));
    
    const sourceProviderPath = sourcePath.length > 0 ? sourcePath.slice(1) : [];
    const destProviderPath = destPath.length > 0 ? destPath.slice(1) : [];

    try {
      await sourceProvider.move(sourceProviderPath, destProviderPath, itemRefs);
      for (const item of items) {
        const oldPath = [...sourcePath, item.name];
        const newPath = [...destPath, item.name];
        this.updatePathsAfterRename(oldPath, newPath);
      }
    } catch (e) {
      alert(`Move failed: ${(e as Error).message}`);
    } finally {
      this.refreshPanes.update(v => v + 1);
    }
  }

  private stopAllResizing(): void {
    const wasResizing = this.isResizingPanes() || this.isResizingBottomPane() || this.isResizingConsolePane();

    this.stopPaneResize();
    this.stopBottomPaneResize();
    this.stopConsolePaneResize();

    if (wasResizing) {
      this.renderer.removeStyle(this.document.body, 'user-select');
    }
  }

  startPaneResize(event: MouseEvent): void {
    if (!this.isSplitView()) return;
    
    this.isResizingPanes.set(true);
    const container = this.paneContainerEl.nativeElement;
    const containerRect = container.getBoundingClientRect();

    event.preventDefault();
    this.renderer.setStyle(this.document.body, 'user-select', 'none');

    this.unlistenPaneMouseMove = this.renderer.listen('document', 'mousemove', (e: MouseEvent) => {
        const mouseX = e.clientX - containerRect.left;
        let newWidthPercent = (mouseX / containerRect.width) * 100;

        const minWidthPercent = 20;
        const maxWidthPercent = 80;
        if (newWidthPercent < minWidthPercent) newWidthPercent = minWidthPercent;
        if (newWidthPercent > maxWidthPercent) newWidthPercent = maxWidthPercent;

        this.pane1Width.set(newWidthPercent);
    });
  }

  private stopPaneResize(): void {
    if (!this.isResizingPanes()) return;
    this.isResizingPanes.set(false);
    
    if (this.unlistenPaneMouseMove) {
        this.unlistenPaneMouseMove();
        this.unlistenPaneMouseMove = null;
    }
    
    this.uiPreferencesService.setSplitViewPaneWidth(this.pane1Width());
  }
  
  onToolbarAction(name: string, payload?: any) {
    this.toolbarAction.set({ name, payload, id: Date.now() });
  }

  onSortChange(criteria: SortCriteria) {
    if (this.activePaneId() === 1) {
      this.pane1SortCriteria.set(criteria);
    } else {
      this.pane2SortCriteria.set(criteria);
    }
  }

  onDisplayModeChange(mode: 'grid' | 'list') {
    if (this.activePaneId() === 1) {
      this.pane1DisplayMode.set(mode);
    } else {
      this.pane2DisplayMode.set(mode);
    }
  }
  
  onFilterChange(query: string) {
    if (this.activePaneId() === 1) {
      this.pane1FilterQuery.set(query);
    } else {
      this.pane2FilterQuery.set(query);
    }
  }
  
  onSaveBookmark(bookmark: NewBookmark): void {
    const path = this.activePanePath();
    this.bookmarkService.addBookmark(path, bookmark);
    if (!this.isDetailPaneOpen()) {
      const truncatedTitle = bookmark.title.length > 30 ? `${bookmark.title.substring(0, 27)}...` : bookmark.title;
      this.toastService.show(`Saved "${truncatedTitle}"`);
    }
  }

  onBookmarkDroppedOnPane(event: { bookmark: NewBookmark, dropOn: FileSystemNode }): void {
    const path = this.activePaneId() === 1 ? this.pane1Path() : this.pane2Path();
    const destPath = [...path, event.dropOn.name];
    this.bookmarkService.addBookmark(destPath, event.bookmark);
  }

  onBookmarkDroppedOnSidebar(event: { bookmark: NewBookmark, destPath: string[] }): void {
    this.bookmarkService.addBookmark(event.destPath, event.bookmark);
  }
  
  // --- Sidebar Context Menu Actions ---
  private async performTreeAction(path: string[], action: (provider: FileSystemProvider, providerPath: string[]) => Promise<any>): Promise<boolean> {
    try {
      const provider = this.getProviderForPath(path);
      const providerPath = path.slice(1);
      await action(provider, providerPath);
      return true;
    } catch(e) {
      alert(`Operation failed: ${(e as Error).message}`);
      return false;
    }
  }

  private updatePathsAfterRename(oldPath: string[], newPath: string[]): void {
    const oldPathString = oldPath.join('/');
    this.panePaths.update(paths => {
        return paths.map(panePath => {
            const currentPathString = panePath.path.join('/');
            if (currentPathString === oldPathString) {
                // This pane was viewing the renamed folder directly
                return { ...panePath, path: newPath };
            }
            if (currentPathString.startsWith(oldPathString + '/')) {
                // This pane was viewing a subfolder of the renamed folder
                const subPath = panePath.path.slice(oldPath.length);
                return { ...panePath, path: [...newPath, ...subPath] };
            }
            return panePath;
        });
    });
  }

  private updatePathsAfterDelete(deletedPath: string[]): void {
    const deletedPathString = deletedPath.join('/');
    if (!deletedPathString) return;

    this.panePaths.update(paths => {
      return paths.map(panePath => {
        const currentPathString = panePath.path.join('/');
        if (currentPathString.startsWith(deletedPathString)) {
          const newPath = deletedPath.slice(0, -1);
          return { ...panePath, path: newPath };
        }
        return panePath;
      });
    });
  }

  async onPaneItemRenamed({ oldName, newName }: { oldName: string, newName: string }, parentPath: string[]): Promise<void> {
    const oldFullPath = [...parentPath, oldName];
    const newFullPath = [...parentPath, newName];
    await this.folderPropertiesService.handleRename(oldFullPath, newFullPath);
    this.updatePathsAfterRename(oldFullPath, newFullPath);
    // Refresh the tree to reflect the change.
    await this.loadFolderTree();
  }

  // Handler for items deleted from a file explorer pane
  onItemsDeleted(paths: string[][]): void {
    for (const path of paths) {
      this.updatePathsAfterDelete(path);
      this.folderPropertiesService.handleDelete(path);
    }
  }
  
  // Handler for items moved within or between file explorer panes
  onItemsMoved({ sourcePath, destPath, items }: { sourcePath: string[]; destPath: string[]; items: ItemReference[] }): void {
    for (const item of items) {
      const oldPath = [...sourcePath, item.name];
      const newPath = [...destPath, item.name];
      this.updatePathsAfterRename(oldPath, newPath);
      this.folderPropertiesService.handleRename(oldPath, newPath);
    }
  }

  async onSidebarRenameItem({ path, newName }: { path: string[]; newName: string }): Promise<void> {
    const oldName = path[path.length - 1];
    const parentPath = path.slice(0, -1);
    const success = await this.performTreeAction(parentPath, (provider, providerPath) => 
      provider.rename(providerPath, oldName, newName)
    );
    if (success) {
      const newFullPath = [...parentPath, newName];
      await this.folderPropertiesService.handleRename(path, newFullPath);
      this.updatePathsAfterRename(path, newFullPath);
      await this.loadFolderTree();
    }
  }

  async onSidebarDeleteItem(path: string[]): Promise<void> {
    const name = path[path.length - 1];
    const parentPath = path.slice(0, -1);
    const success = await this.performTreeAction(parentPath, (provider, providerPath) => 
      provider.removeDirectory(providerPath, name) // Tree only has directories
    );
    if (success) {
      await this.folderPropertiesService.handleDelete(path);
      this.updatePathsAfterDelete(path);
      await this.loadFolderTree();
    }
  }

  async onSidebarNewFolder({ path, name }: { path: string[]; name: string }): Promise<void> {
    const success = await this.performTreeAction(path, (provider, providerPath) => 
      provider.createDirectory(providerPath, name)
    );
    if (success) await this.loadFolderTree();
  }

  async onSidebarNewFile({ path, name }: { path: string[]; name: string }): Promise<void> {
     const success = await this.performTreeAction(path, (provider, providerPath) => 
      provider.createFile(providerPath, name)
    );
    if (success) await this.loadFolderTree();
  }
  
  onConnectToServer(profileId: string): void {
    const profile = this.profileService.profiles().find(p => p.id === profileId);
    if (profile) {
      this.profileForLogin.set(profile);
    }
  }

  onDisconnectFromServer(profileId: string): void {
    const profile = this.profileService.profiles().find(p => p.id === profileId);
    if (profile) {
      this.onUnmountProfile(profile);
    }
  }

  onEditServerProfile(profileId: string): void {
    const profile = this.profileService.profiles().find(p => p.id === profileId);
    if (profile) {
      this.profileForEdit.set(profile);
      this.openServerProfilesDialog();
    }
  }

  async onServerProfileRenamed({ oldName, newName, profile }: { oldName: string, newName: string, profile: ServerProfile }): Promise<void> {
    const oldPath = [oldName];
    const newPath = [newName];
    await this.folderPropertiesService.handleRename(oldPath, newPath);
    
    // 1. Update pane paths that were pointing to the old profile name.
    this.panePaths.update(paths => {
      return paths.map(panePath => {
        if (panePath.path.length > 0 && panePath.path[0] === oldName) {
          const newPath = [newName, ...panePath.path.slice(1)];
          return { ...panePath, path: newPath };
        }
        return panePath;
      });
    });

    // 2. If the profile was mounted, update the provider maps and the mountedProfiles array.
    if (this.mountedProfileIds().includes(profile.id)) {
      if (this.remoteProviders().has(oldName)) {
        // Recreate the provider with the updated profile.
        const user = this.mountedProfileUsers().get(profile.id);
        const newProvider = new RemoteFileSystemService(profile, this.fsService, this.searchService, user?.alias ?? null);

        // Re-key the map with the new provider instance.
        this.remoteProviders.update(map => {
          const newMap = new Map(map);
          newMap.set(newName, newProvider);
          newMap.delete(oldName);
          return newMap;
        });
      }

      // Image service must be recreated as its profile is private.
      if (this.remoteImageServices().has(oldName)) {
        const imageUrl = profile.imageUrl || this.localConfigService.defaultImageUrl();
        const profileForImageService = { ...profile, imageUrl };
        const newImageService = new ImageService(profileForImageService, this.imageClientService, this.preferencesService);
        this.remoteImageServices.update(map => {
            const newMap = new Map(map);
            newMap.set(newName, newImageService);
            newMap.delete(oldName);
            return newMap;
        });
      }
      
      // Also update the mountedProfiles signal to ensure its data is not stale.
      this.mountedProfiles.update(profiles => 
        profiles.map(p => p.id === profile.id ? profile : p)
      );
    }
  }

  // --- Import/Export ---
  handleExport({ node, path }: { node: FileSystemNode; path: string[] }): void {
    const jsonString = JSON.stringify(node, null, 2);
    const blob = new Blob([jsonString], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    const filename = path.length > 0 ? path.join('_') + '.json' : 'local_session_export.json';
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    this.isExportDialogOpen.set(false);
    this.toastService.show(`Exported ${filename}`);
  }

  async handleImport({ destPath, data }: { destPath: string[]; data: FileSystemNode }): Promise<void> {
    try {
      await this.sessionFs.importTree(destPath, data);
      this.isImportDialogOpen.set(false);
      // FIX: Replace `Array.prototype.at()` with `array[array.length - 1]` for compatibility with older TypeScript targets.
      this.toastService.show(`Successfully imported into "${destPath.length > 0 ? destPath[destPath.length - 1] : await this.sessionFs.getFolderTree().then(t => t.name)}".`);
      await this.loadFolderTree();
    } catch (e) {
      this.toastService.show(`Import failed: ${(e as Error).message}`, 'error');
    }
  }

  onKeyDown(event: KeyboardEvent): void {
    // Handle F-key pane toggles first, as they should work anywhere.
    switch(event.key) {
      case 'F7':
        event.preventDefault();
        this.toggleTree();
        return;
      case 'F8':
        event.preventDefault();
        this.toggleChat();
        return;
      case 'F9':
        event.preventDefault();
        this.toggleNotes();
        return;
      case 'F10':
        event.preventDefault();
        this.toggleStream();
        return;
      case 'F11':
        event.preventDefault();
        this.toggleSavedItems();
        return;
      case 'F12':
        event.preventDefault();
        this.toggleRssFeed();
        return;
    }
    
    // Handle high-priority Escape key presses for modals.
    if (event.key === 'Escape') {
      if (this.isThemeDropdownOpen()) {
        event.preventDefault();
        this.isThemeDropdownOpen.set(false);
        return;
      }
      if (this.isExportDialogOpen()) {
        event.preventDefault();
        this.isExportDialogOpen.set(false);
        return;
      }
      if (this.isImportDialogOpen()) {
        event.preventDefault();
        this.isImportDialogOpen.set(false);
        return;
      }
      if (this.profileForLogin()) {
        event.preventDefault();
        this.profileForLogin.set(null);
        return;
      }
       if (this.isRssFeedsDialogOpen()) {
        event.preventDefault();
        this.closeRssFeedsDialog();
        return;
      }
       if (this.isPreferencesDialogOpen()) {
        event.preventDefault();
        this.closePreferencesDialog();
        return;
      }
      if (this.webviewContent()) {
        event.preventDefault();
        this.webviewService.close();
        return;
      }
      if (this.textEditorContent()) {
        event.preventDefault();
        this.textEditorService.close();
        return;
      }
      if (this.isLocalConfigDialogOpen()) {
        event.preventDefault();
        this.closeLocalConfigDialog();
        return;
      }
      if (this.isServerProfilesDialogOpen()) {
        event.preventDefault(); // prevent any other default browser behavior
        this.closeServerProfilesDialog();
        return; // Action handled, stop further processing
      }
    }

    const target = event.target as HTMLElement;
    const isInput = target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.tagName === 'SELECT';
    const isRenameInput = target.classList.contains('rename-input');

    if (isRenameInput) {
      return; // Let rename input handle its own keys.
    }

    // Handle keys that should be blocked if an input is focused.
    if (event.key === 'F2') {
      if (isInput) return; // Don't trigger rename if any other input is focused.
      if (this.canRename()) {
        event.preventDefault();
        this.onToolbarAction('rename');
      }
      return;
    }
    
    if (event.altKey && event.key === 'Enter') {
        if (isInput) return;
        if (this.canRename()) { // canRename implies one item selected
            event.preventDefault();
            this.onToolbarAction('properties');
        }
        return;
    }

    // New File: Ctrl+Alt+N - should work anywhere
    if (event.ctrlKey && event.altKey && event.key.toLowerCase() === 'n') {
        event.preventDefault();
        this.onToolbarAction('newFile');
        return;
    }
    
    // Toggle Split View: Ctrl+\ - should work anywhere
    if (event.ctrlKey && event.key === '\\') {
        event.preventDefault();
        this.toggleSplitView();
        return;
    }
    
    // Toggle Console View: Ctrl+` - should work anywhere
    if (event.ctrlKey && event.key === '`') {
        event.preventDefault();
        this.toggleConsole();
        return;
    }

    // Toggle Details Pane: Ctrl+Shift+D - should work anywhere
    if (event.ctrlKey && event.shiftKey && event.key.toLowerCase() === 'd') {
        event.preventDefault();
        this.toggleDetailPane();
        return;
    }

    if (isInput) {
      if (event.key === 'F5') { // Allow refresh from inputs.
        event.preventDefault();
        this.triggerRefresh();
      }
      if (event.key === 'F6') { // Allow pane switching from inputs
        if (this.isSplitView()) {
            event.preventDefault();
            this.activePaneId.update(id => (id === 1 ? 2 : 1));
        }
      }
      return; // Ignore other hotkeys in inputs.
    }

    if (event.ctrlKey && event.shiftKey && event.key.toLowerCase() === 'n') {
      event.preventDefault();
      this.onToolbarAction('newFolder');
      return;
    }

    if (event.ctrlKey) {
      switch (event.key.toLowerCase()) {
        case 'c':
          if (this.canCutCopyShareDelete()) {
            event.preventDefault();
            this.onToolbarAction('copy');
          }
          break;
        case 'x':
          if (this.canCutCopyShareDelete()) {
            event.preventDefault();
            this.onToolbarAction('cut');
          }
          break;
        case 'v':
          if (this.canPaste()) {
            event.preventDefault();
            this.onToolbarAction('paste');
          }
          break;
        case 'a':
          event.preventDefault();
          this.onToolbarAction('selectAll');
          break;
        case 'f':
          event.preventDefault();
          const filterInput = this.document.getElementById('toolbar-filter-input');
          filterInput?.focus();
          (filterInput as HTMLInputElement)?.select();
          break;
      }
      return;
    }

    switch (event.key) {
      case 'Delete':
        if (this.canCutCopyShareDelete()) {
          event.preventDefault();
          this.onToolbarAction('delete');
        }
        break;
      case 'F5':
        event.preventDefault();
        this.triggerRefresh();
        break;
      case 'F6':
        if (this.isSplitView()) {
            event.preventDefault();
            this.activePaneId.update(id => (id === 1 ? 2 : 1));
        }
        break;
      case 'Backspace':
        if (this.canGoUpActivePane()) {
          event.preventDefault();
          this.goUpActivePane();
        }
        break;
      case 'Escape':
        this.onToolbarAction('clearSelection');
        break;
    }
  }

  // --- Idea Stream Methods ---
  startBottomPaneResize(event: MouseEvent): void {
    this.isResizingBottomPane.set(true);
    const container = this.mainContentWrapperEl.nativeElement;
    const containerRect = container.getBoundingClientRect();

    // CAPTURE the console area height ONCE on mousedown
    const consolePaneEl = container.querySelector<HTMLElement>('[data-console-pane]');
    const consoleResizerEl = container.querySelector<HTMLElement>('[data-console-resizer]');
    this.streamResizeInitialConsoleHeight = 0;
    if (consolePaneEl) {
      this.streamResizeInitialConsoleHeight += consolePaneEl.offsetHeight;
    }
    if (consoleResizerEl) {
      this.streamResizeInitialConsoleHeight += consoleResizerEl.offsetHeight;
    }

    event.preventDefault();
    this.renderer.setStyle(this.document.body, 'user-select', 'none');

    this.unlistenBottomPaneMouseMove = this.renderer.listen('document', 'mousemove', (e: MouseEvent) => {
        const mouseY = e.clientY - containerRect.top;
        const totalHeight = containerRect.height;
        const heightBelowStreamResizerPx = totalHeight - mouseY;
        
        const newStreamHeightPx = heightBelowStreamResizerPx - this.streamResizeInitialConsoleHeight;
        let newHeightPercent = (newStreamHeightPx / totalHeight) * 100;

        const minHeightPercent = 15;
        // Max height should leave at least 15% for the top file explorer pane
        const consoleHeightPercent = (this.streamResizeInitialConsoleHeight / totalHeight) * 100;
        const maxHeightPercent = 85 - consoleHeightPercent;

        if (newHeightPercent < minHeightPercent) newHeightPercent = minHeightPercent;
        if (newHeightPercent > maxHeightPercent) newHeightPercent = maxHeightPercent;

        this.bottomPaneHeight.set(newHeightPercent);
    });
  }

  private stopBottomPaneResize(): void {
      if (!this.isResizingBottomPane()) return;
      this.isResizingBottomPane.set(false);
      
      if (this.unlistenBottomPaneMouseMove) {
          this.unlistenBottomPaneMouseMove();
          this.unlistenBottomPaneMouseMove = null;
      }
      
      this.uiPreferencesService.setExplorerStreamHeight(this.bottomPaneHeight());
  }

  // --- Console Pane Methods ---
  startConsolePaneResize(event: MouseEvent): void {
    this.isResizingConsolePane.set(true);
    const container = this.mainContentWrapperEl.nativeElement;
    const containerRect = container.getBoundingClientRect();

    event.preventDefault();
    this.renderer.setStyle(this.document.body, 'user-select', 'none');

    this.unlistenConsolePaneMouseMove = this.renderer.listen('document', 'mousemove', (e: MouseEvent) => {
        const mouseY = e.clientY - containerRect.top;
        const totalHeight = containerRect.height;
        let newHeightPercent = ((totalHeight - mouseY) / totalHeight) * 100;

        const minHeightPercent = 10;
        const maxHeightPercent = 85;
        if (newHeightPercent < minHeightPercent) newHeightPercent = minHeightPercent;
        if (newHeightPercent > maxHeightPercent) newHeightPercent = maxHeightPercent;

        this.consolePaneHeight.set(newHeightPercent);
    });
  }

  private stopConsolePaneResize(): void {
      if (!this.isResizingConsolePane()) return;
      this.isResizingConsolePane.set(false);
      
      if (this.unlistenConsolePaneMouseMove) {
          this.unlistenConsolePaneMouseMove();
          this.unlistenConsolePaneMouseMove = null;
      }
      
      this.uiPreferencesService.setExplorerConsoleHeight(this.consolePaneHeight());
  }

  toggleStreamPaneCollapse(): void {
    this.uiPreferencesService.toggleStreamPaneCollapse();
  }

  onStreamSearchChange(event: Event): void {
    this.streamSearchQuery.set((event.target as HTMLInputElement).value);
  }

  onStreamSortChange(key: StreamSortKey): void {
    const current = this.streamSortCriteria();
    if (key === 'relevance') {
      this.streamSortCriteria.set({ key: 'relevance', direction: 'asc' });
    } else if (current.key === key) {
      this.streamSortCriteria.update(c => ({ ...c, direction: c.direction === 'asc' ? 'desc' : 'asc' }));
    } else {
      // Newest date first is more intuitive default
      const direction = key === 'date' ? 'desc' : 'asc';
      this.streamSortCriteria.set({ key, direction });
    }
    this.isStreamSortDropdownOpen.set(false);
  }

  toggleStreamFilter(type: StreamItemType): void {
    this.activeStreamFilters.update(filters => {
      const newFilters = new Set(filters);
      if (newFilters.has(type)) {
        newFilters.delete(type);
      } else {
        newFilters.add(type);
      }
      return newFilters;
    });
  }

  toggleAllStreamFilters(): void {
    if (this.activeStreamFilters().size === this.streamFilterTypes.length) {
      this.activeStreamFilters.set(new Set());
    } else {
      this.activeStreamFilters.set(new Set(this.streamFilterTypes.map(t => t.type)));
    }
  }

  toggleStreamSortDropdown(event: MouseEvent): void {
    event.stopPropagation();
    this.isStreamSortDropdownOpen.update(v => !v);
  }

  getStreamItemLink(item: StreamItem): string {
    if ('link' in item && item.link) return item.link;
    if ('url' in item && item.url) return item.url;
    // For Gemini results which lack a URL, create a unique-enough identifier
    if (item.type === 'gemini') return `#gemini-${item.publishedAt}`;
    return '#';
  }


  private streamItemToNewBookmark(item: StreamItem): NewBookmark {
    
    const link = this.getStreamItemLink(item);
    
    let title: string;
    if ('title' in item) title = item.title;
    else if ('query' in item) title = `Gemini response for: ${item.query}`;
    else title = item.description;

    let snippet: string | undefined;
    if ('snippet' in item) snippet = item.snippet;
    else if ('text' in item) snippet = item.text;
    else if ('photographer' in item) snippet = `Photo by ${item.photographer}`;
    else snippet = item.description;

    let source: string;
    if ('source' in item) source = item.source;
    else if ('channelTitle' in item) source = item.channelTitle;
    else if ('publication' in item) source = item.publication;
    else source = 'Gemini Search';

    return {
        type: item.type,
        title,
        link,
        snippet,
        source,
        thumbnailUrl: 'thumbnailUrl' in item ? item.thumbnailUrl : undefined,
    };
  }

  onBookmarkToggled(item: StreamItem): void {
    const link = this.getStreamItemLink(item);
    const existingBookmark = this.bookmarkService.findBookmarkByLink(link);

    if (existingBookmark) {
        this.bookmarkService.deleteBookmark(existingBookmark._id);
    } else {
        const newBookmark = this.streamItemToNewBookmark(item);
        this.onSaveBookmark(newBookmark);
    }
  }
}