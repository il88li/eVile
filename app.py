import os
import logging
import secrets
import base64
from datetime import datetime
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
from sqlalchemy import inspect, text
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

# ==================== Config ====================
class Config:
    SECRET_KEY = os.getenv('SECRET_KEY')
    if not SECRET_KEY:
        raise ValueError("SECRET_KEY environment variable is required!")

    ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD', 'admin123')

    DATABASE_URL = os.getenv('DATABASE_URL', 'postgresql://user:pass@localhost:5432/db')
    # Render provides postgres:// but SQLAlchemy 2.0 requires postgresql://
    if DATABASE_URL.startswith('postgres://'):
        DATABASE_URL = DATABASE_URL.replace('postgres://', 'postgresql://', 1)
    SQLALCHEMY_DATABASE_URI = DATABASE_URL
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_POOL_SIZE = 20
    SQLALCHEMY_MAX_OVERFLOW = 40
    SQLALCHEMY_POOL_PRE_PING = True

    SESSION_TYPE = 'sqlalchemy'
    SESSION_SQLALCHEMY_TABLE = 'flask_sessions'
    SESSION_PERMANENT = True
    SESSION_USE_SIGNER = True
    SESSION_KEY_PREFIX = 'ufoq_session:'
    PERMANENT_SESSION_LIFETIME = 2592000

    CACHE_TYPE = 'SimpleCache'
    CACHE_DEFAULT_TIMEOUT = 300
    RATELIMIT_ENABLED = True
    RATELIMIT_STORAGE_URI = 'memory://'
    RATELIMIT_STRATEGY = 'fixed-window' 

# ==================== Models ====================
db = SQLAlchemy()

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
    image_url = db.Column(db.String(500), nullable=False)
    prompt_text = db.Column(db.Text, nullable=False)
    publisher = db.Column(db.String(80), nullable=True)
    publisher_link = db.Column(db.String(500), nullable=True)
    keywords = db.Column(db.Text, nullable=True)
    copy_count = db.Column(db.Integer, default=0, nullable=False)
    share_count = db.Column(db.Integer, default=0, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class LibraryAd(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    text = db.Column(db.Text, nullable=False)
    image_url = db.Column(db.String(500), nullable=True)
    button_text = db.Column(db.String(100), nullable=False)
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
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

# ==================== App Factory ====================
app = Flask(__name__, template_folder=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'templates'))
app.config.from_object(Config)

db.init_app(app)
migrate = Migrate(app, db)
cache = Cache(app)
limiter = Limiter(get_remote_address, app=app, default_limits=[], storage_uri=app.config['RATELIMIT_STORAGE_URI'])
Talisman(app, force_https=False, content_security_policy={
    'default-src': ["'self'"],
    'style-src': ["'self'", "'unsafe-inline'", "https://fonts.googleapis.com", "https://cdn.jsdelivr.net"],
    'script-src': ["'self'", "'unsafe-inline'", "https://cdn.jsdelivr.net"],
    'font-src': ["'self'", "https://fonts.gstatic.com", "https://cdn.jsdelivr.net"],
    'img-src': ["'self'", "data:", "https:"],
    'connect-src': ["'self'"]
})

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------- Helper functions ----------
def generate_csrf_token():
    if 'csrf_token' not in session:
        session['csrf_token'] = secrets.token_urlsafe(32)
    return session['csrf_token']

def validate_csrf_token(token):
    return token == session.get('csrf_token')

def is_valid_publisher_link(url):
    """Basic safety check before a publisher link is accepted: only http/https
    with a real host are allowed. This is a first-pass automated filter —
    admins should still manually check the link before approving a contribution."""
    if not url:
        return True  # optional field
    try:
        parsed = urlparse(url.strip())
        return parsed.scheme in ('http', 'https') and bool(parsed.netloc)
    except Exception:
        return False

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
    """db.create_all() only creates tables that don't exist yet — it never
    alters tables that are already deployed. Any column added to a model
    after the first deploy (e.g. copy_count, keywords, publisher_link) has
    to be patched onto the live table here, or existing databases will
    raise 'column does not exist' errors."""
    required_columns = {
        'prompt_library': {
            'publisher_link': 'VARCHAR(500)',
            'keywords': 'TEXT',
            'copy_count': 'INTEGER NOT NULL DEFAULT 0',
            'share_count': 'INTEGER NOT NULL DEFAULT 0',
        },
        'upload_contribution': {
            'publisher_link': 'VARCHAR(500)',
            'keywords': 'TEXT',
        },
    }
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
                logger.info(f"✅ Migration: added column {table_name}.{col_name}")
            except Exception as e:
                db.session.rollback()
                logger.error(f"❌ Migration failed for {table_name}.{col_name}: {e}")

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
            logger.info("✅ Database initialized.")
        except Exception as e:
            logger.error(f"❌ DB init error: {e}")
            db.session.rollback()

# ---------- Public routes ----------
@app.route('/')
def index():
    site = SiteSetting.query.first()
    if site and site.status == 'off':
        return render_template('index.html', site_status='off', offline_message=site.offline_message)

    categories = Category.query.order_by(Category.sort_order).all()
    library_items = PromptLibrary.query.order_by(PromptLibrary.created_at.desc()).all()
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

@app.route('/upload', methods=['GET', 'POST'])
def upload():
    if request.method == 'POST':
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'message': 'بيانات غير صحيحة'}), 400

        title = data.get('title', '').strip()
        category = data.get('category', 'general').strip()
        prompt_text = data.get('prompt_text', '').strip()
        image_url = data.get('image_url', '').strip()
        publisher_name = data.get('publisher_name', '').strip()
        publisher_link = data.get('publisher_link', '').strip()
        keywords = data.get('keywords', '').strip()
        csrf_token = data.get('csrf_token', '')

        if not validate_csrf_token(csrf_token):
            return jsonify({'success': False, 'message': 'CSRF خطأ'}), 400

        if not title or not prompt_text:
            return jsonify({'success': False, 'message': 'يرجى ملء العنوان ونص البرومبت'}), 400

        if publisher_link and not is_valid_publisher_link(publisher_link):
            return jsonify({'success': False, 'message': 'رابط الناشر غير صالح، يرجى إدخال رابط http/https صحيح'}), 400

        try:
            contribution = UploadContribution(
                title=title,
                category=category,
                prompt_text=prompt_text,
                image_url=image_url or None,
                publisher_name=publisher_name or None,
                publisher_link=publisher_link or None,
                keywords=keywords or None
            )
            db.session.add(contribution)
            db.session.commit()
            return jsonify({'success': True, 'message': 'تم استلام مساهمتك بنجاح! سيتم مراجعتها قريباً.'})
        except Exception as e:
            db.session.rollback()
            logger.error(f"Upload error: {e}")
            return jsonify({'success': False, 'message': 'خطأ في حفظ البيانات'}), 500

    categories = Category.query.order_by(Category.sort_order).all()
    return render_template('upload.html', categories=categories, csrf_token=generate_csrf_token())

# ---------- Admin ----------
@app.route('/admin', methods=['GET', 'POST'])
def admin_panel():
    if request.method == 'POST' and request.form.get('password') == Config.ADMIN_PASSWORD:
        session['logged_in'] = True
        return redirect(url_for('admin_panel'))

    if session.get('logged_in'):
        categories = Category.query.order_by(Category.sort_order).all()
        library_items = PromptLibrary.query.order_by(PromptLibrary.created_at.desc()).all()
        library_ads = LibraryAd.query.order_by(LibraryAd.created_at.desc()).all()
        site_settings = SiteSetting.query.first()
        contributions = UploadContribution.query.order_by(UploadContribution.created_at.desc()).all()
        return render_template('admin.html',
                               categories=categories,
                               library_items=library_items,
                               library_ads=library_ads,
                               site_settings=site_settings,
                               contributions=contributions,
                               csrf_token=generate_csrf_token())
    return render_template('admin.html')

@app.route('/admin/logout')
def admin_logout():
    session.pop('logged_in', None)
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
            image_url=request.form.get('image_url'),
            prompt_text=request.form.get('prompt_text'),
            publisher=request.form.get('publisher', '').strip() or None,
            publisher_link=publisher_link or None,
            keywords=request.form.get('keywords', '').strip() or None
        )
        db.session.add(item)
        db.session.commit()
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
    item = PromptLibrary.query.get_or_404(item_id)
    db.session.delete(item)
    db.session.commit()
    flash('تم حذف البرومبت', 'success')
    return redirect(url_for('admin_panel'))

@app.route('/admin/library/<int:item_id>/update', methods=['POST'])
@admin_required
def update_library_item(item_id):
    if not validate_csrf_token(request.form.get('csrf_token')):
        return "CSRF Error", 400
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
    flash('تم تحديث البرومبت', 'success')
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
            keywords=contrib.keywords
        )
        db.session.add(item)
        contrib.status = 'approved'
        db.session.commit()
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
            button_text=request.form.get('button_text'),
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
    if not validate_csrf_token(request.json.get('csrf_token')):
        return jsonify({'error': 'CSRF Error'}), 400
    s = SiteSetting.query.first()
    s.status = request.json.get('status', 'on')
    s.offline_message = request.json.get('offline_message', 'الموقع تحت الصيانة حالياً.')
    db.session.commit()
    return jsonify({'success': True})

@app.route('/api/admin/get_site_status')
@admin_required
def get_site_status():
    s = SiteSetting.query.first()
    return jsonify({
        'status': s.status if s else 'on',
        'offline_message': s.offline_message if s else 'الموقع تحت الصيانة حالياً.'
    })

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
    return jsonify({'status': 'ok', 'timestamp': datetime.utcnow().isoformat()})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
