# =================================
# IMPORTS DO FARMACUIDAR - FORMS
# =================================

# Flask-WTF
from flask_wtf import FlaskForm
from flask_wtf.file import FileField, FileAllowed, FileRequired

# WTForms - Campos
from wtforms import (
    StringField, PasswordField, SelectField, TextAreaField, 
    IntegerField, DecimalField, DateField, BooleanField, 
    HiddenField, SubmitField, SelectMultipleField, RadioField,
    DateTimeField, FloatField, SearchField
)

# WTForms - Validadores
from wtforms.validators import (
    DataRequired, Email, Length, Optional, NumberRange, 
    ValidationError, Regexp, EqualTo, URL, AnyOf, NoneOf,
    InputRequired, MacAddress, IPAddress, UUID
)

# WTForms - Widgets
from wtforms.widgets import (
    CheckboxInput, ListWidget, TextArea, PasswordInput,
    HiddenInput, Select, Input, TextInput
)

# Models da aplicação
from app.models import (
    User, Patient, Medication, UserRole, MedicationType,
    ProcessStatus, DispensationStatus, HighCostProcess,
    Dispensation, InventoryMovement, MedicationDispensing  # ✅ NOVO
)

# Utilities
from app.utils import validate_cpf, validate_cns, format_cpf, format_cns

# Python stdlib
from datetime import date, datetime, timedelta
import re
import os
from decimal import Decimal

# Flask
from flask import current_app
from flask_login import current_user

# =================================
# CONFIGURAÇÕES GLOBAIS
# =================================

# Extensões de arquivo permitidas
ALLOWED_EXTENSIONS = {'pdf', 'png', 'jpg', 'jpeg', 'doc', 'docx'}

# Tamanho máximo de arquivo (16MB)
MAX_FILE_SIZE = 16 * 1024 * 1024

# Opções de estados brasileiros
ESTADOS_BRASIL = [
    ('AC', 'Acre'), ('AL', 'Alagoas'), ('AP', 'Amapá'), ('AM', 'Amazonas'),
    ('BA', 'Bahia'), ('CE', 'Ceará'), ('DF', 'Distrito Federal'),
    ('ES', 'Espírito Santo'), ('GO', 'Goiás'), ('MA', 'Maranhão'),
    ('MT', 'Mato Grosso'), ('MS', 'Mato Grosso do Sul'), ('MG', 'Minas Gerais'),
    ('PA', 'Pará'), ('PB', 'Paraíba'), ('PR', 'Paraná'), ('PE', 'Pernambuco'),
    ('PI', 'Piauí'), ('RJ', 'Rio de Janeiro'), ('RN', 'Rio Grande do Norte'),
    ('RS', 'Rio Grande do Sul'), ('RO', 'Rondônia'), ('RR', 'Roraima'),
    ('SC', 'Santa Catarina'), ('SP', 'São Paulo'), ('SE', 'Sergipe'),
    ('TO', 'Tocantins')
]

# Opções de gênero
GENERO_CHOICES = [
    ('', 'Selecione...'),
    ('M', 'Masculino'),
    ('F', 'Feminino'),
    ('O', 'Outro'),
    ('N', 'Não informar')
]

# Formas farmacêuticas
PHARMACEUTICAL_FORMS = [
    ('comprimido', 'Comprimido'),
    ('capsula', 'Cápsula'),
    ('xarope', 'Xarope'),
    ('solucao', 'Solução'),
    ('suspensao', 'Suspensão'),
    ('pomada', 'Pomada'),
    ('creme', 'Creme'),
    ('gel', 'Gel'),
    ('spray', 'Spray'),
    ('inalador', 'Inalador'),
    ('gotas', 'Gotas'),
    ('ampola', 'Ampola'),
    ('frasco', 'Frasco'),
    ('outros', 'Outros')
]

# ✅ UNIDADES PARA CÁLCULOS FARMACOLÓGICOS
STRENGTH_UNITS = [
    ('mg', 'mg (miligramas)'),
    ('g', 'g (gramas)'),
    ('mcg', 'mcg (microgramas)'),
    ('UI', 'UI (Unidades Internacionais)'),
    ('%', '% (percentual)')
]

VOLUME_UNITS = [
    ('ml', 'ml (mililitros)'),
    ('comp', 'comprimido'),
    ('cap', 'cápsula'),
    ('ampola', 'ampola'),
    ('frasco', 'frasco'),
    ('sachê', 'sachê')
]

FREQUENCY_CHOICES = [
    ('1', '1x ao dia'),
    ('2', '2x ao dia (12/12h)'),
    ('3', '3x ao dia (8/8h)'),
    ('4', '4x ao dia (6/6h)'),
    ('6', '6x ao dia (4/4h)'),
    ('8', '8x ao dia (3/3h)')
]

# Níveis de urgência
URGENCY_LEVELS = [
    ('baixa', 'Baixa'),
    ('media', 'Média'),
    ('alta', 'Alta'),
    ('urgente', 'Urgente')
]

# Tipos de movimento de estoque
MOVEMENT_TYPES = [
    ('entry', 'Entrada'),
    ('exit', 'Saída'),
    ('adjustment', 'Ajuste'),
    ('transfer', 'Transferência'),
    ('loss', 'Perda'),
    ('expiry', 'Vencimento')
]

# Tipos de relatório
REPORT_TYPES = [
    ('consumption', 'Consumo de Medicamentos'),
    ('stock', 'Estoque Atual'),
    ('expiry', 'Medicamentos Próximos ao Vencimento'),
    ('high_cost', 'Processos Alto Custo'),
    ('dispensations', 'Dispensações por Período'),
    ('patients', 'Relatório de Pacientes'),
    ('financial', 'Relatório Financeiro'),
    ('esus_integration', 'Integração e-SUS'),  # ✅ NOVO
    ('inventory_movement', 'Movimentação de Estoque'),
    ('user_activity', 'Atividade de Usuários'),
    ('pharmaceutical_calculations', 'Cálculos Farmacológicos')  # ✅ NOVO
]

# Formatos de relatório
FORMAT_TYPES = [
    ('html', 'Visualizar na Tela'),
    ('pdf', 'PDF'),
    ('excel', 'Excel'),
    ('csv', 'CSV')
]

# ✅ OPÇÕES PARA FILTROS DE PACIENTES
AGE_RANGE_CHOICES = [
    ('', 'Todas as idades'),
    ('0-18', '0 a 18 anos'),
    ('19-30', '19 a 30 anos'),
    ('31-50', '31 a 50 anos'),
    ('51-65', '51 a 65 anos'),
    ('65+', 'Acima de 65 anos')
]

REGISTRATION_PERIOD_CHOICES = [
    ('', 'Todos os períodos'),
    ('30', 'Últimos 30 dias'),
    ('90', 'Últimos 3 meses'),
    ('180', 'Últimos 6 meses'),
    ('365', 'Último ano')
]

# =================================
# VALIDADORES CUSTOMIZADOS
# =================================

def validate_crf_format(form, field):
    """Valida formato do CRF"""
    if field.data:
        if not re.match(r'^\d{4,6}-[A-Z]{2}$', field.data):
            raise ValidationError('Formato de CRF inválido. Use: 12345-SP')

def validate_file_size(form, field):
    """Valida tamanho do arquivo"""
    if field.data:
        # Esta validação seria implementada no JavaScript
        pass

def validate_password_strength(form, field):
    """Valida força da senha"""
    if field.data:
        password = field.data
        if len(password) < 6:
            raise ValidationError('Senha deve ter pelo menos 6 caracteres')
        
        # Verificar se tem pelo menos uma letra e um número
        if not re.search(r'[A-Za-z]', password) or not re.search(r'\d', password):
            raise ValidationError('Senha deve conter pelo menos uma letra e um número')

def validate_future_date(form, field):
    """Valida se a data não é no passado"""
    if field.data and field.data < date.today():
        raise ValidationError('Data não pode ser no passado')

def validate_age_range(form, field):
    """Valida faixa etária válida"""
    if field.data:
        today = date.today()
        age = today.year - field.data.year - ((today.month, today.day) < (field.data.month, field.data.day))
        if age < 0 or age > 150:
            raise ValidationError('Data de nascimento inválida')

def validate_positive_number(form, field):
    """Valida se o número é positivo"""
    if field.data is not None and field.data <= 0:
        raise ValidationError('Valor deve ser maior que zero')

def validate_percentage(form, field):
    """Valida se o valor está entre 0 e 100"""
    if field.data is not None and (field.data < 0 or field.data > 100):
        raise ValidationError('Valor deve estar entre 0 e 100')

# ✅ NOVOS VALIDADORES PARA CÁLCULOS FARMACOLÓGICOS
def validate_concentration(form, field):
    """Valida concentração farmacológica"""
    if field.data is not None and field.data <= 0:
        raise ValidationError('Concentração deve ser maior que zero')

def validate_volume_per_dose(form, field):
    """Valida volume por dose"""
    if field.data is not None and field.data <= 0:
        raise ValidationError('Volume por dose deve ser maior que zero')

def validate_stability_days(form, field):
    """Valida dias de estabilidade"""
    if field.data is not None and (field.data < 1 or field.data > 365):
        raise ValidationError('Estabilidade deve estar entre 1 e 365 dias')

def validate_drops_per_ml(form, field):
    """Valida gotas por ml"""
    if field.data is not None and (field.data < 10 or field.data > 50):
        raise ValidationError('Gotas por ml deve estar entre 10 e 50')

# =================================
# WIDGETS CUSTOMIZADOS
# =================================

class MultiCheckboxField(SelectMultipleField):
    """Campo de múltiplas seleções com checkboxes"""
    widget = ListWidget(prefix_label=False)
    option_widget = CheckboxInput()

class DatePickerWidget(Input):
    """Widget personalizado para seleção de data"""
    input_type = 'date'

class CurrencyWidget(TextInput):
    """Widget para campos de moeda"""
    def __call__(self, field, **kwargs):
        kwargs.setdefault('class', 'form-control currency-input')
        kwargs.setdefault('placeholder', '0,00')
        return super(CurrencyWidget, self).__call__(field, **kwargs)

# ✅ WIDGET PARA CAMPOS DE CONCENTRAÇÃO
class ConcentrationWidget(TextInput):
    """Widget para campos de concentração farmacológica"""
    def __call__(self, field, **kwargs):
        kwargs.setdefault('class', 'form-control concentration-input')
        kwargs.setdefault('step', '0.001')
        kwargs.setdefault('min', '0')
        return super(ConcentrationWidget, self).__call__(field, **kwargs)

# =================================
# CAMPOS CUSTOMIZADOS
# =================================

class CurrencyField(DecimalField):
    """Campo para valores monetários"""
    widget = CurrencyWidget()
    
    def process_formdata(self, valuelist):
        if valuelist:
            try:
                # Remover formatação de moeda
                value = valuelist[0].replace('R$', '').replace('.', '').replace(',', '.')
                self.data = Decimal(value)
            except (ValueError, TypeError, AttributeError):
                self.data = None
                raise ValidationError('Valor monetário inválido')

class CPFField(StringField):
    """Campo específico para CPF"""
    def __init__(self, label='CPF', validators=None, **kwargs):
        validators = validators or []
        validators.append(validate_cpf)
        kwargs.setdefault('render_kw', {}).update({
            'class': 'form-control cpf-mask',
            'placeholder': '000.000.000-00',
            'maxlength': '14'
        })
        super(CPFField, self).__init__(label, validators, **kwargs)

class CNSField(StringField):
    """Campo específico para CNS"""
    def __init__(self, label='CNS', validators=None, **kwargs):
        validators = validators or []
        validators.append(validate_cns)
        kwargs.setdefault('render_kw', {}).update({
            'class': 'form-control cns-mask',
            'placeholder': '000 0000 0000 0000',
            'maxlength': '18'
        })
        super(CNSField, self).__init__(label, validators, **kwargs)

class PhoneField(StringField):
    """Campo específico para telefone"""
    def __init__(self, label='Telefone', validators=None, **kwargs):
        kwargs.setdefault('render_kw', {}).update({
            'class': 'form-control phone-mask',
            'placeholder': '(00) 00000-0000'
        })
        super(PhoneField, self).__init__(label, validators, **kwargs)

class CEPField(StringField):
    """Campo específico para CEP"""
    def __init__(self, label='CEP', validators=None, **kwargs):
        kwargs.setdefault('render_kw', {}).update({
            'class': 'form-control cep-mask',
            'placeholder': '00000-000',
            'maxlength': '9'
        })
        super(CEPField, self).__init__(label, validators, **kwargs)

# ✅ NOVO CAMPO PARA CONCENTRAÇÃO
class ConcentrationField(DecimalField):
    """Campo específico para concentrações farmacológicas"""
    widget = ConcentrationWidget()
    
    def __init__(self, label='Concentração', validators=None, **kwargs):
        validators = validators or []
        validators.append(validate_concentration)
        kwargs.setdefault('places', 3)  # 3 casas decimais
        super(ConcentrationField, self).__init__(label, validators, **kwargs)

# Validadores customizados de classe
class CPFValidator:
    def __init__(self, message=None):
        if not message:
            message = 'CPF inválido'
        self.message = message

    def __call__(self, form, field):
        if field.data and not validate_cpf(field.data):
            raise ValidationError(self.message)

class CNSValidator:
    def __init__(self, message=None):
        if not message:
            message = 'CNS inválido'
        self.message = message

    def __call__(self, form, field):
        if field.data and not validate_cns(field.data):
            raise ValidationError(self.message)

class FutureDateValidator:
    def __init__(self, message=None):
        if not message:
            message = 'Data não pode ser no futuro'
        self.message = message

    def __call__(self, form, field):
        if field.data and field.data > date.today():
            raise ValidationError(self.message)

# =================== FORMULÁRIOS DE AUTENTICAÇÃO ===================

class LoginForm(FlaskForm):
    username = StringField('Usuário', validators=[
        DataRequired(message='Campo obrigatório'),
        Length(min=3, max=80, message='Usuário deve ter entre 3 e 80 caracteres')
    ], render_kw={'class': 'form-control', 'autofocus': True})
    
    password = PasswordField('Senha', validators=[
        DataRequired(message='Campo obrigatório')
    ], render_kw={'class': 'form-control'})
    
    remember_me = BooleanField('Lembrar de mim', render_kw={'class': 'form-check-input'})
    
    submit = SubmitField('Entrar', render_kw={'class': 'btn btn-primary'})

class ChangePasswordForm(FlaskForm):
    current_password = PasswordField('Senha Atual', validators=[
        DataRequired(message='Campo obrigatório')
    ], render_kw={'class': 'form-control'})
    
    new_password = PasswordField('Nova Senha', validators=[
        DataRequired(message='Campo obrigatório'),
        Length(min=6, message='Senha deve ter pelo menos 6 caracteres')
    ], render_kw={'class': 'form-control'})
    
    confirm_password = PasswordField('Confirmar Nova Senha', validators=[
        DataRequired(message='Campo obrigatório'),
        EqualTo('new_password', message='Senhas devem ser iguais')
    ], render_kw={'class': 'form-control'})
    
    submit = SubmitField('Alterar Senha', render_kw={'class': 'btn btn-primary'})

# =================== FORMULÁRIOS DE USUÁRIOS ===================

class UserForm(FlaskForm):
    username = StringField('Nome de Usuário', validators=[
        DataRequired(message='Campo obrigatório'),
        Length(min=3, max=80, message='Nome deve ter entre 3 e 80 caracteres'),
        Regexp(r'^[a-zA-Z0-9_]+$', message='Apenas letras, números e underscore')
    ], render_kw={'class': 'form-control'})
    
    email = StringField('Email', validators=[
        DataRequired(message='Campo obrigatório'),
        Email(message='Email inválido'),
        Length(max=120)
    ], render_kw={'class': 'form-control'})
    
    full_name = StringField('Nome Completo', validators=[
        DataRequired(message='Campo obrigatório'),
        Length(min=2, max=100, message='Nome deve ter entre 2 e 100 caracteres')
    ], render_kw={'class': 'form-control'})
    
    role = SelectField('Função', choices=[
        ('attendant', 'Atendente'),
        ('pharmacist', 'Farmacêutico'),
        ('admin', 'Administrador')
    ], validators=[DataRequired()], render_kw={'class': 'form-select'})
    
    crf = StringField('CRF (apenas farmacêuticos)', validators=[
        Optional(),
        Length(max=20)
    ], render_kw={'class': 'form-control', 'placeholder': 'Ex: 12345-SP'})
    
    password = PasswordField('Senha', validators=[
        Optional(),  # Opcional para edição
        Length(min=6, message='Senha deve ter pelo menos 6 caracteres')
    ], render_kw={'class': 'form-control'})
    
    confirm_password = PasswordField('Confirmar Senha', validators=[
        Optional(),
        EqualTo('password', message='As senhas devem ser iguais')
    ], render_kw={'class': 'form-control'})
    
    is_active = BooleanField('Usuário Ativo', default=True, render_kw={'class': 'form-check-input'})
    
    submit = SubmitField('Salvar', render_kw={'class': 'btn btn-primary'})
    
    def __init__(self, user_id=None, *args, **kwargs):
        super(UserForm, self).__init__(*args, **kwargs)
        self.user_id = user_id
        
        # Ajustar validadores baseado no contexto (novo vs edição)
        if not user_id:  # Novo usuário
            self.password.validators = [
                DataRequired(message='Campo obrigatório'),
                Length(min=6, message='Senha deve ter pelo menos 6 caracteres')
            ]
            self.confirm_password.validators = [
                DataRequired(message='Campo obrigatório'),
                EqualTo('password', message='As senhas devem ser iguais')
            ]
    
    def validate_username(self, username):
        user = User.query.filter_by(username=username.data).first()
        if user and (not hasattr(self, 'user_id') or user.id != self.user_id):
            raise ValidationError('Nome de usuário já existe')
    
    def validate_email(self, email):
        user = User.query.filter_by(email=email.data).first()
        if user and (not hasattr(self, 'user_id') or user.id != self.user_id):
            raise ValidationError('Email já está em uso')
    
    def validate_crf(self, crf):
        # Validar CRF apenas se o role for farmacêutico
        if self.role.data == 'pharmacist' and not crf.data:
            raise ValidationError('CRF é obrigatório para farmacêuticos')
        
        # Validar formato do CRF se fornecido
        if crf.data:
            import re
            if not re.match(r'^\d{4,6}-[A-Z]{2}$', crf.data):
                raise ValidationError('Formato de CRF inválido. Use: 12345-SP')
            
            # Verificar se CRF já existe
            user = User.query.filter_by(crf=crf.data).first()
            if user and (not hasattr(self, 'user_id') or user.id != self.user_id):
                raise ValidationError('Este CRF já está cadastrado')

# =================== FORMULÁRIOS DE PACIENTES COM E-SUS ===================

class PatientForm(FlaskForm):
    # ✅ CAMPOS BÁSICOS
    cpf = StringField('CPF', validators=[
        DataRequired(message='Campo obrigatório'),
        Length(min=14, max=14, message='CPF deve ter formato 000.000.000-00')
    ], render_kw={'class': 'form-control', 'data-mask': '000.000.000-00', 'maxlength': '14', 'placeholder': '000.000.000-00'})
    
    cns = StringField('Cartão Nacional de Saúde (CNS)', validators=[
        Optional(),
        Length(min=18, max=18, message='CNS deve ter formato 000 0000 0000 0000')
    ], render_kw={'class': 'form-control', 'data-mask': '000 0000 0000 0000', 'maxlength': '18', 'placeholder': '000 0000 0000 0000'})
    
    full_name = StringField('Nome Completo', validators=[
        DataRequired(message='Campo obrigatório'),
        Length(min=2, max=100, message='Nome deve ter entre 2 e 100 caracteres')
    ], render_kw={'class': 'form-control', 'placeholder': 'Digite o nome completo'})
    
    birth_date = DateField('Data de Nascimento', validators=[
        DataRequired(message='Campo obrigatório')
    ], render_kw={'class': 'form-control'})
    
    gender = SelectField('Sexo', choices=GENERO_CHOICES, validators=[Optional()], render_kw={'class': 'form-select'})
    
    # ✅ CAMPOS E-SUS - FILIAÇÃO
    mother_name = StringField('Nome da Mãe', validators=[
        Optional(),
        Length(max=100, message='Nome muito longo')
    ], render_kw={'class': 'form-control', 'placeholder': 'Nome completo da mãe'})
    
    father_name = StringField('Nome do Pai', validators=[
        Optional(),
        Length(max=100, message='Nome muito longo')
    ], render_kw={'class': 'form-control', 'placeholder': 'Nome completo do pai'})
    
    # ✅ CAMPOS E-SUS - TELEFONES MÚLTIPLOS
    home_phone = StringField('Telefone Residencial', validators=[
        Optional(),
        Length(max=15, message='Telefone muito longo')
    ], render_kw={'class': 'form-control', 'data-mask': '(00) 0000-0000', 'placeholder': '(00) 0000-0000'})
    
    cell_phone = StringField('Telefone Celular', validators=[
        Optional(),
        Length(max=15, message='Telefone muito longo')
    ], render_kw={'class': 'form-control', 'data-mask': '(00) 00000-0000', 'placeholder': '(00) 00000-0000'})
    
    contact_phone = StringField('Telefone de Contato', validators=[
        Optional(),
        Length(max=15, message='Telefone muito longo')
    ], render_kw={'class': 'form-control', 'data-mask': '(00) 00000-0000', 'placeholder': 'Outro telefone'})
    
    # ✅ CAMPO COMPATIBILIDADE
    phone = StringField('Telefone Principal', validators=[
        Optional(),
        Length(max=15, message='Telefone muito longo')
    ], render_kw={'class': 'form-control', 'data-mask': '(00) 00000-0000', 'placeholder': '(00) 00000-0000'})
    
    email = StringField('Email', validators=[
        Optional(),
        Email(message='Email inválido'),
        Length(max=120, message='Email muito longo')
    ], render_kw={'class': 'form-control', 'placeholder': 'exemplo@email.com'})
    
    # ✅ ENDEREÇO COMPLETO
    address = StringField('Logradouro', validators=[
        Optional(),
        Length(max=200, message='Logradouro muito longo')
    ], render_kw={'class': 'form-control', 'placeholder': 'Nome da rua, avenida, etc.'})
    
    number = StringField('Número', validators=[
        Optional(),
        Length(max=10, message='Número muito longo')
    ], render_kw={'class': 'form-control', 'placeholder': 'Nº'})
    
    neighborhood = StringField('Bairro', validators=[
        Optional(),
        Length(max=100, message='Bairro muito longo')
    ], render_kw={'class': 'form-control', 'placeholder': 'Nome do bairro'})
    
    city = StringField('Cidade', validators=[
        DataRequired(message='Campo obrigatório'),
        Length(max=100, message='Cidade muito longa')
    ], render_kw={'class': 'form-control', 'value': 'Cosmópolis'})
    
    state = SelectField('Estado', choices=[('', 'Selecione...')] + ESTADOS_BRASIL, 
                       validators=[DataRequired(message='Campo obrigatório')], 
                       render_kw={'class': 'form-select'})
    
    zip_code = StringField('CEP', validators=[
        Optional()
    ], render_kw={'class': 'form-control', 'data-mask': '00000-000', 'maxlength': '9', 'placeholder': '00000-000'})
    
    submit = SubmitField('Salvar Paciente', render_kw={'class': 'btn btn-primary'})
    
    def __init__(self, patient_id=None, *args, **kwargs):
        super(PatientForm, self).__init__(*args, **kwargs)
        self.patient_id = patient_id
        
        # Pré-selecionar São Paulo se não tiver valor
        if not self.state.data:
            self.state.data = 'SP'
    
    def validate_cpf(self, cpf):
        """Validar CPF único e formato"""
        if cpf.data:
            import re
            # Remove formatação
            clean_cpf = re.sub(r'[^0-9]', '', cpf.data)
            
            # Verificar se tem 11 dígitos
            if len(clean_cpf) != 11:
                raise ValidationError('CPF deve ter 11 dígitos')
            
            # Verificar se não é CPF inválido conhecido
            invalid_cpfs = ['00000000000', '11111111111', '22222222222', '33333333333',
                           '44444444444', '55555555555', '66666666666', '77777777777',
                           '88888888888', '99999999999']
            
            if clean_cpf in invalid_cpfs:
                raise ValidationError('CPF inválido')
            
            # Validar algoritmo do CPF
            if not self._validate_cpf_algorithm(clean_cpf):
                raise ValidationError('CPF inválido')
            
            # Verificar se CPF já existe (exceto para o próprio paciente em edição)
            patient = Patient.query.filter_by(cpf=clean_cpf).first()
            if patient and (not hasattr(self, 'patient_id') or patient.id != self.patient_id):
                raise ValidationError('CPF já cadastrado')
    
    def validate_cns(self, cns):
        """Validar CNS formato e unicidade"""
        if cns.data:
            import re
            # Remove formatação
            clean_cns = re.sub(r'[^0-9]', '', cns.data)
            
            # Verificar se tem 15 dígitos
            if len(clean_cns) != 15:
                raise ValidationError('CNS deve ter 15 dígitos')
            
            # Verificar se não é CNS inválido conhecido
            if clean_cns.startswith('000000000') or len(set(clean_cns)) == 1:
                raise ValidationError('CNS inválido')
            
            # Verificar se CNS já existe (exceto para o próprio paciente em edição)
            patient = Patient.query.filter_by(cns=clean_cns).first()
            if patient and (not hasattr(self, 'patient_id') or patient.id != self.patient_id):
                raise ValidationError('CNS já cadastrado')
    
    def validate_birth_date(self, birth_date):
        """Validar data de nascimento"""
        if birth_date.data:
            today = date.today()
            
            # Não pode ser no futuro
            if birth_date.data > today:
                raise ValidationError('Data de nascimento não pode ser no futuro')
            
            # Não pode ser muito antiga (mais de 150 anos)
            min_date = today - timedelta(days=150*365)
            if birth_date.data < min_date:
                raise ValidationError('Data de nascimento muito antiga')
    
    def validate_zip_code(self, zip_code):
        """Validar formato do CEP"""
        if zip_code.data:
            import re
            # Remover formatação
            clean_cep = re.sub(r'\D', '', zip_code.data)
            
            # Verificar se tem 8 dígitos
            if len(clean_cep) != 8:
                raise ValidationError('CEP deve ter 8 dígitos')
    
    def _validate_phone_format(self, phone_data):
        """Validar formato do telefone"""
        if phone_data:
            import re
            # Remove formatação
            clean_phone = re.sub(r'[^0-9]', '', phone_data)
            
            # Verificar se tem 10 ou 11 dígitos
            if len(clean_phone) not in [10, 11]:
                return False
        return True
    
    def validate_phone(self, phone):
        """Validar telefone principal"""
        if not self._validate_phone_format(phone.data):
            raise ValidationError('Telefone deve ter 10 ou 11 dígitos')
    
    def validate_home_phone(self, home_phone):
        """Validar telefone residencial"""
        if not self._validate_phone_format(home_phone.data):
            raise ValidationError('Telefone residencial deve ter 10 ou 11 dígitos')
    
    def validate_cell_phone(self, cell_phone):
        """Validar telefone celular"""
        if not self._validate_phone_format(cell_phone.data):
            raise ValidationError('Telefone celular deve ter 10 ou 11 dígitos')
    
    def validate_contact_phone(self, contact_phone):
        """Validar telefone de contato"""
        if not self._validate_phone_format(contact_phone.data):
            raise ValidationError('Telefone de contato deve ter 10 ou 11 dígitos')
    
    def _validate_cpf_algorithm(self, cpf):
        """Validar algoritmo do CPF"""
        # Verificar se todos os dígitos são iguais
        if len(set(cpf)) == 1:
            return False
        
        # Calcular primeiro dígito verificador
        sum1 = sum(int(cpf[i]) * (10 - i) for i in range(9))
        digit1 = 11 - (sum1 % 11)
        if digit1 >= 10:
            digit1 = 0
        
        # Calcular segundo dígito verificador
        sum2 = sum(int(cpf[i]) * (11 - i) for i in range(10))
        digit2 = 11 - (sum2 % 11)
        if digit2 >= 10:
            digit2 = 0
        
        # Verificar se os dígitos verificadores estão corretos
        return int(cpf[9]) == digit1 and int(cpf[10]) == digit2

# ✅ FORMULÁRIO DE CONFIGURAÇÃO E-SUS
class ESUSConfigForm(FlaskForm):
    dbname = StringField('Nome do Banco de Dados', validators=[
        DataRequired(message='Campo obrigatório'),
        Length(min=1, max=100)
    ], render_kw={'class': 'form-control', 'placeholder': 'esus_pec'})
    
    user = StringField('Usuário do Banco', validators=[
        DataRequired(message='Campo obrigatório'),
        Length(min=1, max=100)
    ], render_kw={'class': 'form-control', 'placeholder': 'postgres'})
    
    password = PasswordField('Senha do Banco', validators=[
        DataRequired(message='Campo obrigatório'),
        Length(min=1, max=100)
    ], render_kw={'class': 'form-control'})
    
    host = StringField('Host do Servidor', validators=[
        DataRequired(message='Campo obrigatório'),
        Length(min=1, max=255)
    ], render_kw={'class': 'form-control', 'placeholder': 'localhost'})
    
    port = IntegerField('Porta', validators=[
        DataRequired(message='Campo obrigatório'),
        NumberRange(min=1, max=65535)
    ], render_kw={'class': 'form-control', 'placeholder': '5432'})
    
    municipio = StringField('Código do Município (IBGE)', validators=[
        Optional(),
        Length(max=7)
    ], render_kw={'class': 'form-control', 'placeholder': 'Ex: 3513801'})
    
    submit = SubmitField('Salvar Configuração', render_kw={'class': 'btn btn-primary'})

# =================== FORMULÁRIOS DE MEDICAMENTOS ===================

class MedicationForm(FlaskForm):
    commercial_name = StringField('Nome Comercial', validators=[
        DataRequired(message='Campo obrigatório'),
        Length(min=2, max=100)
    ], render_kw={'class': 'form-control'})
    
    generic_name = StringField('Nome Genérico/Princípio Ativo', validators=[
        DataRequired(message='Campo obrigatório'),
        Length(min=2, max=100)
    ], render_kw={'class': 'form-control'})
    
    dosage = StringField('Dosagem', validators=[
        DataRequired(message='Campo obrigatório'),
        Length(max=50)
    ], render_kw={'class': 'form-control', 'placeholder': 'Ex: 500mg, 20mg/ml'})
    
    pharmaceutical_form = SelectField('Forma Farmacêutica', choices=PHARMACEUTICAL_FORMS, 
                                     validators=[DataRequired()], render_kw={'class': 'form-select'})
    
    medication_type = SelectField('Tipo de Medicamento', choices=[
        ('basic', 'Básico'),
        ('controlled', 'Controlado'),
        ('high_cost', 'Alto Custo'),
        ('psychotropic', 'Psicotrópico')
    ], validators=[DataRequired()], render_kw={'class': 'form-select'})
    
    requires_prescription = BooleanField('Requer Prescrição', default=True, 
                                       render_kw={'class': 'form-check-input'})
    
    controlled_substance = BooleanField('Substância Controlada', default=False,
                                      render_kw={'class': 'form-check-input'})
    
    current_stock = IntegerField('Estoque Atual', validators=[
        DataRequired(message='Campo obrigatório'),
        NumberRange(min=0, message='Estoque não pode ser negativo')
    ], render_kw={'class': 'form-control'})
    
    minimum_stock = IntegerField('Estoque Mínimo', validators=[
        DataRequired(message='Campo obrigatório'),
        NumberRange(min=1, message='Estoque mínimo deve ser maior que zero')
    ], render_kw={'class': 'form-control'})
    
    unit_cost = DecimalField('Custo Unitário (R$)', validators=[
        Optional(),
        NumberRange(min=0, message='Custo não pode ser negativo')
    ], render_kw={'class': 'form-control', 'step': '0.01'})
    
    batch_number = StringField('Número do Lote', validators=[
        Optional(),
        Length(max=50)
    ], render_kw={'class': 'form-control'})
    
    expiry_date = DateField('Data de Validade', validators=[
        Optional()
    ], render_kw={'class': 'form-control'})
    
    submit = SubmitField('Salvar Medicamento', render_kw={'class': 'btn btn-primary'})

# ✅ =================== FORMULÁRIOS DE CÁLCULOS FARMACOLÓGICOS ===================

class MedicationDispensingForm(FlaskForm):
    """Formulário para configurar cálculos de dispensação"""
    medication_id = HiddenField('Medication ID', validators=[DataRequired()])
    
    # ✅ CONCENTRAÇÃO/DOSAGEM
    strength_value = ConcentrationField('Concentração/Dosagem', validators=[
        DataRequired(message='Campo obrigatório'),
        NumberRange(min=0.001, message='Concentração deve ser maior que zero')
    ], render_kw={'class': 'form-control', 'placeholder': 'Ex: 250', 'step': '0.001'})
    
    strength_unit = SelectField('Unidade da Concentração', choices=STRENGTH_UNITS, 
                               validators=[DataRequired()], render_kw={'class': 'form-select'})
    
    # ✅ VOLUME/QUANTIDADE POR UNIDADE
    volume_per_dose = ConcentrationField('Volume/Quantidade por Unidade', validators=[
        DataRequired(message='Campo obrigatório'),
        NumberRange(min=0.001, message='Volume deve ser maior que zero')
    ], render_kw={'class': 'form-control', 'placeholder': 'Ex: 5', 'step': '0.001'})
    
    volume_unit = SelectField('Unidade do Volume', choices=VOLUME_UNITS, 
                             validators=[DataRequired()], render_kw={'class': 'form-select'})
    
    # ✅ EMBALAGEM
    package_size = ConcentrationField('Tamanho da Embalagem', validators=[
        DataRequired(message='Campo obrigatório'),
        NumberRange(min=0.001, message='Tamanho deve ser maior que zero')
    ], render_kw={'class': 'form-control', 'placeholder': 'Ex: 150', 'step': '0.001'})
    
    package_unit = SelectField('Unidade da Embalagem', choices=VOLUME_UNITS, 
                              validators=[DataRequired()], render_kw={'class': 'form-select'})
    
    # ✅ CONFIGURAÇÕES ESPECIAIS
    drops_per_ml = IntegerField('Gotas por ml (se líquido)', validators=[
        Optional(),
        NumberRange(min=10, max=50, message='Deve estar entre 10 e 50 gotas/ml')
    ], render_kw={'class': 'form-control', 'value': '20', 'placeholder': '20'})
    
    stability_days = IntegerField('Estabilidade após aberto (dias)', validators=[
        Optional(),
        NumberRange(min=1, max=365, message='Deve estar entre 1 e 365 dias')
    ], render_kw={'class': 'form-control', 'placeholder': 'Ex: 7'})
    
    is_active = BooleanField('Configuração Ativa', default=True, render_kw={'class': 'form-check-input'})
    
    submit = SubmitField('Salvar Configuração', render_kw={'class': 'btn btn-primary'})
    
    def validate_package_unit(self, package_unit):
        """Validar se unidade da embalagem é compatível com volume"""
        if self.volume_unit.data and package_unit.data:
            # Volume e embalagem devem ter unidades compatíveis
            liquid_units = ['ml']
            solid_units = ['comp', 'cap', 'ampola', 'frasco', 'sachê']
            
            vol_is_liquid = self.volume_unit.data in liquid_units
            pack_is_liquid = package_unit.data in liquid_units
            
            if vol_is_liquid != pack_is_liquid:
                raise ValidationError('Unidade da embalagem deve ser compatível com unidade do volume')

class CalculationTestForm(FlaskForm):
    """Formulário para testar cálculos de dispensação"""
    medication_id = HiddenField('Medication ID', validators=[DataRequired()])
    
    # ✅ DADOS DA PRESCRIÇÃO
    prescribed_dose = ConcentrationField('Dose Prescrita', validators=[
        DataRequired(message='Campo obrigatório'),
        NumberRange(min=0.001, message='Dose deve ser maior que zero')
    ], render_kw={'class': 'form-control', 'placeholder': 'Ex: 500', 'step': '0.001'})
    
    prescribed_unit = SelectField('Unidade Prescrita', choices=STRENGTH_UNITS, 
                                 validators=[DataRequired()], render_kw={'class': 'form-select'})
    
    frequency_per_day = SelectField('Frequência por Dia', choices=FREQUENCY_CHOICES, 
                                   validators=[DataRequired()], render_kw={'class': 'form-select'})
    
    treatment_days = IntegerField('Dias de Tratamento', validators=[
        DataRequired(message='Campo obrigatório'),
        NumberRange(min=1, max=365, message='Deve estar entre 1 e 365 dias')
    ], render_kw={'class': 'form-control', 'placeholder': 'Ex: 7'})
    
    submit = SubmitField('Calcular', render_kw={'class': 'btn btn-primary'})

class QuickCalculationForm(FlaskForm):
    """Formulário para cálculo rápido sem configuração prévia"""
    
    # ✅ DADOS DO MEDICAMENTO
    strength_value = ConcentrationField('Concentração Disponível', validators=[
        DataRequired(message='Campo obrigatório'),
        NumberRange(min=0.001, message='Concentração deve ser maior que zero')
    ], render_kw={'class': 'form-control', 'placeholder': 'Ex: 250'})
    
    strength_unit = SelectField('Unidade', choices=STRENGTH_UNITS, 
                               validators=[DataRequired()], render_kw={'class': 'form-select'})
    
    volume_per_dose = ConcentrationField('Volume por Dose', validators=[
        DataRequired(message='Campo obrigatório'),
        NumberRange(min=0.001, message='Volume deve ser maior que zero')
    ], render_kw={'class': 'form-control', 'placeholder': 'Ex: 5'})
    
    volume_unit = SelectField('Unidade', choices=VOLUME_UNITS, 
                             validators=[DataRequired()], render_kw={'class': 'form-select'})
    
    # ✅ DADOS DA PRESCRIÇÃO
    prescribed_dose = ConcentrationField('Dose Prescrita', validators=[
        DataRequired(message='Campo obrigatório'),
        NumberRange(min=0.001, message='Dose deve ser maior que zero')
    ], render_kw={'class': 'form-control', 'placeholder': 'Ex: 500'})
    
    prescribed_unit = SelectField('Unidade', choices=STRENGTH_UNITS, 
                                 validators=[DataRequired()], render_kw={'class': 'form-select'})
    
    frequency_per_day = SelectField('Frequência', choices=FREQUENCY_CHOICES, 
                                   validators=[DataRequired()], render_kw={'class': 'form-select'})
    
    treatment_days = IntegerField('Dias de Tratamento', validators=[
        DataRequired(message='Campo obrigatório'),
        NumberRange(min=1, max=365, message='Entre 1 e 365 dias')
    ], render_kw={'class': 'form-control', 'placeholder': 'Ex: 7'})
    
    # ✅ EMBALAGEM
    package_size = ConcentrationField('Tamanho da Embalagem', validators=[
        DataRequired(message='Campo obrigatório'),
        NumberRange(min=0.001, message='Tamanho deve ser maior que zero')
    ], render_kw={'class': 'form-control', 'placeholder': 'Ex: 150'})
    
    submit = SubmitField('Calcular Dispensação', render_kw={'class': 'btn btn-success'})

class StockEntryForm(FlaskForm):
    medication_id = SelectField('Medicamento', coerce=int, validators=[
        DataRequired(message='Selecione um medicamento')
    ], render_kw={'class': 'form-select'})
    
    quantity = IntegerField('Quantidade a Adicionar', validators=[
        DataRequired(message='Campo obrigatório'),
        NumberRange(min=1, message='Quantidade deve ser maior que zero')
    ], render_kw={'class': 'form-control'})
    
    unit_cost = DecimalField('Custo Unitário (R$)', validators=[
        Optional(),
        NumberRange(min=0, message='Custo não pode ser negativo')
    ], render_kw={'class': 'form-control', 'step': '0.01'})
    
    batch_number = StringField('Número do Lote', validators=[
        DataRequired(message='Campo obrigatório'),
        Length(min=3, max=50)
    ], render_kw={'class': 'form-control'})
    
    expiry_date = DateField('Data de Validade', validators=[
        DataRequired(message='Campo obrigatório')
    ], render_kw={'class': 'form-control'})
    
    reason = TextAreaField('Observações', validators=[
        Optional(),
        Length(max=200)
    ], render_kw={'class': 'form-control', 'rows': 3})
    
    submit = SubmitField('Registrar Entrada', render_kw={'class': 'btn btn-success'})
    
    def validate_expiry_date(self, expiry_date):
        if expiry_date.data and expiry_date.data <= date.today():
            raise ValidationError('Data de validade deve ser futura')

# =================== FORMULÁRIOS DE DISPENSAÇÃO ===================

class DispensationForm(FlaskForm):
    patient_search = StringField('Buscar Paciente (CPF ou Nome)', validators=[
        DataRequired(message='Campo obrigatório')
    ], render_kw={'class': 'form-control', 'placeholder': 'Digite CPF ou nome do paciente'})
    
    patient_id = HiddenField('Patient ID')
    prescription_id = HiddenField('Prescription ID')
    
    observations = TextAreaField('Observações', validators=[
        Optional(),
        Length(max=500)
    ], render_kw={'class': 'form-control', 'rows': 3})
    
    submit = SubmitField('Confirmar Dispensação', render_kw={'class': 'btn btn-success'})

# =================== FORMULÁRIOS ALTO CUSTO ===================

class HighCostRequestForm(FlaskForm):
    patient_id = HiddenField('Patient ID', validators=[DataRequired()])
    
    medication_id = SelectField('Medicamento Alto Custo', coerce=int, validators=[
        DataRequired(message='Selecione um medicamento')
    ], render_kw={'class': 'form-select'})
    
    cid10 = StringField('CID-10', validators=[
        DataRequired(message='Campo obrigatório'),
        Length(min=3, max=10),
        Regexp(r'^[A-Z]\d{2}(\.\d)?$', message='Formato inválido (ex: A10.1)')
    ], render_kw={'class': 'form-control', 'placeholder': 'Ex: E10.9'})
    
    diagnosis = TextAreaField('Diagnóstico Detalhado', validators=[
        DataRequired(message='Campo obrigatório'),
        Length(min=10, max=1000)
    ], render_kw={'class': 'form-control', 'rows': 4})
    
    doctor_name = StringField('Nome do Médico', validators=[
        DataRequired(message='Campo obrigatório'),
        Length(min=2, max=100)
    ], render_kw={'class': 'form-control'})
    
    doctor_crm = StringField('CRM do Médico', validators=[
        DataRequired(message='Campo obrigatório'),
        Length(min=5, max=20)
    ], render_kw={'class': 'form-control'})
    
    requested_quantity = IntegerField('Quantidade Solicitada', validators=[
        DataRequired(message='Campo obrigatório'),
        NumberRange(min=1, message='Quantidade deve ser maior que zero')
    ], render_kw={'class': 'form-control'})
    
    treatment_duration = IntegerField('Duração do Tratamento (dias)', validators=[
        Optional(),
        NumberRange(min=1, max=365)
    ], render_kw={'class': 'form-control'})
    
    justification = TextAreaField('Justificativa Médica', validators=[
        DataRequired(message='Campo obrigatório'),
        Length(min=20, max=2000)
    ], render_kw={'class': 'form-control', 'rows': 6})
    
    urgency_level = SelectField('Nível de Urgência', choices=URGENCY_LEVELS, 
                               default='media', validators=[DataRequired()], render_kw={'class': 'form-select'})
    
    # Documentos
    prescription_file = FileField('Receita Médica (PDF/Imagem)', validators=[
        FileRequired(message='Receita é obrigatória'),
        FileAllowed(['pdf', 'png', 'jpg', 'jpeg'], 'Apenas PDF ou imagens')
    ])
    
    medical_report_file = FileField('Laudo Médico (PDF)', validators=[
        Optional(),
        FileAllowed(['pdf'], 'Apenas arquivos PDF')
    ])
    
    exam_file = FileField('Exames (PDF/Imagem)', validators=[
        Optional(),
        FileAllowed(['pdf', 'png', 'jpg', 'jpeg'], 'Apenas PDF ou imagens')
    ])
    
    submit = SubmitField('Enviar Solicitação', render_kw={'class': 'btn btn-primary'})

class PharmaceuticalEvaluationForm(FlaskForm):
    process_id = HiddenField('Process ID', validators=[DataRequired()])
    
    technical_opinion = TextAreaField('Parecer Técnico', validators=[
        DataRequired(message='Campo obrigatório'),
        Length(min=20, max=2000)
    ], render_kw={'class': 'form-control', 'rows': 6})
    
    meets_protocol = SelectField('Atende ao Protocolo?', choices=[
        ('', 'Selecione...'),
        ('1', 'Sim'),
        ('0', 'Não')
    ], validators=[DataRequired()], render_kw={'class': 'form-select'})
    
    recommended_quantity = IntegerField('Quantidade Recomendada', validators=[
        Optional(),
        NumberRange(min=1)
    ], render_kw={'class': 'form-control'})
    
    recommended_duration = IntegerField('Duração Recomendada (dias)', validators=[
        Optional(),
        NumberRange(min=1, max=365)
    ], render_kw={'class': 'form-control'})
    
    recommendation = SelectField('Recomendação', choices=[
        ('', 'Selecione...'),
        ('approve', 'Aprovar'),
        ('deny', 'Negar'),
        ('request_more_info', 'Solicitar Mais Informações')
    ], validators=[DataRequired()], render_kw={'class': 'form-select'})
    
    observations = TextAreaField('Observações', validators=[
        Optional(),
        Length(max=1000)
    ], render_kw={'class': 'form-control', 'rows': 4})
    
    submit = SubmitField('Salvar Avaliação', render_kw={'class': 'btn btn-primary'})

class ProcessApprovalForm(FlaskForm):
    process_id = HiddenField('Process ID', validators=[DataRequired()])
    
    decision = SelectField('Decisão', choices=[
        ('', 'Selecione...'),
        ('approved', 'Aprovado'),
        ('denied', 'Negado')
    ], validators=[DataRequired()], render_kw={'class': 'form-select'})
    
    approved_quantity = IntegerField('Quantidade Aprovada', validators=[
        Optional(),
        NumberRange(min=1)
    ], render_kw={'class': 'form-control'})
    
    approved_duration = IntegerField('Duração Aprovada (dias)', validators=[
        Optional(),
        NumberRange(min=1, max=365)
    ], render_kw={'class': 'form-control'})
    
    justification = TextAreaField('Justificativa da Decisão', validators=[
        DataRequired(message='Campo obrigatório'),
        Length(min=10, max=1000)
    ], render_kw={'class': 'form-control', 'rows': 5})
    
    special_conditions = TextAreaField('Condições Especiais', validators=[
        Optional(),
        Length(max=500)
    ], render_kw={'class': 'form-control', 'rows': 3})
    
    approval_expires_at = DateField('Aprovação Válida Até', validators=[
        Optional()
    ], render_kw={'class': 'form-control'})
    
    submit = SubmitField('Confirmar Decisão', render_kw={'class': 'btn btn-primary'})

class HighCostDispensationForm(FlaskForm):
    process_id = HiddenField('Process ID', validators=[DataRequired()])
    
    quantity_dispensed = IntegerField('Quantidade a Dispensar', validators=[
        DataRequired(message='Campo obrigatório'),
        NumberRange(min=1)
    ], render_kw={'class': 'form-control'})
    
    next_dispensation_date = DateField('Próxima Dispensação', validators=[
        Optional()
    ], render_kw={'class': 'form-control'})
    
    patient_signature = BooleanField('Paciente Assinou Termo', validators=[
        DataRequired(message='Confirmação obrigatória')
    ], render_kw={'class': 'form-check-input'})
    
    terms_accepted = BooleanField('Termos de Responsabilidade Aceitos', validators=[
        DataRequired(message='Confirmação obrigatória')
    ], render_kw={'class': 'form-check-input'})
    
    observations = TextAreaField('Observações da Dispensação', validators=[
        Optional(),
        Length(max=500)
    ], render_kw={'class': 'form-control', 'rows': 3})
    
    submit = SubmitField('Confirmar Dispensação', render_kw={'class': 'btn btn-success'})

# =================== FORMULÁRIOS DE RELATÓRIOS ===================

class ReportForm(FlaskForm):
    report_type = SelectField('Tipo de Relatório', choices=[
        ('', 'Selecione...')
    ] + REPORT_TYPES, validators=[DataRequired()], render_kw={'class': 'form-select'})
    
    start_date = DateField('Data Inicial', validators=[
        Optional()
    ], render_kw={'class': 'form-control'})
    
    end_date = DateField('Data Final', validators=[
        Optional()
    ], render_kw={'class': 'form-control'})
    
    format_type = SelectField('Formato', choices=FORMAT_TYPES, 
                             default='html', validators=[DataRequired()], render_kw={'class': 'form-select'})
    
    # ✅ FILTROS ESPECÍFICOS PARA RELATÓRIO DE PACIENTES
    age_range = SelectField('Faixa Etária', choices=AGE_RANGE_CHOICES, 
                           validators=[Optional()], render_kw={'class': 'form-select'})
    
    gender = SelectField('Sexo', choices=GENERO_CHOICES, 
                        validators=[Optional()], render_kw={'class': 'form-select'})
    
    registration_period = SelectField('Período de Cadastro', choices=REGISTRATION_PERIOD_CHOICES, 
                                    validators=[Optional()], render_kw={'class': 'form-select'})
    
    submit = SubmitField('Gerar Relatório', render_kw={'class': 'btn btn-primary'})
    
    def validate_end_date(self, end_date):
        if self.start_date.data and end_date.data:
            if end_date.data < self.start_date.data:
                raise ValidationError('Data final deve ser posterior à data inicial')

# =================== FORMULÁRIOS DE BUSCA ===================

class SearchForm(FlaskForm):
    search_type = SelectField('Buscar por', choices=[
        ('name', 'Nome'),
        ('cpf', 'CPF'),
        ('cns', 'CNS')
    ], default='name', render_kw={'class': 'form-select'})
    
    search_term = StringField('Termo de Busca', validators=[
        DataRequired(message='Campo obrigatório')
    ], render_kw={'class': 'form-control', 'placeholder': 'Digite sua busca...'})
    
    submit = SubmitField('Buscar', render_kw={'class': 'btn btn-outline-primary'})

class MedicationSearchForm(FlaskForm):
    search_term = StringField('Buscar Medicamento', validators=[
        Optional()
    ], render_kw={'class': 'form-control', 'placeholder': 'Nome comercial ou genérico...'})
    
    medication_type = SelectField('Tipo', choices=[
        ('', 'Todos'),
        ('basic', 'Básico'),
        ('controlled', 'Controlado'),
        ('high_cost', 'Alto Custo'),
        ('psychotropic', 'Psicotrópico')
    ], render_kw={'class': 'form-select'})
    
    low_stock_only = BooleanField('Apenas Estoque Baixo', render_kw={'class': 'form-check-input'})
    
    submit = SubmitField('Filtrar', render_kw={'class': 'btn btn-outline-primary'})

# ✅ FORMULÁRIO DE BUSCA INTEGRADA E-SUS
class IntegratedSearchForm(FlaskForm):
    search_term = StringField('Buscar Paciente', validators=[
        DataRequired(message='Campo obrigatório'),
        Length(min=3, message='Digite pelo menos 3 caracteres')
    ], render_kw={'class': 'form-control', 'placeholder': 'CPF, CNS ou Nome do paciente...'})
    
    search_type = SelectField('Buscar em', choices=[
        ('all', 'Local + e-SUS'),
        ('local', 'Apenas Local'),
        ('esus', 'Apenas e-SUS')
    ], default='all', render_kw={'class': 'form-select'})
    
    submit = SubmitField('Buscar', render_kw={'class': 'btn btn-primary'})

# ✅ FORMULÁRIO DE PRESCRIÇÃO
class PrescriptionForm(FlaskForm):
    patient_id = HiddenField('Patient ID', validators=[DataRequired()])
    
    doctor_name = StringField('Nome do Médico', validators=[
        DataRequired(message='Campo obrigatório'),
        Length(min=2, max=100)
    ], render_kw={'class': 'form-control'})
    
    doctor_crm = StringField('CRM', validators=[
        DataRequired(message='Campo obrigatório'),
        Length(min=5, max=20)
    ], render_kw={'class': 'form-control', 'placeholder': 'Ex: 123456/SP'})
    
    doctor_specialty = StringField('Especialidade', validators=[
        Optional(),
        Length(max=100)
    ], render_kw={'class': 'form-control'})
    
    prescription_date = DateField('Data da Prescrição', validators=[
        DataRequired(message='Campo obrigatório')
    ], render_kw={'class': 'form-control'})
    
    diagnosis = TextAreaField('Diagnóstico/CID', validators=[
        Optional(),
        Length(max=500)
    ], render_kw={'class': 'form-control', 'rows': 3})
    
    observations = TextAreaField('Observações', validators=[
        Optional(),
        Length(max=500)
    ], render_kw={'class': 'form-control', 'rows': 3})
    
    submit = SubmitField('Salvar Prescrição', render_kw={'class': 'btn btn-primary'})

# ✅ FORMULÁRIO DE ITEM DE DISPENSAÇÃO
class DispensationItemForm(FlaskForm):
    medication_id = SelectField('Medicamento', coerce=int, validators=[
        DataRequired(message='Selecione um medicamento')
    ], render_kw={'class': 'form-select'})
    
    quantity_dispensed = IntegerField('Quantidade', validators=[
        DataRequired(message='Campo obrigatório'),
        NumberRange(min=1, message='Quantidade deve ser maior que zero')
    ], render_kw={'class': 'form-control'})
    
    observations = TextAreaField('Observações', validators=[
        Optional(),
        Length(max=200)
    ], render_kw={'class': 'form-control', 'rows': 2})

# ✅ FORMULÁRIO PARA FILTRO AVANÇADO DE RELATÓRIOS
class AdvancedReportFilterForm(FlaskForm):
    # Filtros de data
    start_date = DateField('Data Inicial', validators=[Optional()], render_kw={'class': 'form-control'})
    end_date = DateField('Data Final', validators=[Optional()], render_kw={'class': 'form-control'})
    
    # Filtros de usuário
    user_id = SelectField('Usuário', coerce=int, validators=[Optional()], render_kw={'class': 'form-select'})
    
    # Filtros de medicamento
    medication_type = SelectField('Tipo de Medicamento', choices=[
        ('', 'Todos'),
        ('basic', 'Básico'),
        ('controlled', 'Controlado'),
        ('high_cost', 'Alto Custo'),
        ('psychotropic', 'Psicotrópico')
    ], validators=[Optional()], render_kw={'class': 'form-select'})
    
    # Filtros de status
    process_status = SelectField('Status do Processo', choices=[
        ('', 'Todos'),
        ('pending', 'Pendente'),
        ('under_evaluation', 'Em Avaliação'),
        ('approved', 'Aprovado'),
        ('denied', 'Negado'),
        ('dispensed', 'Dispensado')
    ], validators=[Optional()], render_kw={'class': 'form-select'})
    
    submit = SubmitField('Aplicar Filtros', render_kw={'class': 'btn btn-outline-primary'})