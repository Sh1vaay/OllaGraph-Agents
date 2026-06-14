import os
import logging

logger = logging.getLogger("OllaGraph.Observability")

def setup_observability() -> bool:
    """
    Initializes Langfuse tracing by monkey-patching the global `openai` package.
    Returns True if successfully initialized, False otherwise.
    """
    public_key = os.environ.get("LANGFUSE_PUBLIC_KEY")
    secret_key = os.environ.get("LANGFUSE_SECRET_KEY")
    base_url = os.environ.get("LANGFUSE_BASE_URL", "https://cloud.langfuse.com")

    if not public_key or not secret_key:
        logger.info("Langfuse public/secret keys not found in environment. Tracing is disabled.")
        return False

    try:
        # Import langfuse.openai. This globally monkeypatches the 'openai' module.
        # Python caches imported modules globally, so subsequent imports of 'openai'
        # in other files (like pyautogen/AG2) will automatically use this instrumented version.
        from langfuse.openai import openai
        
        # Verify authentication
        from langfuse import Langfuse
        lf_client = Langfuse(
            public_key=public_key,
            secret_key=secret_key,
            host=base_url
        )
        
        if lf_client.auth_check():
            logger.info(f"Langfuse tracing successfully authenticated and initialized. Base URL: {base_url}")
            return True
        else:
            logger.warning("Langfuse authentication check failed. Please verify your public/secret keys.")
            return False
            
    except ImportError:
        logger.warning("langfuse library is not installed. Run 'pip install langfuse' to enable tracing.")
        return False
    except Exception as e:
        logger.error(f"Unexpected error while initializing Langfuse tracing: {e}")
        return False
