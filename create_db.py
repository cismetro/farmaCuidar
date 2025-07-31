"""
Script para criar banco de dados e usuários padrão
FarmaCuidar - Cosmópolis
"""
import os
import sys

# Adicionar diretório atual ao path
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, current_dir)

def create_database():
    """Cria as tabelas do banco de dados"""
    print("🚀 Iniciando criação do banco de dados...")
    
    try:
        # Imports corretos
        print("📦 Importando módulos...")
        from app import create_app
        print("✅ create_app importado")
        
        from app.database import db
        print("✅ database.db importado")
        
        from app.models import User, UserRole
        print("✅ models importados")
        
        # Criar aplicação
        print("⚙️ Criando aplicação Flask...")
        app = create_app()
        print("✅ Aplicação criada")
        
        with app.app_context():
            print("📊 Criando tabelas do banco...")
            
            # Criar todas as tabelas
            db.create_all()
            print("✅ Tabelas criadas com sucesso!")
            
            # Verificar se admin já existe
            existing_admin = User.query.filter_by(username='admin').first()
            if not existing_admin:
                print("👤 Criando usuários padrão...")
                
                # Criar usuário admin
                admin = User(
                    username='admin',
                    email='admin@cosmopolis.sp.gov.br',
                    full_name='Administrador Sistema',
                    role=UserRole.ADMIN,
                    is_active=True
                )
                admin.set_password('admin123')
                db.session.add(admin)
                
                # Criar farmacêutico padrão
                pharmacist = User(
                    username='farmaceutico',
                    email='farmaceutico@cosmopolis.sp.gov.br',
                    full_name='Farmacêutico Responsável',
                    role=UserRole.PHARMACIST,
                    is_active=True,
                    crf='12345-SP'
                )
                pharmacist.set_password('farm123')
                db.session.add(pharmacist)
                
                # Salvar no banco
                db.session.commit()
                print("✅ Usuários padrão criados:")
                print("   👤 Usuário: admin | Senha: admin123")
                print("   💊 Usuário: farmaceutico | Senha: farm123")
                
            else:
                print("ℹ️ Usuários padrão já existem no banco.")
            
            print("🎉 Banco de dados configurado com sucesso!")
            return True
            
    except ImportError as e:
        print(f"❌ Erro de importação: {e}")
        print("💡 Verificando arquivos necessários...")
        
        # Verificar arquivos
        files_to_check = [
            'app/__init__.py',
            'app/database.py', 
            'app/models.py',
            'config.py'
        ]
        
        for file_path in files_to_check:
            if os.path.exists(file_path):
                print(f"   ✅ {file_path}")
            else:
                print(f"   ❌ {file_path} - FALTANDO!")
        
        return False
        
    except Exception as e:
        print(f"❌ Erro ao criar banco: {e}")
        print(f"💡 Tipo do erro: {type(e).__name__}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == '__main__':
    print("=" * 60)
    print("🏥 FarmaCuidar - Setup do Banco de Dados")
    print("=" * 60)
    
    success = create_database()
    
    print("=" * 60)
    if success:
        print("✅ SETUP COMPLETO!")
        print("\n🚀 Próximos passos:")
        print("   1. python run.py")
        print("   2. Abrir http://localhost:5000")
        print("   3. Login: admin / admin123")
    else:
        print("❌ FALHA NO SETUP!")
        print("\n🔧 Verifique os arquivos listados acima.")
    print("=" * 60)