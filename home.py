import os
from flask import Flask, request, render_template_string, url_for, redirect, send_from_directory, abort, jsonify, flash, send_file
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
import subprocess
import shutil
from PIL import Image
from sqlalchemy import text
from models import Video, Like, Xp
from extensions import db, migrate  # assure-toi que migrate est défini dans extensions.py
# ------------------------------
# Création de l'application Flask
# ------------------------------
app = Flask(__name__)

# ------------------------------
# Configuration de la base de données
# ------------------------------
# Récupère DATABASE_URL depuis les variables d'environnement
uri = os.getenv("DATABASE_URL")
if not uri:
    raise RuntimeError("DATABASE_URL not set! Configure it in Render environment variables.")

# Remplace postgres:// par postgresql:// si nécessaire (SQLAlchemy >= 2.0)
if uri.startswith("postgres://"):
    uri = uri.replace("postgres://", "postgresql://", 1)

#  Ajout SSL pour Supabase
if "supabase.co" in uri:
    if "?" in uri:
        uri += "&sslmode=require"
    else:
        uri += "?sslmode=require"

# Configuration SQLAlchemy
app.config["SQLALCHEMY_DATABASE_URI"] = uri
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

# Initialisation DB
# Initialisation DB et Migrate avec l'app
db.init_app(app)
migrate.init_app(app, db)


# Configuration Flask
app.config.update(
    SQLALCHEMY_DATABASE_URI=uri,
    SQLALCHEMY_TRACK_MODIFICATIONS=False,
    SECRET_KEY="dev-mitabo-secret-key-change-in-production",
    MAX_CONTENT_LENGTH=1024 * 1024 * 1024,  # 1 Go max upload
    DEBUG=True,
)

# ------------------------------
# Initialisation des extensions
# ------------------------------



login_manager = LoginManager(app)
login_manager.login_view = "login"

# ------------------------------
# Supabase Storage centralisé
# ------------------------------
from supabase_config import supabase, BUCKET_NAME

# Initialisation de Flask-Login
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"




# ------------------------------
# Création automatique des tables (Render inclus)
# ------------------------------
with app.app_context():
    db.create_all()

# ------------------------------
# Lancement en local
# ------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)



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
# Login manager
# -------------------------
@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# -------------------------
# Utils
# -------------------------
def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS

def ffmpeg_exists() -> bool:
    return shutil.which("ffmpeg") is not None

def transcode_to_hls(input_path: str, target_dir: str) -> str:
    """Transcode en HLS multi-qualité (360p, 720p). Retourne chemin relatif du master.m3u8."""
    os.makedirs(target_dir, exist_ok=True)
    master_path = os.path.join(target_dir, "master.m3u8")
    
    cmd = [
        "ffmpeg", "-y", "-i", input_path,
        "-filter:v:0", "scale=w=640:h=360:force_original_aspect_ratio=decrease",
        "-c:a:0", "aac", "-ar:0", "48000", "-c:v:0", "h264", "-profile:v:0", "main",
        "-crf:0", "23", "-sc_threshold", "0", "-g", "48", "-keyint_min", "48",
        "-filter:v:1", "scale=w=1280:h=720:force_original_aspect_ratio=decrease",
        "-c:a:1", "aac", "-ar:1", "48000", "-c:v:1", "h264", "-profile:v:1", "main",
        "-crf:1", "21", "-sc_threshold", "0", "-g", "48", "-keyint_min", "48",
        "-map", "0:v:0", "-map", "0:a:0?", "-map", "0:v:0", "-map", "0:a:0?",
        "-var_stream_map", "v:0,a:0 v:1,a:1", "-master_pl_name", "master.m3u8",
        "-f", "hls", "-hls_time", "4", "-hls_playlist_type", "vod",
        "-hls_segment_filename", os.path.join(target_dir, "v%v/seg_%03d.ts"),
        os.path.join(target_dir, "v%v/index.m3u8"),
    ]

    try:
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except subprocess.CalledProcessError as e:
        print("FFmpeg error:", e.stderr.decode(errors="ignore")[:2000])
        raise

    if not os.path.exists(master_path):
        with open(master_path, "w", encoding="utf-8") as f:
            f.write("#EXTM3U\n#EXT-X-VERSION:3\n")
            f.write("#EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=640x360\nv0/index.m3u8\n")
            f.write("#EXT-X-STREAM-INF:BANDWIDTH=2500000,RESOLUTION=1280x720\nv1/index.m3u8\n")

    rel = os.path.relpath(master_path, HLS_DIR)
    return rel.replace("\\", "/")

def init_db():
    """Initialise la base de données avec des données de test"""
    with app.app_context():
        try:
            db.create_all()
            if User.query.count() == 0:
                u = User(email="demo@mitabo.dev", display_name="Demo")
                u.set_password("demo1234")
                db.session.add(u)
                db.session.commit()
                print("Utilisateur demo créé: demo@mitabo.dev / demo1234")
            
            if Video.query.count() == 0:
                user = User.query.first()
                if user:
                    demo = Video(
                        title="Big Buck Bunny — Démo",
                        description="Vidéo de démonstration pour Mitabo.",
                        category="film",
                        external_url="https://commondatastorage.googleapis.com/gtv-videos-bucket/sample/BigBuckBunny.mp4",
                        thumb_url="https://picsum.photos/seed/mitabo-demo/640/360",
                        duration="10:34",
                        creator="Mitabo",
                        user_id=user.id,
                    )
                    db.session.add(demo)
                    db.session.commit()
                    print("Vidéo de démo créée")
        except Exception as e:
            print(f"Erreur lors de l'initialisation de la DB: {e}")

# -------------------------
# Templates
# -------------------------
BASE_HTML = """<!DOCTYPE html>
<html lang="fr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{{ title }}</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://cdn.jsdelivr.net/npm/hls.js@latest"></script>
</head>
<body class="bg-gray-50">
    <nav class="bg-white shadow-sm border-b">
        <div class="container mx-auto px-4 py-3 flex items-center justify-between">
            <a href="{{ url_for('home') }}" class="text-xl font-bold text-blue-600">Mitabo</a>
            <div class="flex items-center space-x-4">
                {% if current_user.is_authenticated %}
                    <a href="{{ url_for('upload_form') }}" class="bg-blue-500 text-white px-4 py-2 rounded">Upload</a>
                    <span>{{ current_user.display_name }}</span>
                    <a href="{{ url_for('logout') }}" class="text-gray-600">Déconnexion</a>
                {% else %}
                    <a href="{{ url_for('login') }}" class="text-blue-600">Connexion</a>
                    <a href="{{ url_for('register') }}" class="bg-blue-500 text-white px-4 py-2 rounded">Inscription</a>
                {% endif %}
            </div>
        </div>
    </nav>
    
    {% with messages = get_flashed_messages() %}
        {% if messages %}
            <div class="container mx-auto px-4 py-2">
                {% for message in messages %}
                    <div class="bg-red-100 border border-red-400 text-red-700 px-4 py-3 rounded mb-4">{{ message }}</div>
                {% endfor %}
            </div>
        {% endif %}
    {% endwith %}
    
    {{ body|safe }}
    
    <footer class="bg-gray-800 text-white text-center py-4 mt-12">
        <p>&copy; {{ year }} Mitabo</p>
    </footer>
</body>
</html>"""

HOME_BODY = """
<main class="container mx-auto px-4 py-8">
    <div class="mb-6">
        <form method="get" class="flex gap-4 mb-4">
            <input name="q" value="{{ q }}" placeholder="Rechercher..." 
                   class="flex-1 px-4 py-2 border rounded-lg">
            <input name="cat" value="{{ active_cat }}" type="hidden">
            <button type="submit" class="bg-blue-500 text-white px-6 py-2 rounded-lg">Rechercher</button>
        </form>
        
        <div class="flex gap-2">
            {% for cat in categories %}
                <a href="?cat={{ cat.id }}&q={{ q }}" 
                   class="px-4 py-2 rounded-lg {% if cat.id == active_cat %}bg-blue-500 text-white{% else %}bg-gray-200{% endif %}">
                    {{ cat.label }}
                </a>
            {% endfor %}
        </div>
    </div>
    
    <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-6">
        {% for video in items %}
            <div class="bg-white rounded-lg shadow-sm overflow-hidden">
                <a href="{{ url_for('watch', video_id=video.id) }}">
                    {% if video.thumb_url %}
                        <img src="{{ video.thumb_url }}" alt="{{ video.title }}" class="w-full h-48 object-cover">
                    {% else %}
                        <div class="w-full h-48 bg-gray-300 flex items-center justify-center">
                            <span class="text-gray-500">Pas de miniature</span>
                        </div>
                    {% endif %}
                </a>
                <div class="p-4">
                    <h3 class="font-semibold mb-2">
                        <a href="{{ url_for('watch', video_id=video.id) }}" class="hover:text-blue-600">
                            {{ video.title }}
                        </a>
                    </h3>
                    <p class="text-gray-600 text-sm">{{ video.creator }}</p>
                    <p class="text-gray-500 text-sm">{{ video.views or 0 }} vues</p>
                </div>
            </div>
        {% else %}
            <div class="col-span-full text-center py-8">
                <p class="text-gray-500">Aucune vidéo trouvée.</p>
            </div>
        {% endfor %}
    </div>
</main>
"""

WATCH_BODY = """
<main class="container mx-auto px-4 py-8">
    <div class="grid grid-cols-1 lg:grid-cols-3 gap-8">
        <div class="lg:col-span-2">
            <div class="bg-black rounded-lg overflow-hidden mb-4">
                <video id="video-player" controls class="w-full h-auto max-h-96">
                    <source src="{{ video.source_url }}" type="video/mp4">
                    Votre navigateur ne supporte pas la lecture vidéo.
                </video>
            </div>
            
            <h1 class="text-2xl font-bold mb-2">{{ video.title }}</h1>
            <div class="flex items-center justify-between mb-4">
                <div>
                    <p class="text-gray-600">{{ video.creator }}</p>
                    <p class="text-gray-500 text-sm">{{ video.views }} vues • {{ video.created_at.strftime('%d %b %Y') }}</p>
                </div>
                
                {% if current_user.is_authenticated %}
                  <div class="flex items-center space-x-2">
    <!-- Like -->
    <button onclick="likeVideo({{ video.id }})" 
            class="flex items-center space-x-1 px-3 py-1 rounded bg-gray-200 hover:bg-gray-300">
        <span>👍</span>
        <span id="likes-count">{{ video.likes or 0 }}</span>
    </button>

    <!-- Dislike -->
    <button onclick="dislikeVideo({{ video.id }})" 
            class="flex items-center space-x-1 px-3 py-1 rounded bg-gray-200 hover:bg-gray-300">
        <span>👎</span>
        <span id="dislikes-count">{{ video.dislikes or 0 }}</span>
    </button>

    <!-- XP -->
    <button onclick="giveXp({{ video.id }})" 
            class="flex items-center space-x-1 px-3 py-1 rounded bg-yellow-200 hover:bg-yellow-300">
        <span>✨ XP</span>
        <span id="xp-count">{{ video.xp or 0 }}</span>
    </button>
</div>

<script>
function giveXp(videoId) {
    fetch(`/video/${videoId}/xp`, { method: "POST" })
        .then(res => res.json())
        .then(data => {
            document.getElementById("xp-count").textContent = data.xp;
        });
}
</script>

                {% endif %}
            </div>
            
            <div class="bg-gray-100 p-4 rounded-lg mb-6">
                <p>{{ video.description or "Aucune description" }}</p>
            </div>
            
            <!-- Commentaires -->
            {% if current_user.is_authenticated %}
                <form method="post" action="{{ url_for('comment_post', video_id=video.id) }}" class="mb-6">
                    <textarea name="body" placeholder="Ajouter un commentaire..." 
                              class="w-full p-3 border rounded-lg mb-2" rows="3" required></textarea>
                    <button type="submit" class="bg-blue-500 text-white px-4 py-2 rounded">Commenter</button>
                </form>
            {% endif %}
            
            <div class="space-y-4">
                {% for comment in comments %}
                    <div class="bg-white p-4 rounded-lg shadow-sm">
                        <div class="flex items-center space-x-2 mb-2">
                            <strong>{{ comment.user.display_name }}</strong>
                            <span class="text-gray-500 text-sm">{{ comment.created_at.strftime('%d %b %Y à %H:%M') }}</span>
                        </div>
                        <p>{{ comment.body }}</p>
                    </div>
                {% else %}
                    <p class="text-gray-500 text-center">Aucun commentaire pour le moment.</p>
                {% endfor %}
            </div>
        </div>
        
        <!-- Suggestions -->
        <div class="space-y-4">
            <h3 class="font-semibold text-lg">Suggestions</h3>
            {% for suggestion in more %}
                <div class="bg-white rounded-lg shadow-sm overflow-hidden">
                    <a href="{{ url_for('watch', video_id=suggestion.id) }}">
                        {% if suggestion.thumb_url %}
                            <img src="{{ suggestion.thumb_url }}" alt="{{ suggestion.title }}" 
                                 class="w-full h-32 object-cover">
                        {% else %}
                            <div class="w-full h-32 bg-gray-300 flex items-center justify-center">
                                <span class="text-gray-500 text-xs">Pas de miniature</span>
                            </div>
                        {% endif %}
                    </a>
                    <div class="p-3">
                        <h4 class="font-medium text-sm mb-1">
                            <a href="{{ url_for('watch', video_id=suggestion.id) }}">{{ suggestion.title }}</a>
                        </h4>
                        <p class="text-gray-600 text-xs">{{ suggestion.creator }}</p>
                        <p class="text-gray-500 text-xs">{{ suggestion.views or 0 }} vues</p>
                    </div>
                </div>
            {% else %}
                <p class="text-gray-500 text-sm">Aucune suggestion disponible.</p>
            {% endfor %}
        </div>
    </div>
</main>

<script>
function likeVideo(videoId) {
    fetch(`/video/like/${videoId}`, {method: 'POST'})
        .then(r => r.json())
        .then(data => {
            document.getElementById('likes-count').textContent = data.likes;
            document.getElementById('dislikes-count').textContent = data.dislikes;
        })
        .catch(err => console.error('Erreur like:', err));
}

function dislikeVideo(videoId) {
    fetch(`/video/dislike/${videoId}`, {method: 'POST'})
        .then(r => r.json())
        .then(data => {
            document.getElementById('likes-count').textContent = data.likes;
            document.getElementById('dislikes-count').textContent = data.dislikes;
        })
        .catch(err => console.error('Erreur dislike:', err));
}

// HLS support
const video = document.getElementById('video-player');
const videoSrc = '{{ video.source_url }}';
if (Hls.isSupported() && videoSrc.includes('.m3u8')) {
    const hls = new Hls();
    hls.loadSource(videoSrc);
    hls.attachMedia(video);
    hls.on(Hls.Events.ERROR, function (event, data) {
        console.error('HLS error:', data);
    });
}
</script>
"""

UPLOAD_BODY = """
<main class="container mx-auto px-4 py-8">
    <h1 class="text-2xl font-bold mb-6">Téléverser une vidéo</h1>
    
    <form method="post" enctype="multipart/form-data" class="max-w-2xl">
        <div class="space-y-4">
            <div>
                <label class="block text-sm font-medium mb-1">Fichier vidéo</label>
                <input name="file" type="file" accept="video/*" required 
                       class="w-full px-3 py-2 border rounded-lg">
            </div>
            
            <div>
                <label class="block text-sm font-medium mb-1">Titre</label>
                <input name="title" type="text" required 
                       class="w-full px-3 py-2 border rounded-lg">
            </div>
            
            <div>
                <label class="block text-sm font-medium mb-1">Description</label>
                <textarea name="description" rows="4" 
                          class="w-full px-3 py-2 border rounded-lg"></textarea>
            </div>
            
            <div>
                <label class="block text-sm font-medium mb-1">Catégorie</label>
                <select name="category" class="w-full px-3 py-2 border rounded-lg">
                    {% for cat in categories %}
                        <option value="{{ cat.id }}">{{ cat.label }}</option>
                    {% endfor %}
                </select>
            </div>
            
            <div>
                <label class="block text-sm font-medium mb-1">Créateur</label>
                <input name="creator" type="text" value="{{ current_user.display_name }}" 
                       class="w-full px-3 py-2 border rounded-lg">
            </div>
            
            <div>
                <label class="flex items-center space-x-2">
                    <input name="to_hls" type="checkbox">
                    <span class="text-sm">Convertir en HLS (streaming adaptatif)</span>
                </label>
            </div>
            
            <button type="submit" class="bg-blue-500 text-white px-6 py-2 rounded-lg">
                Téléverser
            </button>
        </div>
    </form>
</main>
"""

AUTH_BODY = """
<main class="container mx-auto px-4 py-8 max-w-md">
    <h1 class="text-2xl font-bold text-center mb-6">{{ heading }}</h1>
    
    <form method="post" class="space-y-4">
        {% if mode == 'register' %}
            <div>
                <label class="block text-sm font-medium mb-1">Nom d'affichage</label>
                <input name="display_name" type="text" required 
                       class="w-full px-3 py-2 border rounded-lg">
            </div>
        {% endif %}
        
        <div>
            <label class="block text-sm font-medium mb-1">Email</label>
            <input name="email" type="email" required 
                   class="w-full px-3 py-2 border rounded-lg">
        </div>
        
        <div>
            <label class="block text-sm font-medium mb-1">Mot de passe</label>
            <input name="password" type="password" required 
                   class="w-full px-3 py-2 border rounded-lg">
        </div>
        
        <button type="submit" class="w-full bg-blue-500 text-white py-2 rounded-lg">
            {{ cta }}
        </button>
    </form>
    
    <div class="text-center mt-4">
        {% if mode == 'login' %}
            <p>Pas de compte ? <a href="{{ url_for('register') }}" class="text-blue-600">S'inscrire</a></p>
        {% else %}
            <p>Déjà un compte ? <a href="{{ url_for('login') }}" class="text-blue-600">Se connecter</a></p>
        {% endif %}
    </div>
</main>
"""

PROFIL_BODY = """
<main class="container mx-auto px-4 py-8">
    <h1 class="text-xl font-semibold mb-4">Profil de {{ user.display_name }}</h1>
    <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
        {% for v in videos %}
            <div class="bg-white rounded-lg shadow-sm overflow-hidden">
                <a href="{{ url_for('watch', video_id=v.id) }}">
                    {% if v.thumb_url %}
                        <img src="{{ v.thumb_url }}" alt="{{ v.title }}" class="w-full h-48 object-cover">
                    {% else %}
                        <div class="w-full h-48 bg-gray-300 flex items-center justify-center">
                            <span class="text-gray-500">Pas de miniature</span>
                        </div>
                    {% endif %}
                </a>
                <div class="p-4">
                    <h3 class="font-semibold mb-2">{{ v.title }}</h3>
                    <p class="text-gray-500 text-sm">{{ v.created_at.strftime('%d %b %Y') }}</p>
                </div>
            </div>
        {% else %}
            <p class="col-span-full text-center text-gray-500">Aucune vidéo.</p>
        {% endfor %}
    </div>
</main>
"""

# -------------------------
# Routes principales
# -------------------------
@app.get("/")
def home():
    try:
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
    except Exception as e:
        print(f"Erreur dans home(): {e}")
        return f"Erreur: {e}", 500

@app.get("/watch/<int:video_id>")
def watch(video_id: int):
    try:
        v = Video.query.get_or_404(video_id)
        v.views = (v.views or 0) + 1
        db.session.commit()

        user_like = None
        is_following = False
        if current_user.is_authenticated:
            user_like = Like.query.filter_by(user_id=current_user.id, video_id=video_id).first()
            if v.user_id:
                is_following = Follow.query.filter_by(
                    follower_id=current_user.id, followed_id=v.user_id
                ).first() is not None

        more = (
            Video.query.filter(Video.id != v.id, Video.category == v.category)
            .order_by(Video.created_at.desc())
            .limit(8)
            .all()
        )

        # Récupération des commentaires avec jointure correcte
        comments = (
            Comment.query
            .filter(Comment.video_id == v.id)
            .order_by(Comment.created_at.desc())
            .all()
        )

        body = render_template_string(
            WATCH_BODY,
            video=v,
            more=more,
            comments=comments,
            user_like=user_like,
            is_following=is_following
        )
        return render_template_string(BASE_HTML, body=body, year=datetime.utcnow().year, title=v.title)
    except Exception as e:
        print(f"Erreur dans watch(): {e}")
        return f"Erreur: {e}", 500

# -------------------------
# Upload + HLS
# -------------------------
@app.get("/upload")
@login_required
def upload_form():
    try:
        body = render_template_string(UPLOAD_BODY, categories=CATEGORIES)
        return render_template_string(BASE_HTML, body=body, year=datetime.utcnow().year, title="Téléverser — Mitabo")
    except Exception as e:
        print(f"Erreur dans upload_form(): {e}")
        return f"Erreur: {e}", 500


@app.post("/upload")
@login_required
def upload_post():
    try:
        # --- Récupérer le fichier et infos ---
        f = request.files.get("file")
        print("DEBUG: fichier reçu =", f.filename if f else None, "mimetype =", f.mimetype if f else None)

        title = (request.form.get("title") or "Sans titre").strip()
        description = (request.form.get("description") or "").strip()
        category = request.form.get("category") or "tendance"
        creator = (request.form.get("creator") or getattr(current_user, "display_name", "Anonyme")).strip()
        to_hls = request.form.get("to_hls") is not None

        if not f or f.filename == "":
            flash("Aucun fichier reçu")
            return redirect(url_for("upload_form"))
        if not allowed_file(f.filename):
            flash("Extension non supportée")
            return redirect(url_for("upload_form"))

        # --- Préparer le fichier ---
        filename = secure_filename(f.filename)
        base, ext = os.path.splitext(filename)
        counter = 1
        final = filename
        while os.path.exists(os.path.join(UPLOAD_DIR, final)):
            final = f"{base}-{counter}{ext}"
            counter += 1

        file_path = os.path.join(UPLOAD_DIR, final)
        os.makedirs(UPLOAD_DIR, exist_ok=True)
        f.save(file_path)  # Sauvegarde locale
        print("DEBUG: fichier sauvegardé =", file_path)

        # --- Upload vers Supabase ---
        with open(file_path, "rb") as file_data:
            res = supabase.storage.from_(BUCKET_NAME).upload(
                f"videos/{final}",
                file_data,
                {"content-type": f.mimetype}  # ✅ correction
            )
        print("DEBUG: réponse Supabase =", res)

        public_url = None
        if isinstance(res, dict) and res.get("error"):
            flash(f"Erreur Supabase: {res['error']['message']}")
        else:
            public_url = supabase.storage.from_(BUCKET_NAME).get_public_url(f"videos/{final}")["publicUrl"]

        # --- Créer l'objet Video ---
        v = Video(
            title=title,
            description=description,
            category=category if category in CATEGORIES_MAP else "tendance",
            filename=final,
            thumb_url=f"https://picsum.photos/seed/mitabo-{base}/640/360",
            duration="",
            creator=creator,
            user_id=current_user.id,
            url=public_url
        )

        # --- Transcodage HLS si demandé ---
        if to_hls and ffmpeg_exists():
            target_dir = os.path.join(HLS_DIR, f"video_{datetime.utcnow().timestamp():.0f}")
            try:
                rel_master = transcode_to_hls(file_path, target_dir)
                v.hls_manifest = rel_master
            except Exception as e:
                print(f"Erreur transcodage HLS: {e}")
                flash("Transcodage HLS échoué — lecture MP4 directe utilisée.")

        # --- Sauvegarder dans DB ---
        db.session.add(v)
        db.session.commit()

        flash("Vidéo uploadée avec succès !")
        return redirect(url_for("watch", video_id=v.id))

    except Exception as e:
        import traceback; traceback.print_exc()
        print(f"Erreur dans upload_post(): {e}")
        flash(f"Erreur lors de l'upload: {e}")
        return redirect(url_for("upload_form"))


@app.get("/media/<path:filename>")
def media(filename):
    try:
        return send_from_directory(UPLOAD_DIR, filename, as_attachment=False)
    except Exception as e:
        print(f"Erreur dans media(): {e}")
        abort(404)


@app.get("/hls/<path:filename>")
def hls(filename):
    try:
        return send_from_directory(HLS_DIR, filename, as_attachment=False)
    except Exception as e:
        print(f"Erreur dans hls(): {e}")
        abort(404)


# -------------------------
# Authentification
# -------------------------
@app.route("/login", methods=["GET", "POST"])
def login():
    try:
        if request.method == "POST":
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")

            # Recherche de l'utilisateur
            u = User.query.filter_by(email=email).first()

            if not u or not u.check_password(password):
                flash("Identifiants invalides")
            else:
                login_user(u)
                return redirect(url_for("home"))

        # Affichage du formulaire de connexion
        body = render_template_string(
            AUTH_BODY,
            heading="Connexion",
            cta="Se connecter",
            mode="login"
        )
        return render_template_string(
            BASE_HTML,
            body=body,
            year=datetime.utcnow().year,
            title="Connexion — Mitabo"
        )

    except Exception as e:
        # Log de l'erreur et retour 500
        print(f"Erreur dans login(): {e}")
        return f"Erreur: {e}", 500


@app.route("/register", methods=["GET", "POST"])
def register():
    try:
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
    except Exception as e:
        print(f"Erreur dans register(): {e}")
        return f"Erreur: {e}", 500

@app.get("/logout")
@login_required
def logout():
    try:
        logout_user()
        return redirect(url_for("home"))
    except Exception as e:
        print(f"Erreur dans logout(): {e}")
        return redirect(url_for("home"))

# -------------------------
# Commentaires
# -------------------------
@app.post("/watch/<int:video_id>/comment")
@login_required
def comment_post(video_id: int):
    try:
        v = Video.query.get_or_404(video_id)
        body = (request.form.get("body") or "").strip()
        if not body:
            flash("Commentaire vide")
            return redirect(url_for("watch", video_id=v.id))
        c = Comment(video_id=v.id, user_id=current_user.id, body=body)
        db.session.add(c)
        db.session.commit()
        return redirect(url_for("watch", video_id=v.id))
    except Exception as e:
        print(f"Erreur dans comment_post(): {e}")
        flash("Erreur lors de l'ajout du commentaire")
        return redirect(url_for("watch", video_id=video_id))

# -------------------------
# API minimale
# -------------------------
@app.get("/api/videos")
def api_videos():
    try:
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
    except Exception as e:
        print(f"Erreur dans api_videos(): {e}")
        return jsonify({"error": str(e)}), 500

# -------------------------
# Routes pour les likes/dislikes
# -------------------------
@app.route("/video/like/<int:video_id>", methods=["POST"])
@login_required
def like_video(video_id):
    try:
        v = Video.query.get_or_404(video_id)

        existing = Like.query.filter_by(user_id=current_user.id, video_id=v.id).first()
        if existing:
            if existing.is_like:
                # déjà liké -> supprimer le like (toggle)
                db.session.delete(existing)
                v.likes = max((v.likes or 1) - 1, 0)
            else:
                # transform dislike -> like
                existing.is_like = True
                v.likes = (v.likes or 0) + 1
                v.dislikes = max((v.dislikes or 1) - 1, 0)
        else:
            db.session.add(Like(user_id=current_user.id, video_id=v.id, is_like=True))
            v.likes = (v.likes or 0) + 1

        db.session.commit()
        return jsonify({"likes": v.likes, "dislikes": v.dislikes})
    except Exception as e:
        print(f"Erreur dans like_video(): {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/video/dislike/<int:video_id>", methods=["POST"])
@login_required
def dislike_video(video_id):
    try:
        v = Video.query.get_or_404(video_id)

        existing = Like.query.filter_by(user_id=current_user.id, video_id=v.id).first()
        if existing:
            if not existing.is_like:
                db.session.delete(existing)
                v.dislikes = max((v.dislikes or 1) - 1, 0)
            else:
                existing.is_like = False
                v.dislikes = (v.dislikes or 0) + 1
                v.likes = max((v.likes or 1) - 1, 0)
        else:
            db.session.add(Like(user_id=current_user.id, video_id=v.id, is_like=False))
            v.dislikes = (v.dislikes or 0) + 1

        db.session.commit()
        return jsonify({"likes": v.likes, "dislikes": v.dislikes})
    except Exception as e:
        print(f"Erreur dans dislike_video(): {e}")
        return jsonify({"error": str(e)}), 500

# -------------------------
# Route profil utilisateur
# -------------------------
@app.route("/profil/<username>")
def show_profil(username):
    try:
        user = User.query.filter_by(display_name=username).first_or_404()
        videos = Video.query.filter_by(user_id=user.id).order_by(Video.created_at.desc()).all()

        is_following = False
        if current_user.is_authenticated:
            is_following = Follow.query.filter_by(
                follower_id=current_user.id,
                followed_id=user.id
            ).first() is not None

        body = render_template_string(PROFIL_BODY, user=user, videos=videos, is_following=is_following)
        return render_template_string(BASE_HTML, body=body, year=datetime.utcnow().year, title=f"Profil de {user.display_name}")
    except Exception as e:
        print(f"Erreur dans show_profil(): {e}")
        return f"Erreur: {e}", 500

# -------------------------
# Route pour suivre/ne plus suivre
# -------------------------
@app.route("/follow/<int:user_id>", methods=["POST"])
@login_required
def follow_user(user_id):
    try:
        if user_id == current_user.id:
            return jsonify({"error": "Vous ne pouvez pas vous suivre vous-même"}), 400
        
        target_user = User.query.get_or_404(user_id)
        existing = Follow.query.filter_by(follower_id=current_user.id, followed_id=user_id).first()
        
        if existing:
            db.session.delete(existing)
            following = False
        else:
            db.session.add(Follow(follower_id=current_user.id, followed_id=user_id))
            following = True
        
        db.session.commit()
        return jsonify({"following": following})
    except Exception as e:
        print(f"Erreur dans follow_user(): {e}")
        return jsonify({"error": str(e)}), 500

# -------------------------
# Routes admin
# -------------------------
@app.route("/admin/ban/<int:user_id>")
@login_required
def ban_user(user_id):
    try:
        if not current_user.is_admin:
            flash("Accès refusé")
            return redirect(url_for("home"))
        
        user = User.query.get_or_404(user_id)
        if user.id != current_user.id:  # Éviter de se bannir soi-même
            db.session.delete(user)
            db.session.commit()
            flash(f"Utilisateur {user.display_name} banni")
        
        return redirect(url_for("home"))
    except Exception as e:
        print(f"Erreur dans ban_user(): {e}")
        flash("Erreur lors du bannissement")
        return redirect(url_for("home"))

@app.route("/admin/promote/<int:user_id>")
@login_required
def promote_user(user_id):
    try:
        if not current_user.is_admin:
            flash("Accès refusé")
            return redirect(url_for("home"))
        
        user = User.query.get_or_404(user_id)
        user.is_admin = True
        db.session.commit()
        flash(f"Utilisateur {user.display_name} promu admin")
        
        return redirect(url_for("home"))
    except Exception as e:
        print(f"Erreur dans promote_user(): {e}")
        flash("Erreur lors de la promotion")
        return redirect(url_for("home"))

# -------------------------
# Route favicon
# -------------------------
@app.route('/favicon.ico')
def favicon():
    try:
        # Créer le favicon s'il n'existe pas
        favicon_path = os.path.join(BASE_DIR, "favicon.ico")
        if not os.path.exists(favicon_path):
            # Créer un favicon simple
            img = Image.new('RGB', (32, 32), color='blue')
            img.save(favicon_path, format="ICO")
        return send_file(favicon_path, mimetype='image/x-icon')
    except Exception:
        # Retourner une réponse vide si erreur
        return '', 204

# -------------------------
# Gestion d'erreurs
# -------------------------
@app.errorhandler(404)
def not_found_error(error):
    body = "<main class='container mx-auto px-4 py-8 text-center'><h1 class='text-2xl font-bold'>Page non trouvée</h1><p class='mt-4'><a href='" + url_for('home') + "' class='text-blue-600'>Retour à l'accueil</a></p></main>"
    return render_template_string(BASE_HTML, body=body, year=datetime.utcnow().year, title="Erreur 404"), 404

@app.errorhandler(500)
def internal_error(error):
    db.session.rollback()
    body = "<main class='container mx-auto px-4 py-8 text-center'><h1 class='text-2xl font-bold'>Erreur interne</h1><p class='mt-4'><a href='" + url_for('home') + "' class='text-blue-600'>Retour à l'accueil</a></p></main>"
    return render_template_string(BASE_HTML, body=body, year=datetime.utcnow().year, title="Erreur 500"), 500

# -------------------------
# Commande CLI pour initialiser la DB
# -------------------------
@app.cli.command()
def init_database():
    """Initialise la base de données"""
    init_db()

# -------------------------
# Entrée app
# -------------------------
if __name__ == "__main__":
    # Initialiser la DB au démarrage
    init_db()
    app.run(debug=True, host='0.0.0.0', port=5000)
    from flask import Flask, render_template_string, request, redirect, url_for, flash, send_from_directory, send_file, abort, jsonify
from flask_login import LoginManager, login_user, logout_user, current_user, login_required

















