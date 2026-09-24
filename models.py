from datetime import datetime
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


class Category(db.Model):
    __tablename__ = "categories"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), nullable=False, unique=True)
    display_name = db.Column(db.String(100), nullable=False)
    icon = db.Column(db.String(50), default="ph-tag")
    sort_order = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "display_name": self.display_name,
            "icon": self.icon,
            "sort_order": self.sort_order,
        }


class Prompt(db.Model):
    __tablename__ = "prompts"
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    category = db.Column(db.String(50), nullable=False, default="general")
    image_url = db.Column(db.String(500), default="")
    prompt_text = db.Column(db.Text, nullable=False)
    publisher = db.Column(db.String(80), default="خيال")
    publisher_link = db.Column(db.String(500), default="")
    keywords = db.Column(db.Text, default="")
    copy_count = db.Column(db.Integer, default=0)
    share_count = db.Column(db.Integer, default=0)
    likes = db.Column(db.Integer, default=0)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "title": self.title,
            "category": self.category,
            "image_url": self.image_url or "",
            "prompt_text": self.prompt_text,
            "publisher": self.publisher or "خيال",
            "publisher_link": self.publisher_link or "",
            "keywords": self.keywords or "",
            "copy_count": self.copy_count or 0,
            "share_count": self.share_count or 0,
            "likes": self.likes or 0,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class Ad(db.Model):
    __tablename__ = "ads"
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    text = db.Column(db.Text, nullable=False)
    image_url = db.Column(db.String(500), default="")
    button_text = db.Column(db.String(100), default="زيارة")
    button_link = db.Column(db.String(500), nullable=False)
    duration_seconds = db.Column(db.Integer, default=5)
    is_active = db.Column(db.Boolean, default=True)
    is_mandatory = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "title": self.title,
            "text": self.text,
            "image_url": self.image_url or "",
            "button_text": self.button_text or "زيارة",
            "button_link": self.button_link,
            "duration_seconds": self.duration_seconds or 5,
            "is_active": self.is_active,
            "is_mandatory": self.is_mandatory,
        }


class SiteSetting(db.Model):
    __tablename__ = "site_settings"
    id = db.Column(db.Integer, primary_key=True)
    status = db.Column(db.String(10), default="on")
    offline_message = db.Column(
        db.Text, default="الموقع تحت الصيانة حالياً. نعود قريباً."
    )
    site_name = db.Column(db.String(100), default="خيال")
    site_tagline = db.Column(
        db.String(200), default="مكتبة البرومبتات العربية"
    )
