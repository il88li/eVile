import os
import json
import logging
import bcrypt
import secrets
import base64
import requests
from datetime import datetime, date
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, Response
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_caching import Cache
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_talisman import Talisman
from flask_session import Session
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy import text

from models import db, User, Category, PromptLibrary, LibraryAd, SiteSetting, Notification
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

# ---------- Auto Migration (for free plans without shell) ----------
def auto_migrate():
    try:
        with app.app_context():
            # Add missing columns to prompt_library
            db.session.execute(text("ALTER TABLE prompt_library ADD COLUMN IF NOT EXISTS keywords VARCHAR(500)"))
            db.session.execute(text("ALTER TABLE prompt_library ADD COLUMN IF NOT EXISTS submitted_by VARCHAR(80)"))
            db.session.execute(text("ALTER TABLE prompt_library ADD COLUMN IF NOT EXISTS is_approved BOOLEAN DEFAULT true"))
            
            # Add missing columns to user table (quoted because "user" is reserved)
            db.session.execute(text('ALTER TABLE "user" ADD COLUMN IF NOT EXISTS credits INTEGER DEFAULT 10'))
            db.session.execute(text('ALTER TABLE "user" ADD COLUMN IF NOT EXISTS last_daily_gift DATE'))
            
            db.session.commit()
            logger.info("Auto-migration: columns added successfully.")
    except Exception as e:
        db.session.rollback()
        logger.warning(f"Auto-migration warning (columns may already exist): {e}")

# ---------- Database initialization ----------
_db_initialized = False
@app.before_request
def ensure_db_initialized():
    global _db_initialized
    if not _db_initialized:
        try:
            db.create_all()
            auto_migrate()  # Run auto-migration here
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
            logger.info("Database initialized.")
        except Exception as e:
            logger.error(f"DB init error: {e}")
            db.session.rollback()

# ---------- Public routes ----------
@app.route('/')
def index():
    site = SiteSetting.query.first()
    if site and site.status == 'off':
        return render_template('index.html', site_status='off', offline_message=site.offline_message)

    categories = Category.query.order_by(Category.sort_order).all()
    library_items = PromptLibrary.query.filter_by(is_approved=True).order_by(PromptLibrary.created_at.desc()).all()
    active_ad = LibraryAd.query.filter_by(is_active=True).order_by(LibraryAd.created_at.desc()).first()
    latest_notif = Notification.query.order_by(Notification.created_at.desc()).first()

    user_data = None
    user_id = session.get('user_id')
    if user_id:
        user = User.query.get(user_id)
        if user:
            user_data = {
                'username': user.username,
                'credits': user.credits or 0,
                'last_daily_gift': user.last_daily_gift.isoformat() if user.last_daily_gift else None
            }

    patterns = [{'id': c.id, 'name': c.display_name, 'image_url': f'https://placehold.co/400x200/111827/38bdf8?text={c.display_name}'} for c in categories]

    return render_template('index.html',
                           categories=categories,
                           library_items=library_items,
                           active_ad=active_ad,
                           site_status='on',
                           user_id=user_id,
                           user_data=user_data,
                           patterns=patterns,
                           latest_notification=latest_notif,
                           latest_ad=active_ad)

@app.route('/library')
def library():
    categories = Category.query.order_by(Category.sort_order).all()
    library_items = PromptLibrary.query.filter_by(is_approved=True).order_by(PromptLibrary.created_at.desc()).all()
    active_ad = LibraryAd.query.filter_by(is_active=True).order_by(LibraryAd.created_at.desc()).first()
    return render_template('library.html',
                           categories=categories,
                           library_items=library_items,
                           active_ad=active_ad,
                           user_id=session.get('user_id'))

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

    if action == 'signup':
        if User.query.filter_by(username=username).first():
            return jsonify({'success': False, 'message': 'اسم المستخدم موجود مسبقاً'}), 400
        user = User(username=username, password_hash=hash_password(password), credits=10)
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
    return render_template('settings.html', user=user, csrf_token=generate_csrf_token())

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
        logger.error(f"Image upload error: {e}")
        return jsonify({'success': False, 'message': 'خطأ في رفع الصورة'}), 500

# ---------- API: User Info ----------
@app.route('/api/user_info')
@login_required
def user_info():
    user = User.query.get(session['user_id'])
    if not user:
        return jsonify({'success': False}), 404
    return jsonify({
        'success': True,
        'username': user.username,
        'credits': user.credits or 0,
        'last_daily_gift': user.last_daily_gift.isoformat() if user.last_daily_gift else None
    })

@app.route('/api/update_user', methods=['POST'])
@login_required
def update_user():
    data = request.get_json()
    user = User.query.get(session['user_id'])
    if not user:
        return jsonify({'success': False, 'message': 'المستخدم غير موجود'}), 404

    username = data.get('username', '').strip()
    password = data.get('password', '').strip()

    if not username or not password:
        return jsonify({'success': False, 'message': 'يرجى ملء جميع الحقول'}), 400

    existing = User.query.filter(User.username == username, User.id != user.id).first()
    if existing:
        return jsonify({'success': False, 'message': 'اسم المستخدم مستخدم'}), 400

    user.username = username
    user.password_hash = hash_password(password)
    db.session.commit()
    return jsonify({'success': True, 'message': 'تم التحديث'})

@app.route('/api/claim_daily_gift', methods=['POST'])
@login_required
def claim_daily_gift():
    user = User.query.get(session['user_id'])
    if not user:
        return jsonify({'success': False, 'message': 'غير مسموح'}), 401

    today = date.today()
    if user.last_daily_gift == today:
        return jsonify({'success': False, 'message': 'لقد استلمت الهدية اليوم'}), 400

    user.credits = (user.credits or 0) + 3
    user.last_daily_gift = today
    db.session.commit()
    return jsonify({'success': True, 'message': 'تم استلام 3 نقاط', 'credits': user.credits})

@app.route('/api/transfer_credits', methods=['POST'])
@login_required
def transfer_credits():
    data = request.get_json()
    target_username = data.get('username', '').strip()
    amount = int(data.get('amount', 0))

    if amount <= 0:
        return jsonify({'success': False, 'message': 'المبلغ غير صالح'}), 400

    sender = User.query.get(session['user_id'])
    if not sender or (sender.credits or 0) < amount:
        return jsonify({'success': False, 'message': 'رصيدك غير كافٍ'}), 400

    receiver = User.query.filter_by(username=target_username).first()
    if not receiver:
        return jsonify({'success': False, 'message': 'المستخدم غير موجود'}), 404
    if receiver.id == sender.id:
        return jsonify({'success': False, 'message': 'لا يمكن التحويل لنفسك'}), 400

    sender.credits = (sender.credits or 0) - amount
    receiver.credits = (receiver.credits or 0) + amount
    db.session.commit()

    return jsonify({'success': True, 'message': f'تم تحويل {amount} نقاط', 'credits': sender.credits})

@app.route('/api/users_count')
def users_count():
    count = User.query.count()
    return jsonify({'count': count})

@app.route('/api/notifications')
def get_notifications():
    notifs = Notification.query.order_by(Notification.created_at.desc()).limit(20).all()
    return jsonify([{
        'id': n.id,
        'title': n.title,
        'text': n.text,
        'created_at': n.created_at.isoformat()
    } for n in notifs])

# ---------- AI Chat with OpenRouter ----------
@app.route('/api/chat', methods=['POST'])
@login_required
def chat():
    user = User.query.get(session['user_id'])
    if not user or (user.credits or 0) <= 0:
        return jsonify({'error': 'نقاط غير كافية'}), 402

    data = request.get_json()
    message = data.get('message', '').strip()
    pattern_id = data.get('pattern_id')

    if not message:
        return jsonify({'error': 'الرسالة فارغة'}), 400

    pattern = Category.query.get(pattern_id) if pattern_id else None
    system_prompt = "أنت مساعد إبداعي متخصص في كتابة البرومبتات والمحتوى باللغة العربية."
    if pattern:
        system_prompt += f" التصنيف المختار: {pattern.display_name}."

    api_key = app.config.get('OPENROUTER_API_KEY')
    model = app.config.get('OPENROUTER_MODEL', 'openrouter/auto')

    if not api_key:
        return jsonify({'error': 'مفتاح API غير مضبوط'}), 503

    def generate():
        try:
            resp = requests.post(
                url="https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://evile4.onrender.com",
                    "X-Title": "UFOQ"
                },
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": message}
                    ],
                    "stream": True
                },
                stream=True,
                timeout=60
            )

            if resp.status_code != 200:
                yield json.dumps({'error': 'فشل الاتصال بـ OpenRouter'})
                return

            full_text = ""
            for line in resp.iter_lines():
                if not line:
                    continue
                line = line.decode('utf-8')
                if line.startswith('data: '):
                    chunk = line[6:]
                    if chunk == '[DONE]':
                        break
                    try:
                        data_chunk = json.loads(chunk)
                        delta = data_chunk.get('choices', [{}])[0].get('delta', {}).get('content', '')
                        if delta:
                            full_text += delta
                            yield delta
                    except json.JSONDecodeError:
                        continue

            if full_text.strip():
                user.credits = max(0, (user.credits or 0) - 1)
                db.session.commit()
        except Exception as e:
            logger.error(f"Chat error: {e}")
            yield json.dumps({'error': 'خطأ في الاتصال بالذكاء الاصطناعي'})

    return Response(generate(), mimetype='text/plain')

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
        users = User.query.all()
        site_settings = SiteSetting.query.first()
        return render_template('admin.html',
                               categories=categories,
                               library_items=library_items,
                               library_ads=library_ads,
                               users=users,
                               site_settings=site_settings,
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
    try:
        item = PromptLibrary(
            title=request.form.get('title'),
            category=request.form.get('category', 'general'),
            image_url=request.form.get('image_url'),
            prompt_text=request.form.get('prompt_text'),
            publisher=request.form.get('publisher', '').strip() or None,
            keywords=request.form.get('keywords', '').strip() or None,
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
    item.publisher = request.form.get('publisher', item.publisher) or None
    item.keywords = request.form.get('keywords', item.keywords) or None
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

# ---------- Admin: Notifications ----------
@app.route('/admin/notification/add', methods=['POST'])
@admin_required
def add_notification():
    if not validate_csrf_token(request.form.get('csrf_token')):
        return "CSRF Error", 400
    try:
        n = Notification(
            title=request.form.get('title'),
            text=request.form.get('text')
        )
        db.session.add(n)
        db.session.commit()
        flash('تمت إضافة الإشعار', 'success')
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error adding notification: {e}")
        flash('خطأ في إضافة الإشعار', 'error')
    return redirect(url_for('admin_panel'))

@app.route('/admin/notification/<int:nid>/delete', methods=['POST'])
@admin_required
def delete_notification(nid):
    if not validate_csrf_token(request.form.get('csrf_token')):
        return "CSRF Error", 400
    n = Notification.query.get_or_404(nid)
    db.session.delete(n)
    db.session.commit()
    flash('تم حذف الإشعار', 'success')
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
