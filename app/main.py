"""FastAPI application entrypoint."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.routes import router


FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

app = FastAPI(title="EternityX Interview Agent")

# The API is registered first so /api/* keeps priority over the catch-all mount.
app.include_router(router)

# Serve the chat UI at / so the deployed URL is the demo itself.
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
