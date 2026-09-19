from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware

from losm.config.settings import ENABLE_CRITIC, ENABLE_TEMPLATES

app = FastAPI(title="LOSM Host", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from losm.api.receipts import router as receipts_router
from losm.api.work_requests import router as work_requests_router
from losm.api.artifacts import router as artifacts_router
from losm.api.branches import router as branches_router
from losm.api.routes import router as kernel_router

# vision-srv compatibility surface (decision 5d8e10fd): the /api-prefixed
# routes absorbed from vision-srv (:8003) so vision-ui / vision-mcp / slash
# consumers pass unchanged after the repoint to :8006.
from losm.api.compat import router as compat_router

app.include_router(receipts_router)
app.include_router(work_requests_router)
app.include_router(artifacts_router)
app.include_router(branches_router)
app.include_router(kernel_router)
app.include_router(compat_router)


@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "features": {
            "critic": ENABLE_CRITIC,
            "templates": ENABLE_TEMPLATES,
        },
    }


from losm.api.websocket import websocket_endpoint


@app.websocket("/ws")
async def ws(websocket: WebSocket):
    await websocket_endpoint(websocket)
