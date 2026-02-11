from app import app, db
from sqlalchemy import text

with app.app_context():
    try:
        # Tentar adicionar a coluna data_conclusao
        with db.engine.connect() as conn:
            conn.execute(text('ALTER TABLE investigacoes ADD COLUMN data_conclusao DATE'))
            conn.commit()

        print("✅ Coluna 'data_conclusao' adicionada com sucesso!")

    except Exception as e:
        if "duplicate column name" in str(e).lower():
            print("⚠️ Coluna 'data_conclusao' já existe!")
        else:
            print(f"❌ Erro ao adicionar coluna: {e}")
