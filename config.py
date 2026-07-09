import os
from dotenv import load_dotenv

load_dotenv()

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

    # Redis للتخزين المؤقت والجلسات
    REDIS_URL = os.getenv('REDIS_URL', 'redis://localhost:6379/0')
    CACHE_TYPE = 'RedisCache'
    CACHE_REDIS_URL = REDIS_URL
    CACHE_DEFAULT_TIMEOUT = 300

    SESSION_TYPE = 'redis'
    SESSION_REDIS = REDIS_URL
    SESSION_PERMANENT = False
    SESSION_USE_SIGNER = True
    SESSION_KEY_PREFIX = 'ufoq_session:'

    # Rate Limiting (تم إصلاح الاستراتيجية)
    RATELIMIT_ENABLED = True
    RATELIMIT_STORAGE_URI = REDIS_URL
    RATELIMIT_STRATEGY = 'fixed-window'  # <--- تم التغيير هنا
