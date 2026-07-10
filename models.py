from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(128), nullable=False)
    credits = db.Column(db.Integer, default=10)
    last_daily_gift = db.Column(db.Date)
    last_active = db.Column(db.DateTime, default=datetime.utcnow)

class Pattern(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    image_url = db.Column(db.String(500), nullable=False)
    prompt = db.Column(db.Text, nullable=False)

class Category(db.Model):
    """التصنيفات الديناميكية التي يتحكم فيها الأدمن"""
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), nullable=False)
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
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class LibraryAd(db.Model):
    """إعلانات المكتبة المنبثقة"""
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    text = db.Column(db.Text, nullable=False)
    image_url = db.Column(db.String(500), nullable=True)
    button_text = db.Column(db.String(100), nullable=False)
    button_link = db.Column(db.String(500), nullable=False)
    duration_seconds = db.Column(db.Integer, default=5)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Notification(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(100), nullable=False)
    text = db.Column(db.Text, nullable=False)
    duration_hours = db.Column(db.Integer, default=1)
    show_in_chat = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Ad(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(100), nullable=False)
    text = db.Column(db.Text, nullable=False)
    button_text = db.Column(db.String(50), nullable=False)
    button_link = db.Column(db.String(500), nullable=False)
    duration_seconds = db.Column(db.Integer, default=5)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class SiteSetting(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    status = db.Column(db.String(10), default='on')
    offline_message = db.Column(db.Text, default='الموقع تحت الصيانة حالياً.')

class PageVisit(db.Model):
    """عداد زيارات يومي مُستخدم لحساب متوسط الزيارات في لوحة الإحصائيات"""
    id = db.Column(db.Integer, primary_key=True)
    visit_date = db.Column(db.Date, unique=True, nullable=False)
    count = db.Column(db.Integer, default=0)

class AdView(db.Model):
    """سجل مشاهدات الإعلان (المدة الفعلية بالثواني) لحساب ساعات المشاهدة"""
    id = db.Column(db.Integer, primary_key=True)
    ad_id = db.Column(db.Integer, nullable=True)
    viewed_seconds = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
