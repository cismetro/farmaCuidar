"""
Módulo de integração com banco de dados e-SUS
Banco local: MySQL | Banco e-SUS: PostgreSQL
"""

import mysql.connector
import psycopg2
import logging
from psycopg2.extras import RealDictCursor
from datetime import datetime
from flask import current_app
from sqlalchemy import create_engine, text
from app.database import db

def get_mysql_connection():
    """Conexão com banco local MySQL"""
    try:
        config = current_app.config
        connection = mysql.connector.connect(
            host=config['MYSQL_HOST'],
            port=config['MYSQL_PORT'],
            user=config['MYSQL_USER'],
            password=config['MYSQL_PASSWORD'],
            database=config['MYSQL_DATABASE'],
            charset='utf8mb4',
            collation='utf8mb4_unicode_ci'
        )
        return connection
    except Exception as e:
        logging.error(f"Erro ao conectar MySQL local: {e}")
        return None

def get_esus_db_credentials():
    """Obtém as credenciais do banco de dados e-SUS do MySQL."""
    try:
        conn = get_mysql_connection()
        if not conn:
            return None
            
        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute('SELECT dbname, user, password, host, port, municipio FROM Config WHERE id = 1')
            result = cursor.fetchone()
            cursor.close()
            conn.close()
            return result
        except mysql.connector.Error:
            # Fallback se coluna municipio não existir
            cursor.execute('SELECT dbname, user, password, host, port FROM Config WHERE id = 1')
            result = cursor.fetchone()
            cursor.close()
            conn.close()
            if result:
                result['municipio'] = None
            return result
    except Exception as e:
        logging.error(f"Erro ao obter credenciais e-SUS: {e}")
        return None

def get_esus_db_connection():
    """Estabelece a conexão com o banco de dados e-SUS PostgreSQL."""
    credentials = get_esus_db_credentials()
    if not credentials:
        logging.warning("Credenciais e-SUS não configuradas")
        return None
    
    try:
        required_fields = ['dbname', 'user', 'password', 'host', 'port']
        if not all(credentials.get(field) for field in required_fields):
            logging.warning("Credenciais e-SUS incompletas")
            return None
        
        connection = psycopg2.connect(
            dbname=credentials['dbname'],
            user=credentials['user'],
            password=credentials['password'],
            host=credentials['host'],
            port=credentials['port'],
            connect_timeout=10
        )
        
        logging.info("Conexão e-SUS estabelecida com sucesso")
        return connection
        
    except psycopg2.OperationalError as e:
        logging.error(f"Erro de conexão e-SUS: {e}")
        return None
    except Exception as e:
        logging.error(f"Erro inesperado na conexão e-SUS: {e}")
        return None

def test_esus_connection():
    """Testa a conexão com o banco e-SUS"""
    try:
        conn = get_esus_db_connection()
        if not conn:
            return False, "Não foi possível estabelecer conexão"
        
        with conn.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        
        conn.close()
        return True, "Conexão testada com sucesso"
        
    except Exception as e:
        return False, f"Erro no teste de conexão: {str(e)}"

def init_config_table():
    """Cria a tabela Config se não existir"""
    try:
        conn = get_mysql_connection()
        if not conn:
            return False
            
        cursor = conn.cursor()
        
        # Criar tabela Config
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS Config (
                id INT PRIMARY KEY AUTO_INCREMENT,
                dbname VARCHAR(100) NOT NULL,
                user VARCHAR(100) NOT NULL,
                password VARCHAR(255) NOT NULL,
                host VARCHAR(100) NOT NULL,
                port INT NOT NULL DEFAULT 5432,
                municipio VARCHAR(100) NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """)
        
        conn.commit()
        cursor.close()
        conn.close()
        return True
        
    except Exception as e:
        logging.error(f"Erro ao criar tabela Config: {e}")
        return False

def save_esus_credentials(dbname, user, password, host, port, municipio=None):
    """Salva as credenciais do e-SUS no banco MySQL local"""
    try:
        # Garantir que a tabela existe
        if not init_config_table():
            return False, "Erro ao inicializar tabela de configuração"
        
        conn = get_mysql_connection()
        if not conn:
            return False, "Erro de conexão com banco local"
            
        cursor = conn.cursor()
        
        # Verificar se já existe configuração
        cursor.execute("SELECT id FROM Config WHERE id = 1")
        exists = cursor.fetchone()
        
        if exists:
            # Atualizar
            cursor.execute("""
                UPDATE Config 
                SET dbname = %s, user = %s, password = %s, host = %s, port = %s, municipio = %s, updated_at = NOW()
                WHERE id = 1
            """, (dbname, user, password, host, port, municipio))
        else:
            # Inserir
            cursor.execute("""
                INSERT INTO Config (id, dbname, user, password, host, port, municipio)
                VALUES (1, %s, %s, %s, %s, %s, %s)
            """, (dbname, user, password, host, port, municipio))
        
        conn.commit()
        cursor.close()
        conn.close()
        
        logging.info("Credenciais e-SUS salvas com sucesso")
        return True, "Credenciais salvas com sucesso"
        
    except Exception as e:
        logging.error(f"Erro ao salvar credenciais e-SUS: {e}")
        return False, f"Erro ao salvar credenciais: {str(e)}"

def search_patient_in_esus(query, search_type='all'):
    """Buscar pacientes no banco e-SUS PostgreSQL"""
    try:
        conn = get_esus_db_connection()
        if not conn:
            logging.warning("Conexão com e-SUS não disponível")
            return []
        
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            conditions = []
            params = []
            
            if search_type in ['all', 'name']:
                conditions.append("UPPER(no_cidadao) LIKE UPPER(%s)")
                params.append(f'%{query}%')
            
            if search_type in ['all', 'cpf']:
                clean_cpf = ''.join(filter(str.isdigit, query))
                if len(clean_cpf) >= 3:
                    conditions.append("nu_cpf LIKE %s")
                    params.append(f'%{clean_cpf}%')
            
            if search_type in ['all', 'cns']:
                clean_cns = ''.join(filter(str.isdigit, query))
                if len(clean_cns) >= 3:
                    conditions.append("nu_cns LIKE %s")
                    params.append(f'%{clean_cns}%')
            
            if search_type in ['all', 'birth_date']:
                try:
                    if '/' in query:
                        birth_date = datetime.strptime(query, '%d/%m/%Y').date()
                    elif '-' in query:
                        birth_date = datetime.strptime(query, '%Y-%m-%d').date()
                    else:
                        raise ValueError("Formato de data inválido")
                    
                    conditions.append("dt_nascimento = %s")
                    params.append(birth_date)
                except ValueError:
                    pass
            
            if not conditions:
                return []
            
            # Query SQL para buscar na tabela tb_cidadao do e-SUS
            sql = f"""
                SELECT 
                    nu_cpf, nu_cns, no_cidadao, dt_nascimento,
                    no_mae, no_pai, no_sexo,
                    ds_logradouro, nu_numero, no_bairro, ds_cep,
                    nu_telefone_residencial, nu_telefone_celular, nu_telefone_contato
                FROM tb_cidadao 
                WHERE ({' OR '.join(conditions)})
                AND nu_cpf IS NOT NULL 
                AND no_cidadao IS NOT NULL
                AND LENGTH(TRIM(no_cidadao)) > 0
                ORDER BY no_cidadao
                LIMIT 20
            """
            
            cursor.execute(sql, params)
            results = cursor.fetchall()
            
            logging.info(f"Busca e-SUS retornou {len(results)} resultados para: {query}")
            return [dict(row) for row in results]
            
    except Exception as e:
        logging.error(f"Erro na busca e-SUS: {e}")
        return []
    finally:
        if conn:
            conn.close()

def get_esus_patient_by_cpf(cpf):
    """Buscar paciente específico por CPF no e-SUS"""
    clean_cpf = ''.join(filter(str.isdigit, cpf))
    if len(clean_cpf) != 11:
        return None
    
    results = search_patient_in_esus(clean_cpf, 'cpf')
    return results[0] if results else None

def get_esus_patient_by_cns(cns):
    """Buscar paciente específico por CNS no e-SUS"""
    clean_cns = ''.join(filter(str.isdigit, cns))
    if len(clean_cns) != 15:
        return None
    
    results = search_patient_in_esus(clean_cns, 'cns')
    return results[0] if results else None

def get_esus_statistics():
    """Obter estatísticas do banco e-SUS"""
    try:
        conn = get_esus_db_connection()
        if not conn:
            return None
        
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            # Total de cidadãos
            cursor.execute("SELECT COUNT(*) as total FROM tb_cidadao WHERE nu_cpf IS NOT NULL")
            total = cursor.fetchone()['total']
            
            # Por gênero
            cursor.execute("""
                SELECT no_sexo, COUNT(*) as count 
                FROM tb_cidadao 
                WHERE nu_cpf IS NOT NULL AND no_sexo IS NOT NULL
                GROUP BY no_sexo
            """)
            gender_stats = cursor.fetchall()
            
            # Com CNS
            cursor.execute("""
                SELECT COUNT(*) as with_cns 
                FROM tb_cidadao 
                WHERE nu_cpf IS NOT NULL AND nu_cns IS NOT NULL
            """)
            with_cns = cursor.fetchone()['with_cns']
            
            return {
                'total_patients': total,
                'gender_distribution': {row['no_sexo']: row['count'] for row in gender_stats},
                'patients_with_cns': with_cns,
                'cns_percentage': round((with_cns / total * 100), 2) if total > 0 else 0
            }
            
    except Exception as e:
        logging.error(f"Erro ao obter estatísticas e-SUS: {e}")
        return None
    finally:
        if conn:
            conn.close()

# Funções utilitárias para limpeza de dados
def clean_cpf(cpf):
    """Limpar e validar CPF"""
    if not cpf:
        return None
    clean = ''.join(filter(str.isdigit, str(cpf)))
    return clean if len(clean) == 11 else None

def clean_cns(cns):
    """Limpar e validar CNS"""
    if not cns:
        return None
    clean = ''.join(filter(str.isdigit, str(cns)))
    return clean if len(clean) == 15 else None

def clean_cep(cep):
    """Limpar CEP"""
    if not cep:
        return None
    clean = ''.join(filter(str.isdigit, str(cep)))
    return clean if len(clean) == 8 else None

def clean_phone(phone):
    """Limpar telefone"""
    if not phone:
        return None
    clean = ''.join(filter(str.isdigit, str(phone)))
    return clean if len(clean) >= 10 else None

def map_gender_from_esus(esus_gender):
    """Mapear gênero do e-SUS para o sistema"""
    if not esus_gender:
        return None
    
    gender_map = {
        'MASCULINO': 'M',
        'FEMININO': 'F',
        'M': 'M',
        'F': 'F',
        'MASC': 'M',
        'FEM': 'F'
    }
    
    return gender_map.get(str(esus_gender).upper(), 'O')

def format_esus_data_for_display(esus_data):
    """Formatar dados do e-SUS para exibição"""
    if not esus_data:
        return None
    
    # Formatar CPF
    cpf = clean_cpf(esus_data.get('nu_cpf'))
    formatted_cpf = f"{cpf[:3]}.{cpf[3:6]}.{cpf[6:9]}-{cpf[9:]}" if cpf else None
    
    # Formatar CNS
    cns = clean_cns(esus_data.get('nu_cns'))
    formatted_cns = f"{cns[:3]} {cns[3:7]} {cns[7:11]} {cns[11:]}" if cns else None
    
    # Formatar data de nascimento
    birth_date = esus_data.get('dt_nascimento')
    formatted_birth_date = birth_date.strftime('%d/%m/%Y') if birth_date else None
    
    # Calcular idade
    age = None
    if birth_date:
        today = datetime.now().date()
        age = today.year - birth_date.year - ((today.month, today.day) < (birth_date.month, birth_date.day))
    
    # Telefone principal
    primary_phone = (
        clean_phone(esus_data.get('nu_telefone_celular')) or
        clean_phone(esus_data.get('nu_telefone_contato')) or
        clean_phone(esus_data.get('nu_telefone_residencial'))
    )
    
    return {
        'cpf': formatted_cpf,
        'cns': formatted_cns,
        'full_name': esus_data.get('no_cidadao', '').strip(),
        'birth_date': formatted_birth_date,
        'age': age,
        'mother_name': esus_data.get('no_mae', '').strip() or None,
        'father_name': esus_data.get('no_pai', '').strip() or None,
        'gender': map_gender_from_esus(esus_data.get('no_sexo')),
        'address': esus_data.get('ds_logradouro', '').strip() or None,
        'number': esus_data.get('nu_numero', '').strip() or None,
        'neighborhood': esus_data.get('no_bairro', '').strip() or None,
        'zip_code': clean_cep(esus_data.get('ds_cep')),
        'primary_phone': primary_phone,
        'home_phone': clean_phone(esus_data.get('nu_telefone_residencial')),
        'cell_phone': clean_phone(esus_data.get('nu_telefone_celular')),
        'contact_phone': clean_phone(esus_data.get('nu_telefone_contato')),
        'raw_data': esus_data
    }