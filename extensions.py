from extensions import db, migrate  # assure-toi que migrate est défini dans extensions.py

# Initialisation DB et Migrate avec l'app
db.init_app(app)
migrate.init_app(app, db)
