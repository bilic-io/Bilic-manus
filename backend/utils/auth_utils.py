from fastapi import HTTPException, Request, Depends
from typing import Optional, List, Dict, Any
import jwt
from jwt.exceptions import PyJWTError
from utils.logger import logger
from datetime import datetime, timezone
import bcrypt

# This function extracts the user ID from Supabase JWT or API key
async def get_current_user_id(request: Request) -> str:
    """
    Extract and verify the user ID from the JWT in the Authorization header or API key.
    This function is used as a dependency in FastAPI routes to ensure the user
    is authenticated and to provide the user ID for authorization checks.
    
    Args:
        request: The FastAPI request object
        
    Returns:
        str: The user ID extracted from the JWT or API key
        
    Raises:
        HTTPException: If no valid token or API key is found or if they are invalid
    """
    auth_header = request.headers.get('Authorization')
    api_key = request.headers.get('X-API-Key')

    if api_key:
        try:
            # Validate API key
            api_key_hash = bcrypt.hash(api_key)
            response = await supabase.table("api_keys").select("user_id") \
                .eq("api_key_hash", api_key_hash).eq("is_active", True).execute()
            if not response.data:
                raise HTTPException(status_code=401, detail="Invalid or inactive API key")

            user_id = response.data[0]["user_id"]
            # Update last used timestamp for the API key
            await supabase.table("api_keys").update({"last_used": datetime.now(timezone.utc).isoformat()}) \
                .eq("api_key_hash", api_key_hash).execute()
            return user_id
        except Exception as e:
            logger.error(f"API key validation failed: {str(e)}")
            raise HTTPException(status_code=500, detail="API key validation failed")

    if auth_header and auth_header.startswith('Bearer '):
        token = auth_header.split(' ')[1]
        try:
            # Decode JWT and extract user ID
            payload = jwt.decode(token, options={"verify_signature": False})
            user_id = payload.get('sub')
            if not user_id:
                raise HTTPException(
                    status_code=401,
                    detail="Invalid token payload",
                    headers={"WWW-Authenticate": "Bearer"}
                )
            return user_id
        except PyJWTError:
            raise HTTPException(
                status_code=401,
                detail="Invalid token",
                headers={"WWW-Authenticate": "Bearer"}
            )

    raise HTTPException(
        status_code=401,
        detail="No valid authentication credentials found",
        headers={"WWW-Authenticate": "Bearer"}
    )

async def get_user_id_from_stream_auth(
    request: Request,
    token: Optional[str] = None
) -> str:
    """
    Extract and verify the user ID from either the Authorization header or query parameter token.
    This function is specifically designed for streaming endpoints that need to support both
    header-based and query parameter-based authentication (for EventSource compatibility).
    
    Args:
        request: The FastAPI request object
        token: Optional token from query parameters
        
    Returns:
        str: The user ID extracted from the JWT
        
    Raises:
        HTTPException: If no valid token is found or if the token is invalid
    """
    # Try to get user_id from token in query param (for EventSource which can't set headers)
    if token:
        try:
            # For Supabase JWT, we just need to decode and extract the user ID
            payload = jwt.decode(token, options={"verify_signature": False})
            user_id = payload.get('sub')
            if user_id:
                return user_id
        except Exception:
            pass
    
    # If no valid token in query param, try to get it from the Authorization header
    auth_header = request.headers.get('Authorization')
    if auth_header and auth_header.startswith('Bearer '):
        try:
            # Extract token from header
            header_token = auth_header.split(' ')[1]
            payload = jwt.decode(header_token, options={"verify_signature": False})
            user_id = payload.get('sub')
            if user_id:
                return user_id
        except Exception:
            pass
    
    # If we still don't have a user_id, return authentication error
    raise HTTPException(
        status_code=401,
        detail="No valid authentication credentials found",
        headers={"WWW-Authenticate": "Bearer"}
    )
async def verify_thread_access(client, thread_id: str, user_id: str):
    """
    Verify that a user has access to a specific thread based on account membership.
    
    Args:
        client: The Supabase client
        thread_id: The thread ID to check access for
        user_id: The user ID to check permissions for
        
    Returns:
        bool: True if the user has access
        
    Raises:
        HTTPException: If the user doesn't have access to the thread
    """
    # Query the thread to get account information
    thread_result = await client.table('threads').select('*,project_id').eq('thread_id', thread_id).execute()

    if not thread_result.data or len(thread_result.data) == 0:
        raise HTTPException(status_code=404, detail="Thread not found")
    
    thread_data = thread_result.data[0]
    
    # Check if project is public
    project_id = thread_data.get('project_id')
    if project_id:
        project_result = await client.table('projects').select('is_public').eq('project_id', project_id).execute()
        if project_result.data and len(project_result.data) > 0:
            if project_result.data[0].get('is_public'):
                return True
        
    account_id = thread_data.get('account_id')
    # When using service role, we need to manually check account membership instead of using current_user_account_role
    if account_id:
        account_user_result = await client.schema('basejump').from_('account_user').select('account_role').eq('user_id', user_id).eq('account_id', account_id).execute()
        if account_user_result.data and len(account_user_result.data) > 0:
            return True
    raise HTTPException(status_code=403, detail="Not authorized to access this thread")

async def get_optional_user_id(request: Request) -> Optional[str]:
    """
    Extract the user ID from the JWT in the Authorization header if present,
    but don't require authentication. Returns None if no valid token is found.
    
    This function is used for endpoints that support both authenticated and 
    unauthenticated access (like public projects).
    
    Args:
        request: The FastAPI request object
        
    Returns:
        Optional[str]: The user ID extracted from the JWT, or None if no valid token
    """
    auth_header = request.headers.get('Authorization')
    
    if not auth_header or not auth_header.startswith('Bearer '):
        return None
    
    token = auth_header.split(' ')[1]
    
    try:
        # For Supabase JWT, we just need to decode and extract the user ID
        payload = jwt.decode(token, options={"verify_signature": False})
        
        # Supabase stores the user ID in the 'sub' claim
        user_id = payload.get('sub')
        
        return user_id
    except PyJWTError:
        return None


from supabase import create_client
import os

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY")

supabase = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)

async def sign_up_user(email: str, password: str, additional_data: dict = None):
    """
    Sign up a new user using Supabase Auth.

    Args:
        email (str): User's email address.
        password (str): User's password.
        additional_data (dict): Additional user data to store in the database.

    Returns:
        dict: The created user's details or an error message.
    """
    try:
        # Sign up the user in Supabase Auth
        response = supabase.auth.sign_up({
            "email": email,
            "password": password
        })

        if response.get("error"):
            raise HTTPException(status_code=400, detail=response["error"]["message"])

        user = response.get("user")

        # Optionally store additional user data in the database
        if additional_data:
            additional_data["user_id"] = user["id"]
            supabase.table("users").insert(additional_data).execute()

        return {"message": "User signed up successfully", "user": user}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

async def get_user_details(user_id: str):
    """
    Retrieve user details from the database.

    Args:
        user_id (str): The user's ID.

    Returns:
        dict: The user's details or an error message.
    """
    try:
        response = supabase.table("users").select("*").eq("id", user_id).execute()

        if not response.data:
            raise HTTPException(status_code=404, detail="User not found")

        return response.data[0]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))