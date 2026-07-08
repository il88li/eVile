import os
import logging
import requests
import time
import json
from datetime import datetime, timedelta
from functools import wraps
from contextlib import contextmanager
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, Response

import psycopg2
from psycopg2.extras import RealDictCursor

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'evile-secret-key-2026')
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=7)

ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD', 'evile2026')
OPENROUTER_API_KEY = os.getenv('OPENROUTER_API_KEY', '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')
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DATABASE_URL = "postgresql://evile_site_user:yxWlZVZsC39DhRtXoY7e84ci6NTJgcaR@dpg-d8mpl3rsq97s739pscq0-a.oregon-postgres.render.com/evile_site"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

_characters_cache = {'data': None, 'timestamp': 0}
CACHE_TTL = 300

@contextmanager
def get_db():
    conn = None
    cur = None
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor(cursor_factory=RealDictCursor)
        yield cur
        conn.commit()
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"Database error: {str(e)}")
        raise
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()

def ensure_notification_columns(cur):
    cur.execute("""
        SELECT column_name 
        FROM information_schema.columns 
        WHERE table_name='notifications' AND column_name='duration_hours'
    """)
    if not cur.fetchone():
        cur.execute("ALTER TABLE notifications ADD COLUMN duration_hours INTEGER DEFAULT 1")
    
    cur.execute("""
        SELECT column_name 
        FROM information_schema.columns 
        WHERE table_name='notifications' AND column_name='show_in_chat'
    """)
    if not cur.fetchone():
        cur.execute("ALTER TABLE notifications ADD COLUMN show_in_chat BOOLEAN DEFAULT FALSE")

def ensure_ads_table(cur):
    cur.execute('''CREATE TABLE IF NOT EXISTS ads (
        id SERIAL PRIMARY KEY,
        title TEXT NOT NULL,
        text TEXT NOT NULL,
        button_text TEXT NOT NULL,
        button_link TEXT NOT NULL,
        duration_seconds INTEGER DEFAULT 5,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')

def ensure_users_table(cur):
    cur.execute('''CREATE TABLE IF NOT EXISTS users (
        id SERIAL PRIMARY KEY,
        username TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        credits INTEGER DEFAULT 10,
        last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    
    # إضافة عمود الهدية اليومية
    cur.execute("""
        SELECT column_name 
        FROM information_schema.columns 
        WHERE table_name='users' AND column_name='last_daily_gift'
    """)
    if not cur.fetchone():
        cur.execute("ALTER TABLE users ADD COLUMN last_daily_gift DATE")
        logger.info("✅ Added column 'last_daily_gift' to users table")

def drop_old_telegram_id_column(cur):
    cur.execute("""
        SELECT column_name 
        FROM information_schema.columns 
        WHERE table_name='users' AND column_name='telegram_id'
    """)
    if cur.fetchone():
        cur.execute("ALTER TABLE users DROP COLUMN telegram_id")
        logger.info("✅ Dropped obsolete column 'telegram_id' from users table")

def init_db():
    try:
        with get_db() as cur:
            cur.execute('''CREATE TABLE IF NOT EXISTS characters (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT NOT NULL,
                prompt TEXT NOT NULL,
                callback_key TEXT UNIQUE NOT NULL,
                logo_url TEXT DEFAULT ''
            )''')
            cur.execute('''CREATE TABLE IF NOT EXISTS notifications (
                id SERIAL PRIMARY KEY,
                title TEXT NOT NULL,
                text TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )''')
            
            ensure_notification_columns(cur)
            ensure_ads_table(cur)
            ensure_users_table(cur)
            drop_old_telegram_id_column(cur)
            
            logger.info("Database initialized/updated successfully")
            print("✅ Database tables ensured successfully.")
    except Exception as e:
        logger.error(f"Database initialization error: {e}")
        print(f"❌ Critical DB Init Error: {e}")
        raise

def update_user_activity(user_id):
    if not user_id: return
    try:
        with get_db() as cur:
            cur.execute("UPDATE users SET last_active = CURRENT_TIMESTAMP WHERE id = %s", (user_id,))
    except Exception as e:
        logger.error(f"Update activity error: {e}")

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect(url_for('admin_panel'))
        return f(*args, **kwargs)
    return decorated

@app.route('/')
def index():
    user_id = session.get('user_id')
    if user_id: update_user_activity(user_id)
    user_data = None
    try:
        with get_db() as cur:
            cur.execute('SELECT * FROM characters ORDER BY id')
            characters = cur.fetchall()
            cur.execute('SELECT * FROM notifications WHERE show_in_chat = true ORDER BY created_at DESC LIMIT 1')
            latest_notification = cur.fetchone()
            cur.execute('SELECT * FROM ads ORDER BY created_at DESC LIMIT 1')
            latest_ad = cur.fetchone()
            
            if user_id:
                cur.execute('SELECT username, credits, last_daily_gift FROM users WHERE id=%s', (user_id,))
                user_data = cur.fetchone()
    except Exception as e:
        logger.error(f"Index error: {e}")
        characters = []
        latest_notification = None
        latest_ad = None
        user_data = None
        
    return render_template('index.html',
                         characters=characters,
                         user_id=user_id,
                         latest_notification=latest_notification,
                         latest_ad=latest_ad,
                         user_data=user_data)

@app.route('/sign')
def sign():
    return render_template('sign.html')

@app.route('/api/signup', methods=['POST'])
def signup():
    data = request.json
    username = data.get('username')
    password = data.get('password')
    
    if not username or not password:
        return jsonify({'success': False, 'message': 'بيانات غير كاملة'}), 400
    
    try:
        with get_db() as cur:
            cur.execute("SELECT id FROM users WHERE username=%s", (username,))
            if cur.fetchone():
                return jsonify({'success': False, 'message': 'اسم المستخدم موجود مسبقاً'}), 400
                
            cur.execute("INSERT INTO users (username, password, credits) VALUES (%s, %s, %s) RETURNING id",
                        (username, password, 10))
            row = cur.fetchone()
            session['user_id'] = row['id']
        return jsonify({'success': True, 'message': 'تم التسجيل بنجاح! مرحباً بك.', 'credits': 10})
    except Exception as e:
        logger.error(f"Signup error: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/login', methods=['POST'])
def login():
    data = request.json
    username = data.get('username')
    password = data.get('password')
    
    if not username or not password:
        return jsonify({'success': False, 'message': 'بيانات ناقصة'}), 400
    
    try:
        with get_db() as cur:
            cur.execute("SELECT id, credits FROM users WHERE username=%s AND password=%s", (username, password))
            user = cur.fetchone()
            
            if user:
                session['user_id'] = user['id']
                return jsonify({'success': True, 'message': 'تم تسجيل الدخول بنجاح', 'credits': user['credits']})
            else:
                return jsonify({'success': False, 'message': 'بيانات الدخول غير صحيحة'}), 401
    except Exception as e:
        logger.error(f"Login error: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/user_info')
def user_info():
    user_id = session.get('user_id')
    if not user_id:
        return jsonify(None)
    try:
        with get_db() as cur:
            cur.execute("SELECT username, credits, last_daily_gift FROM users WHERE id=%s", (user_id,))
            user = cur.fetchone()
        return jsonify(user)
    except Exception as e:
        logger.error(f"User info error: {e}")
        return jsonify(None)

@app.route('/api/update_user', methods=['POST'])
def update_user():
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({'success': False, 'message': 'غير مسجل'}), 401
    
    data = request.json
    new_username = data.get('username')
    new_password = data.get('password')
    
    if not new_username or not new_password:
        return jsonify({'success': False, 'message': 'بيانات ناقصة'}), 400
        
    try:
        with get_db() as cur:
            cur.execute("SELECT id FROM users WHERE username=%s AND id!=%s", (new_username, user_id))
            if cur.fetchone():
                return jsonify({'success': False, 'message': 'اسم المستخدم مستخدم بالفعل'}), 400
                
            cur.execute("UPDATE users SET username=%s, password=%s WHERE id=%s", 
                        (new_username, new_password, user_id))
        return jsonify({'success': True, 'message': 'تم تحديث البيانات'})
    except Exception as e:
        logger.error(f"Update user error: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/claim_daily_gift', methods=['POST'])
def claim_daily_gift():
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({'success': False, 'message': 'غير مسجل'}), 401
        
    try:
        with get_db() as cur:
            # التحقق مما إذا كان قد حصل على الهدية اليوم
            cur.execute("SELECT last_daily_gift FROM users WHERE id=%s", (user_id,))
            user = cur.fetchone()
            today = datetime.now().date()
            
            if user and user['last_daily_gift'] == today:
                return jsonify({'success': False, 'message': 'لقد حصلت على هديتك اليومية بالفعل!'}), 400
            
            # إضافة 3 نقاط وتحديث التاريخ
            cur.execute("UPDATE users SET credits = credits + 3, last_daily_gift = %s WHERE id=%s", (today, user_id))
            cur.execute("SELECT credits FROM users WHERE id=%s", (user_id,))
            updated = cur.fetchone()
            
            return jsonify({'success': True, 'message': 'تم الحصول على 3 نقاط!', 'credits': updated['credits']})
    except Exception as e:
        logger.error(f"Claim daily gift error: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/transfer_credits', methods=['POST'])
def transfer_credits():
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({'success': False, 'message': 'غير مسجل'}), 401
        
    data = request.json
    target_username = data.get('username')
    amount = data.get('amount', 0)
    
    if not target_username or amount <= 0:
        return jsonify({'success': False, 'message': 'بيانات غير صحيحة'}), 400
        
    try:
        with get_db() as cur:
            # التحقق من وجود المستخدم الحالي ورصيده
            cur.execute("SELECT credits FROM users WHERE id=%s", (user_id,))
            sender = cur.fetchone()
            if not sender or sender['credits'] < amount:
                return jsonify({'success': False, 'message': 'رصيدك غير كافٍ'}), 400
                
            # التحقق من وجود المستخدم المستلم
            cur.execute("SELECT id FROM users WHERE username=%s", (target_username,))
            receiver = cur.fetchone()
            if not receiver:
                return jsonify({'success': False, 'message': 'اسم المستخدم غير موجود'}), 404
                
            if receiver['id'] == user_id:
                return jsonify({'success': False, 'message': 'لا يمكنك تحويل النقاط لنفسك'}), 400
                
            # إجراء عملية التحويل
            cur.execute("UPDATE users SET credits = credits - %s WHERE id=%s", (amount, user_id))
            cur.execute("UPDATE users SET credits = credits + %s WHERE id=%s", (amount, receiver['id']))
            
            # جلب الرصيد الجديد للمرسل
            cur.execute("SELECT credits FROM users WHERE id=%s", (user_id,))
            updated = cur.fetchone()
            
            return jsonify({'success': True, 'message': f'تم تحويل {amount} نقاط إلى {target_username} بنجاح', 'credits': updated['credits']})
    except Exception as e:
        logger.error(f"Transfer credits error: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/deduct_credit', methods=['POST'])
def deduct_credit():
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({'success': False, 'message': 'غير مسجل'}), 401
        
    try:
        with get_db() as cur:
            cur.execute("UPDATE users SET credits = credits - 1 WHERE id=%s AND credits > 0", (user_id,))
            cur.execute("SELECT credits FROM users WHERE id=%s", (user_id,))
            updated = cur.fetchone()
            return jsonify({'success': True, 'credits': updated['credits'] if updated else 0})
    except Exception as e:
        logger.error(f"Deduct credit error: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/active_users')
def api_active_users():
    try:
        with get_db() as cur:
            cur.execute("SELECT COUNT(*) FROM users WHERE last_active > NOW() - INTERVAL '5 minutes'")
            row = cur.fetchone()
            count = row['count'] if row else 0
        return jsonify({'count': count})
    except Exception as e:
        logger.error(f"Active users error: {e}")
        return jsonify({'count': 0})

@app.route('/health')
def health_check():
    try:
        with get_db() as cur:
            cur.execute("SELECT 1")
            row = cur.fetchone()
            db_ok = row is not None
        return jsonify({
            'status': 'healthy' if db_ok else 'unhealthy',
            'database': 'connected' if db_ok else 'disconnected',
            'timestamp': datetime.now().isoformat()
        })
    except Exception as e:
        return jsonify({'status': 'unhealthy', 'error': str(e)}), 500

@app.route('/admin', methods=['GET', 'POST'])
def admin_panel():
    if request.method == 'POST':
        if request.form.get('password') == ADMIN_PASSWORD:
            session['logged_in'] = True
            return redirect(url_for('admin_panel'))
        flash('كلمة المرور غير صحيحة', 'error')
    
    if session.get('logged_in'):
        try:
            with get_db() as cur:
                cur.execute('SELECT * FROM characters ORDER BY id DESC')
                characters = cur.fetchall()
                cur.execute('SELECT * FROM notifications ORDER BY id DESC')
                notifications = cur.fetchall()
                cur.execute('SELECT * FROM ads ORDER BY id DESC')
                ads = cur.fetchall()
                cur.execute('SELECT COUNT(*) FROM users')
                row = cur.fetchone()
                users_count = row['count'] if row else 0
        except Exception as e:
            logger.error(f"Admin panel data error: {e}")
            characters, notifications, ads, users_count = [], [], [], 0
        return render_template('admin.html', characters=characters, notifications=notifications, ads=ads, users_count=users_count)
    
    return render_template('admin.html')

@app.route('/admin/logout')
def logout():
    session.clear()
    return redirect(url_for('admin_panel'))

@app.route('/admin/character/add', methods=['POST'])
@admin_required
def add_character():
    name = request.form.get('name')
    description = request.form.get('description')
    prompt = request.form.get('prompt')
    callback_key = request.form.get('callback_key', name.lower().replace(' ', '_'))
    logo_url = request.form.get('logo_url', '')
    if name and description and prompt:
        try:
            with get_db() as cur:
                cur.execute("INSERT INTO characters (name, description, prompt, callback_key, logo_url) VALUES (%s, %s, %s, %s, %s)",
                    (name, description, prompt, callback_key, logo_url))
            flash('تمت إضافة الشخصية بنجاح', 'success')
        except Exception as e:
            flash('مفتاح الشخصية موجود مسبقاً' if 'unique' in str(e).lower() else str(e), 'error')
    return redirect(url_for('admin_panel'))

@app.route('/admin/character/<int:char_id>/edit', methods=['POST'])
@admin_required
def edit_character(char_id):
    name = request.form.get('name')
    description = request.form.get('description')
    prompt = request.form.get('prompt')
    logo_url = request.form.get('logo_url', '')
    if name and description and prompt:
        try:
            with get_db() as cur:
                cur.execute("UPDATE characters SET name=%s, description=%s, prompt=%s, logo_url=%s WHERE id=%s",
                    (name, description, prompt, logo_url, char_id))
            flash('تم تعديل الشخصية بنجاح', 'success')
        except Exception as e:
            flash(str(e), 'error')
    return redirect(url_for('admin_panel'))

@app.route('/admin/character/<int:char_id>/delete')
@admin_required
def delete_character(char_id):
    try:
        with get_db() as cur:
            cur.execute("DELETE FROM characters WHERE id=%s", (char_id,))
        flash('تم حذف الشخصية', 'success')
    except Exception as e:
        flash(str(e), 'error')
    return redirect(url_for('admin_panel'))

@app.route('/admin/notification/add', methods=['POST'])
@admin_required
def add_notification():
    title = request.form.get('title')
    text = request.form.get('text')
    duration_hours = request.form.get('duration_hours', 1, type=int)
    show_in_chat = request.form.get('show_in_chat') == 'on'
    if title and text:
        try:
            with get_db() as cur:
                cur.execute(
                    "INSERT INTO notifications (title, text, duration_hours, show_in_chat) VALUES (%s, %s, %s, %s)",
                    (title, text, duration_hours, show_in_chat)
                )
            flash('تم إرسال الإشعار بنجاح', 'success')
        except Exception as e:
            flash(str(e), 'error')
    return redirect(url_for('admin_panel'))

@app.route('/admin/notification/<int:notif_id>/delete')
@admin_required
def delete_notification(notif_id):
    try:
        with get_db() as cur:
            cur.execute("DELETE FROM notifications WHERE id=%s", (notif_id,))
        flash('تم حذف الإشعار', 'success')
    except Exception as e:
        flash(str(e), 'error')
    return redirect(url_for('admin_panel'))

@app.route('/admin/ad/add', methods=['POST'])
@admin_required
def add_ad():
    title = request.form.get('title')
    text = request.form.get('text')
    button_text = request.form.get('button_text')
    button_link = request.form.get('button_link')
    duration_seconds = request.form.get('duration_seconds', 5, type=int)
    
    if title and text and button_text and button_link:
        try:
            with get_db() as cur:
                cur.execute(
                    "INSERT INTO ads (title, text, button_text, button_link, duration_seconds) VALUES (%s, %s, %s, %s, %s)",
                    (title, text, button_text, button_link, duration_seconds)
                )
            flash('تم نشر الإعلان بنجاح', 'success')
        except Exception as e:
            flash(str(e), 'error')
    return redirect(url_for('admin_panel'))

@app.route('/admin/ad/<int:ad_id>/delete')
@admin_required
def delete_ad(ad_id):
    try:
        with get_db() as cur:
            cur.execute("DELETE FROM ads WHERE id=%s", (ad_id,))
        flash('تم حذف الإعلان', 'success')
    except Exception as e:
        flash(str(e), 'error')
    return redirect(url_for('admin_panel'))

@app.route('/api/admin/update_credits', methods=['POST'])
@admin_required
def update_credits():
    data = request.json
    username = data.get('username')
    amount = data.get('amount', 1)
    
    if not username or amount <= 0:
        return jsonify({'success': False, 'message': 'بيانات غير صحيحة'}), 400
        
    try:
        with get_db() as cur:
            cur.execute("UPDATE users SET credits = credits + %s WHERE username = %s", (amount, username))
            if cur.rowcount == 0:
                return jsonify({'success': False, 'message': 'اسم المستخدم غير موجود'}), 404
        return jsonify({'success': True, 'message': f'تم شحن {amount} نقطة للمستخدم {username} بنجاح'})
    except Exception as e:
        logger.error(f"Update credits error: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/characters')
def api_characters():
    now = time.time()
    if _characters_cache['data'] and (now - _characters_cache['timestamp']) < CACHE_TTL:
        return jsonify(_characters_cache['data'])
    try:
        with get_db() as cur:
            cur.execute('SELECT * FROM characters ORDER BY id')
            data = cur.fetchall()
        _characters_cache['data'] = data
        _characters_cache['timestamp'] = now
        return jsonify(data)
    except Exception as e:
        logger.error(f"API characters error: {e}")
        return jsonify([])

@app.route('/api/notifications')
def api_notifications():
    try:
        with get_db() as cur:
            cur.execute('SELECT * FROM notifications ORDER BY id DESC')
            return jsonify(cur.fetchall())
    except Exception as e:
        logger.error(f"API notifications error: {e}")
        return jsonify([])

@app.route('/api/chat', methods=['POST'])
def api_chat():
    data = request.json
    character_key = data.get('character', 'logo_maker')
    message = data.get('message', '')
    user_id = session.get('user_id')
    
    if not user_id:
        return jsonify({'error': 'غير مسجل'}), 401
        
    try:
        with get_db() as cur:
            cur.execute("SELECT credits FROM users WHERE id=%s", (user_id,))
            user = cur.fetchone()
            if not user or user['credits'] <= 0:
                return jsonify({'error': 'no_credits'}), 402
    except Exception as e:
        logger.error(f"Check credits error: {e}")
        return jsonify({'error': 'Database error'}), 500
        
    try:
        with get_db() as cur:
            cur.execute("SELECT * FROM characters WHERE callback_key=%s", (character_key,))
            character = cur.fetchone()
    except Exception as e:
        logger.error(f"Get character error: {e}")
        return jsonify({'error': 'Database connection error: ' + str(e)}), 500
    if not character:
        return jsonify({'error': 'Character not found'}), 404
    
    headers = {
        'Authorization': f'Bearer {OPENROUTER_API_KEY}',
        'Content-Type': 'application/json',
        'HTTP-Referer': request.url_root,
        'X-Title': 'EVILE'
    }
    payload = {
        'model': 'openrouter/auto',
        'messages': [
            {'role': 'system', 'content': character['prompt']},
            {'role': 'user', 'content': message}
        ],
        'temperature': 0.7,
        'stream': True
    }
    
    def generate():
        try:
            response = requests.post(OPENROUTER_URL, json=payload, headers=headers, stream=True, timeout=30)
            response.raise_for_status()
            for chunk in response.iter_lines():
                if chunk:
                    decoded = chunk.decode('utf-8')
                    if decoded.startswith('data: '):
                        data_str = decoded[6:]
                        if data_str != '[DONE]':
                            try:
                                json_data = json.loads(data_str)
                                content = json_data['choices'][0]['delta'].get('content', '')
                                if content:
                                    yield content
                            except Exception as e:
                                logger.error(f"JSON decode error in stream: {e}")
        except requests.exceptions.RequestException as e:
            logger.error(f"OpenRouter API Request Exception: {str(e)}")
            if hasattr(e, 'response') and e.response is not None:
                logger.error(f"Status Code: {e.response.status_code}")
                logger.error(f"Response Body: {e.response.text}")
            yield f"خطأ في الاتصال بـ OpenRouter (تفاصيل الخطأ موجودة في السجلات)"
        except Exception as e:
            logger.error(f"Unexpected error in OpenRouter stream: {e}")
            yield f"حدث خطأ غير متوقع: {str(e)}"
    
    return Response(generate(), mimetype='text/plain')

print("🚀 Starting EVILE Application...")
try:
    init_db()
except Exception as e:
    print(f"❌ FATAL ERROR: Database initialization failed! {e}")

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', 5000)), debug=False)
