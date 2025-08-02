from flask import Flask, render_template
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
    
    # ✅ NOVO: Registrar error handlers
    register_error_handlers(app)
    
    return app

def register_error_handlers(app):
    """Registrar handlers para páginas de erro"""
    
    @app.errorhandler(400)
    def bad_request(error):
        """Requisição inválida"""
        return render_template('errors/400.html'), 400
    
    @app.errorhandler(401)
    def unauthorized(error):
        """Não autorizado"""
        return render_template('errors/401.html'), 401
    
    @app.errorhandler(403)
    def forbidden(error):
        """Acesso negado"""
        return render_template('errors/403.html'), 403
    
    @app.errorhandler(404)
    def not_found(error):
        """Página não encontrada"""
        return render_template('errors/404.html'), 404
    
    @app.errorhandler(500)
    def internal_error(error):
        """Erro interno do servidor"""
        try:
            from app.database import db
            db.session.rollback()
        except:
            pass
        return render_template('errors/500.html'), 500
    
    @app.errorhandler(502)
    def bad_gateway(error):
        """Bad Gateway"""
        return render_template('errors/502.html'), 502
    
    @app.errorhandler(503)
    def service_unavailable(error):
        """Serviço indisponível"""
        return render_template('errors/503.html'), 503

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
    
    def format_currency(value):
        """Formatar valor monetário: 1234.56 -> R$ 1.234,56"""
        if not value:
            return 'R$ 0,00'
        try:
            # Converter para float se for string
            if isinstance(value, str):
                value = float(value.replace(',', '.'))
            elif hasattr(value, '__float__'):
                value = float(value)
            
            # Formatar com separadores brasileiros
            formatted = f"R$ {value:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.')
            return formatted
        except:
            return f"R$ {value}"
    
    def format_percentage(value):
        """Formatar porcentagem: 0.15 -> 15%"""
        if value is None:
            return '0%'
        try:
            if isinstance(value, str):
                value = float(value)
            elif hasattr(value, '__float__'):
                value = float(value)
            
            return f"{value * 100:.1f}%"
        except:
            return f"{value}%"
    
    # Registrar como funções globais (para usar {{ format_cpf(value) }})
    app.jinja_env.globals.update(
        format_cpf=format_cpf,
        format_cns=format_cns,
        format_phone=format_phone,
        format_date=format_date,
        format_datetime=format_datetime,
        calculate_age=calculate_age,
        format_gender=format_gender,
        format_currency=format_currency,
        format_percentage=format_percentage
    )
    
    # Registrar também como filtros (para usar {{ value|format_cpf }})
    app.jinja_env.filters['format_cpf'] = format_cpf
    app.jinja_env.filters['format_cns'] = format_cns
    app.jinja_env.filters['format_phone'] = format_phone
    app.jinja_env.filters['format_date'] = format_date
    app.jinja_env.filters['format_datetime'] = format_datetime
    app.jinja_env.filters['calculate_age'] = calculate_age
    app.jinja_env.filters['format_gender'] = format_gender
    app.jinja_env.filters['format_currency'] = format_currency
    app.jinja_env.filters['format_percentage'] = format_percentage