from flask import Flask, render_template, request, redirect, url_for, session, flash
from models import db, Investigacao, HistoricoDiligencia
from config import Config
from datetime import datetime, timedelta
import json

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

# Criar banco de dados
with app.app_context():
    db.create_all()

    # Importar dados iniciais se o banco estiver vazio
    if Investigacao.query.count() == 0:
        try:
            with open('dados_iniciais.json', 'r', encoding='utf-8') as f:
                dados = json.load(f)

            for item in dados:
                entrada = None
                previsao = None

                if item.get('Entrada_PRFI'):
                    try:
                        entrada = datetime.strptime(item['Entrada_PRFI'], '%d/%m/%Y').date()
                    except:
                        entrada = datetime.now().date()

                if item.get('Previsao_Conclusao'):
                    try:
                        previsao = datetime.strptime(item['Previsao_Conclusao'], '%d/%m/%Y').date()
                    except:
                        previsao = (datetime.now() + timedelta(days=120)).date()

                inv = Investigacao(
                    responsavel=item.get('Responsavel', 'Não informado'),
                    origem=item.get('Origem'),
                    canal=item.get('Canal'),
                    protocolo_origem=item.get('Protocolo_Origem'),
                    admitida_ou_inadmitida=item.get('Admitida_ou_Inadmitida'),
                    unidade_origem=item.get('Unidade_Origem'),
                    classificacao=item.get('Classificacao'),
                    assunto=item.get('Assunto'),
                    processo_gdoc=item.get('Processo_GDOC', f'PROC-{item.get("id", 1)}/2025'),
                    ano=item.get('Ano', 2025),
                    denunciante=item.get('Denunciante'),
                    matricula_denunciado=item.get('Matricula_Denunciado'),
                    nome_denunciado=item.get('Nome_Denunciado'),
                    setor=item.get('Setor'),
                    diretoria=item.get('Diretoria'),
                    vinculo=item.get('Vinculo'),
                    objeto_especificacao=item.get('Objeto_Especificacao'),
                    diligencias=item.get('Diligencias'),
                    complexidade=item.get('Complexidade'),
                    entrada_prfi=entrada,
                    previsao_conclusao=previsao,
                    status=item.get('Status', 'Em Andamento')
                )

                db.session.add(inv)

            db.session.commit()
            print("✅ Dados iniciais importados com sucesso!")

        except FileNotFoundError:
            print("⚠️ Arquivo dados_iniciais.json não encontrado")
        except Exception as e:
            print(f"❌ Erro ao importar dados: {e}")


@app.route('/')
def index():
    if 'usuario' in session:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()

        print(f"🔐 Tentativa de login - Usuário: '{username}', Senha: '{password}'")

        usuarios = app.config['USUARIOS_PADRAO']
        print(f"👥 Usuários disponíveis: {list(usuarios.keys())}")

        if username in usuarios:
            if password == usuarios[username]['senha']:
                session.clear()
                session['usuario'] = username
                session['nome'] = usuarios[username]['nome']
                session.permanent = True
                print(f"✅ Login bem-sucedido para: {username}")
                flash(f'Bem-vindo, {usuarios[username]["nome"]}!', 'success')
                return redirect(url_for('dashboard'))
            else:
                print(f"❌ Senha incorreta para usuário: {username}")
                flash('Senha incorreta!', 'danger')
        else:
            print(f"❌ Usuário não encontrado: {username}")
            flash('Usuário não encontrado!', 'danger')

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

    # Estatísticas simples
    total = Investigacao.query.count()
    em_andamento = Investigacao.query.filter_by(status='Em Andamento').count()
    concluidas = Investigacao.query.filter_by(status='Concluída').count()

    # Investigações recentes
    recentes = Investigacao.query.order_by(Investigacao.id.desc()).limit(5).all()

    return render_template('dashboard.html',
                         total=total,
                         em_andamento=em_andamento,
                         concluidas=concluidas,
                         recentes=recentes)


@app.route('/investigacoes')
def investigacoes():
    if 'usuario' not in session:
        flash('Você precisa fazer login primeiro!', 'warning')
        return redirect(url_for('login'))

    todas = Investigacao.query.order_by(Investigacao.id.desc()).all()
    return render_template('investigacoes.html', investigacoes=todas)


@app.route('/investigacoes/<int:id>')
def detalhes(id):
    if 'usuario' not in session:
        flash('Você precisa fazer login primeiro!', 'warning')
        return redirect(url_for('login'))

    inv = Investigacao.query.get_or_404(id)
    historico = HistoricoDiligencia.query.filter_by(investigacao_id=id).order_by(HistoricoDiligencia.data.desc()).all()
    return render_template('detalhes.html', investigacao=inv, historico=historico)


@app.route('/investigacoes/nova', methods=['GET', 'POST'])
def nova_investigacao():
    if 'usuario' not in session:
        flash('Você precisa fazer login primeiro!', 'warning')
        return redirect(url_for('login'))

    if request.method == 'POST':
        try:
            # Capturar dados do formulário
            entrada_prfi = None
            previsao = None

            if request.form.get('entrada_prfi'):
                entrada_prfi = datetime.strptime(request.form.get('entrada_prfi'), '%Y-%m-%d').date()

            if request.form.get('previsao_conclusao'):
                previsao = datetime.strptime(request.form.get('previsao_conclusao'), '%Y-%m-%d').date()

            # Criar nova investigação
            nova_inv = Investigacao(
                responsavel=request.form.get('responsavel'),
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
                vinculo=request.form.get('vinculo'),
                objeto_especificacao=request.form.get('objeto_especificacao'),
                diligencias=request.form.get('diligencias'),
                complexidade=request.form.get('complexidade'),
                entrada_prfi=entrada_prfi,
                previsao_conclusao=previsao,
                status=request.form.get('status', 'Em Andamento'),
                justificativa=request.form.get('justificativa')
            )

            db.session.add(nova_inv)
            db.session.commit()

            # Registrar no histórico
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

    return render_template('nova_investigacao.html')


@app.route('/investigacoes/<int:id>/editar', methods=['GET', 'POST'])
def editar_investigacao(id):
    if 'usuario' not in session:
        flash('Você precisa fazer login primeiro!', 'warning')
        return redirect(url_for('login'))

    investigacao = Investigacao.query.get_or_404(id)

    if request.method == 'POST':
        try:
            # Capturar valores antigos para histórico
            campos_alterados = []

            # Atualizar campos e registrar alterações
            if investigacao.responsavel != request.form.get('responsavel'):
                campos_alterados.append(f"Responsável: {investigacao.responsavel} → {request.form.get('responsavel')}")
                investigacao.responsavel = request.form.get('responsavel')

            if investigacao.processo_gdoc != request.form.get('processo_gdoc'):
                campos_alterados.append(f"Processo GDOC: {investigacao.processo_gdoc} → {request.form.get('processo_gdoc')}")
                investigacao.processo_gdoc = request.form.get('processo_gdoc')

            if investigacao.status != request.form.get('status'):
                campos_alterados.append(f"Status: {investigacao.status} → {request.form.get('status')}")
                investigacao.status = request.form.get('status')

            # Atualizar todos os outros campos
            investigacao.origem = request.form.get('origem')
            investigacao.canal = request.form.get('canal')
            investigacao.protocolo_origem = request.form.get('protocolo_origem')
            investigacao.admitida_ou_inadmitida = request.form.get('admitida_ou_inadmitida')
            investigacao.unidade_origem = request.form.get('unidade_origem')
            investigacao.classificacao = request.form.get('classificacao')
            investigacao.assunto = request.form.get('assunto')
            investigacao.ano = request.form.get('ano', datetime.now().year)
            investigacao.denunciante = request.form.get('denunciante')
            investigacao.matricula_denunciado = request.form.get('matricula_denunciado')
            investigacao.nome_denunciado = request.form.get('nome_denunciado')
            investigacao.setor = request.form.get('setor')
            investigacao.diretoria = request.form.get('diretoria')
            investigacao.vinculo = request.form.get('vinculo')
            investigacao.objeto_especificacao = request.form.get('objeto_especificacao')
            investigacao.diligencias = request.form.get('diligencias')
            investigacao.complexidade = request.form.get('complexidade')
            investigacao.justificativa = request.form.get('justificativa')
            investigacao.resultado_final = request.form.get('resultado_final')

            # Datas
            if request.form.get('entrada_prfi'):
                investigacao.entrada_prfi = datetime.strptime(request.form.get('entrada_prfi'), '%Y-%m-%d').date()

            if request.form.get('previsao_conclusao'):
                investigacao.previsao_conclusao = datetime.strptime(request.form.get('previsao_conclusao'), '%Y-%m-%d').date()

            db.session.commit()

            # Registrar no histórico
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

    investigacao = Investigacao.query.get_or_404(id)

    try:
        descricao = request.form.get('descricao', '').strip()

        if not descricao:
            flash('A descrição da diligência não pode estar vazia!', 'warning')
            return redirect(url_for('detalhes', id=id))

        # Adicionar ao histórico
        historico = HistoricoDiligencia(
            investigacao_id=id,
            usuario=session.get('nome'),
            descricao=descricao,
            tipo='diligencia'
        )
        db.session.add(historico)

        # Atualizar campo de diligências da investigação
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


if __name__ == '__main__':
    print("🚀 Iniciando Sistema PIP...")
    print("📋 Usuários disponíveis:")
    for user, info in Config.USUARIOS_PADRAO.items():
        print(f"   - {user} / {info['senha']} ({info['nome']})")
    print("\n")
    app.run(debug=True, host='0.0.0.0', port=5000)
