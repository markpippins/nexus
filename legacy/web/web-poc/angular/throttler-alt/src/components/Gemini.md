# `components` Directory Documentation (`/src/components/Gemini.md`)

This directory is the heart of the application's UI, containing all the reusable, standalone Angular components that construct the user interface.

## Architectural Approach

- **Modularity:** Each sub-folder represents a distinct component with a single responsibility (e.g., `toolbar`, `sidebar`, `file-explorer`).
- **Standalone Components:** All components are built using Angular's standalone API, eliminating the need for NgModules and promoting a more streamlined, component-focused architecture.
- **OnPush Change Detection:** Every component uses `changeDetection: ChangeDetectionStrategy.OnPush`. This is a performance best practice that tells Angular a component's view only needs to be updated if its inputs change, an event handler is fired from its template, or a signal it uses is updated.
- **Signal-Based:** Components heavily leverage Angular Signals for managing their internal state, making them highly reactive and efficient.
- **Clear APIs:** Components communicate with each other via well-defined `input()` properties for data flowing in and `output()` properties for events flowing out. This makes the data flow predictable and easy to trace.

---
## Core UI Components

### `file-explorer`
The main component for displaying files, folders, and the "Idea Stream" in a single pane. See its dedicated `Gemini.md` for details.

### `sidebar`
The collapsible and resizable left-hand panel containing the folder tree view and the new Chat tab. See its dedicated `Gemini.md` for details.

### `toolbar`
The row of action buttons (New, Cut, Copy, etc.) and view controls that sits above the file explorer pane.

### `detail-pane`
A slide-out pane on the right side that shows saved bookmarks and an RSS feed relevant to the current folder.

### `chat`
A component displayed within the `sidebar` that provides a boilerplate chat interface, which is enabled if a Gemini API key is configured.

### `rss-feed`
A component displayed within the `detail-pane` that shows a mock list of RSS feed items.

## "Idea Stream" Components
This is a suite of presentational components used within the `file-explorer`'s bottom pane to display different types of contextual content. They are organized into two visual styles: cards (for grid view) and list items (for list view).

- **`stream-cards`**: Contains `WebResultCardComponent`, `ImageResultCardComponent`, `YoutubeResultCardComponent`, `AcademicResultCardComponent`, and `GeminiResultCardComponent`.
- **`stream-list-items`**: Contains the list-based equivalents for each of the card components.