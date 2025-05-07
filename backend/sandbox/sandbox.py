import os
import uuid
from datetime import datetime, timezone, timedelta

from daytona_sdk import Daytona, DaytonaConfig, CreateSandboxParams, Sandbox, SessionExecuteRequest
from daytona_api_client.models.workspace_state import WorkspaceState
from dotenv import load_dotenv

from agentpress.tool import Tool
from utils.logger import logger
from utils.files_utils import clean_path
from services.supabase import DBConnection

load_dotenv()

logger.debug("Initializing Daytona sandbox configuration")
config = DaytonaConfig(
    api_key=os.getenv("DAYTONA_API_KEY"),
    server_url=os.getenv("DAYTONA_SERVER_URL"),
    target=os.getenv("DAYTONA_TARGET")
)

if config.api_key:
    logger.debug("Daytona API key configured successfully")
else:
    logger.warning("No Daytona API key found in environment variables")

if config.server_url:
    logger.debug(f"Daytona server URL set to: {config.server_url}")
else:
    logger.warning("No Daytona server URL found in environment variables")

if config.target:
    logger.debug(f"Daytona target set to: {config.target}")
else:
    logger.warning("No Daytona target found in environment variables")

daytona = Daytona(config)
logger.debug("Daytona client initialized")

async def cleanup_inactive_user_sandboxes(db: DBConnection, max_inactive_days: int = 7):
    """Clean up sandboxes for users who haven't been active for max_inactive_days."""
    try:
        client = await db.client
        cutoff_time = datetime.now(timezone.utc) - timedelta(days=max_inactive_days)
        
        # Find users who haven't been active since cutoff_time
        result = await client.table('user_sandboxes').select('user_id, sandbox_id, last_active_at').execute()
        
        for user_sandbox in result.data:
            if not user_sandbox.get('sandbox_id'):
                continue
                
            # Check if the user has been inactive
            if datetime.fromisoformat(user_sandbox.get('last_active_at', '')).replace(tzinfo=timezone.utc) < cutoff_time:
                sandbox_id = user_sandbox['sandbox_id']
                try:
                    # Delete the sandbox
                    sandbox = daytona.get_current_sandbox(sandbox_id)
                    daytona.delete(sandbox)
                    logger.info(f"Deleted inactive sandbox {sandbox_id} for user {user_sandbox['user_id']}")
                    
                    # Remove the sandbox record
                    await client.table('user_sandboxes').delete().eq('user_id', user_sandbox['user_id']).execute()
                except Exception as e:
                    logger.error(f"Error deleting inactive sandbox {sandbox_id}: {str(e)}")
    except Exception as e:
        logger.error(f"Error in cleanup_inactive_user_sandboxes: {str(e)}")

async def get_or_start_sandbox(sandbox_id: str):
    """Retrieve a sandbox by ID, check its state, and start it if needed."""
    
    logger.info(f"Getting or starting sandbox with ID: {sandbox_id}")
    
    try:
        sandbox = daytona.get_current_sandbox(sandbox_id)
        
        # Check if sandbox needs to be started
        if sandbox.instance.state == WorkspaceState.ARCHIVED or sandbox.instance.state == WorkspaceState.STOPPED:
            logger.info(f"Sandbox is in {sandbox.instance.state} state. Starting...")
            try:
                daytona.start(sandbox)
                # Wait a moment for the sandbox to initialize
                # sleep(5)
                # Refresh sandbox state after starting
                sandbox = daytona.get_current_sandbox(sandbox_id)
                
                # Start supervisord in a session when restarting
                start_supervisord_session(sandbox)
            except Exception as e:
                logger.error(f"Error starting sandbox: {e}")
                raise e
        
        logger.info(f"Sandbox {sandbox_id} is ready")
        return sandbox
        
    except Exception as e:
        logger.error(f"Error retrieving or starting sandbox: {str(e)}")
        raise e

def start_supervisord_session(sandbox: Sandbox):
    """Start supervisord in a session."""
    session_id = "supervisord-session"
    try:
        logger.info(f"Creating session {session_id} for supervisord")
        sandbox.process.create_session(session_id)
        
        # Execute supervisord command
        sandbox.process.execute_session_command(session_id, SessionExecuteRequest(
            command="exec /usr/bin/supervisord -n -c /etc/supervisor/conf.d/supervisord.conf",
            var_async=True
        ))
        logger.info(f"Supervisord started in session {session_id}")
    except Exception as e:
        logger.error(f"Error starting supervisord session: {str(e)}")
        raise e

def create_sandbox(password: str, db: DBConnection = None):
    """Create a new sandbox with all required services configured and running."""
    
    logger.info("Creating new Daytona sandbox environment")
    
    # Clean up old sandboxes before creating a new one
    if db:
        import asyncio
        logger.info("Cleaning old sandboxes ======== Checking 1 , 2 , 3 ......")
        asyncio.create_task(cleanup_inactive_user_sandboxes(db))
    
    logger.debug("Configuring sandbox with browser-use image and environment variables")
        
    sandbox = daytona.create(CreateSandboxParams(
        image="adamcohenhillel/kortix-suna:0.0.20",
        public=True,
        env_vars={
            "CHROME_PERSISTENT_SESSION": "true",
            "RESOLUTION": "1024x768x24",
            "RESOLUTION_WIDTH": "1024",
            "RESOLUTION_HEIGHT": "768",
            "VNC_PASSWORD": password,
            "ANONYMIZED_TELEMETRY": "false",
            "CHROME_PATH": "",
            "CHROME_USER_DATA": "",
            "CHROME_DEBUGGING_PORT": "9222",
            "CHROME_DEBUGGING_HOST": "localhost",
            "CHROME_CDP": ""
        },
        ports=[
            6080,  # noVNC web interface
            5900,  # VNC port
            5901,  # VNC port
            9222,  # Chrome remote debugging port
            8080,   # HTTP website port
            8002,  # The browser api port
        ],
        resources={
            "cpu": 2,
            "memory": 4,
            "disk": 5,
        }
    ))
    logger.info(f"Sandbox created with ID: {sandbox.id}")
    
    # Start supervisord in a session for new sandbox
    start_supervisord_session(sandbox)
    
    logger.info(f"Sandbox environment successfully initialized")
    return sandbox


class SandboxToolsBase(Tool):
    """Tool for executing tasks in a Daytona sandbox with browser-use capabilities."""
    
    # Class variable to track if sandbox URLs have been printed
    _urls_printed = False
    
    def __init__(self, sandbox: Sandbox):
        super().__init__()
        self.sandbox = sandbox
        self.daytona = daytona
        self.workspace_path = "/workspace"

        self.sandbox_id = sandbox.id
        # logger.info(f"Initializing SandboxToolsBase with sandbox ID: {sandbox_id}")
        
        try:
            logger.debug(f"Retrieving sandbox with ID: {self.sandbox_id}")
            self.sandbox = self.daytona.get_current_sandbox(self.sandbox_id)
            # logger.info(f"Successfully retrieved sandbox: {self.sandbox.id}")
        except Exception as e:
            logger.error(f"Error retrieving sandbox: {str(e)}", exc_info=True)
            raise e

        # Get preview links
        vnc_link = self.sandbox.get_preview_link(6080)
        website_link = self.sandbox.get_preview_link(8080)
        
        # Extract the actual URLs from the preview link objects
        vnc_url = vnc_link.url if hasattr(vnc_link, 'url') else str(vnc_link)
        website_url = website_link.url if hasattr(website_link, 'url') else str(website_link)
        
        # Log the actual URLs
        logger.info(f"Sandbox VNC URL: {vnc_url}")
        logger.info(f"Sandbox Website URL: {website_url}")
        
        if not SandboxToolsBase._urls_printed:
            print("\033[95m***")
            print(vnc_url)
            print(website_url)
            print("***\033[0m")
            SandboxToolsBase._urls_printed = True

    def clean_path(self, path: str) -> str:
        cleaned_path = clean_path(path, self.workspace_path)
        logger.debug(f"Cleaned path: {path} -> {cleaned_path}")
        return cleaned_path

async def get_user_sandbox(db: DBConnection, user_id: str):
    """Get or create a sandbox for a specific user."""
    client = await db.client
    
    # Check if user already has a sandbox
    result = await client.table('user_sandboxes').select('*').eq('user_id', user_id).execute()
    
    if result.data and len(result.data) > 0 and result.data[0].get('sandbox_id'):
        # User has a sandbox, get it
        sandbox_id = result.data[0]['sandbox_id']
        logger.info(f"Found existing sandbox {sandbox_id} for user {user_id}")
        
        try:
            # Try to get the sandbox
            sandbox = await get_or_start_sandbox(sandbox_id)
            
            # Update last active timestamp
            await client.table('user_sandboxes').update({
                'last_active_at': datetime.now(timezone.utc).isoformat()
            }).eq('user_id', user_id).execute()
            
            return sandbox, result.data[0]['sandbox_pass']
        except Exception as e:
            logger.error(f"Error retrieving sandbox for user {user_id}: {str(e)}")
            # If sandbox retrieval fails, we'll create a new one below
    
    # Create a new sandbox for this user
    sandbox_pass = str(uuid.uuid4())
    sandbox = create_sandbox(sandbox_pass, db)
    logger.info(f"Created new sandbox {sandbox.id} for user {user_id}")
    
    # Store sandbox info with user
    await client.table('user_sandboxes').upsert({
        'user_id': user_id,
        'sandbox_id': sandbox.id,
        'sandbox_pass': sandbox_pass,
        'last_active_at': datetime.now(timezone.utc).isoformat()
    }).execute()
    
    return sandbox, sandbox_pass

async def update_user_sandbox_activity(db: DBConnection, user_id: str):
    """Update the last_active_at timestamp for a user's sandbox."""
    client = await db.client
    await client.table('user_sandboxes').update({
        'last_active_at': datetime.now(timezone.utc).isoformat()
    }).eq('user_id', user_id).execute()
