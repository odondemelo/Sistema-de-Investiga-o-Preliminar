import os
from datetime import timedelta

class Config:
    # Chave secreta (IMPORTANTE: mude isso em produção!)
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'chave-super-secreta-pip-2025-mudar-em-producao'

    # Configuração do banco de dados SQLite
    BASE_DIR = os.path.abspath(os.path.dirname(__file__))
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL') or \
        'sqlite:///' + os.path.join(BASE_DIR, 'instance', 'database.db')
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Sessão permanente (7 dias)
    PERMANENT_SESSION_LIFETIME = timedelta(days=7)

    # Upload de arquivos
    UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads')
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16MB
    ALLOWED_EXTENSIONS = {'txt', 'pdf', 'png', 'jpg', 'jpeg', 'gif', 'doc', 'docx', 'xls', 'xlsx'}

    # Usuários padrão (criados automaticamente no primeiro acesso)
    USUARIOS_PADRAO = {
        'odon': {
            'senha': 'odon123',  # ⚠️ MUDE ESSA SENHA EM PRODUÇÃO!
            'nome': 'Odon',
            'nivel': 'admin'
        },
        'lucas': {
            'senha': 'lucas123',  # ⚠️ MUDE ESSA SENHA EM PRODUÇÃO!
            'nome': 'Lucas',
            'nivel': 'investigador'
        },
        'emanuel': {
            'senha': 'emanuel123',  # ⚠️ MUDE ESSA SENHA EM PRODUÇÃO!
            'nome': 'Emanuel',
            'nivel': 'investigador'
        },
        'erom': {
            'senha': 'erom123',  # ⚠️ MUDE ESSA SENHA EM PRODUÇÃO!
            'nome': 'Erom',
            'nivel': 'investigador'
        }
    }
