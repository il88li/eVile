from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(128), nullable=False)
    profile_image = db.Column(db.Text, nullable=True)
    custom_prompt = db.Column(db.Text, nullable=True)
    search_keywords = db.Column(db.String(500), nullable=True)
    credits = db.Column(db.Integer, default=10)
    last_daily_gift = db.Column(db.Date, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

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
    keywords = db.Column(db.String(500), nullable=True)
    publisher = db.Column(db.String(80), nullable=True)
    submitted_by = db.Column(db.String(80), nullable=True)
    is_approved = db.Column(db.Boolean, default=True)
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

class Notification(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    text = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
