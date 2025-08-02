from app.database import db
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from sqlalchemy import desc, and_
import enum
import logging
import math

# ✅ IMPORTAÇÕES PARA INTEGRAÇÃO E-SUS
try:
    from app.esus_integration import (
        search_patient_in_esus, 
        clean_cpf, clean_cns, clean_cep, clean_phone, 
        map_gender_from_esus, format_esus_data_for_display
    )
except ImportError:
    # Fallback caso módulo e-SUS não esteja disponível
    def search_patient_in_esus(query, search_type='all'):
        return []
    def clean_cpf(cpf):
        return ''.join(filter(str.isdigit, str(cpf))) if cpf else None
    def clean_cns(cns):
        return ''.join(filter(str.isdigit, str(cns))) if cns else None
    def clean_cep(cep):
        return ''.join(filter(str.isdigit, str(cep))) if cep else None
    def clean_phone(phone):
        return ''.join(filter(str.isdigit, str(phone))) if phone else None
    def map_gender_from_esus(gender):
        return gender
    def format_esus_data_for_display(data):
        return data

# Enums para status e tipos
class UserRole(enum.Enum):
    ADMIN = 'admin'
    PHARMACIST = 'pharmacist'
    ATTENDANT = 'attendant'

class MedicationType(enum.Enum):
    BASIC = 'basic'
    CONTROLLED = 'controlled'
    HIGH_COST = 'high_cost'
    PSYCHOTROPIC = 'psychotropic'

class ProcessStatus(enum.Enum):
    PENDING = 'pending'
    UNDER_EVALUATION = 'under_evaluation'
    APPROVED = 'approved'
    DENIED = 'denied'
    DISPENSED = 'dispensed'
    COMPLETED = 'completed'
    CANCELLED = 'cancelled'

class DispensationStatus(enum.Enum):
    PENDING = 'pending'
    COMPLETED = 'completed'
    CANCELLED = 'cancelled'

# =================== MODELOS DE CONTROLE DE INTERVALOS ===================

class MedicationInterval(db.Model):
    """Controle de intervalos entre dispensações por medicamento"""
    __tablename__ = 'medication_intervals'
    
    id = db.Column(db.Integer, primary_key=True)
    medication_id = db.Column(db.Integer, db.ForeignKey('medications.id'), nullable=False, unique=True)
    
    # Configuração do intervalo
    interval_days = db.Column(db.Integer, nullable=False, default=30)
    interval_type = db.Column(db.Enum('predefined', 'manual', name='interval_type_enum'), default='predefined')
    
    # Controle ativo/inativo
    is_active = db.Column(db.Boolean, default=True)
    requires_justification = db.Column(db.Boolean, default=True)  # Para liberação antecipada
    
    # Metadados
    created_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relacionamentos
    medication = db.relationship('Medication', back_populates='interval_control')
    creator = db.relationship('User', foreign_keys=[created_by])
    
    def __repr__(self):
        return f'<MedicationInterval {self.medication.commercial_name if self.medication else "Unknown"}: {self.interval_days} dias>'

class DispensationControl(db.Model):
    """Controle individual de dispensações com intervalos"""
    __tablename__ = 'dispensation_controls'
    
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('patients.id'), nullable=False)
    medication_id = db.Column(db.Integer, db.ForeignKey('medications.id'), nullable=False)
    dispensation_item_id = db.Column(db.Integer, db.ForeignKey('dispensation_items.id'), nullable=False)
    
    # Controle de datas
    last_dispensation_date = db.Column(db.Date, nullable=False)
    next_allowed_date = db.Column(db.Date, nullable=False)
    interval_days_used = db.Column(db.Integer, nullable=False)
    
    # Status
    is_active = db.Column(db.Boolean, default=True)
    was_released_early = db.Column(db.Boolean, default=False)
    
    # Metadados
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relacionamentos
    patient = db.relationship('Patient', backref='dispensation_controls')
    medication = db.relationship('Medication', backref='dispensation_controls')
    dispensation_item = db.relationship('DispensationItem', backref='control_record')
    early_releases = db.relationship('EarlyReleaseLog', backref='dispensation_control', lazy=True, cascade='all, delete-orphan')
    
    @property
    def days_until_next_allowed(self):
        """Dias restantes até próxima dispensação"""
        if not self.next_allowed_date:
            return 0
        delta = self.next_allowed_date - date.today()
        return max(0, delta.days)
    
    @property
    def can_dispense_today(self):
        """Verifica se pode dispensar hoje"""
        return date.today() >= self.next_allowed_date
    
    @property
    def is_overdue(self):
        """Verifica se passou do prazo (para controle de renovação)"""
        if not self.next_allowed_date:
            return False
        return date.today() > self.next_allowed_date + timedelta(days=30)  # 30 dias de tolerância
    
    @property
    def status_display(self):
        """Status formatado para exibição"""
        if self.can_dispense_today:
            if self.is_overdue:
                return 'Vencido'
            return 'Liberado'
        else:
            return f'Bloqueado ({self.days_until_next_allowed} dias)'
    
    # ✅ MÉTODO PARA SERIALIZAÇÃO JSON
    def to_dict(self):
        """Converter para dicionário serializável"""
        return {
            'id': self.id,
            'patient_id': self.patient_id,
            'patient_name': self.patient.full_name if self.patient else None,
            'medication_id': self.medication_id,
            'medication_name': self.medication.commercial_name if self.medication else None,
            'dispensation_item_id': self.dispensation_item_id,
            'last_dispensation_date': self.last_dispensation_date.strftime('%d/%m/%Y') if self.last_dispensation_date else None,
            'next_allowed_date': self.next_allowed_date.strftime('%d/%m/%Y') if self.next_allowed_date else None,
            'interval_days_used': self.interval_days_used,
            'days_until_next_allowed': self.days_until_next_allowed,
            'can_dispense_today': self.can_dispense_today,
            'is_overdue': self.is_overdue,
            'status_display': self.status_display,
            'is_active': self.is_active,
            'was_released_early': self.was_released_early,
            'created_at': self.created_at.strftime('%d/%m/%Y %H:%M') if self.created_at else None,
            # ✅ INFORMAÇÕES ADICIONAIS ÚTEIS
            'early_releases_count': len(self.early_releases) if self.early_releases else 0,
            'has_early_releases': bool(self.early_releases),
            'dispensation_item_quantity': self.dispensation_item.quantity_dispensed if self.dispensation_item else None,
            'dispensation_item_cost': float(self.dispensation_item.total_cost) if self.dispensation_item and self.dispensation_item.total_cost else 0.0
        }
    
    # ✅ MÉTODO PARA SERIALIZAÇÃO SIMPLIFICADA (APENAS CAMPOS ESSENCIAIS)
    def to_dict_simple(self):
        """Converter para dicionário com campos essenciais apenas"""
        return {
            'id': self.id,
            'medication_name': self.medication.commercial_name if self.medication else 'N/A',
            'last_dispensation_date': self.last_dispensation_date.strftime('%d/%m/%Y') if self.last_dispensation_date else 'N/A',
            'next_allowed_date': self.next_allowed_date.strftime('%d/%m/%Y') if self.next_allowed_date else 'N/A',
            'interval_days': self.interval_days_used,
            'days_until_next_allowed': self.days_until_next_allowed,
            'can_dispense_today': self.can_dispense_today,
            'status_display': self.status_display
        }
    
    # ✅ MÉTODO ESTÁTICO PARA CONVERTER LISTA DE CONTROLES
    @staticmethod
    def list_to_dict(controls, simple=False):
        """Converter lista de controles para lista de dicionários"""
        if simple:
            return [control.to_dict_simple() for control in controls]
        return [control.to_dict() for control in controls]
    
    # ✅ MÉTODO PARA VERIFICAR SE PODE SER LIBERADO ANTECIPADAMENTE
    def can_be_released_early(self):
        """Verifica se o controle pode ser liberado antecipadamente"""
        if self.can_dispense_today:
            return False  # Já pode dispensar, não precisa liberação
        
        if not self.is_active:
            return False  # Controle inativo
        
        # Verificar se não foi liberado recentemente (últimas 24h)
        if self.early_releases:
            last_release = max(self.early_releases, key=lambda x: x.authorized_at)
            if (datetime.utcnow() - last_release.authorized_at).days < 1:
                return False  # Liberado há menos de 24h
        
        return True
    
    # ✅ MÉTODO PARA CALCULAR PRÓXIMA DATA BASEADA EM NOVO INTERVALO
    def calculate_next_date_with_interval(self, new_interval_days):
        """Calcular próxima data com novo intervalo"""
        return self.last_dispensation_date + timedelta(days=new_interval_days)
    
    # ✅ MÉTODO PARA HISTÓRICO DE LIBERAÇÕES ANTECIPADAS
    def get_early_releases_summary(self):
        """Obter resumo das liberações antecipadas"""
        if not self.early_releases:
            return {
                'total_releases': 0,
                'last_release_date': None,
                'total_days_saved': 0
            }
        
        total_days_saved = sum(release.days_early for release in self.early_releases)
        last_release = max(self.early_releases, key=lambda x: x.authorized_at)
        
        return {
            'total_releases': len(self.early_releases),
            'last_release_date': last_release.authorized_at.strftime('%d/%m/%Y %H:%M'),
            'last_release_justification': last_release.justification,
            'total_days_saved': total_days_saved,
            'average_days_early': total_days_saved / len(self.early_releases) if self.early_releases else 0
        }
    
    # ✅ MÉTODO PARA ATUALIZAR CONTROLE COM NOVA DISPENSAÇÃO
    def update_for_new_dispensation(self, new_interval_days=None, force_release=False):
        """Atualizar controle para nova dispensação"""
        # Usar intervalo atual se não especificado
        interval_days = new_interval_days or self.interval_days_used
        
        # Atualizar datas
        self.last_dispensation_date = date.today()
        self.next_allowed_date = date.today() + timedelta(days=interval_days)
        
        # Atualizar intervalo se especificado
        if new_interval_days:
            self.interval_days_used = new_interval_days
        
        # Marcar se foi liberação antecipada
        self.was_released_early = force_release
        
        return self
    
    # ✅ MÉTODO PARA DESATIVAR CONTROLE
    def deactivate(self, reason=None):
        """Desativar controle"""
        self.is_active = False
        # Aqui poderia adicionar um campo 'deactivation_reason' se necessário
        return self
    
    # ✅ MÉTODO PARA VERIFICAR VALIDADE DO CONTROLE
    def is_valid(self):
        """Verificar se o controle está válido"""
        if not self.is_active:
            return False
        
        if not self.patient or not self.medication:
            return False
        
        if not self.last_dispensation_date or not self.next_allowed_date:
            return False
        
        if self.interval_days_used <= 0:
            return False
        
        return True
    
    def __repr__(self):
        return f'<DispensationControl {self.patient.full_name if self.patient else "Unknown"}: {self.medication.commercial_name if self.medication else "Unknown"}>'
    
class EarlyReleaseLog(db.Model):
    """Log de liberações antecipadas"""
    __tablename__ = 'early_release_logs'
    
    id = db.Column(db.Integer, primary_key=True)
    dispensation_control_id = db.Column(db.Integer, db.ForeignKey('dispensation_controls.id'), nullable=False)
    
    # Dados da liberação
    authorized_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    justification = db.Column(db.Text, nullable=False)
    days_early = db.Column(db.Integer, nullable=False)  # Quantos dias antes foi liberado
    
    # Datas
    original_date = db.Column(db.Date, nullable=False)  # Data original permitida
    released_date = db.Column(db.Date, nullable=False)  # Data em que foi liberado
    authorized_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relacionamentos
    authorizer = db.relationship('User', foreign_keys=[authorized_by])
    
    @property
    def formatted_justification(self):
        """Justificativa limitada para exibição"""
        if len(self.justification) > 100:
            return self.justification[:100] + '...'
        return self.justification
    
    def __repr__(self):
        return f'<EarlyReleaseLog {self.days_early} dias antecipado>'

# =================== MODELO DE CÁLCULOS FARMACOLÓGICOS ===================

class MedicationDispensing(db.Model):
    """Configuração simples para cálculos de dispensação"""
    __tablename__ = 'medication_dispensing'
    
    id = db.Column(db.Integer, primary_key=True)
    medication_id = db.Column(db.Integer, db.ForeignKey('medications.id'), nullable=False, unique=True)
    
    # ✅ CONCENTRAÇÃO/DOSAGEM (o que importa para cálculo)
    strength_value = db.Column(db.DECIMAL(10, 3), nullable=False)  # Ex: 250 (mg)
    strength_unit = db.Column(db.String(10), nullable=False)       # Ex: "mg"
    
    # ✅ VOLUME/QUANTIDADE POR UNIDADE
    volume_per_dose = db.Column(db.DECIMAL(10, 3), nullable=False) # Ex: 5 (ml) ou 1 (comprimido)
    volume_unit = db.Column(db.String(10), nullable=False)         # Ex: "ml" ou "comp"
    
    # ✅ EMBALAGEM
    package_size = db.Column(db.DECIMAL(10, 3), nullable=False)    # Ex: 150 (ml) ou 30 (comp)
    package_unit = db.Column(db.String(10), nullable=False)        # Ex: "ml" ou "comp"
    
    # ✅ PARA GOTAS (se aplicável)
    drops_per_ml = db.Column(db.Integer, nullable=True, default=20) # Padrão: 20 gotas/ml
    
    # ✅ ESTABILIDADE (se aplicável)
    stability_days = db.Column(db.Integer, nullable=True)          # Dias após abrir/reconstituir
    
    # Metadados
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relacionamentos
    medication = db.relationship('Medication', back_populates='dispensing_config')
    
    # ✅ MÉTODO PRINCIPAL DE CÁLCULO
    def calculate_dispensation(self, prescribed_dose, prescribed_unit, frequency_per_day, treatment_days):
        """
        Cálculo simples de dispensação
        
        Args:
            prescribed_dose: Dose prescrita (ex: 500)
            prescribed_unit: Unidade prescrita (ex: "mg")
            frequency_per_day: Frequência por dia (ex: 3 para 8/8h)
            treatment_days: Dias de tratamento (ex: 7)
            
        Returns:
            dict: Resultado com volume por dose e embalagens necessárias
        """
        try:
            # Converter tudo para Decimal para precisão
            dose = Decimal(str(prescribed_dose))
            freq = Decimal(str(frequency_per_day))
            days = Decimal(str(treatment_days))
            
            # ✅ 1. CALCULAR VOLUME/QUANTIDADE POR DOSE
            # Regra de 3: strength_value está para volume_per_dose assim como dose está para x
            dose_volume = (dose * self.volume_per_dose) / self.strength_value
            
            # ✅ 2. CALCULAR TOTAL PARA O TRATAMENTO
            total_doses = freq * days
            total_volume = dose_volume * total_doses
            
            # ✅ 3. CALCULAR EMBALAGENS NECESSÁRIAS
            packages_needed = math.ceil(float(total_volume) / float(self.package_size))
            
            # ✅ 4. VERIFICAR ESTABILIDADE (se aplicável)
            packages_adjusted = packages_needed
            stability_note = None
            
            if self.stability_days and self.stability_days < treatment_days:
                # Precisa de mais embalagens devido à estabilidade
                packages_per_period = math.ceil((self.stability_days * freq * dose_volume) / self.package_size)
                periods_needed = math.ceil(treatment_days / self.stability_days)
                packages_adjusted = packages_per_period * periods_needed
                
                stability_note = f"Medicamento estável por {self.stability_days} dias após aberto. Dispensar {packages_adjusted} embalagens."
            
            # ✅ 5. CALCULAR GOTAS (se aplicável)
            drops_info = None
            if self.drops_per_ml and self.volume_unit == 'ml':
                drops_per_dose = float(dose_volume) * self.drops_per_ml
                drops_info = {
                    'drops_per_dose': int(drops_per_dose),
                    'ml_per_dose': float(dose_volume),
                    'conversion': f"{self._round_decimal(dose_volume, 2)}ml × {self.drops_per_ml} gotas/ml = {int(drops_per_dose)} gotas"
                }
            
            return {
                'success': True,
                'dose_per_administration': self._round_decimal(dose_volume, 2),
                'dose_unit': self.volume_unit,
                'total_doses': int(total_doses),
                'total_volume': self._round_decimal(total_volume, 2),
                'packages_needed': packages_adjusted,
                'package_size': float(self.package_size),
                'stability_note': stability_note,
                'drops_info': drops_info,
                'calculation_details': {
                    'concentration': f"{self.strength_value}{self.strength_unit}/{self.volume_per_dose}{self.volume_unit}",
                    'formula': f"{dose}{prescribed_unit} × {self.volume_per_dose}{self.volume_unit} ÷ {self.strength_value}{self.strength_unit} = {self._round_decimal(dose_volume, 2)}{self.volume_unit}",
                    'frequency': f"{frequency_per_day}x/dia × {treatment_days} dias = {int(total_doses)} doses",
                    'total_calculation': f"{self._round_decimal(dose_volume, 2)}{self.volume_unit} × {int(total_doses)} doses = {self._round_decimal(total_volume, 2)}{self.volume_unit}",
                    'packages_calculation': f"{self._round_decimal(total_volume, 2)}{self.volume_unit} ÷ {self.package_size}{self.package_unit} = {packages_needed} embalagens"
                }
            }
            
        except Exception as e:
            return {
                'success': False,
                'error': f'Erro no cálculo: {str(e)}'
            }
    
    # ✅ MÉTODO PARA CONVERTER GOTAS
    def calculate_drops_per_dose(self, dose_ml):
        """Converter ml para gotas se necessário"""
        if self.drops_per_ml and self.volume_unit == 'ml':
            drops = Decimal(str(dose_ml)) * Decimal(str(self.drops_per_ml))
            return {
                'drops': int(drops),
                'ml': float(dose_ml),
                'conversion': f"{dose_ml}ml × {self.drops_per_ml} gotas/ml = {int(drops)} gotas"
            }
        return None
    
    def _round_decimal(self, value, places):
        """Arredondar Decimal com precisão"""
        if isinstance(value, Decimal):
            return float(value.quantize(Decimal('0.' + '0' * places), rounding=ROUND_HALF_UP))
        return round(float(value), places)
    
    def to_dict(self):
        return {
            'id': self.id,
            'medication_id': self.medication_id,
            'concentration': f"{self.strength_value}{self.strength_unit}/{self.volume_per_dose}{self.volume_unit}",
            'package_size': f"{self.package_size}{self.package_unit}",
            'stability_days': self.stability_days,
            'drops_per_ml': self.drops_per_ml,
            'is_active': self.is_active
        }
    
    def __repr__(self):
        return f'<MedicationDispensing {self.medication.commercial_name if self.medication else "Unknown"}: {self.strength_value}{self.strength_unit}/{self.volume_per_dose}{self.volume_unit}>'

# =================== MODELOS PRINCIPAIS ===================

# Modelo de Usuários
class User(UserMixin, db.Model):
    __tablename__ = 'users'
    
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    full_name = db.Column(db.String(100), nullable=False)
    role = db.Column(db.Enum(UserRole), nullable=False, default=UserRole.ATTENDANT)
    crf = db.Column(db.String(20), nullable=True)  # CRF para farmacêuticos
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_login = db.Column(db.DateTime)
    
    # Relacionamentos
    dispensations = db.relationship('Dispensation', backref='dispenser', lazy=True)
    evaluations = db.relationship('PharmaceuticalEvaluation', backref='evaluator', lazy=True)
    approvals = db.relationship('ProcessApproval', backref='approver', lazy=True)
    audit_logs = db.relationship('AuditLog', backref='user', lazy=True)
    
    def set_password(self, password):
        self.password_hash = generate_password_hash(password)
    
    def check_password(self, password):
        return check_password_hash(self.password_hash, password)
    
    def __repr__(self):
        return f'<User {self.username}>'

# ✅ MODELO DE PACIENTES COM INTEGRAÇÃO E-SUS COMPLETA
class Patient(db.Model):
    __tablename__ = 'patients'
    
    id = db.Column(db.Integer, primary_key=True)
    cpf = db.Column(db.String(11), unique=True, nullable=False, index=True)
    cns = db.Column(db.String(15), unique=True, nullable=True, index=True)  # Cartão Nacional de Saúde
    full_name = db.Column(db.String(100), nullable=False)
    birth_date = db.Column(db.Date, nullable=False)
    gender = db.Column(db.Enum('M', 'F', 'O', 'N', name='gender_enum'), nullable=True)
    
    # ✅ NOVOS CAMPOS PARA INTEGRAÇÃO E-SUS
    mother_name = db.Column(db.String(100), nullable=True)  # no_mae
    father_name = db.Column(db.String(100), nullable=True)  # no_pai
    
    # Telefones específicos do e-SUS
    home_phone = db.Column(db.String(15), nullable=True)     # nu_telefone_residencial
    cell_phone = db.Column(db.String(15), nullable=True)     # nu_telefone_celular
    contact_phone = db.Column(db.String(15), nullable=True)  # nu_telefone_contato
    
    # Campo phone mantido para compatibilidade (será preenchido com cell_phone ou contact_phone)
    phone = db.Column(db.String(15), nullable=True)
    email = db.Column(db.String(120), nullable=True)
    
    # Endereço
    address = db.Column(db.String(200), nullable=True)      # ds_logradouro
    number = db.Column(db.String(10), nullable=True)        # nu_numero
    neighborhood = db.Column(db.String(100), nullable=True) # no_bairro
    city = db.Column(db.String(100), nullable=False, default='Cosmópolis')
    state = db.Column(db.String(2), nullable=False, default='SP')
    zip_code = db.Column(db.String(8), nullable=True)       # ds_cep
    
    # ✅ CONTROLE DE ORIGEM DOS DADOS
    source = db.Column(db.Enum('local', 'esus', 'imported', name='source_enum'), default='local')
    esus_sync_date = db.Column(db.DateTime, nullable=True)  # Data da última sincronização com e-SUS
    
    # Metadados
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relacionamentos
    prescriptions = db.relationship('Prescription', backref='patient', lazy=True)
    dispensations = db.relationship('Dispensation', backref='patient', lazy=True)
    high_cost_processes = db.relationship('HighCostProcess', backref='patient', lazy=True)
    tracking_records = db.relationship('PatientTracking', backref='patient', lazy=True)
    
    @property
    def age(self):
        """Calcular idade do paciente"""
        today = date.today()
        return today.year - self.birth_date.year - ((today.month, today.day) < (self.birth_date.month, self.birth_date.day))
    
    @property
    def primary_phone(self):
        """Retorna o telefone principal (celular > contato > residencial > phone)"""
        return self.cell_phone or self.contact_phone or self.home_phone or self.phone
    
    @property
    def full_address(self):
        """Endereço completo formatado"""
        parts = []
        
        if self.address:
            address_part = self.address
            if self.number:
                address_part += f", {self.number}"
            parts.append(address_part)
        
        if self.neighborhood:
            parts.append(self.neighborhood)
        
        if self.city:
            city_part = self.city
            if self.state:
                city_part += f" - {self.state}"
            parts.append(city_part)
        
        if self.zip_code:
            # Formatar CEP
            formatted_cep = f"{self.zip_code[:5]}-{self.zip_code[5:]}" if len(self.zip_code) == 8 else self.zip_code
            parts.append(f"CEP: {formatted_cep}")
        
        return ", ".join(parts) if parts else "Endereço não informado"
    
    @property
    def formatted_cpf(self):
        """CPF formatado"""
        if self.cpf and len(self.cpf) == 11:
            return f"{self.cpf[:3]}.{self.cpf[3:6]}.{self.cpf[6:9]}-{self.cpf[9:]}"
        return self.cpf
    
    @property
    def formatted_cns(self):
        """CNS formatado"""
        if self.cns and len(self.cns) == 15:
            return f"{self.cns[:3]} {self.cns[3:7]} {self.cns[7:11]} {self.cns[11:]}"
        return self.cns
    
    @property
    def formatted_phone(self):
        """Telefone principal formatado"""
        phone = self.primary_phone
        return self._format_phone_field(phone)
    
    # ✅ NOVAS PROPRIEDADES FORMATADAS PARA CADA TIPO DE TELEFONE
    @property
    def formatted_cell_phone(self):
        """Telefone celular formatado"""
        return self._format_phone_field(self.cell_phone)
    
    @property
    def formatted_home_phone(self):
        """Telefone residencial formatado"""
        return self._format_phone_field(self.home_phone)
    
    @property
    def formatted_contact_phone(self):
        """Telefone de contato formatado"""
        return self._format_phone_field(self.contact_phone)
    
    def _format_phone_field(self, phone):
        """Método auxiliar para formatar qualquer telefone"""
        if not phone:
            return None
        
        # Remover formatação existente e manter apenas dígitos
        clean_phone_str = ''.join(filter(str.isdigit, phone))
        
        if len(clean_phone_str) == 11:
            # Celular: (11) 99999-9999
            return f"({clean_phone_str[:2]}) {clean_phone_str[2:7]}-{clean_phone_str[7:]}"
        elif len(clean_phone_str) == 10:
            # Fixo: (11) 9999-9999
            return f"({clean_phone_str[:2]}) {clean_phone_str[2:6]}-{clean_phone_str[6:]}"
        else:
            # Retornar como está se não conseguir formatar
            return phone
    
    @property
    def gender_display(self):
        """Exibição do gênero"""
        gender_map = {
            'M': 'Masculino',
            'F': 'Feminino',
            'O': 'Outro',
            'N': 'Não informar'
        }
        return gender_map.get(self.gender, 'Não informado')
    
    @property
    def source_display(self):
        """Exibição da origem dos dados"""
        source_map = {
            'local': 'Cadastro Local',
            'esus': 'e-SUS',
            'imported': 'Importado do e-SUS'
        }
        return source_map.get(self.source, 'Desconhecido')
    
    # ✅ MÉTODOS DE CONTROLE DE INTERVALOS
    def get_active_medication_controls(self):
        """Obter controles ativos de medicamentos para o paciente"""
        return DispensationControl.query.filter_by(
            patient_id=self.id,
            is_active=True
        ).options(
            db.joinedload(DispensationControl.medication),
            db.joinedload(DispensationControl.early_releases)
        ).order_by(DispensationControl.next_allowed_date).all()
    
    def get_blocked_medications(self):
        """Obter medicamentos bloqueados para o paciente"""
        controls = self.get_active_medication_controls()
        return [control for control in controls if not control.can_dispense_today]
    
    def get_released_medications(self):
        """Obter medicamentos liberados para o paciente"""
        controls = self.get_active_medication_controls()
        return [control for control in controls if control.can_dispense_today]
    
    # ✅ MÉTODOS DE BUSCA INTEGRADA (LOCAL + E-SUS)
    @classmethod
    def search_integrated(cls, query, search_type='all'):
        """
        Busca integrada: primeiro local, depois e-SUS
        
        Args:
            query: termo de busca
            search_type: 'cpf', 'cns', 'name', 'birth_date' ou 'all'
        
        Returns:
            tuple: (local_results, esus_results)
        """
        local_results = []
        esus_results = []
        
        if not query or not query.strip():
            return local_results, esus_results
        
        # 1. Buscar no banco local primeiro
        local_results = cls._search_local(query, search_type)
        
        # 2. Se não encontrou no local, buscar no e-SUS
        if not local_results:
            try:
                raw_esus_results = search_patient_in_esus(query, search_type)
                esus_results = [format_esus_data_for_display(data) for data in raw_esus_results]
                logging.info(f"Busca e-SUS encontrou {len(esus_results)} resultados")
            except Exception as e:
                logging.error(f"Erro na busca e-SUS: {e}")
                esus_results = []
        
        return local_results, esus_results
    
    @classmethod
    def _search_local(cls, query, search_type='all'):
        """Buscar pacientes no banco local"""
        try:
            # Limpar query
            clean_query = ''.join(filter(str.isalnum, query.lower()))
            
            conditions = []
            
            if search_type in ['all', 'name']:
                conditions.append(cls.full_name.like(f'%{query}%'))
            
            if search_type in ['all', 'cpf']:
                conditions.append(cls.cpf.like(f'%{clean_query}%'))
            
            if search_type in ['all', 'cns'] and clean_query:
                conditions.append(cls.cns.like(f'%{clean_query}%'))
            
            if search_type in ['all', 'birth_date']:
                # Tentar parsear data em diferentes formatos
                try:
                    # Formato DD/MM/YYYY
                    if '/' in query:
                        birth_date = datetime.strptime(query, '%d/%m/%Y').date()
                        conditions.append(cls.birth_date == birth_date)
                    # Formato YYYY-MM-DD
                    elif '-' in query and len(query) == 10:
                        birth_date = datetime.strptime(query, '%Y-%m-%d').date()
                        conditions.append(cls.birth_date == birth_date)
                except ValueError:
                    pass
            
            if conditions:
                return cls.query.filter(db.or_(*conditions)).all()
            
        except Exception as e:
            logging.error(f"Erro na busca local: {e}")
        
        return []
    
    @classmethod
    def import_from_esus(cls, esus_data):
        """
        Importar paciente do e-SUS para o banco local
        
        Args:
            esus_data: dict com dados do e-SUS
            
        Returns:
            Patient: instância do paciente importado
        """
        try:
            # Mapear campos do e-SUS para modelo local
            patient_data = {
                'cpf': clean_cpf(esus_data.get('nu_cpf')),
                'cns': clean_cns(esus_data.get('nu_cns')),
                'full_name': esus_data.get('no_cidadao', '').strip(),
                'birth_date': esus_data.get('dt_nascimento'),
                'mother_name': esus_data.get('no_mae', '').strip() or None,
                'father_name': esus_data.get('no_pai', '').strip() or None,
                'gender': map_gender_from_esus(esus_data.get('no_sexo')),
                'address': esus_data.get('ds_logradouro', '').strip() or None,
                'number': esus_data.get('nu_numero', '').strip() or None,
                'neighborhood': esus_data.get('no_bairro', '').strip() or None,
                'zip_code': clean_cep(esus_data.get('ds_cep')),
                'home_phone': clean_phone(esus_data.get('nu_telefone_residencial')),
                'cell_phone': clean_phone(esus_data.get('nu_telefone_celular')),
                'contact_phone': clean_phone(esus_data.get('nu_telefone_contato')),
                'source': 'imported',
                'esus_sync_date': datetime.utcnow()
            }
            
            # Definir telefone principal para compatibilidade
            patient_data['phone'] = (
                patient_data['cell_phone'] or 
                patient_data['contact_phone'] or 
                patient_data['home_phone']
            )
            
            # Verificar se já existe no banco local
            existing = cls.query.filter_by(cpf=patient_data['cpf']).first()
            if existing:
                # Atualizar dados existentes
                for key, value in patient_data.items():
                    if value is not None:  # Só atualizar campos não nulos
                        setattr(existing, key, value)
                existing.updated_at = datetime.utcnow()
                db.session.commit()
                logging.info(f"Paciente atualizado: {existing.full_name}")
                return existing
            else:
                # Criar novo paciente
                patient = cls(**patient_data)
                db.session.add(patient)
                db.session.commit()
                logging.info(f"Paciente importado: {patient.full_name}")
                return patient
                
        except Exception as e:
            logging.error(f"Erro ao importar paciente do e-SUS: {e}")
            db.session.rollback()
            raise
    
    def to_dict(self):
        """Converter para dicionário"""
        return {
            'id': self.id,
            'cpf': self.formatted_cpf,
            'cns': self.formatted_cns,
            'full_name': self.full_name,
            'birth_date': self.birth_date.isoformat() if self.birth_date else None,
            'age': self.age,
            'gender': self.gender_display,
            'mother_name': self.mother_name,
            'father_name': self.father_name,
            'phone': self.formatted_phone,
            'home_phone': self.home_phone,
            'cell_phone': self.cell_phone,
            'contact_phone': self.contact_phone,
            'email': self.email,
            'address': self.address,
            'number': self.number,
            'neighborhood': self.neighborhood,
            'city': self.city,
            'state': self.state,
            'zip_code': self.zip_code,
            'full_address': self.full_address,
            'source': self.source,
            'source_display': self.source_display,
            'is_active': self.is_active,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }
    
    @classmethod
    def search(cls, query):
        """Buscar pacientes por nome, CPF ou CNS - MÉTODO LEGADO PARA COMPATIBILIDADE"""
        if not query:
            return cls.query
        
        # Usar busca integrada mas retornar apenas resultados locais
        local_results, _ = cls.search_integrated(query)
        return local_results
    
    def validate_cpf(self):
        """Validar CPF usando algoritmo oficial"""
        if not self.cpf or len(self.cpf) != 11:
            return False
        
        # Verificar se todos os dígitos são iguais
        if len(set(self.cpf)) == 1:
            return False
        
        # Calcular primeiro dígito verificador
        sum1 = sum(int(self.cpf[i]) * (10 - i) for i in range(9))
        digit1 = 11 - (sum1 % 11)
        if digit1 >= 10:
            digit1 = 0
        
        # Calcular segundo dígito verificador
        sum2 = sum(int(self.cpf[i]) * (11 - i) for i in range(10))
        digit2 = 11 - (sum2 % 11)
        if digit2 >= 10:
            digit2 = 0
        
        return int(self.cpf[9]) == digit1 and int(self.cpf[10]) == digit2
    
    def get_recent_dispensations(self, limit=5):
        """Obter dispensações recentes do paciente"""
        return Dispensation.query.filter_by(patient_id=self.id).order_by(
            Dispensation.dispensation_date.desc()
        ).limit(limit).all()
    
    def get_active_high_cost_processes(self):
        """Obter processos de alto custo ativos"""
        return [p for p in self.high_cost_processes if p.status.value in ['pending', 'under_evaluation', 'approved']]
    
    def sync_with_esus(self):
        """Sincronizar dados com e-SUS se disponível"""
        try:
            if self.cpf:
                esus_data = search_patient_in_esus(self.cpf, 'cpf')
                if esus_data:
                    # Atualizar com dados mais recentes do e-SUS
                    updated_data = esus_data[0]  # Pegar primeiro resultado
                    
                    # Atualizar campos se estiverem vazios localmente
                    if not self.mother_name and updated_data.get('no_mae'):
                        self.mother_name = updated_data['no_mae'].strip()
                    
                    if not self.father_name and updated_data.get('no_pai'):
                        self.father_name = updated_data['no_pai'].strip()
                    
                    if not self.cell_phone and updated_data.get('nu_telefone_celular'):
                        self.cell_phone = clean_phone(updated_data['nu_telefone_celular'])
                    
                    self.esus_sync_date = datetime.utcnow()
                    db.session.commit()
                    
                    logging.info(f"Paciente {self.full_name} sincronizado com e-SUS")
                    return True
        except Exception as e:
            logging.error(f"Erro ao sincronizar com e-SUS: {e}")
        
        return False
    
    def __repr__(self):
        return f'<Patient {self.full_name} - CPF: {self.formatted_cpf}>'

# ✅ MODELO DE MEDICAMENTOS COM CONTROLE DE INTERVALOS E CÁLCULOS
class Medication(db.Model):
    __tablename__ = 'medications'
    
    id = db.Column(db.Integer, primary_key=True)
    commercial_name = db.Column(db.String(100), nullable=False)
    generic_name = db.Column(db.String(100), nullable=False, index=True)
    dosage = db.Column(db.String(50), nullable=False)
    pharmaceutical_form = db.Column(db.String(50), nullable=False)  # comprimido, cápsula, etc
    
    # Classificação
    medication_type = db.Column(db.Enum(MedicationType), nullable=False, default=MedicationType.BASIC)
    requires_prescription = db.Column(db.Boolean, default=True)
    controlled_substance = db.Column(db.Boolean, default=False)
    
    # Estoque
    current_stock = db.Column(db.Integer, default=0)
    minimum_stock = db.Column(db.Integer, default=10)
    unit_cost = db.Column(db.DECIMAL(10, 2), nullable=True)
    
    # Lote e Validade
    batch_number = db.Column(db.String(50), nullable=True)
    expiry_date = db.Column(db.Date, nullable=True)
    
    # Metadados
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relacionamentos - CORRIGIDOS
    prescription_items = db.relationship('PrescriptionItem', backref='medication', lazy=True)
    dispensation_items = db.relationship('DispensationItem', back_populates='medication', lazy=True)
    inventory_movements = db.relationship('InventoryMovement', backref='medication', lazy=True)
    high_cost_processes = db.relationship('HighCostProcess', backref='medication', lazy=True)
    
    # ✅ RELACIONAMENTOS COM CONTROLES
    interval_control = db.relationship('MedicationInterval', back_populates='medication', uselist=False, cascade='all, delete-orphan')
    dispensing_config = db.relationship('MedicationDispensing', back_populates='medication', uselist=False, cascade='all, delete-orphan')
    
    @property
    def is_low_stock(self):
        return self.current_stock <= self.minimum_stock
    
    @property
    def is_near_expiry(self):
        if not self.expiry_date:
            return False
        days_to_expiry = (self.expiry_date - date.today()).days
        return days_to_expiry <= 30
    
    # ✅ PROPRIEDADES DE CONTROLE DE INTERVALO
    @property
    def has_interval_control(self):
        """Verifica se medicamento tem controle de intervalo ativo"""
        return self.interval_control and self.interval_control.is_active
    
    @property
    def interval_days(self):
        """Retorna dias de intervalo configurados"""
        if self.has_interval_control:
            return self.interval_control.interval_days
        return None
    
    @property
    def interval_status_display(self):
        """Status do controle de intervalo para exibição"""
        if self.has_interval_control:
            return f"Ativo ({self.interval_control.interval_days} dias)"
        return "Sem controle"
    
    # ✅ PROPRIEDADES DE CONFIGURAÇÃO DE CÁLCULOS
    @property
    def has_dispensing_config(self):
        """Verifica se medicamento tem configuração de cálculos"""
        return self.dispensing_config and self.dispensing_config.is_active
    
    @property
    def dispensing_config_display(self):
        """Exibição da configuração de cálculos"""
        if self.has_dispensing_config:
            return f"{self.dispensing_config.strength_value}{self.dispensing_config.strength_unit}/{self.dispensing_config.volume_per_dose}{self.dispensing_config.volume_unit}"
        return "Sem configuração"
    
    # ✅ MÉTODOS DE CONTROLE DE INTERVALO
    def get_next_allowed_date_for_patient(self, patient_id):
        """Calcula próxima data permitida para um paciente específico"""
        if not self.has_interval_control:
            return date.today()  # Sem controle, pode dispensar hoje
        
        # Buscar último controle ativo
        last_control = DispensationControl.query.filter_by(
            patient_id=patient_id,
            medication_id=self.id,
            is_active=True
        ).order_by(desc(DispensationControl.last_dispensation_date)).first()
        
        if not last_control:
            return date.today()  # Primeira dispensação, pode dispensar hoje
        
        return last_control.next_allowed_date
    
    def can_dispense_to_patient(self, patient_id):
        """Verifica se pode dispensar para um paciente específico"""
        next_date = self.get_next_allowed_date_for_patient(patient_id)
        return date.today() >= next_date
    
    def get_control_for_patient(self, patient_id):
        """Obter controle ativo para um paciente específico"""
        return DispensationControl.query.filter_by(
            patient_id=patient_id,
            medication_id=self.id,
            is_active=True
        ).order_by(desc(DispensationControl.last_dispensation_date)).first()
    
    def get_blocked_patients_count(self):
        """Número de pacientes com dispensação bloqueada"""
        if not self.has_interval_control:
            return 0
        
        today = date.today()
        return DispensationControl.query.filter(
            and_(
                DispensationControl.medication_id == self.id,
                DispensationControl.is_active == True,
                DispensationControl.next_allowed_date > today
            )
        ).count()
    
    def get_released_patients_count(self):
        """Número de pacientes com dispensação liberada"""
        if not self.has_interval_control:
            return 0
        
        today = date.today()
        return DispensationControl.query.filter(
            and_(
                DispensationControl.medication_id == self.id,
                DispensationControl.is_active == True,
                DispensationControl.next_allowed_date <= today
            )
        ).count()
    
    # ✅ MÉTODOS DE CÁLCULO DE DISPENSAÇÃO
    def calculate_dispensation(self, prescribed_dose, prescribed_unit, frequency_per_day, treatment_days):
        """Calcular dispensação baseado na prescrição"""
        if not self.has_dispensing_config:
            return {
                'success': False,
                'error': 'Medicamento não possui configuração de dispensação',
                'needs_configuration': True
            }
        
        return self.dispensing_config.calculate_dispensation(
            prescribed_dose, prescribed_unit, frequency_per_day, treatment_days
        )
    
    def calculate_drops_per_dose(self, dose_ml):
        """Calcular gotas por dose se aplicável"""
        if not self.has_dispensing_config:
            return None
        
        return self.dispensing_config.calculate_drops_per_dose(dose_ml)
    
    def to_dict_with_calculations(self):
        """Converter para dicionário incluindo configurações de cálculo"""
        base_dict = {
            'id': self.id,
            'commercial_name': self.commercial_name,
            'generic_name': self.generic_name,
            'dosage': self.dosage,
            'pharmaceutical_form': self.pharmaceutical_form,
            'medication_type': self.medication_type.value,
            'current_stock': self.current_stock,
            'minimum_stock': self.minimum_stock,
            'unit_cost': float(self.unit_cost) if self.unit_cost else 0.0,
            'is_low_stock': self.is_low_stock,
            'is_near_expiry': self.is_near_expiry,
            'has_interval_control': self.has_interval_control,
            'interval_status_display': self.interval_status_display,
            'has_dispensing_config': self.has_dispensing_config,
            'dispensing_config_display': self.dispensing_config_display
        }
        
        # Adicionar configuração de cálculos se existir
        if self.has_dispensing_config:
            base_dict['dispensing_config'] = self.dispensing_config.to_dict()
        
        # Adicionar controle de intervalo se existir
        if self.has_interval_control:
            base_dict['interval_control'] = {
                'interval_days': self.interval_control.interval_days,
                'is_active': self.interval_control.is_active,
                'requires_justification': self.interval_control.requires_justification
            }
        
        return base_dict
    
    def __repr__(self):
        return f'<Medication {self.commercial_name}>'

# Modelo de Receitas
class Prescription(db.Model):
    __tablename__ = 'prescriptions'
    
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('patients.id'), nullable=False)
    
    # Médico
    doctor_name = db.Column(db.String(100), nullable=False)
    doctor_crm = db.Column(db.String(20), nullable=False)
    doctor_specialty = db.Column(db.String(100), nullable=True)
    
    # Dados da receita
    prescription_date = db.Column(db.Date, nullable=False)
    diagnosis = db.Column(db.Text, nullable=True)
    observations = db.Column(db.Text, nullable=True)
    
    # Metadados
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    is_active = db.Column(db.Boolean, default=True)
    
    # Relacionamentos
    items = db.relationship('PrescriptionItem', backref='prescription', lazy=True, cascade='all, delete-orphan')
    dispensations = db.relationship('Dispensation', backref='prescription', lazy=True)
    
    def __repr__(self):
        return f'<Prescription {self.id} - Dr. {self.doctor_name}>'

# Itens da Receita
class PrescriptionItem(db.Model):
    __tablename__ = 'prescription_items'
    
    id = db.Column(db.Integer, primary_key=True)
    prescription_id = db.Column(db.Integer, db.ForeignKey('prescriptions.id'), nullable=False)
    medication_id = db.Column(db.Integer, db.ForeignKey('medications.id'), nullable=False)
    
    quantity = db.Column(db.Integer, nullable=False)
    dosage_instructions = db.Column(db.String(200), nullable=False)
    duration_days = db.Column(db.Integer, nullable=True)
    
    def __repr__(self):
        return f'<PrescriptionItem {self.medication.commercial_name}>'

# Modelo de Dispensação Básica
class Dispensation(db.Model):
    __tablename__ = 'dispensations'
    
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('patients.id'), nullable=False)
    prescription_id = db.Column(db.Integer, db.ForeignKey('prescriptions.id'), nullable=True)
    dispenser_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    
    dispensation_date = db.Column(db.DateTime, default=datetime.utcnow)
    status = db.Column(db.Enum(DispensationStatus), default=DispensationStatus.PENDING)
    observations = db.Column(db.Text, nullable=True)
    total_cost = db.Column(db.DECIMAL(10, 2), nullable=True)
    
    # Relacionamentos
    items = db.relationship('DispensationItem', back_populates='dispensation', cascade='all, delete-orphan')
    
    def __repr__(self):
        return f'<Dispensation {self.id} - {self.patient.full_name}>'

# ✅ ITENS DA DISPENSAÇÃO COM CONTROLE DE INTERVALOS
class DispensationItem(db.Model):
    __tablename__ = 'dispensation_items'
    
    id = db.Column(db.Integer, primary_key=True)
    dispensation_id = db.Column(db.Integer, db.ForeignKey('dispensations.id'), nullable=False)
    medication_id = db.Column(db.Integer, db.ForeignKey('medications.id'), nullable=False)
    
    quantity_dispensed = db.Column(db.Integer, nullable=False)
    unit_cost = db.Column(db.DECIMAL(10, 2), nullable=True, default=0.00)
    total_cost = db.Column(db.DECIMAL(10, 2), nullable=True, default=0.00)
    
    # Campo de observações específicas do item
    observations = db.Column(db.Text, nullable=True)
    
    # Timestamp de criação
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relacionamentos - CORRIGIDOS
    dispensation = db.relationship('Dispensation', back_populates='items')
    medication = db.relationship('Medication', back_populates='dispensation_items')
    
    @property
    def formatted_unit_cost(self):
        """Custo unitário formatado"""
        if self.unit_cost:
            return f"R$ {self.unit_cost:.2f}".replace('.', ',')
        return "R$ 0,00"
    
    @property
    def formatted_total_cost(self):
        """Custo total formatado"""
        if self.total_cost:
            return f"R$ {self.total_cost:.2f}".replace('.', ',')
        return "R$ 0,00"
    
    # ✅ MÉTODO PARA CRIAR CONTROLE DE INTERVALO
    def create_interval_control(self):
        """Criar controle de intervalo após dispensação"""
        if not self.medication.has_interval_control:
            return None
        
        # Desativar controles anteriores do mesmo medicamento/paciente
        DispensationControl.query.filter_by(
            patient_id=self.dispensation.patient_id,
            medication_id=self.medication_id,
            is_active=True
        ).update({'is_active': False})
        
        # Calcular próxima data permitida
        interval_days = self.medication.interval_control.interval_days
        next_date = date.today() + timedelta(days=interval_days)
        
        # Criar novo controle
        control = DispensationControl(
            patient_id=self.dispensation.patient_id,
            medication_id=self.medication_id,
            dispensation_item_id=self.id,
            last_dispensation_date=date.today(),
            next_allowed_date=next_date,
            interval_days_used=interval_days
        )
        
        db.session.add(control)
        return control
    
    def to_dict(self):
        """Converter para dicionário"""
        return {
            'id': self.id,
            'dispensation_id': self.dispensation_id,
            'medication_id': self.medication_id,
            'medication_name': self.medication.commercial_name if self.medication else None,
            'quantity_dispensed': self.quantity_dispensed,
            'unit_cost': float(self.unit_cost) if self.unit_cost else 0.00,
            'total_cost': float(self.total_cost) if self.total_cost else 0.00,
            'observations': self.observations,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }
    
    def __repr__(self):
        return f'<DispensationItem {self.medication.commercial_name if self.medication else "Unknown"}: {self.quantity_dispensed}>'

# Processo Alto Custo
class HighCostProcess(db.Model):
    __tablename__ = 'high_cost_processes'
    
    id = db.Column(db.Integer, primary_key=True)
    protocol_number = db.Column(db.String(20), unique=True, nullable=False, index=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('patients.id'), nullable=False)
    medication_id = db.Column(db.Integer, db.ForeignKey('medications.id'), nullable=False)
    
    # Dados médicos
    cid10 = db.Column(db.String(10), nullable=False)
    diagnosis = db.Column(db.Text, nullable=False)
    doctor_name = db.Column(db.String(100), nullable=False)
    doctor_crm = db.Column(db.String(20), nullable=False)
    
    # Solicitação
    requested_quantity = db.Column(db.Integer, nullable=False)
    treatment_duration = db.Column(db.Integer, nullable=True)  # dias
    justification = db.Column(db.Text, nullable=False)
    urgency_level = db.Column(db.Enum('low', 'medium', 'high', 'urgent', name='urgency_enum'), default='medium')
    
    # Status e datas
    status = db.Column(db.Enum(ProcessStatus), default=ProcessStatus.PENDING)
    request_date = db.Column(db.DateTime, default=datetime.utcnow)
    evaluation_date = db.Column(db.DateTime, nullable=True)
    approval_date = db.Column(db.DateTime, nullable=True)
    dispensation_date = db.Column(db.DateTime, nullable=True)
    
    # Relacionamentos
    documents = db.relationship('ProcessDocument', backref='process', lazy=True, cascade='all, delete-orphan')
    evaluations = db.relationship('PharmaceuticalEvaluation', backref='process', lazy=True)
    approvals = db.relationship('ProcessApproval', backref='process', lazy=True)
    dispensations = db.relationship('HighCostDispensation', backref='process', lazy=True)
    
    def generate_protocol(self):
        """Gera número de protocolo único"""
        year = datetime.now().year
        count = HighCostProcess.query.filter(
            db.extract('year', HighCostProcess.request_date) == year
        ).count() + 1
        self.protocol_number = f"AC{year}{count:04d}"
    
    def __repr__(self):
        return f'<HighCostProcess {self.protocol_number}>'

# Documentos do Processo
class ProcessDocument(db.Model):
    __tablename__ = 'process_documents'
    
    id = db.Column(db.Integer, primary_key=True)
    process_id = db.Column(db.Integer, db.ForeignKey('high_cost_processes.id'), nullable=False)
    
    document_type = db.Column(db.Enum('prescription', 'medical_report', 'exam', 'other', name='doc_type_enum'), nullable=False)
    filename = db.Column(db.String(255), nullable=False)
    original_filename = db.Column(db.String(255), nullable=False)
    file_path = db.Column(db.String(500), nullable=False)
    file_size = db.Column(db.Integer, nullable=True)
    mime_type = db.Column(db.String(100), nullable=True)
    
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)
    is_required = db.Column(db.Boolean, default=True)
    
    def __repr__(self):
        return f'<ProcessDocument {self.original_filename}>'

# Avaliação Farmacêutica
class PharmaceuticalEvaluation(db.Model):
    __tablename__ = 'pharmaceutical_evaluations'
    
    id = db.Column(db.Integer, primary_key=True)
    process_id = db.Column(db.Integer, db.ForeignKey('high_cost_processes.id'), nullable=False)
    evaluator_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    
    # Avaliação técnica
    technical_opinion = db.Column(db.Text, nullable=False)
    meets_protocol = db.Column(db.Boolean, nullable=False)
    recommended_quantity = db.Column(db.Integer, nullable=True)
    recommended_duration = db.Column(db.Integer, nullable=True)
    
    # Parecer
    recommendation = db.Column(db.Enum('approve', 'deny', 'request_more_info', name='recommendation_enum'), nullable=False)
    observations = db.Column(db.Text, nullable=True)
    
    evaluation_date = db.Column(db.DateTime, default=datetime.utcnow)
    
    def __repr__(self):
        return f'<PharmaceuticalEvaluation {self.id}>'

# Aprovação do Processo
class ProcessApproval(db.Model):
    __tablename__ = 'process_approvals'
    
    id = db.Column(db.Integer, primary_key=True)
    process_id = db.Column(db.Integer, db.ForeignKey('high_cost_processes.id'), nullable=False)
    approver_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    
    decision = db.Column(db.Enum('approved', 'denied', name='decision_enum'), nullable=False)
    approved_quantity = db.Column(db.Integer, nullable=True)
    approved_duration = db.Column(db.Integer, nullable=True)
    justification = db.Column(db.Text, nullable=False)
    
    special_conditions = db.Column(db.Text, nullable=True)
    approval_expires_at = db.Column(db.Date, nullable=True)
    
    approval_date = db.Column(db.DateTime, default=datetime.utcnow)
    
    def __repr__(self):
        return f'<ProcessApproval {self.decision}>'

# Dispensação Alto Custo
class HighCostDispensation(db.Model):
    __tablename__ = 'high_cost_dispensations'
    
    id = db.Column(db.Integer, primary_key=True)
    process_id = db.Column(db.Integer, db.ForeignKey('high_cost_processes.id'), nullable=False)
    dispenser_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    
    quantity_dispensed = db.Column(db.Integer, nullable=False)
    dispensation_date = db.Column(db.DateTime, default=datetime.utcnow)
    next_dispensation_date = db.Column(db.Date, nullable=True)
    
    patient_signature = db.Column(db.Boolean, default=False)
    terms_accepted = db.Column(db.Boolean, default=False)
    observations = db.Column(db.Text, nullable=True)
    
    # Relacionamentos
    tracking_records = db.relationship('PatientTracking', backref='dispensation', lazy=True)
    
    def __repr__(self):
        return f'<HighCostDispensation {self.id}>'

# Acompanhamento de Pacientes
class PatientTracking(db.Model):
    __tablename__ = 'patient_tracking'
    
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('patients.id'), nullable=False)
    dispensation_id = db.Column(db.Integer, db.ForeignKey('high_cost_dispensations.id'), nullable=True)
    
    tracking_date = db.Column(db.Date, nullable=False)
    tracking_type = db.Column(db.Enum('follow_up', 'adverse_reaction', 'effectiveness', 'renewal', name='tracking_type_enum'), nullable=False)
    
    # Dados do acompanhamento
    clinical_response = db.Column(db.Enum('excellent', 'good', 'partial', 'poor', 'no_response', name='response_enum'), nullable=True)
    adverse_reactions = db.Column(db.Text, nullable=True)
    notes = db.Column(db.Text, nullable=True)
    
    next_tracking_date = db.Column(db.Date, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    def __repr__(self):
        return f'<PatientTracking {self.patient.full_name}>'

# Movimentação de Estoque
class InventoryMovement(db.Model):
    __tablename__ = 'inventory_movements'
    
    id = db.Column(db.Integer, primary_key=True)
    medication_id = db.Column(db.Integer, db.ForeignKey('medications.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    
    movement_type = db.Column(db.Enum('entry', 'exit', 'adjustment', 'expiry', name='movement_type_enum'), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    previous_stock = db.Column(db.Integer, nullable=False)
    new_stock = db.Column(db.Integer, nullable=False)
    
    reason = db.Column(db.String(200), nullable=True)
    reference_id = db.Column(db.Integer, nullable=True)  # ID da dispensação/processo
    reference_type = db.Column(db.String(50), nullable=True)  # tipo da referência
    
    movement_date = db.Column(db.DateTime, default=datetime.utcnow)
    
    def __repr__(self):
        return f'<InventoryMovement {self.movement_type} - {self.quantity}>'

# Log de Auditoria
class AuditLog(db.Model):
    __tablename__ = 'audit_logs'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    
    action = db.Column(db.String(100), nullable=False)
    table_name = db.Column(db.String(50), nullable=False)
    record_id = db.Column(db.Integer, nullable=True)
    
    old_values = db.Column(db.Text, nullable=True)  # JSON
    new_values = db.Column(db.Text, nullable=True)  # JSON
    
    ip_address = db.Column(db.String(45), nullable=True)
    user_agent = db.Column(db.String(500), nullable=True)
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    def __repr__(self):
        return f'<AuditLog {self.action} - {self.table_name}>'