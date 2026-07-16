import os
import re
import logging
import bcrypt
import secrets
import base64
from io import BytesIO
from datetime import datetime
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_caching import Cache
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_talisman import Talisman
from flask_session import Session
from sqlalchemy.exc import SQLAlchemyError
from PIL import Image, UnidentifiedImageError

from models import db, User, Category, PromptLibrary, LibraryAd, SiteSetting
from config import Config

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
def hash_password(password):
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

def check_password(password, hashed):
    return bcrypt.checkpw(password.encode('utf-8'), hashed.encode('utf-8'))

def generate_csrf_token():
    if 'csrf_token' not in session:
        session['csrf_token'] = secrets.token_urlsafe(32)
    return session['csrf_token']

def validate_csrf_token(token):
    session_token = session.get('csrf_token')
    if not token or not session_token:
        return False
    return secrets.compare_digest(str(token).encode('utf-8'), str(session_token).encode('utf-8'))

USERNAME_RE = re.compile(r'^[a-zA-Z0-9_\u0600-\u06FF]{3,30}$')

def is_valid_username(username):
    return bool(USERNAME_RE.match(username))

ALLOWED_IMAGE_MIMES = {'image/jpeg': 'JPEG', 'image/png': 'PNG', 'image/webp': 'WEBP'}
ALLOWED_IMAGE_EXT = {'jpg', 'jpeg', 'png', 'webp'}
MAX_IMAGE_BYTES = 4 * 1024 * 1024  # 4MB

def read_and_validate_image(file_storage):
    """Reads an uploaded file and verifies it is really a supported image.
    Returns (data_uri, error_message). data_uri is None on failure."""
    filename = file_storage.filename or ''
    ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
    if ext not in ALLOWED_IMAGE_EXT:
        return None, 'صيغة الصورة غير مدعومة (JPG, PNG, WEBP فقط)'

    raw = file_storage.read()
    if not raw:
        return None, 'الملف فارغ'
    if len(raw) > MAX_IMAGE_BYTES:
        return None, 'حجم الصورة يتجاوز الحد المسموح (4MB)'

    try:
        img = Image.open(BytesIO(raw))
        img.verify()  # raises if not a real image
        img2 = Image.open(BytesIO(raw))  # re-open: verify() invalidates the file pointer
        detected_format = img2.format
        if detected_format not in ('JPEG', 'PNG', 'WEBP'):
            return None, 'صيغة الصورة غير مدعومة'
    except (UnidentifiedImageError, OSError, ValueError):
        return None, 'الملف المرفوع ليس صورة صالحة'

    mime = 'image/jpeg' if detected_format == 'JPEG' else 'image/png' if detected_format == 'PNG' else 'image/webp'
    b64 = base64.b64encode(raw).decode('utf-8')
    return f"data:{mime};base64,{b64}", None

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('user_id'):
            flash('يرجى تسجيل الدخول أولاً', 'error')
            return redirect(url_for('sign'))
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
@app.before_request
def ensure_db_initialized():
    global _db_initialized
    if not _db_initialized:
        try:
            db.create_all()
            if not SiteSetting.query.first():
                db.session.add(SiteSetting())
                db.session.commit()
            if not Category.query.first():
                defaults = [
                    Category(name='images', display_name='توليد صور', sort_order=1),
                    Category(name='writing', display_name='كتابة محتوى', sort_order=2),
                    Category(name='coding', display_name='برمجة', sort_order=3),
                    Category(name='design', display_name='تصميم UI', sort_order=4),
                    Category(name='analysis', display_name='تحليل بيانات', sort_order=5),
                    Category(name='creative', display_name='إبداعي', sort_order=6),
                ]
                for cat in defaults:
                    db.session.add(cat)
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
    if site and site.status == 'off' and not session.get('logged_in'):
        return render_template('index.html', site_status='off', offline_message=site.offline_message)

    categories = Category.query.order_by(Category.sort_order).all()
    library_items = (PromptLibrary.query
                      .filter_by(is_approved=True)
                      .order_by(PromptLibrary.created_at.desc())
                      .all())
    active_ad = LibraryAd.query.filter_by(is_active=True).order_by(LibraryAd.created_at.desc()).first()

    return render_template('index.html',
                           categories=categories,
                           library_items=library_items,
                           active_ad=active_ad,
                           site_status='on',
                           user_id=session.get('user_id'))

@app.route('/about')
def about():
    return render_template('about.html', current_year=datetime.utcnow().year, user_id=session.get('user_id'))

@app.route('/sign', methods=['GET', 'POST'])
@limiter.limit("15 per minute")
def sign():
    if request.method == 'GET':
        return render_template('sign.html', csrf_token=generate_csrf_token())

    data = request.get_json(silent=True)
    if not data:
        return jsonify({'success': False, 'message': 'بيانات غير صحيحة'}), 400

    if not validate_csrf_token(data.get('csrf_token')):
        return jsonify({'success': False, 'message': 'CSRF خطأ، أعد تحميل الصفحة'}), 400

    username = data.get('username', '').strip()
    password = data.get('password', '').strip()
    action = data.get('action', 'login')

    if not username or not password:
        return jsonify({'success': False, 'message': 'يرجى ملء جميع الحقول'}), 400

    if action == 'signup':
        if not is_valid_username(username):
            return jsonify({'success': False, 'message': 'اسم المستخدم يجب أن يكون 3-30 حرفاً (أحرف/أرقام فقط)'}), 400
        if len(password) < 8:
            return jsonify({'success': False, 'message': 'كلمة المرور يجب ألا تقل عن 8 أحرف'}), 400
        if User.query.filter_by(username=username).first():
            return jsonify({'success': False, 'message': 'اسم المستخدم موجود مسبقاً'}), 400
        user = User(username=username, password_hash=hash_password(password))
        db.session.add(user)
        db.session.commit()
        session.clear()
        session['user_id'] = user.id
        return jsonify({'success': True, 'message': 'تم إنشاء الحساب'})

    else:
        user = User.query.filter_by(username=username).first()
        if user and check_password(password, user.password_hash):
            session.clear()
            session['user_id'] = user.id
            return jsonify({'success': True, 'message': 'تم تسجيل الدخول'})
        return jsonify({'success': False, 'message': 'بيانات غير صحيحة'}), 401

@app.route('/logout')
def logout():
    session.pop('user_id', None)
    flash('تم تسجيل الخروج', 'success')
    return redirect(url_for('index'))

# ---------- Settings ----------
@app.route('/settings')
@login_required
def settings_page():
    user = User.query.get(session['user_id'])
    categories = Category.query.order_by(Category.sort_order).all()
    my_submissions = (PromptLibrary.query
                       .filter_by(submitted_by=user.id)
                       .order_by(PromptLibrary.created_at.desc())
                       .all())
    return render_template('settings.html',
                           user=user,
                           categories=categories,
                           my_submissions=my_submissions,
                           csrf_token=generate_csrf_token())

@app.route('/api/settings', methods=['POST'])
@login_required
def update_settings():
    if not validate_csrf_token(request.json.get('csrf_token')):
        return jsonify({'success': False, 'message': 'CSRF خطأ'}), 400

    user = User.query.get(session['user_id'])
    data = request.get_json()
    user.custom_prompt = data.get('custom_prompt', '').strip()
    user.search_keywords = data.get('search_keywords', '').strip()
    db.session.commit()
    return jsonify({'success': True, 'message': 'تم تحديث الإعدادات'})

@app.route('/api/upload_image', methods=['POST'])
@login_required
@limiter.limit("20 per hour")
def upload_image():
    if not validate_csrf_token(request.form.get('csrf_token')):
        return jsonify({'success': False, 'message': 'CSRF خطأ'}), 400

    if 'image' not in request.files:
        return jsonify({'success': False, 'message': 'لا توجد صورة'}), 400

    file = request.files['image']
    if file.filename == '':
        return jsonify({'success': False, 'message': 'لم يتم اختيار ملف'}), 400

    image_data, error = read_and_validate_image(file)
    if error:
        return jsonify({'success': False, 'message': error}), 400

    try:
        user = User.query.get(session['user_id'])
        user.profile_image = image_data
        db.session.commit()
        return jsonify({'success': True, 'message': 'تم رفع الصورة', 'image': image_data})
    except Exception as e:
        db.session.rollback()
        logger.error(f"Image upload error: {e}")
        return jsonify({'success': False, 'message': 'خطأ في رفع الصورة'}), 500

@app.route('/api/library/submit', methods=['POST'])
@login_required
@limiter.limit("10 per hour")
def submit_library_prompt():
    if not validate_csrf_token(request.form.get('csrf_token')):
        return jsonify({'success': False, 'message': 'CSRF خطأ'}), 400

    title = request.form.get('title', '').strip()[:200]
    category = request.form.get('category', 'general').strip()[:50]
    prompt_text = request.form.get('prompt_text', '').strip()[:4000]
    keywords = request.form.get('keywords', '').strip()[:300]

    if not title or not prompt_text:
        return jsonify({'success': False, 'message': 'يرجى ملء العنوان والبرومبت'}), 400

    if not Category.query.filter_by(name=category).first():
        category = 'general'

    if 'image' not in request.files or request.files['image'].filename == '':
        return jsonify({'success': False, 'message': 'يرجى اختيار صورة'}), 400

    image_data, error = read_and_validate_image(request.files['image'])
    if error:
        return jsonify({'success': False, 'message': error}), 400

    try:
        user = User.query.get(session['user_id'])
        item = PromptLibrary(
            title=title,
            category=category,
            image_url=image_data,
            prompt_text=prompt_text,
            keywords=keywords or None,
            publisher=user.username,
            submitted_by=user.id,
            is_approved=False,
        )
        db.session.add(item)
        db.session.commit()
        return jsonify({'success': True, 'message': 'تم إرسال البرومبت، سيظهر في المكتبة بعد مراجعته'})
    except Exception as e:
        db.session.rollback()
        logger.error(f"Prompt submission error: {e}")
        return jsonify({'success': False, 'message': 'خطأ في إرسال البرومبت'}), 500

# ---------- Admin ----------
@app.route('/admin', methods=['GET', 'POST'])
@limiter.limit("10 per minute")
def admin_panel():
    if request.method == 'POST':
        submitted_password = request.form.get('password', '')
        if secrets.compare_digest(submitted_password.encode('utf-8'), Config.ADMIN_PASSWORD.encode('utf-8')):
            session['logged_in'] = True
            return redirect(url_for('admin_panel'))
        flash('كلمة المرور غير صحيحة', 'error')
        return redirect(url_for('admin_panel'))

    if session.get('logged_in'):
        categories = Category.query.order_by(Category.sort_order).all()
        library_items = (PromptLibrary.query
                          .filter_by(is_approved=True)
                          .order_by(PromptLibrary.created_at.desc())
                          .all())
        pending_items = (PromptLibrary.query
                          .filter_by(is_approved=False)
                          .order_by(PromptLibrary.created_at.desc())
                          .all())
        library_ads = LibraryAd.query.order_by(LibraryAd.created_at.desc()).all()
        users = User.query.all()
        site_settings = SiteSetting.query.first()
        return render_template('admin.html',
                               categories=categories,
                               library_items=library_items,
                               pending_items=pending_items,
                               library_ads=library_ads,
                               users=users,
                               site_settings=site_settings,
                               csrf_token=generate_csrf_token())
    return render_template('admin.html', csrf_token=generate_csrf_token())

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
    try:
        item = PromptLibrary(
            title=request.form.get('title'),
            category=request.form.get('category', 'general'),
            image_url=request.form.get('image_url'),
            prompt_text=request.form.get('prompt_text'),
            keywords=request.form.get('keywords', '').strip() or None,
            publisher=request.form.get('publisher', '').strip() or None,
            is_approved=True
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
    item.title = request.form.get('title', item.title)
    item.category = request.form.get('category', item.category)
    item.image_url = request.form.get('image_url', item.image_url)
    item.prompt_text = request.form.get('prompt_text', item.prompt_text)
    item.keywords = request.form.get('keywords', item.keywords)
    item.publisher = request.form.get('publisher', item.publisher) or None
    db.session.commit()
    flash('تم تحديث البرومبت', 'success')
    return redirect(url_for('admin_panel'))

@app.route('/admin/library/<int:item_id>/approve', methods=['POST'])
@admin_required
def approve_library_item(item_id):
    if not validate_csrf_token(request.form.get('csrf_token')):
        return "CSRF Error", 400
    item = PromptLibrary.query.get_or_404(item_id)
    item.is_approved = True
    db.session.commit()
    flash('تمت الموافقة على البرومبت', 'success')
    return redirect(url_for('admin_panel'))

@app.route('/admin/library/<int:item_id>/reject', methods=['POST'])
@admin_required
def reject_library_item(item_id):
    if not validate_csrf_token(request.form.get('csrf_token')):
        return "CSRF Error", 400
    item = PromptLibrary.query.get_or_404(item_id)
    db.session.delete(item)
    db.session.commit()
    flash('تم رفض البرومبت وحذفه', 'success')
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

# ---------- Health ----------
@app.route('/health')
def health_check():
    return jsonify({'status': 'ok', 'timestamp': datetime.utcnow().isoformat()})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
