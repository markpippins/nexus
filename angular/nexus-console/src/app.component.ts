


import { Component, ChangeDetectionStrategy, signal, computed, inject, effect, untracked, Renderer2, ElementRef, NgZone, OnDestroy, AfterViewInit, Injector, OnInit, ViewChild } from '@angular/core';
import { CommonModule, DOCUMENT } from '@angular/common';
import { FileExplorerComponent } from './components/file-explorer/file-explorer.component.js';
import { SidebarComponent } from './components/sidebar/sidebar.component.js';
import { FileSystemNode, FileType, Mount } from './models/file-system.model.js';
import { FileSystemProvider, ItemReference } from './services/file-system-provider.js';
import { BrokerProfileService } from './services/broker-profile.service.js';
import { HostProfileService } from './services/host-profile.service.js';
import { DetailPaneComponent } from './components/detail-pane/detail-pane.component.js';
import { SessionService } from './services/in-memory-file-system.service.js';
import { BrokerProfile } from './models/broker-profile.model.js';
import { PlatformManagementService, ServicePayload, FrameworkPayload, Server, LOOKUP_SERVER_TYPES, LOOKUP_ENVIRONMENTS, LOOKUP_OPERATING_SYSTEMS, LOOKUP_SERVICE_TYPES, LOOKUP_FRAMEWORK_CATEGORIES, LOOKUP_FRAMEWORK_LANGUAGES } from './services/platform-management.service.js';
import { SecureFileSystemService } from './services/secure-file-system.service.js';
import { DirectFileSystemService } from './services/direct-file-system.service.js';
import { FsService } from './services/fs.service.js';
import { ImageService } from './services/image.service.js';
import { ImageClientService } from './services/image-client.service.js';
import { LoginService } from './services/login.service.js';
import { User } from './models/user.model.js';
import { PreferencesService } from './services/preferences.service.js';
import { DragDropPayload } from './services/drag-drop.service.js';
import { ToolbarComponent, SortCriteria } from './components/toolbar/toolbar.component.js';
import { BottomBarComponent } from './bottom-bar/bottom-bar.component.js';
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
import { GeminiService, GeminiSearchParams } from './services/gemini.service.js';
import { NodeType } from './models/tree-node.model.js';
import { IdeaStreamComponent } from './components/idea-stream/idea-stream.component.js';
import { PreferencesDialogComponent } from './components/preferences-dialog/preferences-dialog.component.js';
import { TerminalComponent } from './components/terminal/terminal.component.js';
import { NotesService } from './services/notes.service.js';
import { ComplexSearchDialogComponent } from './components/complex-search-dialog/complex-search-dialog.component.js';
import { ComplexSearchParams } from './components/complex-search/complex-search.component.js';
import { HealthCheckService } from './services/health-check.service.js';
import { GeminiSearchDialogComponent } from './components/gemini-search-dialog/gemini-search-dialog.component.js';
import { TreeManagerService } from './services/tree-manager.service.js';
import { RegistryServerProvider } from './services/registry-server-provider.service.js';
import { RegistryServerProfileService } from './services/registry-server-profile.service.js';
import { TreeProviderAdapter } from './services/tree-provider-adapter.js';
import { ServiceMeshComponent } from './components/service-mesh/service-mesh.component.js';
import { CreateUserDialogComponent } from './components/create-user/create-user-dialog.component.js';
import { PlatformManagementComponent } from './components/platform-management/platform-management.component.js';
import { ServiceMeshService } from './services/service-mesh.service.js';
import { ArchitectureVizService } from './services/architecture-viz.service.js';
import { AtlasService } from './services/atlas.service.js';
import { ServiceRegistryEditorComponent } from './components/service-registry-editor/service-registry-editor.component.js';
import { GatewayEditorComponent } from './components/gateway-editor/gateway-editor.component.js';
import { ConfirmDialogComponent } from './components/confirm-dialog/confirm-dialog.component.js';
import { GatewayManagementComponent } from './components/gateway-management/gateway-management.component.js';
import { RegistryServerManagementComponent } from './components/registry-server-management/registry-server-management.component.js';
import { IframeViewComponent } from './components/iframe-view/iframe-view.component.js';
import { uiOriginFor } from './ui-origins.js';
import { NavToolbarComponent } from './nav-toolbar/nav-toolbar.component.js';
import { GenericTreeNode } from './models/generic-tree.model.js';
import { MessageBoxContainerComponent } from './components/message-box-container/message-box-container.component.js';
import { UiEventBusService } from './services/ui-event-bus.service.js';

import { SystemHealthComponent } from './components/system-health/system-health.component.js';
import { TopologyComponent } from './components/topology/topology.component.js';
import { NebulaPanelComponent } from './components/nebula-panel/nebula-panel.component.js';
import { LoadViewDialogComponent } from './components/load-view-dialog/load-view-dialog.component.js';

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
  hasFile: (path: string[], filename: string) => Promise.resolve(false),
  hasFolder: () => Promise.resolve(false),
  // Methods added for GenericTreeProvider
  getNodeContent: () => Promise.reject(new Error('Operation not supported.')),
  saveNodeContent: () => Promise.reject(new Error('Operation not supported.')),
  getTree: () => Promise.reject(new Error('Operation not supported.')),
  createFolder: () => Promise.reject(new Error('Operation not supported.')),
  removeFolder: () => Promise.reject(new Error('Operation not supported.')),
  createNode: () => Promise.reject(new Error('Operation not supported.')),
  deleteNode: () => Promise.reject(new Error('Operation not supported.')),
  hasNode: () => Promise.reject(new Error('Operation not supported.')),
};

const disconnectedProvider: FileSystemProvider = {
  getContents: () => Promise.resolve([]),
  getFolderTree: () => Promise.reject(new Error('Server is disconnected.')),
  ...readOnlyProviderOps,
};

@Component({
  selector: 'app-root',
  standalone: true,
  templateUrl: './app.component.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [CommonModule, FileExplorerComponent, SidebarComponent, DetailPaneComponent, ToolbarComponent, ToastsComponent, WebviewDialogComponent, LocalConfigDialogComponent, LoginDialogComponent, RssFeedsDialogComponent, ImportDialogComponent, ExportDialogComponent, TextEditorDialogComponent, IdeaStreamComponent, PreferencesDialogComponent, TerminalComponent, ComplexSearchDialogComponent, GeminiSearchDialogComponent, ServiceMeshComponent, CreateUserDialogComponent, PlatformManagementComponent, ServiceRegistryEditorComponent, GatewayEditorComponent, GatewayManagementComponent, RegistryServerManagementComponent, ConfirmDialogComponent, IframeViewComponent, BottomBarComponent, NavToolbarComponent, MessageBoxContainerComponent, SystemHealthComponent, TopologyComponent, NebulaPanelComponent, LoadViewDialogComponent],
  host: {
    '(document:keydown)': 'onKeyDown($event)',
    '(document:click)': 'onDocumentClick($event)',
  }
})
export class AppComponent implements OnInit, AfterViewInit, OnDestroy {
  private sessionFs = inject(SessionService);
  private profileService = inject(BrokerProfileService);
  private hostProfileService = inject(HostProfileService);
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
  private ngZone = inject(NgZone);
  private elementRef = inject(ElementRef);
  private dbService = inject(DbService);
  private uiPreferencesService = inject(UiPreferencesService);
  private homeProvider: FileSystemProvider;
  private geminiService = inject(GeminiService);
  private notesService = inject(NotesService);
  private healthCheckService = inject(HealthCheckService);
  private treeManager = inject(TreeManagerService);
  private registryServerProvider = inject(RegistryServerProvider);
  private registryServerProfileService = inject(RegistryServerProfileService);
  private serviceMeshService = inject(ServiceMeshService);
  public vizService = inject(ArchitectureVizService);
  private atlasService = inject(AtlasService);
  private eventBus = inject(UiEventBusService);
  private initialAutoConnectAttempted = false;

  // --- State Management ---
  isSplitView = signal(false);
  activePaneId = signal(1);
  folderTree = signal<FileSystemNode | null>(null);
  isLocalConfigDialogOpen = signal(false);
  isRssFeedsDialogOpen = signal(false);
  isImportDialogOpen = signal(false);
  isExportDialogOpen = signal(false);
  isPreferencesDialogOpen = signal(false);
  isHamburgerMenuOpen = signal(false);
  isComplexSearchDialogOpen = signal(false);
  isGeminiSearchDialogOpen = signal(false);
  isCreateUserDialogOpen = signal(false);
  isLoadViewDialogOpen = signal(false);
  isImportDependencyWarningOpen = signal(false);
  importWarningDontShowAgain = signal(false);
  profileForCreateUser = signal<BrokerProfile | undefined>(undefined);
  selectedDetailItem = signal<FileSystemNode | null>(null);
  connectionStatus = signal<ConnectionStatus>('disconnected');
  refreshPanes = signal(0);
  currentViewMode = signal<'file-explorer' | 'service-mesh' | 'atlas' | 'conduit-ui' | 'duality' | 'plurality' | 'assembly' | 'nebula-rms' | 'peb-ui' | 'kernel-ui' | 'tackle-ui' | 'kanban' | 'cascade-ui' | 'execution-ui' | 'vision-ui' | 'wind-ui' | 'nebula-cp' | 'semantics-ui' | 'shrapnel' | 'throttler-ui' | 'barbie' | 'monaco-judge' | 'conduit-legacy-ui' | 'data-explorer'>('file-explorer');  // Default to file explorer
  meshViewMode = signal<'console' | 'graph'>('console');  // Sub-mode when in service-mesh
  graphBackgroundColor = signal('#000510');  // Graph background color
  graphSubView = signal<'canvas' | 'creator'>('canvas');  // Sub-view when in graph mode (canvas vs creator)
  showRunningOnly = signal(false);  // Toggle to show only running services in mesh
  paletteCollapsed = signal(false);  // Toggle to collapse component palette in service graph

  // Left nav collapsed state — persisted via UiPreferencesService
  isNavCollapsed = this.uiPreferencesService.isNavCollapsed;

  viewModeUrls: Record<string, string> = {
    'conduit-ui': uiOriginFor(4201),
    'duality': uiOriginFor(3002),
    'plurality': uiOriginFor(3004),
    'assembly': uiOriginFor(4214),
    'nebula-rms': uiOriginFor(4210),
    'peb-ui': uiOriginFor(4206),
    'kernel-ui': uiOriginFor(4207),
    'tackle-ui': uiOriginFor(4202),
    'cascade-ui': uiOriginFor(4203),
    'execution-ui': uiOriginFor(4205),
    'vision-ui': uiOriginFor(4208),
    'wind-ui': uiOriginFor(4209),
    'nebula-cp': uiOriginFor(4014),
    'semantics-ui': uiOriginFor(4213),
    'shrapnel': uiOriginFor(4217),
    'throttler-ui': uiOriginFor(4211),
    'barbie': uiOriginFor(3010),
    'monaco-judge': uiOriginFor(4016),
    'conduit-legacy-ui': uiOriginFor(4015),
    'data-explorer': uiOriginFor(4212),
    'atlas': uiOriginFor(4216),
  };

  isIframeMode = computed(() =>
    this.currentViewMode() === 'conduit-ui' ||
    this.currentViewMode() === 'duality' ||
    this.currentViewMode() === 'plurality' ||
    this.currentViewMode() === 'assembly' ||
    this.currentViewMode() === 'nebula-rms' ||
    this.currentViewMode() === 'peb-ui' ||
    this.currentViewMode() === 'kernel-ui' ||
    this.currentViewMode() === 'tackle-ui' ||
    this.currentViewMode() === 'cascade-ui' ||
    this.currentViewMode() === 'execution-ui' ||
    this.currentViewMode() === 'vision-ui' ||
    this.currentViewMode() === 'wind-ui' ||
    this.currentViewMode() === 'nebula-cp' ||
    this.currentViewMode() === 'semantics-ui' ||
    this.currentViewMode() === 'shrapnel' ||
    this.currentViewMode() === 'throttler-ui' ||
    this.currentViewMode() === 'barbie' ||
    this.currentViewMode() === 'monaco-judge' ||
    this.currentViewMode() === 'conduit-legacy-ui' ||
    this.currentViewMode() === 'data-explorer' ||
    this.currentViewMode() === 'atlas'
  );

  isKanbanMode = computed(() => this.currentViewMode() === 'kanban');

  /** View modes after the first separator in the nav toolbar that should show the address bar. */
  private readonly SHOW_BAR_VIEW_MODES = new Set([
    // Previously-configured exceptions (before first separator, but explicitly needed)
    'conduit-legacy-ui',
    'cascade-ui',
    'tackle-ui',
    'atlas',
    // All views after the first separator
    'throttler-ui',
    'nebula-cp',
    'semantics-ui',
    'shrapnel',
    'barbie',
    'wind-ui',
    'conduit-ui',
    'execution-ui',
    'peb-ui',
    'vision-ui',
    'kernel-ui',
    'data-explorer',
    'duality',
    'plurality',
    'monaco-judge',
    'assembly',
  ]);

  /** Whether the address bar and branding box should be visible for the current view. */
  showAddressBar = computed(() =>
    !this.isIframeMode() || this.SHOW_BAR_VIEW_MODES.has(this.currentViewMode())
  );

  isFullScreenMode = computed(() =>
    this.isIframeMode() || this.currentViewMode() === 'kanban'
  );

  // --- Pane Visibility State (from service) ---
  isSidebarVisible = this.uiPreferencesService.isSidebarVisible;
  isSidebarCollapsed = this.uiPreferencesService.isSidebarCollapsed;
  isTreeVisible = this.uiPreferencesService.isTreeVisible;
  isNotesVisible = this.uiPreferencesService.isNotesVisible;
  isDetailPaneOpen = this.uiPreferencesService.isDetailPaneOpen;
  isSavedItemsVisible = this.uiPreferencesService.isSavedItemsVisible;
  isRssFeedVisible = this.uiPreferencesService.isRssFeedVisible;
  isStreamVisible = this.uiPreferencesService.isStreamVisible;
  isConsoleCollapsed = this.uiPreferencesService.isConsoleCollapsed;

  /** When true, the terminal fills the entire viewport (fixed overlay). */
  isTerminalMaximized = signal(false);

  /**
   * Dock height for the messagebox above the viewport bottom (px). Clears the
   * console pane when it is open so the messagebox docks above the console
   * panel instead of overlapping it (and its resize handles stay clear of the
   * console resizer). Bottom bar 30px + console resizer 6px + console pane
   * height; when collapsed the console strip is h-6 (24px).
   */
  messageBoxDockOffset = computed(() => {
    const bottomBar = 30;
    if (this.isTerminalMaximized()) return bottomBar;
    if (this.isConsoleCollapsed()) return bottomBar + 24; // h-6 collapsed console strip
    return bottomBar + 6 + this.consolePaneHeight(); // resizer (h-1.5) + console pane
  });

  toggleMaximize(): void {
    this.isTerminalMaximized.update(v => !v);
  }
  isStreamPaneCollapsed = this.uiPreferencesService.isStreamPaneCollapsed;

  shouldShowStreamPane = computed(() => {
    if (!this.isStreamVisible()) return false;

    const path = this.activePanePath();
    if (path.length === 0) return false;

    const sessionName = this.localConfigService.sessionName();
    return path[0] === sessionName || path[0] === 'File Systems' || path[0] === 'Files';
  });

  // --- Content Status Bar (CRUD screens) ---
  contentStatusInfo = signal<{ type: string; count: number } | null>(null);

  // Keep track of each pane's path
  panePaths = signal<PanePath[]>([{ id: 1, path: [] }]);

  // --- Dialog Control State ---
  profileForLogin = signal<BrokerProfile | null>(null);

  // --- Mounted Profile State ---
  mountedProfiles = signal<BrokerProfile[]>([]);
  mountedProfileUsers = signal<Map<string, User>>(new Map());
  mountedProfileTokens = signal<Map<string, string>>(new Map());
  mountedProfileMounts = signal<Map<string, Mount[]>>(new Map());
  mountedProfileIds = computed(() => this.mountedProfiles().map(p => p.id));
  private remoteProviders = signal<Map<string, SecureFileSystemService>>(new Map());
  private remoteImageServices = signal<Map<string, ImageService>>(new Map());
  /** Direct filesystem provider backed by the file-system-server on port 4042. */
  private directFs = new DirectFileSystemService();
  filesystemHealth = signal<Map<string, boolean>>(new Map());

  // --- Status Bar State ---
  pane1Status = signal<PaneStatus>({ selectedItemsCount: 0, totalItemsCount: 0, filteredItemsCount: null });
  pane2Status = signal<PaneStatus>({ selectedItemsCount: 0, totalItemsCount: 0, filteredItemsCount: null });
  pane1FolderIsMagnet = signal(false);
  pane2FolderIsMagnet = signal(false);

  activePaneStatus = computed<PaneStatus>(() => {
    const activeId = this.activePaneId();
    if (activeId === 1) {
      return this.pane1Status();
    }
    return this.pane2Status();
  });

  /** Whether the directory currently shown in the active pane is magnetized. */
  activePaneFolderIsMagnet = computed<boolean>(() => {
    return this.activePaneId() === 1 ? this.pane1FolderIsMagnet() : this.pane2FolderIsMagnet();
  });

  statusBarSelectionInfo = computed(() => {
    const item = this.selectedDetailItem();
    const folderIsMagnet = this.activePaneFolderIsMagnet();

    if (!item) {
      // Nothing selected: reflect the magnet status of the current directory.
      return folderIsMagnet ? '🧲 Magnet Folder' : 'Ready';
    }

    if (item.isServerRoot) {
      const profile = this.profileService.profiles().find(p => p.name === item.name);
      if (profile) {
        return `Broker Profile: ${profile.name} | Broker: ${profile.brokerUrl}`;
      }
    }

    const itemType = item.type.charAt(0).toUpperCase() + item.type.slice(1);
    let info = `${itemType}: ${item.name} | Modified: ${item.modified ? new Date(item.modified).toLocaleString() : 'N/A'}`;

    if (item.isMagnet) {
      info += ' | 🧲 Magnet Folder';
    }

    return info;
  });

 /** Default image server URL for the image substitution scheme (used by bottom bar for site icons) */
  defaultImageUrl = computed(() => this.localConfigService.defaultImageUrl());
  statusBarItemCounts = computed(() => {

    const status = this.activePaneStatus();
    let message = `${status.totalItemsCount} items`;

    if (status.filteredItemsCount !== null) {
      message = `${status.filteredItemsCount} of ${status.totalItemsCount} items`;
    }

    if (status.selectedItemsCount > 0) {
      message += ` | ${status.selectedItemsCount} selected`;
    }
    return message;
  });

  // --- Pane Path & Provider Management ---
  pane1Path = computed(() => this.panePaths().find(p => p.id === 1)?.path ?? []);
  pane2Path = computed(() => this.panePaths().find(p => p.id === 2)?.path ?? []);
  activePanePath = computed(() => this.activePaneId() === 1 ? this.pane1Path() : this.pane2Path());
  activeRootName = computed(() => this.activePanePath()[0] ?? this.localConfigService.sessionName());

  // Derive display path (without server/session name) for address bar
  activeDisplayPath = computed(() => {
    const path = this.activePanePath();
    return path.length > 1 ? path.slice(1) : [];
  });

  canGoUpActivePane = computed(() => this.activePanePath().length > 0);

  pane1Provider = computed<FileSystemProvider>(() => this.getProvider(this.pane1Path()));
  pane2Provider = computed<FileSystemProvider>(() => this.getProvider(this.pane2Path()));
  pane1ImageService = computed<ImageService>(() => this.getImageService(this.pane1Path()));
  pane2ImageService = computed<ImageService>(() => this.getImageService(this.pane2Path()));

  pane1PlatformNode = computed(() => this.getPlatformNodeForPath(this.pane1Path()));
  pane2PlatformNode = computed(() => this.getPlatformNodeForPath(this.pane2Path()));
  activePanePlatformNode = computed(() => this.activePaneId() === 1 ? this.pane1PlatformNode() : this.pane2PlatformNode());

  // Host Server Profile Editor Detection
  // When path is ['Host Servers', 'Profile Name'], we show the editor
  pane1HostServerProfileId = computed(() => this.getHostServerProfileIdForPath(this.pane1Path()));
  pane2HostServerProfileId = computed(() => this.getHostServerProfileIdForPath(this.pane2Path()));

  // Gateway Profile Editor Detection
  pane1GatewayProfileId = computed(() => this.getGatewayProfileIdForPath(this.pane1Path()));
  pane2GatewayProfileId = computed(() => this.getGatewayProfileIdForPath(this.pane2Path()));

  private getHostServerProfileIdForPath(path: string[]): string | null {
    // Path must be ['Platform Management', 'Service Registries', 'Profile Name', ...] to show editor (any depth under the profile).
    if (path.length < 3 || path[0] !== 'Platform Management' || path[1] !== 'Service Registries') {
      return null;
    }
    const profileName = path[2];
    const profile = this.hostProfileService.profiles().find(p => p.name === profileName);
    return profile?.id ?? null;
  }

  private getGatewayProfileIdForPath(path: string[]): string | null {
    // Path must be ['Platform Management', 'Gateways', 'Profile Name', ...] to show editor (any depth under the profile).
    if (path.length < 3 || path[0] !== 'Platform Management' || path[1] !== 'Gateways') {
      return null;
    }
    const profileName = path[2];
    const profile = this.profileService.profiles().find(p => p.name === profileName);
    return profile?.id ?? null;
  }

  /** Map of category type labels → DB discriminator values for child nodes under Categories. */
  private readonly CATEGORY_LABEL_TO_TYPE: Record<string, string> = {
    'framework': 'framework_type',
    'server': 'server_type',
    'library': 'library_type',
    'environment': 'environment_type',
    'service': 'service_type',
    'config': 'service_config_type',
    'system': 'system_type',
    'os': 'operating_systems',
  };

  private getPlatformNodeForPath(path: string[]) {
    // Valid management types (System Health is now a top-level sibling, not a Platform Management child).
    const validTypes = ['data dictionary', 'services', 'frameworks', 'libraries', 'deployments', 'servers', 'lookup tables', 'service types', 'server types', 'framework types', 'framework languages', 'framework categories', 'library types', 'library categories', 'service definitions', 'languages', 'categories', 'operating systems', 'environments', 'systems', 'topology'];
    const profiles = this.hostProfileService.profiles();
    const activeProfile = this.hostProfileService.activeProfile();

    // Helper to normalize type for component
    const normalizeType = (t: string) => {
      let n = t.replace(/\s+/g, '-');
      // Normalize dictionary child types
      if (n === 'data-dictionary') return null; // Data Dictionary is just a folder, don't load data
      if (n === 'service-definitions') return 'services';
      if (n === 'service-hosts' || n === 'hosts') return 'servers';
      if (n === 'languages') return 'framework-languages';
      if (n === 'categories') return 'categories';
      if (n === 'framework-types') return 'framework-categories';
      if (n === 'library-types') return 'library-categories';
      return n;
    };

    if (!path || path.length === 0) {
      return null;
    }

    // Explicitly exclude "Search & Discovery" paths to prevent masking user folders
    if (path[0] === 'Search & Discovery') {
      return null;
    }        // System Health is a top-level sibling of Platform Management (connects to service-registry real-time SSE stream).
        // Handle it here at root before the Platform Management branch — also covers any deeper path.
        if (path[0] === 'System Health') {
          const registryUrl = this.registryServerProfileService.activeBaseUrl();
          if (registryUrl) {
            return { type: 'system-health', baseUrl: registryUrl };
          }
          return null;
        }

    console.log('[AppComponent] getPlatformNodeForPath', { path, profilesCount: profiles.length, activeProfile: activeProfile?.name });

    // Handle single-element path (direct child of root - e.g., ["Services"])
    if (path.length === 1) {
      const type = path[0].toLowerCase();
      if (validTypes.includes(type)) {
        const profile = activeProfile;
        if (profile) {
          const baseUrl = profile.registryServerUrl.startsWith('http') ? profile.registryServerUrl.replace(/\/$/, '') : `http://${profile.registryServerUrl.replace(/\/$/, '')}`;
          console.log('[AppComponent] Matched single-element path', { type, baseUrl });
          return { type: normalizeType(type), baseUrl };
        }
      }
      return null;
    }

    // Handle multi-element path
    const pmIndex = path.findIndex(p => p.toLowerCase() === 'platform management');

    if (pmIndex !== -1) {
      // Path contains "Platform Management"
      const remaining = path.slice(pmIndex + 1);

      let type: string | null = null;
      let targetProfileName: string | null = null;

      if (remaining.length === 0) {
        // Parent "Platform Management" selected - do NOT default to services.
        type = null;
      } else {
        // Find the first element in remaining that matches a valid type
        const typeIndex = remaining.findIndex(p => validTypes.includes(p.toLowerCase()));

        if (typeIndex !== -1) {
          type = remaining[typeIndex].toLowerCase();

          // Special case: if type is "data dictionary", check if there's a sub-type segment
          if (type === 'data dictionary' && remaining.length > typeIndex + 1) {
            const subType = remaining[remaining.length - 1].toLowerCase();
            // Check if the subType is a category label (child under Categories)
            const categoryType = this.CATEGORY_LABEL_TO_TYPE[subType];
            if (categoryType) {
              // Return categories:{discriminator} format so PlatformManagementComponent
              // can show the categories view filtered to this type
              return { type: `categories:${categoryType}`, baseUrl: this.resolveBaseUrl(remaining, profiles, activeProfile, pmIndex, path) };
            }
            if (validTypes.includes(subType)) {
              type = subType;
            }
          }

          // Resolve profile name - if type matches at index 1 or later, index 0 is likely the profile
          if (typeIndex > 0) {
            targetProfileName = remaining[0];
          }
        }
      }

      if (type) {
        const normalizedType = normalizeType(type);
        // If normalized type is null (e.g., Data Dictionary), return null to show children instead
        if (!normalizedType) {
          return null;
        }

        // System Health uses the service-registry URL (SSE based), Topology uses terrain server
        if (normalizedType === 'system-health') {
          const registryUrl = this.registryServerProfileService.activeBaseUrl();
          if (registryUrl) {
            console.log('[AppComponent] Matched Platform Management path - system-health', { registryUrl });
            return { type: normalizedType, baseUrl: registryUrl };
          }
          return null;
        }
        if (normalizedType === 'topology') {
          const terrainUrl = this.localConfigService.terrainServerUrl();
          console.log('[AppComponent] Matched Platform Management path - topology', { terrainUrl });
          return { type: normalizedType, baseUrl: terrainUrl };
        }

        // 'Service Registries' was previously a root sibling and had a special branch here;
        // it now lives inside 'Platform Management', so the special case is unreachable.
        const baseUrl = this.resolveBaseUrl(remaining, profiles, activeProfile, pmIndex, path);
        if (baseUrl) {
          console.log('[AppComponent] Matched Platform Management path', { type: normalizedType, baseUrl, targetProfileName });
          return { type: normalizedType, baseUrl };
        }
      }
    } else {
      // Path does NOT contain "Platform Management" but might still be a valid management path
      // e.g., ['Local Host', 'Services'] or ['Profile Name', 'Frameworks']
      const lastElement = path[path.length - 1].toLowerCase();
      if (validTypes.includes(lastElement)) {
        // Try to find profile by first element of path, or use default
        // Improved profile resolution: check path[1] for profile name (e.g. Services/Profile/Node)
        let profile = path.length > 1 ? profiles.find(p => p.name === path[1]) : null;

        if (!profile) {
          profile = profiles.find(p => p.name === path[0]);
        }

        // Fallback to active profile
        if (!profile) {
          profile = activeProfile;
        }

        if (profile) {
          const baseUrl = profile.registryServerUrl.startsWith('http') ? profile.registryServerUrl.replace(/\/$/, '') : `http://${profile.registryServerUrl.replace(/\/$/, '')}`;
          console.log('[AppComponent] Matched direct management path', { type: lastElement, baseUrl, profileName: path[0] });
          return { type: normalizeType(lastElement), baseUrl };
        }
      }
    }

    console.log('[AppComponent] No match found, returning null');
    return null;
  }

  /** Resolve the baseUrl from a Platform Management path. */
  private resolveBaseUrl(remaining: string[], profiles: any[], activeProfile: any, pmIndex: number, path: string[]): string | null {
    // Find the first element in remaining that matches a valid type
    const validTypes = ['data dictionary', 'services', 'frameworks', 'libraries', 'deployments', 'servers', 'lookup tables', 'service types', 'server types', 'framework types', 'framework languages', 'framework categories', 'library types', 'library categories', 'service definitions', 'languages', 'categories', 'operating systems', 'environments', 'systems', 'topology'];
    const typeIndex = remaining.findIndex(p => validTypes.includes(p.toLowerCase()));

    let targetProfileName: string | null = null;
    if (typeIndex > 0) {
      targetProfileName = remaining[0];
    }

    const profile = targetProfileName
      ? profiles.find((p: any) => p.name === targetProfileName)
      : (path[0].toLowerCase() !== 'platform management' ? profiles.find((p: any) => p.name === path[0]) : activeProfile);

    const finalProfile = profile || activeProfile;
    if (finalProfile) {
      return finalProfile.registryServerUrl.startsWith('http') ? finalProfile.registryServerUrl.replace(/\/$/, '') : `http://${finalProfile.registryServerUrl.replace(/\/$/, '')}`;
    }
    return null;
  }

  /** True when the active platform node is exactly 'categories' (no type filter). */
  isPlainCategoriesSelected = computed(() => {
    const node = this.activePaneId() === 1 ? this.pane1PlatformNode() : this.pane2PlatformNode();
    return node?.type === 'categories';
  });

  // --- Gateway Context Signals ---
  // Gateways now lives at ['Platform Management', 'Gateways', ...] — context = at-or-below the container.
  isGatewayContext = computed(() => this.activePanePath()[0] === 'Platform Management' && this.activePanePath()[1] === 'Gateways');
  isGatewaysNodeSelected = computed(() => this.activePanePath().length === 2 && this.activePanePath()[0] === 'Platform Management' && this.activePanePath()[1] === 'Gateways');
  isGatewaySelected = computed(() => this.activePanePath().length === 3 && this.activePanePath()[0] === 'Platform Management' && this.activePanePath()[1] === 'Gateways');

  // Service Registries analogously.
  isServiceRegistryContext = computed(() => this.activePanePath()[0] === 'Platform Management' && this.activePanePath()[1] === 'Service Registries');
  isServiceRegistriesNodeSelected = computed(() => this.activePanePath().length === 2 && this.activePanePath()[0] === 'Platform Management' && this.activePanePath()[1] === 'Service Registries');
  isServiceRegistrySelected = computed(() => this.activePanePath().length === 3 && this.activePanePath()[0] === 'Platform Management' && this.activePanePath()[1] === 'Service Registries');

  // Combined disjunction — use when callers only care about being anywhere under
  // Gateways or Service Registries and don't need to distinguish between them.
  isInGatewaysOrRegistries = computed(() => this.isGatewayContext() || this.isServiceRegistryContext());

  isPlatformManagementContext = computed(() => {
    // Check if current view is a platform management view
    return !!(this.activePaneId() === 1 ? this.pane1PlatformNode() : this.pane2PlatformNode());
  });

  // Trigger for child editor save/reset
  editorSaveTrigger = signal<{ id: number; paneId: number } | null>(null);
  editorResetTrigger = signal<{ id: number; paneId: number } | null>(null);
  editorIsDirty = signal(false);

  onSaveGateway(): void {
    this.editorSaveTrigger.set({ id: Date.now(), paneId: this.activePaneId() });
  }

  onResetGateway(): void {
    this.editorResetTrigger.set({ id: Date.now(), paneId: this.activePaneId() });
  }

  isDeleteGatewayConfirmOpen = signal(false);
  gatewayToDelete = signal<string | null>(null);

  async onDeleteGateway(): Promise<void> {
    const profileId = this.gatewayToDelete() || this.pane1GatewayProfileId() || this.pane2GatewayProfileId();
    if (profileId) {
      await this.profileService.deleteProfile(profileId);
      this.isDeleteGatewayConfirmOpen.set(false);
      this.gatewayToDelete.set(null);
      await this.loadFolderTree();
      // Navigate back to Gateways container under Platform Management if we were editing
      const activeId = this.activePaneId();
      this.panePaths.update(paths => {          const pathObj = paths.find(p => p.id === activeId);
        if (pathObj && pathObj.path.length >= 2 && pathObj.path[0] === 'Platform Management' && pathObj.path[1] === 'Gateways') {
          const otherPanes = paths.filter(p => p.id !== activeId);
          return [...otherPanes, { id: activeId, path: ['Platform Management', 'Gateways'] }];
        }
        return paths;
      });
    }
  }

  onDeleteGatewayById(id: string): void {
    this.gatewayToDelete.set(id);
    this.isDeleteGatewayConfirmOpen.set(true);
  }

  isDeleteServiceRegistryConfirmOpen = signal(false);
  serviceRegistryToDelete = signal<string | null>(null);

  async onDeleteServiceRegistry(): Promise<void> {
    const profileId = this.serviceRegistryToDelete() || this.pane1HostServerProfileId() || this.pane2HostServerProfileId();
    if (profileId) {
      await this.hostProfileService.deleteProfile(profileId);
      this.isDeleteServiceRegistryConfirmOpen.set(false);
      this.serviceRegistryToDelete.set(null);
      await this.loadFolderTree();
      // Navigate back to Service Registries container under Platform Management
      const activeId = this.activePaneId();
      this.panePaths.update(paths => {          const pathObj = paths.find(p => p.id === activeId);
        if (pathObj && pathObj.path.length >= 2 && pathObj.path[0] === 'Platform Management' && pathObj.path[1] === 'Service Registries') {
          const otherPanes = paths.filter(p => p.id !== activeId);
          return [...otherPanes, { id: activeId, path: ['Platform Management', 'Service Registries'] }];
        }
        return paths;
      });
    }
  }

  onDeleteServiceRegistryById(id: string): void {
    this.serviceRegistryToDelete.set(id);
    this.isDeleteServiceRegistryConfirmOpen.set(true);
  }

  onAddServiceRegistry(): void {
    const name = prompt('Enter name for the new Service Registry:', `New Service Registry ${Date.now()}`);
    if (!name) return; // User cancelled

    const activeId = this.activePaneId();
    this.hostProfileService.saveProfile({
      id: Date.now().toString(),
      name,
      registryServerUrl: 'http://localhost:8000',
      imageUrl: '',
      status: 'ACTIVE'
    }    ).then(() => {
      this.loadFolderTree();
      this.panePaths.update(paths => {
        const otherPanes = paths.filter(p => p.id !== activeId);
        return [...otherPanes, { id: activeId, path: ['Platform Management', 'Service Registries', name] }];
      });
    });
  }

  onAddGateway = async (): Promise<void> => {
    const existingProfiles = this.profileService.profiles();
    let counter = 1;
    let newName = 'New Gateway';
    while (existingProfiles.some(p => p.name === newName)) {
      newName = `New Gateway (${counter++})`;
    }

    const newProfile = {
      name: newName,
      brokerUrl: 'localhost:8081',
      imageUrl: '',
      autoConnect: false,
    };

    await this.profileService.addProfile(newProfile);
    await this.loadFolderTree();

    const activeId = this.activePaneId();
    this.panePaths.update(paths => {
      const otherPanes = paths.filter(p => p.id !== activeId);
      return [...otherPanes, { id: activeId, path: ['Platform Management', 'Gateways', newName] }];
    });
  };

  onEditServiceRegistryByName(name: string): void {
    const activeId = this.activePaneId();
    this.panePaths.update(paths => {
      const otherPanes = paths.filter(p => p.id !== activeId);
      return [...otherPanes, { id: activeId, path: ['Platform Management', 'Service Registries', name] }];
    });
  }

  onEditGatewayByName(name: string): void {
    const activeId = this.activePaneId();
    this.panePaths.update(paths => {
      const otherPanes = paths.filter(p => p.id !== activeId);
      return [...otherPanes, { id: activeId, path: ['Platform Management', 'Gateways', name] }];
    });
  }


  // --- Toolbar State Management ---
  toolbarAction = signal<{ name: string; payload?: any; id: number } | null>(null);

  pane1SortCriteria = signal<SortCriteria>({ key: 'name', direction: 'asc' });
  pane2SortCriteria = signal<SortCriteria>({ key: 'name', direction: 'asc' });
  activeSortCriteria = computed(() => this.activePaneId() === 1 ? this.pane1SortCriteria() : this.pane2SortCriteria());

  pane1DisplayMode = signal<'grid' | 'list'>('grid');
  pane2DisplayMode = signal<'grid' | 'list'>('grid');
  activeDisplayMode = computed(() => this.activePaneId() === 1 ? this.pane1DisplayMode() : this.pane2DisplayMode());

  pane1FilterQuery = signal('');
  pane2FilterQuery = signal('');
  activeFilterQuery = computed(() => this.activePaneId() === 1 ? this.pane1FilterQuery() : this.pane2FilterQuery());

  isHomeContext = computed(() => this.activePanePath().length === 0);

  // Toolbar visibility signals
  showExtendedControls = computed(() => {
    const path = this.activePanePath();
    const sessionName = this.localConfigService.sessionName();

    // sessionName or File Systems as root
    if (path.length === 0 || path[0] === sessionName || path[0] === 'File Systems' || path[0] === 'Files') {
      return true;
    }

    return false;
  });

  showAllToolbarControls = computed(() => {
    return true;
  });

  isActionableContext = computed(() => {
    const path = this.activePanePath();
    if (path.length === 0) {
      return false; // Home root is not actionable
    }

    const rootName = path[0];

    // System mount folder under File Systems — gateway profile level
    // Path is ['File Systems', 'gateway-name'] — at mount container level, not actionable
    if (rootName === 'File Systems' && path.length === 2) {
      return false;
    }

    const profile = this.profileService.profiles().find(p => p.name === rootName);

    if (profile) {
      // It's a server profile path, check if it's mounted
      return this.mountedProfileIds().includes(profile.id);
    }

    // Plain Categories node (no child type selected) — disable add/action buttons
    if (this.isPlainCategoriesSelected()) return false;

    // It's not a server profile path, so it must be the local session, which is always actionable.
    // Also include management nodes (e.g., Infrastructure, Services) which are handled by getPlatformNodeForPath
    if (this.activePaneId() === 1 && this.pane1PlatformNode()) return true;
    if (this.activePaneId() === 2 && this.pane2PlatformNode()) return true;

    return true;
  });

  // States computed from active pane status for toolbar
  canCutCopyShareDelete = computed(() => this.isActionableContext() && this.activePaneStatus().selectedItemsCount > 0);
  canRename = computed(() => this.isActionableContext() && this.activePaneStatus().selectedItemsCount === 1);
  canPaste = computed(() => this.isActionableContext() && !!this.clipboardService.clipboard());
  canMagnetize = computed(() => this.isActionableContext() && this.activePaneStatus().selectedItemsCount > 0);

  // Import capability
  canImport = computed(() => {
    const activeId = this.activePaneId();
    const node = activeId === 1 ? this.pane1PlatformNode() : this.pane2PlatformNode();
    if (!node) return false;

    // Enable for Frameworks, Services, and Servers
    return ['frameworks', 'services', 'servers'].includes(node.type || '');
  });

  @ViewChild('importFileInput') importFileInput!: ElementRef<HTMLInputElement>;
  private platformManagementService = inject(PlatformManagementService);

  onToolbarImport(): void {
    if (this.canImport()) {
      if (this.uiPreferencesService.showImportDependencyWarning()) {
        this.isImportDependencyWarningOpen.set(true);
      } else {
        this.importFileInput.nativeElement.click();
      }
    }
  }

  onConfirmImportWarning(): void {
    if (this.importWarningDontShowAgain()) {
      this.uiPreferencesService.setImportDependencyWarning(false);
    }
    this.isImportDependencyWarningOpen.set(false);
    this.importFileInput.nativeElement.click();
  }

  onImportFileSelected(event: Event): void {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0];
    if (!file) return;

    const reader = new FileReader();
    reader.onload = async (e) => {
      try {
        const text = e.target?.result;
        if (typeof text === 'string') {
          const data = JSON.parse(text);
          const activeId = this.activePaneId();
          const node = activeId === 1 ? this.pane1PlatformNode() : this.pane2PlatformNode();

          if (!node || !node.baseUrl || !Array.isArray(data)) {
            this.toastService.showError('Invalid import data or context.');
            return;
          }

          const baseUrl = node.baseUrl;
          this.toastService.showInfo(`Importing ${data.length} items...`);

          let successes = 0;
          let failures = 0;

          // Pre-load lookups based on context
          let serverTypes: any[] = [];
          let environmentTypes: any[] = [];
          let operatingSystems: any[] = [];
          let serviceTypes: any[] = [];
          let frameworks: any[] = [];
          let frameworkCategories: any[] = [];
          let frameworkLanguages: any[] = [];

          try {
            if (node.type === 'servers') {
              const [st, et, os] = await Promise.all([
                this.platformManagementService.getLookup(baseUrl, LOOKUP_SERVER_TYPES).then(r => r.data),
                this.platformManagementService.getLookup(baseUrl, LOOKUP_ENVIRONMENTS).then(r => r.data),
                this.platformManagementService.getLookup(baseUrl, LOOKUP_OPERATING_SYSTEMS).then(r => r.data)
              ]);
              serverTypes = st;
              environmentTypes = et;
              operatingSystems = os;
            } else if (node.type === 'services') {
              const [fw, st] = await Promise.all([
                this.platformManagementService.getFrameworks(baseUrl).then(r => r.data),
                this.platformManagementService.getLookup(baseUrl, LOOKUP_SERVICE_TYPES).then(r => r.data)
              ]);
              frameworks = fw;
              serviceTypes = st;
            } else if (node.type === 'frameworks') {
              const [fc, fl] = await Promise.all([
                this.platformManagementService.getLookup(baseUrl, LOOKUP_FRAMEWORK_CATEGORIES).then(r => r.data),
                this.platformManagementService.getLookup(baseUrl, LOOKUP_FRAMEWORK_LANGUAGES).then(r => r.data)
              ]);
              frameworkCategories = fc;
              frameworkLanguages = fl;
            }
          } catch (err) {
            console.error('Failed to load lookups', err);
            this.toastService.showError('Failed to load necessary reference data for import.');
            return;
          }

          for (const item of data) {
            try {
              if (node.type === 'frameworks') {
                // Map strings to IDs
                const category = frameworkCategories.find(c => c.name === item.category);
                const language = frameworkLanguages.find(l => l.name === item.language);

                // Remove string fields that conflict with backend relationships
                const { category: _c, language: _l, ...cleanedItem } = item;

                const payload: FrameworkPayload = {
                  ...cleanedItem,
                  categoryId: category ? category.id : item.categoryId,
                  languageId: language ? language.id : item.languageId,
                  vendorId: item.vendorId || undefined
                };
                await this.platformManagementService.createFramework(baseUrl, payload);

              } else if (node.type === 'services') {
                // Map strings to IDs
                const framework = frameworks.find(f => f.name === item.framework);
                const type = serviceTypes.find(t => t.name === item.type);

                if (!framework && !item.frameworkId) {
                  throw new Error(`Framework '${item.framework}' not found`);
                }

                // Remove string fields
                const { framework: _f, type: _t, ...cleanedItem } = item;

                const payload: ServicePayload = {
                  ...cleanedItem,
                  frameworkId: framework ? Number(framework.id) : item.frameworkId,
                  serviceTypeId: type ? type.id : item.serviceTypeId
                };
                await this.platformManagementService.createService(baseUrl, payload);

              } else if (node.type === 'servers') {
                // Map strings to IDs
                const st = serverTypes.find(t => t.name === item.type);
                const et = environmentTypes.find(e => e.name === item.environment);
                const os = operatingSystems.find(o => o.name === item.operatingSystem);

                // Remove string fields
                const { type: _t, environment: _e, operatingSystem: _o, ...cleanedItem } = item;

                // If mappings fail, fallback to existing IDs if present, or let it fail gracefully (or send what we have)
                const payload: Partial<Server> = {
                  ...cleanedItem,
                  serverTypeId: st ? st.id : item.serverTypeId,
                  environmentTypeId: et ? et.id : item.environmentTypeId,
                  operatingSystemId: os ? os.id : item.operatingSystemId
                };

                await this.platformManagementService.createServer(baseUrl, payload);
              }
              successes++;
            } catch (err) {
              console.error('Import error for item', item, err);
              failures++;
            }
          }

          if (failures === 0) {
            this.toastService.showSuccess(`Successfully imported ${successes} items.`);
          } else {
            this.toastService.showWarning(`Imported ${successes} items. Failed to import ${failures} items.`);
          }

          // trigger refresh
          this.triggerRefresh();
        }
      } catch (err) {
        this.toastService.showError(`Error parsing file: ${(err as Error).message}`);
      }
      // Reset
      input.value = '';
    };
    reader.readAsText(file);
  }

  // --- Split View Resizing ---
  pane1Width = signal(this.uiPreferencesService.splitViewPaneWidth() ?? 50); // percentage
  private isResizingPane = false;
  private unlistenPaneResizeMove: (() => void) | null = null;
  private unlistenPaneResizeUp: (() => void) | null = null;

  @ViewChild('paneContainer') paneContainerEl!: ElementRef<HTMLDivElement>;

  // --- Stream Pane Resizing ---
  streamPaneHeight = signal(this.uiPreferencesService.explorerStreamHeight() ?? 25); // percentage
  private isResizingStream = false;
  private unlistenStreamResizeMove: (() => void) | null = null;
  private unlistenStreamResizeUp: (() => void) | null = null;

  @ViewChild('mainContentWrapper') mainContentWrapperEl!: ElementRef<HTMLDivElement>;
  @ViewChild('viewContainer') viewContainerEl!: ElementRef<HTMLDivElement>;

  // --- Console Pane Resizing ---
  consolePaneHeight = signal(200); // pixels — synced from persisted % in ngAfterViewInit
  private isResizingConsole = false;
  private unlistenConsoleResizeMove: (() => void) | null = null;
  private unlistenConsoleResizeUp: (() => void) | null = null;

  // --- Webview and Text Editor State ---
  webviewContent = this.webviewService.viewRequest;
  textEditorContent = this.textEditorService.viewRequest;

  // --- Theme Dropdown ---
  isThemeDropdownOpen = signal(false);
  themeMenuPosition = signal({ top: '0px', right: '0px' });
  themes: { id: Theme; name: string }[] = [
    { id: 'theme-light', name: 'Light' },
    { id: 'theme-steel', name: 'Steel' },
    { id: 'theme-dark', name: 'Dark' },
  ];
  currentTheme = this.uiPreferencesService.theme;

  // --- Pane Context Signals for Stream ---
  pane1Context = computed(() => {
    const path = this.pane1Path();
    const rootName = path.length > 0 ? path[0] : 'Home';
    const profile = this.profileService.profiles().find(p => p.name === rootName);
    const token = profile ? this.mountedProfileTokens().get(profile.id) : null;
    return { path, profile, token };
  });

  pane2Context = computed(() => {
    const path = this.pane2Path();
    const rootName = path.length > 0 ? path[0] : 'Home';
    const profile = this.profileService.profiles().find(p => p.name === rootName);
    const token = profile ? this.mountedProfileTokens().get(profile.id) : null;
    return { path, profile, token };
  });


  private treeAdapters = new Map<string, TreeProviderAdapter>();

  /** Tackle UI (AI Config) runs as a standalone app on port 4202 */
  readonly tackleUiUrl = uiOriginFor(4202);
  showAiConfigPopup = signal(false);

  constructor() {
    // Initialize adapters for each Host Server root
    // Initialize adapters for each Host Server root
    // Services, Users, Search etc are now handled by ServiceMeshComponent and no longer mapped to file system
    // this.treeAdapters.set('Services', new TreeProviderAdapter(this.hostServerProvider, 'services'));
    // this.treeAdapters.set('Users', new TreeProviderAdapter(this.hostServerProvider, 'users'));
    // this.treeAdapters.set('File Systems', new TreeProviderAdapter(this.hostServerProvider, 'filesystems'));
    this.treeAdapters.set('Platform Management', new TreeProviderAdapter(this.registryServerProvider, 'platform'));

    this.homeProvider = {
      getContents: async (path: string[]): Promise<FileSystemNode[]> => {
        console.log('[homeProvider.getContents] path:', path);
        // Get Host Server children (for platform categories like Services, Users, etc.)
        const hostChildren = await this.registryServerProvider.getChildren('root');
        console.log('[homeProvider.getContents] hostChildren:', hostChildren.map(c => c.name));
        const hostNodes: FileSystemNode[] = hostChildren.map(node => {
          // Convert NodeType to FileType
          let fileType: 'folder' | 'file' | 'registry-server' = 'folder';
          if (node.type === NodeType.FILE) {
            fileType = 'file';
          } else if (node.type === NodeType.REGISTRY_SERVER) {
            fileType = 'registry-server';
          }

          return {
            name: node.name,
            type: node.type === NodeType.REGISTRY_SERVER ? 'registry-server' :
              node.type === NodeType.FILE ? 'file' : 'folder',
            id: node.id,
            metadata: node.metadata,
            children: [],
            childrenLoaded: false,
            isServerRoot: false
          };
        });

        // Get Local Session
        const sessionNode = await this.sessionFs.getFolderTree();

        // Build broker gateway nodes for the Gateways folder
        const allBrokerProfiles = this.profileService.profiles();
        const mountedIds = this.mountedProfileIds();
        const brokerProfileNodes: FileSystemNode[] = allBrokerProfiles.map(p => {
          const isConnected = mountedIds.includes(p.id);
          return {
            name: p.name,
            type: 'folder' as const,
            isServerRoot: true,
            profileId: p.id,
            connected: isConnected,
            healthStatus: this.healthCheckService.getServiceStatus(p.imageUrl),
            modified: isConnected ? new Date().toISOString() : undefined,
            children: [],
            childrenLoaded: false,
          };
        });

        // Build host server profile nodes for the Service Registries folder
        const allHostProfiles = this.hostProfileService.profiles();
        const hostProfileNodes: FileSystemNode[] = allHostProfiles.map(p => ({
          name: p.name,
          type: 'folder' as const,
          isServerRoot: true,
          profileId: p.id,
          connected: this.healthCheckService.getServiceStatus(p.imageUrl) !== 'DOWN', // Show X overlay when registry is DOWN
          healthStatus: this.healthCheckService.getServiceStatus(p.imageUrl),
          children: [],
          childrenLoaded: true, // Leaf nodes — editor opens on selection, no tree children to lazy-load
        }));

        // Handle subdirectory paths for virtual organization folders
        if (path.length > 0) {
          const rootName = path[0];

          // Local Session - handle directly if it's at root level
          const sessionName = this.localConfigService.sessionName();
          if (rootName === sessionName) {
            // Path is like ['Local Session', ...], delegate to session provider
            // Adjust path to remove the root name for the session provider
            const sessionPath = path.slice(1);
            return this.sessionFs.getContents(sessionPath);
          }


            // File Systems folder
          if (rootName === 'File Systems') {
            // Return only connected gateways that offer file services
            const mountedIds = this.mountedProfileIds();
            const allBrokerProfiles = this.profileService.profiles();

            // Filter to only return profiles that are currently mounted/connected
            // AND have a healthy file-system server
            const connectedFileServiceGateways = allBrokerProfiles.filter(p =>
              mountedIds.includes(p.id) && this.filesystemHealth().get(p.name) === true
            );

            // Convert to FileSystemNode format
            return connectedFileServiceGateways.map(profile => ({
              name: profile.name,
              type: 'folder' as FileType,
              isServerRoot: true,
              profileId: profile.id,
              connected: true,
              healthStatus: this.healthCheckService.getServiceStatus(profile.imageUrl),
              children: [],
              childrenLoaded: false,
            }));
          }

          // Files node — direct filesystem on port 4042 (browser-based surrogate)
          if (rootName === 'Files') {
            const providerPath = path.slice(1);
            return this.directFs.getContents(providerPath);
          }

          // Gateways / Service Registries virtual containers (now nested under Platform Management).
          // Handles ['Platform Management', 'Gateways'] and ['Platform Management', 'Service Registries'].
          if (rootName === 'Platform Management' && path.length === 2 && path[1] === 'Gateways') {
            return brokerProfileNodes;
          }
          if (rootName === 'Platform Management' && path.length === 2 && path[1] === 'Service Registries') {
            return hostProfileNodes;
          }

          // Platform Management folder - flat structure defaulting to primary registry
          if (rootName === 'Platform Management') {
            let currentNodeId = 'platform';

            if (path.length > 1) {
              // Traverse children starting from platform root
              for (let i = 1; i < path.length; i++) {
                const segment = path[i];
                const children = await this.registryServerProvider.getChildren(currentNodeId);
                const match = children.find(c => c.name === segment);
                if (match) {
                  currentNodeId = match.id;
                } else {
                  return [];
                }
              }
            }

            const nodes = await this.registryServerProvider.getChildren(currentNodeId);
            return nodes.map(node => {
              // Determine the type based on NodeType enum, converting to FileType
              let fileType: 'file' | 'folder' | 'registry-server' = 'folder';
              if (node.type === NodeType.FILE) {
                fileType = 'file';
              } else if (node.type === NodeType.REGISTRY_SERVER) {
                fileType = 'registry-server';
              } else {
                fileType = 'folder';
              }

              return {
                name: node.name,
                type: fileType,
                id: node.id,
                metadata: node.metadata,
                children: [], // Children will be loaded on demand
                childrenLoaded: false, // Children are not loaded until the node is expanded
                isServerRoot: false
              };
            });
          }

          // Services - show empty (managed by HostServerProvider)
          const hostNodeNames = hostNodes.map(n => n.name).filter(n => n !== 'Platform Management' && n !== 'Search & Discovery' && n !== 'Servers' && n !== 'Users');
          if (hostNodeNames.includes(rootName)) {
            return []; // These nodes are placeholders, no children to show in main area
          }

          // Unknown path
          throw new Error(`Home provider does not support path: ${path.join('/')}`);
        }

        // Root path [] - return all children.
        // Gateways and Service Registries are no longer synthesized as root siblings —
        // they live inside Platform Management (added by registry-server-provider.service.ts).
        // brokerProfileNodes / hostProfileNodes still feed those nested children when accessed.

        // Find the "File Systems" node and add Local Session as its child
        const fileSystemsNode = hostNodes.find(n => n.name === 'File Systems');
        if (fileSystemsNode) {
          fileSystemsNode.childrenLoaded = true;
        }

        // Find the "Search & Discovery" node to move it to root level
        const searchDiscoveryNode = hostNodes.find(n => n.name === 'Search & Discovery');

        // Find the "Users" node
        const usersNode = hostNodes.find(n => n.name === 'Users');

        // Filter out nodes we want to order manually.
        // Service Registries / service-registries clauses were removed: registryServerProvider
        // no longer emits them at root (they live under Platform Management), so the defensive
        // clauses were dead code here. in-memory-file-system.service.ts retains its clause
        // because it parses persisted tree snapshots that may still contain stale entries.
        const otherHostNodes = hostNodes.filter((n: FileSystemNode) =>
          n.name !== 'Users' &&
          n.name !== 'Search & Discovery' &&
          n.name !== 'File Systems' &&
          n.name !== 'Platform Management'
        );

        const platformNode = hostNodes.find(n => n.name === 'Platform Management');

        const rootChildren = [
          ...otherHostNodes,
          ...(fileSystemsNode ? [fileSystemsNode] : []),
          ...(platformNode ? [platformNode] : []),
          sessionNode,
          ...(usersNode ? [usersNode] : []),
          ...(searchDiscoveryNode ? [searchDiscoveryNode] : [])
        ];

        // Sort alphabetically (case-insensitive)
        rootChildren.sort((a, b) => a.name.toLowerCase().localeCompare(b.name.toLowerCase()));

        console.log('[homeProvider.getContents] returning rootChildren:', rootChildren.map(c => c.name));
        return rootChildren;
      },
      getFolderTree: () => this.buildCombinedFolderTree(),
      ...readOnlyProviderOps,
    };

    // --- Effects ---
    // Update theme class on body when theme changes
    effect(() => {
      const theme = this.currentTheme();
      this.renderer.removeClass(this.document.body, 'theme-light');
      this.renderer.removeClass(this.document.body, 'theme-steel');
      this.renderer.removeClass(this.document.body, 'theme-dark');
      this.renderer.addClass(this.document.body, theme);
    });

    // Monitor health of server profiles
    effect(() => {
      const brokerProfiles = this.profileService.profiles();
      const hostProfiles = this.hostProfileService.profiles();
      const allProfiles = [
        ...brokerProfiles,
        ...hostProfiles.map(p => ({ imageUrl: p.imageUrl, healthCheckDelayMinutes: undefined }))
      ];
      this.healthCheckService.syncMonitoredProfiles(allProfiles);
    });

    // Reactive Folder Tree and Auto-Connect
    effect(() => {
      // Rebuilds the master folder tree whenever the list of server profiles changes,
      // or when the connection status (mounted profiles) of any profile changes. This ensures
      // the tree view is always in sync with the application's connection state.
      // NOTE: healthCheckService.statusMap() is intentionally NOT included here because
      //       health pings don't change the tree structure — only visual indicators.
      //       Including it caused constant tree rebuilds that reset expansion state.
      this.profileService.profiles(); // Dependency
      this.hostProfileService.profiles(); // Dependency — host profiles also drive tree nodes
      this.mountedProfiles(); // Dependency
      this.loadFolderTree();

      // Handles session restoration and auto-connect on startup, once profiles are loaded.
      if (!this.initialAutoConnectAttempted) {
        const profiles = this.profileService.profiles();
        if (profiles.length > 0) {
          this.initialAutoConnectAttempted = true;
          // First, try to restore sessions from persisted tokens (survives page refresh)
          this.restoreSessions();
        }
      }
    }, { allowSignalWrites: true });
  }

  ngOnInit(): void {
    // Connect to the UI event bus as nexus-console
    this.eventBus.connect('nexus-console');

    // Log incoming events for debugging (nexus-console won't receive its own events)
    this.eventBus.onAny((event) => {
      console.log(`[EventBus] received: ${event.sender} → ${event.eventName}`, event.eventValue);
    });

    // Subscribe to location changes from child apps
    this.eventBus.onLocationChange((location) => {
      console.log(`[EventBus] location change from child app:`, location);
      // TODO: update address bar display when child apps report their location
    });
  }

  ngAfterViewInit(): void {
    // Convert persisted percentage to pixel height once the container is available.
    // Skip if console starts collapsed — conversion will happen on first expand.
    if (this.isConsoleCollapsed()) return;
    this.syncConsolePixelHeight();
  }

  ngOnDestroy(): void {
    this.stopPaneResize();
    this.stopStreamResize();
    this.stopConsoleResize();
  }

  /** Convert the persisted percentage to a pixel height based on current container size. */
  private syncConsolePixelHeight(): void {
    const container = this.viewContainerEl?.nativeElement;
    if (!container) return;
    const pct = this.uiPreferencesService.explorerConsoleHeight();
    if (pct == null) return;
    const height = container.getBoundingClientRect().height;
    this.consolePaneHeight.set(Math.round((pct / 100) * height));
  }

  getProvider = (path: string[]): FileSystemProvider => {
    if (path.length === 0) return this.homeProvider;
    const rootName = path[0];

    // Handle Local Session at root level
    const sessionName = this.localConfigService.sessionName();
    if (rootName === sessionName) {
      // Path is like ['Local Session', ...], use session provider
      return this.sessionFs;
    }

    // Handle "Files" node — direct filesystem on port 4042
    if (rootName === 'Files') {
      return this.directFs;
    }

    // Handle "File Systems" folder - contains connected gateways that offer file services
    if (rootName === 'File Systems') {
      if (path.length === 1) {
        // At the File Systems folder level itself
        return this.homeProvider;
      }
      // Path is like ['File Systems', 'GatewayName', ...]
      const gatewayName = path[1];
      const remoteProvider = this.remoteProviders().get(gatewayName);
      if (remoteProvider) {
        // Return the remote provider for the specific gateway
        return remoteProvider;
      }
      // Gateway is known but not connected
      const isKnownGateway = this.profileService.profiles().some(p => p.name === gatewayName);
      if (isKnownGateway) {
        return disconnectedProvider;
      }
      return this.homeProvider;
    }

    // Handle "Gateways" virtual container nested under "Platform Management" — broker profiles at depth 3.
    if (rootName === 'Platform Management' && path.length > 1 && path[1] === 'Gateways') {
      if (path.length === 2) {
        // At the Gateways container level itself - return home provider
        // (children come from homeProvider.getContents(['Platform Management', 'Gateways']))
        return this.homeProvider;
      }
      // Path is like ['Platform Management', 'Gateways', 'ServerName', ...]
      const serverName = path[2];
      const remoteProvider = this.remoteProviders().get(serverName);
      if (remoteProvider) {
        return remoteProvider;
      }
      const isServerProfile = this.profileService.profiles().some(p => p.name === serverName);
      if (isServerProfile) {
        return disconnectedProvider;
      }
      return this.homeProvider;
    }

    // Handle "Service Registries" virtual container nested under "Platform Management" — host profiles at depth 3.
    if (rootName === 'Platform Management' && path.length > 1 && path[1] === 'Service Registries') {
      if (path.length === 2) {
        // At the Service Registries container level itself - return home provider
        // (children come from homeProvider.getContents(['Platform Management', 'Service Registries']))
        return this.homeProvider;
      }
      // Path is like ['Platform Management', 'Service Registries', 'ProfileName', ...]
      // For now, host server profiles don't have navigable children in the file explorer.
      return this.homeProvider;
    }

    // Handle virtual organization folders (Platform Management, Users, Services, System Health)
    // These are top-level categories that don't have navigable children in the file explorer main area
    const virtualOrgFolders = ['Platform Management', 'Users', 'Services', 'System Health'];
    if (virtualOrgFolders.includes(rootName)) {
      // Return homeProvider which handles these paths with special logic
      return this.homeProvider;
    }

    // Check if it's one of the Host Server root nodes (for tree navigation only)
    if (this.treeAdapters.has(rootName)) {
      return this.treeAdapters.get(rootName)!;
    }

    // Legacy/fallback: Check if the root of the path corresponds to a known server profile directly
    // (This supports old paths like ['ServerName', ...] for backwards compatibility)
    const isServerProfile = this.profileService.profiles().some(p => p.name === rootName);

    if (isServerProfile) {
      const remoteProvider = this.remoteProviders().get(rootName);
      if (remoteProvider) {
        // The server is mounted, return its specific provider.
        return remoteProvider;
      } else {
        // The server is known but not mounted, return the disconnected provider.
        return disconnectedProvider;
      }
    }

    // If the path does not point to a server, it must be the local session.
    return this.sessionFs;
  }

  getImageService = (path: string[]): ImageService => {
    let effectiveRootName = path.length > 0 ? path[0] : this.localConfigService.sessionName();

    // Handle nested Gateways/Service Registries under Platform Management - profile name is at path[2].
    if (effectiveRootName === 'Platform Management' && path.length > 2 && (path[1] === 'Gateways' || path[1] === 'Service Registries')) {
      effectiveRootName = path[2];
    }

    // Handle "File Systems" folder - Local Session is at path[1]
    if (effectiveRootName === 'File Systems' && path.length > 1) {
      effectiveRootName = path[1];
    }

    const remote = this.remoteImageServices().get(effectiveRootName);

    // Check if it's a HOST_SERVER
    // In HostServerProvider, we use 'host-<profileId>' for ID, but name is profile.name
    // We can check if the profile type is 'host'
    const profile = this.profileService.profiles().find(p => p.name === effectiveRootName);
    // const isHostServer = profile?.type === 'host'; // BrokerProfile no longer has type

    if (remote) {
      // We need to intercept the getIconUrl call or subclass/wrap the service
      // But cleaner is to let ImageService handle a "force default image name" logic
      // For now, let's just return the service. The caller (FileExplorer) calls getIconUrl.
      // Wait, the caller passes the item. We need to tell ImageService to override.
      // It seems simpler to modify ImageService.getIconUrl to accept an optional 'overrideName'.
      // BUT, getImageService returns the service instance.
      // Let's modify how ImageService is constructed or used?
      // Actually, the ImageService instance is cached in remoteImageServices.
      // Maybe we just attach a property to the service instance?
      // Or better, let's look at how getIconUrl is CALLED.
      // It is called in file-explorer.component.html: imageService.getIconUrl(item)
      // We can't easily change the call site to know about host-server type without logic there.

      // Alternative: The HostServerProvider sets the node type to HOST_SERVER (which we added).
      // If we change the ImageService.getIconUrl to handle HOST_SERVER type specifically?
      // But ImageService takes a generic FileSystemNode.
      return remote;
    }

    // Fallback for local session or if no remote service is found
    const localProfile: BrokerProfile = {
      id: 'local-session',
      name: this.localConfigService.sessionName(),
      brokerUrl: '', // not used for images
      imageUrl: this.localConfigService.defaultImageUrl(),
    };
    return new ImageService(localProfile, this.imageClientService, this.preferencesService, this.healthCheckService, this.localConfigService);
  }

  async buildCombinedFolderTree(): Promise<FileSystemNode> {
    const sessionTree = await this.sessionFs.getFolderTree();
    const allProfiles = this.profileService.profiles();
    const mountedIds = this.mountedProfileIds();
    const remoteRoots: FileSystemNode[] = [];

    // Host Server Nodes
    const hostChildren = await this.registryServerProvider.getChildren('root');
    const hostNodes: FileSystemNode[] = hostChildren.map(node => ({
      name: node.name,
      type: 'folder' as FileType,
      id: node.id,
      metadata: node.metadata,
      children: [],
      childrenLoaded: false,
      isServerRoot: false
    }));

    // Build broker gateway nodes
    for (const profile of allProfiles) {
      const isConnected = mountedIds.includes(profile.id);
      const fsHealthy = this.filesystemHealth().get(profile.name) === true;

      if (isConnected) {
        const provider = this.remoteProviders().get(profile.name);
        if (provider) {
          try {
            const remoteTree = await provider.getFolderTree();
            // If filesystem is unhealthy, suppress mount children in sidebar
            const children = fsHealthy ? remoteTree.children : [];
            remoteRoots.push({
              name: profile.name,
              type: 'folder' as FileType,
              isServerRoot: true,
              profileId: profile.id,
              connected: true,
              healthStatus: this.healthCheckService.getServiceStatus(profile.imageUrl),
              children: children,
              childrenLoaded: fsHealthy ? remoteTree.childrenLoaded : true,
            });
          } catch (e) {
            console.error(`Failed to get folder tree for ${profile.name}`, e);
            // Fallback to a disconnected-style node on error
            remoteRoots.push({
              name: profile.name,
              type: 'folder' as FileType,
              isServerRoot: true,
              profileId: profile.id,
              connected: false,
              healthStatus: this.healthCheckService.getServiceStatus(profile.imageUrl),
              children: [],
            });
          }
        } else {
          // This case indicates an inconsistency (mounted but no provider). Show as disconnected.
          remoteRoots.push({
            name: profile.name,
            type: 'folder' as FileType,
            isServerRoot: true,
            profileId: profile.id,
            connected: false,
            healthStatus: this.healthCheckService.getServiceStatus(profile.imageUrl),
            children: [],
          });
        }
      } else {
        // Profile is not connected
        remoteRoots.push({
          name: profile.name,
          type: 'folder' as FileType,
          isServerRoot: true,
          profileId: profile.id,
          connected: false,
          healthStatus: this.healthCheckService.getServiceStatus(profile.imageUrl),
          children: [],
        });
      }
    }

    // Gateways and Service Registries no longer synthesized as root siblings — they live
    // inside Platform Management (added by registry-server-provider.service.ts).
    // brokerProfileNodes / hostProfileNodes still feed those nested children when accessed.

    // Find the "File Systems" node
    const fileSystemsNode = hostNodes.find(n => n.name === 'File Systems');
    if (fileSystemsNode) {
      // Add connected, mounted gateway profiles as children of File Systems
      const mountedGateways = remoteRoots
        .filter(r => r.connected === true)
        .map(r => ({
          ...r,
          isServerRoot: false,
          metadata: { ...r.metadata, mountId: true },
        }));
      fileSystemsNode.children = mountedGateways;
      fileSystemsNode.childrenLoaded = true;
      fileSystemsNode.isVirtualFolder = true;
    }

    // Prepare the Local Session to be added at root level
    if (sessionTree.children) {
      sessionTree.children = sessionTree.children.filter((c: FileSystemNode) => c.name !== 'Search & Discovery');
    }

    // Filter out specific nodes for manual ordering.
    // Service Registries / service-registries clauses were removed: registryServerProvider
    // no longer emits them at root (they live under Platform Management), so the defensive
    // clauses were dead. in-memory-file-system.service.ts retains its equivalent clause
    // because it parses persisted tree snapshots that may still contain stale entries.
    const otherHostNodes = hostNodes.filter((n: FileSystemNode) =>
      n.name !== 'File Systems' &&
      n.name !== 'Search & Discovery' &&
      n.name !== 'Platform Management'
    );

    const platformNode = hostNodes.find(n => n.name === 'Platform Management');

    // Build the final tree structure alphabetically.
    // Gateways / Service Registries are no longer root siblings — they live inside
    // Platform Management (registry-server-provider.service.ts appends them there).
    const rootChildren = [
      ...otherHostNodes,
      ...(fileSystemsNode ? [fileSystemsNode] : []),
      ...(platformNode ? [platformNode] : []),
      sessionTree,
    ];

    // Sort alphabetically (case-insensitive)
    rootChildren.sort((a, b) => a.name.toLowerCase().localeCompare(b.name.toLowerCase()));

    return {
      name: 'Home',
      type: 'folder' as FileType,
      children: rootChildren,
      childrenLoaded: true,
    };
  }

  localSessionNode = computed(() => {
    const tree = this.folderTree();
    if (!tree?.children) return null;
    return tree.children.find(c => !c.isServerRoot) ?? null;
  });

  async loadFolderTree(): Promise<void> {
    this.folderTree.set(await this.buildCombinedFolderTree());
  }



  onLoadChildren = async (path: string[]) => {
    const provider = this.getProvider(path);
    const rootName = path[0];

    // Platform Management uses homeProvider but still needs lazy loading
    const needsLazyLoading = rootName === 'Platform Management';

    if (provider === this.homeProvider && !needsLazyLoading) {
      // Home provider children are already loaded, no need to lazy load
      return;
    }

    // For sessionFs, the children are also fully loaded
    if (provider === this.sessionFs) {
      return;
    }

    try {
      // Calculate the provider-relative path by removing the routing prefix
      let providerPath: string[];

      if (rootName === 'Platform Management' && path.length > 2 && path[1] === 'Service Registries') {
        // Service Registry profile nodes are leaf nodes — they show an editor when selected,
        // not file-system children in the tree. Return early to prevent falling through to
        // homeProvider.getContents([]) which would incorrectly return the Home root children.
        return;
      }

      if (rootName === 'Platform Management' && path.length > 2 && path[1] === 'Gateways') {
        // Path is ['Platform Management', 'Gateways', 'ServerName', ...]; strip both container
        // levels so the per-profile remoteProvider sees an inner path.
        providerPath = path.slice(3);
      } else if (rootName === 'File Systems' && path.length > 1) {
        // Path is ['File Systems', 'Local Session', ...], provider expects path without both prefixes
        providerPath = path.slice(2);
      } else if (rootName === 'Files') {
        // Path is ['Files', ...], directFs expects path without root name
        providerPath = path.slice(1);
      } else if (rootName === 'Platform Management') {
        // Platform Management uses homeProvider with full path
        providerPath = path;
      } else {
        // Legacy or other paths: just remove the root name
        providerPath = path.slice(1);
      }

      const children = await provider.getContents(providerPath);

      this.folderTree.update(currentTree => {
        if (!currentTree) return null;

        // Recursive function to perform an immutable update on the tree
        const updateNodeRecursive = (node: FileSystemNode, currentPathSegments: string[]): FileSystemNode => {
          // If we've reached the target node...
          if (currentPathSegments.length === 0) {
            return {
              ...node,
              childrenLoaded: true,
              children: children.map(child => ({
                ...child,
                children: child.type === 'folder' ? [] : undefined,
                childrenLoaded: child.type !== 'folder',
              })),
            };
          }

          // If we're still traversing, find the next child in the path
          const nextSegment = currentPathSegments[0];
          const remainingSegments = currentPathSegments.slice(1);

          return {
            ...node,
            children: (node.children ?? []).map(child => {
              if (child.name === nextSegment) {
                // This is the child we need to recurse into
                return updateNodeRecursive(child, remainingSegments);
              }
              // This is not the child we're looking for, return it as is
              return child;
            }),
          };
        };

        // Start the recursive update from the root node.
        return updateNodeRecursive(currentTree, path);
      });

    } catch (e) {
      this.toastService.show(`Error loading contents for ${path.join('/')}: ${(e as Error).message}`, 'error');
    }
  }

  // --- Pane Management ---
  setActivePane(id: number): void {
    this.toolbarAction.set(null);
    this.activePaneId.set(id);
  }

  toggleSplitView(): void {
    this.isSplitView.update(v => !v);
    const currentPaths = this.panePaths();
    if (this.isSplitView() && !currentPaths.find(p => p.id === 2)) {
      // When opening split view, mirror the active pane's path
      const activePath = this.activePanePath();
      this.panePaths.update(paths => [...paths, { id: 2, path: activePath }]);
    } else if (!this.isSplitView()) {
      // When closing, keep only the active pane's path
      const activeId = this.activePaneId();
      this.panePaths.update(paths => paths.filter(p => p.id === activeId));
      if (activeId === 2) {
        this.panePaths.update(paths => [{ id: 1, path: paths[0]?.path ?? [] }]);
        this.activePaneId.set(1);
      }
    }
  }

  onPane1PathChanged(path: string[]): void {
    this.toolbarAction.set(null);
    this.panePaths.update(paths => {
      const newPaths = paths.filter(p => p.id !== 1);
      return [...newPaths, { id: 1, path }];
    });
  }

  onPane2PathChanged(path: string[]): void {
    this.toolbarAction.set(null);
    this.panePaths.update(paths => {
      const newPaths = paths.filter(p => p.id !== 2);
      return [...newPaths, { id: 2, path }];
    });
  }

  goUpActivePane(): void {
    if (!this.canGoUpActivePane()) return;
    const activeId = this.activePaneId();
    this.panePaths.update(paths => {
      const pane = paths.find(p => p.id === activeId);
      if (pane) {
        const newPath = pane.path.slice(0, -1);
        const otherPanes = paths.filter(p => p.id !== activeId);
        return [...otherPanes, { id: activeId, path: newPath }];
      }
      return paths;
    });
  }

  navigatePathActivePane(index: number): void {
    const activeId = this.activePaneId();
    const currentPath = this.activePanePath();
    const newPath = currentPath.slice(0, index + 1);

    this.panePaths.update(paths => {
      const otherPanes = paths.filter(p => p.id !== activeId);
      return [...otherPanes, { id: activeId, path: newPath }];
    });
  }

  onItemSelectedInPane(item: FileSystemNode | null): void {
    this.selectedDetailItem.set(item);
  }

  onPane1StatusChanged(status: PaneStatus): void {
    this.pane1Status.set(status);
  }

  onPane2StatusChanged(status: PaneStatus): void {
    this.pane2Status.set(status);
  }

  onPane1FolderMagnetChanged(isMagnet: boolean): void {
    this.pane1FolderIsMagnet.set(isMagnet);
  }

  onPane2FolderMagnetChanged(isMagnet: boolean): void {
    this.pane2FolderIsMagnet.set(isMagnet);
  }


  // --- UI Toggles ---
  toggleDetailPane(): void {
    this.uiPreferencesService.toggleDetailPane();
  }

  toggleSidebar(): void {
    this.uiPreferencesService.toggleSidebar();
  }

  toggleTree(): void {
    this.uiPreferencesService.toggleTree();
  }

  toggleNotes(): void {
    this.uiPreferencesService.toggleNotes();
  }

  toggleSavedItems(): void {
    this.uiPreferencesService.toggleSavedItems();
  }

  toggleRssFeed(): void {
    this.uiPreferencesService.toggleRssFeed();
  }

  toggleConsole(): void {
    // Sync pixel height BEFORE toggling so the expanded view renders at the correct size.
    // The collapsed view uses h-6, so updating early has no visible effect on it.
    if (this.isConsoleCollapsed()) {
      this.syncConsolePixelHeight();
    }
    this.uiPreferencesService.toggleConsole();
  }

  onDirectoryChanged(): void {
    this.loadFolderTree();
    this.triggerRefresh();
  }

  triggerRefresh(): void {
    this.refreshPanes.update(v => v + 1);
  }

  toggleViewMode(): void {
    this.currentViewMode.update(mode =>
      mode === 'file-explorer' ? 'service-mesh' : 'file-explorer'
    );
  }

  onMeshViewModeChange(mode: 'console' | 'graph'): void {
    this.meshViewMode.set(mode);
    // The object inspector lives in the left sidebar in graph mode, so the
    // right details pane is not needed there — hide it when entering the view.
    if (mode === 'graph') {
      this.uiPreferencesService.setDetailPaneOpen(false);
    }
  }

  onGraphSubViewChange(view: 'canvas' | 'creator'): void {
    this.graphSubView.set(view);
  }

  onRefreshServices(): void {
    this.serviceMeshService.fetchAllData();
  }

  /** Forward toolbar actions (deploy/start/stop/restart/logs/github/jenkins/docker/swarm/k8s) to the service mesh. */
  meshToolbarAction = signal<{ name: string; id: number } | null>(null);

  /** Whether a service is currently selected in the mesh (drives toolbar button enabled state). */
  meshHasSelection = computed(() => !!this.serviceMeshService.selectedService());

  onMeshToolbarAction(action: string): void {
    this.meshToolbarAction.set({ name: action, id: Date.now() });
  }

  onNavCollapseToggle(): void {
    this.uiPreferencesService.toggleNavCollapse();
  }

  onSidebarCollapseToggle(): void {
    this.uiPreferencesService.toggleSidebarCollapse();
  }

  // --- Graph Visualization Control Handlers ---
  onGraphModeChange(mode: 'camera' | 'edit'): void {
    this.vizService.setMode(mode);
  }

  onToggleSimulation(): void {
    const isActive = this.vizService.isSimulationActive();
    this.vizService.toggleSimulation(!isActive);
  }

  async onSaveGraph(): Promise<void> {
    const inGraphMode = this.currentViewMode() === 'service-mesh' && this.meshViewMode() === 'graph';
    if (!inGraphMode) {
      // Fallback for non-graph views: export current scene to a JSON file
      const data = this.vizService.exportScene();
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = this.document.createElement('a');
      a.href = url;
      a.download = 'service-mesh-graph.json';
      a.click();
      URL.revokeObjectURL(url);
      this.toastService.show('Graph exported to file', 'success');
      return;
    }

    const name = window.prompt('Save graph view as:');
    if (!name || !name.trim()) return;

    try {
      const payload = this.vizService.buildAtlasViewPayload(name.trim());
      const currentId = this.atlasService.selectedViewId();
      if (currentId !== null) {
        await this.atlasService.update(currentId, payload);
        this.toastService.show(`Updated graph view "${name.trim()}"`, 'success');
      } else {
        await this.atlasService.create(payload);
        this.toastService.show(`Saved graph view "${name.trim()}"`, 'success');
      }
    } catch (err) {
      console.error('Failed to save graph view', err);
      this.toastService.show('Failed to save graph view', 'error');
    }
  }

  async openLoadViewDialog(): Promise<void> {
    try {
      await this.atlasService.refresh();
    } catch (err) {
      console.error('Failed to refresh graph views', err);
      this.toastService.show('Failed to load saved views', 'error');
      return;
    }
    this.isLoadViewDialogOpen.set(true);
  }

  closeLoadViewDialog(): void {
    this.isLoadViewDialogOpen.set(false);
  }

  onLoadViewSelected(id: number): void {
    this.closeLoadViewDialog();
    this.atlasService.loadRequested.set(id);
  }

  onLoadGraph(): void {
    const inGraphMode = this.currentViewMode() === 'service-mesh' && this.meshViewMode() === 'graph';
    if (!inGraphMode) {
      // Fallback for non-graph views: import scene from a JSON file
      const input = this.document.createElement('input');
      input.type = 'file';
      input.accept = '.json';
      input.onchange = (e: Event) => {
        const file = (e.target as HTMLInputElement).files?.[0];
        if (file) {
          const reader = new FileReader();
          reader.onload = () => {
            try {
              const data = JSON.parse(reader.result as string);
              this.vizService.importScene(data);
              this.toastService.show('Graph loaded from file', 'success');
            } catch (err) {
              this.toastService.show('Failed to load graph file', 'error');
            }
          };
          reader.readAsText(file);
        }
      };
      input.click();
      return;
    }

    this.openLoadViewDialog();
  }

  onBackgroundColorChange(color: string): void {
    this.graphBackgroundColor.set(color);
    this.vizService.setBackgroundColor(color);
  }

  onZoomIn(): void {
    this.vizService.zoomIn();
  }

  onZoomOut(): void {
    this.vizService.zoomOut();
  }

  onRotateLeft(): void {
    this.vizService.rotateCamera(-Math.PI / 8);
  }

  onRotateRight(): void {
    this.vizService.rotateCamera(Math.PI / 8);
  }

  onResetCamera(): void {
    this.vizService.resetCamera();
  }

  onClearGraph(): void {
    this.vizService.clearScene();
    this.toastService.show('Graph cleared', 'info');
  }

  // --- Toolbar Action Handling ---
  // --- Toolbar Action Handling ---
  onToolbarAction(name: string, payload?: any): void {
    if (name === 'delete') {
      if (this.isGatewaySelected()) {
        this.isDeleteGatewayConfirmOpen.set(true);
        return;
      }
      if (this.isServiceRegistrySelected()) {
        this.isDeleteServiceRegistryConfirmOpen.set(true);
        return;
      }
    }

    if (name === 'rename') {
      if (this.isGatewaysNodeSelected() || this.isServiceRegistriesNodeSelected()) {
        // Handled by management component — leaf signals already imply context.
        return;
      }
    }

    this.toolbarAction.set({ name, payload, id: Date.now() });
  }

  onSortChange(criteria: SortCriteria): void {
    if (this.activePaneId() === 1) {
      this.pane1SortCriteria.set(criteria);
    } else {
      this.pane2SortCriteria.set(criteria);
    }
  }

  onDisplayModeChange(mode: 'grid' | 'list'): void {
    if (this.activePaneId() === 1) {
      this.pane1DisplayMode.set(mode);
    } else {
      this.pane2DisplayMode.set(mode);
    }
  }

  onFilterChange(query: string): void {
    if (this.activePaneId() === 1) {
      this.pane1FilterQuery.set(query);
    } else {
      this.pane2FilterQuery.set(query);
    }
  }

  // --- Sidebar Navigation ---
  onSidebarNavigation(path: string[]): void {
    this.toolbarAction.set(null);
    const activeId = this.activePaneId();
    this.panePaths.update(paths => {
      const otherPanes = paths.filter(p => p.id !== activeId);
      return [...otherPanes, { id: activeId, path }];
    });

    // Check if the selected node is "Service Mesh"
    const isServiceMesh = path.length > 0 && path[path.length - 1] === 'Service Mesh';

    if (isServiceMesh) {
      if (this.currentViewMode() !== 'service-mesh') {
        this.currentViewMode.set('service-mesh');
      }
    } else {
      if (this.currentViewMode() !== 'file-explorer') {
        this.currentViewMode.set('file-explorer');
      }
    }
  }


  // --- Local Config Dialog ---
  openLocalConfigDialog(): void {
    this.isLocalConfigDialogOpen.set(true);
  }

  closeLocalConfigDialog(): void {
    this.isLocalConfigDialogOpen.set(false);
  }

  onLocalConfigSaved(config: LocalConfig): void {
    this.localConfigService.updateConfig(config);
    this.closeLocalConfigDialog();
    this.loadFolderTree(); // Reload tree to reflect new session name
    this.toastService.show('Local configuration saved.');
  }

  // --- RSS Feeds Dialog ---
  openRssFeedsDialog(): void {
    this.isRssFeedsDialogOpen.set(true);
  }

  closeRssFeedsDialog(): void {
    this.isRssFeedsDialogOpen.set(false);
  }

  // --- Import / Export ---
  async handleImport(event: { destPath: string[], data: FileSystemNode }): Promise<void> {
    try {
      await this.sessionFs.importTree(event.destPath.slice(1), event.data);
      this.isImportDialogOpen.set(false);
      this.loadFolderTree();
      this.toastService.show('Folder structure imported successfully.');
    } catch (e) {
      this.toastService.show(`Import failed: ${(e as Error).message}`, 'error');
    }
  }

  async handleExport(event: { node: FileSystemNode, path: string[] }): Promise<void> {
    try {
      const json = JSON.stringify(event.node, null, 2);
      const blob = new Blob([json], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `${event.node.name || 'export'}.json`;
      a.click();
      URL.revokeObjectURL(url);
      this.isExportDialogOpen.set(false);
    } catch (e) {
      this.toastService.show(`Export failed: ${(e as Error).message}`, 'error');
    }
  }

  // --- Login and Connection Management ---

  /**
   * Shared connection logic: creates providers, pulse-checks, lists mounts,
   * and populates all mounted-state signals. Used by both onLoginAndMount
   * (after fresh login) and restoreSessions (after page refresh).
   */
  private async connectProfile(profile: BrokerProfile, token: string, user: User): Promise<{ fsHealthy: boolean }> {
    const provider = new SecureFileSystemService(profile, this.fsService, token);
    const imageService = new ImageService(profile, this.imageClientService, this.preferencesService, this.healthCheckService, this.localConfigService);

    // Pulse check the file-system server before listing mounts
    let fsHealthy = false;
    try {
      fsHealthy = await provider.pulseCheck();
    } catch {
      // File-system server unreachable
    }

    this.filesystemHealth.update(m => new Map(m).set(profile.name, fsHealthy));

    if (fsHealthy) {
      try {
        await provider.ensureDefaultDirectory();
      } catch {
        console.warn(`Could not ensure default directory for ${profile.name}`);
      }

      try {
        const mounts = await provider.listMounts();
        provider.setMounts(mounts);
        this.mountedProfileMounts.update(map => {
          const m = new Map(map);
          m.set(profile.name, mounts);
          return m;
        });
      } catch {
        console.warn(`Could not list mounts for ${profile.name}`);
      }
    }

    // Populate mounted state
    this.mountedProfiles.update(p => [...p, profile]);
    this.mountedProfileUsers.update(m => new Map(m).set(profile.id, user));
    this.mountedProfileTokens.update(m => new Map(m).set(profile.id, token));
    this.remoteProviders.update(m => new Map(m).set(profile.name, provider));
    this.remoteImageServices.update(m => new Map(m).set(profile.name, imageService));
    this.notesService.setToken(profile.id, token);

    // Sync mounts to all providers
    this.syncProviderMounts();

    return { fsHealthy };
  }

  /**
   * Attempt to restore sessions from persisted localStorage tokens.
   * Called once on startup after profiles are loaded.
   * For each profile with a stored token, validates it against the backend
   * and re-establishes the connection if still valid.
   * Falls through to interactive auto-login for autoConnect profiles with no valid stored token.
   */
  private async restoreSessions(): Promise<void> {
    const profiles = this.profileService.profiles();

    for (const profile of profiles) {
      try {
        const storedToken = localStorage.getItem(`nexus-token-${profile.id}`);
        if (!storedToken) {
          // No stored token — try auto-connect if enabled
          if (profile.autoConnect) {
            this.onLoginAndMount({ profile, email: 'auto@auto.com', password: 'auto-password' });
          }
          continue;
        }

        // Validate the stored token against the backend
        const isValid = await this.loginService.isLoggedIn(profile, storedToken);

        if (!isValid) {
          // Token expired or invalid — clean up and fall through to auto-connect
          try {
            localStorage.removeItem(`nexus-token-${profile.id}`);
            localStorage.removeItem(`nexus-user-${profile.id}`);
          } catch { /* ignore */ }

          if (profile.autoConnect) {
            this.onLoginAndMount({ profile, email: 'auto@auto.com', password: 'auto-password' });
          }
          continue;
        }

        // Token is valid — restore the user from localStorage or create a minimal one
        let user: User;
        try {
          const storedUser = localStorage.getItem(`nexus-user-${profile.id}`);
          user = storedUser ? JSON.parse(storedUser) : { id: '', profileId: profile.id, alias: profile.name, email: '' };
        } catch {
          user = { id: '', profileId: profile.id, alias: profile.name, email: '' };
        }

        // Re-establish the connection using the shared helper
        await this.connectProfile(profile, storedToken, user);

        console.log(`[AppComponent] Session restored for ${profile.name}`);
        this.toastService.showInfo(`Session restored for ${profile.name}`);

      } catch (e) {
        console.warn(`[AppComponent] Session restore failed for ${profile.name}:`, e);
        // If restore fails and autoConnect is set, fall through to interactive login
        if (profile.autoConnect) {
          try {
            this.onLoginAndMount({ profile, email: 'auto@auto.com', password: 'auto-password' });
          } catch { /* ignore fallback failure */ }
        }
      }
    }
  }

  async onLoginAndMount({ profile, email, password }: { profile: BrokerProfile, email: string, password: string }): Promise<void> {
    try {
      const { user, token } = await this.loginService.login(profile, email, password);

      // Use the shared connection helper
      const { fsHealthy } = await this.connectProfile(profile, token, user);

      if (!fsHealthy) {
        this.toastService.show(`File-system server is unreachable for ${profile.name}.`, 'warning');
      }

      // Persist token and user to localStorage so session survives page refresh
      try {
        localStorage.setItem(`nexus-token-${profile.id}`, token);
        localStorage.setItem(`nexus-user-${profile.id}`, JSON.stringify(user));
      } catch { /* localStorage may be full or unavailable */ }

      this.toastService.show(`Successfully connected to ${profile.name}.`);

    } catch (e) {
      const profileName = profile ? profile.name : 'the server';
      this.toastService.show(`Login to ${profileName} failed: ${(e as Error).message}`, 'error');
    }
  }

  onUnmountProfile(profile: BrokerProfile): void {
    this.mountedProfiles.update(p => p.filter(item => item.id !== profile.id));

    this.filesystemHealth.update(m => {
      const newMap = new Map(m);
      newMap.delete(profile.name);
      return newMap;
    });

    this.mountedProfileUsers.update(m => {
      const newMap = new Map(m);
      newMap.delete(profile.id);
      return newMap;
    });

    this.mountedProfileTokens.update(m => {
      const newMap = new Map(m);
      newMap.delete(profile.id);
      return newMap;
    });

    this.remoteProviders.update(m => {
      const newMap = new Map(m);
      newMap.delete(profile.name);
      return newMap;
    });

    this.remoteImageServices.update(m => {
      const newMap = new Map(m);
      newMap.delete(profile.name);
      return newMap;
    });

    this.notesService.removeToken(profile.id);

    this.mountedProfileMounts.update(m => {
      const newMap = new Map(m);
      newMap.delete(profile.name);
      return newMap;
    });

    // If any pane was inside the unmounted profile, navigate it to root
    this.panePaths.update(paths => {
      return paths.map(p => {
        if (p.path[0] === profile.name) {
          return { ...p, path: [] };
        }
        return p;
      });
    });

    // Clean up persisted session data
    try {
      localStorage.removeItem(`nexus-token-${profile.id}`);
      localStorage.removeItem(`nexus-user-${profile.id}`);
    } catch { /* localStorage may be unavailable */ }

    this.loadFolderTree();
    this.toastService.show(`Disconnected from ${profile.name}.`);
  }

  /** Sync mounts from mountedProfileMounts to each connected RemoteFileSystemService provider. */
  private syncProviderMounts(): void {
    this.mountedProfileMounts().forEach((mounts, profileName) => {
      const provider = this.remoteProviders().get(profileName);
      if (provider instanceof SecureFileSystemService) {
        provider.setMounts(mounts);
      }
    });
  }

  onConnectToServer(profileId: string): void {
    const profile = this.profileService.profiles().find(p => p.id === profileId);
    if (profile) {
      this.profileForLogin.set(profile);
    }
  }

  /**
   * Called from the gateway editor's Disconnect button.
   * Calls the backend logout to invalidate the session token,
   * then cleans up local state.
   */
  async onDisconnectGateway(profileId: string): Promise<void> {
    const profile = this.mountedProfiles().find(p => p.id === profileId);
    if (!profile) return;

    const token = this.mountedProfileTokens().get(profileId);
    if (token) {
      try {
        await this.loginService.logout(profile, token);
      } catch (e) {
        console.warn(`Backend logout for ${profile.name} failed (proceeding with local cleanup):`, e);
      }
    }
    this.onUnmountProfile(profile);
  }

  onDisconnectFromServer(profileId: string): void {
    const profile = this.mountedProfiles().find(p => p.id === profileId);
    if (profile) {
      this.onUnmountProfile(profile);
    }
  }

  onEditServerProfile(profileId: string): void {
    // Try broker profiles first
    const brokerProfile = this.profileService.profiles().find(p => p.id === profileId);
    if (brokerProfile) {
      const activeId = this.activePaneId();
      this.panePaths.update(paths => {
        const otherPanes = paths.filter(p => p.id !== activeId);
        return [...otherPanes, { id: activeId, path: ['Platform Management', 'Gateways', brokerProfile.name] }];
      });
      return;
    }

    // Try host profiles second
    const hostProfile = this.hostProfileService.profiles().find(p => p.id === profileId);
    if (hostProfile) {
      const activeId = this.activePaneId();
      this.panePaths.update(paths => {
        const otherPanes = paths.filter(p => p.id !== activeId);
        return [...otherPanes, { id: activeId, path: ['Platform Management', 'Service Registries', hostProfile.name] }];
      });
    }
  }

  onServerProfileRenamed(event: { oldName: string, newName: string, profile: BrokerProfile }): void {
    const { oldName, newName, profile } = event;

    // 1. Update remoteProviders and remoteImageServices keys if the profile is mounted
    if (this.remoteProviders().has(oldName)) {
      const provider = this.remoteProviders().get(oldName)!;
      this.remoteProviders.update(m => {
        const newMap = new Map(m);
        newMap.delete(oldName);
        newMap.set(newName, provider);
        return newMap;
      });

      const imageService = this.remoteImageServices().get(oldName)!;
      this.remoteImageServices.update(m => {
        const newMap = new Map(m);
        newMap.delete(oldName);
        newMap.set(newName, imageService);
        return newMap;
      });
    }

    // 2. Update paths in any open panes
    this.panePaths.update(paths => {
      return paths.map(panePath => {
        if (panePath.path[0] === oldName) {
          return { ...panePath, path: [newName, ...panePath.path.slice(1)] };
        }
        return panePath;
      });
    });

    // 3. Reload the folder tree to reflect the name change
    this.loadFolderTree();
  }

  onLoginSubmittedFromSidebar({ email, password }: { email: string, password: string }): void {
    const profile = this.profileForLogin();
    if (profile) {
      this.onLoginAndMount({ profile, email, password });
      this.profileForLogin.set(null);
    } else {
      console.error("Login submitted but no profile was selected for login.");
      this.toastService.show('Login failed: No profile selected.', 'error');
    }
  }

  // --- Drag & Drop for Bookmarks ---
  onBookmarkDroppedOnPane(event: { bookmark: NewBookmark, dropOn: FileSystemNode }): void {
    const destPath = [...this.activePanePath(), event.dropOn.name];
    this.bookmarkService.addBookmark(destPath, event.bookmark);
    this.toastService.show(`Bookmark saved to ${event.dropOn.name}.`);
  }

  onBookmarkDroppedOnSidebar(event: { bookmark: NewBookmark, destPath: string[] }): void {
    this.bookmarkService.addBookmark(event.destPath, event.bookmark);
    this.toastService.show(`Bookmark saved.`);
  }

  // --- File/Folder Item Manipulation (from panes or sidebar) ---
  onPaneItemRenamed(event: { oldName: string, newName: string }, path: string[]): void {
    const oldFullPath = [...path, event.oldName];
    const newFullPath = [...path, event.newName];
    this.folderPropertiesService.handleRename(oldFullPath, newFullPath);
    this.loadFolderTree();
  }

  onSidebarRenameItem(event: { path: string[], newName: string }): void {
    const oldName = event.path[event.path.length - 1];
    const parentPath = event.path.slice(0, -1);
    const provider = this.getProvider(parentPath);
    const providerPath = parentPath.length > 0 ? parentPath.slice(1) : [];

    provider.rename(providerPath, oldName, event.newName)
      .then(() => {
        this.folderPropertiesService.handleRename(event.path, [...parentPath, event.newName]);
        this.loadFolderTree();
        this.toastService.show('Item renamed.');
      })
      .catch(e => this.toastService.show(`Rename failed: ${(e as Error).message}`, 'error'));
  }

  onItemsDeleted(paths: string[][]): void {
    for (const path of paths) {
      this.folderPropertiesService.handleDelete(path);
    }
    this.loadFolderTree();
    this.triggerRefresh();
  }

  onSidebarDeleteItem(path: string[]): void {
    const name = path[path.length - 1];
    const parentPath = path.slice(0, -1);
    const provider = this.getProvider(path);
    const providerPath = parentPath.length > 0 ? parentPath.slice(1) : [];

    // We need to know if it's a file or folder
    provider.getContents(providerPath).then(contents => {
      const item = contents.find(c => c.name === name);
      if (!item) {
        throw new Error("Item not found for deletion.");
      }
      const promise = item.type === 'folder'
        ? provider.removeDirectory(providerPath, name)
        : provider.deleteFile(providerPath, name);

      promise.then(() => {
        this.folderPropertiesService.handleDelete(path);
        this.loadFolderTree();
        this.toastService.show('Item deleted.');
      }).catch(e => this.toastService.show(`Delete failed: ${(e as Error).message}`, 'error'));
    }).catch(e => this.toastService.show(`Delete failed: ${(e as Error).message}`, 'error'));
  }

  onItemsMoved(event: { sourcePath: string[]; destPath: string[]; items: ItemReference[] }): void {
    for (const item of event.items) {
      if (item.type === 'folder') {
        const oldFullPath = [...event.sourcePath, item.name];
        const newFullPath = [...event.destPath, item.name];
        this.folderPropertiesService.handleRename(oldFullPath, newFullPath);
      }
    }
    this.loadFolderTree();
    this.triggerRefresh();
  }

  onSidebarItemsMoved(event: { destPath: string[]; payload: DragDropPayload }): void {
    if (event.payload.type !== 'filesystem') return;

    const { sourceProvider, sourcePath, items } = event.payload.payload;
    const destProvider = this.getProvider(event.destPath);

    // Moving between providers is not supported yet.
    if (sourceProvider !== destProvider) {
      this.toastService.show('Moving items between different file systems is not supported yet.', 'error');
      return;
    }

    const sourceProviderPath = sourcePath.length > 0 ? sourcePath.slice(1) : [];
    const destProviderPath = event.destPath.length > 0 ? event.destPath.slice(1) : [];

    sourceProvider.move(sourceProviderPath, destProviderPath, items.map(i => ({ name: i.name, type: i.type })))
      .then(() => {
        // Update properties and notes for each moved folder
        for (const item of items) {
          if (item.type === 'folder') {
            const oldFullPath = [...sourcePath, item.name];
            const newFullPath = [...event.destPath, item.name];
            this.folderPropertiesService.handleRename(oldFullPath, newFullPath);
          }
        }
        this.loadFolderTree();
        this.triggerRefresh();
        this.toastService.show('Items moved successfully.');
      })
      .catch(e => this.toastService.show(`Move failed: ${(e as Error).message}`, 'error'));
  }

  onSidebarNewFolder(event: { path: string[]; name: string }): void {
    const provider = this.getProvider(event.path);
    const providerPath = event.path.length > 0 ? event.path.slice(1) : [];
    provider.createDirectory(providerPath, event.name)
      .then(() => {
        this.loadFolderTree();
        this.triggerRefresh();
        this.toastService.show('Folder created.');
      })
      .catch(e => this.toastService.show(`Failed to create folder: ${(e as Error).message}`, 'error'));
  }

  onSidebarNewFile(event: { path: string[]; name: string }): void {
    const provider = this.getProvider(event.path);
    const providerPath = event.path.length > 0 ? event.path.slice(1) : [];
    provider.createFile(providerPath, event.name)
      .then(() => {
        this.loadFolderTree();
        this.triggerRefresh();
        this.toastService.show('File created.');
      })
      .catch(e => this.toastService.show(`Failed to create file: ${(e as Error).message}`, 'error'));
  }

  // --- Keyboard Shortcuts ---
  onKeyDown(event: KeyboardEvent): void {
    if (event.ctrlKey && event.key === '`') {
      event.preventDefault();
      this.toggleConsole();
    }
  }

  // --- Global Click for closing menus ---
  onDocumentClick(event: MouseEvent): void {
    const target = event.target as HTMLElement;

    // Close hamburger menu if clicking outside
    if (this.isHamburgerMenuOpen() && !target.closest('[data-hamburger-menu]')) {
      this.isHamburgerMenuOpen.set(false);
    }

    // If the click is on the button that opens the theme menu, do nothing.
    // This prevents the menu from closing immediately after opening.
    if (target.closest('[data-theme-menu-trigger]')) {
      return;
    }

    // Close theme dropdown if clicking outside of it
    if (this.isThemeDropdownOpen() && !target.closest('.theme-menu')) {
      this.isThemeDropdownOpen.set(false);
    }
  }

  // --- Resizing logic ---
  startPaneResize(event: MouseEvent): void {
    if (!this.isSplitView()) return;
    this.isResizingPane = true;
    event.preventDefault();
    const container = this.paneContainerEl.nativeElement;
    const startX = event.clientX;
    const startWidth = container.children[0].getBoundingClientRect().width;
    const totalWidth = container.getBoundingClientRect().width;

    this.unlistenPaneResizeMove = this.renderer.listen('document', 'mousemove', (e: MouseEvent) => {
      const dx = e.clientX - startX;
      let newWidth = startWidth + dx;

      const minWidth = 150; // min width in pixels
      if (newWidth < minWidth) newWidth = minWidth;
      if (newWidth > totalWidth - minWidth) newWidth = totalWidth - minWidth;

      this.pane1Width.set((newWidth / totalWidth) * 100);
    });

    this.unlistenPaneResizeUp = this.renderer.listen('document', 'mouseup', () => this.stopPaneResize());
  }

  private stopPaneResize(): void {
    if (!this.isResizingPane) return;
    this.isResizingPane = false;
    this.unlistenPaneResizeMove?.();
    this.unlistenPaneResizeUp?.();
    this.uiPreferencesService.setSplitViewPaneWidth(this.pane1Width());
  }

  startStreamResize(event: MouseEvent): void {
    this.isResizingStream = true;
    event.preventDefault();
    const container = this.mainContentWrapperEl?.nativeElement;
    if (!container) { console.warn('[AppComponent] mainContentWrapperEl not available'); return; }
    const startY = event.clientY;
    const containerRect = container.getBoundingClientRect();
    const streamEl = container.querySelector('app-idea-stream');
    const initialStreamHeight = streamEl ? streamEl.getBoundingClientRect().height : 0;

    this.ngZone.runOutsideAngular(() => {
      const onMove = (e: MouseEvent) => {
        const dy = startY - e.clientY;
        let newStreamHeight = initialStreamHeight + dy;

        const minHeight = 100;
        const consoleHeight = this.isConsoleCollapsed() ? 28 : this.consolePaneHeight(); // already in pixels
        const maxHeight = containerRect.height - 100 - consoleHeight;

        if (newStreamHeight < minHeight) newStreamHeight = minHeight;
        if (newStreamHeight > maxHeight) newStreamHeight = maxHeight;

        this.streamPaneHeight.set((newStreamHeight / containerRect.height) * 100);
      };

      const onUp = () => {
        document.removeEventListener('mousemove', onMove);
        document.removeEventListener('mouseup', onUp);
        this.ngZone.run(() => this.stopStreamResize());
      };

      document.addEventListener('mousemove', onMove);
      document.addEventListener('mouseup', onUp);
    });
  }

  private stopStreamResize(): void {
    if (!this.isResizingStream) return;
    this.isResizingStream = false;
    this.unlistenStreamResizeMove?.();
    this.unlistenStreamResizeUp?.();
    this.uiPreferencesService.setExplorerStreamHeight(this.streamPaneHeight());
  }

  startConsolePaneResize(event: MouseEvent): void {
    this.isResizingConsole = true;
    event.preventDefault();
    const container = this.viewContainerEl?.nativeElement;
    if (!container) return;
    const containerRect = container.getBoundingClientRect();

    this.ngZone.runOutsideAngular(() => {
      const onMove = (e: MouseEvent) => {
        // Track mouse directly — console fills from resizer to bottom bar.
        // 48px = <app-bottom-bar> (h-12) | 6px = resizer itself (h-1.5)
        const chromeOffset = 54;
        let newConsoleHeight = containerRect.bottom - e.clientY - chromeOffset;

        const minHeight = 100;
        // Console is now a sibling of <main> — leave at least 200px for the main content area
        const maxHeight = containerRect.height - 200;

        if (newConsoleHeight < minHeight) newConsoleHeight = minHeight;
        if (newConsoleHeight > maxHeight) newConsoleHeight = maxHeight;

        this.consolePaneHeight.set(newConsoleHeight);
      };

      const onUp = () => {
        document.removeEventListener('mousemove', onMove);
        document.removeEventListener('mouseup', onUp);
        this.ngZone.run(() => this.stopConsoleResize());
      };

      document.addEventListener('mousemove', onMove);
      document.addEventListener('mouseup', onUp);
    });
  }

  private stopConsoleResize(): void {
    if (!this.isResizingConsole) return;
    this.isResizingConsole = false;
    this.unlistenConsoleResizeMove?.();
    this.unlistenConsoleResizeUp?.();
    // Persist as percentage of container height so it scales across viewport sizes
    const container = this.viewContainerEl?.nativeElement;
    if (container) {
      const pct = (this.consolePaneHeight() / container.getBoundingClientRect().height) * 100;
      this.uiPreferencesService.setExplorerConsoleHeight(pct);
    }
  }

  // --- Theme Menu ---
  openThemeMenu(target: HTMLElement): void {
    const rect = target.getBoundingClientRect();
    const viewportWidth = window.innerWidth;
    this.themeMenuPosition.set({
      top: `${rect.bottom + 8}px`,
      right: `${viewportWidth - rect.right}px`
    });
    this.isThemeDropdownOpen.set(true);
  }

  setTheme(theme: Theme): void {
    this.uiPreferencesService.setTheme(theme);
    this.isThemeDropdownOpen.set(false);
    // Publish theme change to the event bus for other apps
    this.eventBus.publishThemeChange(theme);
  }

  // --- Preferences Dialog ---
  openPreferencesDialog(): void {
    this.isPreferencesDialogOpen.set(true);
  }

  /** Open the AI configuration dialog (tackle-ui iframe popup) */
  openAiConfigDialog(): void {
    this.showAiConfigPopup.set(true);
  }

  closeAiConfigPopup(): void {
    this.showAiConfigPopup.set(false);
  }

  closePreferencesDialog(): void {
    this.isPreferencesDialogOpen.set(false);
  }

  onPreferencesSaved(prefs: Partial<UiPreferences>): void {
    this.uiPreferencesService.saveAllPreferences(prefs);
    this.closePreferencesDialog();
    this.toastService.show('Preferences saved.');
  }

  // --- Complex Search ---
  openComplexSearchDialog(): void {
    this.isComplexSearchDialogOpen.set(true);
  }

  closeComplexSearchDialog(): void {
    this.isComplexSearchDialogOpen.set(false);
  }

  onComplexSearch(params: ComplexSearchParams): void {
    this.toastService.show(`Complex search initiated for: "${params.query}"`);
    console.log("Complex Search Params:", params);
    // In a real app, you would now use these params to call a search service.
  }

  // --- Gemini Search ---
  openGeminiSearchDialog(): void {
    this.isGeminiSearchDialogOpen.set(true);
  }

  closeGeminiSearchDialog(): void {
    this.isGeminiSearchDialogOpen.set(false);
  }

  onGeminiSearch(params: GeminiSearchParams): void {
    this.toastService.show(`Gemini search initiated for: "${params.query}"`);
    console.log("Gemini Search Params:", params);
    // Here we could call the gemini service and update the stream results.
    // For now, just logging as requested.
  }

  onStreamPaneCollapseToggled(): void {
    this.uiPreferencesService.toggleStreamPaneCollapse();
  }

  onStreamActiveSearchToggled(): void {
    this.uiPreferencesService.toggleStreamActiveSearch();
  }

  toggleStream(): void {
    this.uiPreferencesService.toggleStream();
  }

  onComplexSearchRequested(): void {
    this.openComplexSearchDialog();
  }

  onGeminiSearchRequested(): void {
    this.openGeminiSearchDialog();
  }

  /** Navigate to the magnet folder that generated a stream result. */
  onNavigateToMagnet(magnetPath: string[]): void {
    const rootName = this.activeRootName();
    const fullPath = [rootName, ...magnetPath];
    const activeId = this.activePaneId();
    this.panePaths.update(paths => {
      const otherPanes = paths.filter(p => p.id !== activeId);
      return [...otherPanes, { id: activeId, path: fullPath }];
    });
  }

  // Auto-uncollapse the stream pane when navigating to a visible context
  // Use untracked to avoid re-triggering when user manually toggles collapse
  private autoUncollapseStream = effect(() => {
    if (this.shouldShowStreamPane() && untracked(() => this.isStreamPaneCollapsed())) {
      this.uiPreferencesService.setStreamPaneCollapsed(false);
    }
  });
}

