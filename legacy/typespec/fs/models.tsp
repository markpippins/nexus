/**
 * File System Service Models
 * 
 * Common data structures used across all file system operations.
 */

/**
 * Represents a file or directory item in the file system.
 */
model FsItem {
  name: string;
  type: string;
  size: int64;
  lastModified: utcDateTime;
  lastModifiedDate?: string;
  url?: string;
  thumbnailUrl?: string;
  deleteUrl?: string;
  deleteType?: string;
}

/**
 * Response for list operations containing path and items.
 */
model FsListResponse {
  path: string[];
  items: FsItem[];
}

/**
 * Reference to a file or folder item for batch operations.
 */
model FsItemReference {
  name: string;
  type: "file" | "folder";
}

/**
 * Generic response for file system operations.
 * Different operations populate different fields.
 */
model FsOperationResponse {
  path?: string[];
  items?: FsItem[];
  created?: string;
  deleted?: string;
  created_file?: string;
  deleted_file?: string;
  renamed?: string;
  to?: string;
  copied?: string;
  moved?: string;
  exists?: boolean;
  type?: string;
}

/**
 * Request model for all file system operations.
 * 
 * Authentication: Both token and alias are required.
 * - token: UUID identifying the authenticated user session (temporary, will be replaced by proper auth)
 * - alias: User's unique alias used to prepend to paths for user-scoped file operations
 */
model FsRequest {
  token?: string;
  `alias`?: string;
  path?: string[];
  filename?: string;
  newName?: string;
  toToken?: string;
  toAlias?: string;
  toPath?: string[];
  items?: FsItemReference[];
  operation?: string;
}
