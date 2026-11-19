# `VerticalToolbarComponent` Documentation (`/src/components/vertical-toolbar/Gemini.md`)

This is a simple, presentational component that is displayed when the main `SidebarComponent` is in its collapsed state.

## Core Responsibilities

-   **Displaying Essential Controls:** In the collapsed view, there is limited space. This toolbar's purpose is to show only the most critical icons. Currently, this is just the "Expand" button.
-   **Delegating Actions:** It does not implement any logic itself. When the expand button is clicked, it emits an `expandClick` event to its parent, the `SidebarComponent`, which then handles the logic of expanding the sidebar.

## API and Data Flow

-   **Inputs:** None.
-   **Outputs:** `expandClick: void`.
