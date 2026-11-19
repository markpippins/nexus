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

BASE_PATH = Path("C:\\tmp\\images")  # Example root, could be from CLI args
IMAGE_DIR = os.path.join(BASE_PATH, "C:\\tmp\\images")
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


# --- Operations ---

@app.get("/images/{image_name}")
async def get_image(image_name: str):
    image_path = os.path.join(IMAGE_DIR, image_name)
    if os.path.exists(image_path) and os.path.isfile(image_path):
        return FileResponse(image_path)
    raise HTTPException(status_code=404, detail="Image not found")



# ---- Launch server in background and run demo ----
def run_server():
    uvicorn.run(app, host="127.0.0.1", port=8002, log_level="error")
