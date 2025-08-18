from home import app          # crée l'app et fait db.init_app(app) UNE fois
from extensions import db
import models                 # important pour que les tables soient connues

with app.app_context():
    db.create_all()
    print("✅ Base de données créée / mise à jour")
