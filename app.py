from flask import Flask, render_template, request, redirect, url_for, session, flash, send_file, current_app
from models import db, Investigacao, HistoricoDiligencia, Usuario, Anexo
from config import Config
from datetime import datetime, timedelta
import json
# import pandas as pd  # ✅ COMENTADO

from io import BytesIO
from collections import Counter
from werkzeug.utils import secure_filename
import os
import mimetypes

app = Flask(__name__)
app.config.from_object(Config)

db.init_app(app)

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
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in current_app.config['ALLOWED_EXTENSIONS']


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

# ==================== ROTA: EXPORTAR PDF DA INVESTIGAÇÃO (LAYOUT PROFISSIONAL) ====================
@app.route('/investigacoes/<int:id>/exportar-pdf')
def exportar_pdf_investigacao(id):
    if 'usuario' not in session:
        flash('Você precisa fazer login primeiro!', 'warning')
        return redirect(url_for('login'))

    try:
        # Imports necessários para o PDF
        from reportlab.lib.pagesizes import A4
        from reportlab.lib import colors
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import cm
        from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak, Image
        from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_JUSTIFY, TA_RIGHT
        from io import BytesIO

        investigacao = Investigacao.query.get_or_404(id)
        historico = HistoricoDiligencia.query.filter_by(investigacao_id=id).order_by(HistoricoDiligencia.data.desc()).all()
        anexos = Anexo.query.filter_by(investigacao_id=id).order_by(Anexo.data_upload.desc()).all()

        # Calcular dias restantes
        dias_restantes = None
        if investigacao.previsao_conclusao and investigacao.status == 'Em Andamento':
            hoje = datetime.now().date()
            dias_restantes = (investigacao.previsao_conclusao - hoje).days

        # Criar buffer para o PDF
        buffer = BytesIO()

        # Configurar margens do documento
        doc = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            rightMargin=1.5*cm,
            leftMargin=1.5*cm,
            topMargin=1.5*cm,
            bottomMargin=1.5*cm
        )

        # Estilos
        styles = getSampleStyleSheet()

        # Estilo do Cabeçalho
        header_text_style = ParagraphStyle(
            'HeaderText',
            parent=styles['Normal'],
            fontSize=10,
            alignment=TA_LEFT,
            leading=12
        )

        header_title_style = ParagraphStyle(
            'HeaderTitle',
            parent=styles['Normal'],
            fontSize=12,
            fontName='Helvetica-Bold',
            alignment=TA_LEFT,
            leading=14,
            textColor=colors.HexColor('#003366') # Azul escuro institucional
        )

        # Estilo para Títulos de Seção
        section_style = ParagraphStyle(
            'SectionTitle',
            parent=styles['Heading2'],
            fontSize=11,
            textColor=colors.white,
            backColor=colors.HexColor('#005b96'), # Azul médio
            borderPadding=(5, 2, 5, 2), # Padding: top, right, bottom, left
            spaceAfter=6,
            spaceBefore=12,
            fontName='Helvetica-Bold',
            alignment=TA_LEFT
        )

        # Estilo para texto normal
        normal_style = ParagraphStyle(
            'CustomNormal',
            parent=styles['Normal'],
            fontSize=10,
            alignment=TA_JUSTIFY,
            leading=12,
            spaceAfter=6
        )

        elements = []

               # ===== CABEÇALHO COM LOGO (Versão Proporcional) =====
        from reportlab.lib.utils import ImageReader  # Importante para ler o tamanho real

        # Procura por várias extensões possíveis
        extensoes = ['logo.png', 'logo.jpg', 'logo.jpeg', 'logo.PNG', 'logo.JPG']
        logo_path = None

        for ext in extensoes:
            caminho_teste = os.path.join(app.root_path, 'static', ext)
            if os.path.exists(caminho_teste):
                logo_path = caminho_teste
                break

        logo_img = None
        largura_logo = 3 * cm  # Largura padrão caso falhe

        if logo_path:
            try:
                # 1. Ler as dimensões originais da imagem
                img_reader = ImageReader(logo_path)
                iw, ih = img_reader.getSize()
                aspect = iw / float(ih)

                # 2. Definir altura fixa (2.5cm) e calcular largura proporcional
                new_height = 2.2 * cm
                new_width = new_height * aspect
                largura_logo = new_width + (0.5 * cm) # Margem de segurança para a coluna

                logo_img = Image(logo_path, width=new_width, height=new_height)
                logo_img.hAlign = 'LEFT'
            except Exception as e:
                print(f"❌ Erro ao carregar imagem: {e}")
                logo_img = Paragraph("[ERRO IMG]", normal_style)

        # Texto do cabeçalho
        texto_cabecalho = [
            Paragraph("COMPANHIA DE SANEAMENTO AMBIENTAL DO DF - CAESB", header_text_style),
            Paragraph("Corregedoria - PRF", header_title_style),
            Paragraph("Gerência de Investigação", header_text_style),
            Spacer(1, 0.2*cm),
            Paragraph(f"FICHA DE INVESTIGAÇÃO #{investigacao.id}", ParagraphStyle('Title', parent=styles['Heading1'], fontSize=16, alignment=TA_LEFT, spaceBefore=10))
        ]

        # Tabela do cabeçalho (Logo na esquerda, Texto na direita)
        if logo_img:
            # A coluna da logo se adapta à largura da imagem
            data_header = [[logo_img, texto_cabecalho]]
            t_header = Table(data_header, colWidths=[largura_logo, 13*cm])
            t_header.setStyle(TableStyle([
                ('VALIGN', (0,0), (-1,-1), 'TOP'),
                ('LEFTPADDING', (0,0), (-1,-1), 0),
                ('BOTTOMPADDING', (0,0), (-1,-1), 10),
            ]))
            elements.append(t_header)
        else:
            # Se não tiver logo, só mostra o texto
            for item in texto_cabecalho:
                elements.append(item)
            elements.append(Spacer(1, 0.5*cm))

        elements.append(Spacer(1, 0.5*cm))

        # ===== INFORMAÇÕES GERAIS =====
        elements.append(Paragraph("1. INFORMAÇÕES GERAIS", section_style))

        dados_gerais = [
            [Paragraph('<b>Processo GDOC:</b>', normal_style), investigacao.processo_gdoc or '-'],
            [Paragraph('<b>Protocolo Origem:</b>', normal_style), investigacao.protocolo_origem or '-'],
            [Paragraph('<b>Origem / Canal:</b>', normal_style), f"{investigacao.origem or '-'} / {investigacao.canal or '-'}"],
            [Paragraph('<b>Unidade Origem:</b>', normal_style), investigacao.unidade_origem or '-'],
            [Paragraph('<b>Classificação:</b>', normal_style), investigacao.classificacao or '-'],
            [Paragraph('<b>Assunto:</b>', normal_style), investigacao.assunto or '-'],
            [Paragraph('<b>Ano:</b>', normal_style), str(investigacao.ano) if investigacao.ano else '-'],
        ]

        t_gerais = Table(dados_gerais, colWidths=[5*cm, 12.5*cm])
        t_gerais.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (0, -1), colors.HexColor('#f0f0f0')), # Fundo cinza claro na coluna 1
            ('GRID', (0, 0), (-1, -1), 0.25, colors.grey),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('PADDING', (0, 0), (-1, -1), 6),
        ]))
        elements.append(t_gerais)

        # ===== ENVOLVIDOS =====
        elements.append(Paragraph("2. ENVOLVIDOS", section_style))

        dados_envolvidos = [
            [Paragraph('<b>Denunciante(s):</b>', normal_style), Paragraph(investigacao.denunciante or '-', normal_style)],
            [Paragraph('<b>Denunciado:</b>', normal_style), Paragraph(investigacao.nome_denunciado or '-', normal_style)],
            [Paragraph('<b>Matrícula:</b>', normal_style), investigacao.matricula_denunciado or '-'],
            [Paragraph('<b>Setor / Diretoria:</b>', normal_style), f"{investigacao.setor or '-'} / {investigacao.diretoria or '-'}"],
            [Paragraph('<b>Vínculo:</b>', normal_style), investigacao.vinculo or '-'],
        ]

        t_envolvidos = Table(dados_envolvidos, colWidths=[5*cm, 12.5*cm])
        t_envolvidos.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (0, -1), colors.HexColor('#f0f0f0')),
            ('GRID', (0, 0), (-1, -1), 0.25, colors.grey),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('PADDING', (0, 0), (-1, -1), 6),
        ]))
        elements.append(t_envolvidos)

        # ===== OBJETO E DILIGÊNCIAS =====
        elements.append(Paragraph("3. OBJETO E DILIGÊNCIAS", section_style))

        elements.append(Paragraph("<b>Objeto / Especificação:</b>", normal_style))
        elements.append(Paragraph(investigacao.objeto_especificacao or "Não informado.", normal_style))
        elements.append(Spacer(1, 0.3*cm))

        elements.append(Paragraph("<b>Diligências Realizadas:</b>", normal_style))
        if investigacao.diligencias:
            # Quebrar linhas para o PDF
            for linha in investigacao.diligencias.split('\n'):
                if linha.strip():
                    elements.append(Paragraph(linha, normal_style))
        else:
            elements.append(Paragraph("Nenhuma diligência registrada.", normal_style))

        # ===== PRAZOS E STATUS =====
        elements.append(Paragraph("4. PRAZOS E STATUS", section_style))

        dados_prazos = [
            [Paragraph('<b>Responsável:</b>', normal_style), investigacao.responsavel or '-'],
            [Paragraph('<b>Complexidade:</b>', normal_style), investigacao.complexidade or '-'],
            [Paragraph('<b>Entrada PRFI:</b>', normal_style), investigacao.entrada_prfi.strftime('%d/%m/%Y') if investigacao.entrada_prfi else '-'],
            [Paragraph('<b>Previsão Conclusão:</b>', normal_style), investigacao.previsao_conclusao.strftime('%d/%m/%Y') if investigacao.previsao_conclusao else '-'],
            [Paragraph('<b>Status Atual:</b>', normal_style), investigacao.status or '-'],
            [Paragraph('<b>Resultado Final:</b>', normal_style), investigacao.resultado_final or '-'],
        ]

        # Adicionar data de conclusão se houver
        if investigacao.data_conclusao:
             dados_prazos.append([Paragraph('<b>Data Conclusão:</b>', normal_style), investigacao.data_conclusao.strftime('%d/%m/%Y')])

        t_prazos = Table(dados_prazos, colWidths=[5*cm, 12.5*cm])
        t_prazos.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (0, -1), colors.HexColor('#f0f0f0')),
            ('GRID', (0, 0), (-1, -1), 0.25, colors.grey),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('PADDING', (0, 0), (-1, -1), 6),
        ]))
        elements.append(t_prazos)

        # ===== ANEXOS =====
        if anexos:
            elements.append(Paragraph("5. ANEXOS VINCULADOS", section_style))

            dados_anexos = [[Paragraph('<b>Arquivo</b>', normal_style), Paragraph('<b>Tamanho</b>', normal_style), Paragraph('<b>Data Upload</b>', normal_style)]]
            for anexo in anexos:
                tamanho_mb = round(anexo.tamanho_bytes / 1024 / 1024, 2)
                data_upload = anexo.data_upload.strftime('%d/%m/%Y %H:%M') if anexo.data_upload else '-'
                dados_anexos.append([
                    Paragraph(anexo.nome_arquivo, normal_style), 
                    f"{tamanho_mb} MB", 
                    data_upload
                ])

            t_anexos = Table(dados_anexos, colWidths=[9.5*cm, 3*cm, 5*cm])
            t_anexos.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#e0e0e0')), # Cabeçalho cinza
                ('GRID', (0, 0), (-1, -1), 0.25, colors.grey),
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                ('PADDING', (0, 0), (-1, -1), 5),
            ]))
            elements.append(t_anexos)

        # ===== RODAPÉ =====
        elements.append(Spacer(1, 1.5*cm))
        rodape_style = ParagraphStyle(
            'Rodape',
            parent=styles['Normal'],
            fontSize=8,
            textColor=colors.grey,
            alignment=TA_CENTER
        )
        elements.append(Paragraph(
            f"Documento gerado automaticamente pelo Sistema PIP em {datetime.now().strftime('%d/%m/%Y às %H:%M')}",
            rodape_style
        ))
        elements.append(Paragraph(
            f"Usuário responsável pela emissão: {session.get('nome')}",
            rodape_style
        ))

        # Gerar PDF
        doc.build(elements)

        # Preparar para download
        buffer.seek(0)
        nome_arquivo = f"Investigacao_{investigacao.id}_{datetime.now().strftime('%Y%m%d')}.pdf"

        return send_file(
            buffer,
            mimetype='application/pdf',
            as_attachment=True,
            download_name=nome_arquivo
        )

    except Exception as e:
        flash(f'Erro ao gerar PDF: {str(e)}', 'danger')
        print(f"❌ Erro ao gerar PDF: {e}")
        import traceback
        traceback.print_exc()
        return redirect(url_for('detalhes', id=id))

# ==================== ROTA: UPLOAD DE ANEXOS ====================
@app.route('/investigacoes/<int:id>/upload_anexo', methods=['POST'])
def upload_anexo(id):
    if 'usuario' not in session:
        flash('Você precisa fazer login primeiro!', 'warning')
        return redirect(url_for('login'))

    if session.get('nivel') not in ['admin', 'investigador']:
        flash('Você não tem permissão para anexar arquivos!', 'danger')
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
            base, ext = os.path.splitext(filename)
            unique_filename = f"{base}_{datetime.now().strftime('%Y%m%d%H%M%S')}{ext}"

            # ✅ GARANTIR QUE A PASTA EXISTE ANTES DE SALVAR
            pasta_uploads = app.config['UPLOAD_FOLDER']
            if not os.path.exists(pasta_uploads):
                os.makedirs(pasta_uploads)

            filepath = os.path.join(pasta_uploads, unique_filename)
            file.save(filepath)

            mime_type, _ = mimetypes.guess_type(filepath)
            if not mime_type:
                mime_type = 'application/octet-stream'

            novo_anexo = Anexo(
                investigacao_id=investigacao.id,
                nome_arquivo=filename,
                caminho_arquivo=unique_filename,
                tipo_mime=mime_type,
                tamanho_bytes=os.path.getsize(filepath),
                usuario_upload=session.get('nome')
            )
            db.session.add(novo_anexo)
            db.session.commit()

            flash(f'Arquivo "{filename}" anexado com sucesso!', 'success')

        except Exception as e:
            db.session.rollback()
            flash(f'Erro ao fazer upload do arquivo: {str(e)}', 'danger')
            print(f"Erro no upload: {e}")
    else:
        flash('Tipo de arquivo não permitido!', 'danger')

    return redirect(url_for('detalhes', id=id))


# ==================== ROTA: DOWNLOAD DE ANEXOS ====================
@app.route('/anexos/<int:anexo_id>/download')
def download_anexo(anexo_id):
    if 'usuario' not in session:
        flash('Você precisa fazer login primeiro!', 'warning')
        return redirect(url_for('login'))

    anexo = Anexo.query.get_or_404(anexo_id)
    filepath = os.path.join(current_app.config['UPLOAD_FOLDER'], anexo.caminho_arquivo)

    if not os.path.exists(filepath):
        flash('Arquivo não encontrado no servidor!', 'danger')
        return redirect(url_for('detalhes', id=anexo.investigacao_id))

    try:
        return send_file(filepath, as_attachment=True, download_name=anexo.nome_arquivo, mimetype=anexo.tipo_mime)
    except Exception as e:
        flash(f'Erro ao baixar arquivo: {str(e)}', 'danger')
        print(f"Erro no download: {e}")
        return redirect(url_for('detalhes', id=anexo.investigacao_id))

# ==================== ROTA: EXCLUIR ANEXO ====================
@app.route('/anexos/<int:anexo_id>/excluir', methods=['POST'])
def excluir_anexo(anexo_id):
    if 'usuario' not in session:
        flash('Você precisa fazer login primeiro!', 'warning')
        return redirect(url_for('login'))

    # Controle de permissão: Apenas admin e investigador podem excluir
    if session.get('nivel') not in ['admin', 'investigador']:
        flash('Você não tem permissão para excluir anexos!', 'danger')
        return redirect(url_for('investigacoes'))

    try:
        anexo = Anexo.query.get_or_404(anexo_id)
        investigacao_id = anexo.investigacao_id
        nome_arquivo = anexo.nome_arquivo
        caminho_arquivo = anexo.caminho_arquivo

        # Caminho completo do arquivo no servidor
        filepath = os.path.join(current_app.config['UPLOAD_FOLDER'], caminho_arquivo)

        # Excluir arquivo físico do servidor (se existir)
        if os.path.exists(filepath):
            try:
                os.remove(filepath)
                print(f"✅ Arquivo físico excluído: {filepath}")
            except Exception as e:
                print(f"⚠️ Erro ao excluir arquivo físico: {e}")
                # Continua mesmo se não conseguir excluir o arquivo físico

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
        return redirect(url_for('investigacoes'))

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

        historico = HistoricoDiligencia(
            investigacao_id=id,
            usuario=session.get('nome'),
            descricao=descricao,
            tipo='diligencia'
        )
        db.session.add(historico)

        if investigacao.diligencias:
            investigacao.diligencias += f"\n\n[{datetime.now().strftime('%d/%m/%Y %H:%M')}] {session.get('nome')}:\n{descricao}"
        else:
            investigacao.diligencias = f"[{datetime.now().strftime('%d/%m/%Y %H:%M')}] {session.get('nome')}:\n{descricao}"

        db.session.commit()

        flash('Diligência adicionada com sucesso!', 'success')

    except Exception as e:
        db.session.rollback()
        flash(f'Erro ao adicionar diligência: {str(e)}', 'danger')
        print(f"Erro: {e}")

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


if __name__ == '__main__':
    print("🚀 Iniciando Sistema PIP...")
    print("📋 Usuários cadastrados no banco:")
    with app.app_context():
        usuarios_db = Usuario.query.all()
        for u in usuarios_db:
            print(f"   - {u.username} / {u.nome} ({u.nivel})")
    print("\n")
    app.run(debug=True, host='0.0.0.0', port=5000)


