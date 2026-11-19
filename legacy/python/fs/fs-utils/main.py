"""
File System Server - Python Implementation
==========================================

This is a Python-based file system server that provides similar functionality to the Node.js file-system-server.
It supports a wide range of file system operations including listing, creating, deleting, moving, copying,
and checking for existence of files and directories.

The server implements security measures to prevent path traversal attacks and includes proper error handling
and logging for debugging purposes.

Supported Operations:
- ls: List directory contents
- cd: Change directory (validate directory exists)
- mkdir: Create directory
- rmdir: Remove directory (recursively)
- newfile: Create new file
- deletefile: Delete file
- rename: Rename file or directory
- copy: Copy file or directory
- move: Move file or directory
- hasfile: Check if file exists
- hasfolder: Check if folder exists
- health: Health check endpoint

Environment Variables:
- FS_ROOT_DIR: Root directory for file operations (defaults to current directory/fs_root)
- FS_SERVER_PORT: Port for the server (defaults to 4040)
"""

import uvicorn
import requests
import threading
import time
import os
from pathlib import Path
import shutil
import logging
from datetime import datetime
from typing import Optional
from pydantic import BaseModel

# Import our service (can be inline if preferred)
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s [%(name)s] %(message)s',
    handlers=[
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("file-system-server")

# Determine root directory - check environment variable first, then use default
FS_ROOT_DIR = os.environ.get('FS_ROOT_DIR', os.path.join(os.getcwd(), 'fs_root'))
FS_ROOT_DIR = Path(FS_ROOT_DIR).resolve()
FS_ROOT_DIR.mkdir(exist_ok=True)

# Determine port - check environment variable first, then use default
FS_SERVER_PORT = int(os.environ.get('FS_SERVER_PORT', 4040))

app = FastAPI(title="File System Server", description="A Python-based file system server")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # ⚠️ less secure, but fine for local dev
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Utilities ---
def ensure_path_exists(user_root: Path, parts: list[str]) -> Path:
    """
    Ensures that a resolved path is safely within a specified root directory.

    Args:
        user_root: The absolute path to the root directory.
        parts: A list of path segments to join.

    Returns:
        The resolved, validated absolute path.

    Raises:
        HTTPException: If the path traversal is attempted.
    """
    if parts is None:
        parts = []
    full_path = user_root.joinpath(*parts).resolve()
    if not str(full_path).startswith(str(user_root)):
        logger.warning(f"Invalid path traversal attempted: {full_path} outside {user_root}")
        raise HTTPException(status_code=400, detail="Invalid path traversal")
    return full_path

def get_user_root(alias: str) -> Path:
    """
    Get the user root directory. In this implementation, we use a single root directory.

    Args:
        alias: User alias (currently ignored, using global root)

    Returns:
        The root directory path for the user.
    """
    return FS_ROOT_DIR

# --- Models ---
class RequestModel(BaseModel):
    """
    Model representing the request payload for file system operations.
    """
    alias: str
    path: list[str] = []
    operation: str
    new_name: Optional[str] = None
    filename: Optional[str] = None
    source_path: Optional[list[str]] = None
    to_alias: Optional[str] = None
    to_path: Optional[list[str]] = None
    dest_path: Optional[list[str]] = None

# --- Operations ---

@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Middleware to log incoming requests."""
    start_time = datetime.utcnow()
    response = await call_next(request)
    process_time = (datetime.utcnow() - start_time).total_seconds()
    logger.info(f"{request.method} {request.url.path} - {response.status_code} - {process_time:.3f}s")
    return response

@app.get("/health")
async def health_check():
    """
    Health check endpoint to verify the server is running and the file system root is accessible.

    Returns:
        Health check response with status and details.
    """
    logger.info("Health check endpoint called")
    try:
        # Check if the file system root directory is accessible
        if not FS_ROOT_DIR.exists():
            logger.error(f"File system root directory does not exist: {FS_ROOT_DIR}")
            return {
                "status": "DOWN",
                "service": "file-system-server",
                "timestamp": datetime.utcnow().isoformat(),
                "error": "File system root directory not accessible"
            }

        response_data = {
            "status": "UP",
            "service": "file-system-server",
            "timestamp": datetime.utcnow().isoformat(),
            "details": {
                "fsRootDir": str(FS_ROOT_DIR),
                "port": FS_SERVER_PORT
            }
        }
        logger.info("Health check successful")
        return response_data
    except Exception as e:
        logger.error(f"Health check failed: {str(e)}")
        return {
            "status": "DOWN",
            "service": "file-system-server",
            "timestamp": datetime.utcnow().isoformat(),
            "error": "File system root directory not accessible"
        }

@app.post("/fs")
async def handle_request(req: RequestModel):
    """
    Main endpoint to handle file system operations.

    Args:
        req: RequestModel containing operation details

    Returns:
        Operation result as JSON
    """
    logger.info(f"Processing {req.operation} operation", extra={
        "operation": req.operation,
        "path": req.path,
        "alias": req.alias
    })

    user_root = get_user_root(req.alias)

    try:
        if req.operation == "ls":
            logger.info(f"Listing directory contents: {req.path}")
            target = ensure_path_exists(user_root, req.path)
            if not target.exists() or not target.is_dir():
                logger.warning(f"Directory not found: {target}")
                raise HTTPException(status_code=404, detail="Directory not found")

            items = []
            for p in target.iterdir():
                stat = p.stat()
                items.append({
                    "name": p.name,
                    "type": "directory" if p.is_dir() else "file",
                    "size": stat.st_size,
                    "last_modified": stat.st_mtime
                })
            logger.info(f"Directory listing completed: {len(items)} items")
            return {"path": req.path, "items": items}

        elif req.operation == "cd":
            logger.info(f"Changing directory: {req.path}")
            target = ensure_path_exists(user_root, req.path)
            if not target.exists() or not target.is_dir():
                logger.warning(f"Cannot change to non-directory path: {target}")
                raise HTTPException(status_code=404, detail="Directory not found")
            logger.info(f"Directory change completed: {req.path}")
            return {"path": req.path}

        elif req.operation == "mkdir":
            logger.info(f"Creating directory: {req.path}")
            target = ensure_path_exists(user_root, req.path)
            target.mkdir(parents=True, exist_ok=True)
            logger.info(f"Directory created: {target}")
            return {"created": str(target)}

        elif req.operation == "rmdir":
            logger.info(f"Removing directory: {req.path}")
            target = ensure_path_exists(user_root, req.path)
            if not target.exists() or not target.is_dir():
                logger.warning(f"Directory not found for removal: {target}")
                raise HTTPException(status_code=404, detail="Directory not found")
            shutil.rmtree(target)
            logger.info(f"Directory removed: {target}")
            return {"deleted": str(target)}

        elif req.operation == "newfile":
            if not req.filename:
                logger.warning("New file operation missing filename")
                raise HTTPException(status_code=400, detail="Filename required")
            logger.info(f"Creating new file: {req.filename} in {req.path}")
            target = ensure_path_exists(user_root, req.path) / req.filename
            target.parent.mkdir(parents=True, exist_ok=True)
            target.touch(exist_ok=True)
            logger.info(f"New file created: {target}")
            return {"created_file": str(target)}

        elif req.operation == "deletefile":
            if not req.filename:
                logger.warning("Delete file operation missing filename")
                raise HTTPException(status_code=400, detail="Filename required")
            logger.info(f"Deleting file: {req.filename} from {req.path}")
            target = ensure_path_exists(user_root, req.path) / req.filename
            if not target.exists() or not target.is_file():
                logger.warning(f"File not found for deletion: {target}")
                raise HTTPException(status_code=404, detail="File not found")
            target.unlink()
            logger.info(f"File deleted: {target}")
            return {"deleted_file": str(target)}

        elif req.operation == "rename":
            if not req.new_name:
                logger.warning("Rename operation missing new name")
                raise HTTPException(status_code=400, detail="New name required")
            logger.info(f"Renaming: {req.path} to {req.new_name}")
            target = ensure_path_exists(user_root, req.path)
            if not target.exists():
                logger.warning(f"Path not found for renaming: {target}")
                raise HTTPException(status_code=404, detail="Path not found")
            new_target = target.parent / req.new_name
            target.rename(new_target)
            logger.info(f"Rename completed: {target} to {new_target}")
            return {"renamed": str(target), "to": str(new_target)}

        elif req.operation == "copy":
            if not req.to_path:
                logger.warning("Copy operation missing destination path")
                raise HTTPException(status_code=400, detail="Destination path required")
            logger.info(f"Copying: {req.path} to {req.to_path}")
            source_path = ensure_path_exists(user_root, req.path)
            dest_path = ensure_path_exists(user_root, req.to_path)
            if source_path.is_file():
                shutil.copy2(source_path, dest_path)
            else:
                shutil.copytree(source_path, dest_path, dirs_exist_ok=True)
            logger.info(f"Copy completed: {source_path} to {dest_path}")
            return {"copied": str(source_path), "to": str(dest_path)}

        elif req.operation == "move":
            if not req.to_path:
                logger.warning("Move operation missing destination path")
                raise HTTPException(status_code=400, detail="Destination path required")
            logger.info(f"Moving: {req.path} to {req.to_path}")
            source_path = ensure_path_exists(user_root, req.path)
            dest_path = ensure_path_exists(user_root, req.to_path)
            shutil.move(str(source_path), str(dest_path))
            logger.info(f"Move completed: {source_path} to {dest_path}")
            return {"moved": str(source_path), "to": str(dest_path)}

        elif req.operation == "hasfile":
            if not req.filename:
                logger.warning("HasFile operation missing filename")
                raise HTTPException(status_code=400, detail="Filename required")
            logger.info(f"Checking if file exists: {req.filename} in {req.path}")
            target_path = ensure_path_exists(user_root, req.path + [req.filename])
            exists = target_path.exists() and target_path.is_file()
            result = {"exists": exists, "path": str(target_path), "type": "file"}
            logger.info(f"File existence check: {target_path} exists={exists}")
            return result

        elif req.operation == "hasfolder":
            if not req.filename:
                logger.warning("HasFolder operation missing folder name")
                raise HTTPException(status_code=400, detail="Folder name required")
            logger.info(f"Checking if folder exists: {req.filename} in {req.path}")
            target_path = ensure_path_exists(user_root, req.path + [req.filename])
            exists = target_path.exists() and target_path.is_dir()
            result = {"exists": exists, "path": str(target_path), "type": "directory"}
            logger.info(f"Folder existence check: {target_path} exists={exists}")
            return result

        else:
            logger.warning(f"Unknown operation requested: {req.operation}")
            raise HTTPException(status_code=400, detail=f"Unknown operation {req.operation}")

    except HTTPException:
        # Re-raise HTTP exceptions as-is
        raise
    except Exception as e:
        logger.error(f"Request processing failed: {str(e)}", extra={
            "operation": req.operation,
            "error": str(e)
        })
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


# ---- Launch server in background and run demo ----
def run_server():
    """Start the FastAPI server."""
    logger.info(f"Starting server on port {FS_SERVER_PORT}")
    logger.info(f"File system root is {FS_ROOT_DIR}")
    uvicorn.run(app, host="127.0.0.1", port=FS_SERVER_PORT, log_level="info")

if __name__ == "__main__":
    import sys
    
    # Start server in background for demonstration
    thread = threading.Thread(target=run_server, daemon=True)
    thread.start()
    time.sleep(2)  # Wait for server to start
    
    alias = "user123"

    def call_api(payload):
        """Helper function to make API calls for demonstration."""
        try:
            r = requests.post("http://127.0.0.1:4040/fs", json=payload)
            print("Request:", payload)
            print("Response:", r.json(), "\n")
        except requests.exceptions.ConnectionError:
            print("Could not connect to server. Make sure it's running.")
        except Exception as e:
            print(f"Error making request: {e}")

    # Demonstration of operations
    print("Demonstrating file system operations...\n")
    
    # Basic operations
    call_api({"alias": alias, "path": [], "operation": "ls"})
    call_api({"alias": alias, "path": ["docs"], "operation": "mkdir"})
    call_api({"alias": alias, "path": ["docs"], "operation": "ls"})
    call_api({"alias": alias, "path": ["docs"], "operation": "newfile", "filename": "notes.txt"})
    call_api({"alias": alias, "path": ["docs"], "operation": "ls"})
    call_api({"alias": alias, "path": ["docs", "notes.txt"], "operation": "rename", "new_name": "renamed.txt"})
    call_api({"alias": alias, "path": ["docs"], "operation": "ls"})
    
    # Enhanced operations
    call_api({"alias": alias, "path": ["docs", "renamed.txt"], "operation": "copy", "to_path": ["backup"]})
    call_api({"alias": alias, "path": ["backup"], "operation": "ls"})
    call_api({"alias": alias, "path": ["docs", "renamed.txt"], "operation": "move", "to_path": ["backup", "moved_file.txt"]})
    call_api({"alias": alias, "path": ["docs"], "operation": "ls"})
    call_api({"alias": alias, "path": ["backup"], "operation": "ls"})
    
    # Existence checks
    call_api({"alias": alias, "path": ["backup"], "operation": "hasfile", "filename": "moved_file.txt"})
    call_api({"alias": alias, "path": ["backup"], "operation": "hasfolder", "filename": "nonexistent_folder"})
    
    # Cleanup
    call_api({"alias": alias, "path": ["backup"], "operation": "rmdir"})
    call_api({"alias": alias, "path": ["docs"], "operation": "rmdir"})
    call_api({"alias": alias, "path": [], "operation": "ls"})
    
    print("\nDemonstration completed. Server is running on http://127.0.0.1:4040")
    print("Press Ctrl+C to stop the server.")
    
    try:
        # Keep the main thread alive to keep the server running
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nShutting down server...")