import os
import logging
import bcrypt
import secrets
import base64
import time
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
from sqlalchemy.exc import SQLAlchemyError, OperationalError

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
    return token == session.get('csrf_token')

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

# ---------- Database connection with retry ----------
def wait_for_db(max_retries=5, delay=2):
    """محاولة الاتصال بقاعدة البيانات مع إعادة المحاولة."""
    for attempt in range(max_retries):
        try:
            with db.engine.connect() as conn:
                conn.execute(text("SELECT 1"))
                logger.info("✅ Database connection successful.")
                return True
        except OperationalError as e:
            logger.warning(f"Database connection attempt {attempt+1}/{max_retries} failed: {e}")
            if attempt < max_retries - 1:
                time.sleep(delay)
            else:
                logger.error("❌ All database connection attempts failed.")
                return False
    return False

# ---------- Error handlers ----------
@app.errorhandler(404)
def page_not_found(e):
    return render_template('error.html', error_code=404, message="الصفحة غير موجودة"), 404

@app.errorhandler(500)
def internal_server_error(e):
    logger.error(f"Internal Server Error: {e}")
    # حاول عرض صفحة خطأ ودية
    return render_template('error.html', error_code=500, message="حدث خطأ داخلي في الخادم، يرجى المحاولة لاحقاً."), 500

@app.errorhandler(Exception)
def handle_exception(e):
    logger.error(f"Unhandled Exception: {e}")
    return render_template('error.html', error_code=500, message="حدث خطأ غير متوقع."), 500

# ---------- Database initialization ----------
_db_initialized = False
@app.before_request
def ensure_db_initialized():
    global _db_initialized
    if not _db_initialized:
        # حاول الاتصال بقاعدة البيانات
        if not wait_for_db():
            # إذا فشل الاتصال، اعرض صفحة صيانة
            return render_template('error.html', error_code=503, message="تعذر الاتصال بقاعدة البيانات، يرجى المحاولة لاحقاً."), 503
        
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
            return render_template('error.html', error_code=500, message="خطأ في تهيئة قاعدة البيانات."), 500

# ---------- Public routes ----------
@app.route('/')
def index():
    try:
        site = SiteSetting.query.first()
        if site and site.status == 'off':
            return render_template('index.html', site_status='off', offline_message=site.offline_message)

        categories = Category.query.order_by(Category.sort_order).all()
        library_items = PromptLibrary.query.order_by(PromptLibrary.created_at.desc()).all()
        active_ad = LibraryAd.query.filter_by(is_active=True).order_by(LibraryAd.created_at.desc()).first()

        return render_template('index.html',
                               categories=categories,
                               library_items=library_items,
                               active_ad=active_ad,
                               site_status='on',
                               user_id=session.get('user_id'))
    except Exception as e:
        logger.error(f"Index error: {e}")
        return render_template('error.html', error_code=500, message="حدث خطأ أثناء تحميل الصفحة."), 500

@app.route('/about')
def about():
    return render_template('about.html', current_year=datetime.utcnow().year)

@app.route('/sign', methods=['GET', 'POST'])
def sign():
    if request.method == 'GET':
        return render_template('sign.html', csrf_token=generate_csrf_token())

    data = request.get_json()
    if not data:
        return jsonify({'success': False, 'message': 'بيانات غير صحيحة'}), 400

    username = data.get('username', '').strip()
    password = data.get('password', '').strip()
    action = data.get('action', 'login')

    if not username or not password:
        return jsonify({'success': False, 'message': 'يرجى ملء جميع الحقول'}), 400

    try:
        if action == 'signup':
            if User.query.filter_by(username=username).first():
                return jsonify({'success': False, 'message': 'اسم المستخدم موجود مسبقاً'}), 400
            user = User(username=username, password_hash=hash_password(password))
            db.session.add(user)
            db.session.commit()
            session['user_id'] = user.id
            return jsonify({'success': True, 'message': 'تم إنشاء الحساب'})
        else:
            user = User.query.filter_by(username=username).first()
            if user and check_password(password, user.password_hash):
                session['user_id'] = user.id
                return jsonify({'success': True, 'message': 'تم تسجيل الدخول'})
            return jsonify({'success': False, 'message': 'بيانات غير صحيحة'}), 401
    except Exception as e:
        db.session.rollback()
        logger.error(f"Sign error: {e}")
        return jsonify({'success': False, 'message': 'حدث خطأ في الخادم'}), 500

@app.route('/logout')
def logout():
    session.pop('user_id', None)
    flash('تم تسجيل الخروج', 'success')
    return redirect(url_for('index'))

# ---------- Settings ----------
@app.route('/settings')
@login_required
def settings_page():
    try:
        user = User.query.get(session['user_id'])
        return render_template('settings.html', user=user, csrf_token=generate_csrf_token())
    except Exception as e:
        logger.error(f"Settings page error: {e}")
        return render_template('error.html', error_code=500, message="حدث خطأ أثناء تحميل الإعدادات."), 500

@app.route('/api/settings', methods=['POST'])
@login_required
def update_settings():
    if not validate_csrf_token(request.json.get('csrf_token')):
        return jsonify({'success': False, 'message': 'CSRF خطأ'}), 400

    try:
        user = User.query.get(session['user_id'])
        data = request.get_json()
        user.custom_prompt = data.get('custom_prompt', '').strip()
        user.search_keywords = data.get('search_keywords', '').strip()
        db.session.commit()
        return jsonify({'success': True, 'message': 'تم تحديث الإعدادات'})
    except Exception as e:
        db.session.rollback()
        logger.error(f"Settings update error: {e}")
        return jsonify({'success': False, 'message': 'حدث خطأ في الخادم'}), 500

@app.route('/api/upload_image', methods=['POST'])
@login_required
def upload_image():
    if not validate_csrf_token(request.form.get('csrf_token')):
        return jsonify({'success': False, 'message': 'CSRF خطأ'}), 400

    if 'image' not in request.files:
        return jsonify({'success': False, 'message': 'لا توجد صورة'}), 400

    file = request.files['image']
    if file.filename == '':
        return jsonify({'success': False, 'message': 'لم يتم اختيار ملف'}), 400

    try:
        data = file.read()
        b64 = base64.b64encode(data).decode('utf-8')
        ext = file.filename.split('.')[-1].lower()
        mime = 'image/jpeg' if ext in ['jpg', 'jpeg'] else 'image/png' if ext == 'png' else 'image/webp'
        image_data = f"data:{mime};base64,{b64}"

        user = User.query.get(session['user_id'])
        user.profile_image = image_data
        db.session.commit()
        return jsonify({'success': True, 'message': 'تم رفع الصورة', 'image': image_data})
    except Exception as e:
        db.session.rollback()
        logger.error(f"Image upload error: {e}")
        return jsonify({'success': False, 'message': 'خطأ في رفع الصورة'}), 500

# ... باقي المسارات (admin, categories, library, ads, settings) مع نفس نمط معالجة الأخطاء ...

# ---------- Health ----------
@app.route('/health')
def health_check():
    return jsonify({'status': 'ok', 'timestamp': datetime.utcnow().isoformat()})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
