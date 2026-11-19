/**
 * File System Service Operations
 * 
 * All REST endpoints for file system operations.
 * Each operation is a POST endpoint that accepts FsRequest and returns
 * either FsListResponse or FsOperationResponse.
 */
import "@typespec/http";

using Http;

/**
 * List files and directories at the specified path.
 */
@route("/list")
@post
op listFiles(input: FsRequest): FsListResponse;

/**
 * Change to the specified directory (validates path exists and is a directory).
 */
@route("/cd")
@post
op changeDirectory(input: FsRequest): FsListResponse;

/**
 * Create a new directory at the specified path.
 */
@route("/mkdir")
@post
op createDirectory(input: FsRequest): FsOperationResponse;

/**
 * Remove a directory and all its contents.
 */
@route("/rmdir")
@post
op removeDirectory(input: FsRequest): FsOperationResponse;

/**
 * Create a new empty file.
 */
@route("/touch")
@post
op createFile(input: FsRequest): FsOperationResponse;

/**
 * Delete a file.
 */
@route("/rm")
@post
op deleteFile(input: FsRequest): FsOperationResponse;

/**
 * Rename a file or directory.
 */
@route("/rename")
@post
op rename(input: FsRequest): FsOperationResponse;

/**
 * Rename a file or directory (alternative endpoint).
 */
@route("/rename-item")
@post
op renameItem(input: FsRequest): FsOperationResponse;

/**
 * Copy a file or directory to a new location.
 * Supports cross-user copy when toToken/toAlias are provided.
 */
@route("/copy")
@post
op copy(input: FsRequest): FsOperationResponse;

/**
 * Move a file or directory to a new location.
 * Supports cross-user move when toToken/toAlias are provided.
 */
@route("/move")
@post
op move(input: FsRequest): FsOperationResponse;

/**
 * Move multiple items to a destination directory.
 */
@route("/move-items")
@post
op moveItems(input: FsRequest): FsOperationResponse;

/**
 * Check if a file exists at the specified path.
 */
@route("/has-file")
@post
op hasFile(input: FsRequest): { exists: boolean };

/**
 * Check if a folder exists at the specified path.
 */
@route("/has-folder")
@post
op hasFolder(input: FsRequest): { exists: boolean };
