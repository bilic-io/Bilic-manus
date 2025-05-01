# main.py
from fastapi import APIRouter, FastAPI, Depends, HTTPException, Header, status, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from services.supabase import DBConnection
from datetime import datetime, timezone
from contextlib import asynccontextmanager
import uuid
import os
from jose import jwt, JWTError
import secrets
from passlib.hash import bcrypt
from dotenv import load_dotenv
from typing import Optional
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

# Load environment variables
load_dotenv()

router = APIRouter()
# Initialize database connection
db = DBConnection()

# Authentication models
class UserCreate(BaseModel):
    email: str
    password: str

class Token(BaseModel):
    access_token: str
    token_type: str

# API Key models
class ApiKeyCreate(BaseModel):
    description: Optional[str] = None

class ApiKeyResponse(BaseModel):
    key_id: str
    api_key: Optional[str]
    created_at: str
    description: Optional[str]

class ApiKeyListItem(BaseModel):
    key_id: str
    created_at: str
    description: Optional[str]
    last_used: Optional[str]

# Configuration
JWT_SECRET = os.getenv("JWT_SECRET")
JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30

# Rate limiter setup
limiter = Limiter(key_func=get_remote_address)

# Authentication utilities
def create_access_token(user_id: str):
    payload = {"sub": user_id}
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

async def authenticate_user(email: str, password: str):
    try:
        user = await db.table("users").select("*").eq("email", email).execute()
        if not user.data:
            return False
        user_data = user.data[0]
        if not bcrypt.verify(password, user_data["hashed_password"]):
            return False
        return user_data
    except Exception as e:
        raise HTTPException(status_code=500, detail="Database error")

# Authentication dependencies
async def get_current_user_from_jwt(authorization: str = Header(...)):
    try:
        scheme, token = authorization.split()
        if scheme.lower() != "bearer":
            raise HTTPException(status_code=401, detail="Invalid authentication scheme")
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        user_id = payload.get("sub")
        if not user_id:
            raise HTTPException(status_code=401, detail="Invalid JWT")
        return user_id
    except (ValueError, JWTError):
        raise HTTPException(status_code=401, detail="Invalid or missing JWT")

async def get_current_user_from_api_key(x_api_key: str = Header(...)):
    try:
        api_key_hash = bcrypt.hash(x_api_key)
        api_key_data = await db.table("api_keys").select("user_id").eq("api_key_hash", api_key_hash).eq("is_active", True).execute()
        if not api_key_data.data:
            raise HTTPException(status_code=401, detail="Invalid or inactive API key")
        
        await db.table("api_keys").update({"last_used": datetime.now(timezone.utc)}).eq("api_key_hash", api_key_hash).execute()
        return api_key_data.data[0]["user_id"]
    except Exception as e:
        raise HTTPException(status_code=500, detail="Database error")

@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.initialize()
    yield
    await db.disconnect()

# Authentication routes
@router.post("/signup", response_model=Token, status_code=status.HTTP_201_CREATED)
@limiter.limit("5/minute")
async def signup(request: Request, user: UserCreate):
    try:
        existing_user = await db.table("users").select("id").eq("email", user.email).execute()
        if existing_user.data:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email already registered"
            )

        user_id = str(uuid.uuid4())
        hashed_password = bcrypt.hash(user.password)
        
        new_user = {
            "id": user_id,
            "email": user.email,
            "hashed_password": hashed_password
        }
        
        await db.table("users").insert(new_user).execute()
        
        access_token = create_access_token(user_id)
        return {"access_token": access_token, "token_type": "bearer"}
    except Exception as e:
        raise HTTPException(status_code=500, detail="Registration failed")

@router.post("/signin", response_model=Token)
@limiter.limit("10/minute")
async def signin(request: Request, user: UserCreate):
    authenticated_user = await authenticate_user(user.email, user.password)
    if not authenticated_user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password"
        )
    
    access_token = create_access_token(authenticated_user["id"])
    return {"access_token": access_token, "token_type": "bearer"}

# API Key routes
@router.post("/api-keys", response_model=ApiKeyResponse, status_code=201)
@limiter.limit("3/minute")
async def create_api_key(request: Request, data: ApiKeyCreate, user_id: str = Depends(get_current_user_from_jwt)):
    try:
        key_id = str(uuid.uuid4())
        api_key = secrets.token_urlsafe(32)
        api_key_hash = bcrypt.hash(api_key)
        created_at = datetime.now(timezone.utc)
        
        await db.table("api_keys").insert({
            "id": key_id,
            "user_id": user_id,
            "api_key_hash": api_key_hash,
            "description": data.description,
            "created_at": created_at,
            "is_active": True
        }).execute()
        
        return {
            "key_id": key_id,
            "api_key": api_key,
            "created_at": created_at.isoformat(),
            "description": data.description
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to create API key")

@router.get("/api-keys", response_model=list[ApiKeyListItem])
@limiter.limit("10/minute")
async def list_api_keys(request: Request, user_id: str = Depends(get_current_user_from_jwt)):
    try:
        keys = await db.table("api_keys").select("id, created_at, description, last_used").eq("user_id", user_id).eq("is_active", True).execute()
        return [
            {
                "key_id": key["id"],
                "created_at": key["created_at"].isoformat(),
                "description": key["description"],
                "last_used": key["last_used"].isoformat() if key["last_used"] else None
            } for key in keys.data
        ]
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to retrieve API keys")

@router.post("/api-keys/{key_id}/regenerate", response_model=ApiKeyResponse)
@limiter.limit("3/minute")
async def regenerate_api_key(request: Request, key_id: str, user_id: str = Depends(get_current_user_from_jwt)):
    try:
        api_key_data = await db.table("api_keys").select("*").eq("id", key_id).eq("user_id", user_id).eq("is_active", True).execute()
        if not api_key_data.data:
            raise HTTPException(status_code=404, detail="API key not found or access denied")
        
        api_key = secrets.token_urlsafe(32)
        api_key_hash = bcrypt.hash(api_key)
        created_at = datetime.now(timezone.utc)
        
        await db.table("api_keys").update({
            "api_key_hash": api_key_hash,
            "created_at": created_at
        }).eq("id", key_id).execute()
        
        return {
            "key_id": key_id,
            "api_key": api_key,
            "created_at": created_at.isoformat(),
            "description": api_key_data.data[0].get("description")
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to regenerate API key")

@router.delete("/api-keys/{key_id}", status_code=204)
@limiter.limit("5/minute")
async def delete_api_key(request: Request, key_id: str, user_id: str = Depends(get_current_user_from_jwt)):
    try:
        result = await db.table("api_keys").update({"is_active": False}).eq("id", key_id).eq("user_id", user_id).execute()
        if not result.data:
            raise HTTPException(status_code=404, detail="API key not found or access denied")
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to delete API key")