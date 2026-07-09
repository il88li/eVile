import os
import logging
import bcrypt
import requests
import json
import traceback
import secrets
from datetime import datetime
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, Response
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_caching import Cache
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_talisman import Talisman
from sqlalchemy.exc import SQLAlchemyError
import threading

# تم حذف استيراد Category
from models import db, User, Pattern, Notification, Ad, SiteSetting
from config import Config

app = Flask(__name__)
app.config.from_object(Config)

db.init_app(app)
migrate = Migrate(app, db)
cache = Cache(app)
limiter = Limiter(get_remote_address, app=app, default_limits=["200 per day", "50 per hour"], storage_uri=app.config['RATELIMIT_STORAGE_URI'])
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

def hash_password(password): return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
def check_password(password, hashed): return bcrypt.checkpw(password.encode('utf-8'), hashed.encode('utf-8'))
def generate_csrf_token():
    if 'csrf_token' not in session:
        session['csrf_token'] = secrets.token_urlsafe(32)
    return session['csrf_token']
def validate_csrf_token(token):
    return token == session.get('csrf_token')
def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('logged_in'): return redirect(url_for('admin_panel'))
        return f(*args, **kwargs)
    return decorated

_db_initialized = False
@app.before_request
def ensure_db_initialized():
    global _db_initialized
    if not _db_initialized:
        with threading.Lock():
            if not _db_initialized:
                db.create_all()
                if not SiteSetting.query.first():
                    db.session.add(SiteSetting())
                    db.session.commit()
                _db_initialized = True
                logger.info("✅ Database initialized successfully.")

@app.route('/')
def index():
    site = SiteSetting.query.first()
    if site and site.status == 'off': return render_template('index.html', site_status='off', offline_message=site.offline_message)
    
    user_id = session.get('user_id')
    user_data_dict = None
    if user_id:
        user = User.query.get(user_id)
        if user:
            user.last_active = datetime.utcnow()
            db.session.commit()
            user_data_dict = {
                'username': user.username,
                'credits': user.credits,
                'last_daily_gift': user.last_daily_gift.isoformat() if user.last_daily_gift else None
            }
        
    patterns = Pattern.query.all()
    notif = Notification.query.filter_by(show_in_chat=True).order_by(Notification.created_at.desc()).first()
    ad = Ad.query.order_by(Ad.created_at.desc()).first()
    
    return render_template('index.html', 
                         patterns=patterns, 
                         user_id=user_id, 
                         latest_notification=notif, 
                         latest_ad=ad, 
                         user_data=user_data_dict, 
                         site_status='on')

@app.route('/sign')
def sign():
    return render_template('sign.html', csrf_token=generate_csrf_token())

@app.route('/api/signup', methods=['POST'])
@limiter.limit("5 per minute")
def signup():
    data = request.get_json()
    if not data.get('username') or not data.get('password'): return jsonify({'success': False, 'message': 'بيانات غير كاملة'}), 400
    if User.query.filter_by(username=data['username']).first(): return jsonify({'success': False, 'message': 'اسم المستخدم موجود مسبقاً'}), 400
    try:
        user = User(username=data['username'], password_hash=hash_password(data['password']))
        db.session.add(user); db.session.commit()
        session['user_id'] = user.id
        return jsonify({'success': True, 'message': 'تم التسجيل', 'credits': user.credits})
    except SQLAlchemyError as e:
        db.session.rollback(); logger.error(f"Signup DB error: {e}")
        return jsonify({'success': False, 'message': 'خطأ في قاعدة البيانات'}), 500

@app.route('/api/login', methods=['POST'])
@limiter.limit("10 per minute")
def login():
    data = request.get_json()
    user = User.query.filter_by(username=data.get('username')).first()
    if user and check_password(data.get('password'), user.password_hash):
        session['user_id'] = user.id
        return jsonify({'success': True, 'message': 'تم الدخول', 'credits': user.credits})
    return jsonify({'success': False, 'message': 'بيانات غير صحيحة'}), 401

@app.route('/api/user_info')
def user_info():
    user = User.query.get(session.get('user_id'))
    if not user: return jsonify(None)
    return jsonify({'username': user.username, 'credits': user.credits, 'last_daily_gift': str(user.last_daily_gift) if user.last_daily_gift else None})

@app.route('/api/update_user', methods=['POST'])
def update_user():
    user = User.query.get(session.get('user_id'))
    if not user: return jsonify({'success': False, 'message': 'غير مسجل'}), 401
    data = request.get_json()
    if user.username != data['username'] and User.query.filter_by(username=data['username']).first():
        return jsonify({'success': False, 'message': 'الاسم مستخدم'}), 400
    user.username = data['username']; user.password_hash = hash_password(data['password'])
    db.session.commit()
    return jsonify({'success': True, 'message': 'تم التحديث'})

@app.route('/api/claim_daily_gift', methods=['POST'])
def claim_daily_gift():
    user = User.query.get(session.get('user_id'))
    today = datetime.utcnow().date()
    if user.last_daily_gift == today: return jsonify({'success': False, 'message': 'أخذتها اليوم'}), 400
    user.credits += 3; user.last_daily_gift = today
    db.session.commit()
    return jsonify({'success': True, 'message': 'تم الحصول على 3 نقاط', 'credits': user.credits})

@app.route('/api/transfer_credits', methods=['POST'])
def transfer_credits():
    sender = User.query.get(session.get('user_id'))
    if not sender: return jsonify({'success': False, 'message': 'غير مسجل'}), 401
    data = request.get_json()
    receiver = User.query.filter_by(username=data.get('username')).first()
    amount = int(data.get('amount', 0))
    if not receiver: return jsonify({'success': False, 'message': 'المستخدم غير موجود'}), 404
    if receiver.id == sender.id: return jsonify({'success': False, 'message': 'لا يمكنك التحويل لنفسك'}), 400
    if sender.credits < amount: return jsonify({'success': False, 'message': 'رصيدك غير كافٍ'}), 400
    sender.credits -= amount; receiver.credits += amount
    db.session.commit()
    return jsonify({'success': True, 'message': f'تم تحويل {amount}', 'credits': sender.credits})

@app.route('/api/deduct_credit', methods=['POST'])
def deduct_credit():
    user = User.query.get(session.get('user_id'))
    if not user: return jsonify({'success': False, 'message': 'غير مسجل'}), 401
    if user.credits > 0:
        user.credits -= 1; db.session.commit()
        return jsonify({'success': True, 'credits': user.credits})
    return jsonify({'success': False, 'message': 'نفاذ الرصيد'}), 402

@app.route('/api/notifications')
@cache.cached(timeout=120)
def api_notifications():
    return jsonify([{'id': n.id, 'title': n.title, 'text': n.text, 'created_at': n.created_at.isoformat()} for n in Notification.query.order_by(Notification.id.desc()).all()])

@app.route('/api/chat', methods=['POST'])
@limiter.limit("30 per minute")
def api_chat():
    data = request.get_json()
    pattern_id = data.get('pattern_id'); message = data.get('message', '').strip()
    user = User.query.get(session.get('user_id'))
    if not user: return jsonify({'error': 'غير مسجل'}), 401
    if user.credits <= 0: return jsonify({'error': 'no_credits'}), 402
    pattern = Pattern.query.get(pattern_id)
    if not pattern: return jsonify({'error': 'النمط غير موجود'}), 404
    
    try:
        headers = {'Authorization': f'Bearer {app.config["OPENROUTER_API_KEY"]}', 'Content-Type': 'application/json', 'HTTP-Referer': request.url_root, 'X-Title': 'UFOQ'}
        payload = {'model': 'openrouter/auto', 'messages': [{'role': 'system', 'content': pattern.prompt}, {'role': 'user', 'content': message}], 'temperature': 0.7, 'stream': True}
        response = requests.post(app.config["OPENROUTER_URL"], json=payload, headers=headers, stream=True, timeout=30)
        response.raise_for_status()
        
        def generate():
            try:
                user.credits -= 1
                db.session.commit()
            except Exception as e:
                logger.error(f"DB credit deduction error: {e}")
            for chunk in response.iter_lines():
                if chunk:
                    decoded = chunk.decode('utf-8')
                    if decoded.startswith('data: '):
                        data_str = decoded[6:]
                        if data_str != '[DONE]':
                            try:
                                content = json.loads(data_str)['choices'][0]['delta'].get('content', '')
                                if content: yield content
                            except Exception as e:
                                logger.error(f"Stream decode error: {e}")
        return Response(generate(), mimetype='text/plain')
    except requests.RequestException as e:
        logger.error(f"OpenRouter API Exception: {e}")
        return jsonify({'error': 'خطأ في مزود الذكاء الاصطناعي'}), 502
    except Exception as e:
        logger.error(f"Unexpected chat error: {e}"); logger.error(traceback.format_exc())
        return jsonify({'error': 'خطأ داخلي'}), 500

@app.route('/admin', methods=['GET', 'POST'])
def admin_panel():
    if request.method == 'POST' and request.form.get('password') == Config.ADMIN_PASSWORD:
        session['logged_in'] = True
        return redirect(url_for('admin_panel'))
    if session.get('logged_in'):
        # تم حذف categories
        return render_template('admin.html', patterns=Pattern.query.all(), notifications=Notification.query.all(), ads=Ad.query.all(), users_count=User.query.count(), site_settings=SiteSetting.query.first(), csrf_token=generate_csrf_token())
    return render_template('admin.html')

@app.route('/admin/logout')
def logout(): session.clear(); return redirect(url_for('admin_panel'))

@app.route('/admin/pattern/add', methods=['POST'])
@admin_required
def add_pattern():
    if not validate_csrf_token(request.form.get('csrf_token')): return "CSRF Error", 400
    p = Pattern(name=request.form.get('name'), image_url=request.form.get('image_url'), prompt=request.form.get('prompt'))
    db.session.add(p); db.session.commit(); flash('تمت إضافة النمط', 'success')
    return redirect(url_for('admin_panel'))

@app.route('/admin/notification/add', methods=['POST'])
@admin_required
def add_notification():
    if not validate_csrf_token(request.form.get('csrf_token')): return "CSRF Error", 400
    n = Notification(title=request.form.get('title'), text=request.form.get('text'), duration_hours=request.form.get('duration_hours', 1), show_in_chat=request.form.get('show_in_chat') == 'on')
    db.session.add(n); db.session.commit(); flash('تم إرسال الإشعار', 'success')
    return redirect(url_for('admin_panel'))

@app.route('/api/admin/update_site_settings', methods=['POST'])
@admin_required
def update_site_settings():
    if not validate_csrf_token(request.json.get('csrf_token')): return jsonify({'error': 'CSRF Error'}), 400
    s = SiteSetting.query.first(); s.status = request.json.get('status'); s.offline_message = request.json.get('offline_message')
    db.session.commit(); return jsonify({'success': True})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
