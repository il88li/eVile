import os
from dotenv import load_dotenv
import logging

load_dotenv()
logger = logging.getLogger(__name__)

class Config:
    SECRET_KEY = os.getenv('SECRET_KEY', 'default-secret-key-change-this')
    ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD', 'secure_admin_pass')
    OPENROUTER_API_KEY = os.getenv('OPENROUTER_API_KEY')
    OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
    
    # قاعدة البيانات
    SQLALCHEMY_DATABASE_URI = os.getenv('DATABASE_URL', 'postgresql://user:pass@localhost:5432/db')
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_POOL_SIZE = 20
    SQLALCHEMY_MAX_OVERFLOW = 40
    SQLALCHEMY_POOL_PRE_PING = True

    # إعدادات Redis (تمت إضافة مرونة)
    REDIS_URL = os.getenv('REDIS_URL')
    
    if REDIS_URL:
        # إعدادات الأداء العالي (مع Redis)
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
        logger.info("✅ Redis configured successfully using REDIS_URL.")
    else:
        # حل بديل آمن (يعمل فوراً بدون Redis، لكنه لا يتوسع مع عدة عمال)
        CACHE_TYPE = 'SimpleCache'
        CACHE_DEFAULT_TIMEOUT = 300

        SESSION_TYPE = 'filesystem'
        SESSION_PERMANENT = False
        SESSION_USE_SIGNER = True
        SESSION_KEY_PREFIX = 'ufoq_session:'

        RATELIMIT_ENABLED = True
        RATELIMIT_STORAGE_URI = 'memory://'
        RATELIMIT_STRATEGY = 'fixed-window'
        logger.warning("⚠️ REDIS_URL not set. Falling back to in-memory cache/limiter. Session persistence and scaling will NOT work properly with multiple workers.")
