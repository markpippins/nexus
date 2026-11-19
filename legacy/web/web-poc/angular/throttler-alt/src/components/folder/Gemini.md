# `FolderComponent` Documentation (`/src/components/folder/Gemini.md`)

This component is responsible for rendering a single folder item within the main content area of the `FileExplorerComponent`.

## Core Responsibilities

1.  **Visual Representation:** It displays the folder icon and name. It has distinct visual states for being selected, hovered over, or being dragged over.
2.  **Drag-and-Drop Target:** It acts as a drop zone for multiple types of data:
    - **Native Files:** When a user drags files from their operating system and drops them onto the folder icon, this component captures the event.
    - **Internal Items:** It accepts other files/folders being moved from within the application.
    - **Bookmarks:** It accepts bookmarks being dragged from the Idea Stream or Details Pane.
3.  **Event Delegation:** It contains no business logic itself. Instead, it emits events for user interactions (like context menu clicks or drops) to its parent, the `FileExplorerComponent`, which then handles the actual logic.

## API and Data Flow

### Inputs (`input()`)

-   `item: FileSystemNode`: The data object for the folder to be rendered.
-   `iconUrl: string | null`: The URL for a custom icon, if available.
-   `isSelected: boolean`: Determines if the folder should be rendered with a "selected" style.
-   `hasFailedToLoadImage: boolean`: A flag indicating a previous attempt to load `iconUrl` failed.

### Outputs (`output()`)

-   `itemContextMenu: { event: MouseEvent; item: FileSystemNode }`: Emitted on right-click.
-   `itemDrop: { files: FileList; item: FileSystemNode }`: Emitted when native OS files are dropped onto the folder.
-   `internalItemDrop: { dropOn: FileSystemNode }`: Emitted when other file system items from within the app are dropped.
-   `bookmarkDropped: { bookmark: NewBookmark, dropOn: FileSystemNode }`: Emitted when a bookmark is dropped.
-   `imageError: string`: Emitted when the browser fails to load the image from the provided `iconUrl`.

### Internal State (Signals)

-   `isDragOver: boolean`: A local signal that becomes `true` when a valid item is being dragged over the component, used to apply a distinct visual style.