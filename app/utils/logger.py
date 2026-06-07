import os
import logging
from app.config.settings import settings

def setup_logging():
    # Auto-create logs directory if it doesn't exist
    os.makedirs("logs", exist_ok=True)
    
    #logging.FileHandler("logs/app.log")
    log_level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)
    logging.basicConfig(
        level=log_level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler("logs/app.log"),
            logging.StreamHandler()
        ]
    )
    return logging.getLogger(__name__)

logger = setup_logging()
