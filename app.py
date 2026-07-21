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
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy import inspect, text, func
from werkzeug.security import generate_password_hash, check_password_hash
from google.oauth2 import id_token as google_id_token
from google.auth.transport import requests as google_requests
from dotenv import load_dotenv

load_dotenv()

# ==================== Production Logging ====================
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s in %(module)s [%(pathname)s:%(lineno)d]: %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('app.log', encoding='utf-8') if not os.getenv('RENDER') else logging.StreamHandler()
    ]
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
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class SiteSetting(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    status = db.Column(db.String(10), default='on')
    offline_message = db.Column(db.Text, default='الموقع تحت الصيانة حالياً.')

class UploadContribution(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    category = db.Column(db.String(50), nullable=False, default='general')
    image_url = db.Column(db.String(500), nullable=True)
    prompt_text = db.Column(db.Text, nullable=False)
    publisher_name = db.Column(db.String(80), nullable=True)
    publisher_link = db.Column(db.String(500), nullable=True)
    keywords = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(20), default='pending')
    user_id = db.Column(db.Integer, db.ForeignKey('app_user.id'), nullable=True)
    user = db.relationship('User', backref='contributions')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class PromptEditRequest(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    prompt_id = db.Column(db.Integer, db.ForeignKey('prompt_library.id'), nullable=False)
    prompt = db.relationship('PromptLibrary', backref='edit_requests')
    user_id = db.Column(db.Integer, db.ForeignKey('app_user.id'), nullable=False)
    user = db.relationship('User', backref='prompt_edit_requests')
    new_title = db.Column(db.String(200), nullable=False)
    new_category = db.Column(db.String(50), nullable=False)
    new_prompt_text = db.Column(db.Text, nullable=False)
    new_image_url = db.Column(db.String(500), nullable=True)
    new_keywords = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(20), default='pending')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class PromptDeleteRequest(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    prompt_id = db.Column(db.Integer, db.ForeignKey('prompt_library.id'), nullable=False)
    prompt = db.relationship('PromptLibrary', backref='delete_requests')
    user_id = db.Column(db.Integer, db.ForeignKey('app_user.id'), nullable=False)
    user = db.relationship('User', backref='prompt_delete_requests')
    status = db.Column(db.String(20), default='pending')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

# ==================== App Factory ====================
app = Flask(__name__, template_folder=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'templates'))
app.config.from_object(Config)

db.init_app(app)
migrate = Migrate(app, db)
app.config['SESSION_SQLALCHEMY'] = db
Session(app)  # was imported but never initialized -> sessions were never actually persisted server-side
cache = Cache(app)
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
@app.errorhandler(404)
def not_found(e):
    logger.warning(f"404: {request.url} - {request.remote_addr}")
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

# ---------- Database initialization ----------
_db_initialized = False

def run_light_migrations():
    required_columns = {
        'prompt_library': {
            'publisher_link': 'VARCHAR(500)',
            'keywords': 'TEXT',
            'copy_count': 'INTEGER NOT NULL DEFAULT 0',
            'share_count': 'INTEGER NOT NULL DEFAULT 0',
            'user_id': 'INTEGER',
        },
        'upload_contribution': {
            'publisher_link': 'VARCHAR(500)',
            'keywords': 'TEXT',
            'user_id': 'INTEGER',
        },
        'app_user': {
            'last_active': 'TIMESTAMP DEFAULT CURRENT_TIMESTAMP',
        },
    }
    try:
        inspector = inspect(db.engine)
        existing_tables = inspector.get_table_names()
        for table_name, columns in required_columns.items():
            if table_name not in existing_tables:
                continue
            existing_columns = {col['name'] for col in inspector.get_columns(table_name)}
            for col_name, col_def in columns.items():
                if col_name in existing_columns:
                    continue
                try:
                    db.session.execute(text(f'ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS {col_name} {col_def}'))
                    db.session.commit()
                    logger.info(f"Migration: added column {table_name}.{col_name}")
                except Exception as e:
                    db.session.rollback()
                    logger.warning(f"Migration skipped for {table_name}.{col_name}: {e}")
    except Exception as e:
        logger.error(f"Migration error: {e}")
        db.session.rollback()

@app.before_request
def ensure_db_initialized():
    global _db_initialized
    if not _db_initialized:
        try:
            db.create_all()
            run_light_migrations()
            if not SiteSetting.query.first():
                db.session.add(SiteSetting())
                db.session.commit()
            _db_initialized = True
            logger.info("Database initialized successfully")
        except Exception as e:
            logger.error(f"DB init error: {e}")
            db.session.rollback()

# ---------- Cached read helpers (avoid re-querying the full table on every hit) ----------
@cache.memoize(timeout=60)
def get_categories_cached():
    return Category.query.order_by(Category.sort_order).all()

@cache.memoize(timeout=30)
def get_library_items_cached():
    return PromptLibrary.query.order_by(PromptLibrary.created_at.desc()).all()

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
                'duration_seconds': active_ad.duration_seconds
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
@app.route('/sign')  # Alias for backward compatibility
def sign_page():
    if session.get('user_id'):
        return redirect(url_for('settings_page'))
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
        return jsonify({'success': True, 'message': 'تم إنشاء الحساب بنجاح', 'redirect': url_for('settings_page')})
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
        user = User.query.filter_by(email=email).first()

        if not user or not user.password_hash or not check_password_hash(user.password_hash, password):
            return jsonify({'success': False, 'message': 'البريد الإلكتروني أو كلمة المرور غير صحيحة'}), 401

        session.permanent = True
        session['user_id'] = user.id
        user.last_active = datetime.utcnow()
        db.session.commit()
        return jsonify({'success': True, 'message': 'تم تسجيل الدخول بنجاح', 'redirect': url_for('settings_page')})
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
        return jsonify({'success': True, 'message': 'تم تسجيل الدخول عبر جوجل', 'redirect': url_for('settings_page')})
    except Exception as e:
        db.session.rollback()
        logger.error(f"Google auth save error: {e}\n{traceback.format_exc()}")
        return jsonify({'success': False, 'message': 'خطأ في حفظ الحساب'}), 500

@app.route('/logout')
def logout():
    session.pop('user_id', None)
    return redirect(url_for('index'))

# ==================== Settings ====================
@app.route('/settings')
@login_required
def settings_page():
    try:
        user = current_user()
        if not user:
            session.pop('user_id', None)
            return redirect(url_for('sign_page'))

        categories = Category.query.order_by(Category.sort_order).all()
        my_prompts = PromptLibrary.query.filter_by(user_id=user.id).order_by(PromptLibrary.created_at.desc()).all()
        my_pending = UploadContribution.query.filter_by(user_id=user.id, status='pending').order_by(UploadContribution.created_at.desc()).all()
        my_edit_requests = PromptEditRequest.query.filter_by(user_id=user.id, status='pending').order_by(PromptEditRequest.created_at.desc()).all()
        my_delete_requests = PromptDeleteRequest.query.filter_by(user_id=user.id, status='pending').order_by(PromptDeleteRequest.created_at.desc()).all()

        return render_template('settings.html',
                               user=user,
                               categories=categories,
                               my_prompts=my_prompts,
                               my_pending=my_pending,
                               my_edit_requests=my_edit_requests,
                               my_delete_requests=my_delete_requests,
                               csrf_token=generate_csrf_token())
    except Exception as e:
        logger.error(f"Settings page error: {e}\n{traceback.format_exc()}")
        db.session.rollback()
        # Graceful fallback
        try:
            user = current_user()
            return render_template('settings.html',
                                   user=user,
                                   categories=[],
                                   my_prompts=[],
                                   my_pending=[],
                                   my_edit_requests=[],
                                   my_delete_requests=[],
                                   csrf_token=generate_csrf_token())
        except:
            return redirect(url_for('index'))

@app.route('/settings/update', methods=['POST'])
@login_required
def update_settings():
    try:
        data = request.get_json() or {}
        if not validate_csrf_token(data.get('csrf_token')):
            return jsonify({'success': False, 'message': 'CSRF خطأ'}), 400

        user = current_user()
        if not user:
            return jsonify({'success': False, 'message': 'الجلسة منتهية'}), 401

        field = data.get('field')
        value = (data.get('value') or '').strip()

        if field == 'name':
            if not value:
                return jsonify({'success': False, 'message': 'الاسم مطلوب'}), 400
            user.name = value
        elif field == 'avatar':
            if value and not is_valid_publisher_link(value):
                return jsonify({'success': False, 'message': 'رابط الصورة غير صالح'}), 400
            user.avatar_url = value or None
        elif field == 'bio':
            user.bio = value or None
        elif field == 'profile_link':
            if value and not is_valid_publisher_link(value):
                return jsonify({'success': False, 'message': 'الرابط غير صالح'}), 400
            user.profile_link = value or None
        elif field == 'email':
            if not is_valid_email(value):
                return jsonify({'success': False, 'message': 'بريد إلكتروني غير صالح'}), 400
            existing = User.query.filter_by(email=value).first()
            if existing and existing.id != user.id:
                return jsonify({'success': False, 'message': 'هذا البريد مستخدم بالفعل'}), 400
            user.email = value
        elif field == 'password':
            if len(value) < 6:
                return jsonify({'success': False, 'message': 'كلمة المرور يجب أن تكون 6 أحرف على الأقل'}), 400
            user.password_hash = generate_password_hash(value)
        else:
            return jsonify({'success': False, 'message': 'حقل غير معروف'}), 400

        db.session.commit()
        return jsonify({'success': True, 'message': 'تم التحديث بنجاح'})
    except Exception as e:
        db.session.rollback()
        logger.error(f"Settings update error: {e}\n{traceback.format_exc()}")
        return jsonify({'success': False, 'message': 'خطأ في التحديث'}), 500

@app.route('/upload', methods=['GET', 'POST'])
@login_required
def upload():
    user = current_user()
    if request.method == 'POST':
        try:
            data = request.get_json()
            if not data:
                return jsonify({'success': False, 'message': 'بيانات غير صحيحة'}), 400

            title = data.get('title', '').strip()
            category = data.get('category', 'general').strip()
            prompt_text = data.get('prompt_text', '').strip()
            image_url = data.get('image_url', '').strip()
            keywords = data.get('keywords', '').strip()
            csrf_token = data.get('csrf_token', '')

            if not validate_csrf_token(csrf_token):
                return jsonify({'success': False, 'message': 'CSRF خطأ'}), 400

            if not title or not prompt_text:
                return jsonify({'success': False, 'message': 'يرجى ملء العنوان ونص البرومبت'}), 400

            contribution = UploadContribution(
                title=title,
                category=category,
                prompt_text=prompt_text,
                image_url=image_url or None,
                publisher_name=user.name,
                publisher_link=user.profile_link,
                keywords=keywords or None,
                user_id=user.id
            )
            db.session.add(contribution)
            db.session.commit()
            return jsonify({'success': True, 'message': 'تم استلام مساهمتك بنجاح! سيتم مراجعتها قريباً.'})
        except Exception as e:
            db.session.rollback()
            logger.error(f"Upload error: {e}\n{traceback.format_exc()}")
            return jsonify({'success': False, 'message': 'خطأ في حفظ البيانات'}), 500

    try:
        categories = Category.query.order_by(Category.sort_order).all()
        return render_template('upload.html', categories=categories, csrf_token=generate_csrf_token(), user=user)
    except Exception as e:
        logger.error(f"Upload GET error: {e}")
        return render_template('upload.html', categories=[], csrf_token=generate_csrf_token(), user=user)

# ==================== Prompt Edit/Delete Requests ====================
@app.route('/api/prompt/edit-request', methods=['POST'])
@login_required
def prompt_edit_request():
    try:
        data = request.get_json() or {}
        if not validate_csrf_token(data.get('csrf_token')):
            return jsonify({'success': False, 'message': 'CSRF خطأ'}), 400

        user = current_user()
        prompt_id = data.get('prompt_id')
        prompt = PromptLibrary.query.get_or_404(prompt_id)

        if prompt.user_id != user.id:
            return jsonify({'success': False, 'message': 'غير مصرح'}), 403

        existing = PromptEditRequest.query.filter_by(prompt_id=prompt_id, status='pending').first()
        if existing:
            return jsonify({'success': False, 'message': 'يوجد طلب تعديل قيد المراجعة بالفعل'}), 400

        req = PromptEditRequest(
            prompt_id=prompt_id,
            user_id=user.id,
            new_title=data.get('title', prompt.title),
            new_category=data.get('category', prompt.category),
            new_prompt_text=data.get('prompt_text', prompt.prompt_text),
            new_image_url=data.get('image_url', prompt.image_url),
            new_keywords=data.get('keywords', prompt.keywords)
        )
        db.session.add(req)
        db.session.commit()
        return jsonify({'success': True, 'message': 'تم إرسال طلب التعديل للمراجعة'})
    except Exception as e:
        db.session.rollback()
        logger.error(f"Edit request error: {e}\n{traceback.format_exc()}")
        return jsonify({'success': False, 'message': 'خطأ في إرسال الطلب'}), 500

@app.route('/api/prompt/delete-request', methods=['POST'])
@login_required
def prompt_delete_request():
    try:
        data = request.get_json() or {}
        if not validate_csrf_token(data.get('csrf_token')):
            return jsonify({'success': False, 'message': 'CSRF خطأ'}), 400

        user = current_user()
        prompt_id = data.get('prompt_id')
        prompt = PromptLibrary.query.get_or_404(prompt_id)

        if prompt.user_id != user.id:
            return jsonify({'success': False, 'message': 'غير مصرح'}), 403

        existing = PromptDeleteRequest.query.filter_by(prompt_id=prompt_id, status='pending').first()
        if existing:
            return jsonify({'success': False, 'message': 'يوجد طلب حذف قيد المراجعة بالفعل'}), 400

        req = PromptDeleteRequest(prompt_id=prompt_id, user_id=user.id)
        db.session.add(req)
        db.session.commit()
        return jsonify({'success': True, 'message': 'تم إرسال طلب الحذف للمراجعة'})
    except Exception as e:
        db.session.rollback()
        logger.error(f"Delete request error: {e}\n{traceback.format_exc()}")
        return jsonify({'success': False, 'message': 'خطأ في إرسال الطلب'}), 500

# ==================== Admin ====================
@app.route('/admin', methods=['GET', 'POST'])
def admin_panel():
    try:
        if request.method == 'POST' and request.form.get('password') == Config.ADMIN_PASSWORD:
            session.permanent = True
            session['logged_in'] = True
            return redirect(url_for('admin_panel'))

        if session.get('logged_in'):
            categories = Category.query.order_by(Category.sort_order).all()
            library_items = PromptLibrary.query.order_by(PromptLibrary.created_at.desc()).all()
            library_ads = LibraryAd.query.order_by(LibraryAd.created_at.desc()).all()
            site_settings = SiteSetting.query.first()
            contributions = UploadContribution.query.order_by(UploadContribution.created_at.desc()).all()
            edit_requests = PromptEditRequest.query.filter_by(status='pending').order_by(PromptEditRequest.created_at.desc()).all()
            delete_requests = PromptDeleteRequest.query.filter_by(status='pending').order_by(PromptDeleteRequest.created_at.desc()).all()

            fifteen_days_ago = datetime.utcnow() - timedelta(days=15)
            inactive_users_raw = User.query.filter(
                User.last_active < fifteen_days_ago
            ).order_by(User.last_active.asc()).all()

            inactive_users = []
            for user in inactive_users_raw:
                last_prompt = PromptLibrary.query.filter_by(user_id=user.id).order_by(PromptLibrary.created_at.desc()).first()
                inactive_users.append({
                    'id': user.id,
                    'name': user.name,
                    'email': user.email,
                    'avatar_url': user.avatar_url,
                    'last_active_days': (datetime.utcnow() - user.last_active).days,
                    'last_prompt_date': last_prompt.created_at if last_prompt else None
                })

            return render_template('admin.html',
                                   categories=categories,
                                   library_items=library_items,
                                   library_ads=library_ads,
                                   site_settings=site_settings,
                                   contributions=contributions,
                                   edit_requests=edit_requests,
                                   delete_requests=delete_requests,
                                   inactive_users=inactive_users,
                                   csrf_token=generate_csrf_token())
        return render_template('admin.html')
    except Exception as e:
        logger.error(f"Admin panel error: {e}\n{traceback.format_exc()}")
        db.session.rollback()
        return render_template('admin.html'), 500

@app.route('/admin/logout')
def admin_logout():
    session.pop('logged_in', None)
    return redirect(url_for('admin_panel'))

# ---------- Admin: Edit/Delete Request Approval ----------
@app.route('/admin/edit-request/<int:req_id>/approve', methods=['POST'])
@admin_required
def approve_edit_request(req_id):
    if not validate_csrf_token(request.form.get('csrf_token')):
        return "CSRF Error", 400
    try:
        req = PromptEditRequest.query.get_or_404(req_id)
        prompt = req.prompt
        prompt.title = req.new_title
        prompt.category = req.new_category
        prompt.prompt_text = req.new_prompt_text
        prompt.image_url = req.new_image_url or prompt.image_url
        prompt.keywords = req.new_keywords
        req.status = 'approved'
        db.session.commit()
        invalidate_library_cache()
        flash('تمت الموافقة على طلب التعديل', 'success')
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error approving edit request: {e}")
        flash('خطأ', 'error')
    return redirect(url_for('admin_panel'))

@app.route('/admin/edit-request/<int:req_id>/reject', methods=['POST'])
@admin_required
def reject_edit_request(req_id):
    if not validate_csrf_token(request.form.get('csrf_token')):
        return "CSRF Error", 400
    try:
        req = PromptEditRequest.query.get_or_404(req_id)
        req.status = 'rejected'
        db.session.commit()
        flash('تم رفض طلب التعديل', 'success')
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error rejecting edit request: {e}")
        flash('خطأ', 'error')
    return redirect(url_for('admin_panel'))

@app.route('/admin/delete-request/<int:req_id>/approve', methods=['POST'])
@admin_required
def approve_delete_request(req_id):
    if not validate_csrf_token(request.form.get('csrf_token')):
        return "CSRF Error", 400
    try:
        req = PromptDeleteRequest.query.get_or_404(req_id)
        prompt = req.prompt
        db.session.delete(prompt)
        req.status = 'approved'
        db.session.commit()
        invalidate_library_cache()
        flash('تم الحذف بنجاح', 'success')
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error approving delete request: {e}")
        flash('خطأ', 'error')
    return redirect(url_for('admin_panel'))

@app.route('/admin/delete-request/<int:req_id>/reject', methods=['POST'])
@admin_required
def reject_delete_request(req_id):
    if not validate_csrf_token(request.form.get('csrf_token')):
        return "CSRF Error", 400
    try:
        req = PromptDeleteRequest.query.get_or_404(req_id)
        req.status = 'rejected'
        db.session.commit()
        flash('تم رفض طلب الحذف', 'success')
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error rejecting delete request: {e}")
        flash('خطأ', 'error')
    return redirect(url_for('admin_panel'))

# ---------- Admin: User Management ----------
@app.route('/admin/user/<int:user_id>/delete', methods=['POST'])
@admin_required
def delete_user(user_id):
    if not validate_csrf_token(request.form.get('csrf_token')):
        return "CSRF Error", 400
    try:
        user = User.query.get_or_404(user_id)
        PromptLibrary.query.filter_by(user_id=user.id).delete()
        UploadContribution.query.filter_by(user_id=user.id).delete()
        PromptEditRequest.query.filter_by(user_id=user.id).delete()
        PromptDeleteRequest.query.filter_by(user_id=user.id).delete()
        db.session.delete(user)
        db.session.commit()
        invalidate_library_cache()
        flash('تم حذف الحساب بنجاح', 'success')
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error deleting user: {e}")
        flash('خطأ في حذف الحساب', 'error')
    return redirect(url_for('admin_panel'))

# ---------- Admin: Categories ----------
@app.route('/admin/category/add', methods=['POST'])
@admin_required
def add_category():
    if not validate_csrf_token(request.form.get('csrf_token')):
        return "CSRF Error", 400
    try:
        name = request.form.get('name', '').strip().lower().replace(' ', '_')
        display_name = request.form.get('display_name', '').strip()
        icon = request.form.get('icon', 'bi-tag').strip()
        sort_order = int(request.form.get('sort_order', 0))
        if not name or not display_name:
            flash('اسم التصنيف واسم العرض مطلوبان', 'error')
            return redirect(url_for('admin_panel'))
        if Category.query.filter_by(name=name).first():
            flash('التصنيف موجود مسبقاً', 'error')
            return redirect(url_for('admin_panel'))
        cat = Category(name=name, display_name=display_name, icon=icon, sort_order=sort_order)
        db.session.add(cat)
        db.session.commit()
        invalidate_library_cache()
        flash('تمت إضافة التصنيف', 'success')
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error adding category: {e}")
        flash('خطأ في إضافة التصنيف', 'error')
    return redirect(url_for('admin_panel'))

@app.route('/admin/category/<int:category_id>/delete', methods=['POST'])
@admin_required
def delete_category(category_id):
    if not validate_csrf_token(request.form.get('csrf_token')):
        return "CSRF Error", 400
    try:
        cat = Category.query.get_or_404(category_id)
        PromptLibrary.query.filter_by(category=cat.name).update({'category': 'general'})
        db.session.delete(cat)
        db.session.commit()
        invalidate_library_cache()
        flash('تم حذف التصنيف', 'success')
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error deleting category: {e}")
        flash('خطأ في حذف التصنيف', 'error')
    return redirect(url_for('admin_panel'))

# ---------- Admin: Library ----------
@app.route('/admin/library/add', methods=['POST'])
@admin_required
def add_library_item():
    if not validate_csrf_token(request.form.get('csrf_token')):
        return "CSRF Error", 400
    publisher_link = request.form.get('publisher_link', '').strip()
    if publisher_link and not is_valid_publisher_link(publisher_link):
        flash('رابط الناشر غير صالح', 'error')
        return redirect(url_for('admin_panel'))
    try:
        item = PromptLibrary(
            title=request.form.get('title'),
            category=request.form.get('category', 'general'),
            image_url=request.form.get('image_url', ''),
            prompt_text=request.form.get('prompt_text'),
            publisher=request.form.get('publisher', '').strip() or None,
            publisher_link=publisher_link or None,
            keywords=request.form.get('keywords', '').strip() or None
        )
        db.session.add(item)
        db.session.commit()
        invalidate_library_cache()
        flash('تمت إضافة البرومبت', 'success')
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error adding library item: {e}")
        flash('خطأ في إضافة البرومبت', 'error')
    return redirect(url_for('admin_panel'))

@app.route('/admin/library/<int:item_id>/delete', methods=['POST'])
@admin_required
def delete_library_item(item_id):
    if not validate_csrf_token(request.form.get('csrf_token')):
        return "CSRF Error", 400
    try:
        item = PromptLibrary.query.get_or_404(item_id)
        db.session.delete(item)
        db.session.commit()
        invalidate_library_cache()
        flash('تم حذف البرومبت', 'success')
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error deleting library item: {e}")
        flash('خطأ', 'error')
    return redirect(url_for('admin_panel'))

@app.route('/admin/library/<int:item_id>/update', methods=['POST'])
@admin_required
def update_library_item(item_id):
    if not validate_csrf_token(request.form.get('csrf_token')):
        return "CSRF Error", 400
    try:
        item = PromptLibrary.query.get_or_404(item_id)
        publisher_link = request.form.get('publisher_link')
        if publisher_link is not None:
            publisher_link = publisher_link.strip()
            if publisher_link and not is_valid_publisher_link(publisher_link):
                flash('رابط الناشر غير صالح', 'error')
                return redirect(url_for('admin_panel'))
            item.publisher_link = publisher_link or None
        item.title = request.form.get('title', item.title)
        item.category = request.form.get('category', item.category)
        item.image_url = request.form.get('image_url', item.image_url)
        item.prompt_text = request.form.get('prompt_text', item.prompt_text)
        item.publisher = request.form.get('publisher', item.publisher) or None
        item.keywords = request.form.get('keywords', item.keywords)
        db.session.commit()
        invalidate_library_cache()
        flash('تم تحديث البرومبت', 'success')
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error updating library item: {e}")
        flash('خطأ', 'error')
    return redirect(url_for('admin_panel'))

# ---------- Admin: Contributions ----------
@app.route('/admin/contribution/<int:contrib_id>/approve', methods=['POST'])
@admin_required
def approve_contribution(contrib_id):
    if not validate_csrf_token(request.form.get('csrf_token')):
        return "CSRF Error", 400
    try:
        contrib = UploadContribution.query.get_or_404(contrib_id)
        item = PromptLibrary(
            title=contrib.title,
            category=contrib.category,
            image_url=contrib.image_url or '',
            prompt_text=contrib.prompt_text,
            publisher=contrib.publisher_name,
            publisher_link=contrib.publisher_link,
            keywords=contrib.keywords,
            user_id=contrib.user_id
        )
        db.session.add(item)
        contrib.status = 'approved'
        db.session.commit()
        invalidate_library_cache()
        flash('تمت الموافقة على المساهمة', 'success')
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error approving contribution: {e}")
        flash('خطأ', 'error')
    return redirect(url_for('admin_panel'))

@app.route('/admin/contribution/<int:contrib_id>/reject', methods=['POST'])
@admin_required
def reject_contribution(contrib_id):
    if not validate_csrf_token(request.form.get('csrf_token')):
        return "CSRF Error", 400
    try:
        contrib = UploadContribution.query.get_or_404(contrib_id)
        contrib.status = 'rejected'
        db.session.commit()
        flash('تم رفض المساهمة', 'success')
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error rejecting contribution: {e}")
        flash('خطأ', 'error')
    return redirect(url_for('admin_panel'))

@app.route('/admin/contribution/<int:contrib_id>/delete', methods=['POST'])
@admin_required
def delete_contribution(contrib_id):
    if not validate_csrf_token(request.form.get('csrf_token')):
        return "CSRF Error", 400
    try:
        contrib = UploadContribution.query.get_or_404(contrib_id)
        db.session.delete(contrib)
        db.session.commit()
        flash('تم حذف المساهمة', 'success')
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error deleting contribution: {e}")
        flash('خطأ', 'error')
    return redirect(url_for('admin_panel'))

# ---------- Admin: Library Ads ----------
@app.route('/admin/library_ad/add', methods=['POST'])
@admin_required
def add_library_ad():
    if not validate_csrf_token(request.form.get('csrf_token')):
        return "CSRF Error", 400
    try:
        ad = LibraryAd(
            title=request.form.get('title'),
            text=request.form.get('text'),
            image_url=request.form.get('image_url') or None,
            button_text=request.form.get('button_text', 'زيارة'),
            button_link=request.form.get('button_link'),
            duration_seconds=int(request.form.get('duration_seconds', 5)),
            is_active=request.form.get('is_active') == 'on'
        )
        db.session.add(ad)
        db.session.commit()
        flash('تمت إضافة الإعلان', 'success')
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error adding library ad: {e}")
        flash('خطأ في إضافة الإعلان', 'error')
    return redirect(url_for('admin_panel'))

@app.route('/admin/library_ad/<int:ad_id>/delete', methods=['POST'])
@admin_required
def delete_library_ad(ad_id):
    if not validate_csrf_token(request.form.get('csrf_token')):
        return "CSRF Error", 400
    try:
        ad = LibraryAd.query.get_or_404(ad_id)
        db.session.delete(ad)
        db.session.commit()
        flash('تم حذف الإعلان', 'success')
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error deleting library ad: {e}")
        flash('خطأ في حذف الإعلان', 'error')
    return redirect(url_for('admin_panel'))

@app.route('/admin/library_ad/<int:ad_id>/toggle', methods=['POST'])
@admin_required
def toggle_library_ad(ad_id):
    if not validate_csrf_token(request.form.get('csrf_token')):
        return "CSRF Error", 400
    try:
        ad = LibraryAd.query.get_or_404(ad_id)
        ad.is_active = not ad.is_active
        db.session.commit()
        flash('تم تغيير حالة الإعلان', 'success')
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error toggling library ad: {e}")
        flash('خطأ في تغيير حالة الإعلان', 'error')
    return redirect(url_for('admin_panel'))

# ---------- Admin: Site Settings ----------
@app.route('/api/admin/update_site_settings', methods=['POST'])
@admin_required
def update_site_settings():
    try:
        if not validate_csrf_token(request.json.get('csrf_token')):
            return jsonify({'error': 'CSRF Error'}), 400
        s = SiteSetting.query.first()
        s.status = request.json.get('status', 'on')
        s.offline_message = request.json.get('offline_message', 'الموقع تحت الصيانة حالياً.')
        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        logger.error(f"Site settings error: {e}")
        return jsonify({'success': False}), 500

@app.route('/api/admin/get_site_status')
@admin_required
def get_site_status():
    try:
        s = SiteSetting.query.first()
        return jsonify({
            'status': s.status if s else 'on',
            'offline_message': s.offline_message if s else 'الموقع تحت الصيانة حالياً.'
        })
    except Exception as e:
        logger.error(f"Get site status error: {e}")
        return jsonify({'status': 'on', 'offline_message': 'الموقع تحت الصيانة حالياً.'})

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

# ---------- Health ----------
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
