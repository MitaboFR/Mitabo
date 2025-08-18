from flask import Blueprint, redirect, url_for, request, flash, render_template
from flask_login import login_required, current_user
from models import Video, Like, Comment, User
from extensions import db

video_bp = Blueprint("video", __name__)

@video_bp.route("/like/<int:video_id>", methods=["POST"])
@login_required
def like_video(video_id):
    video = Video.query.get_or_404(video_id)
    
    # Vérifier si l'utilisateur a déjà liké/disliké cette vidéo
    existing_like = Like.query.filter_by(
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
@video_bp.route("/comment/<int:video_id>", methods=["POST"])
@login_required
def add_comment(video_id):
    video = Video.query.get_or_404(video_id)
    text = request.form.get("text", "").strip()
    
    if not text:
        flash("Le commentaire ne peut pas être vide")
        return redirect(url_for("video.watch", video_id=video_id))
    
    comment = Comment(
        text=text,
        user_id=current_user.id,
        video_id=video_id
    )
    
    db.session.add(comment)
    db.session.commit()
    
    return redirect(url_for("video.watch", video_id=video_id))

@video_bp.route("/dislike/<int:video_id>", methods=["POST"])
@login_required
def dislike_video(video_id):
    video = Video.query.get_or_404(video_id)
    
    existing_like = Like.query.filter_by(
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
    return redirect(url_for("video.watch", video_id=video_id))

@video_bp.route("/watch/<int:video_id>")
def watch(video_id):
    video = Video.query.get_or_404(video_id)
    
    # Incrémenter les vues
    video.views = (video.views or 0) + 1
    db.session.commit()
    
    # Vérifier si l'utilisateur connecté a liké/disliké cette vidéo
    user_like = None
    is_following = False
    if current_user.is_authenticated:
        user_like = Like.query.filter_by(
            user_id=current_user.id, 
            video_id=video_id
        ).first()
        
        # Vérifier si l'utilisateur suit le créateur de la vidéo
        if video.user:
            from models import Follow
            is_following = Follow.query.filter_by(
                follower_id=current_user.id,
                followed_id=video.user.id
            ).first() is not None
    
    # Vidéos similaires
    more_videos = (
        Video.query.filter(Video.id != video.id, Video.category == video.category)
        .order_by(Video.created_at.desc())
        .limit(8)
        .all()
    )
    
    # Commentaires avec les auteurs
    comments = db.session.query(Comment).join(User).filter(
        Comment.video_id == video_id
    ).order_by(Comment.created_at.desc()).all()
    
    return render_template("watch.html", 
                         video=video, 
                         more_videos=more_videos,
                         comments=comments,
                         user_like=user_like,
                         is_following=is_following)
    