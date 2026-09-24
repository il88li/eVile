# خيال — مكتبة البرومبتات العربية

موقع ويب كامل لمكتبة برومبتات الذكاء الاصطناعي بالعربية، مع لوحة تحكم إدارية.

## المميزات

- واجهة عامة عربية (RTL) بتصميم Glassmorphism داكن
- **شاشة تحميل** مخصصة بهوية خيال
- بحث + فلترة حسب التصنيف
- نسخ / إعجاب / مشاركة مع أصوات تفاعلية
- إعلان إلزامي اختياري قبل النسخ
- لوحة تحكم كاملة: برومبتات، تصنيفات، إعلانات، إعدادات
- وضع صيانة مع رسالة مخصصة
- بيانات أولية جاهزة عند أول تشغيل

## التشغيل السريع

```bash
# 1. استنساخ / نسخ الملفات
cd khayal

# 2. بيئة افتراضية
python3 -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate

# 3. التبعيات
pip install -r requirements.txt

# 4. إعداد البيئة
cp .env.example .env
# عدّل SECRET_KEY و ADMIN_PASSWORD في .env

# 5. تشغيل
python app.py
```

افتح: http://127.0.0.1:5000  
الإدارة: http://127.0.0.1:5000/admin/login  
كلمة المرور الافتراضية: `admin123`

## الهيكل

```
khayal/
├── app.py                 # التطبيق الرئيسي
├── config.py
├── models.py
├── requirements.txt
├── .env.example
├── .gitignore
├── README.md
├── templates/
│   ├── base.html          # يتضمن شاشة التحميل
│   ├── index.html
│   ├── about.html
│   ├── offline.html
│   └── admin/
│       ├── login.html
│       └── dashboard.html
└── static/
    ├── css/
    │   ├── style.css
    │   └── admin.css
    ├── js/
    │   └── app.js
    ├── images/
    │   └── favicon.svg
    └── sounds/
        ├── copy.wav
        ├── like.wav
        └── click.wav
```

## النشر على GitHub

```bash
git init
git add .
git commit -m "خيال — مكتبة البرومبتات العربية v1.0"
git branch -M main
git remote add origin https://github.com/YOUR_USER/khayal.git
git push -u origin main
```

## النشر (Render / Railway / VPS)

- عيّن متغيرات البيئة من `.env.example`
- أمر التشغيل: `gunicorn app:app`
- قاعدة البيانات الافتراضية SQLite (للإنتاج يُفضّل PostgreSQL عبر `DATABASE_URL`)

## كلمة مرور الإدارة

غيّرها فوراً في ملف `.env`:

```
ADMIN_PASSWORD=كلمة_قوية_جداً
```

---

**خيال** · الإصدار 1.0
