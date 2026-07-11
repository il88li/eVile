import os
import logging
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

class Config:
    SECRET_KEY = os.getenv('SECRET_KEY')
    if not SECRET_KEY:
        raise ValueError("SECRET_KEY environment variable is required!")

    # كلمة مرور المسؤول - سهلة للتذكر
    ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD', 'admin123')

    OPENROUTER_API_KEY = os.getenv('OPENROUTER_API_KEY')
    if not OPENROUTER_API_KEY:
        logger.warning("⚠️ OPENROUTER_API_KEY not set! AI features will not work.")

    OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

    DATABASE_URL = os.getenv('DATABASE_URL', 'postgresql://user:pass@localhost:5432/db')
    SQLALCHEMY_DATABASE_URI = DATABASE_URL
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_POOL_SIZE = 20
    SQLALCHEMY_MAX_OVERFLOW = 40
    SQLALCHEMY_POOL_PRE_PING = True

    REDIS_URL = os.getenv('REDIS_URL')

    # ============================================================
    # SESSION CONFIGURATION - SQLite backed for persistence
    # ============================================================
    # Use Flask-Session with SQLAlchemy backend for persistent sessions
    SESSION_TYPE = 'sqlalchemy'
    SESSION_SQLALCHEMY_TABLE = 'flask_sessions'
    SESSION_PERMANENT = True
    SESSION_USE_SIGNER = True
    SESSION_KEY_PREFIX = 'ufoq_session:'
    PERMANENT_SESSION_LIFETIME = 2592000  # 30 days in seconds

    if REDIS_URL:
        CACHE_TYPE = 'RedisCache'
        CACHE_REDIS_URL = REDIS_URL
        CACHE_DEFAULT_TIMEOUT = 300
        RATELIMIT_ENABLED = True
        RATELIMIT_STORAGE_URI = REDIS_URL
        RATELIMIT_STRATEGY = 'fixed-window'
        logger.info("✅ Redis configured successfully for cache/limiter.")
    else:
        CACHE_TYPE = 'SimpleCache'
        CACHE_DEFAULT_TIMEOUT = 300
        RATELIMIT_ENABLED = True
        RATELIMIT_STORAGE_URI = 'memory://'
        RATELIMIT_STRATEGY = 'fixed-window'
        logger.warning("⚠️ REDIS_URL not set. Falling back to in-memory cache/limiter.")
