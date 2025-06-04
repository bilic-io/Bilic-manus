from fastapi import FastAPI, Depends, HTTPException, Header, Request, Security
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from agentpress.thread_manager import ThreadManager
from services.supabase import DBConnection
from datetime import datetime, timezone
from dotenv import load_dotenv
import asyncio
from utils.logger import logger
import uuid
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from passlib.hash import bcrypt
from jose import jwt, JWTError
import os

# Import API modules
from agent import api as agent_api
from sandbox import api as sandbox_api
from external_api_manager.router import router as api_key_router

# Load environment variables
load_dotenv()
JWT_SECRET = os.getenv("JWT_SECRET", "JWT_SECRET")

# Initialize managers
db = DBConnection()
thread_manager = None
instance_id = str(uuid.uuid4())[:8]

# Rate limiter setup
limiter = Limiter(key_func=lambda request: request.headers.get("X-API-Key", get_remote_address(request)))



async def get_current_user_from_api_key(x_api_key: str = Header(...)):
    api_key_hash = bcrypt.hash(x_api_key)
    api_key_data = await db.fetch_one("SELECT user_id FROM api_keys WHERE api_key_hash = $1 AND is_active = true", api_key_hash)
    if not api_key_data:
        raise HTTPException(status_code=401, detail="Invalid or inactive API key")
    await db.execute("UPDATE api_keys SET last_used = $1 WHERE api_key_hash = $2", datetime.now(timezone.utc), api_key_hash)
    return api_key_data["user_id"]

# Combined authentication dependency
async def get_current_user(
    authorization: str = Header(None),
    x_api_key: str = Header(None)
):
    if authorization:
        # Try to authenticate using JWT
        try:
            # return await get_current_user_from_jwt(authorization)
            return
        except HTTPException as jwt_exception:
            if not x_api_key:
                raise jwt_exception  # Raise JWT error if no API key is provided
    if x_api_key:
        # Try to authenticate using API key
        return await get_current_user_from_api_key(x_api_key)
    # If neither JWT nor API key is provided, raise an error
    raise HTTPException(status_code=401, detail="Authorization header or X-API-Key must be provided")

@asynccontextmanager
async def lifespan(app: FastAPI):
    global thread_manager
    logger.info(f"Starting up FastAPI application with instance ID: {instance_id}")
    await db.initialize()
    thread_manager = ThreadManager()
    
    agent_api.initialize(thread_manager, db, instance_id)
    sandbox_api.initialize(db)
    
    from services import redis
    await redis.initialize_async()
    asyncio.create_task(agent_api.restore_running_agent_runs())
    
    yield
    
    
    logger.info("Cleaning up agent resources")
    await agent_api.cleanup()
    logger.info("Disconnecting from database")
    await db.disconnect()

app = FastAPI(lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-API-Key"],
)

# Include routers with the combined authentication dependency
app.include_router(
    agent_api.router,
    prefix="/api",
    # dependencies=[Depends(get_current_user)]
)
app.include_router(
    sandbox_api.router,
    prefix="/api",
    # dependencies=[Depends(get_current_user)]
)
app.include_router(
    api_key_router,
    prefix="/api",
    # dependencies=[Depends(get_current_user)]
)

@app.get("/api/health-check")
async def health_check():
    logger.info("Health check endpoint called")
    return {
        "status": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "instance_id": instance_id
    }

if __name__ == "__main__":
    import uvicorn
    logger.info("Starting server on 0.0.0.0:8000")
    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=True)