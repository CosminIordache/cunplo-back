from fastapi import FastAPI
import uvicorn
from fastapi.middleware.cors import CORSMiddleware

from src.container import Container
from src.presentation.api.router import user

container = Container()
app = FastAPI()
app.container = container
app.include_router(user.router, prefix="/api/v1")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["GET", "POST","DELETE", "PUT", "PATCH"],
    allow_headers=[
        "Authorization",
        "Content-Type",
        "Accept",
        "Origin",
        "User-Agent",
        "DNT",
        "Cache-Control",
        "X-Requested-With",
    ],
)

@app.get("/")
def root():
    return {"docs": "/docs", "health": "/health", "api": "/api/v1"}

@app.get("/health")
def health():
    return {"status": "Cunplo API is healthy!"}

if __name__ == "__main__":
    uvicorn.run("src.main:app", reload=True, loop="uvloop", http="httptools")
