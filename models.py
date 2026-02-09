from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timedelta

db = SQLAlchemy()

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
        if self.previsao_conclusao:
            delta = self.previsao_conclusao - datetime.now().date()
            return delta.days
        return None

    @property
    def esta_atrasado(self):
        return self.dias_restantes is not None and self.dias_restantes < 0 and self.status == 'Em Andamento'

    @property
    def alerta_prazo(self):
        return self.dias_restantes is not None and 0 <= self.dias_restantes <= 15 and self.status == 'Em Andamento'

    def to_dict(self):
        return {
            'id': self.id,
            'responsavel': self.responsavel,
            'processo_gdoc': self.processo_gdoc,
            'assunto': self.assunto,
            'status': self.status,
            'dias_restantes': self.dias_restantes
        }


class HistoricoDiligencia(db.Model):
    __tablename__ = 'historico_diligencias'

    id = db.Column(db.Integer, primary_key=True)
    investigacao_id = db.Column(db.Integer, db.ForeignKey('investigacoes.id'), nullable=False)
    data = db.Column(db.DateTime, default=datetime.utcnow)
    usuario = db.Column(db.String(100))
    descricao = db.Column(db.Text, nullable=False)
    tipo = db.Column(db.String(50))  # 'diligencia', 'atualizacao', 'status'

    def to_dict(self):
        return {
            'id': self.id,
            'data': self.data.isoformat(),
            'usuario': self.usuario,
            'descricao': self.descricao,
            'tipo': self.tipo
        }
