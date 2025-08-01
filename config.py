import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'farmacuidar-cosmopolis-2024-secret-key'
    
    # MySQL Database Configuration - PORTA 3307
    MYSQL_USER = os.environ.get('MYSQL_USER') or 'root'
    MYSQL_PASSWORD = os.environ.get('MYSQL_PASSWORD') or ''
    MYSQL_HOST = os.environ.get('MYSQL_HOST') or 'localhost'
    # USE ESTA LINHA PARA USAR A PORTA 3307
    # MYSQL_PORT = os.environ.get('MYSQL_PORT') or '3307'  # SUA PORTA
    # USE ESTA LINHA PARA RODAR NA PORTA 3306
    MYSQL_PORT = os.environ.get('MYSQL_PORT') or '3306'  # SUA PORTA
    MYSQL_DATABASE = os.environ.get('MYSQL_DATABASE') or 'farmacuidar'
    
    # DESCOMENTE ESTA LINHA PARA RODAR COM SENHA
    # SQLALCHEMY_DATABASE_URI = f"mysql+pymysql://{MYSQL_USER}:{MYSQL_PASSWORD}@{MYSQL_HOST}:{MYSQL_PORT}/{MYSQL_DATABASE}"
    
    # USE ESTA LINHA PARA RODAR SEM SENHA
    SQLALCHEMY_DATABASE_URI = f"mysql+pymysql://{MYSQL_USER}:@{MYSQL_HOST}:{MYSQL_PORT}/{MYSQL_DATABASE}"
    
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    
    # Upload Configuration
    UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'uploads')
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16MB max file size
    
    # Pagination
    PATIENTS_PER_PAGE = 20
    MEDICATIONS_PER_PAGE = 50
    PROCESSES_PER_PAGE = 15
    
    # Sistema
    SYSTEM_NAME = "FarmaCuidar - Cosmópolis"
    MUNICIPALITY = "Cosmópolis - SP"
    
class DevelopmentConfig(Config):
    DEBUG = True
    
class ProductionConfig(Config):
    DEBUG = False
    
config = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'default': DevelopmentConfig
}