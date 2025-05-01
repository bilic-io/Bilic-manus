from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from services.supabase import DBConnection
from datetime import datetime, timezone
import uuid
import os
from jose import jwt, JWTError
import secrets
from fastapi import Header
from passlib.hash import bcrypt
from dotenv import load_dotenv



router = APIRouter()
db = DBConnection()

load_dotenv()
JWT_SECRET = os.getenv("JWT_SECRET", "JWT_SECRET")

async def get_current_user_from_jwt(authorization: str = Header(...)):
    try:
        scheme, token = authorization.split()
        if scheme.lower() != "bearer":
            raise HTTPException(status_code=401, detail="Invalid authentication scheme")
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        user_id = payload.get("sub")
        if not user_id:
            raise HTTPException(status_code=401, detail="Invalid JWT")
        return user_id
    except (ValueError, JWTError):
        raise HTTPException(status_code=401, detail="Invalid or missing JWT")

class ApiKeyCreate(BaseModel):
    description: str | None = None

class ApiKeyResponse(BaseModel):
    key_id: str
    api_key: str | None
    created_at: str
    description: str | None

class ApiKeyListItem(BaseModel):
    key_id: str
    created_at: str
    description: str | None
    last_used: str | None

async def generate_api_key():
    return secrets.token_urlsafe(32)

@router.post("/api-keys", response_model=ApiKeyResponse, status_code=201)
async def create_api_key(data: ApiKeyCreate, user_id: str = Depends(get_current_user_from_jwt)):
    key_id = str(uuid.uuid4())
    api_key = await generate_api_key()
    api_key_hash = bcrypt.hash(api_key)
    created_at = datetime.now(timezone.utc)
    
    await db.execute(
        """
        INSERT INTO api_keys (id, user_id, api_key_hash, description, created_at, is_active)
        VALUES ($1, $2, $3, $4, $5, $6)
        """,
        key_id, user_id, api_key_hash, data.description, created_at, True
    )
    
    return {
        "key_id": key_id,
        "api_key": api_key,
        "created_at": created_at.isoformat(),
        "description": data.description
    }

@router.get("/api-keys", response_model=dict)
async def list_api_keys(user_id: str = Depends(get_current_user_from_jwt)):
    keys = await db.fetch_all(
        "SELECT id, created_at, description, last_used FROM api_keys WHERE user_id = $1 AND is_active = true",
        user_id
    )
    return {
        "api_keys": [
            {
                "key_id": key["id"],
                "created_at": key["created_at"].isoformat(),
                "description": key["description"],
                "last_used": key["last_used"].isoformat() if key["last_used"] else None
            } for key in keys
        ]
    }

@router.post("/api-keys/{key_id}/regenerate", response_model=ApiKeyResponse)
async def regenerate_api_key(key_id: str, user_id: str = Depends(get_current_user_from_jwt)):
    api_key_data = await db.fetch_one(
        "SELECT id FROM api_keys WHERE id = $1 AND user_id = $2 AND is_active = true",
        key_id, user_id
    )
    if not api_key_data:
        raise HTTPException(status_code=404, detail="API key not found or access denied")
    
    api_key = await generate_api_key()
    api_key_hash = bcrypt.hash(api_key)
    created_at = datetime.now(timezone.utc)
    
    await db.execute(
        "UPDATE api_keys SET api_key_hash = $1, created_at = $2 WHERE id = $3",
        api_key_hash, created_at, key_id
    )
    
    return {
        "key_id": key_id,
        "api_key": api_key,
        "created_at": created_at.isoformat(),
        "description": api_key_data.get("description")
    }

@router.delete("/api-keys/{key_id}", status_code=204)
async def delete_api_key(key_id: str, user_id: str = Depends(get_current_user_from_jwt)):
    result = await db.execute(
        "UPDATE api_keys SET is_active = false WHERE id = $1 AND user_id = $2 AND is_active = true",
        key_id, user_id
    )
    if result == 0:
        raise HTTPException(status_code=404, detail="API key not found or access denied")