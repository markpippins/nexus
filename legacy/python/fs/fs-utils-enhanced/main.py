# main.py
import uvicorn
import requests
import threading
import time
import os
from pathlib import Path

# Import our service (can be inline if preferred)
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import shutil

from starlette.responses import FileResponse

BASE_PATH = Path("C:\\tmp\\users")  # Example root, could be from CLI args
BASE_PATH.mkdir(exist_ok=True)

app = FastAPI()

from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # ⚠️ less secure, but fine for local dev
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Utilities ---
def ensure_path_exists(user_root: Path, parts: list[str]) -> Path:
    full_path = user_root.joinpath(*parts).resolve()
    if not str(full_path).startswith(str(user_root)):
        raise HTTPException(status_code=400, detail="Invalid path traversal")
    return full_path

def get_user_root(alias: str) -> Path:
    return BASE_PATH

# --- Models ---
class RequestModel(BaseModel):
    alias: str
    path: list[str] = []
    operation: str
    new_name: str | None = None
    filename: str | None = None

# --- Operations ---

@app.post("/fs")
def handle_request(req: RequestModel):
    user_root = get_user_root(req.alias)

    if req.operation == "ls":
        target = ensure_path_exists(user_root, req.path)
        if not target.exists() or not target.is_dir():
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
        return {"path": req.path, "items": items}

    elif req.operation == "cd":
        target = ensure_path_exists(user_root, req.path)
        if not target.exists() or not target.is_dir():
            raise HTTPException(status_code=404, detail="Directory not found")
        return {"path": req.path}

    elif req.operation == "mkdir":
        target = ensure_path_exists(user_root, req.path)
        target.mkdir(parents=True, exist_ok=True)
        return {"created": str(target)}

    elif req.operation == "rmdir":
        target = ensure_path_exists(user_root, req.path)
        if not target.exists() or not target.is_dir():
            raise HTTPException(status_code=404, detail="Directory not found")
        shutil.rmtree(target)
        return {"deleted": str(target)}

    elif req.operation == "newfile":
        if not req.filename:
            raise HTTPException(status_code=400, detail="Filename required")
        target = ensure_path_exists(user_root, req.path) / req.filename
        target.parent.mkdir(parents=True, exist_ok=True)
        target.touch(exist_ok=True)
        return {"created_file": str(target)}

    elif req.operation == "deletefile":
        if not req.filename:
            raise HTTPException(status_code=400, detail="Filename required")
        target = ensure_path_exists(user_root, req.path) / req.filename
        if not target.exists() or not target.is_file():
            raise HTTPException(status_code=404, detail="File not found")
        target.unlink()
        return {"deleted_file": str(target)}

    elif req.operation == "rename":
        if not req.new_name:
            raise HTTPException(status_code=400, detail="New name required")
        target = ensure_path_exists(user_root, req.path)
        if not target.exists():
            raise HTTPException(status_code=404, detail="Path not found")
        new_target = target.parent / req.new_name
        target.rename(new_target)
        return {"renamed": str(target), "to": str(new_target)}

    else:
        raise HTTPException(status_code=400, detail=f"Unknown operation {req.operation}")


# ---- Launch server in background and run demo ----
def run_server():
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="error")

# uvicorn main:app --reload --host 127.0.0.1 --port 8000

if __name__ == "__main__":
    import sys
    # if "--demo" in sys.argv:
    #     # run server in background and execute test demo
    thread = threading.Thread(target=run_server, daemon=True)
    thread.start()
    time.sleep(1)
    #     # run_demo_requests()  # your sequence of sample API calls
    # else:
        # run server in the foreground (stays alive)
    # uvicorn.run(app, host="127.0.0.1", port=8000, reload=True)

    alias = "user123"

    def call_api(payload):
        r = requests.post("http://127.0.0.1:8000/fs", json=payload)
        print("Request:", payload)
        print("Response:", r.json(), "\n")

    # Demonstration of operations
    call_api({"alias": alias, "path": [], "operation": "ls"})
    call_api({"alias": alias, "path": ["docs"], "operation": "mkdir"})
    call_api({"alias": alias, "path": ["docs"], "operation": "ls"})
    call_api({"alias": alias, "path": ["docs"], "operation": "newfile", "filename": "notes.txt"})
    call_api({"alias": alias, "path": ["docs"], "operation": "ls"})
    call_api({"alias": alias, "path": ["docs", "notes.txt"], "operation": "rename", "new_name": "renamed.txt"})
    call_api({"alias": alias, "path": ["docs"], "operation": "ls"})
    call_api({"alias": alias, "path": ["docs"], "operation": "deletefile", "filename": "renamed.txt"})
    call_api({"alias": alias, "path": [], "operation": "rmdir"})
    call_api({"alias": alias, "path": [], "operation": "ls"})
