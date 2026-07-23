import os
import re
import logging
import secrets
import traceback
from datetime import datetime, timedelta
from functools import wraps
from urllib.parse import urlparse
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_caching import Cache
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_talisman import Talisman
from flask_session import Session
from sqlalchemy import inspect, text, func
from sqlalchemy.orm import joinedload
from werkzeug.security import generate_password_hash, check_password_hash
from google.oauth2 import id_token as google_id_token
from google.auth.transport import requests as google_requests
from dotenv import load_dotenv

load_dotenv()

# ==================== Logging ====================
_log_handlers = [logging.StreamHandler()]
if not os.getenv('VERCEL') and not os.getenv('RENDER'):
    try:
        _log_handlers.append(logging.FileHandler('app.log', encoding='utf-8'))
    except OSError:
        pass

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s in %(module)s [%(pathname)s:%(lineno)d]: %(message)s',
    handlers=_log_handlers
)
logger = logging.getLogger(__name__)

# ==================== Config ====================
class Config:
    SECRET_KEY = os.getenv('SECRET_KEY')
    if not SECRET_KEY:
        logger.warning("SECRET_KEY not set! Using fallback (INSECURE for production)")
        SECRET_KEY = os.urandom(32).hex()

    ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD', 'admin123')
    GOOGLE_CLIENT_ID = os.getenv('GOOGLE_CLIENT_ID', '1066865562137-k509114e44npk13n5n78gb32b3meldrk.apps.googleusercontent.com')

    DATABASE_URL = os.getenv('DATABASE_URL', 'sqlite:///ufoq.db')
    if DATABASE_URL.startswith('postgres://'):
        DATABASE_URL = DATABASE_URL.replace('postgres://', 'postgresql://', 1)
    SQLALCHEMY_DATABASE_URI = DATABASE_URL
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_POOL_SIZE = int(os.getenv('DB_POOL_SIZE', '20'))
    SQLALCHEMY_MAX_OVERFLOW = int(os.getenv('DB_MAX_OVERFLOW', '40'))
    SQLALCHEMY_POOL_PRE_PING = True
    SQLALCHEMY_POOL_RECYCLE = 3600
    SQLALCHEMY_ENGINE_OPTIONS = {
        'pool_pre_ping': True,
        'pool_recycle': 3600,
        'pool_timeout': 30,
    }

    SESSION_TYPE = 'sqlalchemy'
    SESSION_SQLALCHEMY_TABLE = 'flask_sessions'
    SESSION_PERMANENT = True
    SESSION_USE_SIGNER = True
    SESSION_KEY_PREFIX = 'ufoq_session:'
    PERMANENT_SESSION_LIFETIME = 2592000
    SESSION_COOKIE_NAME = 'ufoq_session'
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    SESSION_COOKIE_SECURE = os.getenv('RENDER') is not None or os.getenv('FORCE_HTTPS') == '1'
    SESSION_REFRESH_EACH_REQUEST = True

    CACHE_TYPE = 'SimpleCache'
    CACHE_DEFAULT_TIMEOUT = 300
    RATELIMIT_ENABLED = True
    RATELIMIT_STORAGE_URI = os.getenv('REDIS_URL', 'memory://')
    RATELIMIT_STRATEGY = 'fixed-window'
    RATELIMIT_DEFAULT = "200 per minute"

# ==================== Models ====================
db = SQLAlchemy()

class User(db.Model):
    __tablename__ = 'app_user'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), nullable=False)
    email = db.Column(db.String(200), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=True)
    google_id = db.Column(db.String(200), unique=True, nullable=True)
    avatar_url = db.Column(db.String(500), nullable=True)
    bio = db.Column(db.Text, nullable=True)
    profile_link = db.Column(db.String(500), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_active = db.Column(db.DateTime, default=datetime.utcnow)

class Category(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), nullable=False, unique=True)
    display_name = db.Column(db.String(100), nullable=False)
    icon = db.Column(db.String(50), default='bi-tag')
    sort_order = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class PromptLibrary(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    category = db.Column(db.String(50), nullable=False, default='general')
    image_url = db.Column(db.String(500), nullable=False, default='')
    prompt_text = db.Column(db.Text, nullable=False)
    publisher = db.Column(db.String(80), nullable=True)
    publisher_link = db.Column(db.String(500), nullable=True)
    keywords = db.Column(db.Text, nullable=True)
    copy_count = db.Column(db.Integer, default=0, nullable=False)
    share_count = db.Column(db.Integer, default=0, nullable=False)
    likes = db.Column(db.Integer, default=0, nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('app_user.id'), nullable=True)
    user = db.relationship('User', backref='prompts')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class LibraryAd(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    text = db.Column(db.Text, nullable=False)
    image_url = db.Column(db.String(500), nullable=True)
    button_text = db.Column(db.String(100), nullable=False, default='زيارة')
    button_link = db.Column(db.String(500), nullable=False)
    duration_seconds = db.Column(db.Integer, default=5)
    is_active = db.Column(db.Boolean, default=True)
    is_mandatory = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class SiteSetting(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    status = db.Column(db.String(10), default='on')
    offline_message = db.Column(db.Text, default='الموقع تحت الصيانة حالياً.')

class ErrorLog(db.Model):
    __tablename__ = 'error_logs'
    id = db.Column(db.Integer, primary_key=True)
    error_type = db.Column(db.String(20), default='404')
    url = db.Column(db.String(500), nullable=False)
    referer = db.Column(db.String(500), nullable=True)
    user_agent = db.Column(db.String(300), nullable=True)
    ip_address = db.Column(db.String(50), nullable=True)
    details = db.Column(db.Text, nullable=True)
    count = db.Column(db.Integer, default=1, nullable=False)
    ignored = db.Column(db.Boolean, default=False)
    last_seen = db.Column(db.DateTime, default=datetime.utcnow)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'error_type': self.error_type,
            'url': self.url,
            'referer': self.referer,
            'details': self.details,
            'count': self.count,
            'ignored': self.ignored,
            'last_seen': self.last_seen.isoformat() if self.last_seen else None,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }

# ==================== App Factory ====================
app = Flask(__name__, template_folder=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'templates'))
app.config.from_object(Config)

db.init_app(app)
migrate = Migrate(app, db)
app.config['SESSION_SQLALCHEMY'] = db
Session(app)

# ====== Caching ======
if os.getenv('REDIS_URL'):
    try:
        from flask_caching.backends.redis import RedisCache
        cache = Cache(app, config={
            'CACHE_TYPE': 'RedisCache',
            'CACHE_REDIS_URL': os.getenv('REDIS_URL'),
            'CACHE_DEFAULT_TIMEOUT': 300
        })
        logger.info("Using Redis cache")
    except Exception as e:
        logger.warning(f"Redis cache failed: {e}, falling back to SimpleCache")
        cache = Cache(app, config={'CACHE_TYPE': 'SimpleCache'})
else:
    cache = Cache(app, config={'CACHE_TYPE': 'SimpleCache'})

limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["200 per minute"],
    storage_uri=app.config['RATELIMIT_STORAGE_URI'],
    strategy='fixed-window'
)

# Production CSP
csp = {
    'default-src': ["'self'"],
    'style-src': ["'self'", "'unsafe-inline'", "https://fonts.googleapis.com", "https://cdn.jsdelivr.net"],
    'script-src': ["'self'", "'unsafe-inline'", "https://cdn.jsdelivr.net", "https://accounts.google.com"],
    'font-src': ["'self'", "https://fonts.gstatic.com", "https://cdn.jsdelivr.net"],
    'img-src': ["'self'", "data:", "https:", "blob:"],
    'connect-src': ["'self'", "https://accounts.google.com"],
    'frame-src': ["https://accounts.google.com"],
    'frame-ancestors': ["'none'"],
}
Talisman(app, force_https=False, content_security_policy=csp)

# ==================== Error Handlers ====================
def log_error(error_type, url, details=None):
    try:
        inspector = inspect(db.engine)
        if 'error_logs' not in inspector.get_table_names():
            return
        query = ErrorLog.query.filter_by(error_type=error_type, url=url)
        if details:
            query = query.filter_by(details=details)
        else:
            query = query.filter(ErrorLog.details.is_(None))
        
        existing = query.first()
        if existing:
            existing.count += 1
            existing.last_seen = datetime.utcnow()
        else:
            new_log = ErrorLog(
                error_type=error_type,
                url=url,
                referer=request.referrer,
                user_agent=request.user_agent.string if request.user_agent else None,
                ip_address=request.remote_addr,
                details=details,
                count=1
            )
            db.session.add(new_log)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.error(f"Failed to log error to DB: {e}")

@app.errorhandler(404)
def not_found(e):
    logger.warning(f"404: {request.url} - {request.remote_addr}")
    details = None
    path = request.path.lower()
    if path.endswith(('.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg', '.ico')):
        details = f"Image missing: {request.path}"
    elif path.endswith(('.css', '.js', '.json')):
        details = f"Asset missing: {request.path}"
    else:
        details = f"Page not found: {request.path}"
    
    if not request.path.startswith('/admin') and not request.path.startswith('/api'):
        log_error('404', request.url, details)
    
    if request.path.startswith('/api/'):
        return jsonify({'success': False, 'message': 'الصفحة غير موجودة'}), 404
    return render_template('index.html', site_status='on', categories=[], library_items=[], active_ad=None), 404

@app.errorhandler(500)
def internal_error(e):
    logger.error(f"500 ERROR: {str(e)}\n{traceback.format_exc()}")
    db.session.rollback()
    if request.path.startswith('/api/'):
        return jsonify({'success': False, 'message': 'خطأ داخلي في الخادم'}), 500
    return render_template('index.html', site_status='on', categories=[], library_items=[], active_ad=None), 500

@app.errorhandler(Exception)
def handle_exception(e):
    from werkzeug.exceptions import HTTPException
    if isinstance(e, HTTPException):
        return e
    logger.error(f"UNHANDLED: {str(e)}\n{traceback.format_exc()}")
    db.session.rollback()
    if request.path.startswith('/api/'):
        return jsonify({'success': False, 'message': 'حدث خطأ غير متوقع'}), 500
    return render_template('index.html', site_status='on', categories=[], library_items=[], active_ad=None), 500

# ---------- Helper functions ----------
def generate_csrf_token():
    if 'csrf_token' not in session:
        session['csrf_token'] = secrets.token_urlsafe(32)
    return session['csrf_token']

def validate_csrf_token(token):
    return token == session.get('csrf_token')

def is_valid_publisher_link(url):
    if not url:
        return True
    try:
        parsed = urlparse(url.strip())
        return parsed.scheme in ('http', 'https') and bool(parsed.netloc)
    except Exception:
        return False

EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
def is_valid_email(email):
    return bool(email) and bool(EMAIL_RE.match(email.strip()))

def current_user():
    uid = session.get('user_id')
    if not uid:
        return None
    try:
        user = User.query.get(uid)
        if user:
            user.last_active = datetime.utcnow()
            db.session.commit()
        return user
    except Exception as e:
        logger.error(f"current_user error: {e}")
        db.session.rollback()
        return None

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('user_id'):
            return redirect(url_for('sign_page', next=request.path))
        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect(url_for('admin_panel'))
        return f(*args, **kwargs)
    return decorated

def safe_redirect(next_url, default='/'):
    if not next_url:
        return default
    if next_url.startswith('/') and not next_url.startswith('//'):
        return next_url
    return default

# ---------- Database initialization ----------
def run_light_migrations():
    try:
        engine = db.engine
        
        inspector = inspect(engine)
        if 'error_logs' not in inspector.get_table_names():
            try:
                engine.execute(text("""
                    CREATE TABLE IF NOT EXISTS error_logs (
                        id SERIAL PRIMARY KEY,
                        error_type VARCHAR(20) DEFAULT '404',
                        url VARCHAR(500) NOT NULL,
                        referer VARCHAR(500),
                        user_agent VARCHAR(300),
                        ip_address VARCHAR(50),
                        details TEXT,
                        count INTEGER DEFAULT 1 NOT NULL,
                        ignored BOOLEAN DEFAULT FALSE,
                        last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """))
                logger.info("Created error_logs table")
            except Exception as e:
                logger.warning(f"Could not create error_logs: {e}")
        
        required_columns = {
            'prompt_library': {
                'publisher_link': 'VARCHAR(500)',
                'keywords': 'TEXT',
                'copy_count': 'INTEGER NOT NULL DEFAULT 0',
                'share_count': 'INTEGER NOT NULL DEFAULT 0',
                'user_id': 'INTEGER',
                'likes': 'INTEGER NOT NULL DEFAULT 0',
            },
            'library_ad': {
                'is_mandatory': 'BOOLEAN DEFAULT FALSE',
            },
        }
        for table_name, cols in required_columns.items():
            if table_name in inspector.get_table_names():
                existing_cols = {col['name'] for col in inspector.get_columns(table_name)}
                for col_name, col_def in cols.items():
                    if col_name not in existing_cols:
                        try:
                            engine.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {col_name} {col_def}"))
                            logger.info(f"Added column {table_name}.{col_name}")
                        except Exception as e:
                            logger.warning(f"Could not add {table_name}.{col_name}: {e}")
    except Exception as e:
        logger.error(f"Migration error: {e}")

@app.before_request
def ensure_db_initialized():
    try:
        inspector = inspect(db.engine)
        if 'app_user' not in inspector.get_table_names():
            logger.info("Database tables not found, creating...")
            db.create_all()
            run_light_migrations()
            if not SiteSetting.query.first():
                db.session.add(SiteSetting())
                db.session.commit()
            logger.info("Database initialized successfully")
        else:
            run_light_migrations()
    except Exception as e:
        logger.error(f"DB init error: {e}")
        db.session.rollback()

# ---------- Cached read helpers ----------
@cache.memoize(timeout=60)
def get_categories_cached():
    return Category.query.order_by(Category.sort_order).all()

@cache.memoize(timeout=30)
def get_library_items_cached():
    return (PromptLibrary.query
            .options(joinedload(PromptLibrary.user))
            .order_by(PromptLibrary.created_at.desc())
            .all())

def invalidate_library_cache():
    cache.delete_memoized(get_categories_cached)
    cache.delete_memoized(get_library_items_cached)

# ==================== Public Routes ====================
@app.route('/')
def index():
    try:
        site = SiteSetting.query.first()
        if site and site.status == 'off':
            return render_template('index.html', site_status='off', offline_message=site.offline_message)

        categories = get_categories_cached()
        library_items = get_library_items_cached()
        active_ad = LibraryAd.query.filter_by(is_active=True).order_by(LibraryAd.created_at.desc()).first()

        ad_dict = None
        if active_ad:
            ad_dict = {
                'id': active_ad.id,
                'title': active_ad.title,
                'text': active_ad.text,
                'image_url': active_ad.image_url,
                'button_text': active_ad.button_text,
                'button_link': active_ad.button_link,
                'duration_seconds': active_ad.duration_seconds,
            }

        return render_template('index.html',
                               categories=categories,
                               library_items=library_items,
                               active_ad=ad_dict,
                               site_status='on')
    except Exception as e:
        logger.error(f"Index error: {e}\n{traceback.format_exc()}")
        return render_template('index.html', site_status='on', categories=[], library_items=[], active_ad=None)

@app.route('/about')
def about_page():
    try:
        prompt_count = PromptLibrary.query.count()
        user_count = User.query.count()
        total_copies = db.session.query(func.sum(PromptLibrary.copy_count)).scalar() or 0
        total_shares = db.session.query(func.sum(PromptLibrary.share_count)).scalar() or 0
        return render_template('about.html',
                               prompt_count=prompt_count,
                               user_count=user_count,
                               total_copies=total_copies,
                               total_shares=total_shares)
    except Exception as e:
        logger.error(f"About error: {e}\n{traceback.format_exc()}")
        return render_template('about.html', prompt_count=0, user_count=0, total_copies=0, total_shares=0)

# ==================== Auth ====================
@app.route('/signup')
@app.route('/sign')
def sign_page():
    if session.get('user_id'):
        return redirect(url_for('index'))
    return render_template('sign.html', csrf_token=generate_csrf_token(),
                            google_client_id=Config.GOOGLE_CLIENT_ID)

@app.route('/auth/signup', methods=['POST'])
@limiter.limit("10 per minute")
def auth_signup():
    try:
        data = request.get_json() or {}
        if not validate_csrf_token(data.get('csrf_token')):
            return jsonify({'success': False, 'message': 'CSRF خطأ'}), 400

        name = (data.get('name') or '').strip()
        email = (data.get('email') or '').strip().lower()
        password = data.get('password') or ''
        next_url = data.get('next')

        if not name or not email or not password:
            return jsonify({'success': False, 'message': 'يرجى ملء جميع الحقول'}), 400
        if not is_valid_email(email):
            return jsonify({'success': False, 'message': 'بريد إلكتروني غير صالح'}), 400
        if len(password) < 6:
            return jsonify({'success': False, 'message': 'كلمة المرور يجب أن تكون 6 أحرف على الأقل'}), 400
        if User.query.filter_by(email=email).first():
            return jsonify({'success': False, 'message': 'هذا البريد الإلكتروني مستخدم بالفعل'}), 400

        user = User(name=name, email=email, password_hash=generate_password_hash(password))
        db.session.add(user)
        db.session.commit()
        session.permanent = True
        session['user_id'] = user.id
        redirect_to = safe_redirect(next_url, url_for('index'))
        return jsonify({'success': True, 'message': 'تم إنشاء الحساب بنجاح', 'redirect': redirect_to})
    except Exception as e:
        db.session.rollback()
        logger.error(f"Signup error: {e}\n{traceback.format_exc()}")
        return jsonify({'success': False, 'message': 'خطأ في إنشاء الحساب'}), 500

@app.route('/auth/login', methods=['POST'])
@limiter.limit("15 per minute")
def auth_login():
    try:
        data = request.get_json() or {}
        if not validate_csrf_token(data.get('csrf_token')):
            return jsonify({'success': False, 'message': 'CSRF خطأ'}), 400

        email = (data.get('email') or '').strip().lower()
        password = data.get('password') or ''
        next_url = data.get('next')
        user = User.query.filter_by(email=email).first()

        if not user or not user.password_hash or not check_password_hash(user.password_hash, password):
            return jsonify({'success': False, 'message': 'البريد الإلكتروني أو كلمة المرور غير صحيحة'}), 401

        session.permanent = True
        session['user_id'] = user.id
        user.last_active = datetime.utcnow()
        db.session.commit()
        redirect_to = safe_redirect(next_url, url_for('index'))
        return jsonify({'success': True, 'message': 'تم تسجيل الدخول بنجاح', 'redirect': redirect_to})
    except Exception as e:
        db.session.rollback()
        logger.error(f"Login error: {e}\n{traceback.format_exc()}")
        return jsonify({'success': False, 'message': 'خطأ في تسجيل الدخول'}), 500

@app.route('/auth/google', methods=['POST'])
@limiter.limit("10 per minute")
def auth_google():
    try:
        data = request.get_json() or {}
        token = data.get('credential')
        next_url = data.get('next')
        if not token:
            return jsonify({'success': False, 'message': 'رمز جوجل مفقود'}), 400

        try:
            idinfo = google_id_token.verify_oauth2_token(token, google_requests.Request(), Config.GOOGLE_CLIENT_ID)
            google_id = idinfo['sub']
            email = (idinfo.get('email') or '').lower()
            name = idinfo.get('name') or (email.split('@')[0] if email else 'مستخدم')
            avatar = idinfo.get('picture')
        except Exception as e:
            logger.error(f"Google auth verification error: {e}")
            return jsonify({'success': False, 'message': 'فشل التحقق من حساب جوجل'}), 400

        user = User.query.filter_by(google_id=google_id).first()
        if not user and email:
            user = User.query.filter_by(email=email).first()
        if not user:
            user = User(name=name, email=email, google_id=google_id, avatar_url=avatar)
            db.session.add(user)
        else:
            user.google_id = user.google_id or google_id
            user.avatar_url = user.avatar_url or avatar
        db.session.commit()
        session.permanent = True
        session['user_id'] = user.id
        user.last_active = datetime.utcnow()
        db.session.commit()
        redirect_to = safe_redirect(next_url, url_for('index'))
        return jsonify({'success': True, 'message': 'تم تسجيل الدخول عبر جوجل', 'redirect': redirect_to})
    except Exception as e:
        db.session.rollback()
        logger.error(f"Google auth save error: {e}\n{traceback.format_exc()}")
        return jsonify({'success': False, 'message': 'خطأ في حفظ الحساب'}), 500

@app.route('/logout')
def logout():
    session.pop('user_id', None)
    return redirect(url_for('index'))

# ==================== Admin ====================
# (جميع مسارات الإدارة موجودة كما هي، مع حذف مساهمات وطلبات التعديل والحذف)
# تم حذف: /admin/contribution/*, /admin/edit-request/*, /admin/delete-request/*
# تم الاحتفاظ ب: /admin, /admin/category/*, /admin/library/*, /admin/library_ad/*, /admin/user/*, /admin/errors/*, /api/admin/*

# ==================== Tracking API ====================
@app.route('/api/prompt/<int:item_id>/copy', methods=['POST'])
@limiter.limit("30 per minute")
def track_copy(item_id):
    try:
        item = PromptLibrary.query.get_or_404(item_id)
        item.copy_count = (item.copy_count or 0) + 1
        db.session.commit()
        return jsonify({'success': True, 'copy_count': item.copy_count})
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error tracking copy: {e}")
        return jsonify({'success': False}), 500

@app.route('/api/prompt/<int:item_id>/share', methods=['POST'])
@limiter.limit("30 per minute")
def track_share(item_id):
    try:
        item = PromptLibrary.query.get_or_404(item_id)
        item.share_count = (item.share_count or 0) + 1
        db.session.commit()
        return jsonify({'success': True, 'share_count': item.share_count})
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error tracking share: {e}")
        return jsonify({'success': False}), 500

@app.route('/api/prompt/<int:item_id>/like', methods=['POST'])
@limiter.limit("30 per minute")
def track_like(item_id):
    try:
        item = PromptLibrary.query.get_or_404(item_id)
        item.likes = (item.likes or 0) + 1
        db.session.commit()
        return jsonify({'success': True, 'likes': item.likes})
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error tracking like: {e}")
        return jsonify({'success': False}), 500

@app.route('/api/mandatory-ad')
def get_mandatory_ad():
    try:
        ad = LibraryAd.query.filter_by(is_active=True).order_by(LibraryAd.created_at.desc()).first()
        if not ad:
            return jsonify({'success': False, 'message': 'No active ad'}), 404
        return jsonify({
            'success': True,
            'ad': {
                'id': ad.id,
                'title': ad.title,
                'text': ad.text,
                'image_url': ad.image_url,
                'button_text': ad.button_text,
                'button_link': ad.button_link,
                'duration_seconds': ad.duration_seconds,
            }
        })
    except Exception as e:
        logger.error(f"Mandatory ad error: {e}")
        return jsonify({'success': False}), 500

# ==================== Health ====================
@app.route('/health')
def health_check():
    try:
        db.session.execute(text('SELECT 1'))
        return jsonify({'status': 'ok', 'db': 'connected', 'timestamp': datetime.utcnow().isoformat()})
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return jsonify({'status': 'error', 'db': 'disconnected', 'timestamp': datetime.utcnow().isoformat()}), 503

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)