# models.py
from datetime import datetime
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from extensions import db
from flask import url_for

class User(UserMixin, db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, index=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    display_name = db.Column(db.String(120), nullable=False)
    bio = db.Column(db.Text, nullable=True)
    avatar_url = db.Column(db.String(500), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    is_admin = db.Column(db.Boolean, default=False)
    
    # Relations
    videos = db.relationship('Video', backref='user', lazy=True)
    comments = db.relationship('Comment', backref='user', lazy=True)
    likes = db.relationship('Like', backref='user', lazy=True)
    xps = db.relationship('Xp', backref='user', lazy=True)

    # Suivis
    following = db.relationship(
        'Follow', 
        foreign_keys='Follow.follower_id',
        backref='follower', 
        lazy='dynamic'
    )
    followers = db.relationship(
        'Follow', 
        foreign_keys='Follow.followed_id',
        backref='followed', 
        lazy='dynamic'
    )

    def set_password(self, raw):
        self.password_hash = generate_password_hash(raw)

    def check_password(self, raw) -> bool:
        return check_password_hash(self.password_hash, raw)
    
    def is_following(self, user):
        """Vérifie si cet utilisateur suit un autre utilisateur"""
        return Follow.query.filter_by(
            follower_id=self.id, 
            followed_id=user.id
        ).first() is not None
    
    @property
    def followers_count(self):
        """Nombre d'abonnés"""
        return self.followers.count()
    
    @property
    def following_count(self):
        """Nombre d'abonnements"""
        return self.following.count()


class Video(db.Model):
    __tablename__ = "videos"
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, default="")
    category = db.Column(db.String(40), default="tendance", index=True)
    filename = db.Column(db.String(255), nullable=True)
    external_url = db.Column(db.String(500), nullable=True)
    thumb_url = db.Column(db.String(500), nullable=True)
    duration = db.Column(db.String(20), default="")
    creator = db.Column(db.String(80), default="Anonyme")
    views = db.Column(db.Integer, default=0)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    hls_manifest = db.Column(db.String(500), nullable=True)
    
    # Relations
    comments = db.relationship('Comment', backref='video', lazy=True)
    like_records = db.relationship('Like', backref='video', lazy=True)
    xp_records = db.relationship('Xp', backref='video', lazy=True)

    @property
    def source_url(self):
        """Retourne l'URL de la vidéo avec priorité à Supabase"""
        # Priorité 1 : URL externe (Supabase - permanent)
        if self.external_url:
            return self.external_url
        # Priorité 2 : HLS (si disponible)
        if self.hls_manifest:
            return url_for("hls", filename=self.hls_manifest, _external=False)
        # Priorité 3 : Fichier local (fallback, mais sera supprimé sur Render)
        if self.filename:
            return url_for("media", filename=self.filename, _external=False)
        return ""
    
    @property
    def likes(self):
        """Compte les likes depuis la table Like"""
        return Like.query.filter_by(video_id=self.id, is_like=True).count()
    
    @property
    def dislikes(self):
        """Compte les dislikes depuis la table Like"""
        return Like.query.filter_by(video_id=self.id, is_like=False).count()

    @property
    def xp(self):
        """Compte les XP depuis la table Xp"""
        return Xp.query.filter_by(video_id=self.id).count()


class Comment(db.Model):
    __tablename__ = "comments"
    id = db.Column(db.Integer, primary_key=True)
    video_id = db.Column(db.Integer, db.ForeignKey("videos.id"), index=True, nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), index=True, nullable=False)
    body = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Like(db.Model):
    __tablename__ = "likes"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    video_id = db.Column(db.Integer, db.ForeignKey("videos.id"), nullable=False)
    is_like = db.Column(db.Boolean, nullable=False)  # True = like, False = dislike
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    __table_args__ = (db.UniqueConstraint('user_id', 'video_id'),)


class Follow(db.Model):
    __tablename__ = "follows"
    id = db.Column(db.Integer, primary_key=True)
    follower_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    followed_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    __table_args__ = (db.UniqueConstraint('follower_id', 'followed_id'),)


class Xp(db.Model):
    __tablename__ = "xp"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    video_id = db.Column(db.Integer, db.ForeignKey("videos.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (db.UniqueConstraint('user_id', 'video_id', name="unique_user_xp"),)
