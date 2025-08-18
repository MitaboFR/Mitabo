from flask import (
    Flask,
    request,
    render_template_string,
    url_for,
    redirect,
    send_from_directory,
    abort,
    jsonify,
    flash,
)
from flask_sqlalchemy import SQLAlchemy
from flask_login import (
    LoginManager,
    UserMixin,
    login_user,
    login_required,
    logout_user,
    current_user,
)
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
import subprocess
import shutil
import os

# -------------------------
# Config & bootstrap
# -------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
HLS_DIR = os.path.join(UPLOAD_DIR, "hls")
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(HLS_DIR, exist_ok=True)

app = Flask(__name__)
app.config.update(
    SQLALCHEMY_DATABASE_URI="sqlite:///mitabo.db",
    SQLALCHEMY_TRACK_MODIFICATIONS=False,
    SECRET_KEY="dev-mitabo",  # à remplacer en prod
    MAX_CONTENT_LENGTH=1024 * 1024 * 1024,  # 1GB
)

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"

# -------------------------
# Modèles
# -------------------------
class User(UserMixin, db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, index=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    display_name = db.Column(db.String(120), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def set_password(self, raw):
        self.password_hash = generate_password_hash(raw)

    def check_password(self, raw) -> bool:
        return check_password_hash(self.password_hash, raw)

class Video(db.Model):
    __tablename__ = "videos"
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, default="")
    category = db.Column(db.String(40), default="tendance", index=True)
    filename = db.Column(db.String(255), nullable=True)  # original/MP4 local
    external_url = db.Column(db.String(500), nullable=True)
    thumb_url = db.Column(db.String(500), nullable=True)
    duration = db.Column(db.String(20), default="")
    creator = db.Column(db.String(80), default="Anonyme")
    views = db.Column(db.Integer, default=0)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    # HLS
    hls_manifest = db.Column(db.String(500), nullable=True)  # chemin relatif /hls/.../master.m3u8

    @property
    def source_url(self):
        if self.hls_manifest:
            return url_for("hls", filename=self.hls_manifest, _external=False)
        if self.filename:
            return url_for("media", filename=self.filename, _external=False)
        return self.external_url or ""

class Comment(db.Model):
    __tablename__ = "comments"
    id = db.Column(db.Integer, primary_key=True)
    video_id = db.Column(db.Integer, db.ForeignKey("videos.id"), index=True, nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), index=True, nullable=False)
    body = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

# -------------------------
# Données constantes
# -------------------------
CATEGORIES = [
    {"id": "tendance", "label": "Tendances"},
    {"id": "jeux", "label": "Jeux"},
    {"id": "musique", "label": "Musique"},
    {"id": "film", "label": "Films & Anim"},
]
CATEGORIES_MAP = {c["id"]: c for c in CATEGORIES}
ALLOWED_EXTENSIONS = {"mp4", "webm", "ogg", "mov", "m4v"}

# -------------------------
# Init DB + Seed
# -------------------------
@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

with app.app_context():
    db.create_all()
    if User.query.count() == 0:
        u = User(email="demo@mitabo.dev", display_name="Demo")
        u.set_password("demo1234")
        db.session.add(u)
        db.session.commit()
    if Video.query.count() == 0:
        demo = Video(
            title="Big Buck Bunny — Démo",
            description="Vidéo de démonstration pour Mitabo.",
            category="film",
            external_url="https://commondatastorage.googleapis.com/gtv-videos-bucket/sample/BigBuckBunny.mp4",
            thumb_url="https://picsum.photos/seed/mitabo-demo/640/360",
            duration="10:34",
            creator="Mitabo",
            user_id=User.query.first().id,
        )
        db.session.add(demo)
        db.session.commit()

# -------------------------
# Utils
# -------------------------

def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def ffmpeg_exists() -> bool:
    return shutil.which("ffmpeg") is not None


def transcode_to_hls(input_path: str, target_dir: str) -> str:
    """Transcode en HLS multi-qualité (360p, 720p). Retourne chemin relatif du master.m3u8.
    Nécessite ffmpeg installé sur la machine.
    """
    os.makedirs(target_dir, exist_ok=True)
    master_path = os.path.join(target_dir, "master.m3u8")

    # Commande ffmpeg simple ABR (2 renditions)
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        input_path,
        # 360p
        "-filter:v:0",
        "scale=w=640:h=360:force_original_aspect_ratio=decrease",
        "-c:a:0",
        "aac",
        "-ar:0",
        "48000",
        "-c:v:0",
        "h264",
        "-profile:v:0",
        "main",
        "-crf:0",
        "23",
        "-sc_threshold",
        "0",
        "-g",
        "48",
        "-keyint_min",
        "48",
        # 720p
        "-filter:v:1",
        "scale=w=1280:h=720:force_original_aspect_ratio=decrease",
        "-c:a:1",
        "aac",
        "-ar:1",
        "48000",
        "-c:v:1",
        "h264",
        "-profile:v:1",
        "main",
        "-crf:1",
        "21",
        "-sc_threshold",
        "0",
        "-g",
        "48",
        "-keyint_min",
        "48",
        # HLS options
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",
        "-var_stream_map",
        "v:0,a:0 v:1,a:1",
        "-master_pl_name",
        "master.m3u8",
        "-f",
        "hls",
        "-hls_time",
        "4",
        "-hls_playlist_type",
        "vod",
        "-hls_segment_filename",
        os.path.join(target_dir, "v%v/seg_%03d.ts"),
        os.path.join(target_dir, "v%v/index.m3u8"),
    ]

    try:
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except subprocess.CalledProcessError as e:
        # En cas d'échec, on supprime partiellement et on remonte l'erreur
        print("FFmpeg error:", e.stderr.decode(errors="ignore")[:2000])
        raise

    # Génère un master manquant parfois selon versions — s'assure qu'il existe
    if not os.path.exists(master_path):
        # min-master fallback
        with open(master_path, "w", encoding="utf-8") as f:
            f.write("#EXTM3U\n")
            f.write("#EXT-X-VERSION:3\n")
            f.write("#EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=640x360\n")
            f.write("v0/index.m3u8\n")
            f.write("#EXT-X-STREAM-INF:BANDWIDTH=2500000,RESOLUTION=1280x720\n")
            f.write("v1/index.m3u8\n")

    # Retourne chemin relatif depuis HLS_DIR
    rel = os.path.relpath(master_path, HLS_DIR)
    return rel.replace("\\", "/")



# -------------------------
# Templates (Tailwind + hls.js)
# -------------------------
BASE_HTML = r"""
<!doctype html>
<html lang="fr">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{{ title or 'Mitabo' }}</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <script src="https://cdn.jsdelivr.net/npm/hls.js@1"></script>
  <style>
    .no-scrollbar::-webkit-scrollbar { display: none; }
    .no-scrollbar { -ms-overflow-style: none; scrollbar-width: none; }
    .line-clamp-2 { display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
    .container { max-width: 1120px; }
  </style>
</head>
<body class="min-h-screen bg-gradient-to-b from-gray-50 to-white">
  <header class="sticky top-0 z-40 backdrop-blur bg-white/70 border-b border-gray-100">
    <div class="container mx-auto px-4 py-3 flex items-center gap-3">
      <a href="{{ url_for('home') }}" class="flex items-center gap-2">
        <div class="w-9 h-9 rounded-2xl bg-black text-white grid place-items-center font-bold">M</div>
        <span class="font-extrabold text-xl tracking-tight">Mitabo</span>
      </a>
      <div class="flex-1"></div>
      {% if current_user.is_authenticated %}
        <span class="text-sm text-gray-600 mr-3">👋 {{ current_user.display_name }}</span>
        <a href="{{ url_for('upload_form') }}" class="px-4 py-2 rounded-2xl bg-black text-white">Téléverser</a>
        <a href="{{ url_for('logout') }}" class="ml-2 px-4 py-2 rounded-2xl border">Se déconnecter</a>
      {% else %}
        <a href="{{ url_for('login') }}" class="px-4 py-2 rounded-2xl border">Connexion</a>
        <a href="{{ url_for('register') }}" class="ml-2 px-4 py-2 rounded-2xl bg-black text-white">Créer un compte</a>
      {% endif %}
    </div>
  </header>
  {% with msgs = get_flashed_messages() %}
    {% if msgs %}
      <div class="container mx-auto px-4 mt-4">
        {% for m in msgs %}<div class="p-3 bg-amber-50 border border-amber-200 rounded-xl text-amber-800 mb-2">{{ m }}</div>{% endfor %}
      </div>
    {% endif %}
  {% endwith %}
  {{ body|safe }}
  <footer class="border-t border-gray-100 py-10 text-sm text-gray-500 mt-10">
    <div class="container mx-auto px-4 flex flex-col md:flex-row gap-6 md:items-center md:justify-between">
      <p>© {{ year }} Mitabo — Plateforme vidéo & univers créatifs.</p>
      <div class="flex gap-4">
        <a class="hover:text-black" href="#">Conditions</a>
        <a class="hover:text-black" href="#">Confidentialité</a>
        <a class="hover:text-black" href="#">Aide</a>
      </div>
    </div>
  </footer>
</body>
</html>
"""

HOME_BODY = r"""
<section class="border-b border-gray-100 bg-white">
  <div class="container mx-auto px-4 py-7">
    <div class="flex flex-col lg:flex-row items-start lg:items-center gap-4">
      <form method="get" action="/" class="relative flex-1 w-full">
        <input name="q" value="{{ q }}" placeholder="Rechercher des vidéos, jeux, créateurs…"
               class="w-full pl-4 pr-4 py-3 rounded-2xl border border-gray-200 focus:outline-none focus:ring-2 focus:ring-black/10" />
        <input type="hidden" name="cat" value="{{ active_cat }}" />
      </form>
      <div class="flex flex-nowrap gap-2 overflow-auto no-scrollbar">
        {% for c in categories %}
        <a href="{{ url_for('home', cat=c.id, q=q) }}"
           class="flex items-center gap-2 px-4 py-2 rounded-2xl border text-sm whitespace-nowrap {{ 'bg-black text-white border-black' if active_cat==c.id else 'bg-white hover:bg-gray-50 border-gray-200' }}">
          <span>•</span>
          <span>{{ c.label }}</span>
        </a>
        {% endfor %}
      </div>
    </div>
  </div>
</section>

<main class="container mx-auto px-4 py-8">
  <div class="flex items-center gap-2 mb-4">
    <div class="w-5 h-5 rounded-full bg-gray-900"></div>
    <h2 class="text-lg font-semibold">Découvrir • {{ categories_map[active_cat].label }}</h2>
  </div>

  {% if items %}
  <div class="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 xl:grid-cols-4 gap-5">
    {% for v in items %}
    <a class="group" href="{{ url_for('watch', video_id=v.id) }}">
      <div class="overflow-hidden rounded-2xl shadow hover:shadow-lg transition-shadow cursor-pointer bg-white border border-gray-100">
        <div class="relative">
          <img src="{{ v.thumb_url or 'https://picsum.photos/seed/mitabo-' ~ v.id ~ '/640/360' }}" alt="{{ v.title }}" class="w-full h-40 object-cover" />
          {% if v.duration %}<span class="absolute bottom-2 right-2 text-xs bg-black/80 text-white px-2 py-1 rounded">{{ v.duration }}</span>{% endif %}
        </div>
        <div class="p-3">
          <div class="flex items-start gap-3">
            <div class="w-9 h-9 rounded-full bg-gray-200 flex items-center justify-center overflow-hidden">👤</div>
            <div class="flex-1">
              <h3 class="font-medium leading-tight line-clamp-2">{{ v.title }}</h3>
              <p class="text-xs text-gray-500 mt-1">{{ v.creator }} • {{ '{:,}'.format(v.views).replace(',', ' ') }} vues</p>
            </div>
          </div>
        </div>
      </div>
    </a>
    {% endfor %}
  </div>
  {% else %}
  <div class="text-center text-gray-500 py-16">
    <div class="w-8 h-8 mx-auto mb-3">✨</div>
    <p>Aucun résultat. Essayez une autre recherche ou catégorie.</p>
  </div>
  {% endif %}
</main>
"""

WATCH_BODY = r"""
<main class="container mx-auto px-4 py-8">
  <div class="grid grid-cols-1 lg:grid-cols-3 gap-8">
    <div class="lg:col-span-2">
      <div class="bg-black rounded-2xl overflow-hidden aspect-video">
        {% if video.hls_manifest %}
          <video id="player" controls preload="metadata" playsinline class="w-full h-full" poster="{{ video.thumb_url }}"></video>
          <script>
            (function(){
              var video = document.getElementById('player');
              var src = '{{ url_for('hls', filename=video.hls_manifest) }}';
              if (video.canPlayType('application/vnd.apple.mpegurl')) {
                video.src = src;
              } else if (window.Hls) {
                var hls = new Hls();
                hls.loadSource(src);
                hls.attachMedia(video);
              } else {
                video.outerHTML = '<div class="text-white p-6">Votre navigateur ne supporte pas HLS.</div>';
              }
            })();
          </script>
        {% else %}
          <video controls preload="metadata" playsinline class="w-full h-full" poster="{{ video.thumb_url }}">
            <source src="{{ video.source_url }}" type="video/mp4">
            Votre navigateur ne supporte pas la lecture vidéo HTML5.
          </video>
        {% endif %}
      </div>
      <h1 class="text-xl font-semibold mt-4">{{ video.title }}</h1>
      <p class="text-sm text-gray-500">{{ video.creator }} • {{ '{:,}'.format(video.views).replace(',', ' ') }} vues • {{ video.created_at.strftime('%d %b %Y') }}</p>
      {% if video.description %}
      <div class="mt-3 text-gray-700 whitespace-pre-wrap">{{ video.description }}</div>
      {% endif %}

      <section class="mt-6">
        <h3 class="font-semibold mb-3">Commentaires</h3>
        {% if current_user.is_authenticated %}
        <form method="post" action="{{ url_for('comment_post', video_id=video.id) }}" class="mb-4">
          <textarea name="body" rows="3" required class="w-full border rounded-xl px-3 py-2" placeholder="Écrire un commentaire…"></textarea>
          <button class="mt-2 px-4 py-2 rounded-2xl bg-black text-white">Publier</button>
        </form>
        {% else %}
          <p class="text-sm text-gray-600"><a class="underline" href="{{ url_for('login') }}">Connecte-toi</a> pour commenter.</p>
        {% endif %}
        <div class="space-y-4">
          {% for c in comments %}
          <div class="bg-white border border-gray-100 rounded-xl p-3">
            <p class="text-sm"><span class="font-medium">{{ c.user.display_name }}</span> <span class="text-gray-500">• {{ c.created_at.strftime('%d %b %Y %H:%M') }}</span></p>
            <p class="mt-1">{{ c.body }}</p>
          </div>
          {% else %}
          <p class="text-gray-500">Pas encore de commentaires.</p>
          {% endfor %}
        </div>
      </section>
    </div>
    <aside>
      <h3 class="font-semibold mb-3">Plus comme ça</h3>
      <div class="space-y-4">
        {% for v in more %}
        <a class="flex gap-3" href="{{ url_for('watch', video_id=v.id) }}">
          <img class="w-40 h-24 object-cover rounded-lg" src="{{ v.thumb_url or 'https://picsum.photos/seed/mitabo-' ~ v.id ~ '/320/180' }}" alt="{{ v.title }}" />
          <div class="flex-1">
            <p class="text-sm font-medium leading-tight">{{ v.title }}</p>
            <p class="text-xs text-gray-500">{{ v.creator }} — {{ '{:,}'.format(v.views).replace(',', ' ') }} vues</p>
          </div>
        </a>
        {% endfor %}
      </div>
    </aside>
  </div>
</main>
"""

UPLOAD_BODY = r"""
<main class="container mx-auto px-4 py-8">
  <h1 class="text-xl font-semibold mb-4">Téléverser une vidéo</h1>
  <p class="text-sm text-gray-600 mb-4">Les uploads et les commentaires nécessitent un compte.</p>
  <form method="post" enctype="multipart/form-data" class="space-y-4 max-w-2xl">
    <div>
      <label class="block text-sm font-medium mb-1">Fichier vidéo (mp4, webm, ogg, mov, m4v)</label>
      <input class="block w-full" type="file" name="file" required />
    </div>
    <div>
      <label class="block text-sm font-medium mb-1">Titre</label>
      <input class="w-full border rounded-lg px-3 py-2" name="title" placeholder="Mon chef‑d'œuvre" required />
    </div>
    <div>
      <label class="block text-sm font-medium mb-1">Description</label>
      <textarea class="w-full border rounded-lg px-3 py-2" name="description" rows="4"></textarea>
    </div>
    <div>
      <label class="block text-sm font-medium mb-1">Catégorie</label>
      <select class="w-full border rounded-lg px-3 py-2" name="category">
        {% for c in categories %}
        <option value="{{ c.id }}">{{ c.label }}</option>
        {% endfor %}
      </select>
    </div>
    <div>
      <label class="block text-sm font-medium mb-1">Créateur</label>
      <input class="w-full border rounded-lg px-3 py-2" name="creator" placeholder="Ton nom" value="{{ current_user.display_name if current_user.is_authenticated else '' }}" />
    </div>
    <label class="inline-flex items-center gap-2"><input type="checkbox" name="to_hls" checked> <span>Transcoder en HLS (si ffmpeg dispo)</span></label>
    <button class="block px-4 py-2 rounded-2xl bg-black text-white" type="submit">Envoyer</button>
  </form>
</main>
"""

AUTH_BODY = r"""
<main class="container mx-auto px-4 py-10 max-w-md">
  <h1 class="text-xl font-semibold mb-6">{{ heading }}</h1>
  <form method="post" class="space-y-4">
    {% if mode == 'register' %}
      <div>
        <label class="block text-sm font-medium mb-1">Nom d'affichage</label>
        <input class="w-full border rounded-lg px-3 py-2" name="display_name" required />
      </div>
    {% endif %}
    <div>
      <label class="block text-sm font-medium mb-1">Email</label>
      <input class="w-full border rounded-lg px-3 py-2" name="email" type="email" required />
    </div>
    <div>
      <label class="block text-sm font-medium mb-1">Mot de passe</label>
      <input class="w-full border rounded-lg px-3 py-2" name="password" type="password" required />
    </div>
    <button class="px-4 py-2 rounded-2xl bg-black text-white">{{ cta }}</button>
  </form>
</main>
"""

# -------------------------
# Routes principales
# -------------------------
@app.get("/")
def home():
    q = (request.args.get("q") or "").strip()
    active_cat = request.args.get("cat") or CATEGORIES[0]["id"]

    query = Video.query.filter_by(category=active_cat)
    if q:
        like = f"%{q}%"
        query = query.filter(db.or_(Video.title.ilike(like), Video.creator.ilike(like)))
    items = query.order_by(Video.created_at.desc()).limit(40).all()

    body = render_template_string(
        HOME_BODY,
        q=q,
        active_cat=active_cat,
        items=items,
        categories=CATEGORIES,
        categories_map=CATEGORIES_MAP,
    )
    return render_template_string(BASE_HTML, body=body, year=datetime.utcnow().year, title="Mitabo — Accueil")

@app.get("/watch/<int:video_id>")
def watch(video_id: int):
    v = Video.query.get_or_404(video_id)
    v.views = (v.views or 0) + 1
    db.session.commit()

    more = (
        Video.query.filter(Video.id != v.id, Video.category == v.category)
        .order_by(Video.created_at.desc())
        .limit(8)
        .all()
    )
    comments = (
        db.session.query(Comment, User)
        .join(User, Comment.user_id == User.id)
        .filter(Comment.video_id == v.id)
        .order_by(Comment.created_at.desc())
        .all()
    )
    comments_view = [
        type("CObj", (), {"body": c.body, "created_at": c.created_at, "user": u}) for c, u in comments
    ]

    body = render_template_string(WATCH_BODY, video=v, more=more, comments=comments_view)
    return render_template_string(BASE_HTML, body=body, year=datetime.utcnow().year, title=v.title)

# -------------------------
# Upload + HLS
# -------------------------
@app.get("/upload")
@login_required
def upload_form():
    body = render_template_string(UPLOAD_BODY, categories=CATEGORIES)
    return render_template_string(BASE_HTML, body=body, year=datetime.utcnow().year, title="Téléverser — Mitabo")

@app.post("/upload")
@login_required
def upload_post():
    f = request.files.get("file")
    title = (request.form.get("title") or "Sans titre").strip()
    description = (request.form.get("description") or "").strip()
    category = request.form.get("category") or "tendance"
    creator = (request.form.get("creator") or current_user.display_name or "Anonyme").strip()
    to_hls = request.form.get("to_hls") is not None

    if not f or f.filename == "":
        abort(400, "Aucun fichier reçu")
    if not allowed_file(f.filename):
        abort(400, "Extension non supportée")

    filename = secure_filename(f.filename)
    base, ext = os.path.splitext(filename)
    counter = 1
    final = filename
    while os.path.exists(os.path.join(UPLOAD_DIR, final)):
        final = f"{base}-{counter}{ext}"
        counter += 1

    file_path = os.path.join(UPLOAD_DIR, final)
    f.save(file_path)

    v = Video(
        title=title,
        description=description,
        category=category if category in CATEGORIES_MAP else "tendance",
        filename=final,
        thumb_url="https://picsum.photos/seed/mitabo-" + base + "/640/360",
        duration="",
        creator=creator,
        user_id=current_user.id,
    )

    # HLS transcode si demandé et FFmpeg dispo
    if to_hls and ffmpeg_exists():
        target_dir = os.path.join(HLS_DIR, f"video_{datetime.utcnow().timestamp():.0f}")
        try:
            rel_master = transcode_to_hls(file_path, target_dir)
            v.hls_manifest = rel_master
        except Exception:
            flash("Transcodage HLS échoué — lecture MP4 directe utilisée.")

    db.session.add(v)
    db.session.commit()

    return redirect(url_for("watch", video_id=v.id))

@app.get("/media/<path:filename>")
def media(filename):
    return send_from_directory(UPLOAD_DIR, filename, as_attachment=False)

@app.get("/hls/<path:filename>")
def hls(filename):
    # Sert les .m3u8 et .ts du répertoire HLS
    return send_from_directory(HLS_DIR, filename, as_attachment=False)

# -------------------------
# Authentification
# -------------------------
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        u = User.query.filter_by(email=email).first()
        if not u or not u.check_password(password):
            flash("Identifiants invalides")
        else:
            login_user(u)
            return redirect(url_for("home"))
    body = render_template_string(AUTH_BODY, heading="Connexion", cta="Se connecter", mode="login")
    return render_template_string(BASE_HTML, body=body, year=datetime.utcnow().year, title="Connexion — Mitabo")

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        display_name = (request.form.get("display_name") or "").strip()
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        if not display_name or not email or not password:
            flash("Tous les champs sont requis")
        elif User.query.filter_by(email=email).first():
            flash("Cet email est déjà utilisé")
        else:
            u = User(email=email, display_name=display_name)
            u.set_password(password)
            db.session.add(u)
            db.session.commit()
            login_user(u)
            return redirect(url_for("home"))
    body = render_template_string(AUTH_BODY, heading="Créer un compte", cta="S'inscrire", mode="register")
    return render_template_string(BASE_HTML, body=body, year=datetime.utcnow().year, title="Inscription — Mitabo")

@app.get("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("home"))

# -------------------------
# Commentaires
# -------------------------
@app.post("/watch/<int:video_id>/comment")
@login_required
def comment_post(video_id: int):
    v = Video.query.get_or_404(video_id)
    body = (request.form.get("body") or "").strip()
    if not body:
        flash("Commentaire vide")
        return redirect(url_for("watch", video_id=v.id))
    c = Comment(video_id=v.id, user_id=current_user.id, body=body)
    db.session.add(c)
    db.session.commit()
    return redirect(url_for("watch", video_id=v.id))

# -------------------------
# API minimale
# -------------------------
@app.get("/api/videos")
def api_videos():
    page = max(int(request.args.get("page", 1)), 1)
    per_page = min(max(int(request.args.get("per_page", 12)), 1), 50)
    q = (request.args.get("q") or "").strip()
    cat = request.args.get("cat") or None

    query = Video.query
    if cat:
        query = query.filter_by(category=cat)
    if q:
        like = f"%{q}%"
        query = query.filter(db.or_(Video.title.ilike(like), Video.creator.ilike(like)))

    total = query.count()
    items = (
        query.order_by(Video.created_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )
    return jsonify({
        "page": page,
        "per_page": per_page,
        "total": total,
        "items": [
            {
                "id": v.id,
                "title": v.title,
                "creator": v.creator,
                "category": v.category,
                "views": v.views,
                "thumb_url": v.thumb_url,
                "source_url": v.source_url,
                "hls": bool(v.hls_manifest),
                "created_at": v.created_at.isoformat(),
            }
            for v in items
        ],
    })

# -------------------------
# Entrée app
# -------------------------
if __name__ == "__main__":
  

# -------------------------
# Routes pour les likes/dislikes
# -------------------------
@app.route("/video/like/<int:video_id>", methods=["POST"])
def like_video_route(video_id):
    # Ajoutez ici la logique de gestion du like ou laissez un pass si déjà défini ailleurs
    pass
@login_required
def like_video(video_id):
    video = Video.query.get_or_404(video_id)
    
    # Vérifier si l'utilisateur a déjà liké/disliké cette vidéo
    existing_like = db.session.query(Like).filter_by(
        user_id=current_user.id, 
        video_id=video_id
    ).first()
    
    if existing_like:
        if existing_like.is_like:
            # Déjà liké, on supprime le like
            db.session.delete(existing_like)
        else:
            # C'était un dislike, on transforme en like
            existing_like.is_like = True
    else:
        # Nouveau like
        new_like = Like(
            user_id=current_user.id,
            video_id=video_id,
            is_like=True
        )
        db.session.add(new_like)
    
    db.session.commit()
    return redirect(url_for("watch", video_id=video_id))

@app.route("/dislike/<int:video_id>", methods=["POST"])
@login_required
def dislike_video(video_id):
    video = Video.query.get_or_404(video_id)
    
    existing_like = db.session.query(Like).filter_by(
        user_id=current_user.id,
        video_id=video_id
    ).first()
    
    if existing_like:
        if not existing_like.is_like:
            # Déjà disliké, on supprime
            db.session.delete(existing_like)
        else:
            # C'était un like, on transforme en dislike
            existing_like.is_like = False
    else:
        # Nouveau dislike
        new_dislike = Like(
            user_id=current_user.id,
            video_id=video_id,
            is_like=False
        )
        db.session.add(new_dislike)
    
    db.session.commit()
    return redirect(url_for("watch", video_id=video_id))

# -------------------------
# Route profil utilisateur
# -------------------------
@app.route("/profil/<username>")
def show_profil(username):
    user = User.query.filter_by(display_name=username).first_or_404()  # ou username selon votre modèle
    videos = Video.query.filter_by(user_id=user.id).order_by(Video.created_at.desc()).all()
    
    # Vérifier si l'utilisateur connecté suit ce profil
    is_following = False
    if current_user.is_authenticated:
        is_following = db.session.query(Follow).filter_by(
            follower_id=current_user.id,
            followed_id=user.id
        ).first() is not None
    
    return render_template_string(PROFIL_BODY, 
                                user=user, 
                                videos=videos, 
                                is_following=is_following)

# -------------------------
# Routes pour suivre/ne plus suivre
# -------------------------
@app.route("/profil/follow/<int:user_id>", methods=["POST"])
@login_required
def follow_user(user_id):
    user = User.query.get_or_404(user_id)
    
    if user.id == current_user.id:
        return redirect(request.referrer or url_for("show_profil", username=user.display_name))
    
    existing_follow = db.session.query(Follow).filter_by(
        follower_id=current_user.id,
        followed_id=user_id
    ).first()
    
    if not existing_follow:
        follow = Follow(follower_id=current_user.id, followed_id=user.id)
        db.session.add(follow)
        db.session.commit()
    
    return redirect(request.referrer or url_for("show_profil", username=user.display_name))

@app.route("/profil/unfollow/<int:user_id>", methods=["POST"])
@login_required
def unfollow_user(user_id):
    user = User.query.get_or_404(user_id)
    
    follow = db.session.query(Follow).filter_by(
        follower_id=current_user.id, 
        followed_id=user_id
    ).first()
    
    if follow:
        db.session.delete(follow)
        db.session.commit()
    
    return redirect(request.referrer or url_for("show_profil", username=user.display_name))

# -------------------------
# Mise à jour de la route watch
# -------------------------
# Remplacez votre route @app.get("/watch/<int:video_id>") existante par celle-ci :
@app.get("/watch/<int:video_id>")
def watch(video_id: int):
    v = Video.query.get_or_404(video_id)
    v.views = (v.views or 0) + 1
    db.session.commit()
    
    # Vérifier si l'utilisateur connecté a liké/disliké cette vidéo
    user_like = None
    is_following = False
    if current_user.is_authenticated:
        user_like = db.session.query(Like).filter_by(
            user_id=current_user.id, 
            video_id=video_id
        ).first()
        
        # Vérifier si l'utilisateur suit le créateur de la vidéo
        if v.user_id:
            is_following = db.session.query(Follow).filter_by(
                follower_id=current_user.id,
                followed_id=v.user_id
            ).first() is not None

    more = (
        Video.query.filter(Video.id != v.id, Video.category == v.category)
        .order_by(Video.created_at.desc())
        .limit(8)
        .all()
    )
    
    comments = (
        db.session.query(Comment, User)
        .join(User, Comment.user_id == User.id)
        .filter(Comment.video_id == v.id)
        .order_by(Comment.created_at.desc())
        .all()
    )
    comments_view = [
        type("CObj", (), {"body": c.body, "created_at": c.created_at, "user": u}) for c, u in comments
    ]

    body = render_template_string(WATCH_BODY, 
                                video=v, 
                                more=more, 
                                comments=comments_view,
                                user_like=user_like,
                                is_following=is_following)
    return render_template_string(BASE_HTML, body=body, year=datetime.utcnow().year, title=v.title)

from flask import Flask
from flask_login import LoginManager
from extensions import db  # importer db ici

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///instance/mitabo.db'
app.config['SECRET_KEY'] = 'tonsecret'


login_manager = LoginManager(app)



from extensions import db
from home import app

db.init_app(app)  # UNE seule fois, ici

from flask import redirect, url_for, request, flash
from flask_login import login_required, current_user
from extensions import db
from models import Video, Like

@app.route('/like/<int:video_id>', methods=['POST'])
def like_video(video_id):
    if not current_user.is_authenticated:
        return redirect(url_for('login'))

    like = Like.query.filter_by(user_id=current_user.id, video_id=video_id).first()
    if not like:
        like = Like(user_id=current_user.id, video_id=video_id, is_like=True)
        db.session.add(like)
    else:
        like.is_like = True  # transforme en like si c’était un dislike
    db.session.commit()
    return redirect(url_for('watch', video_id=video_id))


@app.route('/dislike/<int:video_id>', methods=['POST'])
def dislike_video(video_id):
    if not current_user.is_authenticated:
        return redirect(url_for('login'))

    like = Like.query.filter_by(user_id=current_user.id, video_id=video_id).first()
    if not like:
        like = Like(user_id=current_user.id, video_id=video_id, is_like=False)
        db.session.add(like)
    else:
        like.is_like = False  # transforme en dislike si c’était un like
    db.session.commit()
    return redirect(url_for('watch', video_id=video_id))

from flask import render_template

@app.route('/video/<int:video_id>')
def video(video_id):
    # On va chercher la vidéo dans la base
    from models import Video
    video = Video.query.get_or_404(video_id)

    return render_template("video.html", video=video)

from flask import redirect, url_for

@app.route('/video/<int:video_id>/like', methods=['POST'])
def like_video(video_id):
    video = Video.query.get_or_404(video_id)
    video.likes += 1
    db.session.commit()
    return redirect(url_for('video', video_id=video.id))

@app.route('/video/<int:video_id>/dislike', methods=['POST'])
def dislike_video(video_id):
    video = Video.query.get_or_404(video_id)
    video.dislikes += 1
    db.session.commit()
    return redirect(url_for('video', video_id=video.id))

@app.route('/profil/<username>')
def profil(username):
    from models import User
    user = User.query.filter_by(username=username).first_or_404()
    videos = user.videos  # si tu as une relation user.videos
    return render_template("profil.html", user=user, videos=videos)

from video import video_bp
app.register_blueprint(video_bp)

with app.app_context():
    db.create_all()

from extensions import db


from models import User, Video, Like, Follow  # pour enregistrer les modèles


for rule in app.url_map.iter_rules():
    print(rule)
