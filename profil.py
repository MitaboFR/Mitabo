from flask import Blueprint, render_template, redirect, url_for
from flask_login import login_required, current_user
from models import User, Video
from extensions import db

profil_bp = Blueprint("profil", __name__, url_prefix="/profil")

# --- Afficher un profil utilisateur ---
@profil_bp.route("/<username>")
def show_profil(username):
    user = User.query.filter_by(username=username).first_or_404()
    videos = Video.query.filter_by(user_id=user.id).all()
    return render_template("profil.html", user=user, videos=videos)


# --- S'abonner ---
@profil_bp.route("/follow/<int:user_id>")
@login_required
def follow_user(user_id):
    user = User.query.get_or_404(user_id)
    if not current_user.is_following(user):
        from models import Follow
        f = Follow(follower_id=current_user.id, followed_id=user.id)
        db.session.add(f)
        db.session.commit()
    return redirect(url_for("profil.show_profil", username=user.username))


# --- Se désabonner ---
@profil_bp.route("/unfollow/<int:user_id>")
@login_required
def unfollow_user(user_id):
    from models import Follow
    f = Follow.query.filter_by(follower_id=current_user.id, followed_id=user_id).first()
    if f:
        db.session.delete(f)
        db.session.commit()
    user = User.query.get_or_404(user_id)
    return redirect(url_for("profil.show_profil", username=user.username))
