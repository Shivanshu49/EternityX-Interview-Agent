"""FastAPI application entrypoint."""

# Load .env before anything else imports app modules. app.llm reads
# ANTHROPIC_API_KEY at import time, and app.routes imports it transitively, so
# this has to run above those imports rather than below them -- otherwise the
# SDK is constructed against an environment that has not been populated yet.
from dotenv import load_dotenv

load_dotenv()

import logging  # noqa: E402
import os  # noqa: E402
from contextlib import asynccontextmanager  # noqa: E402
from pathlib import Path  # noqa: E402

from fastapi import FastAPI  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

from app import llm  # noqa: E402
from app.routes import router  # noqa: E402


FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
log = logging.getLogger("uvicorn.error")


@asynccontextmanager
async def lifespan(_: FastAPI):
    """State plainly where requests go and whether a key was found.

    A silent misconfiguration here is the difference between a working demo and
    a 503 nobody can explain, so it is logged rather than left to be inferred.
    Only the key's length is logged -- never any part of its value.
    """
    key = os.getenv("ANTHROPIC_API_KEY")
    log.info("[config] endpoint : %s", llm.describe_endpoint())
    log.info("[config] model    : %s", llm.DEFAULT_MODEL)
    log.info("[config] api key  : %s",
             f"loaded, {len(key)} chars" if key else "MISSING - set it in .env")
    yield


app = FastAPI(title="EternityX Interview Agent", lifespan=lifespan)

# The API is registered first so /api/* keeps priority over the catch-all mount.
app.include_router(router)

# Serve the chat UI at / so the deployed URL is the demo itself.
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
