import os
import secrets
from datetime import datetime
from functools import wraps

from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    session,
    flash,
    jsonify,
)
from dotenv import load_dotenv

from config import Config
from models import db, Category, Prompt, Ad, SiteSetting

load_dotenv()


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    db.init_app(app)

    with app.app_context():
        db.create_all()
        _seed_if_empty()

    # ── Helpers ──────────────────────────────────────────────
    def admin_required(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if not session.get("admin_logged_in"):
                return redirect(url_for("admin_login"))
            return f(*args, **kwargs)

        return decorated

    def get_setting():
        s = SiteSetting.query.first()
        if not s:
            s = SiteSetting()
            db.session.add(s)
            db.session.commit()
        return s

    # ── Public routes ────────────────────────────────────────
    @app.route("/")
    def index():
        setting = get_setting()
        if setting.status == "off":
            return render_template(
                "offline.html",
                message=setting.offline_message,
                site_name=setting.site_name,
            )

        categories = Category.query.order_by(Category.sort_order).all()
        prompts = (
            Prompt.query.filter_by(is_active=True)
            .order_by(Prompt.created_at.desc())
            .all()
        )
        ads = (
            Ad.query.filter_by(is_active=True)
            .order_by(Ad.created_at.desc())
            .limit(5)
            .all()
        )
        total_copies = sum(p.copy_count or 0 for p in prompts)
        current_cat = request.args.get("cat", "all")

        return render_template(
            "index.html",
            categories=categories,
            prompts=prompts,
            ads=ads,
            total_copies=total_copies,
            current_cat=current_cat,
            site_name=setting.site_name,
            site_tagline=setting.site_tagline,
        )

    @app.route("/about")
    def about():
        setting = get_setting()
        return render_template(
            "about.html",
            site_name=setting.site_name,
            site_tagline=setting.site_tagline,
        )

    # ── API ──────────────────────────────────────────────────
    @app.route("/api/prompt/<int:pid>/copy", methods=["POST"])
    def api_copy(pid):
        p = Prompt.query.get_or_404(pid)
        p.copy_count = (p.copy_count or 0) + 1
        db.session.commit()
        return jsonify({"ok": True, "copy_count": p.copy_count})

    @app.route("/api/prompt/<int:pid>/like", methods=["POST"])
    def api_like(pid):
        p = Prompt.query.get_or_404(pid)
        p.likes = (p.likes or 0) + 1
        db.session.commit()
        return jsonify({"ok": True, "likes": p.likes})

    @app.route("/api/prompt/<int:pid>/share", methods=["POST"])
    def api_share(pid):
        p = Prompt.query.get_or_404(pid)
        p.share_count = (p.share_count or 0) + 1
        db.session.commit()
        return jsonify({"ok": True, "share_count": p.share_count})

    @app.route("/api/mandatory-ad")
    def api_mandatory_ad():
        ad = (
            Ad.query.filter_by(is_active=True, is_mandatory=True)
            .order_by(Ad.created_at.desc())
            .first()
        )
        if not ad:
            ad = (
                Ad.query.filter_by(is_active=True)
                .order_by(Ad.created_at.desc())
                .first()
            )
        if not ad:
            return jsonify({"ok": False}), 404
        return jsonify({"ok": True, "ad": ad.to_dict()})

    # ── Admin auth ───────────────────────────────────────────
    @app.route("/admin/login", methods=["GET", "POST"])
    def admin_login():
        if session.get("admin_logged_in"):
            return redirect(url_for("admin_dashboard"))
        error = None
        if request.method == "POST":
            pwd = request.form.get("password", "")
            if pwd == app.config["ADMIN_PASSWORD"]:
                session["admin_logged_in"] = True
                session.permanent = True
                return redirect(url_for("admin_dashboard"))
            error = "كلمة المرور غير صحيحة"
        return render_template("admin/login.html", error=error)

    @app.route("/admin/logout")
    def admin_logout():
        session.pop("admin_logged_in", None)
        return redirect(url_for("admin_login"))

    # ── Admin dashboard ──────────────────────────────────────
    @app.route("/admin")
    @admin_required
    def admin_dashboard():
        prompts = Prompt.query.order_by(Prompt.created_at.desc()).all()
        categories = Category.query.order_by(Category.sort_order).all()
        ads = Ad.query.order_by(Ad.created_at.desc()).all()
        setting = get_setting()
        stats = {
            "prompts": Prompt.query.count(),
            "copies": db.session.query(db.func.sum(Prompt.copy_count)).scalar() or 0,
            "likes": db.session.query(db.func.sum(Prompt.likes)).scalar() or 0,
            "categories": Category.query.count(),
            "ads": Ad.query.count(),
        }
        return render_template(
            "admin/dashboard.html",
            prompts=prompts,
            categories=categories,
            ads=ads,
            setting=setting,
            stats=stats,
        )

    # ── Admin: Prompts ───────────────────────────────────────
    @app.route("/admin/prompt/add", methods=["POST"])
    @admin_required
    def admin_add_prompt():
        p = Prompt(
            title=request.form.get("title", "").strip(),
            category=request.form.get("category", "general").strip(),
            image_url=request.form.get("image_url", "").strip(),
            prompt_text=request.form.get("prompt_text", "").strip(),
            publisher=request.form.get("publisher", "خيال").strip(),
            publisher_link=request.form.get("publisher_link", "").strip(),
            keywords=request.form.get("keywords", "").strip(),
        )
        if p.title and p.prompt_text:
            db.session.add(p)
            db.session.commit()
            flash("تمت إضافة البرومبت", "success")
        else:
            flash("العنوان والنص مطلوبان", "error")
        return redirect(url_for("admin_dashboard") + "#prompts")

    @app.route("/admin/prompt/<int:pid>/edit", methods=["POST"])
    @admin_required
    def admin_edit_prompt(pid):
        p = Prompt.query.get_or_404(pid)
        p.title = request.form.get("title", p.title).strip()
        p.category = request.form.get("category", p.category).strip()
        p.image_url = request.form.get("image_url", p.image_url).strip()
        p.prompt_text = request.form.get("prompt_text", p.prompt_text).strip()
        p.publisher = request.form.get("publisher", p.publisher).strip()
        p.publisher_link = request.form.get("publisher_link", p.publisher_link).strip()
        p.keywords = request.form.get("keywords", p.keywords).strip()
        p.is_active = request.form.get("is_active") == "1"
        db.session.commit()
        flash("تم تحديث البرومبت", "success")
        return redirect(url_for("admin_dashboard") + "#prompts")

    @app.route("/admin/prompt/<int:pid>/delete", methods=["POST"])
    @admin_required
    def admin_delete_prompt(pid):
        p = Prompt.query.get_or_404(pid)
        db.session.delete(p)
        db.session.commit()
        flash("تم حذف البرومبت", "success")
        return redirect(url_for("admin_dashboard") + "#prompts")

    # ── Admin: Categories ────────────────────────────────────
    @app.route("/admin/category/add", methods=["POST"])
    @admin_required
    def admin_add_category():
        name = request.form.get("name", "").strip().lower().replace(" ", "_")
        display = request.form.get("display_name", "").strip()
        icon = request.form.get("icon", "ph-tag").strip()
        order = int(request.form.get("sort_order", 0) or 0)
        if name and display:
            if Category.query.filter_by(name=name).first():
                flash("التصنيف موجود مسبقاً", "error")
            else:
                db.session.add(
                    Category(
                        name=name, display_name=display, icon=icon, sort_order=order
                    )
                )
                db.session.commit()
                flash("تمت إضافة التصنيف", "success")
        else:
            flash("الاسم والعرض مطلوبان", "error")
        return redirect(url_for("admin_dashboard") + "#categories")

    @app.route("/admin/category/<int:cid>/delete", methods=["POST"])
    @admin_required
    def admin_delete_category(cid):
        c = Category.query.get_or_404(cid)
        db.session.delete(c)
        db.session.commit()
        flash("تم حذف التصنيف", "success")
        return redirect(url_for("admin_dashboard") + "#categories")

    # ── Admin: Ads ───────────────────────────────────────────
    @app.route("/admin/ad/add", methods=["POST"])
    @admin_required
    def admin_add_ad():
        a = Ad(
            title=request.form.get("title", "").strip(),
            text=request.form.get("text", "").strip(),
            image_url=request.form.get("image_url", "").strip(),
            button_text=request.form.get("button_text", "زيارة").strip(),
            button_link=request.form.get("button_link", "").strip(),
            duration_seconds=int(request.form.get("duration_seconds", 5) or 5),
            is_mandatory=request.form.get("is_mandatory") == "1",
        )
        if a.title and a.button_link:
            db.session.add(a)
            db.session.commit()
            flash("تمت إضافة الإعلان", "success")
        else:
            flash("العنوان والرابط مطلوبان", "error")
        return redirect(url_for("admin_dashboard") + "#ads")

    @app.route("/admin/ad/<int:aid>/toggle", methods=["POST"])
    @admin_required
    def admin_toggle_ad(aid):
        a = Ad.query.get_or_404(aid)
        a.is_active = not a.is_active
        db.session.commit()
        flash("تم تحديث حالة الإعلان", "success")
        return redirect(url_for("admin_dashboard") + "#ads")

    @app.route("/admin/ad/<int:aid>/delete", methods=["POST"])
    @admin_required
    def admin_delete_ad(aid):
        a = Ad.query.get_or_404(aid)
        db.session.delete(a)
        db.session.commit()
        flash("تم حذف الإعلان", "success")
        return redirect(url_for("admin_dashboard") + "#ads")

    # ── Admin: Settings ──────────────────────────────────────
    @app.route("/admin/settings", methods=["POST"])
    @admin_required
    def admin_settings():
        s = get_setting()
        s.status = request.form.get("status", "on")
        s.offline_message = request.form.get("offline_message", s.offline_message)
        s.site_name = request.form.get("site_name", s.site_name).strip() or "خيال"
        s.site_tagline = request.form.get("site_tagline", s.site_tagline).strip()
        db.session.commit()
        flash("تم حفظ الإعدادات", "success")
        return redirect(url_for("admin_dashboard") + "#settings")

    return app


def _seed_if_empty():
    if Category.query.count() > 0:
        return

    cats = [
        ("writing", "كتابة", "ph-pencil-simple", 1),
        ("image", "صور", "ph-image", 2),
        ("code", "برمجة", "ph-code", 3),
        ("marketing", "تسويق", "ph-megaphone", 4),
        ("education", "تعليم", "ph-graduation-cap", 5),
        ("business", "أعمال", "ph-briefcase", 6),
        ("creative", "إبداع", "ph-palette", 7),
    ]
    for name, display, icon, order in cats:
        db.session.add(
            Category(name=name, display_name=display, icon=icon, sort_order=order)
        )

    samples = [
        (
            "كاتب محتوى احترافي",
            "writing",
            "أنت كاتب محتوى عربي محترف. اكتب مقالاً شاملاً ومفصلاً عن [الموضوع] بأسلوب سلس وجذاب، مع عناوين فرعية، ونقاط رئيسية، وخاتمة قوية. استخدم لغة عربية فصحى معاصرة تناسب الجمهور العام.",
            "كتابة مقال محتوى",
        ),
        (
            "مولد صور سينمائية",
            "image",
            "Cinematic still, [وصف المشهد], dramatic lighting, ultra detailed, 8k, shot on Arri Alexa, shallow depth of field, film grain, volumetric light, masterpiece --ar 16:9 --stylize 250",
            "ميدجورني صور سينما",
        ),
        (
            "مراجع كود بايثون",
            "code",
            "أنت خبير بايثون. راجع الكود التالي وحدد الأخطاء المحتملة، واقترح تحسينات للأداء والأمان والقابلية للقراءة. قدم الكود المحسّن مع تعليقات بالعربية:\n\n```python\n[ضع الكود هنا]\n```",
            "بايثون مراجعة كود",
        ),
        (
            "خطة تسويقية شاملة",
            "marketing",
            "أنشئ خطة تسويقية متكاملة لمنتج/خدمة: [الاسم]. تشمل: تحليل الجمهور المستهدف، نقاط البيع الفريدة، قنوات التسويق المناسبة للسوق العربي، محتوى أسبوعي لمدة شهر، ومؤشرات الأداء الرئيسية (KPIs).",
            "تسويق خطة",
        ),
        (
            "شرح مبسط للمفاهيم",
            "education",
            "اشرح مفهوم [المفهوم] بطريقة بسيطة جداً كما لو كنت تشرحه لطفل عمره 12 سنة. استخدم أمثلة من الحياة اليومية، وتشبيهات، وتجنب المصطلحات المعقدة. ثم قدم ملخصاً في 3 نقاط.",
            "تعليم شرح مبسط",
        ),
        (
            "محلل أعمال",
            "business",
            "أنت مستشار أعمال. حلّل فكرة المشروع التالية: [الوصف]. قدم: تحليل SWOT، نموذج الإيرادات المقترح، التكاليف التقديرية للبدء، والمخاطر الرئيسية مع طرق التخفيف.",
            "أعمال تحليل",
        ),
        (
            "كاتب قصص قصيرة",
            "creative",
            "اكتب قصة قصيرة بالعربية الفصحى حول [الفكرة]. الطول 500-700 كلمة. ركّز على بناء الشخصيات، والتشويق، ونهاية مفاجئة أو مؤثرة. استخدم وصفاً حسياً غنياً.",
            "قصة قصيرة إبداع",
        ),
        (
            "برومبت ميدجورني واقعي",
            "image",
            "Photorealistic portrait of [الوصف], natural soft lighting, 85mm lens, f/1.8, bokeh background, highly detailed skin texture, professional photography, 8k uhd --ar 3:4 --v 6",
            "ميدجورني بورتريه",
        ),
    ]
    for title, cat, text, kw in samples:
        db.session.add(
            Prompt(
                title=title,
                category=cat,
                prompt_text=text,
                keywords=kw,
                publisher="خيال",
            )
        )

    db.session.add(SiteSetting())
    db.session.commit()


app = create_app()

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
