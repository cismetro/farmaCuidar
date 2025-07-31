import os
import uuid
from datetime import datetime, date
from decimal import Decimal
from flask import current_app
from werkzeug.utils import secure_filename
import re
from PIL import Image
import json

# Utilitários para CPF e CNS
def validate_cpf(cpf):
    """Valida CPF brasileiro"""
    cpf = re.sub(r'[^0-9]', '', cpf)
    
    if len(cpf) != 11:
        return False
    
    # Verifica se todos os dígitos são iguais
    if cpf == cpf[0] * len(cpf):
        return False
    
    # Valida primeiro dígito verificador
    sum1 = sum(int(cpf[i]) * (10 - i) for i in range(9))
    digit1 = ((sum1 * 10) % 11) % 10
    
    if int(cpf[9]) != digit1:
        return False
    
    # Valida segundo dígito verificador
    sum2 = sum(int(cpf[i]) * (11 - i) for i in range(10))
    digit2 = ((sum2 * 10) % 11) % 10
    
    return int(cpf[10]) == digit2

def format_cpf(cpf):
    """Formata CPF para exibição"""
    cpf = re.sub(r'[^0-9]', '', cpf)
    if len(cpf) == 11:
        return f"{cpf[:3]}.{cpf[3:6]}.{cpf[6:9]}-{cpf[9:]}"
    return cpf

def validate_cns(cns):
    """Valida CNS (Cartão Nacional de Saúde)"""
    if not cns:
        return True  # CNS é opcional
    
    cns = re.sub(r'[^0-9]', '', cns)
    
    if len(cns) != 15:
        return False
    
    # Implementação básica - pode ser melhorada com algoritmo oficial
    return cns.isdigit()

def format_cns(cns):
    """Formata CNS para exibição"""
    if not cns:
        return ""
    cns = re.sub(r'[^0-9]', '', cns)
    if len(cns) == 15:
        return f"{cns[:3]} {cns[3:7]} {cns[7:11]} {cns[11:]}"
    return cns

# Utilitários para upload de arquivos
def allowed_file(filename, allowed_extensions=None):
    """Verifica se o arquivo tem extensão permitida"""
    if allowed_extensions is None:
        allowed_extensions = {'pdf', 'png', 'jpg', 'jpeg', 'gif', 'doc', 'docx'}
    
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in allowed_extensions

def generate_unique_filename(original_filename):
    """Gera nome único para arquivo"""
    ext = original_filename.rsplit('.', 1)[1].lower() if '.' in original_filename else ''
    unique_filename = f"{uuid.uuid4().hex}.{ext}"
    return unique_filename

def save_uploaded_file(file, subfolder='documents'):
    """Salva arquivo uploadado e retorna informações"""
    if file and allowed_file(file.filename):
        original_filename = secure_filename(file.filename)
        unique_filename = generate_unique_filename(original_filename)
        
        # Cria pasta se não existir
        upload_path = os.path.join(current_app.config['UPLOAD_FOLDER'], subfolder)
        if not os.path.exists(upload_path):
            os.makedirs(upload_path)
        
        file_path = os.path.join(upload_path, unique_filename)
        file.save(file_path)
        
        # Informações do arquivo
        file_info = {
            'original_filename': original_filename,
            'filename': unique_filename,
            'file_path': file_path,
            'file_size': os.path.getsize(file_path),
            'mime_type': file.content_type
        }
        
        return file_info
    
    return None

def resize_image(image_path, max_size=(800, 600)):
    """Redimensiona imagem se necessário"""
    try:
        with Image.open(image_path) as img:
            img.thumbnail(max_size, Image.Resampling.LANCZOS)
            img.save(image_path, optimize=True, quality=85)
        return True
    except Exception as e:
        print(f"Erro ao redimensionar imagem: {e}")
        return False

# Utilitários para formatação
def format_currency(value):
    """Formata valor monetário para BRL"""
    if value is None:
        return "R$ 0,00"
    
    if isinstance(value, str):
        try:
            value = Decimal(value)
        except:
            return "R$ 0,00"
    
    return f"R$ {value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

def format_date(date_obj, format_str="%d/%m/%Y"):
    """Formata data para exibição"""
    if date_obj is None:
        return ""
    
    if isinstance(date_obj, str):
        try:
            date_obj = datetime.strptime(date_obj, "%Y-%m-%d").date()
        except:
            return date_obj
    
    return date_obj.strftime(format_str)

def format_datetime(datetime_obj, format_str="%d/%m/%Y %H:%M"):
    """Formata datetime para exibição"""
    if datetime_obj is None:
        return ""
    
    if isinstance(datetime_obj, str):
        try:
            datetime_obj = datetime.fromisoformat(datetime_obj)
        except:
            return datetime_obj
    
    return datetime_obj.strftime(format_str)

def parse_date(date_string):
    """Converte string para objeto date"""
    if not date_string:
        return None
    
    try:
        # Tenta formato brasileiro dd/mm/yyyy
        if '/' in date_string:
            return datetime.strptime(date_string, "%d/%m/%Y").date()
        # Tenta formato ISO yyyy-mm-dd
        elif '-' in date_string:
            return datetime.strptime(date_string, "%Y-%m-%d").date()
    except ValueError:
        pass
    
    return None

# Utilitários para cálculos
def calculate_age(birth_date):
    """Calcula idade a partir da data de nascimento"""
    if not birth_date:
        return None
    
    if isinstance(birth_date, str):
        birth_date = parse_date(birth_date)
    
    if not birth_date:
        return None
    
    today = date.today()
    return today.year - birth_date.year - ((today.month, today.day) < (birth_date.month, birth_date.day))

def calculate_treatment_cost(medication_cost, quantity, duration_days=None):
    """Calcula custo total do tratamento"""
    if not medication_cost or not quantity:
        return Decimal('0.00')
    
    total_cost = Decimal(str(medication_cost)) * quantity
    
    if duration_days:
        # Se tem duração, pode calcular custo diário
        daily_cost = total_cost / duration_days
        return {
            'total_cost': total_cost,
            'daily_cost': daily_cost,
            'duration_days': duration_days
        }
    
    return total_cost

# Utilitários para relatórios
def generate_report_filename(report_type, extension='pdf'):
    """Gera nome de arquivo para relatório"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"relatorio_{report_type}_{timestamp}.{extension}"

def export_to_json(data, filename=None):
    """Exporta dados para JSON"""
    if filename is None:
        filename = generate_report_filename('dados', 'json')
    
    # Converte objetos não serializáveis
    def json_serializer(obj):
        if isinstance(obj, (datetime, date)):
            return obj.isoformat()
        if isinstance(obj, Decimal):
            return float(obj)
        raise TypeError(f"Object of type {type(obj)} is not JSON serializable")
    
    json_data = json.dumps(data, default=json_serializer, indent=2, ensure_ascii=False)
    return json_data, filename

# Utilitários para notificações
def generate_alert_message(alert_type, data):
    """Gera mensagem de alerta baseada no tipo"""
    messages = {
        'low_stock': f"Estoque baixo: {data.get('medication_name')} - Restam apenas {data.get('quantity')} unidades",
        'near_expiry': f"Medicamento próximo ao vencimento: {data.get('medication_name')} - Vence em {data.get('days')} dias",
        'expired': f"Medicamento vencido: {data.get('medication_name')} - Venceu em {data.get('expiry_date')}",
        'high_cost_pending': f"Processo alto custo pendente: {data.get('protocol')} - {data.get('patient_name')}",
        'approval_expired': f"Aprovação expirada: {data.get('protocol')} - Paciente: {data.get('patient_name')}"
    }
    
    return messages.get(alert_type, "Alerta do sistema")

# Utilitários para validação de formulários
def clean_numeric_input(value):
    """Limpa entrada numérica removendo caracteres não numéricos"""
    if not value:
        return None
    
    # Remove tudo exceto números e pontos/vírgulas
    clean_value = re.sub(r'[^\d.,]', '', str(value))
    
    # Converte vírgula para ponto (padrão brasileiro)
    clean_value = clean_value.replace(',', '.')
    
    try:
        return Decimal(clean_value)
    except:
        return None

def validate_medication_batch(batch_number):
    """Valida número de lote do medicamento"""
    if not batch_number:
        return False
    
    # Lote deve ter pelo menos 3 caracteres e máximo 20
    if len(batch_number) < 3 or len(batch_number) > 20:
        return False
    
    # Pode conter letras, números e hífens
    if not re.match(r'^[A-Za-z0-9\-]+$', batch_number):
        return False
    
    return True

# Utilitários para paginação
def paginate_query(query, page, per_page, error_out=False):
    """Aplica paginação a uma query"""
    return query.paginate(
        page=page,
        per_page=per_page,
        error_out=error_out
    )

# Constantes do sistema
MEDICATION_TYPES = {
    'basic': 'Básico',
    'controlled': 'Controlado',
    'high_cost': 'Alto Custo',
    'psychotropic': 'Psicotrópico'
}

PROCESS_STATUS = {
    'pending': 'Pendente',
    'under_evaluation': 'Em Avaliação',
    'approved': 'Aprovado',
    'denied': 'Negado',
    'dispensed': 'Dispensado',
    'completed': 'Concluído',
    'cancelled': 'Cancelado'
}

USER_ROLES = {
    'admin': 'Administrador',
    'pharmacist': 'Farmacêutico',
    'attendant': 'Atendente'
}

# Função para obter configurações do sistema
def get_system_stats():
    """Retorna estatísticas gerais do sistema"""
    from app.models import Patient, Medication, HighCostProcess, Dispensation
    
    try:
        stats = {
            'total_patients': Patient.query.filter_by(is_active=True).count(),
            'total_medications': Medication.query.filter_by(is_active=True).count(),
            'pending_high_cost': HighCostProcess.query.filter_by(status='pending').count(),
            'today_dispensations': Dispensation.query.filter(
                Dispensation.dispensation_date >= datetime.now().date()
            ).count()
        }
        return stats
    except Exception as e:
        print(f"Erro ao obter estatísticas: {e}")
        return {
            'total_patients': 0,
            'total_medications': 0,
            'pending_high_cost': 0,
            'today_dispensations': 0
        }