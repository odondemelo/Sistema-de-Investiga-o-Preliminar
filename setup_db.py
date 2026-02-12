    # setup_db.py
import os
from app import app, db, Servidor, Usuario, Config # Importe tudo que é necessário

with app.app_context():
        print("Iniciando configuração do banco de dados...")

        # 1. Criar todas as tabelas
        db.create_all()
        print("✅ Todas as tabelas foram criadas ou verificadas.")

        # 2. Migrar usuários padrão do Config.py para o banco (apenas se não existirem)
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
        print("✅ Usuários padrão verificados/criados e alterações salvas.")

        # 3. Verificar se a tabela 'servidor' existe
        from sqlalchemy import inspect
        inspector = inspect(db.engine)
        if 'servidor' in inspector.get_table_names():
            print("🎉 A tabela 'servidor' existe no banco de dados!")
        else:
            print("❌ A tabela 'servidor' AINDA NÃO existe no banco de dados.")
            print("Por favor, verifique se o modelo 'Servidor' está corretamente definido em app.py.")

print("Configuração do banco de dados finalizada.")
