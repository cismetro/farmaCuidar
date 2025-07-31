from functools import wraps
from flask import request, jsonify, redirect, url_for, flash, session
from flask_login import current_user, logout_user
from app.models import User, AuditLog, db
import json
from datetime import datetime

def login_required_role(*roles):
    """Decorator que exige login e verifica se o usuário tem uma das roles especificadas"""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not current_user.is_authenticated:
                flash('Você precisa fazer login para acessar esta página.', 'warning')
                return redirect(url_for('main.login'))
            
            if not current_user.is_active:
                flash('Sua conta está inativa. Entre em contato com o administrador.', 'error')
                logout_user()
                return redirect(url_for('main.login'))
            
            if roles and current_user.role.value not in roles:
                flash('Você não tem permissão para acessar esta página.', 'error')
                return redirect(url_for('main.dashboard'))
            
            return f(*args, **kwargs)
        return decorated_function
    return decorator

def admin_required(f):
    """Decorator que exige permissão de administrador"""
    return login_required_role('admin')(f)

def pharmacist_required(f):
    """Decorator que exige permissão de farmacêutico ou admin"""
    return login_required_role('admin', 'pharmacist')(f)

def staff_required(f):
    """Decorator que exige qualquer usuário logado da equipe"""
    return login_required_role('admin', 'pharmacist', 'attendant')(f)

def log_action(action, table_name, record_id=None, old_values=None, new_values=None):
    """Registra ação no log de auditoria"""
    try:
        audit_log = AuditLog(
            user_id=current_user.id if current_user.is_authenticated else None,
            action=action,
            table_name=table_name,
            record_id=record_id,
            old_values=json.dumps(old_values) if old_values else None,
            new_values=json.dumps(new_values) if new_values else None,
            ip_address=request.remote_addr,
            user_agent=request.headers.get('User-Agent', '')[:500]
        )
        db.session.add(audit_log)
        db.session.commit()
    except Exception as e:
        # Em caso de erro no log, não queremos quebrar a aplicação
        print(f"Erro ao registrar log de auditoria: {e}")

def update_last_login(user):
    """Atualiza último login do usuário"""
    try:
        user.last_login = datetime.utcnow()
        db.session.commit()
        log_action('LOGIN', 'users', user.id)
    except Exception as e:
        print(f"Erro ao atualizar último login: {e}")

def check_session_validity():
    """Verifica se a sessão do usuário ainda é válida"""
    if current_user.is_authenticated:
        if not current_user.is_active:
            logout_user()
            flash('Sua sessão expirou. Faça login novamente.', 'warning')
            return False
    return True

def get_user_permissions(user):
    """Retorna as permissões do usuário baseado em sua role"""
    if not user or not user.is_authenticated:
        return []
    
    base_permissions = ['view_own_profile', 'change_own_password']
    
    role_permissions = {
        'admin': [
            'manage_users', 'manage_system', 'view_all_reports', 
            'manage_inventory', 'dispense_medications', 'evaluate_high_cost',
            'approve_high_cost', 'view_audit_logs', 'backup_system'
        ],
        'pharmacist': [
            'manage_inventory', 'dispense_medications', 'evaluate_high_cost',
            'view_reports', 'manage_patients', 'view_prescriptions'
        ],
        'attendant': [
            'dispense_basic_medications', 'manage_patients', 
            'view_basic_reports', 'register_dispensations'
        ]
    }
    
    user_permissions = base_permissions + role_permissions.get(user.role.value, [])
    return user_permissions

def has_permission(permission):
    """Verifica se o usuário atual tem uma permissão específica"""
    if not current_user.is_authenticated:
        return False
    
    user_permissions = get_user_permissions(current_user)
    return permission in user_permissions

def permission_required(permission):
    """Decorator que verifica se o usuário tem uma permissão específica"""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not current_user.is_authenticated:
                flash('Você precisa fazer login para acessar esta página.', 'warning')
                return redirect(url_for('main.login'))
            
            if not has_permission(permission):
                flash('Você não tem permissão para realizar esta ação.', 'error')
                return redirect(url_for('main.dashboard'))
            
            return f(*args, **kwargs)
        return decorated_function
    return decorator

# Validações de senha
def validate_password(password):
    """Valida se a senha atende aos critérios de segurança"""
    errors = []
    
    if len(password) < 6:
        errors.append("A senha deve ter pelo menos 6 caracteres")
    
    if not any(c.isdigit() for c in password):
        errors.append("A senha deve conter pelo menos um número")
    
    if not any(c.isalpha() for c in password):
        errors.append("A senha deve conter pelo menos uma letra")
    
    return errors

def is_safe_url(target):
    """Verifica se uma URL é segura para redirect"""
    from urllib.parse import urlparse, urljoin
    from flask import request
    
    ref_url = urlparse(request.host_url)
    test_url = urlparse(urljoin(request.host_url, target))
    return test_url.scheme in ('http', 'https') and ref_url.netloc == test_url.netloc

def get_redirect_target():
    """Obtém URL de destino para redirect após login"""
    for target in request.values.get('next'), request.referrer:
        if not target:
            continue
        if is_safe_url(target):
            return target

# Context processor para templates
def inject_user_permissions():
    """Injeta permissões do usuário nos templates"""
    if current_user.is_authenticated:
        return {
            'user_permissions': get_user_permissions(current_user),
            'has_permission': has_permission
        }
    return {}

# Configurações de segurança para headers HTTP
def add_security_headers(response):
    """Adiciona headers de segurança às respostas"""
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
    return response

# Funções auxiliares para templates
def format_user_role(role):
    """Formata role do usuário para exibição"""
    role_names = {
        'admin': 'Administrador',
        'pharmacist': 'Farmacêutico',
        'attendant': 'Atendente'
    }
    return role_names.get(role.value if hasattr(role, 'value') else role, 'Desconhecido')

def get_user_dashboard_url(user):
    """Retorna URL do dashboard apropriado para o usuário"""
    if user.role.value == 'admin':
        return url_for('main.admin_dashboard')
    elif user.role.value == 'pharmacist':
        return url_for('main.pharmacist_dashboard')
    else:
        return url_for('main.attendant_dashboard')

# Rate limiting simples
class RateLimiter:
    def __init__(self):
        self.attempts = {}
    
    def is_allowed(self, key, max_attempts=5, window_minutes=15):
        """Verifica se uma tentativa é permitida"""
        import time
        
        now = time.time()
        window_start = now - (window_minutes * 60)
        
        if key not in self.attempts:
            self.attempts[key] = []
        
        # Remove tentativas antigas
        self.attempts[key] = [t for t in self.attempts[key] if t > window_start]
        
        # Verifica se excedeu o limite
        if len(self.attempts[key]) >= max_attempts:
            return False
        
        # Registra nova tentativa
        self.attempts[key].append(now)
        return True
    
    def reset(self, key):
        """Reset contador para uma chave"""
        if key in self.attempts:
            del self.attempts[key]

# Instância global do rate limiter
rate_limiter = RateLimiter()

def check_login_attempts(ip_address):
    """Verifica tentativas de login por IP"""
    return rate_limiter.is_allowed(f"login_{ip_address}", max_attempts=5, window_minutes=15)

def reset_login_attempts(ip_address):
    """Reset tentativas de login após login bem-sucedido"""
    rate_limiter.reset(f"login_{ip_address}")

# Funções para trabalhar com sessões
def clear_user_session():
    """Limpa dados específicos do usuário da sessão"""
    session_keys_to_clear = [
        'user_preferences', 'cart_items', 'selected_patient', 
        'dispensation_data', 'high_cost_form_data'
    ]
    
    for key in session_keys_to_clear:
        session.pop(key, None)

def init_user_session(user):
    """Inicializa sessão do usuário com dados padrão"""
    session['user_role'] = user.role.value
    session['user_full_name'] = user.full_name
    session['login_time'] = datetime.utcnow().isoformat()

# Middleware para verificar sessão
def check_user_session():
    """Middleware para verificar validade da sessão"""
    if current_user.is_authenticated:
        # Verifica se usuário ainda está ativo
        if not current_user.is_active:
            clear_user_session()
            logout_user()
            flash('Sua conta foi desativada. Entre em contato com o administrador.', 'error')
            return redirect(url_for('main.login'))
    
    return None