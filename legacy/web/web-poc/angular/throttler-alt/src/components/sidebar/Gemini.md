# `SidebarComponent` Documentation (`/src/components/sidebar/Gemini.md`)

The `SidebarComponent` is a stateful container component that forms the collapsible and resizable left-hand panel of the application.

## Core Responsibilities

1.  **Layout and Structure:** It provides the main structure for the sidebar, which contains a series of vertically stacked, resizable, and collapsible panes for different functions: the folder tree (`TreeViewComponent`), a chat interface (`ChatComponent`), and a note-taking area (`NotesComponent`). The visibility of each pane can be toggled.
2.  **Collapsibility & Resizability:** It manages its own collapsed/expanded state and allows the user to resize its width via a drag handle.
3.  **Navigation Hub:** It hosts the `TreeViewComponent` and acts as an intermediary, receiving path change events from the tree view and emitting them up to the `AppComponent`.
4.  **Tree View Actions:** It orchestrates actions originating from the tree view's context menu (e.g., rename, delete, new folder/file) by displaying dialogs and emitting the results to the `AppComponent`.
5.  **Drag and Drop Target:** It handles drop events from the `TreeViewComponent`, emitting events to the `AppComponent` when file system items are moved or bookmarks are dropped onto a folder in the tree.

## API and Data Flow

### Inputs (`input()`)

-   `folderTree: FileSystemNode | null`: The hierarchical data structure of all folders, which is passed down to the `TreeViewComponent`.
-   `currentPath: string[]`: The currently active path in the file explorer, passed to the `TreeViewComponent` to highlight the corresponding node.
-   `getProvider`: A function to retrieve the correct `FileSystemProvider` for a given path, used for drag-and-drop operations.

### Outputs (`output()`)

-   `pathChange: string[]`: Emitted when a user clicks on a node in the tree.
-   `loadChildren: string[]`: Emitted when a tree node needs its children to be lazy-loaded.
-   `itemsMoved: { destPath: string[]; payload: DragDropPayload }`: Emitted when file system items are dropped onto a folder in the tree.
-   `bookmarkDropped: { bookmark: NewBookmark, destPath: string[] }`: Emitted when a bookmark is dropped onto a folder in the tree.
-   `renameItemInTree`, `deleteItemInTree`, `createFolderInTree`, `createFileInTree`: Events emitted after the user completes an action in a context-menu-triggered dialog.

## Internal State (Signals)

-   `width: number`: Stores the current width of the sidebar in pixels.
-   `isResizing: boolean`: A flag that is `true` while the user is actively dragging the resize handle.
-   `contextMenu`: Holds the state for the right-click context menu.
-   Various dialog signals (`isInputDialogOpen`, `isConfirmDialogOpen`) for handling tree actions.