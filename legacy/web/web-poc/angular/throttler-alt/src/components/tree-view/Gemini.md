# `TreeViewComponent` Documentation (`/src/components/tree-view/Gemini.md`)

This component serves as the root container for the folder navigation tree displayed in the sidebar.

## Core Responsibilities

1.  **Initiating the Tree:** It receives the `rootNode` of the file system structure.
2.  **Rendering the First Level:** Its template renders the root `TreeNodeComponent`. From there, the `TreeNodeComponent`'s recursive template takes over to build the rest of the tree.
3.  **Event Propagation:** It's a simple pass-through component. It listens for all significant events from the `TreeNodeComponent` hierarchy (`pathChange`, `loadChildren`, `itemsDropped`, `bookmarkDropped`, `contextMenuRequest`) and propagates them up to its parent, the `SidebarComponent`, for handling.

## API and Data Flow

### Inputs (`input()`)

-   `rootNode: FileSystemNode`: The complete, hierarchical folder structure.
-   `currentPath: string[]`: The path of the folder currently being viewed in the `FileExplorerComponent`.
-   `expansionCommand`: A command to globally expand or collapse the tree.
-   `imageService`: The service used to fetch custom icons for tree nodes.
-   `getProvider`: A function to retrieve the correct file system provider for a given path.

### Outputs (`output()`)

-   `pathChange: string[]`: Emits the full path of a folder when a user clicks on it within the tree.
-   `loadChildren: string[]`: Emits when a node needs its children lazy-loaded.
-   `itemsDropped`: Emits when a file system item is dropped on a tree node.
-   `bookmarkDropped`: Emits when a bookmark is dropped on a tree node.
-   `contextMenuRequest`: Emits when a user right-clicks a tree node.