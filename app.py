from flask import Flask, render_template, request, redirect, url_for, session, flash, send_file, current_app, jsonify # Adicionei jsonify
from models import db, Investigacao, HistoricoDiligencia, Usuario, Anexo
from config import Config
from datetime import datetime, timedelta
import json
import pandas as pd  # ✅ DESCOMENTADO E USADO
from io import BytesIO
from collections import Counter
from werkzeug.utils import secure_filename
import os
import mimetypes
from flask_login import login_required

app = Flask(__name__)
app.config.from_object(Config)

db.init_app(app)

# ==================== FILTRO DE DATA (CORREÇÃO DE FUSO HORÁRIO) ====================
@app.template_filter('data_brasil')
def data_brasil_filter(data):
    if not data:
        return ""
    # Subtrai 3 horas do horário UTC para virar horário de Brasília
    data_local = data - timedelta(hours=3)
    return data_local.strftime('%d/%m/%Y às %H:%M')


# ==================== FILTROS PERSONALIZADOS DO JINJA2 ====================
@app.template_filter('reject_key')
def reject_key(args, key):
    """
    Remove uma chave específica de um dicionário de argumentos (request.args).
    Usado na paginação para remover o 'page' antigo antes de adicionar o novo.
    """
    new_args = args.copy()
    if key in new_args:
        new_args.pop(key)
    return new_args


# Filtro de data personalizado
@app.template_filter('date')
def format_date(value, format='%d/%m/%Y'):
    if value is None:
        return ''
    if isinstance(value, str):
        return value
    return value.strftime(format)

# Função auxiliar para verificar extensões permitidas
def allowed_file(filename):
    # Adicionei 'xls' e 'xlsx' para a importação de servidores
    ALLOWED_EXTENSIONS = {'pdf', 'png', 'jpg', 'jpeg', 'doc', 'docx', 'xls', 'xlsx', 'txt'}
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# Configuração de pastas (garantir que a pasta 'uploads' exista)
UPLOAD_FOLDER = 'uploads'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)


# ==================== INICIALIZAÇÃO DO BANCO ====================
with app.app_context():
    db.create_all()

    # ===== MIGRAR USUÁRIOS DO CONFIG.PY PARA O BANCO =====
    for username, info in Config.USUARIOS_PADRAO.items():
        usuario_existente = Usuario.query.filter_by(username=username).first()

        if not usuario_existente:
            novo_usuario = Usuario(
                username=username,
                senha=info['senha'],
                nome=info['nome'],
                nivel=info['nivel'],
                ativo=True
            )
            db.session.add(novo_usuario)
            print(f"✅ Usuário criado: {username} ({info['nome']}) - Nível: {info['nivel']}")

    db.session.commit()
    print("🔐 Migração de usuários concluída!")


# ==================== NOVO MODELO: SERVIDOR ====================
class Servidor(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(150), nullable=False)
    matricula = db.Column(db.String(50), nullable=True, unique=True) # Matrícula única
    cargo = db.Column(db.String(100), nullable=True)
    lotacao = db.Column(db.String(100), nullable=True)

    def to_dict(self):
        return {
            'id': self.id,
            'nome': self.nome,
            'matricula': self.matricula,
            'cargo': self.cargo,
            'lotacao': self.lotacao
        }


# ==================== CONTEXT PROCESSOR PARA NOTIFICAÇÕES ====================
@app.context_processor
def inject_notifications():
    """Injeta contador de notificações e nível do usuário em todos os templates"""
    if 'usuario' in session:
        hoje = datetime.now().date()

        # Investigações atrasadas (em andamento)
        atrasadas = Investigacao.query.filter(
            Investigacao.status == 'Em Andamento',
            Investigacao.previsao_conclusao < hoje
        ).count()

        # Investigações próximas do prazo (15 dias)
        prazo_limite = hoje + timedelta(days=15)
        proximas_prazo = Investigacao.query.filter(
            Investigacao.status == 'Em Andamento',
            Investigacao.previsao_conclusao >= hoje,
            Investigacao.previsao_conclusao <= prazo_limite
        ).count()

        total_alertas = atrasadas + proximas_prazo

        return dict(
            total_alertas=total_alertas,
            qtd_atrasadas=atrasadas,
            qtd_proximas_prazo=proximas_prazo,
            user_nivel=session.get('nivel', 'investigador')
        )

    return dict(total_alertas=0, qtd_atrasadas=0, qtd_proximas_prazo=0, user_nivel='')


@app.route('/')
def index():
    if 'usuario' in session:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))


# ==================== LOGIN ATUALIZADO (USA O BANCO) ====================
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()

        print(f"🔐 Tentativa de login - Usuário: '{username}'")

        # Buscar usuário no banco
        usuario = Usuario.query.filter_by(username=username).first()

        if usuario:
            if not usuario.ativo:
                flash('Usuário desativado. Entre em contato com o administrador.', 'danger')
                return render_template('login.html')

            if usuario.check_senha(password):
                # Login bem-sucedido!
                session.clear()
                session['usuario_id'] = usuario.id
                session['usuario'] = usuario.username
                session['nome'] = usuario.nome
                session['nivel'] = usuario.nivel
                session.permanent = True

                # Registrar último login
                usuario.registrar_login()

                print(f"✅ Login bem-sucedido: {usuario.nome} ({usuario.nivel})")
                flash(f'Bem-vindo, {usuario.nome}!', 'success')
                return redirect(url_for('dashboard'))
            else:
                print(f"❌ Senha incorreta para: {username}")
                flash('Usuário ou senha incorretos!', 'danger')
        else:
            print(f"❌ Usuário não encontrado: {username}")
            flash('Usuário ou senha incorretos!', 'danger')

    return render_template('login.html')


@app.route('/logout')
def logout():
    usuario = session.get('nome', 'Usuário')
    session.clear()
    flash(f'Até logo, {usuario}!', 'info')
    return redirect(url_for('login'))


@app.route('/dashboard')
def dashboard():
    if 'usuario' not in session:
        flash('Você precisa fazer login primeiro!', 'warning')
        return redirect(url_for('login'))

    hoje = datetime.now().date()

    # === DADOS GERAIS ===
    total = Investigacao.query.count()
    em_andamento = Investigacao.query.filter_by(status='Em Andamento').count()
    concluidas = Investigacao.query.filter_by(status='Concluída').count()

    # === DADOS PARA GRÁFICOS ===

    # 1. Gráfico de Pizza: Status
    # Conta quantas investigações existem para cada status
    status_raw = db.session.query(Investigacao.status, db.func.count(Investigacao.status)).group_by(Investigacao.status).all()
    # Transforma em formato fácil para o gráfico: {'Em Andamento': 10, 'Concluída': 5}
    dados_status = {s[0]: s[1] for s in status_raw if s[0]}

    # 2. Gráfico de Barras: Investigações por Ano
    ano_raw = db.session.query(Investigacao.ano, db.func.count(Investigacao.ano)).group_by(Investigacao.ano).all()
    dados_ano = {str(a[0]): a[1] for a in ano_raw if a[0]}

    # 3. Gráfico de Barras: Por Classificação (Assédio, Furto, etc)
    class_raw = db.session.query(Investigacao.classificacao, db.func.count(Investigacao.classificacao)).group_by(Investigacao.classificacao).all()
    dados_classificacao = {c[0]: c[1] for c in class_raw if c[0]}

    # === TABELAS DE ALERTA ===
    # Investigações atrasadas
    atrasadas = Investigacao.query.filter(
        Investigacao.status == 'Em Andamento',
        Investigacao.previsao_conclusao < hoje
    ).order_by(Investigacao.previsao_conclusao.asc()).all()

    # Investigações próximas do prazo
    prazo_limite = hoje + timedelta(days=15)
    proximas_prazo = Investigacao.query.filter(
        Investigacao.status == 'Em Andamento',
        Investigacao.previsao_conclusao >= hoje,
        Investigacao.previsao_conclusao <= prazo_limite
    ).order_by(Investigacao.previsao_conclusao.asc()).all()

    # Investigações recentes
    recentes = Investigacao.query.order_by(Investigacao.id.desc()).limit(5).all()

    return render_template('dashboard.html',
                         total=total,
                         em_andamento=em_andamento,
                         concluidas=concluidas,
                         recentes=recentes,
                         atrasadas=atrasadas,
                         proximas_prazo=proximas_prazo,
                         hoje=hoje,
                         # Passando os dados novos para o HTML
                         dados_status=dados_status,
                         dados_ano=dados_ano,
                         dados_classificacao=dados_classificacao)



# ==================== ROTA: RELATÓRIOS ====================
@app.route('/relatorios')
def relatorios():
    if 'usuario' not in session:
        flash('Você precisa fazer login primeiro!', 'warning')
        return redirect(url_for('login'))

    hoje = datetime.now().date()

    # Buscar todas as investigações
    todas = Investigacao.query.all()

    # Total
    total = len(todas)

    # Contadores para os gráficos
    status_counts = dict(Counter([inv.status for inv in todas if inv.status]))
    responsavel_counts = dict(Counter([inv.responsavel for inv in todas if inv.responsavel]))
    assunto_counts = dict(Counter([inv.assunto for inv in todas if inv.assunto]))
    ano_counts = dict(Counter([str(inv.ano) for inv in todas if inv.ano]))

    # Investigações atrasadas
    atrasadas_query = Investigacao.query.filter(
        Investigacao.status == 'Em Andamento',
        Investigacao.previsao_conclusao < hoje
    ).order_by(Investigacao.previsao_conclusao.asc()).all()

    atrasadas = len(atrasadas_query)

    # Lista de atrasadas COM dias_restantes
    lista_atrasadas = []
    for inv in atrasadas_query:
        lista_atrasadas.append({
            'investigacao': inv,
            'dias_atrasados': (hoje - inv.previsao_conclusao).days
        })

    # Investigações próximas do prazo
    prazo_limite = hoje + timedelta(days=15)
    proximas_query = Investigacao.query.filter(
        Investigacao.status == 'Em Andamento',
        Investigacao.previsao_conclusao >= hoje,
        Investigacao.previsao_conclusao <= prazo_limite
    ).order_by(Investigacao.previsao_conclusao.asc()).all()

    proximas_prazo = len(proximas_query)

    # Lista de próximas COM dias_restantes
    lista_proximas = []
    for inv in proximas_query:
        lista_proximas.append({
            'investigacao': inv,
            'dias_restantes': (inv.previsao_conclusao - hoje).days
        })

    return render_template('relatorios.html',
                         total=total,
                         atrasadas=atrasadas,
                         proximas_prazo=proximas_prazo,
                         status_counts=status_counts,
                         responsavel_counts=responsavel_counts,
                         assunto_counts=assunto_counts,
                         ano_counts=ano_counts,
                         lista_atrasadas=lista_atrasadas,
                         lista_proximas=lista_proximas,
                         hoje=hoje)


# ==================== ROTA: LISTA DE INVESTIGAÇÕES (COM PAGINAÇÃO E FILTROS) ====================
@app.route('/investigacoes')
def investigacoes():
    if 'usuario' not in session:
        flash('Você precisa fazer login primeiro!', 'warning')
        return redirect(url_for('login'))

    # Query base
    query = Investigacao.query

    # ==================== FILTROS AVANÇADOS ====================

    # 1. Filtro por MÚLTIPLOS STATUS (checkboxes)
    filtros_status = request.args.getlist('status')  # Pega lista de valores
    if filtros_status and 'todos' not in filtros_status:
        query = query.filter(Investigacao.status.in_(filtros_status))

    # 2. Filtro por MÚLTIPLOS RESPONSÁVEIS (checkboxes)
    filtros_responsavel = request.args.getlist('responsavel')
    if filtros_responsavel and 'todos' not in filtros_responsavel:
        query = query.filter(Investigacao.responsavel.in_(filtros_responsavel))

    # 3. Filtro por CLASSIFICAÇÃO (dropdown)
    filtro_classificacao = request.args.get('classificacao')
    if filtro_classificacao and filtro_classificacao != 'todos':
        query = query.filter(Investigacao.classificacao == filtro_classificacao)

    # 4. Filtro por ANO (dropdown)
    filtro_ano = request.args.get('ano')
    if filtro_ano and filtro_ano != 'todos':
        query = query.filter(Investigacao.ano == int(filtro_ano))

    # 5. Filtro por PERÍODO DE DATA (Entrada PRFI)
    data_inicio = request.args.get('data_inicio')
    data_fim = request.args.get('data_fim')

    if data_inicio:
        try:
            data_inicio_obj = datetime.strptime(data_inicio, '%Y-%m-%d').date()
            query = query.filter(Investigacao.entrada_prfi >= data_inicio_obj)
        except:
            pass

    if data_fim:
        try:
            data_fim_obj = datetime.strptime(data_fim, '%Y-%m-%d').date()
            query = query.filter(Investigacao.entrada_prfi <= data_fim_obj)
        except:
            pass

    # 6. Filtro por COMPLEXIDADE (dropdown)
    filtro_complexidade = request.args.get('complexidade')
    if filtro_complexidade and filtro_complexidade != 'todos':
        query = query.filter(Investigacao.complexidade == filtro_complexidade)

    # 7. BUSCA POR PALAVRA-CHAVE (melhorada)
    busca = request.args.get('busca')
    if busca:
        search_term = f"%{busca}%"
        query = query.filter(
            (Investigacao.processo_gdoc.ilike(search_term)) |
            (Investigacao.assunto.ilike(search_term)) |
            (Investigacao.denunciante.ilike(search_term)) |
            (Investigacao.nome_denunciado.ilike(search_term)) |
            (Investigacao.objeto_especificacao.ilike(search_term)) |
            (Investigacao.protocolo_origem.ilike(search_term))
        )

    # ==================== ORDENAÇÃO ====================
    ordenar_por = request.args.get('ordenar_por', 'id_desc')

    if ordenar_por == 'id_asc':
        query = query.order_by(Investigacao.id.asc())
    elif ordenar_por == 'id_desc':
        query = query.order_by(Investigacao.id.desc())
    elif ordenar_por == 'previsao_asc':
        query = query.order_by(Investigacao.previsao_conclusao.asc())
    elif ordenar_por == 'previsao_desc':
        query = query.order_by(Investigacao.previsao_conclusao.desc())
    elif ordenar_por == 'status_asc':
        query = query.order_by(Investigacao.status.asc())
    elif ordenar_por == 'status_desc':
        query = query.order_by(Investigacao.status.desc())
    elif ordenar_por == 'responsavel_asc':
        query = query.order_by(Investigacao.responsavel.asc())
    elif ordenar_por == 'responsavel_desc':
        query = query.order_by(Investigacao.responsavel.desc())

    # ==================== EXECUTAR QUERY COM PAGINAÇÃO ====================
    # Pega o número da página da URL (padrão = 1)
    page = request.args.get('page', 1, type=int)
    per_page = 10  # Número de itens por página

    # Executa a paginação
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)

    # Total de resultados encontrados (para exibir na tela)
    total_resultados = pagination.total

    # ==================== LISTAS PARA OS FILTROS DINÂMICOS ====================
    lista_status = db.session.query(Investigacao.status).distinct().order_by(Investigacao.status).all()
    lista_responsaveis = db.session.query(Investigacao.responsavel).distinct().order_by(Investigacao.responsavel).all()
    lista_classificacoes = db.session.query(Investigacao.classificacao).distinct().order_by(Investigacao.classificacao).all()
    lista_anos = db.session.query(Investigacao.ano).distinct().order_by(Investigacao.ano.desc()).all()
    lista_complexidades = db.session.query(Investigacao.complexidade).distinct().order_by(Investigacao.complexidade).all()

    return render_template('investigacoes.html',
                         investigacoes=pagination, # ✅ Passando o objeto de paginação
                         total_resultados=total_resultados,
                         # Listas para popular os filtros
                         lista_status=[s[0] for s in lista_status if s[0]],
                         lista_responsaveis=[r[0] for r in lista_responsaveis if r[0]],
                         lista_classificacoes=[c[0] for c in lista_classificacoes if c[0]],
                         lista_anos=[a[0] for a in lista_anos if a[0]],
                         lista_complexidades=[c[0] for c in lista_complexidades if c[0]],
                         # Valores atuais dos filtros (para manter selecionados)
                         filtros_status=filtros_status,
                         filtros_responsavel=filtros_responsavel,
                         filtro_classificacao=filtro_classificacao,
                         filtro_ano=filtro_ano,
                         filtro_complexidade=filtro_complexidade,
                         data_inicio=data_inicio,
                         data_fim=data_fim,
                         busca=busca,
                         ordenar_por=ordenar_por,
                         hoje=datetime.now().date())



# ==================== ROTA: DETALHES DA INVESTIGAÇÃO (CORRIGIDA!) ====================
@app.route('/investigacoes/<int:id>')
def detalhes(id):
    if 'usuario' not in session:
        flash('Você precisa fazer login primeiro!', 'warning')
        return redirect(url_for('login'))

    investigacao = Investigacao.query.get_or_404(id)
    historico = HistoricoDiligencia.query.filter_by(investigacao_id=id).order_by(HistoricoDiligencia.data.desc()).all()
    anexos = Anexo.query.filter_by(investigacao_id=id).order_by(Anexo.data_upload.desc()).all()

    # Calcular dias restantes (SEM atribuir ao objeto)
    dias_restantes = None
    esta_atrasado = False

    if investigacao.previsao_conclusao and investigacao.status == 'Em Andamento':
        hoje = datetime.now().date()
        dias_restantes = (investigacao.previsao_conclusao - hoje).days
        esta_atrasado = dias_restantes < 0

    return render_template('detalhes_investigacao.html',
                           investigacao=investigacao,
                           historico=historico,
                           anexos=anexos,
                           dias_restantes=dias_restantes,
                           esta_atrasado=esta_atrasado,
                           user_nivel=session.get('nivel'))


# ==================== ROTA DE IMPRESSÃO DA INVESTIGAÇÃO (CORRIGIDA!) ====================
@app.route('/investigacoes/<int:id>/imprimir')
def imprimir_investigacao(id):
    if 'usuario' not in session:
        flash('Você precisa fazer login primeiro!', 'warning')
        return redirect(url_for('login'))

    investigacao = Investigacao.query.get_or_404(id)
    historico = HistoricoDiligencia.query.filter_by(investigacao_id=id).order_by(HistoricoDiligencia.data.desc()).all()
    anexos = Anexo.query.filter_by(investigacao_id=id).order_by(Anexo.data_upload.desc()).all()

    # Calcular dias restantes (SEM atribuir ao objeto)
    dias_restantes = None
    esta_atrasado = False

    if investigacao.previsao_conclusao and investigacao.status == 'Em Andamento':
        hoje = datetime.now().date()
        dias_restantes = (investigacao.previsao_conclusao - hoje).days
        esta_atrasado = dias_restantes < 0

    return render_template('imprimir_investigacao.html',
                           investigacao=investigacao,
                           historico=historico,
                           anexos=anexos,
                           dias_restantes=dias_restantes,
                           esta_atrasado=esta_atrasado,
                           datetime=datetime)

# ==================== ROTA: EXPORTAR PDF (LAYOUT RESTAURADO - VERSÃO BOA) ====================
@app.route('/investigacoes/<int:id>/exportar-pdf')
def exportar_pdf_investigacao(id):
    if 'usuario' not in session:
        return redirect(url_for('login'))

    try:
        # Imports necessários (mantendo os que você já tem)
        from reportlab.lib.pagesizes import A4
        from reportlab.lib import colors
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import cm
        from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image
        from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT, TA_JUSTIFY
        from io import BytesIO
        from reportlab.lib.utils import ImageReader

        investigacao = Investigacao.query.get_or_404(id)
        anexos = Anexo.query.filter_by(investigacao_id=id).order_by(Anexo.data_upload.desc()).all()

        buffer = BytesIO()

        # Configuração do Documento
        doc = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            rightMargin=1.5*cm,
            leftMargin=1.5*cm,
            topMargin=1.5*cm,
            bottomMargin=1.5*cm
        )

        styles = getSampleStyleSheet()

        # --- ESTILOS PERSONALIZADOS (Baseados no Print "Bom") ---

                # Estilo do Texto do Cabeçalho (Direita) - AUMENTADO
        style_right = ParagraphStyle(
            'HeaderRight',
            parent=styles['Normal'],
            fontSize=12,      # Fonte base maior
            alignment=TA_LEFT,
            leading=16        # Espaçamento entre linhas maior para não ficar grudado
        )


        # Estilo dos Títulos das Seções (Fundo Azul, Texto Branco, Numerado)
        style_section_header = ParagraphStyle(
            'SectionHeader',
            parent=styles['Normal'],
            fontSize=10,
            fontName='Helvetica-Bold',
            textColor=colors.white,
            backColor=colors.HexColor('#0054a6'), # Azul Caesb aproximado
            borderPadding=(4, 4, 4, 4),
            spaceAfter=6,
            spaceBefore=12
        )

        # Estilo para Rótulos (Coluna Esquerda da Tabela)
        style_label = ParagraphStyle(
            'Label',
            parent=styles['Normal'],
            fontSize=9,
            fontName='Helvetica-Bold',
            alignment=TA_LEFT
        )

        # Estilo para Valores (Coluna Direita da Tabela)
        style_value = ParagraphStyle(
            'Value',
            parent=styles['Normal'],
            fontSize=9,
            alignment=TA_LEFT
        )

        # Cor de fundo cinza claro para rótulos
        GRAY_BG = colors.HexColor('#f0f0f0')

        elements = []


            # --- CABEÇALHO ---

        # 1. Estilo do Texto (CENTRALIZADO)
        style_header_center = ParagraphStyle(
            'HeaderCenter',
            parent=styles['Normal'],
            fontSize=12,
            alignment=TA_CENTER,
            leading=18
        )

        # 2. Configuração da Logo
        extensoes = ['logo_caesb.png', 'logo.png', 'logo.jpg']
        logo_path = None
        for ext in extensoes:
            path = os.path.join(app.root_path, 'static', 'images', ext)
            if not os.path.exists(path):
                path = os.path.join(app.root_path, 'static', ext)
            if os.path.exists(path):
                logo_path = path
                break

        logo_img = Paragraph("<b>CAESB</b>", styles['Normal'])

        # Largura da coluna da logo (5cm)
        col_logo_width = 5 * cm  

        if logo_path:
            try:
                img = ImageReader(logo_path)
                iw, ih = img.getSize()
                aspect = iw / float(ih)

                target_h = 2.5 * cm 
                max_w = col_logo_width - 0.2 * cm

                w = target_h * aspect
                h = target_h

                if w > max_w:
                    w = max_w
                    h = w / aspect

                logo_img = Image(logo_path, width=w, height=h)
            except:
                pass

        # 3. Texto do Cabeçalho
        header_text = Paragraph(
            "<font size='16'><b><font color='#0054a6'>CORREGEDORIA - PRF</font></b></font><br/>"
            "<font size='13'>Gerência de Investigação - PRFI</font>",
            style_header_center
        )

        # 4. Tabela do Cabeçalho com 3 COLUNAS (Truque para centralizar)
        # Logo (5cm) | Texto (10cm) | Espaço Vazio (3cm)
        # Total = 18cm. O espaço vazio na direita empurra o texto para a esquerda.
        t_header = Table([[logo_img, header_text, '']], colWidths=[5*cm, 10*cm, 3*cm])

        t_header.setStyle(TableStyle([
            ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
            ('ALIGN', (0,0), (0,0), 'LEFT'),      # Logo na esquerda
            ('ALIGN', (1,0), (1,0), 'CENTER'),    # Texto centralizado na coluna dele
            ('LEFTPADDING', (0,0), (0,0), 0),
        ]))
        elements.append(t_header)
        elements.append(Spacer(1, 0.5*cm))






        # --- FUNÇÃO AUXILIAR PARA CRIAR TABELAS DE DADOS ---
        def create_data_table(data_list):
            # Converte strings em Paragraphs para quebra de linha automática
            formatted_data = []
            for row in data_list:
                label = Paragraph(row[0], style_label)
                value = Paragraph(str(row[1]) if row[1] is not None else '-', style_value)
                formatted_data.append([label, value])

            t = Table(formatted_data, colWidths=[5*cm, 12.5*cm])
            t.setStyle(TableStyle([
                ('BACKGROUND', (0,0), (0,-1), GRAY_BG), # Coluna 1 cinza
                ('GRID', (0,0), (-1,-1), 0.5, colors.grey), # Bordas
                ('VALIGN', (0,0), (-1,-1), 'TOP'),
                ('PADDING', (0,0), (-1,-1), 4),
            ]))
            return t

        # --- SEÇÃO 1: INFORMAÇÕES GERAIS ---
        elements.append(Paragraph("1. INFORMAÇÕES GERAIS", style_section_header))

        data_1 = [
            ['Processo GDOC:', investigacao.processo_gdoc],
            ['Protocolo Origem:', investigacao.protocolo_origem],
            ['Origem / Canal:', f"{investigacao.origem or '-'} / {investigacao.canal or '-'}"],
            ['Unidade Origem:', investigacao.unidade_origem],
            ['Classificação:', investigacao.classificacao],
            ['Assunto:', investigacao.assunto],
            ['Ano:', investigacao.ano]
        ]
        elements.append(create_data_table(data_1))
        elements.append(Spacer(1, 0.5*cm))

        # --- SEÇÃO 2: ENVOLVIDOS ---
        elements.append(Paragraph("2. ENVOLVIDOS", style_section_header))

        data_2 = [
            ['Denunciante(s):', investigacao.denunciante],
            ['Denunciado:', investigacao.nome_denunciado],
            ['Matrícula:', investigacao.matricula_denunciado],
            ['Setor / Diretoria:', f"{investigacao.setor or '-'} / {investigacao.diretoria or '-'}"],
            ['Vínculo:', investigacao.vinculo]
        ]
        elements.append(create_data_table(data_2))
        elements.append(Spacer(1, 0.5*cm))

        # --- SEÇÃO 3: OBJETO E DILIGÊNCIAS ---
        # Esta seção é diferente, tem textos longos, melhor não usar a tabela lateral
        elements.append(Paragraph("3. OBJETO E DILIGÊNCIAS", style_section_header))

        # Objeto
        elements.append(Paragraph("<b>Objeto / Especificação:</b>", style_label))
        elements.append(Paragraph(investigacao.objeto_especificacao or "Não informado.", style_value))
        elements.append(Spacer(1, 0.3*cm))

        # Diligências
        elements.append(Paragraph("<b>Diligências Realizadas:</b>", style_label))
        # Converte quebras de linha do texto para <br/> do HTML/PDF
        diligencias_text = (investigacao.diligencias or "Nenhuma diligência registrada.").replace('\n', '<br/>')
        elements.append(Paragraph(diligencias_text, style_value))
        elements.append(Spacer(1, 0.5*cm))

        # --- SEÇÃO 4: PRAZOS E STATUS ---
        elements.append(Paragraph("4. PRAZOS E STATUS", style_section_header))

        # Formatar datas
        entrada = investigacao.entrada_prfi.strftime('%d/%m/%Y') if investigacao.entrada_prfi else '-'
        previsao = investigacao.previsao_conclusao.strftime('%d/%m/%Y') if investigacao.previsao_conclusao else '-'

        data_4 = [
            ['Responsável:', investigacao.responsavel],
            ['Complexidade:', investigacao.complexidade],
            ['Entrada PRFI:', entrada],
            ['Previsão Conclusão:', previsao],
            ['Status Atual:', investigacao.status],
            ['Resultado Final:', investigacao.resultado_final]
        ]
        elements.append(create_data_table(data_4))
        elements.append(Spacer(1, 0.5*cm))

               # --- SEÇÃO 5: ANEXOS VINCULADOS ---
        elements.append(Paragraph("5. ANEXOS VINCULADOS", style_section_header))

        if anexos:
            # Cabeçalho da tabela de anexos
            anexos_data = [['Arquivo', 'Tamanho', 'Data Upload']]
            for anexo in anexos:
                # Tenta pegar o tamanho (se tiver o campo no banco ou calcula do arquivo)
                tamanho = "-"
                if hasattr(anexo, 'tamanho_bytes') and anexo.tamanho_bytes:
                     tamanho = f"{round(anexo.tamanho_bytes / 1024, 2)} KB"
                else:
                    # Tenta calcular pelo arquivo físico se não tiver no banco
                    try:
                        path = os.path.join(app.config['UPLOAD_FOLDER'], anexo.caminho_arquivo)
                        if os.path.exists(path):
                            size_kb = os.path.getsize(path) / 1024
                            tamanho = f"{size_kb:.2f} KB"
                    except:
                        pass

                # CORREÇÃO AQUI: Usando anexo.nome_arquivo em vez de nome_original
                anexos_data.append([
                    Paragraph(anexo.nome_arquivo, style_value),
                    tamanho,
                    anexo.data_upload.strftime('%d/%m/%Y %H:%M')
                ])

            t_anexos = Table(anexos_data, colWidths=[10*cm, 3*cm, 4.5*cm])
            t_anexos.setStyle(TableStyle([
                ('BACKGROUND', (0,0), (-1,0), GRAY_BG), # Cabeçalho cinza
                ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
                ('GRID', (0,0), (-1,-1), 0.5, colors.grey),
                ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
                ('PADDING', (0,0), (-1,-1), 4),
            ]))
            elements.append(t_anexos)
        else:
            elements.append(Paragraph("Nenhum anexo vinculado.", style_value))


        # Construir PDF
        doc.build(elements)
        buffer.seek(0)

        return send_file(
            buffer,
            as_attachment=True,
            download_name=f"Ficha_Investigacao_{id}.pdf",
            mimetype='application/pdf'
        )

    except Exception as e:
        flash(f'Erro ao gerar PDF: {str(e)}', 'danger')
        print(f"Erro PDF: {e}")
        import traceback
        traceback.print_exc()
        return redirect(url_for('detalhes', id=id))



# ==================== ROTAS DE ANEXOS ====================
@app.route('/investigacoes/<int:id>/upload-anexo', methods=['POST'])
def upload_anexo(id):
    if 'usuario' not in session:
        flash('Você precisa fazer login primeiro!', 'warning')
        return redirect(url_for('login'))

    if session.get('nivel') not in ['admin', 'editor', 'investigador']:
        flash('Você não tem permissão para enviar anexos!', 'danger')
        return redirect(url_for('detalhes', id=id))

    investigacao = Investigacao.query.get_or_404(id)

    if 'file' not in request.files:
        flash('Nenhum arquivo selecionado!', 'warning')
        return redirect(url_for('detalhes', id=id))

    file = request.files['file']

    if file.filename == '':
        flash('Nenhum arquivo selecionado!', 'warning')
        return redirect(url_for('detalhes', id=id))

    if file and allowed_file(file.filename):
        try:
            filename = secure_filename(file.filename)
            # Garante nome único para o arquivo no sistema de arquivos
            unique_filename = f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{filename}"
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
            file.save(filepath)

            # Obtém o tamanho do arquivo
            tamanho_bytes = os.path.getsize(filepath)

            novo_anexo = Anexo(
                investigacao_id=id,
                nome_arquivo=filename,
                caminho_arquivo=unique_filename, # Salva o nome único no banco
                usuario_upload=session.get('nome'),
                tamanho_bytes=tamanho_bytes
            )
            db.session.add(novo_anexo)

            hist = HistoricoDiligencia(
                investigacao_id=id,
                usuario=session.get('nome'),
                descricao=f"Anexo '{filename}' adicionado.",
                tipo='upload_anexo'
            )
            db.session.add(hist)

            db.session.commit()
            flash('Anexo enviado com sucesso!', 'success')
        except Exception as e:
            db.session.rollback()
            flash(f'Erro ao enviar anexo: {str(e)}', 'danger')
            print(f"❌ Erro ao enviar anexo: {e}")
    else:
        flash('Tipo de arquivo não permitido!', 'danger')

    return redirect(url_for('detalhes', id=id))

@app.route('/anexos/<int:id>/download')
def download_anexo(id):
    if 'usuario' not in session:
        return redirect(url_for('login'))

    anexo = Anexo.query.get_or_404(id)
    filepath = os.path.join(current_app.config['UPLOAD_FOLDER'], anexo.caminho_arquivo)

    if os.path.exists(filepath):
        # Tenta adivinhar o mimetype
        mimetype, _ = mimetypes.guess_type(filepath)
        if mimetype is None:
            mimetype = 'application/octet-stream' # Tipo genérico se não conseguir adivinhar

        return send_file(filepath, as_attachment=True, download_name=anexo.nome_arquivo, mimetype=mimetype)
    else:
        flash('Arquivo não encontrado!', 'danger')
        return redirect(url_for('detalhes', id=anexo.investigacao_id))

@app.route('/anexos/<int:id>/excluir', methods=['POST'])
def excluir_anexo(id):
    if 'usuario' not in session:
        return redirect(url_for('login'))
    if session.get('nivel') not in ['admin', 'editor']: # Apenas admin/editor podem excluir
        flash('Acesso negado para excluir anexos!', 'danger')
        return redirect(url_for('detalhes', id=anexo.investigacao_id)) # Redireciona para a investigação do anexo

    anexo = Anexo.query.get_or_404(id)
    investigacao_id = anexo.investigacao_id # Guarda o ID antes de excluir o anexo
    nome_arquivo = anexo.nome_arquivo # Guarda o nome para a mensagem

    try:
        filepath = os.path.join(current_app.config['UPLOAD_FOLDER'], anexo.caminho_arquivo)
        if os.path.exists(filepath):
            os.remove(filepath) # Exclui o arquivo físico

        # Excluir registro do banco de dados
        db.session.delete(anexo)
        db.session.commit()

        # Registrar no histórico
        historico = HistoricoDiligencia(
            investigacao_id=investigacao_id,
            usuario=session.get('nome'),
            descricao=f'Anexo "{nome_arquivo}" excluído por {session.get("nome")}',
            tipo='exclusao_anexo'
        )
        db.session.add(historico)
        db.session.commit()

        flash(f'Anexo "{nome_arquivo}" excluído com sucesso!', 'success')

    except Exception as e:
        db.session.rollback()
        flash(f'Erro ao excluir anexo: {str(e)}', 'danger')
        print(f"❌ Erro ao excluir anexo: {e}")
        return redirect(url_for('detalhes', id=investigacao_id))

    return redirect(url_for('detalhes', id=investigacao_id))



@app.route('/nova-investigacao', methods=['GET', 'POST'])
def nova_investigacao():
    if 'usuario' not in session:
        flash('Você precisa fazer login primeiro!', 'warning')
        return redirect(url_for('login'))

    if session.get('nivel') not in ['admin', 'investigador']:
        flash('Você não tem permissão para criar investigações!', 'danger')
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        try:
            entrada_prfi = None
            if request.form.get('entrada_prfi'):
                entrada_prfi = datetime.strptime(request.form.get('entrada_prfi'), '%Y-%m-%d').date()

            previsao_conclusao = None
            if request.form.get('previsao_conclusao'):
                previsao_conclusao = datetime.strptime(request.form.get('previsao_conclusao'), '%Y-%m-%d').date()

            nova_inv = Investigacao(
                responsavel=request.form.get('responsavel', session.get('nome')),
                origem=request.form.get('origem'),
                canal=request.form.get('canal'),
                protocolo_origem=request.form.get('protocolo_origem'),
                admitida_ou_inadmitida=request.form.get('admitida_ou_inadmitida'),
                unidade_origem=request.form.get('unidade_origem'),
                classificacao=request.form.get('classificacao'),
                assunto=request.form.get('assunto'),
                processo_gdoc=request.form.get('processo_gdoc'),
                ano=request.form.get('ano', datetime.now().year),
                denunciante=request.form.get('denunciante'),
                matricula_denunciado=request.form.get('matricula_denunciado'),
                nome_denunciado=request.form.get('nome_denunciado'),
                setor=request.form.get('setor'),
                diretoria=request.form.get('diretoria'),
                vinculo=request.form.get('vinculo'), # ✅ CAMPO ADICIONADO AQUI
                objeto_especificacao=request.form.get('objeto_especificacao'),
                diligencias=request.form.get('diligencias'),
                complexidade=request.form.get('complexidade'),
                entrada_prfi=entrada_prfi,
                previsao_conclusao=previsao_conclusao,
                status=request.form.get('status', 'Em Andamento'),
                justificativa=request.form.get('justificativa'),
                resultado_final=request.form.get('resultado_final')
            )

            db.session.add(nova_inv)
            db.session.commit()

            historico = HistoricoDiligencia(
                investigacao_id=nova_inv.id,
                usuario=session.get('nome'),
                descricao=f"Investigação criada por {session.get('nome')}",
                tipo='criacao'
            )
            db.session.add(historico)
            db.session.commit()

            flash(f'Investigação #{nova_inv.id} cadastrada com sucesso!', 'success')
            return redirect(url_for('detalhes', id=nova_inv.id))

        except Exception as e:
            db.session.rollback()
            flash(f'Erro ao cadastrar investigação: {str(e)}', 'danger')
            print(f"Erro: {e}")
            import traceback
            traceback.print_exc()

    return render_template('nova_investigacao.html', datetime=datetime)


@app.route('/investigacoes/<int:id>/editar', methods=['GET', 'POST'])
def editar_investigacao(id):
    if 'usuario' not in session:
        flash('Você precisa fazer login primeiro!', 'warning')
        return redirect(url_for('login'))

    if session.get('nivel') not in ['admin', 'investigador']:
        flash('Você não tem permissão para editar investigações!', 'danger')
        return redirect(url_for('detalhes', id=id))

    investigacao = Investigacao.query.get_or_404(id)

    if request.method == 'POST':
        try:
            campos_alterados = []

            def check_and_update(field_name, form_value, current_value):
                current_value_str = str(current_value) if current_value is not None else ''
                form_value_str = str(form_value) if form_value is not None else ''

                if form_value_str != current_value_str:
                    campos_alterados.append(f"- {field_name}: de '{current_value_str}' para '{form_value_str}'")
                    return form_value
                return current_value

            old_responsavel = investigacao.responsavel
            old_origem = investigacao.origem
            old_canal = investigacao.canal
            old_protocolo_origem = investigacao.protocolo_origem
            old_admitida_ou_inadmitida = investigacao.admitida_ou_inadmitida
            old_unidade_origem = investigacao.unidade_origem
            old_classificacao = investigacao.classificacao
            old_assunto = investigacao.assunto
            old_processo_gdoc = investigacao.processo_gdoc
            old_ano = investigacao.ano
            old_denunciante = investigacao.denunciante
            old_matricula_denunciado = investigacao.matricula_denunciado
            old_nome_denunciado = investigacao.nome_denunciado
            old_setor = investigacao.setor
            old_diretoria = investigacao.diretoria
            old_vinculo = investigacao.vinculo
            old_objeto_especificacao = investigacao.objeto_especificacao
            old_diligencias = investigacao.diligencias
            old_complexidade = investigacao.complexidade
            old_status = investigacao.status  # ✅ GUARDAMOS O STATUS ANTIGO
            old_justificativa = investigacao.justificativa
            old_resultado_final = investigacao.resultado_final
            old_entrada_prfi = investigacao.entrada_prfi.strftime('%Y-%m-%d') if investigacao.entrada_prfi else ''
            old_previsao_conclusao = investigacao.previsao_conclusao.strftime('%Y-%m-%d') if investigacao.previsao_conclusao else ''

            investigacao.responsavel = check_and_update('Responsável', request.form.get('responsavel'), old_responsavel)
            investigacao.origem = check_and_update('Origem', request.form.get('origem'), old_origem)
            investigacao.canal = check_and_update('Canal', request.form.get('canal'), old_canal)
            investigacao.protocolo_origem = check_and_update('Protocolo Origem', request.form.get('protocolo_origem'), old_protocolo_origem)
            investigacao.admitida_ou_inadmitida = check_and_update('Admitida/Inadmitida', request.form.get('admitida_ou_inadmitida'), old_admitida_ou_inadmitida)
            investigacao.unidade_origem = check_and_update('Unidade Origem', request.form.get('unidade_origem'), old_unidade_origem)
            investigacao.classificacao = check_and_update('Classificação', request.form.get('classificacao'), old_classificacao)
            investigacao.assunto = check_and_update('Assunto', request.form.get('assunto'), old_assunto)
            investigacao.processo_gdoc = check_and_update('Processo GDOC', request.form.get('processo_gdoc'), old_processo_gdoc)
            investigacao.ano = int(check_and_update('Ano', request.form.get('ano'), old_ano))
            investigacao.denunciante = check_and_update('Denunciante', request.form.get('denunciante'), old_denunciante)
            investigacao.matricula_denunciado = check_and_update('Matrícula Denunciado', request.form.get('matricula_denunciado'), old_matricula_denunciado)
            investigacao.nome_denunciado = check_and_update('Nome Denunciado', request.form.get('nome_denunciado'), old_nome_denunciado)
            investigacao.setor = check_and_update('Setor', request.form.get('setor'), old_setor)
            investigacao.diretoria = check_and_update('Diretoria', request.form.get('diretoria'), old_diretoria)
            investigacao.vinculo = check_and_update('Vínculo', request.form.get('vinculo'), old_vinculo)
            investigacao.objeto_especificacao = check_and_update('Objeto/Especificação', request.form.get('objeto_especificacao'), old_objeto_especificacao)
            investigacao.diligencias = check_and_update('Diligências', request.form.get('diligencias'), old_diligencias)
            investigacao.complexidade = check_and_update('Complexidade', request.form.get('complexidade'), old_complexidade)
            investigacao.justificativa = check_and_update('Justificativa', request.form.get('justificativa'), old_justificativa)
            investigacao.resultado_final = check_and_update('Resultado Final', request.form.get('resultado_final'), old_resultado_final)

            # ✅ REGISTRAR DATA DE CONCLUSÃO (MANUAL OU AUTOMÁTICA)
            novo_status = request.form.get('status')
            data_conclusao_form = request.form.get('data_conclusao')

            # Se o status é "Concluída"
            if novo_status == 'Concluída':
                # Se o usuário preencheu a data manualmente
                if data_conclusao_form:
                    nova_data = datetime.strptime(data_conclusao_form, '%Y-%m-%d').date()
                    if investigacao.data_conclusao != nova_data:
                        investigacao.data_conclusao = nova_data
                        campos_alterados.append(f"- Data de Conclusão definida como {nova_data.strftime('%d/%m/%Y')}")
                # Se não preencheu e não tinha data antes, usar hoje
                elif not investigacao.data_conclusao:
                    investigacao.data_conclusao = datetime.now().date()
                    campos_alterados.append(f"- Status alterado para 'Concluída' em {investigacao.data_conclusao.strftime('%d/%m/%Y')}")

            # Se mudou de "Concluída" para outro status, limpar a data
            elif old_status == 'Concluída' and novo_status != 'Concluída':
                investigacao.data_conclusao = None
                campos_alterados.append(f"- Data de Conclusão removida (status mudou de 'Concluída' para '{novo_status}')")

            investigacao.status = check_and_update('Status', novo_status, old_status)


            nova_entrada_prfi_str = request.form.get('entrada_prfi')
            if nova_entrada_prfi_str and nova_entrada_prfi_str != old_entrada_prfi:
                campos_alterados.append(f"- Entrada PRFI: de '{old_entrada_prfi}' para '{nova_entrada_prfi_str}'")
                investigacao.entrada_prfi = datetime.strptime(nova_entrada_prfi_str, '%Y-%m-%d').date()

            nova_previsao_conclusao_str = request.form.get('previsao_conclusao')
            if nova_previsao_conclusao_str and nova_previsao_conclusao_str != old_previsao_conclusao:
                campos_alterados.append(f"- Previsão Conclusão: de '{old_previsao_conclusao}' para '{nova_previsao_conclusao_str}'")
                investigacao.previsao_conclusao = datetime.strptime(nova_previsao_conclusao_str, '%Y-%m-%d').date()

            db.session.commit()

            if campos_alterados:
                descricao = f"Investigação editada por {session.get('nome')}:\n" + "\n".join(campos_alterados)
                historico = HistoricoDiligencia(
                    investigacao_id=investigacao.id,
                    usuario=session.get('nome'),
                    descricao=descricao,
                    tipo='edicao'
                )
                db.session.add(historico)
                db.session.commit()

            flash(f'Investigação #{investigacao.id} atualizada com sucesso!', 'success')
            return redirect(url_for('detalhes', id=investigacao.id))

        except Exception as e:
            db.session.rollback()
            flash(f'Erro ao atualizar investigação: {str(e)}', 'danger')
            print(f"Erro: {e}")
            import traceback
            traceback.print_exc()

    return render_template('editar_investigacao.html', investigacao=investigacao)



@app.route('/investigacoes/<int:id>/adicionar-diligencia', methods=['POST'])
def adicionar_diligencia(id):
    if 'usuario' not in session:
        flash('Você precisa fazer login primeiro!', 'warning')
        return redirect(url_for('login'))

    if session.get('nivel') not in ['admin', 'investigador']:
        flash('Você não tem permissão para adicionar diligências!', 'danger')
        return redirect(url_for('detalhes', id=id))

    investigacao = Investigacao.query.get_or_404(id)

    try:
        descricao = request.form.get('descricao', '').strip()

        if not descricao:
            flash('A descrição da diligência não pode estar vazia!', 'warning')
            return redirect(url_for('detalhes', id=id))

        # ===== CORREÇÃO DE HORÁRIO AQUI =====
        # Força a subtração de 3 horas para garantir horário de Brasília
        data_brasilia = datetime.now() 

        historico = HistoricoDiligencia(
            investigacao_id=id,
            usuario=session.get('nome'),
            descricao=descricao,
            tipo='diligencia',
            data=data_brasilia  # <--- Forçando a data corrigida no histórico
        )
        db.session.add(historico)

        # Formata a data corrigida para usar no texto abaixo
        data_formatada = data_brasilia.strftime('%d/%m/%Y %H:%M')

        if investigacao.diligencias:
            investigacao.diligencias += f"\n\n[{data_formatada}] {session.get('nome')}:\n{descricao}"
        else:
            investigacao.diligencias = f"[{data_formatada}] {session.get('nome')}:\n{descricao}"

        db.session.commit()

        flash('Diligência adicionada com sucesso!', 'success')

    except Exception as e:
        db.session.rollback()
        flash(f'Erro ao adicionar diligência: {str(e)}', 'danger')
        print(f"Erro: {e}")
        import traceback
        traceback.print_exc()

    return redirect(url_for('detalhes', id=id))



# ==================== ROTA: EXPORTAR EXCEL (COMENTADA TEMPORARIAMENTE) ====================
# @app.route('/exportar-excel')
# def exportar_excel():
#     if 'usuario' not in session:
#         flash('Você precisa fazer login primeiro!', 'warning')
#         return redirect(url_for('login'))
#
#     try:
#         investigacoes = Investigacao.query.all()
#
#         dados = []
#         for inv in investigacoes:
#             dados.append({
#                 'ID': inv.id,
#                 'Processo GDOC': inv.processo_gdoc,
#                 'Responsável': inv.responsavel,
#                 'Status': inv.status,
#                 'Origem': inv.origem,
#                 'Canal': inv.canal,
#                 'Protocolo Origem': inv.protocolo_origem,
#                 'Admitida/Inadmitida': inv.admitida_ou_inadmitida,
#                 'Unidade Origem': inv.unidade_origem,
#                 #                 'Unidade Origem': inv.unidade_origem,
#                 'Classificação': inv.classificacao,
#                 'Assunto': inv.assunto,
#                 'Ano': inv.ano,
#                 'Denunciante': inv.denunciante,
#                 'Matrícula Denunciado': inv.matricula_denunciado,
#                 'Nome Denunciado': inv.nome_denunciado,
#                 'Setor': inv.setor,
#                 'Diretoria': inv.diretoria,
#                 'Vínculo': inv.vinculo,
#                 'Objeto/Especificação': inv.objeto_especificacao,
#                 'Diligências': inv.diligencias,
#                 'Complexidade': inv.complexidade,
#                 'Entrada PRFI': inv.entrada_prfi.strftime('%d/%m/%Y') if inv.entrada_prfi else '',
#                 'Previsão Conclusão': inv.previsao_conclusao.strftime('%d/%m/%Y') if inv.previsao_conclusao else '',
#                 'Justificativa': inv.justificativa,
#                 'Resultado Final': inv.resultado_final
#             })
#
#         df = pd.DataFrame(dados)
#
#         output = BytesIO()
#         with pd.ExcelWriter(output, engine='openpyxl') as writer:
#             df.to_excel(writer, index=False, sheet_name='Investigações')
#
#         output.seek(0)
#
#         return send_file(
#             output,
#             mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
#             as_attachment=True,
#             download_name=f'investigacoes_{datetime.now().strftime("%Y%m%d_%H%M%S")}.xlsx'
#         )
#
#     except Exception as e:
#         flash(f'Erro ao exportar planilha: {str(e)}', 'danger')
#         print(f"Erro: {e}")
#         return redirect(url_for('dashboard'))


# ==================== ROTA DE GERENCIAMENTO DE USUÁRIOS ====================
@app.route('/usuarios')
def usuarios():
    if 'usuario' not in session:
        flash('Você precisa fazer login primeiro!', 'warning')
        return redirect(url_for('login'))

    if session.get('nivel') != 'admin':
        flash('Você não tem permissão para acessar essa página!', 'danger')
        return redirect(url_for('dashboard'))

    todos_usuarios = Usuario.query.order_by(Usuario.nome).all()

    return render_template('usuarios.html', usuarios=todos_usuarios)


@app.route('/usuarios/novo', methods=['POST'])
def novo_usuario():
    if 'usuario' not in session or session.get('nivel') != 'admin':
        flash('Acesso negado!', 'danger')
        return redirect(url_for('dashboard'))

    try:
        username = request.form.get('username', '').strip()
        nome = request.form.get('nome', '').strip()
        senha = request.form.get('senha', '').strip()
        nivel = request.form.get('nivel', 'consulta')

        if not username or not nome or not senha:
            flash('Preencha todos os campos!', 'warning')
            return redirect(url_for('usuarios'))

        existe = Usuario.query.filter_by(username=username).first()
        if existe:
            flash(f'Usuário "{username}" já existe!', 'warning')
            return redirect(url_for('usuarios'))

        novo = Usuario(
            username=username,
            nome=nome,
            senha=senha,
            nivel=nivel,
            ativo=True
        )
        db.session.add(novo)
        db.session.commit()

        flash(f'Usuário "{nome}" criado com sucesso!', 'success')

    except Exception as e:
        db.session.rollback()
        flash(f'Erro ao criar usuário: {str(e)}', 'danger')
        print(f"Erro: {e}")
        import traceback
        traceback.print_exc()

    return redirect(url_for('usuarios'))


@app.route('/usuarios/<int:id>/editar', methods=['POST'])
def editar_usuario(id):
    if 'usuario' not in session or session.get('nivel') != 'admin':
        flash('Acesso negado!', 'danger')
        return redirect(url_for('dashboard'))

    try:
        usuario = Usuario.query.get_or_404(id)

        usuario.nome = request.form.get('nome', '').strip()
        usuario.username = request.form.get('username', '').strip()
        usuario.nivel = request.form.get('nivel', 'consulta')

        nova_senha = request.form.get('senha', '').strip()
        if nova_senha:
            usuario.set_senha(nova_senha)

        db.session.commit()

        flash(f'Usuário "{usuario.nome}" atualizado com sucesso!', 'success')

    except Exception as e:
        db.session.rollback()
        flash(f'Erro ao atualizar usuário: {str(e)}', 'danger')
        print(f"Erro: {e}")
        import traceback
        traceback.print_exc()

    return redirect(url_for('usuarios'))


@app.route('/usuarios/<int:id>/ativar')
def ativar_usuario(id):
    if 'usuario' not in session or session.get('nivel') != 'admin':
        flash('Acesso negado!', 'danger')
        return redirect(url_for('dashboard'))

    try:
        usuario = Usuario.query.get_or_404(id)
        usuario.ativo = True
        db.session.commit()

        flash(f'Usuário "{usuario.nome}" ativado!', 'success')

    except Exception as e:
        db.session.rollback()
        flash(f'Erro: {str(e)}', 'danger')
        print(f"Erro: {e}")
        import traceback
        traceback.print_exc()

    return redirect(url_for('usuarios'))


@app.route('/usuarios/<int:id>/desativar')
def desativar_usuario(id):
    if 'usuario' not in session or session.get('nivel') != 'admin':
        flash('Acesso negado!', 'danger')
        return redirect(url_for('dashboard'))

    try:
        if id == session.get('usuario_id'):
            flash('Você não pode desativar sua própria conta!', 'warning')
            return redirect(url_for('usuarios'))

        usuario = Usuario.query.get_or_404(id)
        usuario.ativo = False
        db.session.commit()

        flash(f'Usuário "{usuario.nome}" desativado!', 'warning')

    except Exception as e:
        db.session.rollback()
        flash(f'Erro: {str(e)}', 'danger')
        print(f"Erro: {e}")
        import traceback
        traceback.print_exc()

    return redirect(url_for('usuarios'))

# ==================== ROTA: EXCLUIR INVESTIGAÇÃO (SÓ ADMIN) ====================
@app.route('/investigacoes/<int:id>/excluir', methods=['POST'])
def excluir_investigacao(id):
    if 'usuario' not in session:
        flash('Você precisa fazer login primeiro!', 'warning')
        return redirect(url_for('login'))

    # APENAS ADMIN PODE EXCLUIR
    if session.get('nivel') != 'admin':
        flash('Você não tem permissão para excluir investigações!', 'danger')
        return redirect(url_for('investigacoes'))

    try:
        investigacao = Investigacao.query.get_or_404(id)
        processo_gdoc = investigacao.processo_gdoc

        # 1. PRIMEIRO: Excluir arquivos físicos dos anexos
        anexos = Anexo.query.filter_by(investigacao_id=id).all()
        for anexo in anexos:
            caminho_completo = os.path.join(current_app.config['UPLOAD_FOLDER'], anexo.caminho_arquivo)
            if os.path.exists(caminho_completo):
                try:
                    os.remove(caminho_completo)
                    print(f"✅ Arquivo físico excluído: {caminho_completo}")
                except Exception as e:
                    print(f"⚠️ Erro ao excluir arquivo: {e}")

        # 2. SEGUNDO: Excluir registros de anexos do banco
        Anexo.query.filter_by(investigacao_id=id).delete()

        # 3. TERCEIRO: Excluir histórico
        HistoricoDiligencia.query.filter_by(investigacao_id=id).delete()

        # 4. QUARTO: Excluir a investigação
        db.session.delete(investigacao)

        # 5. COMMIT FINAL
        db.session.commit()

        flash(f'Investigação #{id} ({processo_gdoc}) excluída com sucesso por {session.get("nome")}!', 'success')
        print(f"🗑️ Investigação #{id} excluída por {session.get('nome')}")

    except Exception as e:
        db.session.rollback()
        flash(f'Erro ao excluir investigação: {str(e)}', 'danger')
        print(f"❌ Erro ao excluir: {e}")
        import traceback
        traceback.print_exc()
        return redirect(url_for('investigacoes'))

    return redirect(url_for('investigacoes'))


# ==================== ROTA: IMPORTAR SERVIDORES (ATUALIZADA PARA CSV) ====================
@app.route('/importar-servidores', methods=['GET', 'POST'])
def importar_servidores():
    if 'usuario' not in session or session.get('nivel') != 'admin':
        flash('Acesso negado!', 'danger')
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        if 'file' not in request.files:
            flash('Nenhum arquivo selecionado!', 'warning')
            return redirect(request.url)

        file = request.files['file']
        # Verifica se é Excel ou CSV
        if file and (file.filename.endswith('.xlsx') or file.filename.endswith('.xls') or file.filename.endswith('.csv')):
            try:
                # Se for CSV, usa o leitor de CSV (Muito mais leve e rápido)
                if file.filename.endswith('.csv'):
                    # dtype=str garante que matrículas como "0123" não virem "123"
                    df = pd.read_csv(file, dtype=str)
                else:
                    # Se for Excel, usa o leitor de Excel
                    df = pd.read_excel(file, dtype=str)

                # Remove espaços em branco dos nomes das colunas
                df.columns = df.columns.str.strip()

                contador = 0
                novos_servidores = []

                # Otimização: Pega todas as matrículas existentes de uma vez para não consultar o banco mil vezes
                matriculas_existentes = set(s.matricula for s in Servidor.query.with_entities(Servidor.matricula).all())

                for index, row in df.iterrows():
                    # Tenta pegar os campos com ou sem acento/maiúscula
                    nome = str(row.get('Nome', row.get('NOME', ''))).strip()
                    # Tenta Matrícula, MATRICULA ou MATRÍCULA
                    matricula = str(row.get('Matrícula', row.get('MATRICULA', row.get('MATRÍCULA', '')))).strip()
                    cargo = str(row.get('Cargo', row.get('CARGO', ''))).strip()
                    lotacao = str(row.get('Lotação', row.get('LOTACAO', row.get('LOTAÇÃO', '')))).strip()


                    # Ignora linhas vazias ou sem matrícula
                    if not nome or not matricula or nome == 'nan' or matricula == 'nan':
                        continue

                    # Se a matrícula não existe no banco, adiciona na lista para salvar
                    if matricula not in matriculas_existentes:
                        novo_servidor = Servidor(
                            nome=nome,
                            matricula=matricula,
                            cargo=cargo,
                            lotacao=lotacao
                        )
                        novos_servidores.append(novo_servidor)
                        matriculas_existentes.add(matricula) # Adiciona no set para evitar duplicata na própria planilha
                        contador += 1

                # Salva tudo de uma vez (Muito mais rápido e gasta menos memória)
                if novos_servidores:
                    db.session.bulk_save_objects(novos_servidores)
                    db.session.commit()

                flash(f'{contador} servidores importados com sucesso!', 'success')
                return redirect(url_for('dashboard'))

            except Exception as e:
                db.session.rollback()
                flash(f'Erro ao processar arquivo: {str(e)}', 'danger')
                print(f"❌ Erro ao importar servidores: {e}")
                import traceback
                traceback.print_exc()
        else:
            flash('Formato inválido! Use CSV (.csv) ou Excel (.xlsx)', 'danger')

    return render_template('importar_servidores.html')



# ==================== ROTA: API PARA BUSCAR SERVIDOR (PARA AUTOCOMPLETE) ====================
@app.route('/api/buscar-servidor')
# @login_required  <-- MANTENHA COMENTADO POR ENQUANTO
def buscar_servidor():
    try:
        termo = request.args.get('q', '')

        # Se digitar menos de 3 letras, não busca nada
        if len(termo) < 3:
            return jsonify([])

        # Busca no banco de dados (limite de 10 para não travar)
        servidores = Servidor.query.filter(Servidor.nome.ilike(f'%{termo}%')).limit(10).all()

        # Monta a lista de resultados
        resultado = []
        for s in servidores:
            resultado.append({
                'nome': s.nome,
                'matricula': s.matricula,
                'cargo': s.cargo,
                'lotacao': s.lotacao
            })

        return jsonify(resultado)

    except Exception as e:
        print(f"ERRO AO BUSCAR SERVIDOR: {e}")
        return jsonify([])


# ISSO VAI FORÇAR A CRIAÇÃO DAS TABELAS NO RENDER
with app.app_context():
    db.create_all()






if __name__ == '__main__':
    print("🚀 Iniciando Sistema PIP...")
    print("📋 Usuários cadastrados no banco:")
    with app.app_context():
        usuarios_db = Usuario.query.all()
        for u in usuarios_db:
            print(f"   - {u.username} / {u.nome} ({u.nivel})")
    print("\n")
    app.run(debug=True, host='0.0.0.0', port=5000)
