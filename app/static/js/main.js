/**
 * FarmaCuidar - Cosmópolis
 * Sistema de Gestão Farmacêutica
 * JavaScript Principal
 */

// Configuração global
$(document).ready(function() {
    // Inicializar componentes
    initializeApp();
    
    // Configurar AJAX
    setupAjax();
    
    // Inicializar tooltips e popovers
    initializeBootstrapComponents();
    
    // Configurar alertas automáticos
    setupAlerts();
    
    // Configurar máscaras de input
    setupInputMasks();
    
    // Configurar validações
    setupValidation();
});

/**
 * Inicialização principal da aplicação
 */
function initializeApp() {
    console.log('🚀 FarmaCuidar iniciado');
    
    // Configurar tema
    setupTheme();
    
    // Configurar navegação
    setupNavigation();
    
    // Configurar auto-save para formulários longos
    setupAutoSave();
}

/**
 * Configuração do AJAX global
 */
function setupAjax() {
    // Token CSRF
    const csrfToken = $('meta[name=csrf-token]').attr('content');
    
    $.ajaxSetup({
        beforeSend: function(xhr, settings) {
            if (!/^(GET|HEAD|OPTIONS|TRACE)$/i.test(settings.type) && !this.crossDomain) {
                xhr.setRequestHeader("X-CSRFToken", csrfToken);
            }
        }
    });
    
    // Interceptar erros AJAX globais
    $(document).ajaxError(function(event, xhr, settings, thrownError) {
        console.error('Erro AJAX:', {
            url: settings.url,
            status: xhr.status,
            error: thrownError
        });
        
        if (xhr.status === 401) {
            showNotification('Sessão expirada. Redirecionando para login...', 'error');
            setTimeout(() => {
                window.location.href = '/login';
            }, 2000);
        } else if (xhr.status === 403) {
            showNotification('Acesso negado.', 'error');
        } else if (xhr.status >= 500) {
            showNotification('Erro interno do servidor. Tente novamente.', 'error');
        }
    });
}

/**
 * Inicializar componentes do Bootstrap
 */
function initializeBootstrapComponents() {
    // Tooltips
    const tooltipTriggerList = [].slice.call(document.querySelectorAll('[data-bs-toggle="tooltip"]'));
    tooltipTriggerList.map(function(tooltipTriggerEl) {
        return new bootstrap.Tooltip(tooltipTriggerEl);
    });
    
    // Popovers
    const popoverTriggerList = [].slice.call(document.querySelectorAll('[data-bs-toggle="popover"]'));
    popoverTriggerList.map(function(popoverTriggerEl) {
        return new bootstrap.Popover(popoverTriggerEl);
    });
}

/**
 * Configurar sistema de alertas
 */
function setupAlerts() {
    // Atualizar alertas a cada 30 segundos
    setInterval(updateAlerts, 30000);
    
    // Carregar alertas iniciais
    updateAlerts();
    
    // Auto-dismiss alerts após 5 segundos
    $('.alert[data-auto-dismiss="true"]').each(function() {
        const alert = this;
        setTimeout(() => {
            $(alert).fadeOut(() => {
                $(alert).remove();
            });
        }, 5000);
    });
}

/**
 * Atualizar alertas do sistema
 */
function updateAlerts() {
    $.ajax({
        url: '/api/stock/alerts',
        method: 'GET',
        success: function(data) {
            updateAlertsBadge(data.total_alerts);
            updateAlertsMenu(data);
        },
        error: function() {
            console.warn('Falha ao carregar alertas');
        }
    });
}

/**
 * Atualizar badge de alertas
 */
function updateAlertsBadge(count) {
    const badge = $('#alertsBadge');
    if (count > 0) {
        badge.text(count).show();
        badge.addClass('pulse');
    } else {
        badge.hide();
        badge.removeClass('pulse');
    }
}

/**
 * Atualizar menu de alertas
 */
function updateAlertsMenu(data) {
    const menu = $('#alertsContent');
    
    if (data.total_alerts === 0) {
        menu.html(`
            <div class="text-center text-muted py-3">
                <i class="fas fa-check-circle fa-2x mb-2"></i>
                <p class="mb-0">Nenhum alerta no momento</p>
            </div>
        `);
        return;
    }
    
    let html = '<div class="list-group list-group-flush">';
    
    if (data.low_stock > 0) {
        html += `
            <a href="/inventory/alerts" class="list-group-item list-group-item-action">
                <div class="d-flex w-100 justify-content-between">
                    <h6 class="mb-1">
                        <i class="fas fa-exclamation-triangle text-warning me-2"></i>
                        Estoque Baixo
                    </h6>
                    <span class="badge bg-warning">${data.low_stock}</span>
                </div>
                <p class="mb-1 small">Medicamentos com estoque baixo</p>
            </a>
        `;
    }
    
    if (data.near_expiry > 0) {
        html += `
            <a href="/inventory/alerts" class="list-group-item list-group-item-action">
                <div class="d-flex w-100 justify-content-between">
                    <h6 class="mb-1">
                        <i class="fas fa-calendar-times text-danger me-2"></i>
                        Próximo ao Vencimento
                    </h6>
                    <span class="badge bg-danger">${data.near_expiry}</span>
                </div>
                <p class="mb-1 small">Medicamentos vencendo em 30 dias</p>
            </a>
        `;
    }
    
    if (data.pending_high_cost > 0) {
        html += `
            <a href="/high-cost" class="list-group-item list-group-item-action">
                <div class="d-flex w-100 justify-content-between">
                    <h6 class="mb-1">
                        <i class="fas fa-star text-primary me-2"></i>
                        Alto Custo Pendente
                    </h6>
                    <span class="badge bg-primary">${data.pending_high_cost}</span>
                </div>
                <p class="mb-1 small">Processos aguardando avaliação</p>
            </a>
        `;
    }
    
    html += '</div>';
    menu.html(html);
}

/**
 * Configurar máscaras de input
 */
function setupInputMasks() {
    // CPF
    $(document).on('input', 'input[data-mask="cpf"]', function() {
        let value = this.value.replace(/\D/g, '');
        value = value.replace(/(\d{3})(\d)/, '$1.$2');
        value = value.replace(/(\d{3})(\d)/, '$1.$2');
        value = value.replace(/(\d{3})(\d{1,2})$/, '$1-$2');
        this.value = value;
    });
    
    // CNS
    $(document).on('input', 'input[data-mask="cns"]', function() {
        let value = this.value.replace(/\D/g, '');
        value = value.replace(/(\d{3})(\d)/, '$1 $2');
        value = value.replace(/(\d{4})(\d)/, '$1 $2');
        value = value.replace(/(\d{4})(\d)/, '$1 $2');
        this.value = value;
    });
    
    // Telefone
    $(document).on('input', 'input[data-mask="phone"]', function() {
        let value = this.value.replace(/\D/g, '');
        if (value.length <= 10) {
            value = value.replace(/(\d{2})(\d)/, '($1) $2');
            value = value.replace(/(\d{4})(\d)/, '$1-$2');
        } else {
            value = value.replace(/(\d{2})(\d)/, '($1) $2');
            value = value.replace(/(\d{5})(\d)/, '$1-$2');
        }
        this.value = value;
    });
    
    // CEP
    $(document).on('input', 'input[data-mask="cep"]', function() {
        let value = this.value.replace(/\D/g, '');
        value = value.replace(/(\d{5})(\d)/, '$1-$2');
        this.value = value;
    });
    
    // Moeda
    $(document).on('input', 'input[data-mask="currency"]', function() {
        let value = this.value.replace(/[^\d,]/g, '');
        value = value.replace(',', '.');
        this.value = value;
    });
}

/**
 * Configurar validações de formulário
 */
function setupValidation() {
    // Validação em tempo real
    $(document).on('blur', 'input, select, textarea', function() {
        validateField($(this));
    });
    
    // Validação de CPF
    $(document).on('blur', 'input[data-validate="cpf"]', function() {
        const cpf = this.value.replace(/\D/g, '');
        if (cpf && !isValidCPF(cpf)) {
            setFieldError($(this), 'CPF inválido');
        } else {
            clearFieldError($(this));
        }
    });
    
    // Validação de email
    $(document).on('blur', 'input[type="email"]', function() {
        const email = this.value;
        if (email && !isValidEmail(email)) {
            setFieldError($(this), 'Email inválido');
        } else {
            clearFieldError($(this));
        }
    });
}

/**
 * Validar campo individual
 */
function validateField($field) {
    const value = $field.val();
    const required = $field.prop('required');
    
    if (required && !value.trim()) {
        setFieldError($field, 'Campo obrigatório');
        return false;
    }
    
    clearFieldError($field);
    return true;
}

/**
 * Definir erro no campo
 */
function setFieldError($field, message) {
    $field.addClass('is-invalid');
    
    let feedback = $field.siblings('.invalid-feedback');
    if (feedback.length === 0) {
        feedback = $('<div class="invalid-feedback"></div>');
        $field.after(feedback);
    }
    feedback.text(message);
}

/**
 * Limpar erro do campo
 */
function clearFieldError($field) {
    $field.removeClass('is-invalid');
    $field.siblings('.invalid-feedback').remove();
}

/**
 * Validar CPF
 */
function isValidCPF(cpf) {
    if (cpf.length !== 11 || /^(\d)\1{10}$/.test(cpf)) {
        return false;
    }
    
    let sum = 0;
    for (let i = 0; i < 9; i++) {
        sum += parseInt(cpf[i]) * (10 - i);
    }
    let remainder = (sum * 10) % 11;
    if (remainder === 10 || remainder === 11) remainder = 0;
    if (remainder !== parseInt(cpf[9])) return false;
    
    sum = 0;
    for (let i = 0; i < 10; i++) {
        sum += parseInt(cpf[i]) * (11 - i);
    }
    remainder = (sum * 10) % 11;
    if (remainder === 10 || remainder === 11) remainder = 0;
    if (remainder !== parseInt(cpf[10])) return false;
    
    return true;
}

/**
 * Validar email
 */
function isValidEmail(email) {
    const re = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
    return re.test(email);
}

/**
 * Configurar tema claro/escuro
 */
function setupTheme() {
    // Detectar preferência do sistema
    if (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches) {
        document.documentElement.setAttribute('data-bs-theme', 'dark');
    }
    
    // Listener para mudanças
    window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', event => {
        document.documentElement.setAttribute('data-bs-theme', event.matches ? 'dark' : 'light');
    });
}

/**
 * Configurar navegação
 */
function setupNavigation() {
    // Destacar página atual
    const currentPath = window.location.pathname;
    $('.navbar-nav .nav-link').each(function() {
        const href = $(this).attr('href');
        if (href && currentPath.startsWith(href) && href !== '/') {
            $(this).addClass('active');
        }
    });
    
    // Collapse navbar em mobile após clique
    $('.navbar-nav .nav-link').on('click', function() {
        if (window.innerWidth < 992) {
            $('.navbar-collapse').collapse('hide');
        }
    });
}

/**
 * Configurar auto-save para formulários
 */
function setupAutoSave() {
    let autoSaveTimer;
    
    $(document).on('input', 'form[data-auto-save="true"] input, form[data-auto-save="true"] textarea', function() {
        clearTimeout(autoSaveTimer);
        autoSaveTimer = setTimeout(() => {
            saveFormData($(this).closest('form'));
        }, 2000);
    });
}

/**
 * Salvar dados do formulário no localStorage
 */
function saveFormData($form) {
    const formId = $form.attr('id');
    if (!formId) return;
    
    const formData = {};
    $form.find('input, textarea, select').each(function() {
        const name = $(this).attr('name');
        if (name && $(this).attr('type') !== 'file') {
            formData[name] = $(this).val();
        }
    });
    
    try {
        localStorage.setItem(`form_${formId}`, JSON.stringify(formData));
        showNotification('Rascunho salvo automaticamente', 'info', 2000);
    } catch (e) {
        console.warn('Erro ao salvar rascunho:', e);
    }
}

/**
 * Restaurar dados do formulário do localStorage
 */
function restoreFormData($form) {
    const formId = $form.attr('id');
    if (!formId) return;
    
    try {
        const savedData = localStorage.getItem(`form_${formId}`);
        if (savedData) {
            const formData = JSON.parse(savedData);
            Object.keys(formData).forEach(name => {
                const $field = $form.find(`[name="${name}"]`);
                if ($field.length && !$field.val()) {
                    $field.val(formData[name]);
                }
            });
        }
    } catch (e) {
        console.warn('Erro ao restaurar rascunho:', e);
    }
}

/**
 * Limpar dados salvos do formulário
 */
function clearFormData(formId) {
    try {
        localStorage.removeItem(`form_${formId}`);
    } catch (e) {
        console.warn('Erro ao limpar rascunho:', e);
    }
}

/**
 * Exibir notificação
 */
function showNotification(message, type = 'info', duration = 5000) {
    const alertClass = type === 'error' ? 'danger' : type;
    const iconClass = {
        'success': 'fa-check-circle',
        'error': 'fa-exclamation-circle',
        'danger': 'fa-exclamation-circle',
        'warning': 'fa-exclamation-triangle',
        'info': 'fa-info-circle'
    }[type] || 'fa-info-circle';
    
    const alertHtml = `
        <div class="alert alert-${alertClass} alert-dismissible fade show position-fixed" 
             style="top: 20px; right: 20px; z-index: 9999; min-width: 300px;" role="alert">
            <i class="fas ${iconClass} me-2"></i>
            ${message}
            <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
        </div>
    `;
    
    $('body').append(alertHtml);
    
    // Auto-remove após duração especificada
    setTimeout(() => {
        $('.alert').last().fadeOut(() => {
            $('.alert').last().remove();
        });
    }, duration);
}

/**
 * Confirmar ação
 */
function confirmAction(message, callback) {
    if (confirm(message)) {
        callback();
    }
}

/**
 * Loading overlay
 */
function showLoading(message = 'Carregando...') {
    const loadingHtml = `
        <div class="loading-overlay">
            <div class="text-center">
                <div class="spinner-border spinner-border-custom" role="status">
                    <span class="visually-hidden">Carregando...</span>
                </div>
                <p class="mt-3 text-white">${message}</p>
            </div>
        </div>
    `;
    
    $('body').append(loadingHtml);
}

function hideLoading() {
    $('.loading-overlay').remove();
}

/**
 * Formatar moeda
 */
function formatCurrency(value) {
    return new Intl.NumberFormat('pt-BR', {
        style: 'currency',
        currency: 'BRL'
    }).format(value);
}

/**
 * Formatar data
 */
function formatDate(date) {
    return new Intl.DateTimeFormat('pt-BR').format(new Date(date));
}

/**
 * Debounce function
 */
function debounce(func, wait, immediate) {
    let timeout;
    return function executedFunction(...args) {
        const later = () => {
            timeout = null;
            if (!immediate) func(...args);
        };
        const callNow = immediate && !timeout;
        clearTimeout(timeout);
        timeout = setTimeout(later, wait);
        if (callNow) func(...args);
    };
}

/**
 * Utilitários de exportação
 */
window.FarmaCuidar = {
    showNotification,
    confirmAction,
    showLoading,
    hideLoading,
    formatCurrency,
    formatDate,
    debounce,
    isValidCPF,
    isValidEmail,
    validateField,
    setFieldError,
    clearFieldError,
    saveFormData,
    restoreFormData,
    clearFormData
};

// Log de inicialização
console.log('📊 FarmaCuidar JavaScript carregado com sucesso');