import os
import sys
from pydantic_settings import BaseSettings, SettingsConfigDict
from dotenv import load_dotenv

def get_base_path():
    """Get the absolute path to the directory containing the executable or source script."""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(os.path.abspath(sys.executable))
    else:
        # Settings is located in app/config/settings.py, so two levels up is the root
        return os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))

def get_app_path():
    """Get the absolute path to the temporary directory containing bundled resources."""
    if getattr(sys, 'frozen', False):
        return getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(sys.executable)))
    else:
        return get_base_path()

# Find and load env variables explicitly from potential absolute paths
def find_env_file():
    paths_to_check = [
        os.path.join(get_base_path(), '.env'),
        os.path.abspath(os.path.join(get_base_path(), '..', '.env')),
        os.path.abspath(os.path.join(get_base_path(), '../..', '.env')),
        os.path.join(os.getcwd(), '.env')
    ]
    for path in paths_to_check:
        if os.path.exists(path):
            return path
    return None

env_path = find_env_file()
if env_path:
    load_dotenv(env_path)

class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra='ignore')

    GOOGLE_CLIENT_ID: str
    GOOGLE_CLIENT_SECRET: str
    GOOGLE_REDIRECT_URI: str = "http://localhost:8080/"

    WHATSAPP_SESSION_PATH: str = "./whatsapp_session"
    WHATSAPP_GROUP_INVITE_LINK: str
    
    DEFAULT_COUNTRY: str = "US"
    LOG_LEVEL: str = "INFO"
    FLYER_PATH: str = "./data/registration_flyer.png"

    @property
    def BASE_PATH(self) -> str:
        if env_path:
            return os.path.dirname(env_path)
        return get_base_path()

    @property
    def APP_PATH(self) -> str:
        return get_app_path()

    @property
    def RESOLVED_WHATSAPP_SESSION_PATH(self) -> str:
        if os.path.isabs(self.WHATSAPP_SESSION_PATH):
            return self.WHATSAPP_SESSION_PATH
        return os.path.abspath(os.path.join(self.BASE_PATH, self.WHATSAPP_SESSION_PATH))

    @property
    def RESOLVED_FLYER_PATH(self) -> str:
        if os.path.isabs(self.FLYER_PATH):
            path = self.FLYER_PATH
        else:
            path = os.path.abspath(os.path.join(self.BASE_PATH, self.FLYER_PATH))

        if os.path.exists(path):
            return path

        # Try alternative image extensions in case of minor naming mismatches
        base, ext = os.path.splitext(path)
        for alt_ext in ['.jpg', '.jpeg', '.png', '.gif']:
            alt_path = base + alt_ext
            if os.path.exists(alt_path):
                return alt_path

        return path

settings = Settings()
