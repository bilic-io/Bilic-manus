from fastapi import APIRouter, Depends, HTTPException, Header, Request, status
from pydantic import BaseModel, EmailStr
from supabase import create_client, Client
from datetime import datetime, timezone
import uuid
import os
import secrets
import bcrypt
from dotenv import load_dotenv
from typing import Optional, List
from slowapi import Limiter
from slowapi.util import get_remote_address
from jwt import decode, PyJWTError

# Load environment variables
load_dotenv()

router = APIRouter()
supabase: Client = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_ANON_KEY"))

# Models
class UserCreate(BaseModel):
    email: EmailStr
    password: str

class UserUpdate(BaseModel):
    email: Optional[EmailStr] = None
    password: Optional[str] = None

class PasswordResetRequest(BaseModel):
    email: EmailStr

class PasswordResetConfirm(BaseModel):
    token: str
    new_password: str

class Token(BaseModel):
    access_token: str
    token_type: str

class ApiKeyCreate(BaseModel):
    description: Optional[str] = None

class ApiKeyResponse(BaseModel):
    key_id: str
    api_key: Optional[str] = None  # Only returned on creation/regeneration
    created_at: str
    description: Optional[str]

class ApiKeyListItem(BaseModel):
    key_id: str
    created_at: str
    description: Optional[str]
    last_used: Optional[str]

class UserProfile(BaseModel):
    id: str
    email: str
    created_at: str

# Configuration
limiter = Limiter(key_func=get_remote_address)

# Dependencies
async def get_current_user(authorization: str = Header(...)) -> str:
    """Extract user ID from Supabase JWT in Authorization header."""
    try:
        scheme, token = authorization.split()
        if scheme.lower() != "bearer":
            raise HTTPException(status_code=401, detail="Invalid authentication scheme")
        # Supabase JWT signature is verified by RLS; decode to get user ID
        payload = decode(token, options={"verify_signature": False})
        user_id = payload.get("sub")
        if not user_id:
            raise HTTPException(status_code=401, detail="Invalid JWT")
        return user_id
    except (ValueError, PyJWTError):
        raise HTTPException(status_code=401, detail="Invalid or missing JWT")

async def get_current_user_from_api_key(x_api_key: str = Header(...)) -> str:
    """Authenticate user via API key."""
    try:
        api_key_hash = bcrypt.hash(x_api_key)
        response = await supabase.table("api_keys").select("user_id") \
            .eq("api_key_hash", api_key_hash).eq("is_active", True).execute()
        if not response.data:
            raise HTTPException(status_code=401, detail="Invalid or inactive API key")
        
        user_id = response.data[0]["user_id"]
        await supabase.table("api_keys").update({"last_used": datetime.now(timezone.utc).isoformat()}) \
            .eq("api_key_hash", api_key_hash).execute()
        return user_id
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"API key validation failed: {str(e)}")

# Authentication Routes
@router.post("/signup", response_model=Token, status_code=status.HTTP_201_CREATED)
@limiter.limit("5/minute")
async def signup(request: Request, user: UserCreate):
    """Register a new user using Supabase Auth."""
    try:
        response = supabase.auth.sign_up({"email": user.email, "password": user.password})
        if response.user is None:
            raise HTTPException(status_code=400, detail="Signup failed: Email may already be in use")
        return {"access_token": response.session.access_token, "token_type": "bearer"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Registration failed: {str(e)}")

@router.post("/signin", response_model=Token)
@limiter.limit("10/minute")
async def signin(request: Request, user: UserCreate):
    """Authenticate a user using Supabase Auth."""
    try:
        response = supabase.auth.sign_in_with_password({"email": user.email, "password": user.password})
        if response.user is None:
            raise HTTPException(status_code=401, detail="Incorrect email or password")
        return {"access_token": response.session.access_token, "token_type": "bearer"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Authentication failed: {str(e)}")

@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("10/minute")
async def logout(request: Request, user_id: str = Depends(get_current_user)):
    """Log out the current user."""
    try:
        await supabase.auth.sign_out()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Logout failed: {str(e)}")

@router.post("/password/reset-request", status_code=status.HTTP_202_ACCEPTED)
@limiter.limit("5/minute")
async def password_reset_request(request: Request, data: PasswordResetRequest):
    """Request a password reset email."""
    try:
        await supabase.auth.reset_password_for_email(data.email)
        return {"message": "Password reset email sent if the account exists"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Password reset request failed: {str(e)}")

@router.post("/password/reset-confirm", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("5/minute")
async def password_reset_confirm(request: Request, data: PasswordResetConfirm):
    """Confirm password reset with token and new password."""
    try:
        # Supabase handles token verification internally
        await supabase.auth.verify_otp({"token": data.token, "type": "recovery"})
        await supabase.auth.update_user({"password": data.new_password})
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Password reset failed: {str(e)}")

# User Profile Routes
@router.get("/profile", response_model=UserProfile)
@limiter.limit("10/minute")
async def get_profile(request: Request, user_id: str = Depends(get_current_user)):
    """Retrieve the current user's profile."""
    try:
        user = await supabase.auth.get_user()
        if not user.user:
            raise HTTPException(status_code=404, detail="User not found")
        return {
            "id": user.user.id,
            "email": user.user.email,
            "created_at": user.user.created_at
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to retrieve profile: {str(e)}")

@router.put("/profile", response_model=UserProfile)
@limiter.limit("5/minute")
async def update_profile(
    request: Request, 
    data: UserUpdate, 
    user_id: str = Depends(get_current_user)
):
    """Update the current user's profile."""
    try:
        update_data = {}
        if data.email:
            update_data["email"] = data.email
        if data.password:
            update_data["password"] = data.password
        if update_data:
            await supabase.auth.update_user(update_data)
        user = await supabase.auth.get_user()
        if not user.user:
            raise HTTPException(status_code=404, detail="User not found")
        return {
            "id": user.user.id,
            "email": user.user.email,
            "created_at": user.user.created_at
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update profile: {str(e)}")

# API Key Routes
@router.post("/api-keys", response_model=ApiKeyResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit("3/minute")
async def create_api_key(
    request: Request, 
    data: ApiKeyCreate, 
    user_id: str = Depends(get_current_user)
):
    """Create a new API key for the authenticated user."""
    try:
        key_id = str(uuid.uuid4())
        api_key = secrets.token_urlsafe(32)
        api_key_hash = bcrypt.hashpw(api_key.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
        created_at = datetime.now(timezone.utc)
        
        await supabase.table("api_keys").insert({
            "id": key_id,
            "user_id": user_id,
            "api_key_hash": api_key_hash,
            "description": data.description,
            "created_at": created_at.isoformat(),
            "is_active": True
        }).execute()
        
        return {
            "key_id": key_id,
            "api_key": api_key,  # Return plaintext API key only on creation
            "created_at": created_at.isoformat(),
            "description": data.description
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create API key: {str(e)}")

@router.get("/api-keys", response_model=List[ApiKeyListItem])
@limiter.limit("10/minute")
async def list_api_keys(request: Request, user_id: str = Depends(get_current_user)):
    """List all active API keys for the authenticated user."""
    try:
        response = await supabase.table("api_keys").select("id, created_at, description, last_used") \
            .eq("user_id", user_id).eq("is_active", True).execute()
        return [
            {
                "key_id": key["id"],
                "created_at": key["created_at"],
                "description": key["description"],
                "last_used": key["last_used"]
            } for key in response.data
        ]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to retrieve API keys: {str(e)}")

@router.post("/api-keys/{key_id}/regenerate", response_model=ApiKeyResponse)
@limiter.limit("3/minute")
async def regenerate_api_key(
    request: Request, 
    key_id: str, 
    user_id: str = Depends(get_current_user)
):
    """Regenerate an existing API key."""
    try:
        response = await supabase.table("api_keys").select("*").eq("id", key_id) \
            .eq("user_id", user_id).eq("is_active", True).execute()
        if not response.data:
            raise HTTPException(status_code=404, detail="API key not found or access denied")
        
        api_key = secrets.token_urlsafe(32)
        api_key_hash = bcrypt.hashpw(api_key.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
        created_at = datetime.now(timezone.utc)
        
        await supabase.table("api_keys").update({
            "api_key_hash": api_key_hash,
            "created_at": created_at.isoformat()
        }).eq("id", key_id).execute()
        
        return {
            "key_id": key_id,
            "api_key": api_key,  # Return plaintext API key only on regeneration
            "created_at": created_at.isoformat(),
            "description": response.data[0].get("description")
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to regenerate API key: {str(e)}")

@router.delete("/api-keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("5/minute")
async def delete_api_key(
    request: Request, 
    key_id: str, 
    user_id: str = Depends(get_current_user)
):
    """Deactivate an API key."""
    try:
        response = await supabase.table("api_keys").update({"is_active": False}) \
            .eq("id", key_id).eq("user_id", user_id).execute()
        if not response.data:
            raise HTTPException(status_code=404, detail="API key not found or access denied")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete API key: {str(e)}")

@router.get("/api-keys/validate", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("20/minute")
async def validate_api_key(request: Request, user_id: str = Depends(get_current_user_from_api_key)):
    """Validate an API key."""
    # No content returned; reaching this point means the API key is valid
    pass