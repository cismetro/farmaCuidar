from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify, send_file, current_app
from flask_login import login_user, logout_user, login_required, current_user
from urllib.parse import urlparse
from sqlalchemy import or_, and_, desc, extract, func
from sqlalchemy.orm import joinedload
from datetime import datetime, date, timedelta
from decimal import Decimal
import os
import json
import re

# Imports para geração de relatórios
from io import BytesIO, StringIO
import pandas as pd
from reportlab.lib.pagesizes import letter, A4
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib import colors
import csv

# ✅ ADICIONAR NO TOPO DO routes.py:
from app.calculations import (
    validate_medication_configuration,
    calculate_from_database, 
    calculate_complete_dispensation,
    validate_calculation_inputs,
    get_supported_units,
    convert_units,
    format_calculation_result
)

from app.database import db, login_manager
from app.models import *
from app.forms import *
from app.auth import *
from app.utils import *

# ✅ IMPORTAR FUNÇÕES E-SUS
from app.esus_integration import (
    get_esus_db_credentials, save_esus_credentials, 
    test_esus_connection, search_patient_in_esus,
    get_esus_statistics, format_esus_data_for_display,
    get_esus_patient_by_cpf, get_esus_patient_by_cns
)

# Blueprint principal
main = Blueprint('main', __name__)

# User loader para Flask-Login
@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# Context processor para injetar dados nos templates
@main.context_processor
def inject_template_vars():
    from app.auth import has_permission
    
    def calculate_age(birth_date):
        if not birth_date:
            return 0
        from datetime import date
        today = date.today()
        return today.year - birth_date.year - ((today.month, today.day) < (birth_date.month, birth_date.day))
    
    def format_process_status(status):
        status_map = {
            'pending': 'Aguardando Avaliação',
            'under_evaluation': 'Em Avaliação',
            'approved': 'Aprovado',
            'denied': 'Rejeitado',
            'dispensed': 'Dispensado',
            'completed': 'Concluído',
            'cancelled': 'Cancelado'
        }
        return status_map.get(status.value if hasattr(status, 'value') else status, status)
    
    return {
        'system_name': current_app.config.get('SYSTEM_NAME', 'FarmaCuidar'),
        'municipality': current_app.config.get('MUNICIPALITY', 'Cosmópolis - SP'),
        'format_currency': format_currency,
        'format_date': format_date,
        'format_datetime': format_datetime,
        'format_user_role': format_user_role,
        'calculate_age': calculate_age,
        'format_process_status': format_process_status,
        'get_system_stats': get_system_stats if current_user.is_authenticated else lambda: {},
        'current_year': datetime.now().year,
        'current_date': date.today(),
        'has_permission': lambda perm: has_permission(perm) if current_user.is_authenticated else False
    }

# =================== ROTAS DE AUTENTICAÇÃO ===================

@main.route('/')
def index():
    """Página inicial - redireciona para login ou dashboard"""
    if current_user.is_authenticated:
        return redirect(url_for('main.dashboard'))
    return redirect(url_for('main.login'))

@main.route('/login', methods=['GET', 'POST'])
def login():
    """Página de login"""
    if current_user.is_authenticated:
        return redirect(url_for('main.dashboard'))
    
    form = LoginForm()
    
    if form.validate_on_submit():
        # Verificar rate limiting
        if not check_login_attempts(request.remote_addr):
            flash('Muitas tentativas de login. Tente novamente em 15 minutos.', 'error')
            return render_template('login.html', form=form)
        
        # Buscar usuário
        user = User.query.filter_by(username=form.username.data).first()
        
        if user and user.check_password(form.password.data):
            if not user.is_active:
                flash('Conta desativada. Entre em contato com o administrador.', 'error')
                return render_template('login.html', form=form)
            
            # Login bem-sucedido
            login_user(user, remember=form.remember_me.data)
            update_last_login(user)
            reset_login_attempts(request.remote_addr)
            init_user_session(user)
            
            flash(f'Bem-vindo, {user.full_name}!', 'success')
            
            # Redirect para próxima página ou dashboard
            next_page = request.args.get('next')
            if not next_page or urlparse(next_page).netloc != '':
                next_page = url_for('main.dashboard')
            
            return redirect(next_page)
        else:
            flash('Usuário ou senha incorretos.', 'error')
    
    return render_template('login.html', form=form)

@main.route('/logout')
@login_required
def logout():
    """Logout do usuário"""
    log_action('LOGOUT', 'users', current_user.id)
    clear_user_session()
    logout_user()
    flash('Logout realizado com sucesso.', 'info')
    return redirect(url_for('main.login'))

@main.route('/change-password', methods=['GET', 'POST'])
@login_required
def change_password():
    """Alterar senha do usuário"""
    form = ChangePasswordForm()
    
    if form.validate_on_submit():
        if not current_user.check_password(form.current_password.data):
            flash('Senha atual incorreta.', 'error')
            return render_template('auth/change_password.html', form=form)
        
        # Validar nova senha
        password_errors = validate_password(form.new_password.data)
        if password_errors:
            for error in password_errors:
                flash(error, 'error')
            return render_template('auth/change_password.html', form=form)
        
        # Alterar senha
        current_user.set_password(form.new_password.data)
        db.session.commit()
        
        log_action('CHANGE_PASSWORD', 'users', current_user.id)
        flash('Senha alterada com sucesso.', 'success')
        return redirect(url_for('main.dashboard'))
    
    return render_template('auth/change_password.html', form=form)

# =================== DASHBOARDS ===================

@main.route('/dashboard')
@login_required
def dashboard():
    """Dashboard principal - redireciona para dashboard específico do usuário"""
    if current_user.role.value == 'admin':
        return redirect(url_for('main.admin_dashboard'))
    elif current_user.role.value == 'pharmacist':
        return redirect(url_for('main.pharmacist_dashboard'))
    else:
        return redirect(url_for('main.attendant_dashboard'))

@main.route('/dashboard/admin')
@admin_required
def admin_dashboard():
    """Dashboard do administrador"""
    # Estatísticas gerais
    stats = {
        'total_patients': Patient.query.filter_by(is_active=True).count(),
        'total_users': User.query.filter_by(is_active=True).count(),
        'total_medications': Medication.query.filter_by(is_active=True).count(),
        'pending_high_cost': HighCostProcess.query.filter_by(status=ProcessStatus.PENDING).count(),
        'low_stock_medications': Medication.query.filter(
            Medication.current_stock <= Medication.minimum_stock
        ).count(),
        'near_expiry_medications': Medication.query.filter(
            and_(
                Medication.expiry_date.isnot(None),
                Medication.expiry_date <= date.today() + timedelta(days=30)
            )
        ).count(),
        # ✅ ESTATÍSTICAS E-SUS
        'patients_from_esus': Patient.query.filter_by(source='imported').count(),
        'patients_synced_esus': Patient.query.filter(Patient.esus_sync_date.isnot(None)).count()
    }
    
    # Dispensações dos últimos 7 dias
    week_ago = datetime.now() - timedelta(days=7)
    recent_dispensations = Dispensation.query.filter(
        Dispensation.dispensation_date >= week_ago
    ).order_by(desc(Dispensation.dispensation_date)).limit(10).all()
    
    # Processos alto custo recentes
    recent_high_cost = HighCostProcess.query.order_by(
        desc(HighCostProcess.request_date)
    ).limit(5).all()
    
    return render_template('dashboard/admin_dash.html', 
                         stats=stats, 
                         recent_dispensations=recent_dispensations,
                         recent_high_cost=recent_high_cost)

@main.route('/dashboard/pharmacist')
@pharmacist_required
def pharmacist_dashboard():
    """Dashboard do farmacêutico"""
    # Estatísticas do farmacêutico
    stats = {
        'pending_evaluations': HighCostProcess.query.filter_by(
            status=ProcessStatus.UNDER_EVALUATION
        ).count(),
        'pending_approvals': HighCostProcess.query.filter_by(
            status=ProcessStatus.PENDING
        ).count(),
        'low_stock_count': Medication.query.filter(
            Medication.current_stock <= Medication.minimum_stock
        ).count(),
        'today_dispensations': Dispensation.query.filter(
            Dispensation.dispensation_date >= date.today()
        ).count()
    }
    
    # Alertas importantes
    alerts = []
    
    # Medicamentos com estoque baixo
    low_stock = Medication.query.filter(
        Medication.current_stock <= Medication.minimum_stock
    ).limit(5).all()
    
    # Medicamentos próximos ao vencimento
    near_expiry = Medication.query.filter(
        and_(
            Medication.expiry_date.isnot(None),
            Medication.expiry_date <= date.today() + timedelta(days=30)
        )
    ).limit(5).all()
    
    # Processos alto custo pendentes de avaliação
    pending_processes = HighCostProcess.query.filter_by(
        status=ProcessStatus.PENDING
    ).order_by(HighCostProcess.request_date).limit(5).all()
    
    return render_template('dashboard/pharmacist_dash.html',
                         stats=stats,
                         low_stock=low_stock,
                         near_expiry=near_expiry,
                         pending_processes=pending_processes)

@main.route('/dashboard/attendant')
@staff_required
def attendant_dashboard():
    """Dashboard do atendente"""
    # Estatísticas do atendente
    stats = {
        'today_dispensations': Dispensation.query.filter(
            and_(
                Dispensation.dispensation_date >= date.today(),
                Dispensation.dispenser_id == current_user.id
            )
        ).count(),
        'total_patients': Patient.query.filter_by(is_active=True).count(),
        'available_medications': Medication.query.filter(
            and_(
                Medication.is_active == True,
                Medication.current_stock > 0
            )
        ).count()
    }
    
    # Dispensações recentes do usuário
    my_dispensations = Dispensation.query.filter_by(
        dispenser_id=current_user.id
    ).order_by(desc(Dispensation.dispensation_date)).limit(10).all()
    
    return render_template('dashboard/attendant_dash.html',
                         stats=stats,
                         my_dispensations=my_dispensations)

# =================== CONFIGURAÇÃO E-SUS ===================

@main.route('/admin/esus-config')
@admin_required
def esus_config():
    """Página de configuração do e-SUS"""
    # Buscar credenciais existentes
    credentials = get_esus_db_credentials()
    
    # Obter estatísticas se conectado
    stats = None
    if credentials:
        stats = get_esus_statistics()
    
    return render_template('admin/esus_config.html', 
                         credentials=credentials,
                         stats=stats,
                         title='Configuração e-SUS')

@main.route('/admin/esus-config', methods=['POST'])
@admin_required
def esus_config_save():
    """Salvar configurações do e-SUS"""
    try:
        dbname = request.form.get('dbname', '').strip()
        user = request.form.get('user', '').strip()
        password = request.form.get('password', '').strip()
        host = request.form.get('host', '').strip()
        port = request.form.get('port', '5432')
        municipio = request.form.get('municipio', '').strip()
        
        # Validar campos obrigatórios
        if not all([dbname, user, password, host]):
            flash('Todos os campos obrigatórios devem ser preenchidos.', 'error')
            return redirect(url_for('main.esus_config'))
        
        try:
            port = int(port)
        except ValueError:
            flash('Porta deve ser um número válido.', 'error')
            return redirect(url_for('main.esus_config'))
        
        # Salvar credenciais
        success, message = save_esus_credentials(dbname, user, password, host, port, municipio)
        
        if success:
            flash(message, 'success')
            log_action('UPDATE', 'esus_config', 1, new_values={'host': host, 'port': port})
        else:
            flash(message, 'error')
            
    except Exception as e:
        flash(f'Erro ao salvar configurações: {str(e)}', 'error')
    
    return redirect(url_for('main.esus_config'))

@main.route('/admin/esus-test', methods=['POST'])
@admin_required
def esus_test_connection():
    """Testar conexão com e-SUS"""
    try:
        success, message = test_esus_connection()
        return jsonify({
            'success': success,
            'message': message
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'Erro no teste: {str(e)}'
        })

# =================== GESTÃO DE PACIENTES COM E-SUS ===================

@main.route('/patients')
@staff_required
def patients_list():
    """Lista de pacientes com filtros expandidos"""
    page = request.args.get('page', 1, type=int)
    per_page = current_app.config.get('PATIENTS_PER_PAGE', 20)
    
    # ✅ FILTROS ATUALIZADOS
    search_term = request.args.get('search_term', '')
    search_type = request.args.get('search_type', 'name')
    status_filter = request.args.get('status_filter', '')
    gender_filter = request.args.get('gender_filter', '')
    
    query = Patient.query
    
    # ✅ APLICAR FILTROS DE BUSCA EXPANDIDOS
    if search_term:
        if search_type == 'name':
            query = query.filter(Patient.full_name.ilike(f'%{search_term}%'))
        elif search_type == 'cpf':
            clean_cpf = re.sub(r'[^0-9]', '', search_term)
            query = query.filter(Patient.cpf.like(f'%{clean_cpf}%'))
        elif search_type == 'cns':
            clean_cns = re.sub(r'[^0-9]', '', search_term)
            query = query.filter(Patient.cns.like(f'%{clean_cns}%'))
        elif search_type == 'phone':
            # ✅ BUSCAR EM TODOS OS TELEFONES
            clean_phone = re.sub(r'[^0-9]', '', search_term)
            query = query.filter(
                or_(
                    Patient.cell_phone.like(f'%{clean_phone}%'),
                    Patient.home_phone.like(f'%{clean_phone}%'),
                    Patient.contact_phone.like(f'%{clean_phone}%')
                )
            )
        elif search_type == 'email':
            query = query.filter(Patient.email.ilike(f'%{search_term}%'))
        elif search_type == 'mother':
            # ✅ NOVO: BUSCA POR NOME DA MÃE
            query = query.filter(Patient.mother_name.ilike(f'%{search_term}%'))
    
    # ✅ FILTROS DE STATUS E GÊNERO
    if status_filter == 'active':
        query = query.filter(Patient.is_active == True)
    elif status_filter == 'inactive':
        query = query.filter(Patient.is_active == False)
    else:
        # Se não especificado, mostrar apenas ativos por padrão
        query = query.filter(Patient.is_active == True)
    
    if gender_filter:
        query = query.filter(Patient.gender == gender_filter)
    
    patients = query.order_by(Patient.full_name).paginate(
        page=page, per_page=per_page, error_out=False
    )
    
    # ✅ ESTATÍSTICAS PARA O DASHBOARD
    today = date.today()
    today_registrations = Patient.query.filter(
        func.date(Patient.created_at) == today
    ).count()
    
    # ✅ CALCULAR ESTATÍSTICAS RÁPIDAS PARA O TEMPLATE
    try:
        total_patients = Patient.query.count()
        active_patients = Patient.query.filter_by(is_active=True).count()
        patients_with_cns = Patient.query.filter(
            and_(Patient.cns.isnot(None), Patient.is_active == True)
        ).count()
        
        quick_stats = {
            'total': total_patients,
            'active': active_patients,
            'today': today_registrations,
            'with_cns': patients_with_cns
        }
    except Exception as e:
        current_app.logger.error(f"Erro ao calcular estatísticas: {e}")
        quick_stats = {
            'total': 0,
            'active': 0,
            'today': 0,
            'with_cns': 0
        }
    
    return render_template('patients/list.html', 
                         patients=patients,
                         search_term=search_term,
                         search_type=search_type,
                         status_filter=status_filter,
                         gender_filter=gender_filter,
                         today_registrations=today_registrations,
                         quick_stats=quick_stats)

@main.route('/patients/search-integrated')
@staff_required
def patients_search_integrated():
    """✅ BUSCA INTEGRADA DE PACIENTES (LOCAL + E-SUS)"""
    search_term = request.args.get('q', '').strip()
    search_type = request.args.get('type', 'all')
    
    if not search_term:
        return render_template('patients/search_results.html', 
                             local_results=[], 
                             esus_results=[],
                             search_term='',
                             search_type='all')
    
    try:
        # Usar busca integrada do modelo Patient
        local_results, esus_results = Patient.search_integrated(search_term, search_type)
        
        return render_template('patients/search_results.html',
                             local_results=local_results,
                             esus_results=esus_results,
                             search_term=search_term,
                             search_type=search_type)
        
    except Exception as e:
        flash(f'Erro na busca: {str(e)}', 'error')
        return render_template('patients/search_results.html', 
                             local_results=[], 
                             esus_results=[],
                             search_term=search_term,
                             search_type=search_type)

@main.route('/patients/search-esus')
@staff_required
def search_esus_patients():
    """✅ BUSCAR PACIENTES NO E-SUS VIA AJAX"""
    query = request.args.get('q', '').strip()
    search_type = request.args.get('type', 'all')
    
    if not query:
        return jsonify({'success': False, 'message': 'Termo de busca é obrigatório'})
    
    try:
        raw_results = search_patient_in_esus(query, search_type)
        formatted_results = []
        
        for patient in raw_results:
            formatted_data = format_esus_data_for_display(patient)
            if formatted_data:
                formatted_results.append(formatted_data)
        
        return jsonify({
            'success': True,
            'results': formatted_results,
            'count': len(formatted_results)
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'Erro na busca: {str(e)}'
        })

@main.route('/patients/import-esus', methods=['POST'])
@staff_required
def import_esus_patient():
    """✅ IMPORTAR PACIENTE DO E-SUS"""
    try:
        data = request.get_json()
        
        if not data or 'raw_data' not in data:
            return jsonify({'success': False, 'message': 'Dados do paciente são obrigatórios'})
        
        esus_data = data['raw_data']
        
        # Importar paciente usando o método do modelo
        patient = Patient.import_from_esus(esus_data)
        
        log_action('CREATE', 'patients', patient.id, new_values={
            'source': 'imported',
            'cpf': patient.cpf,
            'full_name': patient.full_name
        })
        
        return jsonify({
            'success': True,
            'message': f'Paciente {patient.full_name} importado com sucesso',
            'patient_id': patient.id,
            'redirect_url': url_for('main.patient_view', id=patient.id)
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'Erro ao importar paciente: {str(e)}'
        })

@main.route('/patients/create', methods=['GET', 'POST'])
@staff_required
def patient_create():
    """Criar novo paciente com campos expandidos"""
    form = PatientForm()
    
    if form.validate_on_submit():
        # Limpar CPF e CNS
        clean_cpf = re.sub(r'[^0-9]', '', form.cpf.data)
        clean_cns = re.sub(r'[^0-9]', '', form.cns.data) if form.cns.data else None
        
        patient = Patient(
            cpf=clean_cpf,
            cns=clean_cns,
            full_name=form.full_name.data,
            birth_date=form.birth_date.data,
            gender=form.gender.data or None,
            # ✅ NOVOS CAMPOS FAMILIARES
            mother_name=form.mother_name.data if hasattr(form, 'mother_name') and form.mother_name.data else None,
            father_name=form.father_name.data if hasattr(form, 'father_name') and form.father_name.data else None,
            # ✅ MÚLTIPLOS TELEFONES
            cell_phone=re.sub(r'[^0-9]', '', form.cell_phone.data) if hasattr(form, 'cell_phone') and form.cell_phone.data else None,
            home_phone=re.sub(r'[^0-9]', '', form.home_phone.data) if hasattr(form, 'home_phone') and form.home_phone.data else None,
            contact_phone=re.sub(r'[^0-9]', '', form.contact_phone.data) if hasattr(form, 'contact_phone') and form.contact_phone.data else None,
            # ✅ MANTER COMPATIBILIDADE COM PHONE ANTIGO
            phone=form.phone.data if hasattr(form, 'phone') and form.phone.data else None,
            email=form.email.data,
            address=form.address.data,
            # ✅ NOVO CAMPO NÚMERO
            number=form.number.data if hasattr(form, 'number') and form.number.data else None,
            neighborhood=form.neighborhood.data,
            city=form.city.data,
            state=form.state.data,
            zip_code=re.sub(r'[^0-9]', '', form.zip_code.data) if form.zip_code.data else None,
            source='local'  # ✅ MARCAR COMO ORIGEM LOCAL
        )
        
        db.session.add(patient)
        db.session.commit()
        
        log_action('CREATE', 'patients', patient.id, new_values={
            'cpf': clean_cpf,
            'full_name': form.full_name.data,
            'source': 'local'
        })
        
        flash(f'Paciente {patient.full_name} cadastrado com sucesso.', 'success')
        
        # ✅ VERIFICAR REDIRECIONAMENTO PARA ALTO CUSTO
        redirect_to = request.args.get('redirect_to')
        if redirect_to == 'high_cost':
            return redirect(url_for('main.high_cost_request', patient_id=patient.id))
        
        return redirect(url_for('main.patient_view', id=patient.id))
    
    return render_template('patients/form.html', form=form, title='Novo Paciente')

@main.route('/patients/<int:id>')
@staff_required
def patient_view(id):
    """Visualizar paciente"""
    patient = Patient.query.get_or_404(id)
    
    # Calcular estatísticas do paciente
    try:
        # Dispensações totais
        dispensations = Dispensation.query.filter_by(patient_id=id).all()
        total_dispensations = len(dispensations)
        
        # Medicamentos únicos
        unique_meds = set()
        for disp in dispensations:
            for item in disp.items:
                unique_meds.add(item.medication_id)
        unique_medications = len(unique_meds)
        
        # Processos alto custo
        high_cost_processes_count = HighCostProcess.query.filter_by(patient_id=id).count()
        
        # Última dispensação
        last_dispensation_obj = Dispensation.query.filter_by(patient_id=id).order_by(
            desc(Dispensation.dispensation_date)
        ).first()
        
        last_dispensation_date = last_dispensation_obj.dispensation_date if last_dispensation_obj else None
        
    except Exception as e:
        print(f"Erro ao calcular estatísticas: {e}")
        total_dispensations = 0
        unique_medications = 0
        high_cost_processes_count = 0
        last_dispensation_date = None
    
    # Dispensações recentes (últimas 5)
    recent_dispensations = Dispensation.query.filter_by(patient_id=id).order_by(
        desc(Dispensation.dispensation_date)
    ).limit(5).all()
    
    # Processos alto custo (últimos 5)
    high_cost_processes = HighCostProcess.query.filter_by(patient_id=id).order_by(
        desc(HighCostProcess.request_date)
    ).limit(5).all()
    
    # Montar objeto de estatísticas
    stats = {
        'total_dispensations': total_dispensations,
        'unique_medications': unique_medications,
        'high_cost_processes': high_cost_processes_count,
        'last_dispensation': last_dispensation_date
    }
    
    return render_template('patients/view.html',
                         patient=patient,
                         stats=stats,
                         recent_dispensations=recent_dispensations,
                         high_cost_processes=high_cost_processes)

@main.route('/patients/<int:id>/sync-esus', methods=['POST'])
@staff_required
def patient_sync_esus(id):
    """✅ SINCRONIZAR PACIENTE COM E-SUS"""
    patient = Patient.query.get_or_404(id)
    
    try:
        success = patient.sync_with_esus()
        
        if success:
            log_action('UPDATE', 'patients', patient.id, new_values={
                'esus_sync_date': patient.esus_sync_date.isoformat() if patient.esus_sync_date else None
            })
            
            return jsonify({
                'success': True,
                'message': f'Paciente {patient.full_name} sincronizado com e-SUS',
                'sync_date': patient.esus_sync_date.isoformat() if patient.esus_sync_date else None
            })
        else:
            return jsonify({
                'success': False,
                'message': 'Não foi possível sincronizar com e-SUS'
            })
            
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'Erro na sincronização: {str(e)}'
        })

@main.route('/patients/<int:id>/edit', methods=['GET', 'POST'])
@staff_required
def patient_edit(id):
    """Editar paciente com campos expandidos"""
    patient = Patient.query.get_or_404(id)
    form = PatientForm(obj=patient)
    form.patient_id = id  # Para validação única
    
    if form.validate_on_submit():
        old_values = {
            'cpf': patient.cpf,
            'full_name': patient.full_name,
            'phone': getattr(patient, 'phone', None),
            'cell_phone': getattr(patient, 'cell_phone', None),
            'email': patient.email
        }
        
        # Atualizar dados básicos
        patient.cpf = re.sub(r'[^0-9]', '', form.cpf.data)
        patient.cns = re.sub(r'[^0-9]', '', form.cns.data) if form.cns.data else None
        patient.full_name = form.full_name.data
        patient.birth_date = form.birth_date.data
        patient.gender = form.gender.data or None
        
        # ✅ ATUALIZAR NOVOS CAMPOS FAMILIARES
        if hasattr(form, 'mother_name'):
            patient.mother_name = form.mother_name.data
        if hasattr(form, 'father_name'):
            patient.father_name = form.father_name.data
            
        # ✅ ATUALIZAR MÚLTIPLOS TELEFONES
        if hasattr(form, 'cell_phone'):
            patient.cell_phone = re.sub(r'[^0-9]', '', form.cell_phone.data) if form.cell_phone.data else None
        if hasattr(form, 'home_phone'):
            patient.home_phone = re.sub(r'[^0-9]', '', form.home_phone.data) if form.home_phone.data else None
        if hasattr(form, 'contact_phone'):
            patient.contact_phone = re.sub(r'[^0-9]', '', form.contact_phone.data) if form.contact_phone.data else None
            
        # ✅ MANTER PHONE ANTIGO PARA COMPATIBILIDADE
        if hasattr(form, 'phone'):
            patient.phone = form.phone.data
            
        patient.email = form.email.data
        patient.address = form.address.data
        
        # ✅ ATUALIZAR NÚMERO DO ENDEREÇO
        if hasattr(form, 'number'):
            patient.number = form.number.data
            
        patient.neighborhood = form.neighborhood.data
        patient.city = form.city.data
        patient.state = form.state.data
        patient.zip_code = re.sub(r'[^0-9]', '', form.zip_code.data) if form.zip_code.data else None
        patient.updated_at = datetime.utcnow()
        
        db.session.commit()
        
        new_values = {
            'cpf': patient.cpf,
            'full_name': patient.full_name,
            'phone': getattr(patient, 'phone', None),
            'cell_phone': getattr(patient, 'cell_phone', None),
            'email': patient.email
        }
        
        log_action('UPDATE', 'patients', patient.id, 
                  old_values=old_values,
                  new_values=new_values)
        
        flash(f'Paciente {patient.full_name} atualizado com sucesso.', 'success')
        return redirect(url_for('main.patient_view', id=patient.id))
    
    return render_template('patients/form.html', form=form, title='Editar Paciente', patient=patient)

@main.route('/patients/<int:id>/history')
@staff_required
def patient_history(id):
    """✅ HISTÓRICO COMPLETO DO PACIENTE - VERSÃO CORRIGIDA"""
    patient = Patient.query.get_or_404(id)
    page = request.args.get('page', 1, type=int)
    
    # ✅ FILTROS DA URL
    period = request.args.get('period', '')
    type_filter = request.args.get('type', '')
    medication_filter = request.args.get('medication', '')
    
    # ✅ CALCULAR ESTATÍSTICAS GERAIS
    all_dispensations = Dispensation.query.filter_by(patient_id=id).all()
    all_high_cost = HighCostProcess.query.filter_by(patient_id=id).all()
    
    total_dispensations = len(all_dispensations)
    total_high_cost = len(all_high_cost)
    
    # ✅ MEDICAMENTOS ÚNICOS
    unique_meds = set()
    for disp in all_dispensations:
        if hasattr(disp, 'items') and disp.items:
            for item in disp.items:
                unique_meds.add(item.medication_id)
    for process in all_high_cost:
        unique_meds.add(process.medication_id)
    unique_medications = len(unique_meds)
    
    # ✅ ÚLTIMA DISPENSAÇÃO
    last_dispensation = Dispensation.query.filter_by(patient_id=id).order_by(
        desc(Dispensation.dispensation_date)
    ).first()
    
    # ✅ CRIAR TIMELINE UNIFICADA
    history_items_list = []
    
    # Adicionar dispensações
    for dispensation in all_dispensations:
        if hasattr(dispensation, 'items') and dispensation.items:
            for item in dispensation.items:
                history_items_list.append({
                    'type': 'dispensation',
                    'date': dispensation.dispensation_date,
                    'medication_name': item.medication.commercial_name if item.medication else 'Medicamento não encontrado',
                    'quantity': item.quantity_dispensed,
                    'pharmacist_name': dispensation.dispenser.full_name if dispensation.dispenser else 'N/A',
                    'prescription_number': getattr(dispensation, 'prescription_number', None),
                    'notes': item.observations or dispensation.observations,
                    'status': dispensation.status.value if hasattr(dispensation.status, 'value') else str(dispensation.status),
                    'attachments': []
                })
        else:
            # Se não há itens, criar entrada genérica
            history_items_list.append({
                'type': 'dispensation',
                'date': dispensation.dispensation_date,
                'medication_name': 'Dispensação registrada',
                'quantity': 1,
                'pharmacist_name': dispensation.dispenser.full_name if dispensation.dispenser else 'N/A',
                'prescription_number': getattr(dispensation, 'prescription_number', None),
                'notes': dispensation.observations,
                'status': dispensation.status.value if hasattr(dispensation.status, 'value') else str(dispensation.status),
                'attachments': []
            })
    
    # Adicionar processos alto custo
    for process in all_high_cost:
        history_items_list.append({
            'type': 'high_cost',
            'date': process.request_date,
            'medication_name': process.medication.commercial_name if process.medication else 'Medicamento não encontrado',
            'protocol_number': process.protocol_number,
            'status': process.status.value if hasattr(process.status, 'value') else str(process.status),
            'prescribing_doctor': process.doctor_name,
            'cid_code': process.cid10,
            'quantity_approved': getattr(process, 'approved_quantity', None),
            'attachments': getattr(process, 'documents', [])
        })
    
    # ✅ APLICAR FILTROS
    filtered_items = []
    
    for item in history_items_list:
        # Filtro por período
        if period:
            try:
                days = int(period)
                cutoff_date = datetime.now() - timedelta(days=days)
                item_date = item['date']
                
                # Converter para datetime se for date
                if hasattr(item_date, 'date'):
                    item_datetime = item_date
                else:
                    item_datetime = datetime.combine(item_date, datetime.min.time())
                
                if item_datetime < cutoff_date:
                    continue
            except (ValueError, TypeError):
                pass
        
        # Filtro por tipo
        if type_filter and item['type'] != type_filter:
            continue
        
        # Filtro por medicamento
        if medication_filter and medication_filter.lower() not in item['medication_name'].lower():
            continue
        
        filtered_items.append(item)
    
    # ✅ ORDENAR POR DATA (MAIS RECENTE PRIMEIRO)
    filtered_items.sort(key=lambda x: x['date'], reverse=True)
    
    # ✅ PAGINAÇÃO MANUAL
    per_page = 10
    total_count = len(history_items_list)
    filtered_count = len(filtered_items)
    
    start = (page - 1) * per_page
    end = start + per_page
    paginated_items = filtered_items[start:end]
    
    # ✅ CRIAR CLASSE DE PAGINAÇÃO ITERÁVEL
    class PaginationWrapper:
        def __init__(self, items, page, per_page, total):
            self._items = items
            self.page = page
            self.per_page = per_page
            self.total = total
            self.pages = (total + per_page - 1) // per_page if total > 0 else 1
            self.has_prev = page > 1
            self.has_next = page < self.pages
            self.prev_num = page - 1 if self.has_prev else None
            self.next_num = page + 1 if self.has_next else None
        
        def __iter__(self):
            """Permite iteração direta sobre o objeto"""
            return iter(self._items)
        
        def __len__(self):
            """Retorna comprimento dos itens"""
            return len(self._items)
        
        @property
        def items(self):
            """Compatibilidade com Flask-SQLAlchemy pagination"""
            return self._items
        
        def iter_pages(self, left_edge=2, left_current=2, right_current=3, right_edge=2):
            """Gerador de páginas para navegação"""
            last = self.pages
            for num in range(1, last + 1):
                if (num <= left_edge or 
                    (self.page - left_current - 1 < num < self.page + right_current) or 
                    num > last - right_edge):
                    yield num
    
    # ✅ CRIAR OBJETO DE PAGINAÇÃO
    history_items = PaginationWrapper(paginated_items, page, per_page, filtered_count)
    
    return render_template('patients/history.html', 
                         patient=patient,
                         history_items=history_items,
                         total_dispensations=total_dispensations,
                         unique_medications=unique_medications,
                         total_high_cost=total_high_cost,
                         last_dispensation=last_dispensation,
                         total_count=total_count,
                         filtered_count=filtered_count,
                         period=period,
                         type=type_filter,
                         medication_filter=medication_filter)
# =================== DISPENSAÇÃO BÁSICA ===================

@main.route('/dispensation')
@staff_required
def dispensation_index():
    """Página principal de dispensação"""
    form = DispensationForm()
    return render_template('dispensation/index.html', form=form)

@main.route('/dispensation/search-patient', methods=['POST'])
@staff_required
def dispensation_search_patient():
    """Buscar paciente para dispensação com campos expandidos"""
    search_term = request.form.get('patient_search', '').strip()
    search_type = request.form.get('search_type', 'name_cpf_cns')
    
    if len(search_term) < 3:
        return jsonify({'error': 'Digite pelo menos 3 caracteres'}), 400
    
    try:
        query = Patient.query.filter(Patient.is_active == True)
        
        if search_term.isdigit():
            if len(search_term) == 11:
                # CPF
                query = query.filter(Patient.cpf == search_term)
            elif len(search_term) == 15:
                # CNS
                query = query.filter(Patient.cns == search_term)
            else:
                # ✅ BUSCA EM TELEFONES E DOCUMENTOS
                query = query.filter(
                    or_(
                        Patient.cpf.like(f'%{search_term}%'),
                        Patient.cns.like(f'%{search_term}%'),
                        Patient.cell_phone.like(f'%{search_term}%'),
                        Patient.home_phone.like(f'%{search_term}%'),
                        Patient.contact_phone.like(f'%{search_term}%')
                    )
                )
        else:
            # ✅ BUSCA POR NOME OU NOME DA MÃE
            query = query.filter(
                or_(
                    Patient.full_name.ilike(f'%{search_term}%'),
                    Patient.mother_name.ilike(f'%{search_term}%')
                )
            )
        
        patients = query.order_by(Patient.full_name).limit(50).all()
        
        # ✅ FORMATAR DADOS COM NOVOS CAMPOS
        patients_data = []
        for patient in patients:
            # Telefone principal (prioridade: celular > residencial > contato)
            primary_phone = patient.cell_phone or patient.home_phone or patient.contact_phone or getattr(patient, 'phone', None)
            
            patients_data.append({
                'id': patient.id,
                'name': patient.full_name,
                'cpf': format_cpf(patient.cpf),
                'cns': format_cns(patient.cns) if patient.cns else None,
                'birth_date': patient.birth_date.isoformat() if patient.birth_date else None,
                'age': patient.age,
                'phone': format_phone(primary_phone) if primary_phone else None,
                'mother_name': getattr(patient, 'mother_name', None),
                'source': getattr(patient, 'source', 'local') or 'local'
            })
        
        return jsonify({
            'success': True,
            'patients': patients_data,
            'count': len(patients_data)
        })
        
    except Exception as e:
        current_app.logger.error(f"Erro na busca de pacientes: {e}")
        return jsonify({'error': 'Erro interno do servidor'}), 500

@main.route('/dispensation/patient/<int:patient_id>')
@staff_required
def dispensation_select_medications(patient_id):
    """Selecionar medicamentos para dispensação"""
    patient = Patient.query.get_or_404(patient_id)
    
    # Medicamentos disponíveis (não alto custo para dispensação básica)
    available_medications = Medication.query.filter(
        and_(
            Medication.is_active == True,
            Medication.current_stock > 0,
            Medication.medication_type.in_(['basic', 'controlled', 'psychotropic'])
        )
    ).order_by(Medication.commercial_name).all()
    
    # Prescrições recentes do paciente
    recent_prescriptions = Prescription.query.filter_by(
        patient_id=patient_id, is_active=True
    ).order_by(desc(Prescription.prescription_date)).limit(5).all()
    
    return render_template('dispensation/select_medications.html',
                         patient=patient,
                         available_medications=available_medications,
                         recent_prescriptions=recent_prescriptions)

@main.route('/dispensation/confirm')
@staff_required
def dispensation_confirm_page():
    """Página de confirmação da dispensação"""
    patient_id = request.args.get('patient_id', type=int)
    if not patient_id:
        flash('Paciente não especificado.', 'error')
        return redirect(url_for('main.dispensation_index'))
    
    patient = Patient.query.get_or_404(patient_id)
    
    return render_template('dispensation/confirm.html', patient=patient)

@main.route('/dispensation/create', methods=['POST'])
@staff_required
def dispensation_create():
    """Processar dispensação com controle de intervalos UNIVERSAL"""
    data = request.get_json()
    
    patient_id = data.get('patient_id')
    medications = data.get('medications', [])
    general_observations = data.get('general_observations', '')
    force_release = data.get('force_release', False)
    force_justification = data.get('force_justification', '')
    
    if not patient_id or not medications:
        return jsonify({'error': 'Dados incompletos'}), 400
    
    patient = Patient.query.get(patient_id)
    if not patient:
        return jsonify({'error': 'Paciente não encontrado'}), 404
    
    try:
        # ✅ VERIFICAR INTERVALOS PARA TODOS OS MEDICAMENTOS - LÓGICA UNIVERSAL
        blocked_medications = []
        
        for med_data in medications:
            medication = Medication.query.get(med_data['medication_id'])
            
            if medication:
                # ✅ BUSCAR CONTROLE EXISTENTE PARA QUALQUER MEDICAMENTO
                existing_control = DispensationControl.query.filter_by(
                    patient_id=patient_id,
                    medication_id=medication.id,
                    is_active=True
                ).first()
                
                if existing_control:
                    # ✅ MEDICAMENTO JÁ TEM CONTROLE - VERIFICAR SE PODE DISPENSAR
                    if not existing_control.can_dispense_today:
                        days_remaining = existing_control.days_until_next_allowed
                        
                        blocked_medications.append({
                            'medication_id': medication.id,
                            'name': medication.commercial_name,
                            'days_remaining': days_remaining,
                            'next_date': existing_control.next_allowed_date.strftime('%d/%m/%Y'),
                            'control_id': existing_control.id,
                            'reason': 'Em período de carência',
                            'interval_days': existing_control.interval_days_used,
                            'last_dispensation': existing_control.last_dispensation_date.strftime('%d/%m/%Y') if existing_control.last_dispensation_date else None,
                            'can_force_release': True  # ✅ PERMITE LIBERAÇÃO ANTECIPADA
                        })
                else:
                    # ✅ PRIMEIRA DISPENSAÇÃO - VERIFICAR SE HÁ DADOS DE CONTROLE
                    interval_control_data = med_data.get('interval_control')
                    
                    if not interval_control_data or not interval_control_data.get('enabled'):
                        # ✅ BLOQUEAR: TODO MEDICAMENTO PRECISA DE CONTROLE NA PRIMEIRA DISPENSAÇÃO
                        blocked_medications.append({
                            'medication_id': medication.id,
                            'name': medication.commercial_name,
                            'days_remaining': 0,
                            'next_date': 'Não configurado',
                            'control_id': None,
                            'reason': 'Controle de intervalo obrigatório não configurado',
                            'requires_configuration': True,
                            'is_first_dispensation': True,
                            'can_force_release': False  # ✅ NÃO PERMITE LIBERAÇÃO SEM CONFIGURAÇÃO
                        })
        
        # ✅ SE TEM MEDICAMENTOS BLOQUEADOS E NÃO É LIBERAÇÃO FORÇADA
        if blocked_medications and not force_release:
            return jsonify({
                'error': 'Medicamentos bloqueados para dispensação',
                'blocked_medications': blocked_medications,
                'requires_authorization': any(med.get('can_force_release', False) for med in blocked_medications),
                'requires_configuration': any(med.get('requires_configuration', False) for med in blocked_medications)
            }), 400
        
        # ✅ SE É LIBERAÇÃO FORÇADA, VERIFICAR SE TODOS PODEM SER LIBERADOS
        if force_release:
            cannot_force = [med for med in blocked_medications if not med.get('can_force_release', False)]
            if cannot_force:
                return jsonify({
                    'error': 'Alguns medicamentos não podem ser liberados antecipadamente',
                    'blocked_medications': cannot_force,
                    'requires_configuration': True
                }), 400
        
        # ✅ CRIAR DISPENSAÇÃO
        dispensation = Dispensation(
            patient_id=patient_id,
            dispenser_id=current_user.id,
            observations=general_observations,
            status=DispensationStatus.COMPLETED
        )
        db.session.add(dispensation)
        db.session.flush()  # Para obter o ID
        
        total_cost = Decimal('0.00')
        interval_controls_created = 0
        early_releases_count = 0
        
        # ✅ PROCESSAR CADA MEDICAMENTO
        for med_data in medications:
            medication = Medication.query.get(med_data['medication_id'])
            quantity = int(med_data['quantity'])
            med_observations = med_data.get('observations', '')
            interval_control_data = med_data.get('interval_control')
            
            if not medication:
                raise ValueError(f"Medicamento {med_data['medication_id']} não encontrado")
            
            if medication.current_stock < quantity:
                raise ValueError(f"Estoque insuficiente para {medication.commercial_name}")
            
            # ✅ CALCULAR CUSTO
            unit_cost = medication.unit_cost or Decimal('0.00')
            item_cost = unit_cost * quantity
            total_cost += item_cost
            
            # ✅ CRIAR ITEM DA DISPENSAÇÃO PRIMEIRO (OBRIGATÓRIO PARA O CONTROLE)
            dispensation_item = DispensationItem(
                dispensation_id=dispensation.id,
                medication_id=medication.id,
                quantity_dispensed=quantity,
                unit_cost=unit_cost,
                total_cost=item_cost,
                observations=med_observations
            )
            db.session.add(dispensation_item)
            db.session.flush()  # ✅ IMPORTANTE: Obter ID do item para o controle
            
            # ✅ PROCESSAR CONTROLE DE INTERVALO (OBRIGATÓRIO PARA TODOS)
            existing_control = DispensationControl.query.filter_by(
                patient_id=patient_id,
                medication_id=medication.id,
                is_active=True
            ).first()
            
            if existing_control:
                # ✅ ATUALIZAR CONTROLE EXISTENTE
                if force_release and existing_control.id in [bc.get('control_id') for bc in blocked_medications if bc.get('control_id')]:
                    # Criar log de liberação antecipada
                    early_release = EarlyReleaseLog(
                        dispensation_control_id=existing_control.id,
                        authorized_by=current_user.id,
                        justification=force_justification,
                        days_early=existing_control.days_until_next_allowed,
                        original_date=existing_control.next_allowed_date,
                        released_date=date.today()
                    )
                    db.session.add(early_release)
                    early_releases_count += 1
                
                # Atualizar controle (usar intervalo configurado ou manter o atual)
                if interval_control_data and interval_control_data.get('enabled'):
                    interval_days = interval_control_data['interval_days']
                    next_date = datetime.strptime(interval_control_data['next_allowed_date'], '%Y-%m-%d').date()
                    existing_control.interval_days_used = interval_days
                else:
                    interval_days = existing_control.interval_days_used
                    next_date = date.today() + timedelta(days=interval_days)
                
                existing_control.last_dispensation_date = date.today()
                existing_control.next_allowed_date = next_date
                existing_control.was_released_early = force_release
                
            else:
                # ✅ CRIAR NOVO CONTROLE (OBRIGATÓRIO PARA TODOS)
                if interval_control_data and interval_control_data.get('enabled'):
                    interval_days = interval_control_data['interval_days']
                    next_date = datetime.strptime(interval_control_data['next_allowed_date'], '%Y-%m-%d').date()
                    justification = interval_control_data.get('justification', '')
                else:
                    # ✅ USAR VALORES PADRÃO BASEADOS NO TIPO DO MEDICAMENTO
                    if hasattr(medication, 'medication_type') and medication.medication_type:
                        if medication.medication_type.value in ['controlled', 'psychotropic']:
                            interval_days = 30  # Controlados: 30 dias
                        elif medication.medication_type.value == 'high_cost':
                            interval_days = 90  # Alto custo: 90 dias
                        else:
                            interval_days = 30  # Padrão: 30 dias
                    else:
                        interval_days = 30  # Padrão geral
                    
                    next_date = date.today() + timedelta(days=interval_days)
                    justification = f'Controle automático - medicamento {medication.medication_type.value if hasattr(medication, "medication_type") else "padrão"}'
                
                # ✅ CRIAR CONTROLE COM OS CAMPOS CORRETOS DO MODELO
                new_control = DispensationControl(
                    patient_id=patient_id,
                    medication_id=medication.id,
                    dispensation_item_id=dispensation_item.id,  # ✅ CAMPO OBRIGATÓRIO
                    last_dispensation_date=date.today(),
                    next_allowed_date=next_date,
                    interval_days_used=interval_days,
                    is_active=True,
                    was_released_early=force_release
                    # ✅ created_at será preenchido automaticamente
                    # ✅ NÃO TEM created_by no modelo
                )
                
                db.session.add(new_control)
                interval_controls_created += 1
            
            # ✅ ATUALIZAR ESTOQUE
            old_stock = medication.current_stock
            medication.current_stock -= quantity
            
            # ✅ REGISTRAR MOVIMENTO DE ESTOQUE
            movement = InventoryMovement(
                medication_id=medication.id,
                user_id=current_user.id,
                movement_type='exit',
                quantity=quantity,
                previous_stock=old_stock,
                new_stock=medication.current_stock,
                reason=f'Dispensação #{dispensation.id}',
                reference_id=dispensation.id,
                reference_type='dispensation'
            )
            db.session.add(movement)
        
        # ✅ ATUALIZAR CUSTO TOTAL
        dispensation.total_cost = total_cost
        
        db.session.commit()
        
        # ✅ LOG DA AÇÃO
        log_action('CREATE', 'dispensations', dispensation.id, new_values={
            'patient_id': patient_id,
            'total_cost': float(total_cost),
            'items_count': len(medications),
            'interval_controls_created': interval_controls_created,
            'early_releases': early_releases_count,
            'force_release': force_release,
            'universal_control': True  # ✅ MARCAR COMO CONTROLE UNIVERSAL
        })
        
        # ✅ MENSAGEM DE SUCESSO
        message = 'Dispensação realizada com sucesso!'
        if interval_controls_created > 0:
            message += f' {interval_controls_created} controle(s) de intervalo criado(s).'
        if early_releases_count > 0:
            message += f' {early_releases_count} liberação(ões) antecipada(s) autorizada(s).'
        
        return jsonify({
            'success': True,
            'dispensation_id': dispensation.id,
            'message': message,
            'interval_controls_created': interval_controls_created,
            'early_releases': early_releases_count,
            'universal_control_applied': True,
            'redirect_url': url_for('main.dispensation_index')  # ✅ URL CORRETA PARA REDIRECIONAMENTO
        })
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Erro na dispensação universal: {e}")
        return jsonify({'error': str(e)}), 500
    
@main.route('/api/patients/<int:patient_id>/medication-controls', methods=['GET'])
@staff_required
def api_patient_medication_controls(patient_id):
    """API para obter controles de medicamentos do paciente"""
    try:
        patient = Patient.query.get_or_404(patient_id)
        
        # Obter controles ativos
        controls = patient.get_active_medication_controls()
        
        # Converter para dicionários serializáveis
        controls_data = []
        for control in controls:
            controls_data.append({
                'id': control.id,
                'medication_name': control.medication.commercial_name if control.medication else 'N/A',
                'last_dispensation_date': control.last_dispensation_date.strftime('%d/%m/%Y') if control.last_dispensation_date else 'N/A',
                'next_allowed_date': control.next_allowed_date.strftime('%d/%m/%Y') if control.next_allowed_date else 'N/A',
                'interval_days': control.interval_days_used,
                'days_until_next_allowed': control.days_until_next_allowed,
                'can_dispense_today': control.can_dispense_today,
                'status_display': control.status_display
            })
        
        return jsonify({
            'success': True,
            'controls': controls_data
        })
        
    except Exception as e:
        current_app.logger.error(f"Erro ao obter controles do paciente {patient_id}: {e}")
        return jsonify({
            'success': False,
            'error': 'Erro interno do servidor'
        }), 500

@main.route('/dispensation/details', methods=['POST'])
@staff_required
def dispensation_details():
    """API para buscar detalhes de uma dispensação"""
    
    dispensation_id = request.form.get('dispensation_id')
    
    if not dispensation_id:
        return jsonify({'success': False, 'message': 'ID da dispensação não fornecido'})
    
    try:
        dispensation = Dispensation.query.options(
            joinedload(Dispensation.patient),
            joinedload(Dispensation.dispenser),
            joinedload(Dispensation.items).joinedload(DispensationItem.medication)
        ).get_or_404(dispensation_id)
        
        # Verificar permissões
        if current_user.role == UserRole.ATTENDANT and dispensation.dispenser_id != current_user.id:
            return jsonify({'success': False, 'message': 'Sem permissão para ver esta dispensação'})
        
        dispensation_data = {
            'id': dispensation.id,
            'formatted_date': dispensation.dispensation_date.strftime('%d/%m/%Y %H:%M'),
            'dispenser_name': dispensation.dispenser.full_name,
            'status': dispensation.status.value,
            'status_text': dispensation.status.value.title(),
            'observations': dispensation.observations,
            'total_cost': f"{dispensation.total_cost:.2f}".replace('.', ',') if dispensation.total_cost else '0,00',
            'items': []
        }
        
        for item in dispensation.items:
            item_data = {
                'medication_name': item.medication.commercial_name,
                'dosage': item.medication.dosage,
                'quantity': item.quantity_dispensed,
                'observations': item.observations,
                'cost': f"{item.total_cost:.2f}".replace('.', ',') if item.total_cost else None
            }
            dispensation_data['items'].append(item_data)
        
        return jsonify({'success': True, 'dispensation': dispensation_data})
        
    except Exception as e:
        current_app.logger.error(f"Erro ao buscar dispensação {dispensation_id}: {str(e)}")
        return jsonify({'success': False, 'message': 'Erro interno do servidor'})    


# =================== RELATÓRIOS DO PACIENTE ===================

@main.route('/patients/<int:patient_id>/report')
@staff_required
def patient_report(patient_id):
    """✅ RELATÓRIO ESPECÍFICO DO PACIENTE"""
    patient = Patient.query.get_or_404(patient_id)
    
    # Parâmetros de filtro
    start_date_str = request.args.get('start_date')
    end_date_str = request.args.get('end_date')
    format_type = request.args.get('format', 'html')
    
    # Converter datas
    start_date = None
    end_date = None
    
    if start_date_str:
        try:
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
        except ValueError:
            pass
    
    if end_date_str:
        try:
            end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()
        except ValueError:
            pass
    
    # Se não especificado, usar últimos 6 meses
    if not start_date:
        start_date = date.today() - timedelta(days=180)
    if not end_date:
        end_date = date.today()
    
    return generate_patient_report(patient, start_date, end_date, format_type)

@main.route('/patients/<int:patient_id>/report/export')
@staff_required
def patient_report_export(patient_id):
    """✅ EXPORTAÇÃO DO RELATÓRIO DO PACIENTE"""
    format_type = request.args.get('format', 'pdf')
    start_date_str = request.args.get('start_date')
    end_date_str = request.args.get('end_date')
    
    # Converter datas
    start_date = None
    end_date = None
    
    if start_date_str:
        try:
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
        except ValueError:
            pass
    
    if end_date_str:
        try:
            end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()
        except ValueError:
            pass
    
    # Se não especificado, usar últimos 6 meses
    if not start_date:
        start_date = date.today() - timedelta(days=180)
    if not end_date:
        end_date = date.today()
    
    patient = Patient.query.get_or_404(patient_id)
    return generate_patient_report(patient, start_date, end_date, format_type)

def generate_patient_report(patient, start_date, end_date, format_type):
    """✅ GERAR RELATÓRIO ESPECÍFICO DO PACIENTE"""
    
    # Buscar dispensações do paciente no período
    dispensations_query = Dispensation.query.filter(
        and_(
            Dispensation.patient_id == patient.id,
            Dispensation.dispensation_date >= start_date,
            Dispensation.dispensation_date <= end_date
        )
    ).options(
        joinedload(Dispensation.items).joinedload(DispensationItem.medication),
        joinedload(Dispensation.dispenser)
    ).order_by(desc(Dispensation.dispensation_date))
    
    dispensations = dispensations_query.all()
    
    # Buscar processos alto custo no período (se existir a tabela)
    high_cost_processes = []
    try:
        from app.models import HighCostProcess
        high_cost_query = HighCostProcess.query.filter(
            and_(
                HighCostProcess.patient_id == patient.id,
                HighCostProcess.request_date >= start_date,
                HighCostProcess.request_date <= end_date
            )
        ).options(
            joinedload(HighCostProcess.medication)
        ).order_by(desc(HighCostProcess.request_date))
        
        high_cost_processes = high_cost_query.all()
    except:
        # Tabela não existe ainda
        pass
    
    # Calcular estatísticas
    total_dispensations = len(dispensations)
    total_high_cost = len(high_cost_processes)
    
    # Medicamentos únicos
    unique_medications = set()
    total_cost = 0
    total_quantity = 0
    
    # Timeline de eventos
    timeline_events = []
    
    # Adicionar dispensações à timeline
    for dispensation in dispensations:
        for item in dispensation.items:
            unique_medications.add(item.medication_id)
            total_quantity += item.quantity_dispensed
            total_cost += item.total_cost or 0
            
            timeline_events.append({
                'date': dispensation.dispensation_date,
                'type': 'dispensation',
                'description': f"Dispensação: {item.medication.commercial_name}",
                'details': f"{item.quantity_dispensed} unidades",
                'cost': item.total_cost or 0,
                'pharmacist': dispensation.dispenser.full_name,
                'status': dispensation.status.value
            })
    
    # Adicionar processos alto custo à timeline
    for process in high_cost_processes:
        timeline_events.append({
            'date': process.request_date,
            'type': 'high_cost',
            'description': f"Alto Custo: {process.medication.commercial_name}",
            'details': f"Protocolo {process.protocol_number} - {process.status.value.title()}",
            'cost': 0,
            'pharmacist': 'Sistema',
            'status': process.status.value
        })
    
    # Ordenar timeline por data
    timeline_events.sort(key=lambda x: x['date'], reverse=True)
    
    # Estatísticas do período
    stats = {
        'total_dispensations': total_dispensations,
        'total_high_cost': total_high_cost,
        'unique_medications': len(unique_medications),
        'total_quantity': total_quantity,
        'total_cost': total_cost,
        'period_start': start_date,
        'period_end': end_date,
        'avg_cost_per_dispensation': total_cost / total_dispensations if total_dispensations > 0 else 0
    }
    
    if format_type == 'html':
        return render_template('reports/patient_report.html',
                             patient=patient,
                             dispensations=dispensations,
                             high_cost_processes=high_cost_processes,
                             timeline_events=timeline_events,
                             stats=stats,
                             start_date=start_date,
                             end_date=end_date)
    elif format_type == 'pdf':
        return generate_patient_report_pdf(patient, dispensations, high_cost_processes, timeline_events, stats, start_date, end_date)
    elif format_type == 'excel':
        return generate_patient_report_excel(patient, dispensations, high_cost_processes, timeline_events, stats, start_date, end_date)

def generate_patient_report_pdf(patient, dispensations, high_cost_processes, timeline_events, stats, start_date, end_date):
    """Gerar PDF do relatório do paciente"""
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4)
    styles = getSampleStyleSheet()
    story = []
    
    # Título
    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Heading1'],
        fontSize=18,
        spaceAfter=30,
        alignment=1
    )
    
    title = Paragraph(f"Relatório do Paciente: {patient.full_name}", title_style)
    story.append(title)
    
    # Período
    period = Paragraph(f"Período: {format_date(start_date)} a {format_date(end_date)}", styles['Normal'])
    story.append(period)
    story.append(Spacer(1, 12))
    
    # Dados do paciente
    patient_data = [
        ['Dados do Paciente', ''],
        ['Nome', patient.full_name],
        ['CPF', format_cpf(patient.cpf)],
        ['CNS', format_cns(patient.cns) if patient.cns else 'N/A'],
        ['Idade', f"{patient.age} anos"],
        ['Telefone', patient.primary_phone or 'N/A']
    ]
    
    patient_table = Table(patient_data)
    patient_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.lightblue),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.black),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 12),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
        ('GRID', (0, 0), (-1, -1), 1, colors.black)
    ]))
    
    story.append(patient_table)
    story.append(Spacer(1, 12))
    
    # Estatísticas do período
    stats_data = [
        ['Estatísticas do Período', ''],
        ['Total de Dispensações', str(stats['total_dispensations'])],
        ['Processos Alto Custo', str(stats['total_high_cost'])],
        ['Medicamentos Únicos', str(stats['unique_medications'])],
        ['Quantidade Total', str(stats['total_quantity'])],
        ['Custo Total', f"R$ {stats['total_cost']:.2f}"]
    ]
    
    stats_table = Table(stats_data)
    stats_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.lightgreen),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.black),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 12),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
        ('GRID', (0, 0), (-1, -1), 1, colors.black)
    ]))
    
    story.append(stats_table)
    story.append(Spacer(1, 12))
    
    # Timeline de eventos (últimos 20)
    if timeline_events:
        timeline_title = Paragraph("Timeline de Eventos (Últimos 20)", styles['Heading2'])
        story.append(timeline_title)
        story.append(Spacer(1, 6))
        
        timeline_data = [['Data', 'Tipo', 'Descrição', 'Detalhes']]
        
        for event in timeline_events[:20]:
            timeline_data.append([
                format_date(event['date']),
                'Dispensação' if event['type'] == 'dispensation' else 'Alto Custo',
                event['description'][:40],
                event['details'][:30]
            ])
        
        timeline_table = Table(timeline_data)
        timeline_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 10),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
            ('GRID', (0, 0), (-1, -1), 1, colors.black),
            ('FONTSIZE', (0, 1), (-1, -1), 8)
        ]))
        
        story.append(timeline_table)
    
    doc.build(story)
    buffer.seek(0)
    
    return send_file(
        buffer,
        as_attachment=True,
        download_name=f'relatorio_paciente_{patient.full_name.replace(" ", "_")}_{datetime.now().strftime("%Y%m%d")}.pdf',
        mimetype='application/pdf'
    )

def generate_patient_report_excel(patient, dispensations, high_cost_processes, timeline_events, stats, start_date, end_date):
    """Gerar Excel do relatório do paciente"""
    output = BytesIO()
    
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        # Aba com dados do paciente
        patient_data = pd.DataFrame([
            {'Campo': 'Nome', 'Valor': patient.full_name},
            {'Campo': 'CPF', 'Valor': format_cpf(patient.cpf)},
            {'Campo': 'CNS', 'Valor': format_cns(patient.cns) if patient.cns else 'N/A'},
            {'Campo': 'Idade', 'Valor': f"{patient.age} anos"},
            {'Campo': 'Telefone', 'Valor': patient.primary_phone or 'N/A'},
            {'Campo': 'Email', 'Valor': patient.email or 'N/A'},
            {'Campo': 'Endereço', 'Valor': patient.full_address},
            {'Campo': 'Período do Relatório', 'Valor': f"{format_date(start_date)} a {format_date(end_date)}"}
        ])
        patient_data.to_excel(writer, sheet_name='Dados do Paciente', index=False)
        
        # Aba com estatísticas
        stats_data = pd.DataFrame([
            {'Indicador': 'Total de Dispensações', 'Valor': stats['total_dispensations']},
            {'Indicador': 'Processos Alto Custo', 'Valor': stats['total_high_cost']},
            {'Indicador': 'Medicamentos Únicos', 'Valor': stats['unique_medications']},
            {'Indicador': 'Quantidade Total Dispensada', 'Valor': stats['total_quantity']},
            {'Indicador': 'Custo Total', 'Valor': f"R$ {stats['total_cost']:.2f}"},
            {'Indicador': 'Custo Médio por Dispensação', 'Valor': f"R$ {stats['avg_cost_per_dispensation']:.2f}"}
        ])
        stats_data.to_excel(writer, sheet_name='Estatísticas', index=False)
        
        # Aba com dispensações
        if dispensations:
            dispensations_data = []
            for dispensation in dispensations:
                for item in dispensation.items:
                    dispensations_data.append({
                        'Data': dispensation.dispensation_date.strftime('%d/%m/%Y'),
                        'Medicamento': item.medication.commercial_name,
                        'Dosagem': item.medication.dosage,
                        'Quantidade': item.quantity_dispensed,
                        'Custo': f"R$ {item.total_cost:.2f}" if item.total_cost else "R$ 0,00",
                        'Lote': item.batch_number or '',
                        'Validade': item.expiry_date.strftime('%d/%m/%Y') if item.expiry_date else '',
                        'Farmacêutico': dispensation.dispenser.full_name,
                        'Status': dispensation.status.value.title(),
                        'Observações': item.observations or dispensation.observations or ''
                    })
            
            dispensations_df = pd.DataFrame(dispensations_data)
            dispensations_df.to_excel(writer, sheet_name='Dispensações', index=False)
        
        # Aba com processos alto custo
        if high_cost_processes:
            high_cost_data = []
            for process in high_cost_processes:
                high_cost_data.append({
                    'Data Solicitação': process.request_date.strftime('%d/%m/%Y'),
                    'Protocolo': process.protocol_number,
                    'Medicamento': process.medication.commercial_name,
                    'Status': process.status.value.title(),
                    'CID-10': process.cid10,
                    'Médico': process.doctor_name,
                    'Quantidade Solicitada': process.requested_quantity
                })
            
            high_cost_df = pd.DataFrame(high_cost_data)
            high_cost_df.to_excel(writer, sheet_name='Alto Custo', index=False)
        
        # Aba com timeline
        if timeline_events:
            timeline_data = []
            for event in timeline_events:
                timeline_data.append({
                    'Data': event['date'].strftime('%d/%m/%Y'),
                    'Tipo': 'Dispensação' if event['type'] == 'dispensation' else 'Alto Custo',
                    'Descrição': event['description'],
                    'Detalhes': event['details'],
                    'Custo': f"R$ {event['cost']:.2f}" if event['cost'] > 0 else '',
                    'Responsável': event['pharmacist'],
                    'Status': event['status'].title()
                })
            
            timeline_df = pd.DataFrame(timeline_data)
            timeline_df.to_excel(writer, sheet_name='Timeline', index=False)
        
        # Ajustar larguras
        for sheet_name in writer.sheets:
            worksheet = writer.sheets[sheet_name]
            for column in worksheet.columns:
                max_length = 0
                column_letter = column[0].column_letter
                for cell in column:
                    try:
                        if len(str(cell.value)) > max_length:
                            max_length = len(str(cell.value))
                    except:
                        pass
                adjusted_width = min(max_length + 2, 50)
                worksheet.column_dimensions[column_letter].width = adjusted_width
    
    output.seek(0)
    
    return send_file(
        output,
        as_attachment=True,
        download_name=f'relatorio_paciente_{patient.full_name.replace(" ", "_")}_{datetime.now().strftime("%Y%m%d")}.xlsx',
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )

@main.route('/api/patients/<int:patient_id>/medication-intervals')
@staff_required
def api_patient_medication_intervals(patient_id):
    """✅ API para histórico de intervalos e dispensações do paciente"""
    try:
        medication_id = request.args.get('medication_id', type=int)
        
        # Buscar dispensações do paciente
        dispensations_query = Dispensation.query.filter_by(patient_id=patient_id).options(
            joinedload(Dispensation.items).joinedload(DispensationItem.medication),
            joinedload(Dispensation.dispenser)
        ).order_by(desc(Dispensation.dispensation_date))
        
        # Filtrar por medicamento se especificado
        if medication_id:
            dispensations_query = dispensations_query.join(DispensationItem).filter(
                DispensationItem.medication_id == medication_id
            )
        
        dispensations = dispensations_query.limit(10).all()
        
        # Buscar controles ativos
        from app.models import DispensationControl
        controls_query = DispensationControl.query.filter_by(
            patient_id=patient_id,
            is_active=True
        ).options(joinedload(DispensationControl.medication))
        
        if medication_id:
            controls_query = controls_query.filter_by(medication_id=medication_id)
            
        active_controls = controls_query.all()
        
        # Formatar dados de dispensações
        dispensations_data = []
        for disp in dispensations:
            for item in disp.items:
                if not medication_id or item.medication_id == medication_id:
                    dispensations_data.append({
                        'date': disp.dispensation_date.strftime('%d/%m/%Y'),
                        'quantity': item.quantity_dispensed,
                        'dispenser': disp.dispenser.full_name,
                        'observations': item.observations or disp.observations
                    })
        
        # Formatar dados de medicamentos com controle
        medications_data = []
        for control in active_controls:
            medications_data.append({
                'name': control.medication.commercial_name,
                'dosage': control.medication.dosage,
                'has_active_control': True,
                'can_dispense': control.can_dispense_today,
                'last_dispensation': control.last_dispensation_date.strftime('%d/%m/%Y') if control.last_dispensation_date else None,
                'next_allowed_date': control.next_allowed_date.strftime('%d/%m/%Y') if control.next_allowed_date else None,
                'days_remaining': (control.next_allowed_date - date.today()).days if control.next_allowed_date and control.next_allowed_date > date.today() else 0
            })
        
        return jsonify({
            'success': True,
            'dispensations': dispensations_data,
            'medications': medications_data
        })
        
    except Exception as e:
        current_app.logger.error(f"Erro ao buscar intervalos do paciente: {e}")
        return jsonify({
            'success': False,
            'dispensations': [],
            'medications': []
        })

@main.route('/api/dispensation/interval-status/<int:patient_id>/<int:medication_id>')
@staff_required
def api_interval_status(patient_id, medication_id):
    """✅ API específica para status de intervalo (usado pelo template)"""
    try:
        patient = Patient.query.get_or_404(patient_id)
        medication = Medication.query.get_or_404(medication_id)
        
        # Verificar se há controle ativo
        has_control = medication.has_interval_control
        can_dispense = True
        control_id = None
        last_dispensation = None
        next_allowed_date = None
        days_remaining = 0
        interval_days = 0
        
        if has_control:
            can_dispense = medication.can_dispense_to_patient(patient_id)
            next_allowed_date = medication.get_next_allowed_date_for_patient(patient_id)
            
            if not can_dispense and next_allowed_date:
                days_remaining = (next_allowed_date - date.today()).days
                
            # Buscar último controle para detalhes
            from app.models import DispensationControl
            last_control = DispensationControl.query.filter_by(
                patient_id=patient_id,
                medication_id=medication_id,
                is_active=True
            ).order_by(desc(DispensationControl.last_dispensation_date)).first()
            
            if last_control:
                control_id = last_control.id
                last_dispensation = last_control.last_dispensation_date.isoformat()
                interval_days = last_control.interval_days
        
        return jsonify({
            'success': True,
            'has_control': has_control,
            'can_dispense': can_dispense,
            'control_id': control_id,
            'last_dispensation': last_dispensation,
            'next_allowed_date': next_allowed_date.isoformat() if next_allowed_date else None,
            'days_remaining': days_remaining,
            'interval_days': interval_days
        })
        
    except Exception as e:
        current_app.logger.error(f"Erro na verificação de intervalo: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@main.route('/inventory/<int:medication_id>/interval-config', methods=['GET', 'POST'])
@pharmacist_required
def medication_interval_config(medication_id):
    """Configurar intervalo de dispensação para medicamento"""
    medication = Medication.query.get_or_404(medication_id)
    
    if request.method == 'POST':
        try:
            interval_days = request.form.get('interval_days', type=int)
            interval_type = request.form.get('interval_type', 'predefined')
            is_active = request.form.get('is_active') == 'on'
            requires_justification = request.form.get('requires_justification') == 'on'
            
            if not interval_days or interval_days < 1:
                flash('Intervalo deve ser pelo menos 1 dia.', 'error')
                return redirect(request.url)
            
            # Buscar ou criar configuração
            from app.models import MedicationInterval
            interval_config = medication.interval_control
            if not interval_config:
                interval_config = MedicationInterval(
                    medication_id=medication_id,
                    created_by=current_user.id
                )
                db.session.add(interval_config)
            
            # Atualizar configuração
            interval_config.interval_days = interval_days
            interval_config.interval_type = interval_type
            interval_config.is_active = is_active
            interval_config.requires_justification = requires_justification
            interval_config.updated_at = datetime.utcnow()
            
            db.session.commit()
            
            log_action('UPDATE', 'medication_intervals', interval_config.id, new_values={
                'medication_id': medication_id,
                'interval_days': interval_days,
                'is_active': is_active
            })
            
            status_text = 'ativado' if is_active else 'desativado'
            flash(f'Controle de intervalo {status_text} para {medication.commercial_name} - {interval_days} dias.', 'success')
            
        except Exception as e:
            db.session.rollback()
            flash(f'Erro ao configurar intervalo: {str(e)}', 'error')
        
        return redirect(url_for('main.medication_view', id=medication_id))
    
    # GET - exibir formulário
    return render_template('inventory/interval_config.html', medication=medication)

@main.route('/dispensation/check-interval', methods=['POST'])
@staff_required
def dispensation_check_interval():
    """API para verificar se medicamento pode ser dispensado"""
    try:
        patient_id = request.form.get('patient_id', type=int)
        medication_id = request.form.get('medication_id', type=int)
        
        if not patient_id or not medication_id:
            return jsonify({'error': 'Parâmetros inválidos'}), 400
        
        patient = Patient.query.get(patient_id)
        medication = Medication.query.get(medication_id)
        
        if not patient or not medication:
            return jsonify({'error': 'Paciente ou medicamento não encontrado'}), 404
        
        # Verificar se pode dispensar
        can_dispense = medication.can_dispense_to_patient(patient_id)
        next_allowed_date = medication.get_next_allowed_date_for_patient(patient_id)
        
        if can_dispense:
            return jsonify({
                'can_dispense': True,
                'message': 'Dispensação liberada',
                'next_date': None
            })
        else:
            # Buscar último controle para detalhes
            from app.models import DispensationControl
            last_control = DispensationControl.query.filter_by(
                patient_id=patient_id,
                medication_id=medication_id,
                is_active=True
            ).order_by(desc(DispensationControl.last_dispensation_date)).first()
            
            days_remaining = (next_allowed_date - date.today()).days
            
            return jsonify({
                'can_dispense': False,
                'blocked': True,
                'message': f'Aguarde {days_remaining} dias para nova dispensação',
                'days_remaining': days_remaining,
                'last_dispensation': last_control.last_dispensation_date.strftime('%d/%m/%Y') if last_control else None,
                'next_allowed_date': next_allowed_date.strftime('%d/%m/%Y'),
                'interval_days': medication.interval_days,
                'medication_name': medication.commercial_name,
                'patient_name': patient.full_name,
                'control_id': last_control.id if last_control else None
            })
            
    except Exception as e:
        current_app.logger.error(f"Erro na verificação de intervalo: {e}")
        return jsonify({'error': 'Erro interno do servidor'}), 500

@main.route('/dispensation/early-release', methods=['POST'])
@pharmacist_required
def dispensation_early_release():
    """Liberar dispensação antes do prazo com justificativa"""
    try:
        control_id = request.form.get('control_id', type=int)
        justification = request.form.get('justification', '').strip()
        
        if not control_id or not justification:
            return jsonify({'error': 'Controle ID e justificativa são obrigatórios'}), 400
        
        if len(justification) < 10:
            return jsonify({'error': 'Justificativa deve ter pelo menos 10 caracteres'}), 400
        
        # Buscar controle
        from app.models import DispensationControl, EarlyReleaseLog
        control = DispensationControl.query.get(control_id)
        if not control:
            return jsonify({'error': 'Controle não encontrado'}), 404
        
        if control.can_dispense_today:
            return jsonify({'error': 'Dispensação já está liberada'}), 400
        
        # Calcular dias de antecipação
        days_early = (control.next_allowed_date - date.today()).days
        
        # Criar log de liberação antecipada
        early_release = EarlyReleaseLog(
            dispensation_control_id=control_id,
            authorized_by=current_user.id,
            justification=justification,
            days_early=days_early,
            original_date=control.next_allowed_date,
            released_date=date.today()
        )
        
        # Atualizar controle
        control.next_allowed_date = date.today()
        control.was_released_early = True
        
        db.session.add(early_release)
        db.session.commit()
        
        log_action('CREATE', 'early_release_logs', early_release.id, new_values={
            'control_id': control_id,
            'days_early': days_early,
            'authorized_by': current_user.id
        })
        
        return jsonify({
            'success': True,
            'message': f'Dispensação liberada {days_early} dias antes do prazo',
            'authorized_by': current_user.full_name,
            'justification': justification
        })
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Erro na liberação antecipada: {e}")
        return jsonify({'error': 'Erro interno do servidor'}), 500

@main.route('/dispensation/interval-history/<int:patient_id>')
@staff_required
def dispensation_interval_history(patient_id):
    """Histórico de controles de intervalo do paciente"""
    patient = Patient.query.get_or_404(patient_id)
    
    # Controles ativos
    from app.models import DispensationControl
    active_controls = DispensationControl.query.filter_by(
        patient_id=patient_id,
        is_active=True
    ).options(
        joinedload(DispensationControl.medication),
        joinedload(DispensationControl.early_releases)
    ).order_by(DispensationControl.next_allowed_date).all()
    
    # Histórico de controles
    all_controls = DispensationControl.query.filter_by(
        patient_id=patient_id
    ).options(
        joinedload(DispensationControl.medication),
        joinedload(DispensationControl.early_releases)
    ).order_by(desc(DispensationControl.last_dispensation_date)).limit(50).all()
    
    return render_template('dispensation/interval_history.html',
                         patient=patient,
                         active_controls=active_controls,
                         all_controls=all_controls)

@main.route('/admin/interval-controls')
@admin_required
def admin_interval_controls():
    """Administração de controles de intervalo"""
    page = request.args.get('page', 1, type=int)
    
    # Medicamentos com controle ativo
    from app.models import MedicationInterval, DispensationControl, EarlyReleaseLog
    controlled_medications = Medication.query.join(MedicationInterval).filter(
        MedicationInterval.is_active == True
    ).order_by(Medication.commercial_name).all()
    
    # Controles ativos próximos ao vencimento (próximos 7 dias)
    upcoming_releases = DispensationControl.query.filter(
        and_(
            DispensationControl.is_active == True,
            DispensationControl.next_allowed_date <= date.today() + timedelta(days=7),
            DispensationControl.next_allowed_date >= date.today()
        )
    ).options(
        joinedload(DispensationControl.patient),
        joinedload(DispensationControl.medication)
    ).order_by(DispensationControl.next_allowed_date).all()
    
    # Liberações antecipadas recentes (últimos 30 dias)
    recent_early_releases = EarlyReleaseLog.query.filter(
        EarlyReleaseLog.authorized_at >= datetime.now() - timedelta(days=30)
    ).options(
        joinedload(EarlyReleaseLog.dispensation_control).joinedload(DispensationControl.patient),
        joinedload(EarlyReleaseLog.dispensation_control).joinedload(DispensationControl.medication),
        joinedload(EarlyReleaseLog.authorizer)
    ).order_by(desc(EarlyReleaseLog.authorized_at)).limit(20).all()
    
    return render_template('admin/interval_controls.html',
                         controlled_medications=controlled_medications,
                         upcoming_releases=upcoming_releases,
                         recent_early_releases=recent_early_releases)

# =================== MODIFICAR API DE BUSCA DE MEDICAMENTOS ===================

@main.route('/api/medications/search-with-intervals')
@staff_required
def api_medications_search_with_intervals():
    """API para buscar medicamentos com verificação de intervalos"""
    term = request.args.get('q', '').strip()
    patient_id = request.args.get('patient_id', type=int)
    medication_type = request.args.get('type', '')
    
    if len(term) < 3:
        return jsonify([])
    
    # Query base
    query = Medication.query.filter(
        and_(
            Medication.is_active == True,
            Medication.current_stock > 0,
            or_(
                Medication.commercial_name.ilike(f'%{term}%'),
                Medication.generic_name.ilike(f'%{term}%')
            )
        )
    )
    
    # Filtro por tipo se especificado
    if medication_type:
        query = query.filter(Medication.medication_type == medication_type)
    
    medications = query.limit(20).all()
    
    results = []
    for med in medications:
        # Dados básicos do medicamento
        med_data = {
            'id': med.id,
            'commercial_name': med.commercial_name,
            'generic_name': med.generic_name,
            'dosage': med.dosage,
            'pharmaceutical_form': med.pharmaceutical_form,
            'current_stock': med.current_stock,
            'minimum_stock': med.minimum_stock,
            'unit_cost': float(med.unit_cost) if med.unit_cost else 0,
            'requires_prescription': med.requires_prescription,
            'controlled_substance': med.controlled_substance,
            'has_interval_control': med.has_interval_control,
            'interval_days': med.interval_days
        }
        
        # Verificar controle de intervalo se paciente especificado
        if patient_id and med.has_interval_control:
            can_dispense = med.can_dispense_to_patient(patient_id)
            next_date = med.get_next_allowed_date_for_patient(patient_id)
            
            med_data.update({
                'can_dispense': can_dispense,
                'next_allowed_date': next_date.isoformat() if next_date else None,
                'days_until_allowed': (next_date - date.today()).days if next_date and next_date > date.today() else 0
            })
        else:
            med_data.update({
                'can_dispense': True,
                'next_allowed_date': None,
                'days_until_allowed': 0
            })
        
        results.append(med_data)
    
    return jsonify(results)

# ✅ ROTA PARA CONFIGURAR CÁLCULOS DE MEDICAMENTO
@main.route('/medication/<int:medication_id>/dispensing_config', methods=['GET', 'POST'])
@login_required
def medication_dispensing_config(medication_id):
    """Configurar cálculos de dispensação para medicamento"""
    medication = Medication.query.get_or_404(medication_id)
    
    config = medication.dispensing_config
    if not config:
        config = MedicationDispensing(medication_id=medication_id)
    
    form = MedicationDispensingForm(obj=config)
    form.medication_id.data = medication_id
    
    if form.validate_on_submit():
        try:
            # ✅ VALIDAR ANTES DE SALVAR:
            is_valid, validation_msg = validate_medication_configuration(
                strength_value=float(form.strength_value.data),
                strength_unit=form.strength_unit.data,
                volume_per_dose=float(form.volume_per_dose.data),
                volume_unit=form.volume_unit.data,
                package_size=float(form.package_size.data),
                package_unit=form.package_unit.data
            )
            
            if not is_valid:
                flash(f'❌ {validation_msg}', 'error')
                return render_template('medications/dispensing_config.html', 
                                     form=form, medication=medication, config=config)
            
            # Continuar com o salvamento...
            if not config.id:
                db.session.add(config)
            
            form.populate_obj(config)
            config.medication_id = medication_id
            config.updated_at = datetime.utcnow()
            
            db.session.commit()
            flash('✅ Configuração de cálculos salva com sucesso!', 'success')
            
            return redirect(url_for('main.medication_view', id=medication_id))
            
        except Exception as e:
            db.session.rollback()
            flash(f'Erro ao salvar configuração: {str(e)}', 'error')
            current_app.logger.error(f"Erro ao salvar config dispensação: {e}")
    
    return render_template('medications/dispensing_config.html', 
                         form=form, medication=medication, config=config)

# ✅ ROTA PARA TESTAR CÁLCULOS
@main.route('/medication/<int:medication_id>/test_calculation', methods=['GET', 'POST'])
@login_required
def test_medication_calculation(medication_id):
    """Testar cálculos de dispensação"""
    medication = Medication.query.get_or_404(medication_id)
    
    if not medication.has_dispensing_config:
        flash('Medicamento não possui configuração de cálculos', 'warning')
        return redirect(url_for('main.medication_view', id=medication_id))
    
    form = CalculationTestForm()
    form.medication_id.data = medication_id
    
    calculation_result = None
    
    if form.validate_on_submit():
        try:
            # Chamar função de cálculo do banco
            result = calculate_from_database(
                medication_id=medication_id,
                prescribed_dose=float(form.prescribed_dose.data),
                prescribed_unit=form.prescribed_unit.data,
                frequency=int(form.frequency_per_day.data),
                treatment_days=int(form.treatment_days.data)
            )
            
            calculation_result = result
            
            if result.get('success'):
                flash('Cálculo realizado com sucesso!', 'success')
            else:
                flash(f"Erro no cálculo: {result.get('error')}", 'error')
                
        except Exception as e:
            flash(f'Erro ao calcular: {str(e)}', 'error')
            current_app.logger.error(f"Erro no teste de cálculo: {e}")
    
    return render_template('medications/test_calculation.html', 
                         form=form, medication=medication, 
                         calculation_result=calculation_result)

# ✅ ROTA PARA CÁLCULO RÁPIDO (SEM CONFIGURAÇÃO PRÉVIA)
@main.route('/quick_calculation', methods=['GET', 'POST'])
@login_required
def quick_calculation():
    """Cálculo rápido sem configuração prévia"""
    form = QuickCalculationForm()
    calculation_result = None
    
    if form.validate_on_submit():
        try:
            # Validar dados de entrada
            calc_data = {
                'prescribed_dose': float(form.prescribed_dose.data),
                'prescribed_unit': form.prescribed_unit.data,
                'strength_value': float(form.strength_value.data),
                'strength_unit': form.strength_unit.data,
                'volume_per_dose': float(form.volume_per_dose.data),
                'volume_unit': form.volume_unit.data,
                'frequency_per_day': int(form.frequency_per_day.data),
                'treatment_days': int(form.treatment_days.data),
                'package_size': float(form.package_size.data)
            }
            
            # Validar entradas
            is_valid, validation_msg = validate_calculation_inputs(calc_data)
            
            if not is_valid:
                flash(f'Dados inválidos: {validation_msg}', 'error')
            else:
                # Chamar função de cálculo completo
                result = calculate_complete_dispensation(
                    prescribed_dose=calc_data['prescribed_dose'],
                    prescribed_unit=calc_data['prescribed_unit'],
                    strength_value=calc_data['strength_value'],
                    strength_unit=calc_data['strength_unit'],
                    volume_per_dose=calc_data['volume_per_dose'],
                    volume_unit=calc_data['volume_unit'],
                    frequency=calc_data['frequency_per_day'],
                    days=calc_data['treatment_days'],
                    package_size=calc_data['package_size'],
                    package_unit=calc_data['volume_unit']  # Assumir mesma unidade
                )
                
                calculation_result = result
                
                if result.get('success'):
                    flash('Cálculo realizado com sucesso!', 'success')
                else:
                    flash(f"Erro no cálculo: {result.get('error')}", 'error')
                    
        except Exception as e:
            flash(f'Erro ao calcular: {str(e)}', 'error')
            current_app.logger.error(f"Erro no cálculo rápido: {e}")
    
    return render_template('calculations/quick_calculation.html', 
                         form=form, calculation_result=calculation_result)

# ✅ ROTA PARA LISTAR MEDICAMENTOS COM CONFIGURAÇÕES
@main.route('/medications/with_calculations')
@login_required
def medications_with_calculations():
    """Listar medicamentos com configurações de cálculo"""
    page = request.args.get('page', 1, type=int)
    per_page = 20
    
    # Buscar medicamentos que têm configuração ativa
    medications = Medication.query.join(MedicationDispensing).filter(
        MedicationDispensing.is_active == True,
        Medication.is_active == True
    ).order_by(Medication.commercial_name).paginate(
        page=page, per_page=per_page, error_out=False
    )
    
    return render_template('medications/with_calculations.html', 
                         medications=medications)

# ✅ ROTA PARA MEDICAMENTOS SEM CONFIGURAÇÕES
@main.route('/medications/without_calculations')
@login_required
def medications_without_calculations():
    """Listar medicamentos sem configurações de cálculo"""
    page = request.args.get('page', 1, type=int)
    per_page = 20
    
    # Medicamentos sem configuração ou com configuração inativa
    medications = Medication.query.outerjoin(MedicationDispensing).filter(
        or_(
            MedicationDispensing.id == None,
            MedicationDispensing.is_active == False
        ),
        Medication.is_active == True
    ).order_by(Medication.commercial_name).paginate(
        page=page, per_page=per_page, error_out=False
    )
    
    return render_template('medications/without_calculations.html', 
                         medications=medications)

# ✅ ROTA PARA EXCLUIR CONFIGURAÇÃO DE CÁLCULO
@main.route('/medication/<int:medication_id>/dispensing_config/delete', methods=['POST'])
@login_required
def delete_dispensing_config(medication_id):
    """Excluir configuração de cálculos"""
    medication = Medication.query.get_or_404(medication_id)
    config = medication.dispensing_config
    
    if not config:
        flash('Configuração não encontrada', 'error')
        return redirect(url_for('main.medication_view', id=medication_id))
    
    try:
        # Log antes de excluir
        log_action('DELETE', 'medication_dispensing', config.id, 
                  old_values=f"Configuração para {medication.commercial_name}")
        
        db.session.delete(config)
        db.session.commit()
        
        flash('Configuração de cálculos removida com sucesso!', 'success')
        
    except Exception as e:
        db.session.rollback()
        flash(f'Erro ao remover configuração: {str(e)}', 'error')
        current_app.logger.error(f"Erro ao deletar config dispensação: {e}")
    
    return redirect(url_for('main.medication_view', id=medication_id))

# ✅ ROTA PARA OBTER UNIDADES SUPORTADAS (AJAX)
@main.route('/api/supported_units')
@login_required
def api_supported_units():
    """API para obter unidades suportadas"""
    try:
        units = get_supported_units()
        return jsonify(units)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ✅ ROTA PARA CONVERTER UNIDADES (AJAX)
@main.route('/api/convert_units', methods=['POST'])
@login_required
def api_convert_units():
    """API para converter entre unidades"""
    try:
        data = request.get_json()
        
        value = float(data.get('value', 0))
        from_unit = data.get('from_unit', '')
        to_unit = data.get('to_unit', '')
        
        if not all([value > 0, from_unit, to_unit]):
            return jsonify({'error': 'Parâmetros inválidos'}), 400
        
        converted_value = convert_units(value, from_unit, to_unit)
        
        return jsonify({
            'success': True,
            'original_value': value,
            'converted_value': converted_value,
            'from_unit': from_unit,
            'to_unit': to_unit
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ✅ INTEGRAÇÃO COM DISPENSAÇÃO EXISTENTE
@main.route('/dispensation/calculate/<int:patient_id>/<int:medication_id>', methods=['POST'])
@login_required
def calculate_dispensation_for_patient(patient_id, medication_id):
    """Calcular dispensação durante processo de dispensação"""
    try:
        data = request.get_json()
        
        prescribed_dose = float(data.get('prescribed_dose', 0))
        prescribed_unit = data.get('prescribed_unit', '')
        frequency = int(data.get('frequency', 1))
        treatment_days = int(data.get('treatment_days', 1))
        
        # ✅ AGORA A FUNÇÃO ESTÁ IMPORTADA CORRETAMENTE:
        result = calculate_from_database(
            medication_id=medication_id,
            prescribed_dose=prescribed_dose,
            prescribed_unit=prescribed_unit,
            frequency=frequency,
            treatment_days=treatment_days
        )
        
        # ✅ MAPEAR RESULTADO PARA O TEMPLATE:
        if result['success']:
            mapped_result = {
                'success': True,
                'recommended_quantity': result['packages_needed'],
                'volume_per_dose': result['dose_per_administration'],
                'volume_unit': result['dose_unit'],
                'daily_volume': result['dose_per_administration'] * frequency,
                'actual_duration': treatment_days,
                'total_volume': result['total_volume'],
                'conversions': [],
                'notes': [result.get('medication_name', '')]
            }
            
            if result.get('drops_info'):
                mapped_result['conversions'].append(result['drops_info']['conversion_text'])
            
            return jsonify(mapped_result)
        else:
            return jsonify(result), 400
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': f'Erro no cálculo: {str(e)}'
        }), 500

# ✅ ROTA PARA DASHBOARD DE CÁLCULOS
@main.route('/calculations/dashboard')
@login_required
def calculations_dashboard():
    """Dashboard com estatísticas de cálculos"""
    try:
        # Estatísticas gerais
        total_medications = Medication.query.filter_by(is_active=True).count()
        
        with_config = db.session.query(Medication).join(MedicationDispensing).filter(
            MedicationDispensing.is_active == True,
            Medication.is_active == True
        ).count()
        
        without_config = total_medications - with_config
        
        # Medicamentos por tipo com configuração
        config_by_type = db.session.query(
            Medication.medication_type,
            func.count(Medication.id).label('count')
        ).join(MedicationDispensing).filter(
            MedicationDispensing.is_active == True,
            Medication.is_active == True
        ).group_by(Medication.medication_type).all()
        
        # Medicamentos com configuração de gotas
        with_drops = db.session.query(MedicationDispensing).filter(
            MedicationDispensing.is_active == True,
            MedicationDispensing.drops_per_ml.isnot(None)
        ).count()
        
        # Medicamentos com configuração de estabilidade
        with_stability = db.session.query(MedicationDispensing).filter(
            MedicationDispensing.is_active == True,
            MedicationDispensing.stability_days.isnot(None)
        ).count()
        
        stats = {
            'total_medications': total_medications,
            'with_config': with_config,
            'without_config': without_config,
            'config_percentage': round((with_config / total_medications * 100) if total_medications > 0 else 0, 1),
            'config_by_type': dict(config_by_type),
            'with_drops': with_drops,
            'with_stability': with_stability
        }
        
        return render_template('calculations/dashboard.html', stats=stats)
        
    except Exception as e:
        flash(f'Erro ao carregar dashboard: {str(e)}', 'error')
        return redirect(url_for('main.index'))

# ✅ ROTA PARA RELATÓRIO DE CÁLCULOS
@main.route('/reports/calculations')
@login_required
def calculations_report():
    """Relatório de configurações de cálculo"""
    try:
        # Buscar todos os medicamentos com configuração
        medications_with_config = db.session.query(
            Medication, MedicationDispensing
        ).join(MedicationDispensing).filter(
            MedicationDispensing.is_active == True,
            Medication.is_active == True
        ).order_by(Medication.commercial_name).all()
        
        # Buscar medicamentos sem configuração
        medications_without_config = Medication.query.outerjoin(MedicationDispensing).filter(
            or_(
                MedicationDispensing.id == None,
                MedicationDispensing.is_active == False
            ),
            Medication.is_active == True
        ).order_by(Medication.commercial_name).all()
        
        return render_template('reports/calculations_report.html',
                             medications_with_config=medications_with_config,
                             medications_without_config=medications_without_config)
        
    except Exception as e:
        flash(f'Erro ao gerar relatório: {str(e)}', 'error')
        return redirect(url_for('main.reports_index'))

# ✅ ROTA PARA VALIDAR CONFIGURAÇÃO (AJAX)
@main.route('/api/validate_calculation_config', methods=['POST'])
@login_required
def api_validate_calculation_config():
    """Validar configuração de cálculo via API"""
    try:
        data = request.get_json()
        
        # ✅ AGORA A FUNÇÃO ESTÁ IMPORTADA CORRETAMENTE:
        is_valid, message = validate_medication_configuration(
            strength_value=float(data.get('strength_value', 0)),
            strength_unit=data.get('strength_unit', ''),
            volume_per_dose=float(data.get('volume_per_dose', 0)),
            volume_unit=data.get('volume_unit', ''),
            package_size=float(data.get('package_size', 0)),
            package_unit=data.get('package_unit', '')
        )
        
        return jsonify({
            'valid': is_valid,
            'message': message
        })
        
    except Exception as e:
        return jsonify({
            'valid': False,
            'message': f'Erro na validação: {str(e)}'
        }), 500

# ✅ ROTA PARA EXPORTAR CONFIGURAÇÕES
@main.route('/calculations/export')
@login_required
def export_calculations_config():
    """Exportar configurações de cálculo para Excel"""
    try:
        import pandas as pd
        from io import BytesIO
        
        # Buscar dados
        query = db.session.query(
            Medication.commercial_name,
            Medication.generic_name,
            Medication.dosage,
            Medication.pharmaceutical_form,
            MedicationDispensing.strength_value,
            MedicationDispensing.strength_unit,
            MedicationDispensing.volume_per_dose,
            MedicationDispensing.volume_unit,
            MedicationDispensing.package_size,
            MedicationDispensing.package_unit,
            MedicationDispensing.drops_per_ml,
            MedicationDispensing.stability_days,
            MedicationDispensing.is_active
        ).join(MedicationDispensing).filter(
            Medication.is_active == True
        ).order_by(Medication.commercial_name)
        
        # Converter para DataFrame
        df = pd.DataFrame(query.all(), columns=[
            'Nome Comercial', 'Nome Genérico', 'Dosagem', 'Forma Farmacêutica',
            'Concentração', 'Unidade Concentração', 'Volume por Dose', 'Unidade Volume',
            'Tamanho Embalagem', 'Unidade Embalagem', 'Gotas/ml', 'Estabilidade (dias)', 'Ativo'
        ])
        
        # Criar arquivo Excel
        output = BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            df.to_excel(writer, sheet_name='Configurações Cálculo', index=False)
            
            # Formatação
            workbook = writer.book
            worksheet = writer.sheets['Configurações Cálculo']
            
            # Formato de cabeçalho
            header_format = workbook.add_format({
                'bold': True,
                'text_wrap': True,
                'valign': 'top',
                'fg_color': '#D7E4BC',
                'border': 1
            })
            
            # Aplicar formato
            for col_num, value in enumerate(df.columns.values):
                worksheet.write(0, col_num, value, header_format)
                worksheet.set_column(col_num, col_num, 15)
        
        output.seek(0)
        
        return send_file(
            output,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name=f'configuracoes_calculo_{datetime.now().strftime("%Y%m%d_%H%M%S")}.xlsx'
        )
        
    except Exception as e:
        flash(f'Erro ao exportar: {str(e)}', 'error')
        return redirect(url_for('main.calculations_dashboard'))

@main.route('/calculations/import', methods=['GET', 'POST'])
@login_required
@pharmacist_required  # ✅ ADICIONE ISSO
def import_calculations_config():
    """Importar configurações de cálculo via Excel"""
    if request.method == 'POST':
        try:
            file = request.files.get('file')
            if not file or not file.filename.endswith(('.xlsx', '.xls')):
                flash('Arquivo Excel inválido', 'error')
                return redirect(request.url)
            
            import pandas as pd
            
            # Ler arquivo
            df = pd.read_excel(file)
            
            # Validar colunas esperadas
            required_columns = [
                'Nome Comercial', 'Concentração', 'Unidade Concentração',
                'Volume por Dose', 'Unidade Volume', 'Tamanho Embalagem'
            ]
            
            missing_columns = [col for col in required_columns if col not in df.columns]
            if missing_columns:
                flash(f'Colunas obrigatórias ausentes: {", ".join(missing_columns)}', 'error')
                return redirect(request.url)
            
            imported_count = 0
            errors = []
            
            for index, row in df.iterrows():
                try:
                    # Buscar medicamento
                    medication = Medication.query.filter_by(
                        commercial_name=row['Nome Comercial']
                    ).first()
                    
                    if not medication:
                        errors.append(f"Linha {index + 2}: Medicamento '{row['Nome Comercial']}' não encontrado")
                        continue
                    
                    # Criar ou atualizar configuração
                    config = medication.dispensing_config
                    if not config:
                        config = MedicationDispensing(medication_id=medication.id)
                        db.session.add(config)
                    
                    config.strength_value = float(row['Concentração'])
                    config.strength_unit = str(row['Unidade Concentração'])
                    config.volume_per_dose = float(row['Volume por Dose'])
                    config.volume_unit = str(row['Unidade Volume'])
                    config.package_size = float(row['Tamanho Embalagem'])
                    config.package_unit = str(row.get('Unidade Embalagem', row['Unidade Volume']))
                    
                    # Campos opcionais
                    if 'Gotas/ml' in row and pd.notna(row['Gotas/ml']):
                        config.drops_per_ml = int(row['Gotas/ml'])
                    
                    if 'Estabilidade (dias)' in row and pd.notna(row['Estabilidade (dias)']):
                        config.stability_days = int(row['Estabilidade (dias)'])
                    
                    config.is_active = True
                    imported_count += 1
                    
                except Exception as e:
                    errors.append(f"Linha {index + 2}: {str(e)}")
            
            db.session.commit()
            
            flash(f'{imported_count} configurações importadas com sucesso!', 'success')
            
            if errors:
                error_msg = f"{len(errors)} erros encontrados:\n" + "\n".join(errors[:5])
                if len(errors) > 5:
                    error_msg += f"\n... e mais {len(errors) - 5} erros"
                flash(error_msg, 'warning')
            
        except Exception as e:
            db.session.rollback()
            flash(f'Erro ao importar: {str(e)}', 'error')
    
    return render_template('calculations/import_config.html')



# =================== GESTÃO DE MEDICAMENTOS ===================

@main.route('/inventory')
@staff_required
def inventory_list():
    """Lista de medicamentos com filtros de cálculo"""
    page = request.args.get('page', 1, type=int)
    per_page = current_app.config.get('MEDICATIONS_PER_PAGE', 50)
    
    # Filtros
    search_form = MedicationSearchForm()
    search_term = request.args.get('search_term', '')
    medication_type = request.args.get('medication_type', '')
    low_stock_only = request.args.get('low_stock_only', False, type=bool)
    has_calculations_filter = request.args.get('has_calculations', '')  # ✅ NOVO
    
    query = Medication.query.filter_by(is_active=True)
    
    if search_term:
        query = query.filter(
            or_(
                Medication.commercial_name.ilike(f'%{search_term}%'),
                Medication.generic_name.ilike(f'%{search_term}%')
            )
        )
    
    if medication_type:
        query = query.filter(Medication.medication_type == medication_type)
    
    if low_stock_only:
        query = query.filter(Medication.current_stock <= Medication.minimum_stock)
    
    # ✅ NOVO FILTRO DE CÁLCULOS
    if has_calculations_filter == 'yes':
        query = query.join(MedicationDispensing).filter(MedicationDispensing.is_active == True)
    elif has_calculations_filter == 'no':
        query = query.outerjoin(MedicationDispensing).filter(
            or_(MedicationDispensing.id == None, MedicationDispensing.is_active == False)
        )
    
    medications = query.order_by(Medication.commercial_name).paginate(
        page=page, per_page=per_page, error_out=False
    )
    
    return render_template('inventory/list.html',
                         medications=medications,
                         search_form=search_form,
                         search_term=search_term,
                         medication_type=medication_type,
                         low_stock_only=low_stock_only,
                         has_calculations_filter=has_calculations_filter)  # ✅ NOVO

@main.route('/inventory/create', methods=['GET', 'POST'])
@pharmacist_required
def medication_create():
    """Criar novo medicamento"""
    form = MedicationForm()
    
    if form.validate_on_submit():
        medication = Medication(
            commercial_name=form.commercial_name.data,
            generic_name=form.generic_name.data,
            dosage=form.dosage.data,
            pharmaceutical_form=form.pharmaceutical_form.data,
            medication_type=form.medication_type.data,
            requires_prescription=form.requires_prescription.data,
            controlled_substance=form.controlled_substance.data,
            current_stock=form.current_stock.data,
            minimum_stock=form.minimum_stock.data,
            unit_cost=form.unit_cost.data,
            batch_number=form.batch_number.data,
            expiry_date=form.expiry_date.data
        )
        
        db.session.add(medication)
        db.session.commit()
        
        # Registrar movimento de estoque inicial
        if form.current_stock.data > 0:
            movement = InventoryMovement(
                medication_id=medication.id,
                user_id=current_user.id,
                movement_type='entry',
                quantity=form.current_stock.data,
                previous_stock=0,
                new_stock=form.current_stock.data,
                reason='Estoque inicial'
            )
            db.session.add(movement)
            db.session.commit()
        
        log_action('CREATE', 'medications', medication.id, new_values={
            'commercial_name': medication.commercial_name,
            'generic_name': medication.generic_name
        })
        
        flash(f'Medicamento {medication.commercial_name} cadastrado com sucesso.', 'success')
        return redirect(url_for('main.medication_view', id=medication.id))
    
    return render_template('inventory/form.html', form=form, title='Novo Medicamento')

@main.route('/inventory/<int:id>')
@staff_required
def medication_view(id):
    """Visualizar medicamento com configurações de cálculo"""
    medication = Medication.query.get_or_404(id)
    
    # Movimentações recentes
    movements = InventoryMovement.query.filter_by(medication_id=id).order_by(
        desc(InventoryMovement.movement_date)
    ).limit(20).all()
    
    return render_template('inventory/view.html', 
                         medication=medication,
                         movements=movements,
                         current_date=date.today(),
                         has_calculation_config=medication.has_dispensing_config,  # ✅ NOVO
                         calculation_config=medication.dispensing_config)         # ✅ NOVO

@main.route('/inventory/<int:id>/edit', methods=['GET', 'POST'])
@pharmacist_required
def medication_edit(id):
    """Editar medicamento"""
    medication = Medication.query.get_or_404(id)
    form = MedicationForm(obj=medication)
    
    if form.validate_on_submit():
        old_values = {
            'commercial_name': medication.commercial_name,
            'current_stock': medication.current_stock,
            'unit_cost': float(medication.unit_cost) if medication.unit_cost else None
        }
        
        # Verificar se estoque mudou
        stock_changed = form.current_stock.data != medication.current_stock
        old_stock = medication.current_stock
        
        # Atualizar dados
        medication.commercial_name = form.commercial_name.data
        medication.generic_name = form.generic_name.data
        medication.dosage = form.dosage.data
        medication.pharmaceutical_form = form.pharmaceutical_form.data
        medication.medication_type = form.medication_type.data
        medication.requires_prescription = form.requires_prescription.data
        medication.controlled_substance = form.controlled_substance.data
        medication.current_stock = form.current_stock.data
        medication.minimum_stock = form.minimum_stock.data
        medication.unit_cost = form.unit_cost.data
        medication.batch_number = form.batch_number.data
        medication.expiry_date = form.expiry_date.data
        medication.updated_at = datetime.utcnow()
        
        db.session.commit()
        
        # Registrar movimento se estoque mudou
        if stock_changed:
            movement_type = 'entry' if form.current_stock.data > old_stock else 'adjustment'
            quantity = abs(form.current_stock.data - old_stock)
            
            movement = InventoryMovement(
                medication_id=medication.id,
                user_id=current_user.id,
                movement_type=movement_type,
                quantity=quantity,
                previous_stock=old_stock,
                new_stock=form.current_stock.data,
                reason='Ajuste manual via edição'
            )
            db.session.add(movement)
            db.session.commit()
        
        log_action('UPDATE', 'medications', medication.id, 
                  old_values=old_values,
                  new_values={
                      'commercial_name': medication.commercial_name,
                      'current_stock': medication.current_stock,
                      'unit_cost': float(medication.unit_cost) if medication.unit_cost else None
                  })
        
        flash(f'Medicamento {medication.commercial_name} atualizado com sucesso.', 'success')
        return redirect(url_for('main.medication_view', id=medication.id))
    
    return render_template('inventory/form.html', form=form, title='Editar Medicamento', medication=medication)

@main.route('/inventory/stock-entry', methods=['GET', 'POST'])
@pharmacist_required
def stock_entry():
    """Entrada de estoque"""
    form = StockEntryForm()
    
    # Carregar medicamentos para o select
    medications = Medication.query.filter_by(is_active=True).order_by(Medication.commercial_name).all()
    form.medication_id.choices = [(m.id, f"{m.commercial_name} - {m.dosage}") for m in medications]
    
    if form.validate_on_submit():
        medication = Medication.query.get(form.medication_id.data)
        old_stock = medication.current_stock
        new_stock = old_stock + form.quantity.data
        
        # Atualizar estoque
        medication.current_stock = new_stock
        if form.unit_cost.data:
            medication.unit_cost = form.unit_cost.data
        if form.batch_number.data:
            medication.batch_number = form.batch_number.data
        if form.expiry_date.data:
            medication.expiry_date = form.expiry_date.data
        
        # Registrar movimento
        movement = InventoryMovement(
            medication_id=medication.id,
            user_id=current_user.id,
            movement_type='entry',
            quantity=form.quantity.data,
            previous_stock=old_stock,
            new_stock=new_stock,
            reason=form.reason.data or 'Entrada de estoque'
        )
        
        db.session.add(movement)
        db.session.commit()
        
        log_action('STOCK_ENTRY', 'medications', medication.id, new_values={
            'quantity_added': form.quantity.data,
            'new_stock': new_stock
        })
        
        flash(f'Entrada de {form.quantity.data} unidades de {medication.commercial_name} registrada.', 'success')
        return redirect(url_for('main.medication_view', id=medication.id))
    
    return render_template('inventory/stock_entry.html', form=form)

@main.route('/inventory/alerts')
@pharmacist_required
def inventory_alerts():
    """Alertas de estoque e validade"""
    # Medicamentos com estoque baixo
    low_stock = Medication.query.filter(
        and_(
            Medication.is_active == True,
            Medication.current_stock <= Medication.minimum_stock
        )
    ).order_by(Medication.current_stock).all()
    
    # Medicamentos próximos ao vencimento (30 dias)
    near_expiry = Medication.query.filter(
        and_(
            Medication.is_active == True,
            Medication.expiry_date.isnot(None),
            Medication.expiry_date <= date.today() + timedelta(days=30)
        )
    ).order_by(Medication.expiry_date).all()
    
    # Medicamentos vencidos
    expired = Medication.query.filter(
        and_(
            Medication.is_active == True,
            Medication.expiry_date.isnot(None),
            Medication.expiry_date < date.today()
        )
    ).order_by(Medication.expiry_date).all()
    
    return render_template('inventory/alerts.html',
                         low_stock=low_stock,
                         near_expiry=near_expiry,
                         expired=expired)

# =================== ALTO CUSTO ===================

@main.route('/high-cost')
@pharmacist_required
def high_cost_index():
    """Lista de processos alto custo"""
    page = request.args.get('page', 1, type=int)
    status_filter = request.args.get('status', '')
    
    query = HighCostProcess.query
    
    if status_filter:
        query = query.filter(HighCostProcess.status == status_filter)
    
    processes = query.order_by(desc(HighCostProcess.request_date)).paginate(
        page=page, per_page=15, error_out=False
    )
    
    # Estatísticas rápidas
    stats = {
        'pending': HighCostProcess.query.filter_by(status=ProcessStatus.PENDING).count(),
        'under_evaluation': HighCostProcess.query.filter_by(status=ProcessStatus.UNDER_EVALUATION).count(),
        'approved': HighCostProcess.query.filter_by(status=ProcessStatus.APPROVED).count(),
        'denied': HighCostProcess.query.filter_by(status=ProcessStatus.DENIED).count()
    }
    
    return render_template('high_cost/index.html',
                         processes=processes,
                         stats=stats,
                         status_filter=status_filter)

@main.route('/high-cost/request/<int:patient_id>', methods=['GET', 'POST'])
@pharmacist_required
def high_cost_request(patient_id):
    """Solicitar medicamento alto custo"""
    patient = Patient.query.get_or_404(patient_id)
    form = HighCostRequestForm()
    
    # Carregar medicamentos alto custo
    high_cost_meds = Medication.query.filter_by(
        medication_type=MedicationType.HIGH_COST,
        is_active=True
    ).order_by(Medication.commercial_name).all()
    
    form.medication_id.choices = [(m.id, f"{m.commercial_name} - {m.dosage}") for m in high_cost_meds]
    form.patient_id.data = patient_id
    
    if form.validate_on_submit():
        # Criar processo
        process = HighCostProcess(
            patient_id=patient_id,
            medication_id=form.medication_id.data,
            cid10=form.cid10.data.upper(),
            diagnosis=form.diagnosis.data,
            doctor_name=form.doctor_name.data,
            doctor_crm=form.doctor_crm.data,
            requested_quantity=form.requested_quantity.data,
            treatment_duration=form.treatment_duration.data,
            justification=form.justification.data,
            urgency_level=form.urgency_level.data
        )
        
        # Gerar protocolo
        process.generate_protocol()
        
        db.session.add(process)
        db.session.flush()  # Para obter o ID
        
        # Salvar documentos
        documents_saved = []
        
        if form.prescription_file.data:
            file_info = save_uploaded_file(form.prescription_file.data, 'prescriptions')
            if file_info:
                doc = ProcessDocument(
                    process_id=process.id,
                    document_type='prescription',
                    filename=file_info['filename'],
                    original_filename=file_info['original_filename'],
                    file_path=file_info['file_path'],
                    file_size=file_info['file_size'],
                    mime_type=file_info['mime_type'],
                    is_required=True
                )
                db.session.add(doc)
                documents_saved.append('Receita')
        
        if form.medical_report_file.data:
            file_info = save_uploaded_file(form.medical_report_file.data, 'medical_reports')
            if file_info:
                doc = ProcessDocument(
                    process_id=process.id,
                    document_type='medical_report',
                    filename=file_info['filename'],
                    original_filename=file_info['original_filename'],
                    file_path=file_info['file_path'],
                    file_size=file_info['file_size'],
                    mime_type=file_info['mime_type'],
                    is_required=False
                )
                db.session.add(doc)
                documents_saved.append('Laudo médico')
        
        if form.exam_file.data:
            file_info = save_uploaded_file(form.exam_file.data, 'documents')
            if file_info:
                doc = ProcessDocument(
                    process_id=process.id,
                    document_type='exam',
                    filename=file_info['filename'],
                    original_filename=file_info['original_filename'],
                    file_path=file_info['file_path'],
                    file_size=file_info['file_size'],
                    mime_type=file_info['mime_type'],
                    is_required=False
                )
                db.session.add(doc)
                documents_saved.append('Exames')
        
        db.session.commit()
        
        log_action('CREATE', 'high_cost_processes', process.id, new_values={
            'protocol': process.protocol_number,
            'patient_id': patient_id,
            'medication_id': form.medication_id.data
        })
        
        docs_text = ', '.join(documents_saved) if documents_saved else 'nenhum documento'
        flash(f'Processo {process.protocol_number} criado com sucesso! Documentos salvos: {docs_text}', 'success')
        return redirect(url_for('main.high_cost_view', id=process.id))
    
    return render_template('high_cost/request_form.html', form=form, patient=patient)

@main.route('/high-cost/<int:id>')
@pharmacist_required
def high_cost_view(id):
    """Visualizar processo alto custo"""
    process = HighCostProcess.query.options(
        joinedload(HighCostProcess.patient),
        joinedload(HighCostProcess.medication),
        joinedload(HighCostProcess.documents),
        joinedload(HighCostProcess.evaluations),
        joinedload(HighCostProcess.approvals),
        joinedload(HighCostProcess.dispensations)
    ).get_or_404(id)
    
    return render_template('high_cost/view_process.html', process=process)

@main.route('/high-cost/<int:id>/evaluate', methods=['GET', 'POST'])
@pharmacist_required
def high_cost_evaluate(id):
    """Avaliar processo alto custo"""
    process = HighCostProcess.query.get_or_404(id)
    
    if process.status not in [ProcessStatus.PENDING, ProcessStatus.UNDER_EVALUATION]:
        flash('Este processo não pode mais ser avaliado.', 'error')
        return redirect(url_for('main.high_cost_view', id=id))
    
    form = PharmaceuticalEvaluationForm()
    form.process_id.data = id
    
    if form.validate_on_submit():
        # Atualizar status do processo
        process.status = ProcessStatus.UNDER_EVALUATION
        process.evaluation_date = datetime.utcnow()
        
        # Criar avaliação
        evaluation = PharmaceuticalEvaluation(
            process_id=id,
            evaluator_id=current_user.id,
            technical_opinion=form.technical_opinion.data,
            meets_protocol=bool(int(form.meets_protocol.data)),
            recommended_quantity=form.recommended_quantity.data,
            recommended_duration=form.recommended_duration.data,
            recommendation=form.recommendation.data,
            observations=form.observations.data
        )
        
        db.session.add(evaluation)
        db.session.commit()
        
        log_action('CREATE', 'pharmaceutical_evaluations', evaluation.id, new_values={
            'process_id': id,
            'recommendation': form.recommendation.data
        })
        
        flash('Avaliação farmacêutica registrada com sucesso.', 'success')
        return redirect(url_for('main.high_cost_view', id=id))
    
    return render_template('high_cost/evaluation.html', form=form, process=process)

@main.route('/high-cost/<int:id>/approve', methods=['GET', 'POST'])
@admin_required
def high_cost_approve(id):
    """Aprovar/Negar processo alto custo"""
    process = HighCostProcess.query.get_or_404(id)
    
    if process.status != ProcessStatus.UNDER_EVALUATION:
        flash('Este processo não está em condições de ser aprovado.', 'error')
        return redirect(url_for('main.high_cost_view', id=id))
    
    form = ProcessApprovalForm()
    form.process_id.data = id
    
    if form.validate_on_submit():
        # Atualizar status do processo
        if form.decision.data == 'approved':
            process.status = ProcessStatus.APPROVED
        else:
            process.status = ProcessStatus.DENIED
        
        process.approval_date = datetime.utcnow()
        
        # Criar aprovação
        approval = ProcessApproval(
            process_id=id,
            approver_id=current_user.id,
            decision=form.decision.data,
            approved_quantity=form.approved_quantity.data,
            approved_duration=form.approved_duration.data,
            justification=form.justification.data,
            special_conditions=form.special_conditions.data,
            approval_expires_at=form.approval_expires_at.data
        )
        
        db.session.add(approval)
        db.session.commit()
        
        log_action('CREATE', 'process_approvals', approval.id, new_values={
            'process_id': id,
            'decision': form.decision.data
        })
        
        status_text = 'aprovado' if form.decision.data == 'approved' else 'negado'
        flash(f'Processo {process.protocol_number} {status_text} com sucesso.', 'success')
        return redirect(url_for('main.high_cost_view', id=id))
    
    return render_template('high_cost/approval.html', form=form, process=process)

@main.route('/high-cost/<int:id>/dispense', methods=['GET', 'POST'])
@pharmacist_required
def high_cost_dispense(id):
    """Dispensar medicamento alto custo"""
    process = HighCostProcess.query.get_or_404(id)
    
    if process.status != ProcessStatus.APPROVED:
        flash('Este processo não está aprovado para dispensação.', 'error')
        return redirect(url_for('main.high_cost_view', id=id))
    
    form = HighCostDispensationForm()
    form.process_id.data = id
    
    # Definir quantidade máxima baseada na aprovação
    approval = process.approvals[-1] if process.approvals else None
    max_quantity = approval.approved_quantity if approval else process.requested_quantity
    
    if form.validate_on_submit():
        # Verificar estoque
        medication = process.medication
        if medication.current_stock < form.quantity_dispensed.data:
            flash(f'Estoque insuficiente. Disponível: {medication.current_stock}', 'error')
            return render_template('high_cost/dispensation.html', form=form, process=process, max_quantity=max_quantity)
        
        # Criar dispensação
        dispensation = HighCostDispensation(
            process_id=id,
            dispenser_id=current_user.id,
            quantity_dispensed=form.quantity_dispensed.data,
            next_dispensation_date=form.next_dispensation_date.data,
            patient_signature=form.patient_signature.data,
            terms_accepted=form.terms_accepted.data,
            observations=form.observations.data
        )
        
        db.session.add(dispensation)
        db.session.flush()
        
        # Atualizar estoque
        old_stock = medication.current_stock
        medication.current_stock -= form.quantity_dispensed.data
        
        # Registrar movimento
        movement = InventoryMovement(
            medication_id=medication.id,
            user_id=current_user.id,
            movement_type='exit',
            quantity=form.quantity_dispensed.data,
            previous_stock=old_stock,
            new_stock=medication.current_stock,
            reason=f'Dispensação Alto Custo #{process.protocol_number}',
            reference_id=dispensation.id,
            reference_type='high_cost_dispensation'
        )
        
        db.session.add(movement)
        
        # Atualizar status do processo
        process.status = ProcessStatus.DISPENSED
        process.dispensation_date = datetime.utcnow()
        
        db.session.commit()
        
        log_action('CREATE', 'high_cost_dispensations', dispensation.id, new_values={
            'process_id': id,
            'quantity_dispensed': form.quantity_dispensed.data
        })
        
        flash(f'Dispensação de {form.quantity_dispensed.data} unidades realizada com sucesso.', 'success')
        return redirect(url_for('main.high_cost_view', id=id))
    
    return render_template('high_cost/dispensation.html', form=form, process=process, max_quantity=max_quantity)

@main.route('/high-cost/new', methods=['GET', 'POST'])
@pharmacist_required
def high_cost_new():
    """✅ SELEÇÃO DE PACIENTE PARA NOVO PROCESSO ALTO CUSTO COM E-SUS"""
    if request.method == 'POST':
        patient_id = request.form.get('patient_id')
        if patient_id:
            return redirect(url_for('main.high_cost_request', patient_id=patient_id))
        else:
            flash('Por favor, selecione um paciente.', 'error')
    
    # Busca de pacientes
    search_term = request.args.get('search', '')
    local_patients = []
    esus_patients = []
    
    if search_term:
        # Usar busca integrada
        local_results, esus_results = Patient.search_integrated(search_term)
        local_patients = local_results[:10]  # Limitar a 10
        
        # Se não encontrou no local, mostrar do e-SUS
        if not local_patients and esus_results:
            esus_patients = esus_results[:10]
    
    return render_template('high_cost/select_patient.html', 
                         local_patients=local_patients,
                         esus_patients=esus_patients,
                         search_term=search_term)

# =================== ACOMPANHAMENTO DE PACIENTES ===================

@main.route('/high-cost/<int:process_id>/tracking', methods=['GET', 'POST'])
@pharmacist_required
def patient_tracking(process_id):
    """Acompanhamento de paciente alto custo"""
    process = HighCostProcess.query.get_or_404(process_id)
    patient = process.patient
    
    # Buscar acompanhamentos existentes
    tracking_records = PatientTracking.query.filter_by(
        patient_id=patient.id
    ).order_by(desc(PatientTracking.tracking_date)).all()
    
    if request.method == 'POST':
        # Criar novo registro de acompanhamento
        tracking_type = request.form.get('tracking_type')
        clinical_response = request.form.get('clinical_response')
        adverse_reactions = request.form.get('adverse_reactions')
        notes = request.form.get('notes')
        next_tracking_date = request.form.get('next_tracking_date')
        
        if tracking_type:
            tracking = PatientTracking(
                patient_id=patient.id,
                dispensation_id=None,  # Pode ser associado a uma dispensação específica
                tracking_date=date.today(),
                tracking_type=tracking_type,
                clinical_response=clinical_response if clinical_response else None,
                adverse_reactions=adverse_reactions,
                notes=notes,
                next_tracking_date=parse_date(next_tracking_date) if next_tracking_date else None
            )
            
            db.session.add(tracking)
            db.session.commit()
            
            log_action('CREATE', 'patient_tracking', tracking.id, new_values={
                'patient_id': patient.id,
                'tracking_type': tracking_type
            })
            
            flash('Registro de acompanhamento salvo com sucesso.', 'success')
            return redirect(url_for('main.patient_tracking', process_id=process_id))
    
    return render_template('high_cost/tracking.html', 
                         process=process, 
                         patient=patient,
                         tracking_records=tracking_records)

# =================== RELATÓRIOS ===================

@main.route('/reports')
@staff_required
def reports_index():
    """Página principal de relatórios"""
    form = ReportForm()
    return render_template('reports/index.html', form=form)

@main.route('/reports/generate', methods=['GET', 'POST'])
@staff_required
def reports_generate():
    """Gerar relatório"""
    
    # ✅ DETECTAR SE É GET OU POST
    if request.method == 'GET':
        # Capturar parâmetros do GET
        report_type = request.args.get('report_type')
        start_date = None
        end_date = None
        format_type = request.args.get('format_type', 'html')
        age_range = request.args.get('age_range')
        gender = request.args.get('gender') 
        registration_period = request.args.get('registration_period')
        
        # ✅ VALIDAÇÃO BÁSICA PARA GET
        if not report_type:
            flash('Tipo de relatório não especificado.', 'error')
            return redirect(url_for('main.reports_index'))
            
    else:  # POST
        # Usar formulário original
        form = ReportForm()
        
        if not form.validate_on_submit():
            flash('Dados do formulário inválidos.', 'error')
            return redirect(url_for('main.reports_index'))
        
        report_type = form.report_type.data
        start_date = form.start_date.data
        end_date = form.end_date.data
        format_type = form.format_type.data
        age_range = None
        gender = None
        registration_period = None
    
    try:
        if report_type == 'consumption':
            return generate_consumption_report(start_date, end_date, format_type)
        elif report_type == 'stock':
            return generate_stock_report(format_type)
        elif report_type == 'expiry':
            return generate_expiry_report(format_type)
        elif report_type == 'high_cost':
            return generate_high_cost_report(start_date, end_date, format_type)
        elif report_type == 'dispensations':
            return generate_dispensations_report(start_date, end_date, format_type)
        elif report_type == 'patients':
            return generate_patients_report(format_type, age_range, gender, registration_period)
        elif report_type == 'financial':
            return generate_financial_report(start_date, end_date, format_type)
        elif report_type == 'esus_integration':  # ✅ NOVO RELATÓRIO E-SUS
            return generate_esus_integration_report(format_type)
        else:
            flash('Tipo de relatório inválido.', 'error')
            return redirect(url_for('main.reports_index'))
    
    except Exception as e:
        flash(f'Erro ao gerar relatório: {str(e)}', 'error')
        return redirect(url_for('main.reports_index'))

# ✅ NOVO RELATÓRIO DE INTEGRAÇÃO E-SUS
def generate_esus_integration_report(format_type):
    """Relatório de integração e-SUS"""
    # Estatísticas da integração
    stats = {
        'total_patients': Patient.query.filter_by(is_active=True).count(),
        'local_patients': Patient.query.filter_by(source='local', is_active=True).count(),
        'imported_patients': Patient.query.filter_by(source='imported', is_active=True).count(),
        'esus_patients': Patient.query.filter_by(source='esus', is_active=True).count(),
        'synced_patients': Patient.query.filter(Patient.esus_sync_date.isnot(None)).count(),
        'patients_with_cns': Patient.query.filter(
            and_(Patient.cns.isnot(None), Patient.is_active == True)
        ).count()
    }
    
    # Pacientes importados recentemente (últimos 30 dias)
    recent_imports = Patient.query.filter(
        and_(
            Patient.source == 'imported',
            Patient.created_at >= datetime.now() - timedelta(days=30)
        )
    ).order_by(desc(Patient.created_at)).all()
    
    # Qualidade dos dados
    quality_stats = {
        'complete_address': Patient.query.filter(
            and_(
                Patient.address.isnot(None),
                Patient.neighborhood.isnot(None),
                Patient.zip_code.isnot(None),
                Patient.is_active == True
            )
        ).count(),
        'with_phone': Patient.query.filter(
            and_(
                or_(
                    Patient.phone.isnot(None),
                    Patient.cell_phone.isnot(None),
                    Patient.contact_phone.isnot(None)
                ),
                Patient.is_active == True
            )
        ).count(),
        'with_parents_info': Patient.query.filter(
            and_(
                or_(
                    Patient.mother_name.isnot(None),
                    Patient.father_name.isnot(None)
                ),
                Patient.is_active == True
            )
        ).count()
    }
    
    if format_type == 'html':
        return render_template('reports/esus_integration.html',
                             stats=stats,
                             quality_stats=quality_stats,
                             recent_imports=recent_imports)
    elif format_type == 'pdf':
        return generate_esus_integration_pdf(stats, quality_stats, recent_imports)
    elif format_type == 'excel':
        return generate_esus_integration_excel(stats, quality_stats, recent_imports)

# [CONTINUAR COM OUTRAS FUNÇÕES DE RELATÓRIO...]

def generate_consumption_report(start_date, end_date, format_type):
    """Relatório de consumo - APENAS DADOS REAIS"""
    query = db.session.query(
        Medication.commercial_name,
        Medication.generic_name,
        Medication.dosage,
        func.sum(DispensationItem.quantity_dispensed).label('total_dispensed'),
        func.count(DispensationItem.id).label('dispensations_count'),
        func.sum(DispensationItem.total_cost).label('total_cost')
    ).join(DispensationItem).join(Dispensation)
    
    if start_date:
        query = query.filter(Dispensation.dispensation_date >= start_date)
    if end_date:
        query = query.filter(Dispensation.dispensation_date <= end_date)
    
    data = query.group_by(
        Medication.id, Medication.commercial_name, 
        Medication.generic_name, Medication.dosage
    ).order_by(desc('total_dispensed')).all()
    
    if format_type == 'html':
        return render_template('reports/consumption.html', data=data, 
                             start_date=start_date, end_date=end_date)
    elif format_type == 'pdf':
        return generate_consumption_pdf(data, start_date, end_date)
    elif format_type == 'excel':
        return generate_consumption_excel(data, start_date, end_date)

def generate_stock_report(format_type):
    """Relatório de estoque - APENAS DADOS REAIS"""
    medications = Medication.query.filter_by(is_active=True).order_by(
        Medication.commercial_name
    ).all()
    
    total_value = sum(
        (med.current_stock * (med.unit_cost or 0)) for med in medications
    )
    
    if format_type == 'html':
        return render_template('reports/stock.html', 
                             medications=medications, 
                             total_value=total_value)
    elif format_type == 'pdf':
        return generate_stock_pdf(medications, total_value)
    elif format_type == 'excel':
        return generate_stock_excel(medications, total_value)

def generate_expiry_report(format_type):
    """Relatório de vencimentos - APENAS DADOS REAIS"""
    # Próximos 30 dias
    near_expiry = Medication.query.filter(
        and_(
            Medication.is_active == True,
            Medication.expiry_date.isnot(None),
            Medication.expiry_date <= date.today() + timedelta(days=30),
            Medication.expiry_date >= date.today()
        )
    ).order_by(Medication.expiry_date).all()
    
    # Vencidos
    expired = Medication.query.filter(
        and_(
            Medication.is_active == True,
            Medication.expiry_date.isnot(None),
            Medication.expiry_date < date.today()
        )
    ).order_by(Medication.expiry_date).all()
    
    if format_type == 'html':
        return render_template('reports/expiry.html', 
                             near_expiry=near_expiry, 
                             expired=expired)
    elif format_type == 'pdf':
        return generate_expiry_pdf(near_expiry, expired)
    elif format_type == 'excel':
        return generate_expiry_excel(near_expiry, expired)

def generate_high_cost_report(start_date, end_date, format_type):
    """Relatório de processos alto custo - APENAS DADOS REAIS"""
    
    query = HighCostProcess.query.options(
        joinedload(HighCostProcess.patient),
        joinedload(HighCostProcess.medication)
    )
    
    # Aplicar filtros de data se fornecidos
    if start_date:
        query = query.filter(HighCostProcess.request_date >= start_date)
    if end_date:
        query = query.filter(HighCostProcess.request_date <= end_date)
    
    # Buscar processos ordenados por data
    processes = query.order_by(desc(HighCostProcess.request_date)).all()
    
    # Calcular estatísticas reais dos dados
    total_processes = len(processes)
    
    # Contar por status usando os valores corretos do enum
    stats = {
        'total': total_processes,
        'pending': 0,
        'under_evaluation': 0,
        'approved': 0,
        'denied': 0,
        'dispensed': 0,
        'completed': 0,
        'cancelled': 0
    }
    
    for process in processes:
        status_value = process.status.value if hasattr(process.status, 'value') else str(process.status)
        
        if status_value == ProcessStatus.PENDING.value:
            stats['pending'] += 1
        elif status_value == ProcessStatus.UNDER_EVALUATION.value:
            stats['under_evaluation'] += 1
        elif status_value == ProcessStatus.APPROVED.value:
            stats['approved'] += 1
        elif status_value == ProcessStatus.DENIED.value:
            stats['denied'] += 1
        elif status_value == ProcessStatus.DISPENSED.value:
            stats['dispensed'] += 1
        elif status_value == ProcessStatus.COMPLETED.value:
            stats['completed'] += 1
        elif status_value == ProcessStatus.CANCELLED.value:
            stats['cancelled'] += 1
    
    if format_type == 'html':
        return render_template('reports/high_cost_reports.html', 
                             processes=processes, 
                             stats=stats,
                             start_date=start_date, 
                             end_date=end_date)
    elif format_type == 'pdf':
        return generate_high_cost_pdf(processes, stats, start_date, end_date)
    elif format_type == 'excel':
        return generate_high_cost_excel(processes, stats, start_date, end_date)

def generate_dispensations_report(start_date, end_date, format_type):
    """Relatório de dispensações - APENAS DADOS REAIS"""
    query = Dispensation.query.options(
        joinedload(Dispensation.patient),
        joinedload(Dispensation.dispenser),
        joinedload(Dispensation.items)
    )
    
    if start_date:
        query = query.filter(Dispensation.dispensation_date >= start_date)
    if end_date:
        query = query.filter(Dispensation.dispensation_date <= end_date)
    
    dispensations = query.order_by(desc(Dispensation.dispensation_date)).all()
    
    # Estatísticas reais
    total_cost = sum(d.total_cost or 0 for d in dispensations)
    total_items = sum(len(d.items) for d in dispensations)
    
    if format_type == 'html':
        return render_template('reports/dispensations.html', 
                             dispensations=dispensations,
                             total_cost=total_cost,
                             total_items=total_items,
                             start_date=start_date, 
                             end_date=end_date)
    elif format_type == 'pdf':
        return generate_dispensations_pdf(dispensations, total_cost, total_items, start_date, end_date)
    elif format_type == 'excel':
        return generate_dispensations_excel(dispensations, total_cost, total_items, start_date, end_date)

# =================== APIS CORRIGIDAS PARA RELATÓRIO ===================

@main.route('/api/patients/datatable', methods=['POST'])
@staff_required
def api_patients_datatable():
    """✅ API DataTables server-side CORRIGIDA"""
    try:
        # Parâmetros do DataTables
        draw = request.form.get('draw', type=int)
        start = request.form.get('start', type=int)
        length = request.form.get('length', type=int)
        search_value = request.form.get('search[value]', '')
        
        # Filtros customizados
        age_range = request.form.get('age_range', '')
        gender = request.form.get('gender', '')
        registration_period = request.form.get('registration_period', '')
        
        # Query base
        query = Patient.query.filter_by(is_active=True)
        
        # ✅ APLICAR FILTROS DE IDADE (CORRIGIDO)
        if age_range:
            current_year = datetime.now().year
            if age_range == '0-18':
                min_birth_year = current_year - 18
                query = query.filter(extract('year', Patient.birth_date) >= min_birth_year)
            elif age_range == '19-30':
                min_birth_year = current_year - 30
                max_birth_year = current_year - 19
                query = query.filter(
                    and_(
                        extract('year', Patient.birth_date) >= min_birth_year,
                        extract('year', Patient.birth_date) <= max_birth_year
                    )
                )
            elif age_range == '31-50':
                min_birth_year = current_year - 50
                max_birth_year = current_year - 31
                query = query.filter(
                    and_(
                        extract('year', Patient.birth_date) >= min_birth_year,
                        extract('year', Patient.birth_date) <= max_birth_year
                    )
                )
            elif age_range == '51-65':
                min_birth_year = current_year - 65
                max_birth_year = current_year - 51
                query = query.filter(
                    and_(
                        extract('year', Patient.birth_date) >= min_birth_year,
                        extract('year', Patient.birth_date) <= max_birth_year
                    )
                )
            elif age_range == '65+':
                max_birth_year = current_year - 65
                query = query.filter(extract('year', Patient.birth_date) <= max_birth_year)
        
        # ✅ APLICAR FILTRO DE GÊNERO
        if gender:
            query = query.filter(Patient.gender == gender)
        
        # ✅ APLICAR FILTRO DE PERÍODO DE CADASTRO
        if registration_period:
            try:
                days = int(registration_period)
                cutoff_date = datetime.now() - timedelta(days=days)
                query = query.filter(Patient.created_at >= cutoff_date)
            except ValueError:
                pass  # Ignorar valor inválido
        
        # ✅ BUSCA GLOBAL
        if search_value:
            search_filter = or_(
                Patient.full_name.ilike(f'%{search_value}%'),
                Patient.cpf.like(f'%{search_value}%'),
                Patient.city.ilike(f'%{search_value}%')
            )
            query = query.filter(search_filter)
        
        # ✅ TOTAL DE REGISTROS (SEM FILTROS)
        total_records = Patient.query.filter_by(is_active=True).count()
        
        # ✅ TOTAL FILTRADO
        filtered_records = query.count()
        
        # ✅ ORDENAÇÃO
        order_column = request.form.get('order[0][column]', '0', type=int)
        order_dir = request.form.get('order[0][dir]', 'asc')
        
        if order_column == 6:  # Data de cadastro
            if order_dir == 'desc':
                query = query.order_by(desc(Patient.created_at))
            else:
                query = query.order_by(Patient.created_at)
        else:
            query = query.order_by(Patient.full_name)
        
        # ✅ PAGINAÇÃO
        patients = query.offset(start).limit(length).all()
        
        # ✅ FORMATAR DADOS PARA DATATABLE
        data = []
        for patient in patients:
            data.append({
                'full_name': patient.full_name,
                'formatted_cpf': patient.formatted_cpf,
                'formatted_cns': patient.formatted_cns if patient.cns else '--',
                'age': patient.age,
                'gender_display': patient.gender_display,
                'city': patient.city or '--',
                'created_at': format_date(patient.created_at),
                'status_badge': f'<span class="badge bg-success">Ativo</span>' if patient.is_active else f'<span class="badge bg-secondary">Inativo</span>',
                'actions': f'''
                    <div class="btn-group btn-group-sm">
                        <a href="{url_for('main.patient_view', id=patient.id)}" class="btn btn-outline-primary" title="Visualizar">
                            <i class="fas fa-eye"></i>
                        </a>
                        <a href="{url_for('main.patient_edit', id=patient.id)}" class="btn btn-outline-secondary" title="Editar">
                            <i class="fas fa-edit"></i>
                        </a>
                    </div>
                '''
            })
        
        return jsonify({
            'draw': draw,
            'recordsTotal': total_records,
            'recordsFiltered': filtered_records,
            'data': data
        })
        
    except Exception as e:
        current_app.logger.error(f"Erro na API DataTables: {e}")
        return jsonify({
            'draw': draw or 1,
            'recordsTotal': 0,
            'recordsFiltered': 0,
            'data': [],
            'error': str(e)
        }), 500

@main.route('/api/patients/charts')
@staff_required
def api_patients_charts():
    """✅ API para dados dos gráficos CORRIGIDA"""
    try:
        # ✅ DADOS DE GÊNERO
        gender_counts = {'M': 0, 'F': 0, 'O': 0, 'N': 0}
        
        # Buscar todos os gêneros dos pacientes ativos
        patients = Patient.query.filter_by(is_active=True).all()
        
        for patient in patients:
            if patient.gender:
                gender_counts[patient.gender] = gender_counts.get(patient.gender, 0) + 1
            else:
                gender_counts['N'] += 1  # Não informado
        
        gender_data = {
            'labels': ['Masculino', 'Feminino', 'Outro', 'Não informar'],
            'values': [gender_counts['M'], gender_counts['F'], gender_counts['O'], gender_counts['N']]
        }
        
        # ✅ DADOS DE IDADE
        age_groups = {'0-18': 0, '19-30': 0, '31-50': 0, '51-65': 0, '65+': 0}
        
        for patient in patients:
            age = patient.age
            if age <= 18:
                age_groups['0-18'] += 1
            elif age <= 30:
                age_groups['19-30'] += 1
            elif age <= 50:
                age_groups['31-50'] += 1
            elif age <= 65:
                age_groups['51-65'] += 1
            else:
                age_groups['65+'] += 1
        
        age_data = {
            'labels': list(age_groups.keys()),
            'values': list(age_groups.values())
        }
        
        # ✅ DADOS DE CADASTROS POR MÊS (últimos 6 meses)
        registration_data = {
            'labels': [],
            'values': []
        }
        
        for i in range(5, -1, -1):
            # Calcular início e fim do mês
            target_date = datetime.now() - timedelta(days=i*30)
            month_start = target_date.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            
            if i == 0:
                month_end = datetime.now()
            else:
                next_month = month_start.replace(month=month_start.month + 1) if month_start.month < 12 else month_start.replace(year=month_start.year + 1, month=1)
                month_end = next_month
            
            count = Patient.query.filter(
                and_(
                    Patient.created_at >= month_start,
                    Patient.created_at < month_end,
                    Patient.is_active == True
                )
            ).count()
            
            registration_data['labels'].append(month_start.strftime('%b'))
            registration_data['values'].append(count)
        
        return jsonify({
            'success': True,
            'gender_data': gender_data,
            'age_data': age_data,
            'registration_data': registration_data
        })
        
    except Exception as e:
        current_app.logger.error(f"Erro na API de gráficos: {e}")
        return jsonify({
            'success': False,
            'message': str(e),
            'gender_data': {'labels': [], 'values': []},
            'age_data': {'labels': [], 'values': []},
            'registration_data': {'labels': [], 'values': []}
        }), 200  # Retornar 200 com dados vazios

@main.route('/api/patients/geographic')
@staff_required
def api_patients_geographic():
    """✅ API para dados geográficos CORRIGIDA"""
    try:
        # ✅ DADOS POR CIDADE (usando Python para contar)
        cities_count = {}
        states_count = {}
        
        patients = Patient.query.filter_by(is_active=True).all()
        
        for patient in patients:
            # Contar cidades
            if patient.city:
                cities_count[patient.city] = cities_count.get(patient.city, 0) + 1
            
            # Contar estados  
            if patient.state:
                states_count[patient.state] = states_count.get(patient.state, 0) + 1
        
        # Ordenar e pegar top 10
        cities_data = []
        for city, count in sorted(cities_count.items(), key=lambda x: x[1], reverse=True)[:10]:
            cities_data.append({
                'name': city,
                'count': count
            })
        
        states_data = []
        for state, count in sorted(states_count.items(), key=lambda x: x[1], reverse=True)[:10]:
            states_data.append({
                'name': state,
                'count': count
            })
        
        return jsonify({
            'success': True,
            'cities': cities_data,
            'states': states_data
        })
        
    except Exception as e:
        current_app.logger.error(f"Erro na API geográfica: {e}")
        return jsonify({
            'success': False,
            'message': str(e),
            'cities': [],
            'states': []
        }), 200

@main.route('/api/patients/stats')
@staff_required
def api_patients_stats():
    """✅ API para estatísticas pré-calculadas CORRIGIDA"""
    try:
        # ✅ BUSCAR TODOS OS PACIENTES ATIVOS DE UMA VEZ
        patients = Patient.query.filter_by(is_active=True).all()
        
        total_patients = len(patients)
        active_patients = total_patients
        
        if total_patients == 0:
            return jsonify({
                'success': True,
                'total_patients': 0,
                'active_patients': 0,
                'new_patients_30': 0,
                'patients_with_cns': 0,
                'completeness_rate': 0,
                'cns_rate': 0,
                'contact_rate': 0,
                'address_rate': 0
            })
        
        # ✅ CALCULAR ESTATÍSTICAS COM PYTHON
        thirty_days_ago = datetime.now() - timedelta(days=30)
        new_patients_30 = 0
        patients_with_cns = 0
        contact_count = 0
        address_count = 0
        completeness_score = 0
        
        for patient in patients:
            # Novos pacientes (30 dias)
            if patient.created_at >= thirty_days_ago:
                new_patients_30 += 1
            
            # Pacientes com CNS
            if patient.cns and patient.cns.strip():
                patients_with_cns += 1
            
            # Pacientes com dados de contato
            if (patient.cell_phone or patient.home_phone or patient.contact_phone or 
                patient.phone or patient.email):
                contact_count += 1
            
            # Pacientes com endereço
            if patient.address and patient.address.strip():
                address_count += 1
            
            # Completude dos dados (6 campos principais)
            score = 0
            if patient.full_name and patient.full_name.strip():
                score += 1
            if patient.cpf and patient.cpf.strip():
                score += 1
            if patient.birth_date:
                score += 1
            if patient.gender:
                score += 1
            if (patient.cell_phone or patient.home_phone or patient.contact_phone or patient.phone):
                score += 1
            if patient.address and patient.address.strip():
                score += 1
            
            completeness_score += score
        
        # ✅ CALCULAR PERCENTUAIS
        max_score = total_patients * 6
        completeness_rate = (completeness_score / max_score * 100) if max_score > 0 else 0
        cns_rate = (patients_with_cns / total_patients * 100) if total_patients > 0 else 0
        contact_rate = (contact_count / total_patients * 100) if total_patients > 0 else 0
        address_rate = (address_count / total_patients * 100) if total_patients > 0 else 0
        
        return jsonify({
            'success': True,
            'total_patients': total_patients,
            'active_patients': active_patients,
            'new_patients_30': new_patients_30,
            'patients_with_cns': patients_with_cns,
            'completeness_rate': round(completeness_rate, 1),
            'cns_rate': round(cns_rate, 1),
            'contact_rate': round(contact_rate, 1),
            'address_rate': round(address_rate, 1)
        })
        
    except Exception as e:
        current_app.logger.error(f"Erro na API de estatísticas: {e}")
        return jsonify({
            'success': False,
            'message': str(e),
            'total_patients': 0,
            'active_patients': 0,
            'new_patients_30': 0,
            'patients_with_cns': 0,
            'completeness_rate': 0,
            'cns_rate': 0,
            'contact_rate': 0,
            'address_rate': 0
        }), 200

def generate_patients_report(format_type, age_range=None, gender=None, registration_period=None):
    """✅ RELATÓRIO DE PACIENTES OTIMIZADO - CORRIGIDO"""
    
    if format_type == 'html':
        # ✅ PARA HTML, BUSCAR ESTATÍSTICAS DIRETAMENTE
        try:
            # Calcular estatísticas direto no Python
            patients = Patient.query.filter_by(is_active=True).all()
            
            total_patients = len(patients)
            
            if total_patients == 0:
                stats = {
                    'total_patients': 0,
                    'active_patients': 0,
                    'new_patients_30': 0,
                    'patients_with_cns': 0,
                    'completeness_rate': 0,
                    'cns_rate': 0,
                    'contact_rate': 0,
                    'address_rate': 0
                }
            else:
                # Calcular estatísticas
                thirty_days_ago = datetime.now() - timedelta(days=30)
                new_patients_30 = len([p for p in patients if p.created_at >= thirty_days_ago])
                patients_with_cns = len([p for p in patients if p.cns and p.cns.strip()])
                
                # Completude e outras taxas
                contact_count = len([p for p in patients if (p.cell_phone or p.home_phone or p.contact_phone or p.phone or p.email)])
                address_count = len([p for p in patients if p.address and p.address.strip()])
                
                # Completude dos dados
                completeness_score = 0
                for patient in patients:
                    score = sum([
                        1 if patient.full_name and patient.full_name.strip() else 0,
                        1 if patient.cpf and patient.cpf.strip() else 0,
                        1 if patient.birth_date else 0,
                        1 if patient.gender else 0,
                        1 if (patient.cell_phone or patient.home_phone or patient.contact_phone or patient.phone) else 0,
                        1 if patient.address and patient.address.strip() else 0
                    ])
                    completeness_score += score
                
                max_score = total_patients * 6
                completeness_rate = (completeness_score / max_score * 100) if max_score > 0 else 0
                
                stats = {
                    'total_patients': total_patients,
                    'active_patients': total_patients,
                    'new_patients_30': new_patients_30,
                    'patients_with_cns': patients_with_cns,
                    'completeness_rate': round(completeness_rate, 1),
                    'cns_rate': round((patients_with_cns / total_patients * 100), 1) if total_patients > 0 else 0,
                    'contact_rate': round((contact_count / total_patients * 100), 1) if total_patients > 0 else 0,
                    'address_rate': round((address_count / total_patients * 100), 1) if total_patients > 0 else 0
                }
            
            return render_template('reports/patients.html', 
                                 patients=None,  # Não carrega pacientes para HTML
                                 stats=stats)
        except Exception as e:
            current_app.logger.error(f"Erro no relatório HTML: {e}")
            # Retornar com stats vazias em caso de erro
            stats = {
                'total_patients': 0,
                'active_patients': 0,
                'new_patients_30': 0,
                'patients_with_cns': 0,
                'completeness_rate': 0,
                'cns_rate': 0,
                'contact_rate': 0,
                'address_rate': 0
            }
            return render_template('reports/patients.html', 
                                 patients=None,
                                 stats=stats)
    
    else:
        # ✅ PARA PDF/EXCEL, CARREGA OS DADOS COMPLETOS (COM LIMITE)
        query = Patient.query.filter_by(is_active=True)
        
        # Aplicar filtros se especificados
        if gender:
            query = query.filter(Patient.gender == gender)
        
        if age_range:
            current_year = datetime.now().year
            if age_range == '0-18':
                min_birth_year = current_year - 18
                query = query.filter(extract('year', Patient.birth_date) >= min_birth_year)
            elif age_range == '19-30':
                min_birth_year = current_year - 30
                max_birth_year = current_year - 19
                query = query.filter(
                    and_(
                        extract('year', Patient.birth_date) >= min_birth_year,
                        extract('year', Patient.birth_date) <= max_birth_year
                    )
                )
            elif age_range == '31-50':
                min_birth_year = current_year - 50
                max_birth_year = current_year - 31
                query = query.filter(
                    and_(
                        extract('year', Patient.birth_date) >= min_birth_year,
                        extract('year', Patient.birth_date) <= max_birth_year
                    )
                )
            elif age_range == '51-65':
                min_birth_year = current_year - 65
                max_birth_year = current_year - 51
                query = query.filter(
                    and_(
                        extract('year', Patient.birth_date) >= min_birth_year,
                        extract('year', Patient.birth_date) <= max_birth_year
                    )
                )
            elif age_range == '65+':
                max_birth_year = current_year - 65
                query = query.filter(extract('year', Patient.birth_date) <= max_birth_year)
        
        if registration_period:
            try:
                days = int(registration_period)
                cutoff_date = datetime.now() - timedelta(days=days)
                query = query.filter(Patient.created_at >= cutoff_date)
            except ValueError:
                pass
        
        # ✅ LIMITAR A 5000 REGISTROS PARA PDF/EXCEL
        patients = query.order_by(Patient.full_name).limit(5000).all()
        
        # Calcular estatísticas simples dos dados carregados
        age_groups = {'0-18': 0, '19-30': 0, '31-50': 0, '51-65': 0, '65+': 0}
        for patient in patients:
            age = patient.age
            if age <= 18:
                age_groups['0-18'] += 1
            elif age <= 30:
                age_groups['19-30'] += 1
            elif age <= 50:
                age_groups['31-50'] += 1
            elif age <= 65:
                age_groups['51-65'] += 1
            else:
                age_groups['65+'] += 1
        
        # Estatísticas e-SUS simples
        esus_stats = {
            'local_source': len([p for p in patients if getattr(p, 'source', 'local') == 'local']),
            'imported_source': len([p for p in patients if getattr(p, 'source', 'local') == 'imported']),
            'esus_source': len([p for p in patients if getattr(p, 'source', 'local') == 'esus']),
            'with_cns': len([p for p in patients if p.cns]),
            'with_mother_name': len([p for p in patients if getattr(p, 'mother_name', None)]),
            'with_multiple_phones': len([p for p in patients if sum(1 for phone in [getattr(p, 'home_phone', None), getattr(p, 'cell_phone', None), getattr(p, 'contact_phone', None)] if phone) > 1])
        }
        
        if format_type == 'pdf':
            return generate_patients_pdf(patients, age_groups, esus_stats)
        elif format_type == 'excel':
            return generate_patients_excel(patients, age_groups, esus_stats)

def generate_financial_report(start_date, end_date, format_type):
    """Relatório financeiro - APENAS DADOS REAIS"""
    
    # Custos de dispensações básicas
    dispensation_query = db.session.query(
        func.sum(Dispensation.total_cost).label('total_basic_cost'),
        func.count(Dispensation.id).label('total_dispensations')
    ).filter(Dispensation.status == DispensationStatus.COMPLETED)
    
    if start_date:
        dispensation_query = dispensation_query.filter(Dispensation.dispensation_date >= start_date)
    if end_date:
        dispensation_query = dispensation_query.filter(Dispensation.dispensation_date <= end_date)
    
    dispensation_stats = dispensation_query.first()
    
    # Valor do estoque atual
    stock_value = db.session.query(
        func.sum(Medication.current_stock * Medication.unit_cost)
    ).filter(
        and_(Medication.is_active == True, Medication.unit_cost.isnot(None))
    ).scalar() or 0
    
    # Custos por medicamento
    medication_costs = db.session.query(
        Medication.commercial_name,
        func.sum(DispensationItem.total_cost).label('total_cost'),
        func.sum(DispensationItem.quantity_dispensed).label('total_quantity')
    ).join(DispensationItem).join(Dispensation)
    
    if start_date:
        medication_costs = medication_costs.filter(Dispensation.dispensation_date >= start_date)
    if end_date:
        medication_costs = medication_costs.filter(Dispensation.dispensation_date <= end_date)
    
    medication_costs = medication_costs.group_by(
        Medication.id, Medication.commercial_name
    ).order_by(desc('total_cost')).limit(20).all()
    
    if format_type == 'html':
        return render_template('reports/financial.html',
                             dispensation_stats=dispensation_stats,
                             stock_value=stock_value,
                             medication_costs=medication_costs,
                             start_date=start_date,
                             end_date=end_date)
    elif format_type == 'pdf':
        return generate_financial_pdf(dispensation_stats, stock_value, medication_costs, start_date, end_date)
    elif format_type == 'excel':
        return generate_financial_excel(dispensation_stats, stock_value, medication_costs, start_date, end_date)

@main.route('/reports/dispensations')
@staff_required  
def dispensations_report():
    """✅ RELATÓRIO DE DISPENSAÇÕES - VERSÃO FINAL CORRIGIDA"""
    
    # ✅ CAPTURAR FILTROS
    start_date_str = request.args.get('start_date')
    end_date_str = request.args.get('end_date')
    pharmacist_id = request.args.get('pharmacist_id')
    
    # ✅ CONVERTER DATAS
    start_date = None
    end_date = None
    
    if start_date_str:
        try:
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
        except ValueError:
            pass
    
    if end_date_str:
        try:
            end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()
        except ValueError:
            pass
    
    # ✅ QUERY BASE COM JOINS OTIMIZADOS
    query = Dispensation.query.options(
        joinedload(Dispensation.patient),
        joinedload(Dispensation.dispenser),
        joinedload(Dispensation.items).joinedload(DispensationItem.medication)
    )
    
    # ✅ APLICAR FILTROS
    if start_date:
        query = query.filter(Dispensation.dispensation_date >= start_date)
    if end_date:
        query = query.filter(Dispensation.dispensation_date <= end_date)
    if pharmacist_id:
        query = query.filter(Dispensation.dispenser_id == pharmacist_id)
    
    # ✅ BUSCAR DISPENSAÇÕES
    dispensations = query.order_by(desc(Dispensation.dispensation_date)).all()
    
    # ✅ INICIALIZAR VARIÁVEIS
    total_dispensations = len(dispensations)
    unique_patients = len(set(d.patient_id for d in dispensations)) if dispensations else 0
    total_quantity = 0
    unique_medications = set()
    total_cost = 0
    dispensation_items = []
    
    # ✅ DADOS PARA GRÁFICOS - SEMPRE INICIALIZAR
    chart_dates = []
    chart_dispensations = []
    top_medications_labels = []
    top_medications_data = []
    
    # ✅ PROCESSAR DISPENSAÇÕES SE EXISTIREM
    if dispensations:
        from collections import defaultdict
        
        # ✅ PROCESSAR CADA DISPENSAÇÃO
        for dispensation in dispensations:
            # Processar itens da dispensação
            for item in dispensation.items:
                total_quantity += item.quantity_dispensed
                unique_medications.add(item.medication_id)
                total_cost += item.total_cost or 0
                
                # ✅ CRIAR OBJETO PARA TEMPLATE
                dispensation_obj = type('obj', (object,), {
                    'dispensation_date': dispensation.dispensation_date,
                    'patient': dispensation.patient,
                    'medication': item.medication,
                    'quantity': item.quantity_dispensed,
                    'dispensed_by': dispensation.dispenser,
                    'observations': item.observations or dispensation.observations
                })()
                
                dispensation_items.append(dispensation_obj)
        
        # ✅ CALCULAR DADOS DO GRÁFICO POR DIA
        daily_counts = defaultdict(int)
        for dispensation in dispensations:
            # Usar apenas DD/MM para o gráfico
            date_key = dispensation.dispensation_date.strftime('%d/%m')
            daily_counts[date_key] += 1
        
        # ✅ ORDENAR DATAS CORRETAMENTE
        if daily_counts:
            # Tentar ordenar cronologicamente
            try:
                current_year = datetime.now().year
                sorted_dates = sorted(daily_counts.keys(), 
                    key=lambda x: datetime.strptime(f"{x}/{current_year}", '%d/%m/%Y'))
            except:
                # Fallback: ordenar alfabeticamente
                sorted_dates = sorted(daily_counts.keys())
            
            chart_dates = sorted_dates
            chart_dispensations = [daily_counts[date] for date in sorted_dates]
        
        # ✅ CALCULAR TOP MEDICAMENTOS
        medication_counts = defaultdict(int)
        for dispensation in dispensations:
            for item in dispensation.items:
                if item.medication and item.medication.commercial_name:
                    # Limitar o nome do medicamento para evitar labels muito grandes
                    med_name = item.medication.commercial_name[:25]
                    medication_counts[med_name] += item.quantity_dispensed
        
        # ✅ PEGAR TOP 10 MEDICAMENTOS
        if medication_counts:
            top_medications = sorted(medication_counts.items(), 
                                   key=lambda x: x[1], reverse=True)[:10]
            top_medications_labels = [med[0] for med in top_medications]
            top_medications_data = [med[1] for med in top_medications]
    
    unique_medications_count = len(unique_medications)
    
    # ✅ BUSCAR FARMACÊUTICOS PARA FILTRO
    pharmacists = User.query.filter(
        and_(
            User.is_active == True,
            User.role.in_([UserRole.PHARMACIST, UserRole.ADMIN])
        )
    ).order_by(User.full_name).all()
    
       
    return render_template('reports/dispensations.html',
                         # ✅ DADOS PRINCIPAIS
                         dispensations=dispensation_items,
                         pharmacists=pharmacists,
                         
                         # ✅ ESTATÍSTICAS
                         total_dispensations=total_dispensations,
                         unique_patients=unique_patients,
                         unique_medications=unique_medications_count,
                         total_quantity=total_quantity,
                         total_cost=total_cost,
                         
                         # ✅ GRÁFICOS - SEMPRE PASSADOS (mesmo que vazios)
                         chart_dates=chart_dates,
                         chart_dispensations=chart_dispensations,
                         top_medications_labels=top_medications_labels,
                         top_medications_data=top_medications_data,
                         
                         # ✅ FILTROS
                         start_date=start_date,
                         end_date=end_date,
                         pharmacist_id=int(pharmacist_id) if pharmacist_id else None)

@main.route('/reports/dispensations/export')
@staff_required
def dispensations_export():
    """✅ EXPORTAÇÃO DO RELATÓRIO DE DISPENSAÇÕES"""
    format_type = request.args.get('format', 'pdf')
    start_date_str = request.args.get('start_date')
    end_date_str = request.args.get('end_date')
    
    # Converter datas
    start_date = None
    end_date = None
    
    if start_date_str:
        try:
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
        except ValueError:
            pass
    
    if end_date_str:
        try:
            end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()
        except ValueError:
            pass
    
    # Redirecionar para função de geração existente
    return generate_dispensations_report(start_date, end_date, format_type)

# =================== ADMINISTRAÇÃO ===================

@main.route('/admin')
@admin_required
def admin_index():
    """Página principal de administração"""
    return render_template('admin/index.html')

@main.route('/admin/users')
@admin_required
def admin_users_list():
    """Lista de usuários"""
    page = request.args.get('page', 1, type=int)
    users = User.query.order_by(User.full_name).paginate(
        page=page, per_page=20, error_out=False
    )
    return render_template('admin/users/list.html', users=users)

@main.route('/admin/users/create', methods=['GET', 'POST'])
@admin_required
def admin_user_create():
    """Criar novo usuário"""
    form = UserForm()
    
    if form.validate_on_submit():
        user = User(
            username=form.username.data,
            email=form.email.data,
            full_name=form.full_name.data,
            role=UserRole(form.role.data),
            crf=form.crf.data if form.crf.data else None,
            is_active=form.is_active.data
        )
        
        if form.password.data:
            # Validar senha
            password_errors = validate_password(form.password.data)
            if password_errors:
                for error in password_errors:
                    flash(error, 'error')
                return render_template('admin/users/form.html', form=form, title='Novo Usuário')
            
            user.set_password(form.password.data)
        else:
            # Senha padrão
            user.set_password('123456')
        
        db.session.add(user)
        db.session.commit()
        
        log_action('CREATE', 'users', user.id, new_values={
            'username': user.username,
            'role': user.role.value
        })
        
        flash(f'Usuário {user.full_name} criado com sucesso.', 'success')
        if not form.password.data:
            flash('Senha padrão definida: 123456. Peça para o usuário alterar.', 'warning')
        
        return redirect(url_for('main.admin_users_list'))
    
    return render_template('admin/users/form.html', form=form, title='Novo Usuário')

@main.route('/admin/users/<int:id>/edit', methods=['GET', 'POST'])
@admin_required
def admin_user_edit(id):
    """Editar usuário"""
    user = User.query.get_or_404(id)
    form = UserForm(obj=user)
    form.user_id = id  # Para validação única
    form.role.data = user.role.value
    
    if form.validate_on_submit():
        old_values = {
            'username': user.username,
            'role': user.role.value,
            'is_active': user.is_active
        }
        
        user.username = form.username.data
        user.email = form.email.data
        user.full_name = form.full_name.data
        user.role = UserRole(form.role.data)
        user.crf = form.crf.data if form.crf.data else None
        user.is_active = form.is_active.data
        
        if form.password.data:
            password_errors = validate_password(form.password.data)
            if password_errors:
                for error in password_errors:
                    flash(error, 'error')
                return render_template('admin/users/form.html', form=form, title='Editar Usuário', user=user)
            
            user.set_password(form.password.data)
        
        db.session.commit()
        
        log_action('UPDATE', 'users', user.id, 
                  old_values=old_values,
                  new_values={
                      'username': user.username,
                      'role': user.role.value,
                      'is_active': user.is_active
                  })
        
        flash(f'Usuário {user.full_name} atualizado com sucesso.', 'success')
        return redirect(url_for('main.admin_users_list'))
    
    return render_template('admin/users/form.html', form=form, title='Editar Usuário', user=user)

@main.route('/admin/users/<int:id>/toggle-status', methods=['POST'])
@admin_required
def admin_user_toggle_status(id):
    """Ativar/Desativar usuário"""
    user = User.query.get_or_404(id)
    
    if user.id == current_user.id:
        flash('Você não pode desativar sua própria conta.', 'error')
        return redirect(url_for('main.admin_users_list'))
    
    old_status = user.is_active
    user.is_active = not user.is_active
    db.session.commit()
    
    log_action('UPDATE', 'users', user.id, 
              old_values={'is_active': old_status},
              new_values={'is_active': user.is_active})
    
    status_text = 'ativado' if user.is_active else 'desativado'
    flash(f'Usuário {user.full_name} {status_text} com sucesso.', 'success')
    
    return redirect(url_for('main.admin_users_list'))

@main.route('/admin/audit-logs')
@admin_required
def admin_audit_logs():
    """Logs de auditoria"""
    page = request.args.get('page', 1, type=int)
    action_filter = request.args.get('action', '')
    table_filter = request.args.get('table', '')
    user_filter = request.args.get('user', '', type=int)
    
    query = AuditLog.query.options(joinedload(AuditLog.user))
    
    if action_filter:
        query = query.filter(AuditLog.action.ilike(f'%{action_filter}%'))
    if table_filter:
        query = query.filter(AuditLog.table_name == table_filter)
    if user_filter:
        query = query.filter(AuditLog.user_id == user_filter)
    
    logs = query.order_by(desc(AuditLog.created_at)).paginate(
        page=page, per_page=50, error_out=False
    )
    
    # Usuários para filtro
    users = User.query.filter_by(is_active=True).order_by(User.full_name).all()
    
    return render_template('admin/audit_logs.html', 
                         logs=logs, 
                         users=users,
                         action_filter=action_filter,
                         table_filter=table_filter,
                         user_filter=user_filter)

@main.route('/admin/system-info')
@admin_required
def admin_system_info():
    """Informações do sistema"""
    import psutil
    import platform
    
    # Informações do sistema
    system_info = {
        'platform': platform.platform(),
        'python_version': platform.python_version(),
        'cpu_percent': psutil.cpu_percent(interval=1),
        'memory': psutil.virtual_memory(),
        'disk': psutil.disk_usage('/'),
        'uptime': datetime.now() - datetime.fromtimestamp(psutil.boot_time())
    }
    
    # Estatísticas do banco
    db_stats = {
        'total_patients': Patient.query.count(),
        'active_patients': Patient.query.filter_by(is_active=True).count(),
        'total_medications': Medication.query.count(),
        'active_medications': Medication.query.filter_by(is_active=True).count(),
        'total_dispensations': Dispensation.query.count(),
        'total_high_cost_processes': HighCostProcess.query.count(),
        'total_users': User.query.count(),
        'active_users': User.query.filter_by(is_active=True).count(),
        # ✅ ESTATÍSTICAS E-SUS
        'esus_imported_patients': Patient.query.filter_by(source='imported').count(),
        'esus_synced_patients': Patient.query.filter(Patient.esus_sync_date.isnot(None)).count()
    }
    
    # ✅ STATUS E-SUS
    esus_status = {
        'configured': get_esus_db_credentials() is not None,
        'connected': False,
        'last_sync': None
    }
    
    if esus_status['configured']:
        success, _ = test_esus_connection()
        esus_status['connected'] = success
        
        # Última sincronização
        last_sync_patient = Patient.query.filter(
            Patient.esus_sync_date.isnot(None)
        ).order_by(desc(Patient.esus_sync_date)).first()
        
        if last_sync_patient:
            esus_status['last_sync'] = last_sync_patient.esus_sync_date
    
    return render_template('admin/system_info.html', 
                         system_info=system_info,
                         db_stats=db_stats,
                         esus_status=esus_status,
                         current_time=datetime.now(),
                         current_date=date.today())

@main.route('/dispensation/search-patient-birthdate', methods=['POST'])
@login_required
def dispensation_search_patient_birthdate():
    """Buscar pacientes por data de nascimento"""
    try:
        birthdate_str = request.form.get('birthdate')
        period = request.form.get('period', 'exact')
        
        if not birthdate_str:
            return jsonify({'error': 'Data de nascimento é obrigatória'}), 400
        
        # ✅ Converter string para date sem timezone
        try:
            birthdate = datetime.strptime(birthdate_str, '%Y-%m-%d').date()
        except ValueError:
            return jsonify({'error': 'Formato de data inválido'}), 400
        
        # Construir query baseada no período
        query = Patient.query.filter(Patient.is_active == True)
        
        if period == 'exact':
            # ✅ Data exata - usar DATE() para extrair apenas a parte da data
            query = query.filter(func.date(Patient.birth_date) == birthdate)
            
        elif period == 'month':
            # ✅ Mês inteiro - usar YEAR e MONTH
            query = query.filter(
                and_(
                    func.year(Patient.birth_date) == birthdate.year,
                    func.month(Patient.birth_date) == birthdate.month
                )
            )
            
        elif period == 'week':
            # ✅ ±3 dias - usar DATE() para comparação precisa
            start_date = birthdate - timedelta(days=3)
            end_date = birthdate + timedelta(days=3)
            
            query = query.filter(
                and_(
                    func.date(Patient.birth_date) >= start_date,
                    func.date(Patient.birth_date) <= end_date
                )
            )
        
        # Executar query e ordenar por nome
        patients = query.order_by(Patient.full_name).all()
        
        # Formatar resultados
        patients_data = []
        for patient in patients:
            # ✅ Calcular idade usando apenas a parte da data
            today = date.today()
            if patient.birth_date:
                # Garantir que estamos comparando apenas dates, não datetimes
                if isinstance(patient.birth_date, datetime):
                    birth_date_only = patient.birth_date.date()
                else:
                    birth_date_only = patient.birth_date
                    
                age = today.year - birth_date_only.year - ((today.month, today.day) < (birth_date_only.month, birth_date_only.day))
            else:
                age = 0
                birth_date_only = None
            
            # Telefone principal
            phone = patient.cell_phone or patient.home_phone or patient.contact_phone
            
            patients_data.append({
                'id': patient.id,
                'name': patient.full_name,
                'cpf': format_cpf(patient.cpf) if patient.cpf else 'N/A',
                'cns': format_cns(patient.cns) if patient.cns else None,
                'birth_date': birth_date_only.isoformat() if birth_date_only else None,
                'age': age,
                'phone': format_phone(phone) if phone else None,
                'mother_name': patient.mother_name,
                'source': patient.source or 'local'
            })
        
        # Log da busca
        current_app.logger.info(f"Busca por aniversário - Data: {birthdate_str}, Período: {period}, Resultados: {len(patients_data)}")
        
        return jsonify({
            'success': True,
            'patients': patients_data,
            'search_type': 'birthdate',
            'search_date': birthdate_str,
            'search_period': period,
            'count': len(patients_data)
        })
        
    except Exception as e:
        current_app.logger.error(f"Erro na busca por aniversário: {e}")
        return jsonify({'error': 'Erro interno do servidor'}), 500

# Funções auxiliares (adicionar se não existirem)
def format_cpf(cpf):
    """Formatar CPF para exibição"""
    if not cpf or len(cpf) != 11:
        return cpf
    return f"{cpf[:3]}.{cpf[3:6]}.{cpf[6:9]}-{cpf[9:]}"

def format_phone(phone):
    """Formatar telefone para exibição"""
    if not phone:
        return None
    
    # Remover apenas dígitos
    clean_phone = ''.join(filter(str.isdigit, phone))
    
    if len(clean_phone) == 11:  # Celular com DDD
        return f"({clean_phone[:2]}) {clean_phone[2:7]}-{clean_phone[7:]}"
    elif len(clean_phone) == 10:  # Fixo com DDD
        return f"({clean_phone[:2]}) {clean_phone[2:6]}-{clean_phone[6:]}"
    else:
        return phone

# =================== APIs JSON ===================

@main.route('/api/medications/search')
@staff_required
def api_medications_search():
    """API para buscar medicamentos com informações de cálculo"""
    term = request.args.get('q', '').strip()
    
    if len(term) < 2:
        return jsonify([])
    
    medications = Medication.query.filter(
        and_(
            Medication.is_active == True,
            or_(
                Medication.commercial_name.ilike(f'%{term}%'),
                Medication.generic_name.ilike(f'%{term}%')
            )
        )
    ).limit(20).all()
    
    results = []
    for med in medications:
        results.append({
            'id': med.id,
            'commercial_name': med.commercial_name,
            'generic_name': med.generic_name,
            'dosage': med.dosage,
            'current_stock': med.current_stock,
            'unit_cost': float(med.unit_cost) if med.unit_cost else None,
            'requires_prescription': med.requires_prescription,
            'controlled_substance': med.controlled_substance,
            'has_calculation_config': med.has_dispensing_config,        # ✅ NOVO
            'calculation_config_display': med.dispensing_config_display  # ✅ NOVO
        })
    
    return jsonify(results)

@main.route('/api/patients/search')
@staff_required
def api_patients_search():
    """API para buscar pacientes com campos expandidos"""
    term = request.args.get('q', '').strip()
    include_esus = request.args.get('include_esus', 'false').lower() == 'true'
    
    if len(term) < 2:
        return jsonify([])
    
    results = []
    
    try:
        # ✅ BUSCA LOCAL EXPANDIDA
        query = Patient.query.filter(Patient.is_active == True)
        
        if term.isdigit():
            # Busca por números (CPF, CNS, telefones)
            query = query.filter(
                or_(
                    Patient.cpf.like(f'%{term}%'),
                    Patient.cns.like(f'%{term}%'),
                    Patient.cell_phone.like(f'%{term}%'),
                    Patient.home_phone.like(f'%{term}%'),
                    Patient.contact_phone.like(f'%{term}%')
                )
            )
        else:
            # ✅ BUSCA POR NOME, NOME DA MÃE OU EMAIL
            query = query.filter(
                or_(
                    Patient.full_name.ilike(f'%{term}%'),
                    Patient.mother_name.ilike(f'%{term}%'),
                    Patient.email.ilike(f'%{term}%')
                )
            )
        
        local_patients = query.order_by(Patient.full_name).limit(10).all()
        
        # ✅ FORMATAR RESULTADOS LOCAIS
        for patient in local_patients:
            primary_phone = patient.cell_phone or patient.home_phone or patient.contact_phone
            
            results.append({
                'id': patient.id,
                'name': patient.full_name,
                'cpf': format_cpf(patient.cpf),
                'cns': format_cns(patient.cns) if patient.cns else '',
                'age': patient.age,
                'phone': format_phone(primary_phone) if primary_phone else '',
                'mother_name': getattr(patient, 'mother_name', ''),
                'source': getattr(patient, 'source_display', 'Local'),
                'type': 'local'
            })
        
        # ✅ BUSCA E-SUS SE SOLICITADO E SEM RESULTADOS LOCAIS
        if include_esus and not local_patients:
            try:
                # Usar busca integrada se disponível
                if hasattr(Patient, 'search_integrated'):
                    _, esus_results = Patient.search_integrated(term)
                    
                    for esus_patient in esus_results[:5]:
                        results.append({
                            'name': esus_patient.get('full_name'),
                            'cpf': esus_patient.get('cpf'),
                            'cns': esus_patient.get('cns', ''),
                            'age': esus_patient.get('age'),
                            'phone': esus_patient.get('primary_phone', ''),
                            'mother_name': esus_patient.get('mother_name', ''),
                            'source': 'e-SUS',
                            'type': 'esus',
                            'raw_data': esus_patient.get('raw_data')
                        })
            except Exception as e:
                current_app.logger.warning(f"Erro na busca e-SUS: {e}")
        
    except Exception as e:
        current_app.logger.error(f"Erro na API de busca de pacientes: {e}")
    
    return jsonify(results)

@main.route('/api/stock/alerts')
@pharmacist_required
def api_stock_alerts():
    """API para alertas de estoque"""
    # Medicamentos com estoque baixo
    low_stock = Medication.query.filter(
        and_(
            Medication.is_active == True,
            Medication.current_stock <= Medication.minimum_stock
        )
    ).count()
    
    # Medicamentos próximos ao vencimento
    near_expiry = Medication.query.filter(
        and_(
            Medication.is_active == True,
            Medication.expiry_date.isnot(None),
            Medication.expiry_date <= date.today() + timedelta(days=30)
        )
    ).count()
    
    # Processos alto custo pendentes
    pending_high_cost = HighCostProcess.query.filter_by(
        status=ProcessStatus.PENDING
    ).count()
    
    return jsonify({
        'low_stock': low_stock,
        'near_expiry': near_expiry,
        'pending_high_cost': pending_high_cost,
        'total_alerts': low_stock + near_expiry + pending_high_cost
    })

@main.route('/api/dashboard/stats')
@staff_required
def api_dashboard_stats():
    """API para estatísticas do dashboard"""
    today = date.today()
    
    stats = {
        'today_dispensations': Dispensation.query.filter(
            Dispensation.dispensation_date >= today
        ).count(),
        'total_patients': Patient.query.filter_by(is_active=True).count(),
        'low_stock_medications': Medication.query.filter(
            Medication.current_stock <= Medication.minimum_stock
        ).count(),
        'pending_evaluations': HighCostProcess.query.filter_by(
            status=ProcessStatus.PENDING
        ).count() if has_permission('evaluate_high_cost') else 0,
        # ✅ ESTATÍSTICAS E-SUS
        'esus_imported_today': Patient.query.filter(
            and_(
                Patient.source == 'imported',
                Patient.created_at >= today
            )
        ).count(),
        'esus_connection_status': test_esus_connection()[0] if get_esus_db_credentials() else False
    }
    
    return jsonify(stats)

# ✅ NOVA API PARA DATATABLES DOS PACIENTES
@main.route('/api/patients/list', methods=['POST'])
@staff_required
def api_patients_list():
    """✅ API DataTables server-side para lista de pacientes"""
    try:
        # ✅ PARÂMETROS DO DATATABLES
        draw = request.form.get('draw', type=int)
        start = request.form.get('start', type=int)
        length = request.form.get('length', type=int)
        search_value = request.form.get('search[value]', '')
        
        # ✅ FILTROS CUSTOMIZADOS
        search_type = request.form.get('search_type', 'name')
        status_filter = request.form.get('status_filter', '')
        gender_filter = request.form.get('gender_filter', '')
        select_for = request.form.get('select_for', '')
        
        # ✅ QUERY BASE
        query = Patient.query
        
        # ✅ APLICAR FILTROS DE STATUS
        if status_filter == 'active':
            query = query.filter(Patient.is_active == True)
        elif status_filter == 'inactive':
            query = query.filter(Patient.is_active == False)
        else:
            # Padrão: apenas ativos
            query = query.filter(Patient.is_active == True)
        
        # ✅ APLICAR FILTRO DE GÊNERO
        if gender_filter:
            query = query.filter(Patient.gender == gender_filter)
        
        # ✅ APLICAR BUSCA GLOBAL
        if search_value:
            if search_type == 'name':
                query = query.filter(Patient.full_name.ilike(f'%{search_value}%'))
            elif search_type == 'cpf':
                clean_cpf = re.sub(r'[^0-9]', '', search_value)
                query = query.filter(Patient.cpf.like(f'%{clean_cpf}%'))
            elif search_type == 'cns':
                clean_cns = re.sub(r'[^0-9]', '', search_value)
                query = query.filter(Patient.cns.like(f'%{clean_cns}%'))
            elif search_type == 'phone':
                clean_phone = re.sub(r'[^0-9]', '', search_value)
                query = query.filter(
                    or_(
                        Patient.cell_phone.like(f'%{clean_phone}%'),
                        Patient.home_phone.like(f'%{clean_phone}%'),
                        Patient.contact_phone.like(f'%{clean_phone}%'),
                        Patient.phone.like(f'%{clean_phone}%')
                    )
                )
            elif search_type == 'email':
                query = query.filter(Patient.email.ilike(f'%{search_value}%'))
            elif search_type == 'mother':
                query = query.filter(Patient.mother_name.ilike(f'%{search_value}%'))
        
        # ✅ TOTAL DE REGISTROS (SEM FILTROS)
        total_records = Patient.query.filter_by(is_active=True).count()
        
        # ✅ TOTAL FILTRADO
        filtered_records = query.count()
        
        # ✅ ORDENAÇÃO
        order_column = request.form.get('order[0][column]', '0', type=int)
        order_dir = request.form.get('order[0][dir]', 'asc')
        
        columns = ['full_name', 'cpf', 'cell_phone', 'city', 'age', 'is_active', 'created_at']
        
        if 0 <= order_column < len(columns):
            column_name = columns[order_column]
            if hasattr(Patient, column_name):
                if order_dir == 'desc':
                    query = query.order_by(desc(getattr(Patient, column_name)))
                else:
                    query = query.order_by(getattr(Patient, column_name))
        else:
            query = query.order_by(Patient.full_name)
        
        # ✅ PAGINAÇÃO
        patients = query.offset(start).limit(length).all()
        
        # ✅ FORMATAR DADOS PARA DATATABLE
        data = []
        for patient in patients:
            # Telefone principal
            primary_phone = patient.cell_phone or patient.home_phone or patient.contact_phone or getattr(patient, 'phone', None)
            
            # Botões baseados no modo
            if select_for == 'high_cost':
                actions = f'''
                    <div class="btn-group btn-group-sm">
                        <a href="{url_for('main.patient_view', id=patient.id)}" 
                           class="btn btn-outline-info btn-sm" title="Visualizar">
                            <i class="fas fa-eye"></i>
                        </a>
                        {"" if not patient.is_active else f'''
                        <a href="{url_for('main.high_cost_request', patient_id=patient.id)}" 
                           class="btn btn-success btn-sm" title="Selecionar">
                            <i class="fas fa-check me-1"></i>Selecionar
                        </a>
                        '''}
                    </div>
                '''
            else:
                actions = f'''
                    <div class="btn-group btn-group-sm">
                        <a href="{url_for('main.patient_view', id=patient.id)}" 
                           class="btn btn-outline-primary btn-sm" title="Visualizar">
                            <i class="fas fa-eye"></i>
                        </a>
                        <a href="{url_for('main.patient_edit', id=patient.id)}" 
                           class="btn btn-outline-secondary btn-sm" title="Editar">
                            <i class="fas fa-edit"></i>
                        </a>
                        <div class="dropdown">
                            <button class="btn btn-outline-success btn-sm dropdown-toggle" data-bs-toggle="dropdown">
                                <i class="fas fa-star"></i>
                            </button>
                            <ul class="dropdown-menu">
                                <li><a class="dropdown-item" href="{url_for('main.high_cost_request', patient_id=patient.id)}">
                                    <i class="fas fa-star me-2"></i>Alto Custo
                                </a></li>
                            </ul>
                        </div>
                    </div>
                '''
            
            data.append({
                'avatar': patient.full_name[0].upper(),
                'full_name': patient.full_name,
                'gender': patient.gender_display,
                'cpf': format_cpf(patient.cpf),
                'cns': format_cns(patient.cns) if patient.cns else '--',
                'primary_phone': format_phone(primary_phone) if primary_phone else '--',
                'email': patient.email or '--',
                'city': patient.city or '--',
                'mother_name': getattr(patient, 'mother_name', '') or '--',
                'age': patient.age,
                'status': f'<span class="badge bg-success">Ativo</span>' if patient.is_active else f'<span class="badge bg-secondary">Inativo</span>',
                'created_at': format_date(patient.created_at),
                'source': f'<small class="badge bg-info">e-SUS</small>' if getattr(patient, 'source', 'local') == 'imported' else f'<small class="badge bg-success">Local</small>',
                'actions': actions
            })
        
        return jsonify({
            'draw': draw,
            'recordsTotal': total_records,
            'recordsFiltered': filtered_records,
            'data': data
        })
        
    except Exception as e:
        current_app.logger.error(f"Erro na API DataTables de pacientes: {e}")
        return jsonify({
            'draw': draw or 1,
            'recordsTotal': 0,
            'recordsFiltered': 0,
            'data': [],
            'error': str(e)
        }), 500

# =================== DOWNLOAD DE ARQUIVOS ===================

@main.route('/download/document/<int:doc_id>')
@pharmacist_required
def download_document(doc_id):
    """Download de documento de processo"""
    document = ProcessDocument.query.get_or_404(doc_id)
    
    # Verificar se arquivo existe
    if not os.path.exists(document.file_path):
        flash('Arquivo não encontrado.', 'error')
        return redirect(request.referrer or url_for('main.high_cost_index'))
    
    log_action('DOWNLOAD', 'process_documents', doc_id)
    
    return send_file(
        document.file_path,
        as_attachment=True,
        download_name=document.original_filename,
        mimetype=document.mime_type
    )

# =================== TRATAMENTO DE ERROS ===================

@main.errorhandler(404)
def not_found(error):
    return render_template('errors/404.html'), 404

@main.errorhandler(500)
def internal_error(error):
    db.session.rollback()
    return render_template('errors/500.html'), 500

@main.errorhandler(403)
def forbidden(error):
    return render_template('errors/403.html'), 403

# =================== BEFORE/AFTER REQUEST ===================

@main.before_request
def before_request():
    """Executado antes de cada request"""
    # Verificar validade da sessão
    if current_user.is_authenticated:
        session_check = check_user_session()
        if session_check:
            return session_check

@main.after_request
def after_request(response):
    """Executado após cada request"""
    # Adicionar headers de segurança
    return add_security_headers(response)

# =================== ROTAS ESPECIAIS ===================

@main.route('/health')
def health_check():
    """Health check para monitoramento"""
    # ✅ INCLUIR STATUS E-SUS
    esus_configured = get_esus_db_credentials() is not None
    esus_connected = False
    
    if esus_configured:
        esus_connected, _ = test_esus_connection()
    
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.utcnow().isoformat(),
        'version': '1.0.0',
        'esus': {
            'configured': esus_configured,
            'connected': esus_connected
        }
    })

@main.route('/robots.txt')
def robots_txt():
    """Arquivo robots.txt"""
    return "User-agent: *\nDisallow: /", 200, {'Content-Type': 'text/plain'}

# =================== FUNÇÕES AUXILIARES ===================

def calculate_days_until_expiry(expiry_date):
    """Calcula dias até o vencimento"""
    if not expiry_date:
        return None
    return (expiry_date - date.today()).days

def get_medication_stock_status(medication):
    """Retorna status do estoque do medicamento"""
    if medication.current_stock <= 0:
        return 'out_of_stock'
    elif medication.current_stock <= medication.minimum_stock:
        return 'low_stock'
    elif medication.current_stock <= medication.minimum_stock * 2:
        return 'medium_stock'
    else:
        return 'good_stock'

def format_process_status(status):
    """Formata status do processo para exibição"""
    status_map = {
        'pending': 'Pendente',
        'under_evaluation': 'Em Avaliação',
        'approved': 'Aprovado',
        'denied': 'Negado',
        'dispensed': 'Dispensado',
        'completed': 'Concluído',
        'cancelled': 'Cancelado'
    }
    return status_map.get(status.value if hasattr(status, 'value') else status, 'Desconhecido')

# ✅ NOVAS FUNÇÕES PARA E-SUS
def get_esus_integration_stats():
    """Obter estatísticas de integração e-SUS"""
    return {
        'total_patients': Patient.query.filter_by(is_active=True).count(),
        'local_patients': Patient.query.filter_by(source='local').count(),
        'imported_patients': Patient.query.filter_by(source='imported').count(),
        'synced_patients': Patient.query.filter(Patient.esus_sync_date.isnot(None)).count(),
        'connection_status': test_esus_connection()[0] if get_esus_db_credentials() else False
    }

# Registrar funções nos templates
@main.context_processor
def utility_processor():
    return {
        'calculate_days_until_expiry': calculate_days_until_expiry,
        'get_medication_stock_status': get_medication_stock_status,
        'format_process_status': format_process_status,
        'get_esus_integration_stats': get_esus_integration_stats,
        'enumerate': enumerate,
        'len': len,
        'str': str,
        'int': int,
        'float': float
    }

# =================== FUNÇÕES DE GERAÇÃO DE PDF ===================

def generate_high_cost_pdf(processes, stats, start_date, end_date):
    """Gerar PDF do relatório de alto custo"""
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4)
    styles = getSampleStyleSheet()
    story = []
    
    # Título
    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Heading1'],
        fontSize=18,
        spaceAfter=30,
        alignment=1  # Center
    )
    
    title = Paragraph("Relatório de Processos Alto Custo", title_style)
    story.append(title)
    
    # Período
    if start_date and end_date:
        period = Paragraph(f"Período: {format_date(start_date)} a {format_date(end_date)}", styles['Normal'])
        story.append(period)
        story.append(Spacer(1, 12))
    
    # Estatísticas
    stats_data = [
        ['Estatística', 'Valor'],
        ['Total de Processos', str(stats['total'])],
        ['Aprovados', str(stats['approved'])],
        ['Negados', str(stats['denied'])],
        ['Pendentes', str(stats['pending'])],
        ['Em Avaliação', str(stats['under_evaluation'])],
        ['Dispensados', str(stats['dispensed'])]
    ]
    
    stats_table = Table(stats_data)
    stats_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 14),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
        ('GRID', (0, 0), (-1, -1), 1, colors.black)
    ]))
    
    story.append(stats_table)
    story.append(Spacer(1, 12))
    
    # Tabela de processos
    if processes:
        data = [['Protocolo', 'Paciente', 'Medicamento', 'Status', 'Data']]
        
        for process in processes[:50]:  # Limitar a 50 registros
            data.append([
                process.protocol_number,
                process.patient.full_name[:30],
                process.medication.commercial_name[:30],
                format_process_status(process.status),
                format_date(process.request_date)
            ])
        
        table = Table(data)
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 10),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
            ('GRID', (0, 0), (-1, -1), 1, colors.black),
            ('FONTSIZE', (0, 1), (-1, -1), 8)
        ]))
        
        story.append(table)
    
    doc.build(story)
    buffer.seek(0)
    
    return send_file(
        buffer,
        as_attachment=True,
        download_name=f'relatorio_alto_custo_{datetime.now().strftime("%Y%m%d")}.pdf',
        mimetype='application/pdf'
    )

def generate_financial_pdf(dispensation_stats, stock_value, medication_costs, start_date, end_date):
    """Gerar PDF do relatório financeiro"""
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4)
    styles = getSampleStyleSheet()
    story = []
    
    # Título
    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Heading1'],
        fontSize=18,
        spaceAfter=30,
        alignment=1
    )
    
    title = Paragraph("Relatório Financeiro", title_style)
    story.append(title)
    
    # Período
    if start_date and end_date:
        period = Paragraph(f"Período: {format_date(start_date)} a {format_date(end_date)}", styles['Normal'])
        story.append(period)
        story.append(Spacer(1, 12))
    
    # Resumo financeiro
    financial_data = [
        ['Indicador', 'Valor'],
        ['Total Dispensado', f"R$ {dispensation_stats.total_basic_cost or 0:,.2f}"],
        ['Número de Dispensações', str(dispensation_stats.total_dispensations or 0)],
        ['Valor do Estoque', f"R$ {stock_value:,.2f}"]
    ]
    
    financial_table = Table(financial_data)
    financial_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 14),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
        ('GRID', (0, 0), (-1, -1), 1, colors.black)
    ]))
    
    story.append(financial_table)
    story.append(Spacer(1, 12))
    
    # Top medicamentos por custo
    if medication_costs:
        subtitle = Paragraph("Top Medicamentos por Custo", styles['Heading2'])
        story.append(subtitle)
        story.append(Spacer(1, 12))
        
        med_data = [['Medicamento', 'Custo Total', 'Quantidade']]
        
        for med in medication_costs[:20]:
            med_data.append([
                med.commercial_name[:40],
                f"R$ {med.total_cost:,.2f}",
                str(med.total_quantity)
            ])
        
        med_table = Table(med_data)
        med_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 10),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
            ('GRID', (0, 0), (-1, -1), 1, colors.black),
            ('FONTSIZE', (0, 1), (-1, -1), 8)
        ]))
        
        story.append(med_table)
    
    doc.build(story)
    buffer.seek(0)
    
    return send_file(
        buffer,
        as_attachment=True,
        download_name=f'relatorio_financeiro_{datetime.now().strftime("%Y%m%d")}.pdf',
        mimetype='application/pdf'
    )

def generate_stock_pdf(medications, total_value):
    """Gerar PDF do relatório de estoque"""
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4)
    styles = getSampleStyleSheet()
    story = []
    
    title = Paragraph("Relatório de Estoque", styles['Title'])
    story.append(title)
    story.append(Spacer(1, 12))
    
    # Resumo
    summary = Paragraph(f"Valor Total do Estoque: R$ {total_value:,.2f}", styles['Heading2'])
    story.append(summary)
    story.append(Spacer(1, 12))
    
    # Tabela de medicamentos
    data = [['Medicamento', 'Estoque Atual', 'Estoque Mínimo', 'Valor Unitário', 'Valor Total']]
    
    for med in medications:
        valor_total = (med.current_stock * (med.unit_cost or 0))
        data.append([
            med.commercial_name[:30],
            str(med.current_stock),
            str(med.minimum_stock),
            f"R$ {med.unit_cost or 0:.2f}",
            f"R$ {valor_total:.2f}"
        ])
    
    table = Table(data)
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 10),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
        ('GRID', (0, 0), (-1, -1), 1, colors.black),
        ('FONTSIZE', (0, 1), (-1, -1), 8)
    ]))
    
    story.append(table)
    doc.build(story)
    buffer.seek(0)
    
    return send_file(
        buffer,
        as_attachment=True,
        download_name=f'relatorio_estoque_{datetime.now().strftime("%Y%m%d")}.pdf',
        mimetype='application/pdf'
    )

def generate_expiry_pdf(near_expiry, expired):
    """Gerar PDF do relatório de vencimentos"""
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4)
    styles = getSampleStyleSheet()
    story = []
    
    title = Paragraph("Relatório de Vencimentos", styles['Title'])
    story.append(title)
    story.append(Spacer(1, 12))
    
    # Medicamentos vencidos
    if expired:
        subtitle = Paragraph("Medicamentos Vencidos", styles['Heading2'])
        story.append(subtitle)
        story.append(Spacer(1, 12))
        
        data = [['Medicamento', 'Lote', 'Data de Vencimento', 'Estoque']]
        for med in expired:
            data.append([
                med.commercial_name[:30],
                med.batch_number or 'N/A',
                format_date(med.expiry_date) if med.expiry_date else 'N/A',
                str(med.current_stock)
            ])
        
        table = Table(data)
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.red),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 10),
            ('GRID', (0, 0), (-1, -1), 1, colors.black),
            ('FONTSIZE', (0, 1), (-1, -1), 8)
        ]))
        
        story.append(table)
        story.append(Spacer(1, 12))
    
    # Medicamentos próximos ao vencimento
    if near_expiry:
        subtitle = Paragraph("Próximos ao Vencimento (30 dias)", styles['Heading2'])
        story.append(subtitle)
        story.append(Spacer(1, 12))
        
        data = [['Medicamento', 'Lote', 'Data de Vencimento', 'Dias Restantes', 'Estoque']]
        for med in near_expiry:
            days_left = (med.expiry_date - date.today()).days if med.expiry_date else 0
            data.append([
                med.commercial_name[:30],
                med.batch_number or 'N/A',
                format_date(med.expiry_date) if med.expiry_date else 'N/A',
                str(days_left),
                str(med.current_stock)
            ])
        
        table = Table(data)
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.orange),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 10),
            ('GRID', (0, 0), (-1, -1), 1, colors.black),
            ('FONTSIZE', (0, 1), (-1, -1), 8)
        ]))
        
        story.append(table)
    
    doc.build(story)
    buffer.seek(0)
    
    return send_file(
        buffer,
        as_attachment=True,
        download_name=f'relatorio_vencimentos_{datetime.now().strftime("%Y%m%d")}.pdf',
        mimetype='application/pdf'
    )

def generate_consumption_pdf(data, start_date, end_date):
    """Gerar PDF do relatório de consumo"""
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4)
    styles = getSampleStyleSheet()
    story = []
    
    title = Paragraph("Relatório de Consumo de Medicamentos", styles['Title'])
    story.append(title)
    
    if start_date and end_date:
        period = Paragraph(f"Período: {format_date(start_date)} a {format_date(end_date)}", styles['Normal'])
        story.append(period)
    
    story.append(Spacer(1, 12))
    
    # Tabela de consumo
    table_data = [['Medicamento', 'Genérico', 'Dosagem', 'Quantidade', 'Dispensações', 'Custo Total']]
    
    for item in data:
        table_data.append([
            item.commercial_name[:25],
            item.generic_name[:25],
            item.dosage,
            str(item.total_dispensed),
            str(item.dispensations_count),
            f"R$ {item.total_cost:.2f}" if item.total_cost else "R$ 0,00"
        ])
    
    table = Table(table_data)
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 9),
        ('GRID', (0, 0), (-1, -1), 1, colors.black),
        ('FONTSIZE', (0, 1), (-1, -1), 8)
    ]))
    
    story.append(table)
    doc.build(story)
    buffer.seek(0)
    
    return send_file(
        buffer,
        as_attachment=True,
        download_name=f'relatorio_consumo_{datetime.now().strftime("%Y%m%d")}.pdf',
        mimetype='application/pdf'
    )

def generate_dispensations_pdf(dispensations, total_cost, total_items, start_date, end_date):
    """Gerar PDF do relatório de dispensações"""
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4)
    styles = getSampleStyleSheet()
    story = []
    
    title = Paragraph("Relatório de Dispensações", styles['Title'])
    story.append(title)
    
    if start_date and end_date:
        period = Paragraph(f"Período: {format_date(start_date)} a {format_date(end_date)}", styles['Normal'])
        story.append(period)
    
    # Resumo
    summary = Paragraph(f"Total de Dispensações: {len(dispensations)} | Itens Dispensados: {total_items} | Custo Total: R$ {total_cost:.2f}", styles['Heading3'])
    story.append(summary)
    story.append(Spacer(1, 12))
    
    # Tabela de dispensações
    table_data = [['Data', 'Paciente', 'Atendente', 'Itens', 'Custo']]
    
    for disp in dispensations[:100]:  # Limitar a 100
        table_data.append([
            format_date(disp.dispensation_date),
            disp.patient.full_name[:20],
            disp.dispenser.full_name[:20],
            str(len(disp.items)),
            f"R$ {disp.total_cost:.2f}" if disp.total_cost else "R$ 0,00"
        ])
    
    table = Table(table_data)
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 10),
        ('GRID', (0, 0), (-1, -1), 1, colors.black),
        ('FONTSIZE', (0, 1), (-1, -1), 8)
    ]))
    
    story.append(table)
    doc.build(story)
    buffer.seek(0)
    
    return send_file(
        buffer,
        as_attachment=True,
        download_name=f'relatorio_dispensacoes_{datetime.now().strftime("%Y%m%d")}.pdf',
        mimetype='application/pdf'
    )

def generate_patients_pdf(patients, age_groups, esus_stats=None):
    """Gerar PDF do relatório de pacientes"""
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4)
    styles = getSampleStyleSheet()
    story = []
    
    title = Paragraph("Relatório de Pacientes", styles['Title'])
    story.append(title)
    story.append(Spacer(1, 12))
    
    # Estatísticas por idade
    age_data = [['Faixa Etária', 'Quantidade']]
    for age_range, count in age_groups.items():
        age_data.append([age_range, str(count)])
    
    age_table = Table(age_data)
    age_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('GRID', (0, 0), (-1, -1), 1, colors.black)
    ]))
    
    story.append(age_table)
    story.append(Spacer(1, 12))
    
    # ✅ ESTATÍSTICAS E-SUS SE DISPONÍVEIS
    if esus_stats:
        esus_title = Paragraph("Estatísticas e-SUS", styles['Heading2'])
        story.append(esus_title)
        story.append(Spacer(1, 6))
        
        esus_data = [
            ['Origem', 'Quantidade'],
            ['Cadastro Local', str(esus_stats['local_source'])],
            ['Importado do e-SUS', str(esus_stats['imported_source'])],
            ['e-SUS Direto', str(esus_stats['esus_source'])],
            ['Com CNS', str(esus_stats['with_cns'])],
            ['Com Nome da Mãe', str(esus_stats['with_mother_name'])],
            ['Múltiplos Telefones', str(esus_stats['with_multiple_phones'])]
        ]
        
        esus_table = Table(esus_data)
        esus_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.lightblue),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.black),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('GRID', (0, 0), (-1, -1), 1, colors.black)
        ]))
        
        story.append(esus_table)
        story.append(Spacer(1, 12))
    
    # Lista de pacientes
    patient_data = [['Nome', 'CPF', 'Idade', 'Telefone', 'Origem']]
    
    for patient in patients[:200]:  # Limitar a 200
        patient_data.append([
            patient.full_name[:25],
            format_cpf(patient.cpf),
            str(patient.age),
            patient.primary_phone[:15] if patient.primary_phone else 'N/A',
            patient.source_display[:10]
        ])
    
    patient_table = Table(patient_data)
    patient_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 9),
        ('GRID', (0, 0), (-1, -1), 1, colors.black),
        ('FONTSIZE', (0, 1), (-1, -1), 7)
    ]))
    
    story.append(patient_table)
    doc.build(story)
    buffer.seek(0)
    
    return send_file(
        buffer,
        as_attachment=True,
        download_name=f'relatorio_pacientes_{datetime.now().strftime("%Y%m%d")}.pdf',
        mimetype='application/pdf'
    )

def generate_esus_integration_pdf(stats, quality_stats, recent_imports):
    """✅ GERAR PDF DO RELATÓRIO DE INTEGRAÇÃO E-SUS"""
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4)
    styles = getSampleStyleSheet()
    story = []
    
    # Título
    title = Paragraph("Relatório de Integração e-SUS", styles['Title'])
    story.append(title)
    story.append(Spacer(1, 12))
    
    # Estatísticas gerais
    general_data = [
        ['Indicador', 'Valor'],
        ['Total de Pacientes', str(stats['total_patients'])],
        ['Pacientes Locais', str(stats['local_patients'])],
        ['Importados do e-SUS', str(stats['imported_patients'])],
        ['Pacientes e-SUS Direto', str(stats['esus_patients'])],
        ['Sincronizados', str(stats['synced_patients'])],
        ['Com CNS', str(stats['patients_with_cns'])]
    ]
    
    general_table = Table(general_data)
    general_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.lightblue),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.black),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 12),
        ('GRID', (0, 0), (-1, -1), 1, colors.black)
    ]))
    
    story.append(general_table)
    story.append(Spacer(1, 12))
    
    # Qualidade dos dados
    quality_title = Paragraph("Qualidade dos Dados", styles['Heading2'])
    story.append(quality_title)
    story.append(Spacer(1, 6))
    
    quality_data = [
        ['Indicador', 'Quantidade', 'Percentual'],
        ['Endereço Completo', str(quality_stats['complete_address']), 
         f"{(quality_stats['complete_address']/stats['total_patients']*100):.1f}%" if stats['total_patients'] > 0 else "0%"],
        ['Com Telefone', str(quality_stats['with_phone']), 
         f"{(quality_stats['with_phone']/stats['total_patients']*100):.1f}%" if stats['total_patients'] > 0 else "0%"],
        ['Info dos Pais', str(quality_stats['with_parents_info']), 
         f"{(quality_stats['with_parents_info']/stats['total_patients']*100):.1f}%" if stats['total_patients'] > 0 else "0%"]
    ]
    
    quality_table = Table(quality_data)
    quality_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.lightgreen),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.black),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('GRID', (0, 0), (-1, -1), 1, colors.black)
    ]))
    
    story.append(quality_table)
    story.append(Spacer(1, 12))
    
    # Importações recentes
    if recent_imports:
        imports_title = Paragraph("Importações Recentes (30 dias)", styles['Heading2'])
        story.append(imports_title)
        story.append(Spacer(1, 6))
        
        imports_data = [['Nome', 'CPF', 'Data Importação']]
        
        for patient in recent_imports[:20]:  # Limitar a 20
            imports_data.append([
                patient.full_name[:35],
                format_cpf(patient.cpf),
                format_date(patient.created_at)
            ])
        
        imports_table = Table(imports_data)
        imports_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 10),
            ('GRID', (0, 0), (-1, -1), 1, colors.black),
            ('FONTSIZE', (0, 1), (-1, -1), 8)
        ]))
        
        story.append(imports_table)
    
    doc.build(story)
    buffer.seek(0)
    
    return send_file(
        buffer,
        as_attachment=True,
        download_name=f'relatorio_integracao_esus_{datetime.now().strftime("%Y%m%d")}.pdf',
        mimetype='application/pdf'
    )

# =================== FUNÇÕES DE GERAÇÃO DE EXCEL ===================

def generate_high_cost_excel(processes, stats, start_date, end_date):
    """Gerar Excel do relatório de alto custo"""
    output = BytesIO()
    
    # Criar dados para o DataFrame
    data = []
    for process in processes:
        data.append({
            'Protocolo': process.protocol_number,
            'Paciente': process.patient.full_name,
            'CPF': format_cpf(process.patient.cpf),
            'Medicamento': process.medication.commercial_name,
            'Dosagem': process.medication.dosage,
            'Status': format_process_status(process.status),
            'Data Solicitação': process.request_date.strftime('%d/%m/%Y'),
            'CID-10': process.cid10,
            'Médico': process.doctor_name,
            'CRM': process.doctor_crm,
            'Quantidade Solicitada': process.requested_quantity,
            'Justificativa': process.justification[:100] + '...' if len(process.justification) > 100 else process.justification
        })
    
    df = pd.DataFrame(data)
    
    # Criar arquivo Excel com múltiplas abas
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        # Aba principal com dados
        df.to_excel(writer, sheet_name='Processos', index=False)
        
        # Aba com estatísticas
        stats_df = pd.DataFrame([
            {'Estatística': 'Total de Processos', 'Valor': stats['total']},
            {'Estatística': 'Aprovados', 'Valor': stats['approved']},
            {'Estatística': 'Negados', 'Valor': stats['denied']},
            {'Estatística': 'Pendentes', 'Valor': stats['pending']},
            {'Estatística': 'Em Avaliação', 'Valor': stats['under_evaluation']},
            {'Estatística': 'Dispensados', 'Valor': stats['dispensed']}
        ])
        stats_df.to_excel(writer, sheet_name='Estatísticas', index=False)
        
        # Ajustar largura das colunas
        for sheet_name in writer.sheets:
            worksheet = writer.sheets[sheet_name]
            for column in worksheet.columns:
                max_length = 0
                column_letter = column[0].column_letter
                for cell in column:
                    try:
                        if len(str(cell.value)) > max_length:
                            max_length = len(str(cell.value))
                    except:
                        pass
                adjusted_width = min(max_length + 2, 50)
                worksheet.column_dimensions[column_letter].width = adjusted_width
    
    output.seek(0)
    
    return send_file(
        output,
        as_attachment=True,
        download_name=f'relatorio_alto_custo_{datetime.now().strftime("%Y%m%d")}.xlsx',
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )

def generate_financial_excel(dispensation_stats, stock_value, medication_costs, start_date, end_date):
    """Gerar Excel do relatório financeiro"""
    output = BytesIO()
    
    # Dados dos medicamentos
    med_data = []
    for med in medication_costs:
        med_data.append({
            'Medicamento': med.commercial_name,
            'Custo Total': float(med.total_cost),
            'Quantidade Dispensada': med.total_quantity,
            'Custo Médio por Unidade': float(med.total_cost) / med.total_quantity if med.total_quantity > 0 else 0
        })
    
    med_df = pd.DataFrame(med_data)
    
    # Resumo financeiro
    summary_data = [
        {'Indicador': 'Total Dispensado', 'Valor': float(dispensation_stats.total_basic_cost or 0)},
        {'Indicador': 'Número de Dispensações', 'Valor': dispensation_stats.total_dispensations or 0},
        {'Indicador': 'Valor do Estoque Atual', 'Valor': float(stock_value)},
        {'Indicador': 'Período Início', 'Valor': start_date.strftime('%d/%m/%Y') if start_date else 'N/A'},
        {'Indicador': 'Período Fim', 'Valor': end_date.strftime('%d/%m/%Y') if end_date else 'N/A'}
    ]
    
    summary_df = pd.DataFrame(summary_data)
    
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        # Aba com resumo
        summary_df.to_excel(writer, sheet_name='Resumo Financeiro', index=False)
        
        # Aba com medicamentos
        med_df.to_excel(writer, sheet_name='Custos por Medicamento', index=False)
        
        # Ajustar larguras
        for sheet_name in writer.sheets:
            worksheet = writer.sheets[sheet_name]
            for column in worksheet.columns:
                max_length = 0
                column_letter = column[0].column_letter
                for cell in column:
                    try:
                        if len(str(cell.value)) > max_length:
                            max_length = len(str(cell.value))
                    except:
                        pass
                adjusted_width = min(max_length + 2, 50)
                worksheet.column_dimensions[column_letter].width = adjusted_width
    
    output.seek(0)
    
    return send_file(
        output,
        as_attachment=True,
        download_name=f'relatorio_financeiro_{datetime.now().strftime("%Y%m%d")}.xlsx',
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )

def generate_stock_excel(medications, total_value):
    """Gerar Excel do relatório de estoque"""
    output = BytesIO()
    
    data = []
    for med in medications:
        valor_total = (med.current_stock * (med.unit_cost or 0))
        data.append({
            'Medicamento': med.commercial_name,
            'Genérico': med.generic_name,
            'Dosagem': med.dosage,
            'Estoque Atual': med.current_stock,
            'Estoque Mínimo': med.minimum_stock,
            'Valor Unitário': float(med.unit_cost or 0),
            'Valor Total': float(valor_total),
            'Status': 'Baixo' if med.current_stock <= med.minimum_stock else 'Normal',
            'Data Vencimento': med.expiry_date.strftime('%d/%m/%Y') if med.expiry_date else 'N/A',
            'Lote': med.batch_number or 'N/A',
            'Tipo': med.medication_type.value if hasattr(med.medication_type, 'value') else str(med.medication_type)
        })
    
    df = pd.DataFrame(data)
    
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, sheet_name='Estoque', index=False)
        
        # Resumo
        summary = pd.DataFrame([
            {'Indicador': 'Total de Medicamentos', 'Valor': len(medications)},
            {'Indicador': 'Valor Total do Estoque', 'Valor': float(total_value)},
            {'Indicador': 'Medicamentos com Estoque Baixo', 'Valor': len([m for m in medications if m.current_stock <= m.minimum_stock])},
            {'Indicador': 'Medicamentos Vencidos', 'Valor': len([m for m in medications if m.expiry_date and m.expiry_date < date.today()])},
            {'Indicador': 'Medicamentos Próximos ao Vencimento', 'Valor': len([m for m in medications if m.expiry_date and m.expiry_date <= date.today() + timedelta(days=30)])}
        ])
        summary.to_excel(writer, sheet_name='Resumo', index=False)
        
        # Ajustar larguras
        for sheet_name in writer.sheets:
            worksheet = writer.sheets[sheet_name]
            for column in worksheet.columns:
                max_length = 0
                column_letter = column[0].column_letter
                for cell in column:
                    try:
                        if len(str(cell.value)) > max_length:
                            max_length = len(str(cell.value))
                    except:
                        pass
                adjusted_width = min(max_length + 2, 50)
                worksheet.column_dimensions[column_letter].width = adjusted_width
    
    output.seek(0)
    
    return send_file(
        output,
        as_attachment=True,
        download_name=f'relatorio_estoque_{datetime.now().strftime("%Y%m%d")}.xlsx',
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )

def generate_expiry_excel(near_expiry, expired):
    """Gerar Excel do relatório de vencimentos"""
    output = BytesIO()
    
    # Medicamentos vencidos
    expired_data = []
    for med in expired:
        expired_data.append({
            'Medicamento': med.commercial_name,
            'Genérico': med.generic_name,
            'Dosagem': med.dosage,
            'Lote': med.batch_number or 'N/A',
            'Data Vencimento': med.expiry_date.strftime('%d/%m/%Y') if med.expiry_date else 'N/A',
            'Estoque': med.current_stock,
            'Valor Unitário': float(med.unit_cost or 0),
            'Perda Total': float((med.current_stock * (med.unit_cost or 0))),
            'Tipo': med.medication_type.value if hasattr(med.medication_type, 'value') else str(med.medication_type)
        })
    
    # Medicamentos próximos ao vencimento
    near_expiry_data = []
    for med in near_expiry:
        days_left = (med.expiry_date - date.today()).days if med.expiry_date else 0
        near_expiry_data.append({
            'Medicamento': med.commercial_name,
            'Genérico': med.generic_name,
            'Dosagem': med.dosage,
            'Lote': med.batch_number or 'N/A',
            'Data Vencimento': med.expiry_date.strftime('%d/%m/%Y') if med.expiry_date else 'N/A',
            'Dias Restantes': days_left,
            'Estoque': med.current_stock,
            'Valor Unitário': float(med.unit_cost or 0),
            'Valor Total': float((med.current_stock * (med.unit_cost or 0))),
            'Tipo': med.medication_type.value if hasattr(med.medication_type, 'value') else str(med.medication_type)
        })
    
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        # Aba vencidos
        if expired_data:
            expired_df = pd.DataFrame(expired_data)
            expired_df.to_excel(writer, sheet_name='Vencidos', index=False)
        
        # Aba próximos ao vencimento
        if near_expiry_data:
            near_df = pd.DataFrame(near_expiry_data)
            near_df.to_excel(writer, sheet_name='Próximos ao Vencimento', index=False)
        
        # Resumo
        total_expired_value = sum([item['Perda Total'] for item in expired_data])
        total_near_expiry_value = sum([item['Valor Total'] for item in near_expiry_data])
        
        summary = pd.DataFrame([
            {'Indicador': 'Medicamentos Vencidos', 'Quantidade': len(expired_data), 'Valor': total_expired_value},
            {'Indicador': 'Próximos ao Vencimento', 'Quantidade': len(near_expiry_data), 'Valor': total_near_expiry_value},
            {'Indicador': 'Perda Total', 'Quantidade': len(expired_data) + len(near_expiry_data), 'Valor': total_expired_value + total_near_expiry_value}
        ])
        summary.to_excel(writer, sheet_name='Resumo', index=False)
        
        # Ajustar larguras
        for sheet_name in writer.sheets:
            worksheet = writer.sheets[sheet_name]
            for column in worksheet.columns:
                max_length = 0
                column_letter = column[0].column_letter
                for cell in column:
                    try:
                        if len(str(cell.value)) > max_length:
                            max_length = len(str(cell.value))
                    except:
                        pass
                adjusted_width = min(max_length + 2, 50)
                worksheet.column_dimensions[column_letter].width = adjusted_width
    
    output.seek(0)
    
    return send_file(
        output,
        as_attachment=True,
        download_name=f'relatorio_vencimentos_{datetime.now().strftime("%Y%m%d")}.xlsx',
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )

def generate_consumption_excel(data, start_date, end_date):
    """Gerar Excel do relatório de consumo"""
    output = BytesIO()
    
    consumption_data = []
    for item in data:
        consumption_data.append({
            'Medicamento': item.commercial_name,
            'Genérico': item.generic_name,
            'Dosagem': item.dosage,
            'Quantidade Dispensada': item.total_dispensed,
            'Número de Dispensações': item.dispensations_count,
            'Custo Total': float(item.total_cost) if item.total_cost else 0,
            'Custo Médio por Dispensação': float(item.total_cost) / item.dispensations_count if item.total_cost and item.dispensations_count > 0 else 0,
            'Custo Médio por Unidade': float(item.total_cost) / item.total_dispensed if item.total_cost and item.total_dispensed > 0 else 0
        })
    
    df = pd.DataFrame(consumption_data)
    
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, sheet_name='Consumo', index=False)
        
        # Resumo
        total_dispensed = sum([item['Quantidade Dispensada'] for item in consumption_data])
        total_cost = sum([item['Custo Total'] for item in consumption_data])
        total_dispensations = sum([item['Número de Dispensações'] for item in consumption_data])
        
        summary = pd.DataFrame([
            {'Indicador': 'Total Medicamentos Dispensados', 'Valor': total_dispensed},
            {'Indicador': 'Total de Dispensações', 'Valor': total_dispensations},
            {'Indicador': 'Custo Total', 'Valor': total_cost},
            {'Indicador': 'Custo Médio por Dispensação', 'Valor': total_cost / total_dispensations if total_dispensations > 0 else 0},
            {'Indicador': 'Período Início', 'Valor': start_date.strftime('%d/%m/%Y') if start_date else 'N/A'},
            {'Indicador': 'Período Fim', 'Valor': end_date.strftime('%d/%m/%Y') if end_date else 'N/A'}
        ])
        summary.to_excel(writer, sheet_name='Resumo', index=False)
        
        # Ajustar larguras
        for sheet_name in writer.sheets:
            worksheet = writer.sheets[sheet_name]
            for column in worksheet.columns:
                max_length = 0
                column_letter = column[0].column_letter
                for cell in column:
                    try:
                        if len(str(cell.value)) > max_length:
                            max_length = len(str(cell.value))
                    except:
                        pass
                adjusted_width = min(max_length + 2, 50)
                worksheet.column_dimensions[column_letter].width = adjusted_width
    
    output.seek(0)
    
    return send_file(
        output,
        as_attachment=True,
        download_name=f'relatorio_consumo_{datetime.now().strftime("%Y%m%d")}.xlsx',
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )

def generate_dispensations_excel(dispensations, total_cost, total_items, start_date, end_date):
    """Gerar Excel do relatório de dispensações"""
    output = BytesIO()
    
    dispensation_data = []
    for disp in dispensations:
        dispensation_data.append({
            'ID': disp.id,
            'Data': disp.dispensation_date.strftime('%d/%m/%Y'),
            'Hora': disp.dispensation_date.strftime('%H:%M'),
            'Paciente': disp.patient.full_name,
            'CPF': format_cpf(disp.patient.cpf),
            'Idade': disp.patient.age,
            'Atendente': disp.dispenser.full_name,
            'Número de Itens': len(disp.items),
            'Custo Total': float(disp.total_cost) if disp.total_cost else 0,
            'Status': disp.status.value if hasattr(disp.status, 'value') else str(disp.status),
            'Observações': disp.observations[:100] + '...' if disp.observations and len(disp.observations) > 100 else (disp.observations or '')
        })
    
    df = pd.DataFrame(dispensation_data)
    
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, sheet_name='Dispensações', index=False)
        
        # Resumo
        summary = pd.DataFrame([
            {'Indicador': 'Total de Dispensações', 'Valor': len(dispensations)},
            {'Indicador': 'Total de Itens Dispensados', 'Valor': total_items},
            {'Indicador': 'Custo Total', 'Valor': float(total_cost)},
            {'Indicador': 'Custo Médio por Dispensação', 'Valor': float(total_cost) / len(dispensations) if dispensations else 0},
            {'Indicador': 'Itens Médios por Dispensação', 'Valor': total_items / len(dispensations) if dispensations else 0},
            {'Indicador': 'Período Início', 'Valor': start_date.strftime('%d/%m/%Y') if start_date else 'N/A'},
            {'Indicador': 'Período Fim', 'Valor': end_date.strftime('%d/%m/%Y') if end_date else 'N/A'}
        ])
        summary.to_excel(writer, sheet_name='Resumo', index=False)
        
        # Ajustar larguras
        for sheet_name in writer.sheets:
            worksheet = writer.sheets[sheet_name]
            for column in worksheet.columns:
                max_length = 0
                column_letter = column[0].column_letter
                for cell in column:
                    try:
                        if len(str(cell.value)) > max_length:
                            max_length = len(str(cell.value))
                    except:
                        pass
                adjusted_width = min(max_length + 2, 50)
                worksheet.column_dimensions[column_letter].width = adjusted_width
    
    output.seek(0)
    
    return send_file(
        output,
        as_attachment=True,
        download_name=f'relatorio_dispensacoes_{datetime.now().strftime("%Y%m%d")}.xlsx',
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )

def generate_patients_excel(patients, age_groups, esus_stats=None):
    """Gerar Excel do relatório de pacientes"""
    output = BytesIO()
    
    patient_data = []
    for patient in patients:
        patient_data.append({
            'ID': patient.id,
            'Nome': patient.full_name,
            'CPF': format_cpf(patient.cpf),
            'CNS': format_cns(patient.cns) if patient.cns else 'N/A',
            'Data Nascimento': patient.birth_date.strftime('%d/%m/%Y') if patient.birth_date else 'N/A',
            'Idade': patient.age,
            'Sexo': patient.gender_display,
            'Telefone Principal': patient.primary_phone or 'N/A',
            'Telefone Residencial': patient.home_phone or 'N/A',
            'Telefone Celular': patient.cell_phone or 'N/A',
            'Telefone Contato': patient.contact_phone or 'N/A',
            'Email': patient.email or 'N/A',
            'Nome da Mãe': patient.mother_name or 'N/A',
            'Nome do Pai': patient.father_name or 'N/A',
            'Endereço': patient.address or 'N/A',
            'Número': patient.number or 'N/A',
            'Bairro': patient.neighborhood or 'N/A',
            'Cidade': patient.city or 'N/A',
            'Estado': patient.state or 'N/A',
            'CEP': patient.zip_code or 'N/A',
            'Origem': patient.source_display,
            'Data Cadastro': patient.created_at.strftime('%d/%m/%Y') if patient.created_at else 'N/A',
            'Última Atualização': patient.updated_at.strftime('%d/%m/%Y') if patient.updated_at else 'N/A',
            'Sincronização e-SUS': patient.esus_sync_date.strftime('%d/%m/%Y') if patient.esus_sync_date else 'N/A'
        })
    
    df = pd.DataFrame(patient_data)
    
    # Estatísticas por idade
    age_df = pd.DataFrame([
        {'Faixa Etária': faixa, 'Quantidade': quantidade}
        for faixa, quantidade in age_groups.items()
    ])
    
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, sheet_name='Pacientes', index=False)
        age_df.to_excel(writer, sheet_name='Faixas Etárias', index=False)
        
        # ✅ ABA COM ESTATÍSTICAS E-SUS
        if esus_stats:
            esus_df = pd.DataFrame([
                {'Estatística e-SUS': 'Cadastro Local', 'Quantidade': esus_stats['local_source']},
                {'Estatística e-SUS': 'Importado do e-SUS', 'Quantidade': esus_stats['imported_source']},
                {'Estatística e-SUS': 'e-SUS Direto', 'Quantidade': esus_stats['esus_source']},
                {'Estatística e-SUS': 'Com CNS', 'Quantidade': esus_stats['with_cns']},
                {'Estatística e-SUS': 'Com Nome da Mãe', 'Quantidade': esus_stats['with_mother_name']},
                {'Estatística e-SUS': 'Múltiplos Telefones', 'Quantidade': esus_stats['with_multiple_phones']}
            ])
            esus_df.to_excel(writer, sheet_name='Estatísticas e-SUS', index=False)
        
        # Resumo
        summary = pd.DataFrame([
            {'Indicador': 'Total de Pacientes', 'Valor': len(patients)},
            {'Indicador': 'Pacientes Ativos', 'Valor': len([p for p in patients if p.is_active])},
            {'Indicador': 'Idade Média', 'Valor': sum([p.age for p in patients]) / len(patients) if patients else 0},
            {'Indicador': 'Pacientes com CNS', 'Valor': len([p for p in patients if p.cns])},
            {'Indicador': 'Pacientes com Telefone', 'Valor': len([p for p in patients if p.primary_phone])},
            {'Indicador': 'Pacientes com Email', 'Valor': len([p for p in patients if p.email])}
        ])
        summary.to_excel(writer, sheet_name='Resumo', index=False)
        
        # Ajustar larguras
        for sheet_name in writer.sheets:
            worksheet = writer.sheets[sheet_name]
            for column in worksheet.columns:
                max_length = 0
                column_letter = column[0].column_letter
                for cell in column:
                    try:
                        if len(str(cell.value)) > max_length:
                            max_length = len(str(cell.value))
                    except:
                        pass
                adjusted_width = min(max_length + 2, 50)
                worksheet.column_dimensions[column_letter].width = adjusted_width
    
    output.seek(0)
    
    return send_file(
        output,
        as_attachment=True,
        download_name=f'relatorio_pacientes_{datetime.now().strftime("%Y%m%d")}.xlsx',
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )

def generate_esus_integration_excel(stats, quality_stats, recent_imports):
    """✅ GERAR EXCEL DO RELATÓRIO DE INTEGRAÇÃO E-SUS"""
    output = BytesIO()
    
    # Dados das importações recentes
    imports_data = []
    for patient in recent_imports:
        imports_data.append({
            'Nome': patient.full_name,
            'CPF': format_cpf(patient.cpf),
            'CNS': format_cns(patient.cns) if patient.cns else 'N/A',
            'Data Nascimento': patient.birth_date.strftime('%d/%m/%Y') if patient.birth_date else 'N/A',
            'Idade': patient.age,
            'Nome da Mãe': patient.mother_name or 'N/A',
            'Telefone': patient.primary_phone or 'N/A',
            'Endereço': patient.full_address,
            'Data Importação': patient.created_at.strftime('%d/%m/%Y %H:%M'),
            'Sincronização': patient.esus_sync_date.strftime('%d/%m/%Y %H:%M') if patient.esus_sync_date else 'N/A'
        })
    
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        # Aba com estatísticas gerais
        general_df = pd.DataFrame([
            {'Indicador': 'Total de Pacientes', 'Valor': stats['total_patients']},
            {'Indicador': 'Pacientes Locais', 'Valor': stats['local_patients']},
            {'Indicador': 'Importados do e-SUS', 'Valor': stats['imported_patients']},
            {'Indicador': 'Pacientes e-SUS Direto', 'Valor': stats['esus_patients']},
            {'Indicador': 'Sincronizados', 'Valor': stats['synced_patients']},
            {'Indicador': 'Com CNS', 'Valor': stats['patients_with_cns']},
            {'Indicador': '% Importados', 'Valor': f"{(stats['imported_patients']/stats['total_patients']*100):.1f}%" if stats['total_patients'] > 0 else "0%"},
            {'Indicador': '% Com CNS', 'Valor': f"{(stats['patients_with_cns']/stats['total_patients']*100):.1f}%" if stats['total_patients'] > 0 else "0%"}
        ])
        general_df.to_excel(writer, sheet_name='Estatísticas Gerais', index=False)
        
        # Aba com qualidade dos dados
        quality_df = pd.DataFrame([
            {'Indicador de Qualidade': 'Endereço Completo', 'Quantidade': quality_stats['complete_address'], 'Percentual': f"{(quality_stats['complete_address']/stats['total_patients']*100):.1f}%" if stats['total_patients'] > 0 else "0%"},
            {'Indicador de Qualidade': 'Com Telefone', 'Quantidade': quality_stats['with_phone'], 'Percentual': f"{(quality_stats['with_phone']/stats['total_patients']*100):.1f}%" if stats['total_patients'] > 0 else "0%"},
            {'Indicador de Qualidade': 'Informações dos Pais', 'Quantidade': quality_stats['with_parents_info'], 'Percentual': f"{(quality_stats['with_parents_info']/stats['total_patients']*100):.1f}%" if stats['total_patients'] > 0 else "0%"}
        ])
        quality_df.to_excel(writer, sheet_name='Qualidade dos Dados', index=False)
        
        # Aba com importações recentes
        if imports_data:
            imports_df = pd.DataFrame(imports_data)
            imports_df.to_excel(writer, sheet_name='Importações Recentes', index=False)
        
        # Ajustar larguras
        for sheet_name in writer.sheets:
            worksheet = writer.sheets[sheet_name]
            for column in worksheet.columns:
                max_length = 0
                column_letter = column[0].column_letter
                for cell in column:
                    try:
                        if len(str(cell.value)) > max_length:
                            max_length = len(str(cell.value))
                    except:
                        pass
                adjusted_width = min(max_length + 2, 50)
                worksheet.column_dimensions[column_letter].width = adjusted_width
    
    output.seek(0)
    
    return send_file(
        output,
        as_attachment=True,
        download_name=f'relatorio_integracao_esus_{datetime.now().strftime("%Y%m%d")}.xlsx',
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )

# ✅ ADICIONAR DEPOIS DAS OUTRAS FUNÇÕES AUXILIARES (final do arquivo)

def format_cpf(cpf):
    """Formatar CPF para exibição"""
    if not cpf or len(cpf) != 11:
        return cpf
    return f"{cpf[:3]}.{cpf[3:6]}.{cpf[6:9]}-{cpf[9:]}"

def format_cns(cns):
    """Formatar CNS para exibição"""
    if not cns or len(cns) != 15:
        return cns
    return f"{cns[:3]} {cns[3:7]} {cns[7:11]} {cns[11:]}"

def format_phone(phone):
    """Formatar telefone para exibição"""
    if not phone:
        return None
    
    # Remover apenas dígitos
    clean_phone = ''.join(filter(str.isdigit, phone))
    
    if len(clean_phone) == 11:  # Celular com DDD
        return f"({clean_phone[:2]}) {clean_phone[2:7]}-{clean_phone[7:]}"
    elif len(clean_phone) == 10:  # Fixo com DDD
        return f"({clean_phone[:2]}) {clean_phone[2:6]}-{clean_phone[6:]}"
    else:
        return phone