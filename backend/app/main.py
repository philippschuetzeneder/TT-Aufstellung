from fastapi import FastAPI

from .routers import rc

app = FastAPI()
app.include_router(rc.router, prefix="/api/rc")
