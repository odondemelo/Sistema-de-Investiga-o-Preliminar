import os
from datetime import timedelta

class Config:
    # Chave secreta - MUDE ISSO para algo aleatório em produção
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'pip-corregedoria-2025-secretkey-change-me'

    # Banco de dados
    SQLALCHEMY_DATABASE_URI = 'sqlite:///database.db'
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Sessão
    PERMANENT_SESSION_LIFETIME = timedelta(hours=8)
    SESSION_COOKIE_HTTPONLY = True

    # Upload
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16MB max
    UPLOAD_FOLDER = 'uploads'

    # Usuários padrão
    USUARIOS_PADRAO = {
        'admin': {'senha': 'admin123', 'nome': 'Administrador'},
        'odon': {'senha': 'odon123', 'nome': 'Odon'},
        'investigador1': {'senha': 'inv123', 'nome': 'Investigador 1'},
        'investigador2': {'senha': 'inv123', 'nome': 'Investigador 2'}
    }
