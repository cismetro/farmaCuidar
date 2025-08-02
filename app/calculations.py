"""
Módulo de Cálculos Farmacológicos Completo
Funções para cálculos de dispensação, conversão de unidades e dosagens
COM VALIDAÇÕES FARMACOLÓGICAS E INTEGRAÇÃO COM O BANCO
"""

from decimal import Decimal, ROUND_UP
from typing import Dict, Union, Optional, Tuple
import math
import logging

# =================== CONVERSÕES DE UNIDADES ===================

def convert_units(value: float, from_unit: str, to_unit: str) -> float:
    """
    Converter entre unidades farmacológicas
    
    Args:
        value: Valor a ser convertido
        from_unit: Unidade de origem
        to_unit: Unidade de destino
    
    Returns:
        Valor convertido
    """
    # Normalizar unidades para lowercase
    from_unit = from_unit.lower().strip()
    to_unit = to_unit.lower().strip()
    
    # Se as unidades são iguais, retornar o valor original
    if from_unit == to_unit:
        return value
    
    # Tabela de conversões (todas para mg como base)
    weight_conversions = {
        'kg': 1000000,    # 1 kg = 1,000,000 mg
        'g': 1000,        # 1 g = 1,000 mg
        'mg': 1,          # 1 mg = 1 mg (base)
        'mcg': 0.001,     # 1 mcg = 0.001 mg
        'µg': 0.001,      # 1 µg = 0.001 mg (símbolo alternativo)
        'ug': 0.001,      # 1 ug = 0.001 mg (sem símbolo especial)
        'ng': 0.000001,   # 1 ng = 0.000001 mg
    }
    
    # Conversões de volume (todas para ml como base)
    volume_conversions = {
        'l': 1000,        # 1 L = 1000 ml
        'ml': 1,          # 1 ml = 1 ml (base)
        'cc': 1,          # 1 cc = 1 ml
        'dl': 100,        # 1 dl = 100 ml
        'cl': 10,         # 1 cl = 10 ml
    }
    
    # Verificar se ambas as unidades são de peso
    if from_unit in weight_conversions and to_unit in weight_conversions:
        # Converter para mg primeiro, depois para unidade de destino
        mg_value = value * weight_conversions[from_unit]
        return mg_value / weight_conversions[to_unit]
    
    # Verificar se ambas as unidades são de volume
    elif from_unit in volume_conversions and to_unit in volume_conversions:
        # Converter para ml primeiro, depois para unidade de destino
        ml_value = value * volume_conversions[from_unit]
        return ml_value / volume_conversions[to_unit]
    
    # Se não conseguir converter, retornar valor original
    else:
        raise ValueError(f"Não é possível converter de '{from_unit}' para '{to_unit}'")

# =================== VALIDAÇÕES FARMACOLÓGICAS ===================

def validate_medication_configuration(strength_value: float, strength_unit: str, 
                                    volume_per_dose: float, volume_unit: str,
                                    package_size: float, package_unit: str) -> Tuple[bool, str]:
    """
    ✅ VALIDAR CONFIGURAÇÃO FARMACOLÓGICA
    
    Args:
        strength_value: Valor da concentração
        strength_unit: Unidade da concentração  
        volume_per_dose: Volume por dose
        volume_unit: Unidade do volume
        package_size: Tamanho da embalagem
        package_unit: Unidade da embalagem
        
    Returns:
        Tupla (válido, mensagem)
    """
    try:
        # ✅ REGRA 1: Para comprimidos/cápsulas, volume_per_dose deve ser 1
        if volume_unit.lower() in ['comp', 'comprimido', 'caps', 'capsula']:
            if volume_per_dose != 1:
                return False, f"❌ ERRO: Para {volume_unit}, o volume por dose deve ser 1 (cada {volume_unit} contém {strength_value}{strength_unit})"
        
        # ✅ REGRA 2: Para líquidos, volume deve ser > 0.1ml
        if volume_unit.lower() in ['ml', 'l']:
            if volume_per_dose < 0.1:
                return False, f"❌ ERRO: Volume muito pequeno para medicamento líquido: {volume_per_dose}{volume_unit}"
        
        # ✅ REGRA 3: Embalagem deve ser maior que volume por dose
        if volume_unit == package_unit:
            if package_size < volume_per_dose:
                return False, f"❌ ERRO: Embalagem ({package_size}{package_unit}) não pode ser menor que volume por dose ({volume_per_dose}{volume_unit})"
        
        # ✅ REGRA 4: Concentração deve ser positiva
        if strength_value <= 0:
            return False, f"❌ ERRO: Concentração deve ser positiva: {strength_value}{strength_unit}"
        
        # ✅ REGRA 5: Para comprimidos, embalagem deve ser múltiplo inteiro
        if volume_unit.lower() in ['comp', 'comprimido', 'caps', 'capsula']:
            if package_size != int(package_size):
                return False, f"❌ ERRO: Para {volume_unit}, embalagem deve ser número inteiro: {package_size}"
        
        return True, "✅ Configuração farmacológica válida"
        
    except Exception as e:
        return False, f"❌ ERRO na validação: {str(e)}"

def get_unit_type(unit: str) -> str:
    """Identificar tipo de unidade"""
    unit_lower = unit.lower().strip()
    
    weight_units = ['kg', 'g', 'mg', 'mcg', 'µg', 'ug', 'ng']
    volume_units = ['l', 'ml', 'cc', 'dl', 'cl']
    solid_units = ['comp', 'comprimido', 'caps', 'capsula', 'comprimidos', 'cápsulas']
    
    if unit_lower in weight_units:
        return 'weight'
    elif unit_lower in volume_units:
        return 'volume'
    elif unit_lower in solid_units:
        return 'solid'
    else:
        return 'unknown'

# =================== CÁLCULOS DE DOSAGEM ===================

def calculate_dose_volume(prescribed_dose: float, prescribed_unit: str, 
                         strength_value: float, strength_unit: str,
                         volume_per_dose: float, volume_unit: str) -> Dict:
    """
    Calcular o volume necessário para uma dose prescrita
    
    Args:
        prescribed_dose: Dose prescrita
        prescribed_unit: Unidade da dose prescrita
        strength_value: Concentração do medicamento (valor)
        strength_unit: Unidade da concentração
        volume_per_dose: Volume da concentração
        volume_unit: Unidade do volume
    
    Returns:
        Dicionário com resultado do cálculo
    """
    try:
        # ✅ VALIDAÇÃO FARMACOLÓGICA
        is_valid, msg = validate_medication_configuration(
            strength_value, strength_unit, volume_per_dose, volume_unit, 1, volume_unit
        )
        if not is_valid:
            return {
                'success': False,
                'error': f"Configuração inválida: {msg}",
                'dose_volume': 0
            }
        
        # Converter unidades se necessário
        if prescribed_unit != strength_unit:
            prescribed_dose_converted = convert_units(prescribed_dose, prescribed_unit, strength_unit)
        else:
            prescribed_dose_converted = prescribed_dose
        
        # ✅ CALCULAR VOLUME NECESSÁRIO
        # Fórmula: (dose prescrita / concentração) × volume da concentração
        dose_volume = (prescribed_dose_converted / strength_value) * volume_per_dose
        
        return {
            'success': True,
            'dose_volume': round(dose_volume, 3),
            'dose_unit': volume_unit,
            'prescribed_dose_converted': prescribed_dose_converted,
            'conversion_applied': prescribed_unit != strength_unit,
            'formula_used': f"({prescribed_dose_converted}{strength_unit} ÷ {strength_value}{strength_unit}) × {volume_per_dose}{volume_unit}"
        }
        
    except Exception as e:
        return {
            'success': False,
            'error': str(e),
            'dose_volume': 0
        }

def calculate_total_volume(dose_volume: float, frequency: int, days: int) -> Dict:
    """
    Calcular volume total necessário para o tratamento
    
    Args:
        dose_volume: Volume por dose
        frequency: Frequência por dia
        days: Dias de tratamento
    
    Returns:
        Dicionário com resultado do cálculo
    """
    try:
        total_doses = frequency * days
        total_volume = dose_volume * total_doses
        
        return {
            'success': True,
            'total_doses': total_doses,
            'total_volume': round(total_volume, 3),
            'calculation': f"{dose_volume} × {frequency} × {days} = {total_volume:.3f}"
        }
        
    except Exception as e:
        return {
            'success': False,
            'error': str(e),
            'total_volume': 0
        }

def calculate_packages_needed(total_volume: float, package_size: float, stability_days: Optional[int] = None, treatment_days: int = 0) -> Dict:
    """
    Calcular quantas embalagens são necessárias
    
    Args:
        total_volume: Volume total necessário
        package_size: Tamanho da embalagem
        stability_days: Dias de estabilidade (opcional)
        treatment_days: Dias de tratamento
    
    Returns:
        Dicionário com resultado do cálculo
    """
    try:
        # Calcular embalagens necessárias (arredondando para cima)
        packages_exact = total_volume / package_size
        packages_needed = math.ceil(packages_exact)
        
        # ✅ CONSIDERAR ESTABILIDADE
        if stability_days and stability_days < treatment_days:
            # Medicamento vence antes do fim do tratamento
            # Calcular quantas embalagens por período de estabilidade
            periods_needed = math.ceil(treatment_days / stability_days)
            volume_per_period = total_volume / periods_needed
            packages_per_period = math.ceil(volume_per_period / package_size)
            packages_needed = packages_per_period * periods_needed
            
            stability_warning = f"⚠️ Medicamento estável por {stability_days} dias. Dispensar {packages_needed} embalagens em {periods_needed} período(s)."
        else:
            stability_warning = None
        
        # Volume total que será dispensado
        total_dispensed = packages_needed * package_size
        
        # Volume que sobra
        leftover = total_dispensed - total_volume
        
        return {
            'success': True,
            'packages_needed': packages_needed,
            'packages_exact': round(packages_exact, 2),
            'total_dispensed': round(total_dispensed, 3),
            'leftover': round(leftover, 3),
            'stability_warning': stability_warning,
            'calculation': f"{total_volume} ÷ {package_size} = {packages_exact:.2f} → {packages_needed} embalagem(s)"
        }
        
    except Exception as e:
        return {
            'success': False,
            'error': str(e),
            'packages_needed': 0
        }

# =================== FUNÇÃO PRINCIPAL DE CÁLCULO ===================

def calculate_complete_dispensation(prescribed_dose: float, prescribed_unit: str,
                                  strength_value: float, strength_unit: str,
                                  volume_per_dose: float, volume_unit: str,
                                  frequency: int, days: int, 
                                  package_size: float, package_unit: str,
                                  drops_per_ml: Optional[int] = None,
                                  stability_days: Optional[int] = None) -> Dict:
    """
    ✅ CÁLCULO COMPLETO DE DISPENSAÇÃO FARMACOLÓGICA
    
    Args:
        prescribed_dose: Dose prescrita
        prescribed_unit: Unidade da dose prescrita
        strength_value: Valor da concentração
        strength_unit: Unidade da concentração
        volume_per_dose: Volume da concentração
        volume_unit: Unidade do volume
        frequency: Frequência por dia
        days: Dias de tratamento
        package_size: Tamanho da embalagem
        package_unit: Unidade da embalagem
        drops_per_ml: Gotas por ml (opcional)
        stability_days: Dias de estabilidade (opcional)
    
    Returns:
        Dicionário com resultado completo
    """
    try:
        # ✅ 0. VALIDAÇÃO INICIAL
        is_valid, validation_msg = validate_medication_configuration(
            strength_value, strength_unit, volume_per_dose, volume_unit, package_size, package_unit
        )
        if not is_valid:
            return {
                'success': False,
                'error': validation_msg
            }
        
        # ✅ 1. Calcular volume por dose
        dose_calc = calculate_dose_volume(
            prescribed_dose, prescribed_unit,
            strength_value, strength_unit,
            volume_per_dose, volume_unit
        )
        
        if not dose_calc['success']:
            return dose_calc
        
        dose_volume = dose_calc['dose_volume']
        
        # ✅ 2. Calcular volume total
        total_calc = calculate_total_volume(dose_volume, frequency, days)
        
        if not total_calc['success']:
            return total_calc
        
        total_volume = total_calc['total_volume']
        total_doses = total_calc['total_doses']
        
        # ✅ 3. Calcular embalagens necessárias (considerando estabilidade)
        packages_calc = calculate_packages_needed(total_volume, package_size, stability_days, days)
        
        if not packages_calc['success']:
            return packages_calc
        
        # ✅ 4. Calcular informações sobre gotas (se aplicável)
        drops_info = None
        if drops_per_ml and volume_unit.lower() == 'ml':
            drops_per_dose = dose_volume * drops_per_ml
            drops_info = {
                'drops_per_dose': round(drops_per_dose, 1),
                'drops_per_day': round(drops_per_dose * frequency, 1),
                'total_drops': round(drops_per_dose * total_doses, 1),
                'conversion_text': f"{dose_volume:.2f}ml = {drops_per_dose:.0f} gotas"
            }
        
        # ✅ 5. Informações sobre estabilidade
        stability_info = None
        if stability_days:
            stability_info = {
                'days': stability_days,
                'note': f"Após aberto, usar em até {stability_days} dias",
                'sufficient': days <= stability_days,
                'warning': packages_calc.get('stability_warning')
            }
        
        # ✅ 6. Montar resultado final
        result = {
            'success': True,
            'dose_per_administration': dose_volume,
            'dose_unit': volume_unit,
            'total_doses': total_doses,
            'total_volume': total_volume,
            'packages_needed': packages_calc['packages_needed'],
            'total_dispensed': packages_calc['total_dispensed'],
            'leftover': packages_calc['leftover'],
            'drops_info': drops_info,
            'stability_info': stability_info,
            'calculation_details': {
                'dose_formula': dose_calc['formula_used'],
                'frequency_text': f"{frequency}x/dia por {days} dias",
                'total_calculation': total_calc['calculation'],
                'packages_calculation': packages_calc['calculation'],
                'conversion_applied': dose_calc['conversion_applied'],
                'configuration': f"{strength_value}{strength_unit}/{volume_per_dose}{volume_unit} (embalagem: {package_size}{package_unit})"
            }
        }
        
        return result
        
    except Exception as e:
        return {
            'success': False,
            'error': f"Erro no cálculo completo: {str(e)}"
        }

# =================== INTEGRAÇÃO COM BANCO DE DADOS ===================

def calculate_from_database(medication_id: int, prescribed_dose: float, 
                          prescribed_unit: str, frequency: int, treatment_days: int) -> Dict:
    """
    ✅ CALCULAR DISPENSAÇÃO USANDO DADOS DO BANCO
    
    Args:
        medication_id: ID do medicamento na tabela medications
        prescribed_dose: Dose prescrita
        prescribed_unit: Unidade da dose prescrita
        frequency: Frequência por dia
        treatment_days: Dias de tratamento
        
    Returns:
        Dict: Resultado do cálculo
    """
    try:
        # Importar aqui para evitar dependência circular
        from app.models import Medication, MedicationDispensing
        
        # ✅ 1. Buscar medicamento
        medication = Medication.query.get(medication_id)
        if not medication:
            return {
                'success': False,
                'error': f'Medicamento com ID {medication_id} não encontrado'
            }
        
        # ✅ 2. Buscar configuração de dispensação
        config = medication.dispensing_config
        if not config:
            return {
                'success': False,
                'error': f'Medicamento "{medication.commercial_name}" não possui configuração de cálculos',
                'needs_configuration': True
            }
        
        # ✅ 3. Extrair dados da configuração
        calc_data = {
            'prescribed_dose': prescribed_dose,
            'prescribed_unit': prescribed_unit,
            'strength_value': float(config.strength_value),
            'strength_unit': config.strength_unit,
            'volume_per_dose': float(config.volume_per_dose),
            'volume_unit': config.volume_unit,
            'frequency': frequency,
            'days': treatment_days,
            'package_size': float(config.package_size),
            'package_unit': config.package_unit,
            'drops_per_ml': config.drops_per_ml,
            'stability_days': config.stability_days
        }
        
        # ✅ 4. Chamar função de cálculo
        result = calculate_complete_dispensation(**calc_data)
        
        # ✅ 5. Adicionar informações do medicamento
        if result['success']:
            result.update({
                'medication_id': medication_id,
                'medication_name': medication.commercial_name,
                'medication_form': medication.pharmaceutical_form,
                'current_stock': medication.current_stock,
                'sufficient_stock': medication.current_stock >= result['packages_needed']
            })
        
        return result
        
    except Exception as e:
        logging.error(f"Erro no cálculo para medicamento {medication_id}: {e}")
        return {
            'success': False,
            'error': f'Erro interno: {str(e)}'
        }

# =================== FORMATAÇÃO DE RESULTADOS ===================

def format_calculation_result(result: Dict, include_details: bool = True) -> str:
    """
    Formatar resultado do cálculo para exibição
    
    Args:
        result: Resultado do cálculo
        include_details: Se deve incluir detalhes do cálculo
    
    Returns:
        String formatada
    """
    if not result.get('success'):
        return f"❌ Erro no cálculo: {result.get('error', 'Erro desconhecido')}"
    
    # ✅ Resultado principal
    text = f"📋 **RESULTADO DO CÁLCULO**\n"
    text += f"Medicamento: {result.get('medication_name', 'N/A')}\n"
    text += f"Dose por administração: {result['dose_per_administration']:.3f} {result['dose_unit']}\n"
    text += f"Total de doses: {result['total_doses']}\n"
    text += f"Volume total necessário: {result['total_volume']:.3f} {result['dose_unit']}\n"
    text += f"Embalagens necessárias: {result['packages_needed']}\n"
    text += f"Total a dispensar: {result['total_dispensed']:.3f} {result['dose_unit']}\n"
    
    if result.get('leftover', 0) > 0:
        text += f"Sobra: {result['leftover']:.3f} {result['dose_unit']}\n"
    
    # ✅ Informações sobre gotas
    if result.get('drops_info'):
        drops = result['drops_info']
        text += f"\n💧 **INFORMAÇÕES SOBRE GOTAS:**\n"
        text += f"Gotas por dose: {drops['drops_per_dose']}\n"
        text += f"Gotas por dia: {drops['drops_per_day']}\n"
        text += f"Conversão: {drops['conversion_text']}\n"
    
    # ✅ Informações sobre estabilidade
    if result.get('stability_info'):
        stability = result['stability_info']
        text += f"\n⏱️ **ESTABILIDADE:**\n"
        text += f"{stability['note']}\n"
        if stability.get('warning'):
            text += f"{stability['warning']}\n"
        if not stability.get('sufficient', True):
            text += "⚠️ ATENÇÃO: O tempo de tratamento pode exceder a estabilidade\n"
    
    # ✅ Verificação de estoque
    if 'sufficient_stock' in result:
        text += f"\n📦 **ESTOQUE:**\n"
        text += f"Estoque atual: {result.get('current_stock', 0)} unidades\n"
        if result['sufficient_stock']:
            text += "✅ Estoque suficiente\n"
        else:
            text += "❌ Estoque insuficiente\n"
    
    # ✅ Detalhes do cálculo
    if include_details and result.get('calculation_details'):
        details = result['calculation_details']
        text += f"\n🔢 **DETALHES DO CÁLCULO:**\n"
        text += f"Configuração: {details['configuration']}\n"
        text += f"Fórmula: {details['dose_formula']}\n"
        text += f"Frequência: {details['frequency_text']}\n"
        text += f"Cálculo total: {details['total_calculation']}\n"
        text += f"Embalagens: {details['packages_calculation']}\n"
        
        if details.get('conversion_applied'):
            text += "🔄 Conversão de unidades aplicada\n"
    
    return text

# =================== FUNÇÕES AUXILIARES ===================

def validate_calculation_inputs(data: Dict) -> Tuple[bool, str]:
    """
    ✅ VALIDAR DADOS DE ENTRADA PARA CÁLCULOS
    
    Args:
        data: Dicionário com dados de entrada
    
    Returns:
        Tupla (válido, mensagem_erro)
    """
    required_fields = [
        'prescribed_dose', 'prescribed_unit', 'strength_value', 'strength_unit',
        'volume_per_dose', 'volume_unit', 'frequency_per_day', 'treatment_days',
        'package_size'
    ]
    
    # ✅ Verificar campos obrigatórios
    for field in required_fields:
        if field not in data or data[field] is None:
            return False, f"Campo obrigatório: {field}"
    
    # ✅ Verificar valores numéricos
    numeric_fields = ['prescribed_dose', 'strength_value', 'volume_per_dose', 
                     'frequency_per_day', 'treatment_days', 'package_size']
    
    for field in numeric_fields:
        try:
            value = float(data[field])
            if value <= 0:
                return False, f"Campo {field} deve ser maior que zero"
        except (ValueError, TypeError):
            return False, f"Campo {field} deve ser um número válido"
    
    # ✅ Verificar frequência e dias (devem ser inteiros)
    try:
        frequency = int(data['frequency_per_day'])
        days = int(data['treatment_days'])
        if frequency < 1 or days < 1:
            return False, "Frequência e dias devem ser pelo menos 1"
    except (ValueError, TypeError):
        return False, "Frequência e dias devem ser números inteiros"
    
    # ✅ Verificar unidades suportadas
    valid_weight_units = ['kg', 'g', 'mg', 'mcg', 'µg', 'ug', 'ng']
    valid_volume_units = ['l', 'ml', 'cc', 'dl', 'cl']
    valid_solid_units = ['comp', 'comprimido', 'caps', 'capsula']
    
    prescribed_unit = data['prescribed_unit'].lower()
    strength_unit = data['strength_unit'].lower()
    volume_unit = data['volume_unit'].lower()
    
    all_valid_units = valid_weight_units + valid_volume_units + valid_solid_units
    
    if prescribed_unit not in all_valid_units:
        return False, f"Unidade da dose prescrita inválida: {data['prescribed_unit']}"
    
    if strength_unit not in all_valid_units:
        return False, f"Unidade da concentração inválida: {data['strength_unit']}"
    
    if volume_unit not in all_valid_units:
        return False, f"Unidade do volume inválida: {data['volume_unit']}"
    
    # ✅ Validação farmacológica específica
    is_valid, msg = validate_medication_configuration(
        data['strength_value'], data['strength_unit'],
        data['volume_per_dose'], data['volume_unit'],
        data['package_size'], data.get('package_unit', data['volume_unit'])
    )
    
    if not is_valid:
        return False, msg
    
    return True, "✅ Dados válidos"

def get_supported_units() -> Dict[str, list]:
    """
    Obter lista de unidades suportadas
    
    Returns:
        Dicionário com listas de unidades por categoria
    """
    return {
        'weight': ['kg', 'g', 'mg', 'mcg', 'µg', 'ug', 'ng'],
        'volume': ['l', 'ml', 'cc', 'dl', 'cl'],
        'solid': ['comp', 'comprimido', 'caps', 'capsula'],
        'weight_display': [
            {'value': 'kg', 'label': 'Quilograma (kg)'},
            {'value': 'g', 'label': 'Grama (g)'},
            {'value': 'mg', 'label': 'Miligrama (mg)'},
            {'value': 'mcg', 'label': 'Micrograma (mcg)'},
            {'value': 'µg', 'label': 'Micrograma (µg)'},
            {'value': 'ug', 'label': 'Micrograma (ug)'},
            {'value': 'ng', 'label': 'Nanograma (ng)'}
        ],
        'volume_display': [
            {'value': 'l', 'label': 'Litro (L)'},
            {'value': 'ml', 'label': 'Mililitro (ml)'},
            {'value': 'cc', 'label': 'Centímetro cúbico (cc)'},
            {'value': 'dl', 'label': 'Decilitro (dl)'},
            {'value': 'cl', 'label': 'Centilitro (cl)'}
        ],
        'solid_display': [
            {'value': 'comp', 'label': 'Comprimido'},
            {'value': 'caps', 'label': 'Cápsula'}
        ]
    }

# =================== EXEMPLOS E TESTES ===================

def test_calculations():
    """
    ✅ FUNÇÃO PARA TESTAR OS CÁLCULOS COM EXEMPLOS REAIS
    """
    print("🧪 **TESTANDO CÁLCULOS FARMACOLÓGICOS**\n")
    
    # ✅ TESTE 1: Medicamento líquido (Amoxicilina 250mg/5ml)
    print("📋 **TESTE 1: Amoxicilina Suspensão**")
    test_data_1 = {
        'prescribed_dose': 500,
        'prescribed_unit': 'mg',
        'strength_value': 250,
        'strength_unit': 'mg',
        'volume_per_dose': 5,
        'volume_unit': 'ml',
        'frequency': 3,
        'days': 7,
        'package_size': 150,
        'package_unit': 'ml',
        'drops_per_ml': 20
    }
    
    result_1 = calculate_complete_dispensation(**test_data_1)
    print(format_calculation_result(result_1))
    print("-" * 50)
    
    # ✅ TESTE 2: Medicamento sólido CORRETO (750mg por 1 comprimido)
    print("📋 **TESTE 2: Amoxicilina Comprimidos (CORRETO)**")
    test_data_2 = {
        'prescribed_dose': 750,
        'prescribed_unit': 'mg',
        'strength_value': 750,
        'strength_unit': 'mg',
        'volume_per_dose': 1,  # ✅ CORRETO: 1 comprimido
        'volume_unit': 'comp',
        'frequency': 2,
        'days': 7,
        'package_size': 12,
        'package_unit': 'comp',
        'drops_per_ml': None
    }
    
    result_2 = calculate_complete_dispensation(**test_data_2)
    print(format_calculation_result(result_2))
    print("-" * 50)
    
    # ✅ TESTE 3: Configuração INCORRETA (para mostrar validação)
    print("📋 **TESTE 3: Configuração Incorreta (VALIDAÇÃO)**")
    test_data_3 = {
        'prescribed_dose': 750,
        'prescribed_unit': 'mg',
        'strength_value': 750,
        'strength_unit': 'mg',
        'volume_per_dose': 10,  # ❌ INCORRETO: 10 comprimidos
        'volume_unit': 'comp',
        'frequency': 2,
        'days': 7,
        'package_size': 12,
        'package_unit': 'comp',
        'drops_per_ml': None
    }
    
    result_3 = calculate_complete_dispensation(**test_data_3)
    print(format_calculation_result(result_3))
    
    return result_1, result_2, result_3

def test_database_integration(medication_id: int = 10):
    """
    ✅ TESTAR INTEGRAÇÃO COM BANCO DE DADOS
    """
    print(f"🔗 **TESTANDO INTEGRAÇÃO COM BANCO (medication_id={medication_id})**\n")
    
    result = calculate_from_database(
        medication_id=medication_id,
        prescribed_dose=750,
        prescribed_unit='mg',
        frequency=2,
        treatment_days=7
    )
    
    print(format_calculation_result(result))
    return result

if __name__ == "__main__":
    # Executar testes
    test_calculations()
    print("\n" + "=" * 80 + "\n")
    # test_database_integration()  # Descomente para testar com banco