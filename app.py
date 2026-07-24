import os
import re
import logging
import secrets
import traceback
from datetime import datetime, timedelta
from functools import wraps
from urllib.parse import urlparse

from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, send_from_directory
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_caching import Cache
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_talisman import Talisman
from flask_session import Session
from sqlalchemy import inspect, text, func
from sqlalchemy.orm import joinedload
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

# ==================== Extensions ====================
db = SQLAlchemy()
migrate = Migrate()
cache = Cache()
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["200 per minute"],
    storage_uri=os.getenv('REDIS_URL', 'memory://'),
    strategy='fixed-window'
)
talisman = Talisman()
session_ext = Session()

# ==================== Models ====================
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

# ==================== Helper Functions ====================
def generate_csrf_token():
    if 'csrf_token' not in session:
        session['csrf_token'] = secrets.token_urlsafe(32)
    return session['csrf_token']

def validate_csrf_token(token):
    return token == session.get('csrf_token')

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect(url_for('admin.admin_panel'))
        return f(*args, **kwargs)
    return decorated

def is_valid_publisher_link(url):
    if not url:
        return True
    try:
        parsed = urlparse(url.strip())
        return parsed.scheme in ('http', 'https') and bool(parsed.netloc)
    except Exception:
        return False

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

# ==================== Caching Helpers ====================
@cache.memoize(timeout=60)
def get_categories_cached():
    return Category.query.order_by(Category.sort_order).all()

@cache.memoize(timeout=30)
def get_library_items_cached():
    return (PromptLibrary.query
            .order_by(PromptLibrary.created_at.desc())
            .all())

def invalidate_library_cache():
    cache.delete_memoized(get_categories_cached)
    cache.delete_memoized(get_library_items_cached)

# ==================== Blueprints ====================
from flask import Blueprint

# ----- Main Blueprint -----
main_bp = Blueprint('main', __name__)

@main_bp.route('/')
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

@main_bp.route('/about')
def about_page():
    try:
        prompt_count = PromptLibrary.query.count()
        total_copies = db.session.query(func.sum(PromptLibrary.copy_count)).scalar() or 0
        total_shares = db.session.query(func.sum(PromptLibrary.share_count)).scalar() or 0
        return render_template('about.html',
                               prompt_count=prompt_count,
                               total_copies=total_copies,
                               total_shares=total_shares)
    except Exception as e:
        logger.error(f"About error: {e}\n{traceback.format_exc()}")
        return render_template('about.html', prompt_count=0, total_copies=0, total_shares=0)

@main_bp.route('/robots.txt')
def robots_txt():
    return """User-agent: *
Allow: /
Disallow: /admin
Disallow: /api/admin
Sitemap: https://ufoq.vercel.app/sitemap.xml
""", 200, {'Content-Type': 'text/plain'}

@main_bp.route('/favicon.ico')
def favicon():
    return '', 204

@main_bp.route('/health')
def health_check():
    try:
        db.session.execute(text('SELECT 1'))
        return jsonify({'status': 'ok', 'db': 'connected', 'timestamp': datetime.utcnow().isoformat()})
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return jsonify({'status': 'error', 'db': 'disconnected', 'timestamp': datetime.utcnow().isoformat()}), 503

# ----- API Blueprint -----
api_bp = Blueprint('api', __name__, url_prefix='/api')

@api_bp.route('/prompt/<int:item_id>/copy', methods=['POST'])
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
        return jsonify({'success': False, 'message': str(e)}), 500

@api_bp.route('/prompt/<int:item_id>/like', methods=['POST'])
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
        return jsonify({'success': False, 'message': str(e)}), 500

@api_bp.route('/prompt/<int:item_id>/share', methods=['POST'])
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
        return jsonify({'success': False, 'message': str(e)}), 500

@api_bp.route('/mandatory-ad')
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

# ----- Admin Blueprint -----
admin_bp = Blueprint('admin', __name__, url_prefix='/admin')

@admin_bp.route('/', methods=['GET', 'POST'])
def admin_panel():
    try:
        if request.method == 'POST' and request.form.get('password') == Config.ADMIN_PASSWORD:
            session.permanent = True
            session['logged_in'] = True
            return redirect(url_for('admin.admin_panel'))

        if session.get('logged_in'):
            categories = Category.query.order_by(Category.sort_order).all()
            library_items = PromptLibrary.query.order_by(PromptLibrary.created_at.desc()).all()
            library_ads = LibraryAd.query.order_by(LibraryAd.created_at.desc()).all()
            site_settings = SiteSetting.query.first()

            return render_template('admin.html',
                                   categories=categories,
                                   library_items=library_items,
                                   library_ads=library_ads,
                                   site_settings=site_settings,
                                   csrf_token=generate_csrf_token())
        return render_template('admin.html')
    except Exception as e:
        logger.error(f"Admin panel error: {e}\n{traceback.format_exc()}")
        db.session.rollback()
        return render_template('admin.html'), 500

@admin_bp.route('/logout')
def admin_logout():
    session.pop('logged_in', None)
    return redirect(url_for('admin.admin_panel'))

# ---------- Admin: Categories ----------
@admin_bp.route('/category/add', methods=['POST'])
@admin_required
def add_category():
    if not validate_csrf_token(request.form.get('csrf_token')):
        flash('CSRF خطأ', 'error')
        return redirect(url_for('admin.admin_panel'))
    try:
        name = request.form.get('name', '').strip().lower().replace(' ', '_')
        display_name = request.form.get('display_name', '').strip()
        icon = request.form.get('icon', 'bi-tag').strip()
        sort_order = int(request.form.get('sort_order', 0))
        if not name or not display_name:
            flash('اسم التصنيف واسم العرض مطلوبان', 'error')
            return redirect(url_for('admin.admin_panel'))
        if Category.query.filter_by(name=name).first():
            flash('التصنيف موجود مسبقاً', 'error')
            return redirect(url_for('admin.admin_panel'))
        cat = Category(name=name, display_name=display_name, icon=icon, sort_order=sort_order)
        db.session.add(cat)
        db.session.commit()
        invalidate_library_cache()
        flash('تمت إضافة التصنيف', 'success')
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error adding category: {e}")
        flash('خطأ في إضافة التصنيف', 'error')
    return redirect(url_for('admin.admin_panel'))

@admin_bp.route('/category/<int:category_id>/delete', methods=['POST'])
@admin_required
def delete_category(category_id):
    if not validate_csrf_token(request.form.get('csrf_token')):
        flash('CSRF خطأ', 'error')
        return redirect(url_for('admin.admin_panel'))
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
    return redirect(url_for('admin.admin_panel'))

@admin_bp.route('/category/<int:category_id>/edit', methods=['POST'])
@admin_required
def edit_category(category_id):
    if not validate_csrf_token(request.form.get('csrf_token')):
        flash('CSRF خطأ', 'error')
        return redirect(url_for('admin.admin_panel'))
    try:
        cat = Category.query.get_or_404(category_id)
        name = request.form.get('name', '').strip().lower().replace(' ', '_')
        display_name = request.form.get('display_name', '').strip()
        icon = request.form.get('icon', 'bi-tag').strip()
        sort_order = int(request.form.get('sort_order', 0))
        
        if not name or not display_name:
            flash('اسم التصنيف واسم العرض مطلوبان', 'error')
            return redirect(url_for('admin.admin_panel'))
        
        existing = Category.query.filter(Category.name == name, Category.id != category_id).first()
        if existing:
            flash('التصنيف موجود مسبقاً', 'error')
            return redirect(url_for('admin.admin_panel'))
        
        cat.name = name
        cat.display_name = display_name
        cat.icon = icon
        cat.sort_order = sort_order
        db.session.commit()
        invalidate_library_cache()
        flash('تم تحديث التصنيف', 'success')
    except Exception as e:
        db.session.rollback()
        logger.error(f"Edit category error: {e}\n{traceback.format_exc()}")
        flash('خطأ في تحديث التصنيف', 'error')
    return redirect(url_for('admin.admin_panel'))

# ---------- Admin: Library ----------
@admin_bp.route('/library/add', methods=['POST'])
@admin_required
def add_library_item():
    if not validate_csrf_token(request.form.get('csrf_token')):
        flash('CSRF خطأ', 'error')
        return redirect(url_for('admin.admin_panel'))
    publisher_link = request.form.get('publisher_link', '').strip()
    if publisher_link and not is_valid_publisher_link(publisher_link):
        flash('رابط الناشر غير صالح', 'error')
        return redirect(url_for('admin.admin_panel'))
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
    return redirect(url_for('admin.admin_panel'))

@admin_bp.route('/library/<int:item_id>/delete', methods=['POST'])
@admin_required
def delete_library_item(item_id):
    if not validate_csrf_token(request.form.get('csrf_token')):
        flash('CSRF خطأ', 'error')
        return redirect(url_for('admin.admin_panel'))
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
    return redirect(url_for('admin.admin_panel'))

@admin_bp.route('/library/<int:item_id>/update', methods=['POST'])
@admin_required
def update_library_item(item_id):
    if not validate_csrf_token(request.form.get('csrf_token')):
        flash('CSRF خطأ', 'error')
        return redirect(url_for('admin.admin_panel'))
    try:
        item = PromptLibrary.query.get_or_404(item_id)
        publisher_link = request.form.get('publisher_link')
        if publisher_link is not None:
            publisher_link = publisher_link.strip()
            if publisher_link and not is_valid_publisher_link(publisher_link):
                flash('رابط الناشر غير صالح', 'error')
                return redirect(url_for('admin.admin_panel'))
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
    return redirect(url_for('admin.admin_panel'))

# ---------- Admin: Ads ----------
@admin_bp.route('/library_ad/add', methods=['POST'])
@admin_required
def add_library_ad():
    if not validate_csrf_token(request.form.get('csrf_token')):
        flash('CSRF خطأ', 'error')
        return redirect(url_for('admin.admin_panel'))
    try:
        ad = LibraryAd(
            title=request.form.get('title'),
            text=request.form.get('text'),
            image_url=request.form.get('image_url') or None,
            button_text=request.form.get('button_text', 'زيارة'),
            button_link=request.form.get('button_link'),
            duration_seconds=int(request.form.get('duration_seconds', 5)),
            is_active=request.form.get('is_active') == 'on',
            is_mandatory=request.form.get('is_mandatory') == 'on'
        )
        db.session.add(ad)
        db.session.commit()
        flash('تمت إضافة الإعلان', 'success')
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error adding library ad: {e}")
        flash('خطأ في إضافة الإعلان', 'error')
    return redirect(url_for('admin.admin_panel'))

@admin_bp.route('/library_ad/<int:ad_id>/delete', methods=['POST'])
@admin_required
def delete_library_ad(ad_id):
    if not validate_csrf_token(request.form.get('csrf_token')):
        flash('CSRF خطأ', 'error')
        return redirect(url_for('admin.admin_panel'))
    try:
        ad = LibraryAd.query.get_or_404(ad_id)
        db.session.delete(ad)
        db.session.commit()
        flash('تم حذف الإعلان', 'success')
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error deleting library ad: {e}")
        flash('خطأ في حذف الإعلان', 'error')
    return redirect(url_for('admin.admin_panel'))

@admin_bp.route('/library_ad/<int:ad_id>/toggle', methods=['POST'])
@admin_required
def toggle_library_ad(ad_id):
    if not validate_csrf_token(request.form.get('csrf_token')):
        flash('CSRF خطأ', 'error')
        return redirect(url_for('admin.admin_panel'))
    try:
        ad = LibraryAd.query.get_or_404(ad_id)
        ad.is_active = not ad.is_active
        db.session.commit()
        flash('تم تغيير حالة الإعلان', 'success')
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error toggling library ad: {e}")
        flash('خطأ في تغيير حالة الإعلان', 'error')
    return redirect(url_for('admin.admin_panel'))

# ---------- Admin: Site Settings (API) ----------
@admin_bp.route('/api/admin/update_site_settings', methods=['POST'])
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

@admin_bp.route('/api/admin/get_site_status')
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

# ---------- Admin: Errors ----------
@admin_bp.route('/errors')
@admin_required
def admin_get_errors():
    show_ignored = request.args.get('show_ignored', 'false').lower() == 'true'
    query = ErrorLog.query
    if not show_ignored:
        query = query.filter_by(ignored=False)
    errors = query.order_by(ErrorLog.last_seen.desc()).all()
    return jsonify([e.to_dict() for e in errors])

@admin_bp.route('/error/<int:error_id>/ignore', methods=['POST'])
@admin_required
def admin_ignore_error(error_id):
    if not validate_csrf_token(request.form.get('csrf_token')):
        return jsonify({'success': False, 'message': 'CSRF خطأ'}), 400
    try:
        error = ErrorLog.query.get_or_404(error_id)
        error.ignored = True
        db.session.commit()
        return jsonify({'success': True, 'message': 'تم تجاهل الخطأ'})
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error ignoring error: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500

@admin_bp.route('/error/<int:error_id>/unignore', methods=['POST'])
@admin_required
def admin_unignore_error(error_id):
    if not validate_csrf_token(request.form.get('csrf_token')):
        return jsonify({'success': False, 'message': 'CSRF خطأ'}), 400
    try:
        error = ErrorLog.query.get_or_404(error_id)
        error.ignored = False
        db.session.commit()
        return jsonify({'success': True, 'message': 'تم إلغاء تجاهل الخطأ'})
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error unignoring error: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500

@admin_bp.route('/error/<int:error_id>/delete', methods=['POST'])
@admin_required
def admin_delete_error(error_id):
    if not validate_csrf_token(request.form.get('csrf_token')):
        return jsonify({'success': False, 'message': 'CSRF خطأ'}), 400
    try:
        error = ErrorLog.query.get_or_404(error_id)
        db.session.delete(error)
        db.session.commit()
        return jsonify({'success': True, 'message': 'تم حذف الخطأ'})
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error deleting error: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500

@admin_bp.route('/errors/clear-all', methods=['POST'])
@admin_required
def admin_clear_all_errors():
    if not validate_csrf_token(request.form.get('csrf_token')):
        return jsonify({'success': False, 'message': 'CSRF خطأ'}), 400
    try:
        ErrorLog.query.filter_by(ignored=False).delete()
        db.session.commit()
        return jsonify({'success': True, 'message': 'تم حذف جميع الأخطاء'})
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error clearing errors: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500

# ==================== Application Factory ====================
def create_app():
    app = Flask(__name__, template_folder='templates')
    app.config.from_object(Config)

    # Initialize extensions
    db.init_app(app)
    migrate.init_app(app, db)
    cache.init_app(app)
    limiter.init_app(app)
    talisman.init_app(app, force_https=False, content_security_policy={
        'default-src': ["'self'"],
        'style-src': ["'self'", "'unsafe-inline'", "https://fonts.googleapis.com", "https://cdn.jsdelivr.net"],
        'script-src': ["'self'", "'unsafe-inline'", "https://cdn.jsdelivr.net"],
        'font-src': ["'self'", "https://fonts.gstatic.com", "https://cdn.jsdelivr.net"],
        'img-src': ["'self'", "data:", "https:", "blob:"],
        'connect-src': ["'self'"],
        'frame-ancestors': ["'none'"],
    })
    app.config['SESSION_SQLALCHEMY'] = db
    session_ext.init_app(app)

    # Register blueprints
    app.register_blueprint(main_bp)
    app.register_blueprint(api_bp)
    app.register_blueprint(admin_bp)

    # ---------- Error Handlers ----------
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

    @app.before_request
    def ensure_db_initialized():
        try:
            inspector = inspect(db.engine)
            if 'category' not in inspector.get_table_names():
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

    return app