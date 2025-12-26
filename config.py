"""
Configuration management for Schematic Extraction MVP.
Loads settings from environment variables with fail-fast validation.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env file
load_dotenv()

# Base directories
BASE_DIR = Path(__file__).parent.absolute()
UPLOADS_DIR = BASE_DIR / "uploads"
DATABASE_PATH = BASE_DIR / "schematic_analysis.db"

# Ensure uploads directory exists
UPLOADS_DIR.mkdir(exist_ok=True)


class Config:
    """Application configuration with fail-fast validation."""
    
    # Flask settings
    SECRET_KEY = os.getenv("SECRET_KEY", os.urandom(24).hex())
    DEBUG = os.getenv("FLASK_DEBUG", "false").lower() == "true"
    
    # Database
    SQLALCHEMY_DATABASE_URI = f"sqlite:///{DATABASE_PATH}"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    
    # Gemini 3 API Configuration
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
    GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3-pro-preview")
    GEMINI_FLASH_MODEL = os.getenv("GEMINI_FLASH_MODEL", "gemini-3-flash-preview")
    GEMINI_TEMPERATURE = float(os.getenv("GEMINI_TEMPERATURE", "0.1"))
    GEMINI_THINKING_LEVEL = os.getenv("GEMINI_THINKING_LEVEL", "medium")  # low/medium/high
    GEMINI_MEDIA_RESOLUTION = os.getenv("GEMINI_MEDIA_RESOLUTION", "high")  # low/medium/high
    GEMINI_TIMEOUT = int(os.getenv("GEMINI_TIMEOUT", "120"))  # seconds
    GEMINI_MAX_RETRIES = int(os.getenv("GEMINI_MAX_RETRIES", "3"))
    
    # File upload settings
    MAX_CONTENT_LENGTH = int(os.getenv("MAX_FILE_SIZE_MB", "100")) * 1024 * 1024  # 100MB default
    ALLOWED_EXTENSIONS = {"pdf"}
    UPLOADS_DIR = UPLOADS_DIR
    
    # Extraction settings (MVP - pages 7, 8, 9)
    MVP_PDF_PAGES = [6, 7, 8]  # 0-based indices for PDF pages 7, 8, 9
    DEFAULT_CONTEXT_PAGES = [1, 2]  # 0-based indices for PDF pages 2, 3 (instructions, legend)
    
    # Validation thresholds
    VALIDATION_COMPONENT_PASS = float(os.getenv("VALIDATION_COMPONENT_PASS", "0.95"))
    VALIDATION_COMPONENT_WARNING = float(os.getenv("VALIDATION_COMPONENT_WARNING", "0.80"))
    VALIDATION_CONNECTION_PASS = float(os.getenv("VALIDATION_CONNECTION_PASS", "0.90"))
    VALIDATION_CONNECTION_WARNING = float(os.getenv("VALIDATION_CONNECTION_WARNING", "0.70"))
    VALIDATION_WIRE_LABEL_PASS = float(os.getenv("VALIDATION_WIRE_LABEL_PASS", "0.90"))
    VALIDATION_WIRE_LABEL_WARNING = float(os.getenv("VALIDATION_WIRE_LABEL_WARNING", "0.75"))
    VALIDATION_COORD_ERROR_PASS = int(os.getenv("VALIDATION_COORD_ERROR_PASS", "5"))  # pixels
    VALIDATION_COORD_ERROR_WARNING = int(os.getenv("VALIDATION_COORD_ERROR_WARNING", "15"))
    VALIDATION_SPOT_CHECK_PERCENT = float(os.getenv("VALIDATION_SPOT_CHECK_PERCENT", "0.10"))
    
    # Retry/backoff settings
    RETRY_MAX_ATTEMPTS = int(os.getenv("RETRY_MAX_ATTEMPTS", "3"))
    RETRY_BASE_DELAY = float(os.getenv("RETRY_BASE_DELAY", "1.0"))  # seconds
    RETRY_MAX_DELAY = float(os.getenv("RETRY_MAX_DELAY", "30.0"))  # seconds
    
    # Security
    MAX_CONCURRENT_EXTRACTIONS = int(os.getenv("MAX_CONCURRENT_EXTRACTIONS", "3"))
    SESSION_TIMEOUT_MINUTES = int(os.getenv("SESSION_TIMEOUT_MINUTES", "30"))
    
    @classmethod
    def validate(cls):
        """Validate required configuration. Fail-fast if missing."""
        errors = []
        
        if not cls.GEMINI_API_KEY:
            errors.append("GEMINI_API_KEY is required. Set it in .env file.")
        
        if cls.GEMINI_THINKING_LEVEL not in ("low", "medium", "high"):
            errors.append(f"GEMINI_THINKING_LEVEL must be low/medium/high, got: {cls.GEMINI_THINKING_LEVEL}")
        
        if cls.GEMINI_MEDIA_RESOLUTION not in ("low", "medium", "high"):
            errors.append(f"GEMINI_MEDIA_RESOLUTION must be low/medium/high, got: {cls.GEMINI_MEDIA_RESOLUTION}")
        
        if errors:
            raise ValueError("Configuration errors:\n" + "\n".join(f"  - {e}" for e in errors))
        
        return True


# Validate on import
Config.validate()

