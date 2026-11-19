# `services` Directory Documentation (`/src/services/Gemini.md`)

This directory is the logical core of the application, containing all the injectable Angular services. These services encapsulate business logic, API communication, and shared state management, keeping the components lean and focused on presentation. All services are registered as singletons at the root level (`providedIn: 'root'`).

## Core Architectural Services

### `file-system-provider.ts`

-   **Purpose:** Defines the `FileSystemProvider` **abstract class**. This is a critical piece of the application's architecture. It acts as a "contract" or an interface that any file system service must adhere to.
-   **API:** It declares abstract methods for all file system operations (`getContents`, `rename`, `copy`, `move`, etc.).
-   **Benefit:** By having components depend on this abstraction rather than a concrete implementation, the application can seamlessly switch between different file systems without changing any of the UI component code.

### `in-memory-file-system.service.ts` (exports `SessionService`)

-   **Purpose:** An implementation of `FileSystemProvider` that creates a complete, writable virtual file system that persists in the browser's `localStorage`.
-   **Role:** This service provides the default "Session" drive in the application. It manages a hierarchical tree of files and folders in an Angular Signal. All file operations are handled through robust, immutable update patterns. The entire file system state is serialized to JSON and saved to `localStorage`, so changes are preserved between sessions.

### `remote-file-system.service.ts`

-   **Purpose:** An implementation of `FileSystemProvider` that communicates with a remote backend via the broker.
-   **Role:** This service is instantiated by the `AppComponent` when a user "mounts" a server profile. It uses the **user's username** as the `alias` for all backend file system requests, effectively creating a secure, sandboxed file system for each user.

## State Management & Interaction Services

### `clipboard.service.ts`
-   **Purpose:** A stateful service that acts as a global, application-wide clipboard. It holds a signal with the `ClipboardPayload`, allowing cut/copy/paste operations between components, such as two file explorer panes.

### `bookmark.service.ts`
-   **Purpose:** Manages all CRUD (Create, Read, Update, Delete) operations for bookmarks. It holds the application's entire list of bookmarks in a signal and persists them to `localStorage` whenever they change.

### `drag-drop.service.ts`
-   **Purpose:** A global singleton service that holds state *during* a drag-and-drop operation. This allows the source component (which starts the drag) to communicate the data payload to a completely unrelated destination component (which handles the drop), without needing a direct parent-child relationship.

## Content & Search Services

This is a suite of mock services that simulate fetching data for the "Idea Stream". Each service returns hardcoded data wrapped in a `Promise` with a `timer` to simulate network latency.

-   **`google-search.service.ts`**: Provides mock web search results.
-   **`unsplash.service.ts`**: Provides mock image search results.
-   **`youtube-search.service.ts`**: Provides mock video search results.
-   **`academic-search.service.ts`**: Provides mock academic paper results.
-   **`gemini.service.ts`**: Provides a mock generative AI text summary.

## Backend Communication Services

### `broker.service.ts`
- **Purpose:** A low-level service that provides a generic `submitRequest` method for communicating with a backend broker, abstracting away the details of the `fetch` API call and the request/response format.

### `fs.service.ts`
- **Purpose:** A typed wrapper around the `BrokerService` specifically for file system operations. It provides methods like `listFiles`, `createDirectory`, etc., that call the correct service and operation on the backend.

### `login.service.ts`
- **Purpose:** A typed wrapper around `BrokerService` for handling user authentication. It takes a username and password and returns a mapped `User` object on success.