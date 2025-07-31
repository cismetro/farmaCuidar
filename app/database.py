"""
Database configuration
"""
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager

# Instâncias globais
db = SQLAlchemy()
login_manager = LoginManager()

def init_extensions(app):
    """Inicializar extensões com a app"""
    db.init_app(app)
    login_manager.init_app(app)
    
    # Configurações do login manager
    login_manager.login_view = 'main.login'
    login_manager.login_message = 'Faça login para acessar esta página.'
    login_manager.login_message_category = 'info'
    
    return db, login_manager