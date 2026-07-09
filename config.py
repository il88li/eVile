import os
import logging
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

class Config:
    SECRET_KEY = os.getenv('SECRET_KEY', 'default-secret-key-change-this')
    
    # كلمة مرور المسؤول الجديدة
    ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD', 'UFOQ_Admin_Secure2026')
    
    OPENROUTER_API_KEY = os.getenv('OPENROUTER_API_KEY')
    OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
    
    DATABASE_URL = os.getenv('DATABASE_URL', 'postgresql://user:pass@localhost:5432/db')
    SQLALCHEMY_DATABASE_URI = DATABASE_URL
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_POOL_SIZE = 20
    SQLALCHEMY_MAX_OVERFLOW = 40
    SQLALCHEMY_POOL_PRE_PING = True

    REDIS_URL = os.getenv('REDIS_URL')
    
    if REDIS_URL:
        CACHE_TYPE = 'RedisCache'
        CACHE_REDIS_URL = REDIS_URL
        CACHE_DEFAULT_TIMEOUT = 300
        SESSION_TYPE = 'redis'
        SESSION_REDIS = REDIS_URL
        SESSION_PERMANENT = False
        SESSION_USE_SIGNER = True
        SESSION_KEY_PREFIX = 'ufoq_session:'
        RATELIMIT_ENABLED = True
        RATELIMIT_STORAGE_URI = REDIS_URL
        RATELIMIT_STRATEGY = 'fixed-window'
        logger.info("✅ Redis configured successfully.")
    else:
        CACHE_TYPE = 'SimpleCache'
        CACHE_DEFAULT_TIMEOUT = 300
        SESSION_TYPE = 'filesystem'
        SESSION_PERMANENT = False
        SESSION_USE_SIGNER = True
        SESSION_KEY_PREFIX = 'ufoq_session:'
        RATELIMIT_ENABLED = True
        RATELIMIT_STORAGE_URI = 'memory://'
        RATELIMIT_STRATEGY = 'fixed-window'
        logger.warning("⚠️ REDIS_URL not set. Falling back to in-memory cache/limiter.")
