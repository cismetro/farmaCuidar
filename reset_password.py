from app import create_app
from app.models import User, db

app = create_app()
with app.app_context():
    user = User.query.filter_by(username='admin').first()
    if user:
        user.set_password('admin123')
        db.session.commit()
        print("Senha do admin resetada para: admin123")