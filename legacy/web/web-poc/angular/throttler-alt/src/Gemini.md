# `src` Directory Documentation (`/src/Gemini.md`)

This directory contains the root component of the Angular application.

## `AppComponent` - The Application Orchestrator

The `AppComponent` is the top-level component that acts as the central controller and state manager for the entire user interface. It is responsible for creating a unified experience that combines multiple different file system backends, including managing user sessions for remote connections.

### Core Responsibilities

1.  **State Management:** It holds and manages the primary application state using **Angular Signals**. Key state signals include:
    -   `isSplitView`: A boolean that controls whether one or two file explorer panes are visible.
    -   `isDetailPaneOpen`: Controls the visibility of the right-hand details/bookmarks pane.
    -   `activePaneId`: Determines which of the two panes is currently active.
    -   `folderTree`: Holds the combined, virtual directory structure.
    -   `panePaths`: An array that holds the current path for each active file explorer pane.
    -   `mountedProfiles` & `mountedProfileUsers`: Signals that track active server connections and the authenticated users for each session.

2.  **Multi-Root & Per-User File System Orchestration:** This is the key architectural feature of the component.
    -   On startup, it constructs a virtual "Home" folder that contains the root from the `SessionService` and roots from any mounted remote servers.
    -   When a user logs into a remote server, it creates a `RemoteFileSystemService` instance *specifically for that user*. This service uses the user's username as an `alias` for all backend file operations, ensuring each user has a sandboxed view of the remote file system.
    -   This virtual tree, including user-specific remote roots, is passed to the sidebar, allowing seamless navigation across different data sources.

3.  **Per-Pane Dynamic File System Provider:**
    -   The component manages a map of `RemoteFileSystemService` instances, keyed by the server profile's name.
    -   It uses `computed` signals (`pane1Provider`, `pane2Provider`) to dynamically assign the correct file system service to each file explorer pane based on its current path.
    -   This design makes the `FileExplorerComponent` completely agnostic about its data source; it simply interacts with the `FileSystemProvider` interface it is given.

4.  **Component Coordination:**
    -   It orchestrates interactions between the `SidebarComponent`, `ToolbarComponent`, `FileExplorerComponent`(s), and the `DetailPaneComponent`.
    -   It handles the `(loginAndMount)` event from the `ServerProfilesDialogComponent`, initiating the authentication flow and creating a new user-specific file system provider upon success.
    -   It manages bookmarking events (`onSaveBookmark`, `onBookmarkDropped...`) by interacting with the `BookmarkService` and passing the relevant data.

5.  **Layout & Pane Management:** It manages the overall application layout, including the visibility and size of major UI sections like the main sidebar, the details pane, the **Idea Stream**, and the **Console**. The Idea Stream and Console are rendered as resizable panes at the bottom of the main content area, providing contextual information and a command-line interface, respectively.

### API and Interactions

-   **Inputs:** None (as it's the root component).
-   **Outputs:** None.
-   **Methods:**
    -   `loadFolderTree()`: An async method that builds the combined virtual folder tree.
    -   `onLoginAndMount()`: Handles the login process and, on success, mounts a user-specific remote file system.
    -   `onUnmountProfile()`: Destroys a `RemoteFileSystemService` instance and clears the associated user session.
    -   `toggleSplitView()`: Manages the split-view layout.
    -   `toggleDetailPane()`: Manages the visibility of the details pane.
    -   `onPane1PathChanged()` / `onPane2PathChanged()`: Keeps the parent component's record of each pane's path in sync.