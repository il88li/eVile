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
from sqlalchemy import text

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

# ===== معالجات الأخطاء =====
@app.errorhandler(404)
def page_not_found(e):
    try:
        return render_template('error.html', error_code=404, message="الصفحة غير موجودة"), 404
    except:
        return """
        <!DOCTYPE html>
        <html dir="rtl">
        <head><meta charset="UTF-8"><title>404 - غير موجود</title>
        <style>body{font-family:Tajawal,sans-serif;background:#0b0e1a;color:#f8fafc;display:flex;align-items:center;justify-content:center;height:100vh;text-align:center;padding:20px;margin:0;}
        .card{background:rgba(20,30,48,0.6);backdrop-filter:blur(20px);border:1px solid rgba(255,255,255,0.1);border-radius:24px;padding:40px;max-width:500px;width:100%;}
        h1{font-size:48px;color:#38bdf8;margin:0 0 10px;}
        p{color:#94a3b8;font-size:18px;}
        a{display:inline-block;margin-top:16px;padding:12px 28px;background:#38bdf8;color:#0b0e1a;border-radius:14px;text-decoration:none;font-weight:700;}</style>
        </head>
        <body><div class="card"><h1>404</h1><p>الصفحة غير موجودة</p><a href="/">العودة للرئيسية</a></div></body>
        </html>
        """, 404

@app.errorhandler(500)
def internal_server_error(e):
    logger.error(f"Internal Server Error: {e}")
    try:
        return render_template('error.html', error_code=500, message="حدث خطأ داخلي في الخادم، يرجى المحاولة لاحقاً."), 500
    except:
        return """
        <!DOCTYPE html>
        <html dir="rtl">
        <head><meta charset="UTF-8"><title>500 - خطأ داخلي</title>
        <style>body{font-family:Tajawal,sans-serif;background:#0b0e1a;color:#f8fafc;display:flex;align-items:center;justify-content:center;height:100vh;text-align:center;padding:20px;margin:0;}
        .card{background:rgba(20,30,48,0.6);backdrop-filter:blur(20px);border:1px solid rgba(255,255,255,0.1);border-radius:24px;padding:40px;max-width:500px;width:100%;}
        h1{font-size:48px;color:#38bdf8;margin:0 0 10px;}
        p{color:#94a3b8;font-size:18px;}
        a{display:inline-block;margin-top:16px;padding:12px 28px;background:#38bdf8;color:#0b0e1a;border-radius:14px;text-decoration:none;font-weight:700;}</style>
        </head>
        <body><div class="card"><h1>500</h1><p>حدث خطأ داخلي، يرجى المحاولة لاحقاً.</p><a href="/">العودة للرئيسية</a></div></body>
        </html>
        """, 500

@app.errorhandler(Exception)
def handle_exception(e):
    logger.error(f"Unhandled Exception: {e}")
    try:
        return render_template('error.html', error_code=500, message="حدث خطأ غير متوقع."), 500
    except:
        return """
        <!DOCTYPE html>
        <html dir="rtl">
        <head><meta charset="UTF-8"><title>500 - خطأ</title>
        <style>body{font-family:Tajawal,sans-serif;background:#0b0e1a;color:#f8fafc;display:flex;align-items:center;justify-content:center;height:100vh;text-align:center;padding:20px;margin:0;}
        .card{background:rgba(20,30,48,0.6);backdrop-filter:blur(20px);border:1px solid rgba(255,255,255,0.1);border-radius:24px;padding:40px;max-width:500px;width:100%;}
        h1{font-size:48px;color:#38bdf8;margin:0 0 10px;}
        p{color:#94a3b8;font-size:18px;}
        a{display:inline-block;margin-top:16px;padding:12px 28px;background:#38bdf8;color:#0b0e1a;border-radius:14px;text-decoration:none;font-weight:700;}</style>
        </head>
        <body><div class="card"><h1>500</h1><p>حدث خطأ غير متوقع.</p><a href="/">العودة للرئيسية</a></div></body>
        </html>
        """, 500

# ---------- Database initialization ----------
_db_initialized = False
@app.before_request
def ensure_db_initialized():
    global _db_initialized
    if not _db_initialized:
        # حاول الاتصال بقاعدة البيانات
        if not wait_for_db():
            # إذا فشل الاتصال، اعرض صفحة صيانة
            return """
            <!DOCTYPE html>
            <html dir="rtl">
            <head><meta charset="UTF-8"><title>503 - الخدمة غير متاحة</title>
            <style>body{font-family:Tajawal,sans-serif;background:#0b0e1a;color:#f8fafc;display:flex;align-items:center;justify-content:center;height:100vh;text-align:center;padding:20px;margin:0;}
            .card{background:rgba(20,30,48,0.6);backdrop-filter:blur(20px);border:1px solid rgba(255,255,255,0.1);border-radius:24px;padding:40px;max-width:500px;width:100%;}
            h1{font-size:48px;color:#38bdf8;margin:0 0 10px;}
            p{color:#94a3b8;font-size:18px;}
            a{display:inline-block;margin-top:16px;padding:12px 28px;background:#38bdf8;color:#0b0e1a;border-radius:14px;text-decoration:none;font-weight:700;}</style>
            </head>
            <body><div class="card"><h1>503</h1><p>تعذر الاتصال بقاعدة البيانات، يرجى المحاولة لاحقاً.</p><a href="/">المحاولة مرة أخرى</a></div></body>
            </html>
            """, 503
        
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
            return """
            <!DOCTYPE html>
            <html dir="rtl">
            <head><meta charset="UTF-8"><title>500 - خطأ في التهيئة</title>
            <style>body{font-family:Tajawal,sans-serif;background:#0b0e1a;color:#f8fafc;display:flex;align-items:center;justify-content:center;height:100vh;text-align:center;padding:20px;margin:0;}
            .card{background:rgba(20,30,48,0.6);backdrop-filter:blur(20px);border:1px solid rgba(255,255,255,0.1);border-radius:24px;padding:40px;max-width:500px;width:100%;}
            h1{font-size:48px;color:#38bdf8;margin:0 0 10px;}
            p{color:#94a3b8;font-size:18px;}
            a{display:inline-block;margin-top:16px;padding:12px 28px;background:#38bdf8;color:#0b0e1a;border-radius:14px;text-decoration:none;font-weight:700;}</style>
            </head>
            <body><div class="card"><h1>500</h1><p>خطأ في تهيئة قاعدة البيانات.</p><a href="/">المحاولة مرة أخرى</a></div></body>
            </html>
            """, 500

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

# ---------- Admin panel ----------
@app.route('/admin', methods=['GET', 'POST'])
def admin_panel():
    if request.method == 'POST' and request.form.get('password') == Config.ADMIN_PASSWORD:
        session['logged_in'] = True
        return redirect(url_for('admin_panel'))

    if session.get('logged_in'):
        try:
            categories = Category.query.order_by(Category.sort_order).all()
            library_items = PromptLibrary.query.order_by(PromptLibrary.created_at.desc()).all()
            library_ads = LibraryAd.query.order_by(LibraryAd.created_at.desc()).all()
            users = User.query.all()
            site_settings = SiteSetting.query.first()
            return render_template('admin.html',
                                   categories=categories,
                                   library_items=library_items,
                                   library_ads=library_ads,
                                   users=users,
                                   site_settings=site_settings,
                                   csrf_token=generate_csrf_token())
        except Exception as e:
            logger.error(f"Admin panel error: {e}")
            return render_template('error.html', error_code=500, message="خطأ في لوحة التحكم"), 500
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

@app.route('/admin/category/<int:category_id>/update', methods=['POST'])
@admin_required
def update_category(category_id):
    if not validate_csrf_token(request.form.get('csrf_token')):
        return "CSRF Error", 400
    cat = Category.query.get_or_404(category_id)
    cat.display_name = request.form.get('display_name', cat.display_name).strip()
    cat.icon = request.form.get('icon', cat.icon).strip()
    cat.sort_order = int(request.form.get('sort_order', cat.sort_order))
    db.session.commit()
    flash('تم تحديث التصنيف', 'success')
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
            publisher=request.form.get('publisher', '').strip() or None
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
    item.publisher = request.form.get('publisher', item.publisher) or None
    db.session.commit()
    flash('تم تحديث البرومبت', 'success')
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

# ---------- Health check ----------
@app.route('/health')
def health_check():
    return jsonify({'status': 'ok', 'timestamp': datetime.utcnow().isoformat()})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
