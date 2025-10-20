import os
import time
from flask import (
    Flask, request, render_template_string, url_for, redirect,
    send_from_directory, abort, jsonify, flash, send_file
)
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_login import (
    LoginManager, UserMixin, login_user, login_required,
    logout_user, current_user
)
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
import subprocess
import shutil
from PIL import Image
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

# ⚡ Import extensions AVANT les modèles
from extensions import db, migrate  # doit exister dans extensions.py

# ------------------------------
# Configuration des répertoires
# ------------------------------
BASE_DIR = os.path.dirname(__file__)
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
HLS_DIR = os.path.join(BASE_DIR, "hls")

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(HLS_DIR, exist_ok=True)

# ------------------------------
# Création de l'application Flask
# ------------------------------
app = Flask(__name__)

# ------------------------------
# Configuration de la base de données
# ------------------------------
uri = os.getenv("DATABASE_URL")
if not uri:
    raise RuntimeError("DATABASE_URL not set! Configure it in Render environment variables.")

# Corrige l'ancien format postgres://
if uri.startswith("postgres://"):
    uri = uri.replace("postgres://", "postgresql://", 1)

# Force SSL pour Supabase si nécessaire
if "supabase.co" in uri and "sslmode=" not in uri:
    if "?" in uri:
        uri += "&sslmode=require"
    else:
        uri += "?sslmode=require"

# Configuration SQLAlchemy + app
app.config.update(
    SQLALCHEMY_DATABASE_URI=uri,
    SQLALCHEMY_TRACK_MODIFICATIONS=False,
    SECRET_KEY="dev-mitabo-secret-key-change-in-production",
    MAX_CONTENT_LENGTH=1024 * 1024 * 1024,  # 1 Go max upload
    DEBUG=True,
    SQLALCHEMY_ENGINE_OPTIONS={
        "pool_size": 5,
        "max_overflow": 10,
        "pool_timeout": 30,
        "pool_pre_ping": True,
        "pool_recycle": 300,  # Recycle toutes les 5 minutes au lieu de 30
        "connect_args": {
            "sslmode": "require",
            "connect_timeout": 10,
            "keepalives": 1,
            "keepalives_idle": 30,
            "keepalives_interval": 10,
            "keepalives_count": 5
        }
    }
)

# ------------------------------
# Initialisation DB et Migrate avec l'app
# ------------------------------
db.init_app(app)
migrate.init_app(app, db)

# ✅ Import des modèles APRÈS l'initialisation
from models import Video, Like, Xp, User, Follow, Comment

# ------------------------------
# Bloc retry connexion DB au démarrage (important pour Render)
# ------------------------------
with app.app_context():
    retries = 5
    for i in range(retries):
        try:
            db.session.execute(text("SELECT 1"))
            print("✅ Connexion à la base de données réussie")
            break
        except OperationalError as e:
            print(f"⚠️ Tentative {i+1}/{retries} : connexion DB échouée — {e}")
            time.sleep(3)
    else:
        raise RuntimeError("❌ Impossible de se connecter à la base après plusieurs tentatives")

# ------------------------------
# Middleware pour gérer les déconnexions DB
# ------------------------------
@app.before_request
def before_request():
    """Vérifie la connexion DB avant chaque requête"""
    try:
        db.session.execute(text("SELECT 1"))
    except OperationalError:
        print("⚠️ Connexion DB perdue, tentative de reconnexion...")
        db.session.rollback()
        try:
            db.session.execute(text("SELECT 1"))
            print("✅ Reconnexion réussie")
        except Exception as e:
            print(f"❌ Reconnexion échouée: {e}")

@app.teardown_appcontext
def shutdown_session(exception=None):
    """Nettoie la session DB après chaque requête"""
    if exception:
        db.session.rollback()
    db.session.remove()

# ------------------------------
# Initialisation LoginManager
# ------------------------------
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# ------------------------------
# Supabase Storage centralisé
# ------------------------------
from supabase_config import supabase, BUCKET_NAME

# ------------------------------
# Création automatique des tables (Render inclus)
# ------------------------------
with app.app_context():
    db.create_all()

# -------------------------
# Données constantes
# -------------------------
CATEGORIES = [
    {"id": "tendance", "label": "Tendances"},
    {"id": "jeux", "label": "Jeux"},
    {"id": "musique", "label": "Musique"},
    {"id": "film", "label": "Films & Anim"},
    {"id": "sports", "label": "Sports"},
    {"id": "football", "label": "Football"},
    {"id": "basket", "label": "Basket"},
    {"id": "skateboard", "label": "Skateboard"},
    {"id": "tennis", "label": "Tennis"},
    {"id": "politique", "label": "Politique"},
    {"id": "france", "label": "France"},
    {"id": "alsace", "label": "Alsace"},
    {"id": "paris", "label": "Paris"},
    {"id": "iledefrance", "label": "Île de France"},
    {"id": "grandest", "label": "Grand-Est"},
    {"id": "actualite", "label": "Actualité"},
    {"id": "divertissement", "label": "Divertissement"},
    {"id": "usa", "label": "États-Unis"},
    {"id": "ue", "label": "L'Union Européenne"},
]
CATEGORIES_MAP = {c["id"]: c for c in CATEGORIES}
ALLOWED_EXTENSIONS = {"mp4", "webm", "ogg", "mov", "m4v"}

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
                        creator="MitaboBigBuckBunny",
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
    <style>
        body {
            background-size: cover;
            background-position: center;
            background-attachment: fixed;
            transition: background-image 0.3s ease;
        }
    </style>
</head>
<body class="bg-gray-50">
    <nav class="bg-white shadow-sm border-b">
        <div class="container mx-auto px-4 py-3 flex items-center justify-between">
            <a href="{{ url_for('home') }}" class="text-xl font-bold text-blue-600">Mitabo</a>
            <div class="flex items-center space-x-4">
                {% if current_user.is_authenticated %}
                    <a href="{{ url_for('upload_form') }}" class="bg-blue-500 text-white px-4 py-2 rounded hover:bg-blue-600">Upload</a>
                    
                    <!-- Bouton personnalisation fond d'écran -->
                    <button onclick="openBackgroundModal()" class="p-2 rounded hover:bg-gray-100" title="Personnaliser le fond d'écran">
                        <svg class="w-6 h-6 text-gray-800" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z"></path>
                        </svg>
                    </button>
                    
                    <span class="text-gray-700">{{ current_user.display_name }}</span>
                    
                    <!-- Menu Hamburger -->
                    <div class="relative">
                        <button onclick="toggleMenu()" class="p-2 rounded hover:bg-gray-100" id="menu-button">
                            <svg class="w-6 h-6 text-blue-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6h16M4 12h16M4 18h16"></path>
                            </svg>
                        </button>
                        
                        <!-- Dropdown Menu -->
                        <div id="dropdown-menu" class="hidden absolute right-0 mt-2 w-48 bg-white rounded-lg shadow-lg border z-50">
                            <a href="{{ url_for('show_profil', username=current_user.display_name) }}" 
                               class="block px-4 py-3 text-gray-700 hover:bg-gray-100 rounded-t-lg">
                                Profil
                            </a>
                            <a href="{{ url_for('reglement') }}" 
                               class="block px-4 py-3 text-gray-700 hover:bg-gray-100">
                                Règlement
                            </a>
                            <a href="{{ url_for('parametres') }}" 
                               class="block px-4 py-3 text-gray-700 hover:bg-gray-100">
                                Paramètres
                            </a>
                            <hr class="my-1">
                            <a href="{{ url_for('logout') }}" 
                               class="block px-4 py-3 text-red-600 hover:bg-red-50 rounded-b-lg">
                                Déconnexion
                            </a>
                        </div>
                    </div>
                {% else %}
                    <a href="{{ url_for('login') }}" class="text-blue-600 hover:text-blue-800">Connexion</a>
                    <a href="{{ url_for('register') }}" class="bg-blue-500 text-white px-4 py-2 rounded hover:bg-blue-600">Inscription</a>
                {% endif %}
            </div>
        </div>
    </nav>
    
    <!-- Modale de sélection de fond d'écran -->
    <div id="background-modal" class="hidden fixed inset-0 bg-black bg-opacity-50 z-50 flex items-center justify-center p-4">
        <div class="bg-white rounded-lg max-w-4xl w-full max-h-[90vh] overflow-y-auto">
            <div class="p-6">
                <div class="flex items-center justify-between mb-6">
                    <h2 class="text-2xl font-bold">Personnaliser le fond d'écran</h2>
                    <button onclick="closeBackgroundModal()" class="text-gray-500 hover:text-gray-700">
                        <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"></path>
                        </svg>
                    </button>
                </div>
                
                <div class="grid grid-cols-2 md:grid-cols-3 gap-4">
                    <!-- Par défaut -->
                    <div class="cursor-pointer group" onclick="setBackground('default')">
                        <div class="aspect-video bg-gray-100 rounded-lg overflow-hidden border-2 border-transparent hover:border-blue-500 transition">
                            <div class="w-full h-full flex items-center justify-center text-gray-400">
                                <span class="text-sm">Par défaut</span>
                            </div>
                        </div>
                        <p class="text-center mt-2 text-sm font-medium">Par défaut</p>
                    </div>
                    
                    <!-- Nature -->
                    <div class="cursor-pointer group" onclick="setBackground('nature')">
                        <div class="aspect-video bg-cover bg-center rounded-lg overflow-hidden border-2 border-transparent hover:border-blue-500 transition" 
                             style="background-image: url('https://images.unsplash.com/photo-1441974231531-c6227db76b6e?w=800')">
                        </div>
                        <p class="text-center mt-2 text-sm font-medium">Nature</p>
                    </div>
                    
                    <!-- Plage -->
                    <div class="cursor-pointer group" onclick="setBackground('plage')">
                        <div class="aspect-video bg-cover bg-center rounded-lg overflow-hidden border-2 border-transparent hover:border-blue-500 transition" 
                             style="background-image: url('https://images.unsplash.com/photo-1507525428034-b723cf961d3e?w=800')">
                        </div>
                        <p class="text-center mt-2 text-sm font-medium">Plage</p>
                    </div>
                    
                    <!-- Galaxy -->
                    <div class="cursor-pointer group" onclick="setBackground('galaxy')">
                        <div class="aspect-video bg-cover bg-center rounded-lg overflow-hidden border-2 border-transparent hover:border-blue-500 transition" 
                             style="background-image: url('https://images.unsplash.com/photo-1419242902214-272b3f66ee7a?w=800')">
                        </div>
                        <p class="text-center mt-2 text-sm font-medium">Galaxy</p>
                    </div>
                    
                    <!-- Abstrait -->
                    <div class="cursor-pointer group" onclick="setBackground('abstrait')">
                        <div class="aspect-video bg-cover bg-center rounded-lg overflow-hidden border-2 border-transparent hover:border-blue-500 transition" 
                             style="background-image: url('https://images.unsplash.com/photo-1557672172-298e090bd0f1?w=800')">
                        </div>
                        <p class="text-center mt-2 text-sm font-medium">Abstrait</p>
                    </div>
                    
                    <!-- Montagne -->
                    <div class="cursor-pointer group" onclick="setBackground('montagne')">
                        <div class="aspect-video bg-cover bg-center rounded-lg overflow-hidden border-2 border-transparent hover:border-blue-500 transition" 
                             style="background-image: url('https://images.unsplash.com/photo-1506905925346-21bda4d32df4?w=800')">
                        </div>
                        <p class="text-center mt-2 text-sm font-medium">Montagne</p>
                    </div>
                    
                    <!-- Ville -->
                    <div class="cursor-pointer group" onclick="setBackground('ville')">
                        <div class="aspect-video bg-cover bg-center rounded-lg overflow-hidden border-2 border-transparent hover:border-blue-500 transition" 
                             style="background-image: url('https://images.unsplash.com/photo-1480714378408-67cf0d13bc1b?w=800')">
                        </div>
                        <p class="text-center mt-2 text-sm font-medium">Ville</p>
                    </div>
                    
                    <!-- Coucher de soleil -->
                    <div class="cursor-pointer group" onclick="setBackground('sunset')">
                        <div class="aspect-video bg-cover bg-center rounded-lg overflow-hidden border-2 border-transparent hover:border-blue-500 transition" 
                             style="background-image: url('https://images.unsplash.com/photo-1495567720989-cebdbdd97913?w=800')">
                        </div>
                        <p class="text-center mt-2 text-sm font-medium">Coucher de soleil</p>
                    </div>
                    
                    <!-- Gradient -->
                    <div class="cursor-pointer group" onclick="setBackground('gradient')">
                        <div class="aspect-video rounded-lg overflow-hidden border-2 border-transparent hover:border-blue-500 transition" 
                             style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%)">
                        </div>
                        <p class="text-center mt-2 text-sm font-medium">Gradient</p>
                    </div>
                </div>
            </div>
        </div>
    </div>
    
    {% with messages = get_flashed_messages() %}
        {% if messages %}
            <div class="container mx-auto px-4 py-2">
                {% for message in messages %}
                    <div class="bg-blue-100 border border-blue-400 text-blue-700 px-4 py-3 rounded mb-4">{{ message }}</div>
                {% endfor %}
            </div>
        {% endif %}
    {% endwith %}
    
    {{ body|safe }}
    
    <footer class="bg-gray-800 text-white text-center py-4 mt-12">
        <p>&copy; {{ year }} Mitabo</p>
    </footer>
    
    <script>
        // Backgrounds disponibles
        const backgrounds = {
            'default': 'none',
            'nature': 'url("https://images.unsplash.com/photo-1441974231531-c6227db76b6e?w=1920")',
            'plage': 'url("https://images.unsplash.com/photo-1507525428034-b723cf961d3e?w=1920")',
            'galaxy': 'url("https://images.unsplash.com/photo-1419242902214-272b3f66ee7a?w=1920")',
            'abstrait': 'url("https://images.unsplash.com/photo-1557672172-298e090bd0f1?w=1920")',
            'montagne': 'url("https://images.unsplash.com/photo-1506905925346-21bda4d32df4?w=1920")',
            'ville': 'url("https://images.unsplash.com/photo-1480714378408-67cf0d13bc1b?w=1920")',
            'sunset': 'url("https://images.unsplash.com/photo-1495567720989-cebdbdd97913?w=1920")',
            'gradient': 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)'
        };
        
        // Charger le fond d'écran sauvegardé au chargement de la page
        window.addEventListener('DOMContentLoaded', function() {
            const savedBg = localStorage.getItem('mitabo_background');
            if (savedBg && backgrounds[savedBg]) {
                if (savedBg === 'default') {
                    document.body.style.background = '#f7f8fa';
                } else {
                    document.body.style.backgroundImage = backgrounds[savedBg];
                }
            }
        });
        
        function openBackgroundModal() {
            document.getElementById('background-modal').classList.remove('hidden');
        }
        
        function closeBackgroundModal() {
            document.getElementById('background-modal').classList.add('hidden');
        }
        
        function setBackground(bgName) {
            if (bgName === 'default') {
                document.body.style.backgroundImage = 'none';
                document.body.style.background = '#f7f8fa';
            } else {
                document.body.style.backgroundImage = backgrounds[bgName];
            }
            localStorage.setItem('mitabo_background', bgName);
            closeBackgroundModal();
        }
        
        function toggleMenu() {
            const menu = document.getElementById('dropdown-menu');
            menu.classList.toggle('hidden');
        }
        
        // Fermer le menu si on clique ailleurs
        document.addEventListener('click', function(event) {
            const menu = document.getElementById('dropdown-menu');
            const button = document.getElementById('menu-button');
            if (button && !button.contains(event.target) && !menu.contains(event.target)) {
                menu.classList.add('hidden');
            }
        });
        
        // Fermer la modale si on clique en dehors
        document.getElementById('background-modal')?.addEventListener('click', function(event) {
            if (event.target === this) {
                closeBackgroundModal();
            }
        });
    </script>
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
        
        <div class="flex gap-2 overflow-x-auto">
            {% for cat in categories %}
                <a href="?cat={{ cat.id }}&q={{ q }}" 
                   class="px-4 py-2 rounded-lg whitespace-nowrap {% if cat.id == active_cat %}bg-blue-500 text-white{% else %}bg-gray-200{% endif %}">
                    {{ cat.label }}
                </a>
            {% endfor %}
        </div>
    </div>
    
    <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-6">
        {% for video in items %}
            <div class="bg-white rounded-lg shadow-sm overflow-hidden hover:shadow-md transition">
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
                    <button onclick="likeVideo({{ video.id }})" 
                            class="flex items-center space-x-1 px-3 py-1 rounded bg-gray-200 hover:bg-gray-300">
                        <span>👍</span>
                        <span id="likes-count">{{ video.likes or 0 }}</span>
                    </button>

                    <button onclick="dislikeVideo({{ video.id }})" 
                            class="flex items-center space-x-1 px-3 py-1 rounded bg-gray-200 hover:bg-gray-300">
                        <span>👎</span>
                        <span id="dislikes-count">{{ video.dislikes or 0 }}</span>
                    </button>

                    <button onclick="giveXp({{ video.id }})" 
                            class="flex items-center space-x-1 px-3 py-1 rounded bg-yellow-200 hover:bg-yellow-300">
                        <span>✨ XP</span>
                        <span id="xp-count">{{ video.xp or 0 }}</span>
                    </button>
                </div>
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
                <div class="bg-white rounded-lg shadow-sm overflow-hidden hover:shadow-md transition">
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

function giveXp(videoId) {
    fetch(`/video/${videoId}/xp`, { method: "POST" })
        .then(res => res.json())
        .then(data => {
            document.getElementById("xp-count").textContent = data.xp;
        })
        .catch(err => console.error('Erreur XP:', err));
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
            
            <button type="submit" class="bg-blue-500 text-white px-6 py-2 rounded-lg hover:bg-blue-600">
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
        <div>
            <label class="block text-sm font-medium mb-1">Nom d'utilisateur</label>
            <input name="username" type="text" required 
                   class="w-full px-3 py-2 border rounded-lg">
        </div>
        
        <div>
            <label class="block text-sm font-medium mb-1">Mot de passe</label>
            <input name="password" type="password" required 
                   class="w-full px-3 py-2 border rounded-lg">
        </div>
        
        <button type="submit" class="w-full bg-blue-500 text-white py-2 rounded-lg hover:bg-blue-600">
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
    <div class="max-w-4xl mx-auto">
        <!-- En-tête du profil -->
        <div class="bg-white rounded-lg shadow-sm p-6 mb-6">
            <div class="flex items-start space-x-6">
                <!-- Avatar -->
                <div class="flex-shrink-0">
                    <img src="https://ui-avatars.com/api/?name={{ user.display_name }}&size=120&background=3b82f6&color=fff" 
                         alt="Avatar de {{ user.display_name }}" 
                         class="w-32 h-32 rounded-full border-4 border-blue-500">
                </div>
                
                <!-- Informations -->
                <div class="flex-1">
                    <div class="flex items-center justify-between mb-4">
                        <h2 class="text-3xl font-bold">{{ user.display_name }}</h2>
                        
                        <!-- Bouton follow/unfollow -->
                        {% if current_user.is_authenticated and current_user.id != user.id %}
                            {% if is_following %}
                                <button onclick="followUser({{ user.id }})" id="follow-btn" 
                                        class="px-6 py-2 rounded-lg bg-gray-300 hover:bg-gray-400 transition">
                                    Se désabonner
                                </button>
                            {% else %}
                                <button onclick="followUser({{ user.id }})" id="follow-btn" 
                                        class="px-6 py-2 rounded-lg bg-blue-500 text-white hover:bg-blue-600 transition">
                                    S'abonner
                                </button>
                            {% endif %}
                        {% endif %}
                    </div>
                    
                    <p class="text-gray-600 mb-4">{{ user.email }}</p>
                    
                    <!-- Statistiques -->
                    <div class="flex space-x-6 text-sm">
                        <div>
                            <span class="font-semibold text-gray-800">{{ videos|length }}</span>
                            <span class="text-gray-600">vidéos</span>
                        </div>
                        <div>
                            <span class="font-semibold text-gray-800" id="followers-count">{{ user.followers.count() }}</span>
                            <span class="text-gray-600">abonnés</span>
                        </div>
                        <div>
                            <span class="font-semibold text-gray-800">{{ user.following.count() }}</span>
                            <span class="text-gray-600">abonnements</span>
                        </div>
                    </div>
                    
                    <p class="text-gray-500 text-sm mt-4">
                        Membre depuis le {{ user.created_at.strftime('%d %B %Y') }}
                    </p>
                </div>
            </div>
        </div>
        
        <!-- Vidéos de l'utilisateur -->
        <div class="bg-white rounded-lg shadow-sm p-6">
            <h3 class="text-xl font-semibold mb-4">Vidéos de {{ user.display_name }}</h3>
            
            {% if videos %}
                <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
                    {% for v in videos %}
                        <div class="bg-gray-50 rounded-lg overflow-hidden hover:shadow-md transition">
                            <a href="{{ url_for('watch', video_id=v.id) }}">
                                {% if v.thumb_url %}
                                    <img src="{{ v.thumb_url }}" alt="{{ v.title }}" class="w-full h-40 object-cover">
                                {% else %}
                                    <div class="w-full h-40 bg-gray-300 flex items-center justify-center">
                                        <span class="text-gray-500">Pas de miniature</span>
                                    </div>
                                {% endif %}
                            </a>
                            <div class="p-3">
                                <h4 class="font-semibold text-sm mb-1">
                                    <a href="{{ url_for('watch', video_id=v.id) }}" class="hover:text-blue-600">
                                        {{ v.title }}
                                    </a>
                                </h4>
                                <p class="text-gray-500 text-xs">{{ v.views or 0 }} vues</p>
                                <p class="text-gray-400 text-xs">{{ v.created_at.strftime('%d %b %Y') }}</p>
                            </div>
                        </div>
                    {% endfor %}
                </div>
            {% else %}
                <div class="text-center py-8 text-gray-500">
                    <svg class="w-16 h-16 mx-auto mb-4 text-gray-300" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 10l4.553-2.276A1 1 0 0121 8.618v6.764a1 1 0 01-1.447.894L15 14M5 18h8a2 2 0 002-2V8a2 2 0 00-2-2H5a2 2 0 00-2 2v8a2 2 0 002 2z"></path>
                    </svg>
                    <p>Aucune vidéo postée pour le moment.</p>
                </div>
            {% endif %}
        </div>
    </div>
</main>

<script>
function followUser(userId) {
    fetch(`/follow/${userId}`, {method: 'POST'})
        .then(r => r.json())
        .then(data => {
            const btn = document.getElementById('follow-btn');
            const followersCount = document.getElementById('followers-count');
            
            if (data.following) {
                btn.textContent = 'Se désabonner';
                btn.className = 'px-6 py-2 rounded-lg bg-gray-300 hover:bg-gray-400 transition';
                followersCount.textContent = parseInt(followersCount.textContent) + 1;
            } else {
                btn.textContent = "S'abonner";
                btn.className = 'px-6 py-2 rounded-lg bg-blue-500 text-white hover:bg-blue-600 transition';
                followersCount.textContent = Math.max(0, parseInt(followersCount.textContent) - 1);
            }
        })
        .catch(err => console.error('Erreur follow:', err));
}
</script>
"""

# -------------------------
# Routes Authentification
# -------------------------

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")

        if not username or not password:
            flash("Veuillez remplir tous les champs.")
            return redirect(url_for("register"))

        existing = User.query.filter_by(display_name=username).first()
        if existing:
            flash("Ce nom d'utilisateur existe déjà.")
            return redirect(url_for("register"))

        hashed = generate_password_hash(password)
        # Générer un email automatique basé sur le username
        email = f"{username.lower().replace(' ', '')}@mitabo.local"
        new_user = User(display_name=username, email=email, password_hash=hashed)
        db.session.add(new_user)
        db.session.commit()

        flash("✅ Inscription réussie, vous pouvez vous connecter.")
        return redirect(url_for("login"))

    body = render_template_string(AUTH_BODY, mode='register', heading='Inscription', cta='S\'inscrire')
    return render_template_string(BASE_HTML, body=body, year=datetime.utcnow().year, title="Inscription")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")

        user = User.query.filter_by(display_name=username).first()
        if not user or not check_password_hash(user.password_hash, password):
            flash("Nom d'utilisateur ou mot de passe incorrect.")
            return redirect(url_for("login"))

        login_user(user)
        flash(f"Bienvenue, {user.display_name} 👋")
        return redirect(url_for("home"))

    body = render_template_string(AUTH_BODY, mode='login', heading='Connexion', cta='Se connecter')
    return render_template_string(BASE_HTML, body=body, year=datetime.utcnow().year, title="Connexion")

@app.route("/logout")
@login_required
def logout():
    logout_user()
    flash("Vous êtes déconnecté.")
    return redirect(url_for("login"))

# -------------------------
# Routes Règlement et Paramètres
# -------------------------
@app.route("/reglement")
def reglement():
    """Page du règlement de la plateforme"""
    body = """
    <style>
        .reglement-container { max-width: 900px; margin: 32px auto; background: #fff; border-radius: 8px; box-shadow: 0 6px 18px rgba(17,17,17,0.06); padding: 28px; }
        .reglement-container header h1 { margin: 0 0 8px 0; font-size: 24px; letter-spacing: 0.2px; }
        .reglement-container .meta { color: #6b7280; font-size: 13px; margin-bottom: 18px; }
        .reglement-container hr { border: none; border-top: 1px solid #e6e9ee; margin: 22px 0; }
        .reglement-container h2 { font-size: 18px; margin: 18px 0 8px; }
        .reglement-container p, .reglement-container li { line-height: 1.55; font-size: 15px; }
        .reglement-container ol { padding-left: 1.2em; }
        .reglement-container ul { padding-left: 1.2em; }
        .reglement-container .article { margin-bottom: 12px; }
        .reglement-container .foot { color: #6b7280; font-size: 13px; margin-top: 18px; }
    </style>
    
    <div class="reglement-container">
        <header>
            <h1>Règlement Officiel de Mitabo</h1>
            <div class="meta">Version officielle — Ton administratif</div>
        </header>
        
        <section class="article">
            <h2>Article 1 – Objet du règlement</h2>
            <p>Le présent règlement a pour objet de définir les conditions de publication, de diffusion et d'utilisation de la plateforme <strong>Mitabo</strong>. Il vise à assurer un environnement respectueux, créatif et conforme à la législation en vigueur.</p>
        </section>
        <hr />
        
        <section class="article">
            <h2>Article 2 – Format et durée des vidéos</h2>
            <ol>
                <li>Les vidéos publiées sur Mitabo doivent avoir une durée comprise entre <strong>3 et 5 minutes</strong>.</li>
                <li>Le format recommandé est horizontal (16:9) avec une qualité minimale de 720p (HD).</li>
                <li>Les vidéos doivent être montées, finalisées et conformes aux standards de qualité avant leur mise en ligne.</li>
            </ol>
        </section>
        <hr />
        
        <section class="article">
            <h2>Article 3 – Contenu autorisé</h2>
            <p>Sont autorisés :</p>
            <ul>
                <li>Les créations originales (documentaires, tutoriels, vlogs, courts-métrages, etc.) ;</li>
                <li>Les contenus respectueux de la loi, des droits d'auteur et de la dignité des personnes ;</li>
                <li>Les musiques et extraits sous licence libre ou disposant d'une autorisation d'utilisation.</li>
            </ul>
        </section>
        <hr />
        
        <section class="article">
            <h2>Article 4 – Contenu interdit</h2>
            <p>Sont formellement interdits :</p>
            <ul>
                <li>Les propos ou images à caractère haineux, violent, discriminatoire ou diffamatoire ;</li>
                <li>Les contenus mensongers, trompeurs ou incitant à des comportements dangereux ;</li>
                <li>La diffusion de données personnelles sans consentement préalable ;</li>
                <li>Toute forme de plagiat ou d'atteinte aux droits d'autrui.</li>
            </ul>
        </section>
        <hr />
        
        <section class="article">
            <h2>Article 5 – Comportement des utilisateurs</h2>
            <p>Les utilisateurs de Mitabo doivent :</p>
            <ul>
                <li>Adopter une attitude respectueuse envers la communauté et l'équipe de modération ;</li>
                <li>Publier et commenter de manière constructive et courtoise ;</li>
                <li>Signaler tout contenu non conforme au présent règlement.</li>
            </ul>
        </section>
        <hr />
        
        <section class="article">
            <h2>Article 6 – Sanctions</h2>
            <p>Tout manquement au présent règlement pourra entraîner :</p>
            <ol>
                <li>Un avertissement écrit adressé à l'utilisateur concerné ;</li>
                <li>Une suspension temporaire du compte en cas de récidive ;</li>
                <li>Une exclusion définitive en cas de manquement grave ou répété.</li>
            </ol>
            <p>Les décisions de modération sont prises avec impartialité et dans le respect du droit d'expression de chacun.</p>
        </section>
        <hr />
        
        <section class="article">
            <h2>Article 7 – Entrée en vigueur</h2>
            <p>Le présent règlement entre en vigueur à compter de sa publication officielle sur la plateforme Mitabo. Toute utilisation du service implique l'acceptation sans réserve des dispositions énoncées ci‑dessus.</p>
        </section>
        
        <div class="foot">
            <a href="{{ url_for('home') }}" class="text-blue-600 hover:text-blue-800">← Retour à l'accueil</a>
        </div>
    </div>
    """
    return render_template_string(BASE_HTML, body=body, year=datetime.utcnow().year, title="Règlement Officiel — Mitabo")

@app.route("/parametres", methods=["GET", "POST"])
@login_required
def parametres():
    """Page des paramètres utilisateur"""
    if request.method == "POST":
        new_display_name = request.form.get("display_name")
        new_email = request.form.get("email")
        
        if new_display_name and new_display_name != current_user.display_name:
            current_user.display_name = new_display_name
            db.session.commit()
            flash("✅ Nom d'affichage mis à jour")
        
        if new_email and new_email != current_user.email:
            current_user.email = new_email
            db.session.commit()
            flash("✅ Email mis à jour")
        
        return redirect(url_for("parametres"))
    
    body = """
    <main class="container mx-auto px-4 py-8 max-w-2xl">
        <h1 class="text-3xl font-bold mb-6">⚙️ Paramètres</h1>
        
        <div class="bg-white rounded-lg shadow-sm p-6">
            <form method="POST" class="space-y-4">
                <div>
                    <label class="block text-sm font-medium mb-1">Nom d'affichage</label>
                    <input name="display_name" type="text" value="{{ current_user.display_name }}" 
                           class="w-full px-3 py-2 border rounded-lg">
                </div>
                
                <div>
                    <label class="block text-sm font-medium mb-1">Email</label>
                    <input name="email" type="email" value="{{ current_user.email }}" 
                           class="w-full px-3 py-2 border rounded-lg">
                </div>
                
                <div class="pt-4">
                    <button type="submit" class="bg-blue-500 text-white px-6 py-2 rounded-lg hover:bg-blue-600">
                        Enregistrer les modifications
                    </button>
                </div>
            </form>
            
            <hr class="my-6">
            
            <div>
                <h2 class="text-lg font-semibold mb-2">Informations du compte</h2>
                <p class="text-gray-600 text-sm">Membre depuis : {{ current_user.created_at.strftime('%d %B %Y') }}</p>
                <p class="text-gray-600 text-sm">Nombre de vidéos : {{ current_user.videos|length }}</p>
            </div>
        </div>
        
        <div class="mt-6 text-center">
            <a href="{{ url_for('home') }}" class="text-blue-600 hover:text-blue-800">← Retour à l'accueil</a>
        </div>
    </main>
    """
    return render_template_string(BASE_HTML, body=body, year=datetime.utcnow().year, title="Paramètres — Mitabo")

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

        filename = secure_filename(f.filename)
        base, ext = os.path.splitext(filename)
        counter = 1
        final = filename
        while os.path.exists(os.path.join(UPLOAD_DIR, final)):
            final = f"{base}-{counter}{ext}"
            counter += 1

        file_path = os.path.join(UPLOAD_DIR, final)
        os.makedirs(UPLOAD_DIR, exist_ok=True)
        f.save(file_path)
        print("DEBUG: fichier sauvegardé =", file_path)

        public_url = None
        # Upload vers Supabase OBLIGATOIRE
        if supabase:
            try:
                with open(file_path, "rb") as file_data:
                    res = supabase.storage.from_(BUCKET_NAME).upload(
                        f"videos/{final}",
                        file_data,
                        {"content-type": f.mimetype or "video/mp4"}
                    )
                print("DEBUG: réponse Supabase =", res)

                # Récupérer l'URL publique
                public_url = supabase.storage.from_(BUCKET_NAME).get_public_url(f"videos/{final}")
                print("DEBUG: URL publique Supabase =", public_url)

            except Exception as e:
                print(f"Erreur upload vers Supabase: {e}")
                flash(f"Erreur lors de l'upload Supabase: {e}")
                # Supprimer le fichier local et abandonner
                if os.path.exists(file_path):
                    os.remove(file_path)
                return redirect(url_for("upload_form"))
        else:
            flash("Supabase non configuré - impossible d'uploader")
            return redirect(url_for("upload_form"))

        # Supprimer le fichier local après upload réussi vers Supabase
        if os.path.exists(file_path):
            os.remove(file_path)
            print("DEBUG: fichier local supprimé après upload Supabase")

        v = Video(
            title=title,
            description=description,
            category=category if category in CATEGORIES_MAP else "tendance",
            filename=final,
            thumb_url=f"https://picsum.photos/seed/mitabo-{base}/640/360",
            duration="",
            creator=creator,
            user_id=current_user.id,
            external_url=public_url  # Utiliser l'URL Supabase
        )

        db.session.add(v)
        db.session.commit()

        flash("✅ Vidéo uploadée avec succès sur Supabase !")
        return redirect(url_for("watch", video_id=v.id))

    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"Erreur dans upload_post(): {e}")
        flash(f"Erreur lors de l'upload: {e}")
        return redirect(url_for("upload_form"))

@app.get("/hls/<path:filename>")
def hls(filename):
    try:
        return send_from_directory(HLS_DIR, filename, as_attachment=False)
    except Exception as e:
        print(f"Erreur dans hls(): {e}")
        abort(404)

@app.get("/media/<path:filename>")
def media(filename):
    """Route pour servir les fichiers vidéo uploadés localement"""
    try:
        return send_from_directory(UPLOAD_DIR, filename, as_attachment=False)
    except Exception as e:
        print(f"Erreur dans media(): {e}")
        abort(404)

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
                db.session.delete(existing)
            else:
                existing.is_like = True
        else:
            db.session.add(Like(user_id=current_user.id, video_id=v.id, is_like=True))

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
            else:
                existing.is_like = False
        else:
            db.session.add(Like(user_id=current_user.id, video_id=v.id, is_like=False))

        db.session.commit()
        return jsonify({"likes": v.likes, "dislikes": v.dislikes})
    except Exception as e:
        print(f"Erreur dans dislike_video(): {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/video/<int:video_id>/xp", methods=["POST"])
@login_required
def give_xp(video_id):
    try:
        v = Video.query.get_or_404(video_id)
        
        existing = Xp.query.filter_by(user_id=current_user.id, video_id=v.id).first()
        if not existing:
            db.session.add(Xp(user_id=current_user.id, video_id=v.id))
            db.session.commit()
        
        return jsonify({"xp": v.xp})
    except Exception as e:
        print(f"Erreur dans give_xp(): {e}")
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
        if user.id != current_user.id:
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
        favicon_path = os.path.join(BASE_DIR, "favicon.ico")
        if not os.path.exists(favicon_path):
            img = Image.new('RGB', (32, 32), color='blue')
            img.save(favicon_path, format="ICO")
        return send_file(favicon_path, mimetype='image/x-icon')
    except Exception:
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
    init_db()
    app.run(debug=True, host='0.0.0.0', port=5000)














































