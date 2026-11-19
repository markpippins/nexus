# `TabControl` and `Tab` Components Documentation (`/src/components/tabs/Gemini.md`)

This directory contains a pair of components that work together to create a reusable, generic tabbed interface. This pattern is a powerful example of content projection and component composition in Angular.

## `TabControlComponent`

This is the container or "manager" component for the tab set.

### Responsibilities

-   **Tab Discovery:** It uses the `@contentChildren` query to get a list of all `<app-tab>` components that are projected into it from a parent template (like `sidebar.component.html`).
-   **State Management:** It maintains the `activeTabIndex` signal, which keeps track of which tab is currently selected.
-   **Rendering Headers:** It iterates through the discovered `tabs()` and renders a button for each one, using the `tab.title()` for the label.
-   **Active Tab Coordination:** It uses an `effect` to watch for changes to `activeTabIndex`. When the index changes, it iterates through all the `TabComponent` instances and sets the `active` signal to `true` for the selected tab and `false` for all others.

### API

-   **Outputs:** `collapseClick: void` (A specific output for its use case in the sidebar).

## `TabComponent`

This is a simple wrapper component for the content of a single tab.

### Responsibilities

-   **Content Projection:** Its primary purpose is to hold the content for a tab using `<ng-content>`.
-   **Visibility Control:** It has an `active` signal which is controlled by the parent `TabControlComponent`. The content inside the `TabComponent` is conditionally hidden (`[hidden]="!active()"`) based on this signal.
-   **Metadata:** It provides metadata (its `title`) to the `TabControlComponent` so the controller knows what to render in the tab header.

### API

-   **Inputs:** `title: string` (required).
