from flask import Flask
from config import config
import os

def create_app(config_name=None):
    if config_name is None:
        config_name = os.environ.get('FLASK_ENV', 'default')
    
    app = Flask(__name__)
    app.config.from_object(config[config_name])
    
    # Inicializar extensões
    from app.database import init_extensions
    db, login_manager = init_extensions(app)
    
    # Criar pasta de uploads
    upload_folder = app.config.get('UPLOAD_FOLDER', 'uploads')
    if not os.path.exists(upload_folder):
        os.makedirs(upload_folder)
        os.makedirs(os.path.join(upload_folder, 'prescriptions'), exist_ok=True)
        os.makedirs(os.path.join(upload_folder, 'medical_reports'), exist_ok=True)
        os.makedirs(os.path.join(upload_folder, 'documents'), exist_ok=True)
    
    # Adicionar funções básicas de formatação
    add_template_functions(app)
    
    # Registrar blueprints (APÓS inicializar extensões)
    from app.routes import main
    app.register_blueprint(main)
    
    return app

def add_template_functions(app):
    """Adicionar funções básicas para templates"""
    
    # Funções de formatação simples
    def format_cpf(cpf):
        """Formatar CPF: 12345678901 -> 123.456.789-01"""
        if not cpf:
            return ''
        # Remover não números
        cpf = ''.join(filter(str.isdigit, str(cpf)))
        if len(cpf) == 11:
            return f"{cpf[:3]}.{cpf[3:6]}.{cpf[6:9]}-{cpf[9:]}"
        return cpf
    
    def format_cns(cns):
        """Formatar CNS: 123456789012345 -> 123 4567 8901 2345"""
        if not cns:
            return ''
        cns = ''.join(filter(str.isdigit, str(cns)))
        if len(cns) == 15:
            return f"{cns[:3]} {cns[3:7]} {cns[7:11]} {cns[11:]}"
        return cns
    
    def format_phone(phone):
        """Formatar telefone: 11987654321 -> (11) 98765-4321"""
        if not phone:
            return ''
        phone = ''.join(filter(str.isdigit, str(phone)))
        if len(phone) == 11:
            return f"({phone[:2]}) {phone[2:7]}-{phone[7:]}"
        elif len(phone) == 10:
            return f"({phone[:2]}) {phone[2:6]}-{phone[6:]}"
        return phone
    
    def format_date(date_obj):
        """Formatar data: 2024-01-15 -> 15/01/2024"""
        if not date_obj:
            return ''
        try:
            if hasattr(date_obj, 'strftime'):
                return date_obj.strftime('%d/%m/%Y')
            return str(date_obj)
        except:
            return str(date_obj)
    
    def format_datetime(datetime_obj):
        """Formatar data e hora: 2024-01-15 14:30:00 -> 15/01/2024 14:30"""
        if not datetime_obj:
            return ''
        try:
            if hasattr(datetime_obj, 'strftime'):
                return datetime_obj.strftime('%d/%m/%Y %H:%M')
            return str(datetime_obj)
        except:
            return str(datetime_obj)
    
    def calculate_age(birth_date):
        """Calcular idade a partir da data de nascimento"""
        if not birth_date:
            return 0
        try:
            from datetime import date
            if isinstance(birth_date, str):
                from datetime import datetime
                birth_date = datetime.strptime(birth_date, '%Y-%m-%d').date()
            elif hasattr(birth_date, 'date'):
                birth_date = birth_date.date()
            
            today = date.today()
            age = today.year - birth_date.year
            if today.month < birth_date.month or (today.month == birth_date.month and today.day < birth_date.day):
                age -= 1
            return age
        except:
            return 0
    
    def format_gender(gender):
        """Formatar gênero para exibição"""
        gender_map = {
            'M': 'Masculino',
            'F': 'Feminino', 
            'O': 'Outro',
            'N': 'Não informar'
        }
        return gender_map.get(gender, 'Não informado')
    
    # Registrar como funções globais (para usar {{ format_cpf(value) }})
    app.jinja_env.globals.update(
        format_cpf=format_cpf,
        format_cns=format_cns,
        format_phone=format_phone,
        format_date=format_date,
        format_datetime=format_datetime,
        calculate_age=calculate_age,
        format_gender=format_gender
    )
    
    # Registrar também como filtros (para usar {{ value|format_cpf }})
    app.jinja_env.filters['format_cpf'] = format_cpf
    app.jinja_env.filters['format_cns'] = format_cns
    app.jinja_env.filters['format_phone'] = format_phone
    app.jinja_env.filters['format_date'] = format_date
    app.jinja_env.filters['format_datetime'] = format_datetime
    app.jinja_env.filters['calculate_age'] = calculate_age
    app.jinja_env.filters['format_gender'] = format_gender