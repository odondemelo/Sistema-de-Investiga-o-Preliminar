from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timedelta
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()


# ==================== MODELO DE USUÁRIO ====================
class Usuario(db.Model):
    __tablename__ = 'usuarios'

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False, index=True)
    senha_hash = db.Column(db.String(255), nullable=False)
    nome = db.Column(db.String(100), nullable=False)
    nivel = db.Column(db.String(20), nullable=False, default='investigador')  # admin / investigador / visualizador
    ativo = db.Column(db.Boolean, default=True)
    criado_em = db.Column(db.DateTime, default=datetime.utcnow)
    ultimo_login = db.Column(db.DateTime)

    def __init__(self, username, senha, nome, nivel='investigador', ativo=True):
        self.username = username
        self.set_senha(senha)
        self.nome = nome
        self.nivel = nivel
        self.ativo = ativo

    def set_senha(self, senha):
        """Criptografa a senha"""
        self.senha_hash = generate_password_hash(senha)

    def check_senha(self, senha):
        """Verifica se a senha está correta"""
        return check_password_hash(self.senha_hash, senha)

    def registrar_login(self):
        """Atualiza o último login"""
        self.ultimo_login = datetime.utcnow()
        db.session.commit()

    @property
    def eh_admin(self):
        """Verifica se o usuário é administrador"""
        return self.nivel == 'admin'

    @property
    def pode_editar(self):
        """Verifica se o usuário pode editar investigações"""
        return self.nivel in ['admin', 'investigador']

    @property
    def pode_visualizar(self):
        """Qualquer usuário ativo pode visualizar"""
        return self.ativo

    def to_dict(self):
        return {
            'id': self.id,
            'username': self.username,
            'nome': self.nome,
            'nivel': self.nivel,
            'ativo': self.ativo,
            'criado_em': self.criado_em.isoformat() if self.criado_em else None,
            'ultimo_login': self.ultimo_login.isoformat() if self.ultimo_login else None
        }

    def __repr__(self):
        return f'<Usuario {self.username} ({self.nivel})>'


# ==================== MODELO DE INVESTIGAÇÃO ====================
class Investigacao(db.Model):
    __tablename__ = 'investigacoes'

    id = db.Column(db.Integer, primary_key=True)
    responsavel = db.Column(db.String(100), nullable=False)
    origem = db.Column(db.String(50))
    canal = db.Column(db.String(50))
    protocolo_origem = db.Column(db.String(100))
    admitida_ou_inadmitida = db.Column(db.String(20))
    unidade_origem = db.Column(db.String(100))
    classificacao = db.Column(db.String(100))
    assunto = db.Column(db.String(200))
    processo_gdoc = db.Column(db.String(100))
    ano = db.Column(db.Integer)
    denunciante = db.Column(db.String(200))
    matricula_denunciado = db.Column(db.String(50))
    nome_denunciado = db.Column(db.String(200))
    setor = db.Column(db.String(100))
    diretoria = db.Column(db.String(100))
    vinculo = db.Column(db.String(50))
    objeto_especificacao = db.Column(db.Text)
    diligencias = db.Column(db.Text)
    complexidade = db.Column(db.String(20))
    entrada_prfi = db.Column(db.Date)
    portaria_instauracao = db.Column(db.String(100))
    distribuicao_prfip = db.Column(db.Date)
    distribuicao_prfir = db.Column(db.Date)
    previsao_conclusao = db.Column(db.Date)
    data_conclusao = db.Column(db.Date, nullable=True)  # ✅ NOVO CAMPO ADICIONADO!
    status = db.Column(db.String(50), default='Em Andamento')
    resultado_final = db.Column(db.String(200))
    justificativa = db.Column(db.Text)
    envio_prfi = db.Column(db.Date)
    envio_prf = db.Column(db.Date)

    # Campos de controle
    criado_em = db.Column(db.DateTime, default=datetime.utcnow)
    atualizado_em = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relacionamento com diligências
    historico = db.relationship('HistoricoDiligencia', backref='investigacao', lazy=True, cascade='all, delete-orphan')

    def __init__(self, **kwargs):
        super(Investigacao, self).__init__(**kwargs)
        if not self.ano:
            self.ano = datetime.now().year
        if not self.entrada_prfi:
            self.entrada_prfi = datetime.now().date()
        if not self.previsao_conclusao:
            # 120 dias após entrada
            self.previsao_conclusao = (datetime.now() + timedelta(days=120)).date()

    @property
    def dias_restantes(self):
        """Calcula dias restantes APENAS se NÃO estiver concluída"""
        if self.status == 'Concluída':
            return None  # ✅ NÃO CALCULA PRAZO PARA CONCLUÍDAS!

        if self.previsao_conclusao:
            delta = self.previsao_conclusao - datetime.now().date()
            return delta.days
        return None

    @property
    def esta_atrasado(self):
        """Verifica se está atrasado APENAS se estiver em andamento"""
        if self.status != 'Em Andamento':
            return False  # ✅ CONCLUÍDAS NUNCA ESTÃO ATRASADAS!

        return self.dias_restantes is not None and self.dias_restantes < 0

    @property
    def alerta_prazo(self):
        """Alerta de prazo APENAS para investigações em andamento"""
        if self.status != 'Em Andamento':
            return False  # ✅ CONCLUÍDAS NÃO TÊM ALERTA!

        return self.dias_restantes is not None and 0 <= self.dias_restantes <= 15

    def to_dict(self):
        return {
            'id': self.id,
            'responsavel': self.responsavel,
            'processo_gdoc': self.processo_gdoc,
            'assunto': self.assunto,
            'status': self.status,
            'dias_restantes': self.dias_restantes,
            'data_conclusao': self.data_conclusao.strftime('%d/%m/%Y') if self.data_conclusao else None
        }


# ==================== MODELO DE HISTÓRICO ====================
class HistoricoDiligencia(db.Model):
    __tablename__ = 'historico_diligencias'

    id = db.Column(db.Integer, primary_key=True)
    investigacao_id = db.Column(db.Integer, db.ForeignKey('investigacoes.id'), nullable=False)
    data = db.Column(db.DateTime, default=datetime.utcnow)
    usuario = db.Column(db.String(100))
    descricao = db.Column(db.Text, nullable=False)
    tipo = db.Column(db.String(50))  # 'diligencia', 'edicao', 'status'

    def to_dict(self):
        return {
            'id': self.id,
            'data': self.data.isoformat(),
            'usuario': self.usuario,
            'descricao': self.descricao,
            'tipo': self.tipo
        }


# ==================== MODELO DE ANEXO ====================
class Anexo(db.Model):
    __tablename__ = 'anexos'

    id = db.Column(db.Integer, primary_key=True)
    investigacao_id = db.Column(db.Integer, db.ForeignKey('investigacoes.id'), nullable=False)
    nome_arquivo = db.Column(db.String(255), nullable=False)  # Nome original do arquivo
    caminho_arquivo = db.Column(db.String(255), nullable=False)  # Caminho onde o arquivo está salvo no servidor
    tipo_mime = db.Column(db.String(100))  # Tipo MIME do arquivo (ex: application/pdf, image/jpeg)
    tamanho_bytes = db.Column(db.Integer)  # Tamanho do arquivo em bytes
    data_upload = db.Column(db.DateTime, default=datetime.utcnow)
    usuario_upload = db.Column(db.String(100))  # Quem fez o upload

    # Relacionamento com Investigacao
    investigacao = db.relationship('Investigacao', backref='anexos', lazy=True)

    def to_dict(self):
        return {
            'id': self.id,
            'investigacao_id': self.investigacao_id,
            'nome_arquivo': self.nome_arquivo,
            'caminho_arquivo': self.caminho_arquivo,
            'tipo_mime': self.tipo_mime,
            'tamanho_bytes': self.tamanho_bytes,
            'data_upload': self.data_upload.isoformat() if self.data_upload else None,
            'usuario_upload': self.usuario_upload
        }
