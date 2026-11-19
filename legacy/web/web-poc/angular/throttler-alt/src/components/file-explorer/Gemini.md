# `FileExplorerComponent` Documentation (`/src/components/file-explorer/Gemini.md`)

The `FileExplorerComponent` is the most complex and central UI component in the application. It provides the main view for navigating and interacting with a file system.

## Core Responsibilities

1.  **Displaying Directory Contents:** It fetches and displays the list of files and folders for the `path` it receives as an input, using the provided `fileSystemProvider`. It can display items in a grid or a list.
2.  **User Interaction & Selection:** It handles all user input for selecting items:
    - Single click (with a delay to distinguish from double-click for rename)
    - Ctrl/Meta + click for multi-selection
    - Shift + click for range selection
    - Click-and-drag "lasso" selection in the grid view.
    - It maintains the set of selected items in the `selectedItems` signal.
3.  **Navigation & File Operations:** It orchestrates all file manipulation operations by calling methods on its `fileSystemProvider`, including creating, renaming, and deleting items.
4.  **Clipboard Integration:** It uses the shared `ClipboardService` to perform cut, copy, and paste operations.
5.  **Drag and Drop:** It supports dragging files from the user's OS for upload, and internal drag-and-drop for moving items between folders.

## API and Data Flow

### Inputs (`input()`)

-   `id: number`: A unique identifier (1 or 2), crucial for multi-pane management.
-   `path: string[]`: The full path that this explorer should display. The component reactively loads content whenever this input changes.
-   `isActive: boolean`: Indicates if this pane is the currently active one.
-   `fileSystemProvider: FileSystemProvider`: The abstraction the component uses for all file system operations.
-   `toolbarAction`: A signal that triggers an action (e.g., 'newFolder', 'paste') from the main toolbar.

### Outputs (`output()`)

-   `activated: number`: Emits its `id` when clicked, telling the parent to set it as the active pane.
-   `pathChanged: string[]`: Emits the new path when the user navigates to a new folder.
-   `itemSelected: FileSystemNode | null`: Emits the selected item when the selection contains exactly one item.
-   `statusChanged`: Emits statistics about the current view (item counts, selection count).
-   `bookmarkDropped`: Emitted when a bookmark is dragged from an external source and dropped onto a folder.

## Internal State (Signals)

-   `state`: Tracks the loading status and the list of `items` in the current directory.
-   `selectedItems`: A `Set<string>` containing the names of the selected items.
-   `isLassoing` / `lassoRect`: Manage the state of the selection rectangle.