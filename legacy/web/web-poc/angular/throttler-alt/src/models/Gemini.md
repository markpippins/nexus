# `models` Directory Documentation (`/src/models/Gemini.md`)

This directory contains the TypeScript `interface` and `type` definitions that establish the core data models for the entire application. Defining these structures provides strong typing, improves code clarity, and prevents common errors.

## Core File System Models (`file-system.model.ts`)

-   **`FileType`**: A string literal type (`'folder' | 'file'`) that ensures the `type` property of a node can only be one of these two values.
-   **`FileSystemNode`**: The fundamental interface for files and folders. It's a recursive structure that can represent an entire directory tree.
-   **`SearchResultNode`**: Extends `FileSystemNode` to include the `path` to the item, used for displaying file search results.

## Server & User Models

### `server-profile.model.ts`
-   **`ServerProfile`**: Defines the connection profile for a remote server, including URLs for the broker and image services.

### `user.model.ts`
-   **`User`**: Defines the structure for an authenticated user's data. The `username` property is critically used as the `alias` for remote file system operations.

## "Idea Stream" and Search Models

These models define the shape of data returned from the various (mock) search services used in the Idea Stream.

- **`google-search-result.model.ts`**: Defines a standard web search result with a title, link, snippet, and source.
- **`image-search-result.model.ts`**: Defines an image result with URLs, a description, and photographer info.
- **`youtube-search-result.model.ts`**: Defines a video result with a video ID, title, thumbnail, and channel information.
- **`academic-search-result.model.ts`**: Defines a result for a scholarly article, including authors and publication.

## Bookmark Model (`bookmark.model.ts`)

- **`BookmarkType`**: A string literal type (`'web' | 'image' | 'youtube'`, etc.) corresponding to the different types of content that can be saved.
- **`Bookmark`**: The interface for a saved bookmark. It includes the original content data, a unique ID, and the `path` of the folder it was saved to.
- **`NewBookmark`**: A utility type representing a bookmark before it has been saved and assigned an ID and path.