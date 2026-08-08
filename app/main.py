"""FastAPI application entrypoint."""

from fastapi import FastAPI

from app.routes import router


app = FastAPI(title="EternityX Interview Agent")
app.include_router(router)
