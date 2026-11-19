# `ToolbarComponent` Documentation (`/src/components/toolbar/Gemini.md`)

The `ToolbarComponent` is a presentational component that renders the row of action buttons and view controls displayed at the top of the main content area.

## Core Responsibilities

1.  **Displaying Controls:** It renders all the buttons, icons, and dropdowns for file operations (New, Cut, Copy, Paste, Rename, Delete), view manipulation (Grid/List, Split View, Details Pane), and sorting.
2.  **Displaying Filter:** It includes the input field for filtering the file list by name.
3.  **Delegating Actions:** It contains no business logic. When a user interacts with a control, it simply emits a corresponding event to notify its parent component.
4.  **Reflecting State:** It dynamically enables or disables its buttons based on boolean inputs provided by the parent. It also visually reflects the current state for toggle buttons like display mode, split view, and the details pane.

## API and Data Flow

This component is a clear example of unidirectional data flow. Data flows **in** via inputs, and events flow **out** via outputs.

### Inputs (`input()`)

-   `canCut`, `canCopy`, `canPaste`, `canRename`, `canDelete`, etc.: Booleans to control the enabled/disabled state of action buttons.
-   `currentSort: SortCriteria`: Used to display a checkmark next to the currently active sorting option.
-   `displayMode: 'grid' | 'list'`: To highlight the active view mode button.
-   `filterQuery: string`: To bind to the value of the filter input field.
-   `isSplitViewActive: boolean`: To highlight the split view button.
-   `isDetailPaneActive: boolean`: To highlight the details pane button.

### Outputs (`output()`)

-   `newFolderClick: void`, `copyClick: void`, etc.: Events for all file operations.
-   `sortChange: SortCriteria`: Emits the new sorting criteria when an option is selected.
-   `displayModeChange: 'grid' | 'list'`: Emits when the user clicks a view mode button.
-   `filterChange: string`: Emits the new query as the user types in the filter input.
-   `splitViewClick: void`: Emits when the split view button is clicked.
-   `detailPaneClick: void`: Emits when the details pane button is clicked.
-   `themeMenuClick: MouseEvent`: Emits when the theme menu option is selected from the hamburger menu.