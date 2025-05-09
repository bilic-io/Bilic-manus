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
import logging
import jwt

from utils.auth_utils import sign_up_user  # Add this import

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

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
    logger.info("Extracting user ID from JWT")
    try:
        scheme, token = authorization.split()
        if scheme.lower() != "bearer":
            logger.warning("Invalid authentication scheme")
            raise HTTPException(status_code=401, detail="Invalid authentication scheme")
        payload = decode(token, options={"verify_signature": False})
        user_id = payload.get("sub")
        if not user_id:
            logger.warning("Invalid JWT: Missing user ID")
            raise HTTPException(status_code=401, detail="Invalid JWT")
        logger.info("User ID extracted successfully")
        return user_id
    except (ValueError, PyJWTError) as e:
        logger.error(f"JWT extraction failed: {str(e)}")
        raise HTTPException(status_code=401, detail="Invalid or missing JWT")

async def get_current_user_from_api_key(x_api_key: str = Header(...)) -> str:
    logger.info("Authenticating user via API key")
    try:
        response = supabase.table("api_keys").select("user_id, api_key_hash") \
            .eq("is_active", True).execute()
        if not response.data:
            logger.warning("No active API keys found")
            raise HTTPException(status_code=401, detail="Invalid or inactive API key")

        for record in response.data:
            if bcrypt.checkpw(x_api_key.encode('utf-8'), record["api_key_hash"].encode('utf-8')):
                user_id = record["user_id"]
                supabase.table("api_keys").update({"last_used": datetime.now(timezone.utc).isoformat()}) \
                    .eq("api_key_hash", record["api_key_hash"]).execute()
                logger.info("API key validated successfully")
                return user_id

        logger.warning("Invalid API key")
        raise HTTPException(status_code=401, detail="Invalid or inactive API key")
    except Exception as e:
        logger.error(f"API key validation failed: {str(e)}")
        raise HTTPException(status_code=500, detail=f"API key validation failed: {str(e)}")

# Authentication Routes
@router.post("/signup", response_model=Token, status_code=status.HTTP_201_CREATED)
@limiter.limit("5/minute")
async def signup(request: Request, user: UserCreate):
    logger.info("User signup initiated")
    return sign_up_user(user.email, user.password)



@router.post("/signin", response_model=Token)
@limiter.limit("10/minute")
async def signin(request: Request, user: UserCreate):
    logger.info("User signin initiated")
    try:
        response = supabase.auth.sign_in_with_password({"email": user.email, "password": user.password})
        if response.user is None:
            logger.warning("Incorrect email or password")
            raise HTTPException(status_code=401, detail="Incorrect email or password")
        logger.info("User signed in successfully")
        return {"access_token": response.session.access_token, "token_type": "bearer"}
    except Exception as e:
        logger.error(f"Authentication failed: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Authentication failed: {str(e)}")

@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("10/minute")
async def logout(request: Request, user_id: str = Depends(get_current_user)):
    logger.info("User logout initiated")
    try:
        await supabase.auth.sign_out()
        logger.info("User logged out successfully")
    except Exception as e:
        logger.error(f"Logout failed: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Logout failed: {str(e)}")

@router.post("/password/reset-request", status_code=status.HTTP_202_ACCEPTED)
@limiter.limit("5/minute")
async def password_reset_request(request: Request, data: PasswordResetRequest):
    logger.info("Password reset request initiated")
    try:
        await supabase.auth.reset_password_for_email(data.email)
        logger.info("Password reset email sent successfully")
        return {"message": "Password reset email sent if the account exists"}
    except Exception as e:
        logger.error(f"Password reset request failed: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Password reset request failed: {str(e)}")

@router.post("/password/reset-confirm", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("5/minute")
async def password_reset_confirm(request: Request, data: PasswordResetConfirm):
    logger.info("Password reset confirmation initiated")
    try:
        await supabase.auth.verify_otp({"token": data.token, "type": "recovery"})
        await supabase.auth.update_user({"password": data.new_password})
        logger.info("Password reset confirmed successfully")
    except Exception as e:
        logger.error(f"Password reset failed: {str(e)}")
        raise HTTPException(status_code=400, detail=f"Password reset failed: {str(e)}")

# User Profile Routes
@router.get("/profile", response_model=UserProfile)
@limiter.limit("10/minute")
async def get_profile(request: Request, user_id: str = Depends(get_current_user)):
    logger.info("Retrieving user profile")
    try:
        user = await supabase.auth.get_user()
        if not user.user:
            logger.warning("User not found")
            raise HTTPException(status_code=404, detail="User not found")
        logger.info("User profile retrieved successfully")
        return {
            "id": user.user.id,
            "email": user.user.email,
            "created_at": user.user.created_at
        }
    except Exception as e:
        logger.error(f"Failed to retrieve profile: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to retrieve profile: {str(e)}")

@router.put("/profile", response_model=UserProfile)
@limiter.limit("5/minute")
async def update_profile(
    request: Request, 
    data: UserUpdate, 
    user_id: str = Depends(get_current_user)
):
    logger.info("Updating user profile")
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
            logger.warning("User not found")
            raise HTTPException(status_code=404, detail="User not found")
        logger.info("User profile updated successfully")
        return {
            "id": user.user.id,
            "email": user.user.email,
            "created_at": user.user.created_at
        }
    except Exception as e:
        logger.error(f"Failed to update profile: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to update profile: {str(e)}")

# API Key Routes
@router.post("/api-keys", response_model=ApiKeyResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit("3/minute")
async def create_api_key(
    request: Request, 
    data: ApiKeyCreate, 
    user_id: str = Depends(get_current_user)
):
    logger.info("Creating API key")
    try:
        key_id = str(uuid.uuid4())
        api_key = secrets.token_urlsafe(32)  # Generate the original API key
        api_key_hash = bcrypt.hashpw(api_key.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
        created_at = datetime.now(timezone.utc)

        response = supabase.table("api_keys").insert({
            "id": key_id,
            "user_id": user_id,
            "api_key_hash": api_key_hash,  # Store only the hash in the database
            "description": data.description,
            "created_at": created_at.isoformat(),
            "is_active": True
        }).execute()

        if not response.data:
            logger.error(f"Failed to create API key: {response}")
            raise HTTPException(status_code=500, detail="Failed to create API key")

        logger.info("API key created successfully")
        return {
            "key_id": key_id,
            "api_key": api_key,  # Return the original API key in the response
            "created_at": created_at.isoformat(),
            "description": data.description
        }
    except Exception as e:
        logger.error(f"Failed to create API key: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to create API key: {str(e)}")

@router.get("/api-keys", response_model=List[ApiKeyListItem])
@limiter.limit("10/minute")
async def list_api_keys(request: Request, user_id: str = Depends(get_current_user)):
    logger.info("Listing API keys for user")
    try:
        response = supabase.table("api_keys").select("id, created_at, description, last_used") \
            .eq("user_id", user_id).eq("is_active", True).execute()

        data = response.data
        if not data:
            logger.warning("No API keys found for the user")
            return []

        logger.info("API keys retrieved successfully")
        return [
            {
                "key_id": key["id"],
                "created_at": key["created_at"],
                "description": key["description"],
                "last_used": key["last_used"]
            } for key in data
        ]
    except Exception as e:
        logger.error(f"Failed to retrieve API keys: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to retrieve API keys: {str(e)}")

@router.post("/api-keys/{key_id}/regenerate", response_model=ApiKeyResponse)
@limiter.limit("3/minute")
async def regenerate_api_key(
    request: Request, 
    key_id: str, 
    user_id: str = Depends(get_current_user)
):
    logger.info("Regenerating API key")
    try:
        response = supabase.table("api_keys").select("*").eq("id", key_id) \
            .eq("user_id", user_id).eq("is_active", True).execute()
        print("resp::::::::",response)
        if not response.data:
            logger.warning("API key not found or access denied")
            raise HTTPException(status_code=404, detail="API key not found or access denied")
        
        api_key = secrets.token_urlsafe(32)
        api_key_hash = bcrypt.hashpw(api_key.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
        created_at = datetime.now(timezone.utc)
        
        supabase.table("api_keys").update({
            "api_key_hash": api_key_hash,
            "created_at": created_at.isoformat()
        }).eq("id", key_id).execute()
        
        logger.info("API key regenerated successfully")
        return {
            "key_id": key_id,
            "api_key": api_key,  # Return plaintext API key only on regeneration
            "created_at": created_at.isoformat(),
            "description": response.data[0].get("description")
        }
    except Exception as e:
        logger.error(f"Failed to regenerate API key: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to regenerate API key: {str(e)}")

@router.delete("/api-keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("5/minute")
async def delete_api_key(
    request: Request, 
    key_id: str, 
    user_id: str = Depends(get_current_user)
):
    logger.info("Deleting API key")
    try:
        response = supabase.table("api_keys").update({"is_active": False}) \
            .eq("id", key_id).eq("user_id", user_id).execute()
        if not response.data:
            logger.warning("API key not found or access denied")
            raise HTTPException(status_code=404, detail="API key not found or access denied")
        logger.info("API key deleted successfully")
    except Exception as e:
        logger.error(f"Failed to delete API key: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to delete API key: {str(e)}")

@router.get("/api-keys/validate", response_model=dict, status_code=status.HTTP_200_OK)
@limiter.limit("8/minute")
async def validate_api_key(request: Request, user_id: str = Depends(get_current_user_from_api_key)):
    logger.info("Validating API key")
    try:
        # Generate a JWT to impersonate the user
        secret_key = os.getenv("JWT_SECRET")
        if not secret_key:
            logger.error("JWT_SECRET is not set")
            raise HTTPException(status_code=500, detail="Server configuration error")

        token = jwt.encode({"sub": user_id}, secret_key, algorithm="HS256")

        # Use the generated JWT to impersonate the user
        supabase.auth.session = {"access_token": token}
        user = supabase.auth.get_user()  # Removed 'await'

        if not user or not user.user:
            logger.warning("User not found")
            raise HTTPException(status_code=404, detail="User not found")

        logger.info("API key validated successfully and user details retrieved")
        return {
            "id": user.user.id,
            "email": user.user.email,
            "created_at": user.user.created_at
        }
    except Exception as e:
        logger.error(f"Failed to retrieve user details: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to retrieve user details: {str(e)}")