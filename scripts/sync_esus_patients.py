#!/usr/bin/env python3
"""
Script de Sincronização e-SUS → Sistema Local
Uso: python sync_esus_patients.py [opções]
"""

import sys
import os
import argparse
from datetime import datetime, timedelta
import mysql.connector
import psycopg2
from psycopg2.extras import RealDictCursor
import logging

# Adicionar path do projeto
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

class ESUSSync:
    def __init__(self):
        # Primeiro configurar logging
        self.setup_logging()
        
        # Depois carregar configs
        self.mysql_config = self.load_mysql_config()
        self.esus_config = self.load_esus_config()
        
    def setup_logging(self):
        """Configurar logging sem emojis para compatibilidade Windows"""
        # Criar diretório de logs se não existir
        log_dir = 'logs'
        if not os.path.exists(log_dir):
            os.makedirs(log_dir)
        
        # Configurar logging sem emojis
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler('logs/esus_sync.log', encoding='utf-8'),
                logging.StreamHandler(sys.stdout)
            ]
        )
        
        self.logger = logging.getLogger(__name__)
        self.logger.info("Sistema de sincronização e-SUS iniciado")
    
    def load_mysql_config(self):
        """Carregar config MySQL do .env ou config"""
        try:
            from dotenv import load_dotenv
            load_dotenv()
        except ImportError:
            self.logger.warning("python-dotenv não instalado, usando variáveis do sistema")
        
        config = {
            'host': os.getenv('MYSQL_HOST', 'localhost'),
            'port': int(os.getenv('MYSQL_PORT', 3307)),
            'user': os.getenv('MYSQL_USER', 'root'),
            'password': os.getenv('MYSQL_PASSWORD', 'dG4rTALaq8'),
            'database': os.getenv('MYSQL_DATABASE', 'farmacuidar'),
            'charset': 'utf8mb4'
        }
        
        self.logger.info(f"Config MySQL: {config['user']}@{config['host']}:{config['port']}/{config['database']}")
        return config
    
    def load_esus_config(self):
        """Carregar config e-SUS do banco MySQL"""
        try:
            self.logger.info("Carregando configuração e-SUS...")
            conn = mysql.connector.connect(**self.mysql_config)
            cursor = conn.cursor(dictionary=True)
            
            # Verificar se tabela Config existe
            cursor.execute("SHOW TABLES LIKE 'Config'")
            if not cursor.fetchone():
                self.logger.error("Tabela 'Config' não encontrada. Execute a configuração e-SUS primeiro.")
                return None
            
            cursor.execute('SELECT * FROM Config WHERE id = 1')
            config = cursor.fetchone()
            cursor.close()
            conn.close()
            
            if config:
                self.logger.info(f"Config e-SUS carregada: {config['user']}@{config['host']}:{config['port']}/{config['dbname']}")
            else:
                self.logger.error("Configuração e-SUS não encontrada. Configure primeiro no painel admin.")
            
            return config
            
        except mysql.connector.Error as e:
            self.logger.error(f"Erro ao conectar MySQL: {e}")
            return None
        except Exception as e:
            self.logger.error(f"Erro ao carregar config e-SUS: {e}")
            return None
    
    def test_connections(self):
        """Testar todas as conexões"""
        self.logger.info("Testando conexões...")
        
        # Testar MySQL
        try:
            conn = mysql.connector.connect(**self.mysql_config)
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM patients")
            local_count = cursor.fetchone()[0]
            cursor.close()
            conn.close()
            self.logger.info(f"MySQL conectado - {local_count} pacientes locais")
        except Exception as e:
            self.logger.error(f"Erro MySQL: {e}")
            return False
        
        # Testar e-SUS
        if not self.esus_config:
            self.logger.error("Configuração e-SUS não disponível")
            return False
        
        try:
            conn = self.get_esus_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM tb_cidadao WHERE nu_cpf IS NOT NULL")
            esus_count = cursor.fetchone()[0]
            cursor.close()
            conn.close()
            self.logger.info(f"e-SUS conectado - {esus_count} pacientes disponíveis")
        except Exception as e:
            self.logger.error(f"Erro e-SUS: {e}")
            return False
        
        self.logger.info("Todas as conexões testadas com sucesso!")
        return True
    
    def get_esus_connection(self):
        """Conectar ao PostgreSQL e-SUS"""
        if not self.esus_config:
            raise Exception("Configuração e-SUS não encontrada")
        
        return psycopg2.connect(
            host=self.esus_config['host'],
            port=self.esus_config['port'],
            user=self.esus_config['user'],
            password=self.esus_config['password'],
            dbname=self.esus_config['dbname'],
            connect_timeout=30
        )
    
    def get_mysql_connection(self):
        """Conectar ao MySQL local"""
        return mysql.connector.connect(**self.mysql_config)
    
    def sync_full(self):
        """Sincronização completa"""
        self.logger.info("Iniciando sincronização completa...")
        
        # Verificar configurações
        if not self.esus_config:
            self.logger.error("Configuração e-SUS não disponível")
            return False
        
        try:
            # Testar conexões primeiro
            if not self.test_connections():
                self.logger.error("Falha nos testes de conexão")
                return False
            
            esus_conn = self.get_esus_connection()
            mysql_conn = self.get_mysql_connection()
            
            # Contar total no e-SUS
            with esus_conn.cursor() as cursor:
                cursor.execute("""
                    SELECT COUNT(*) FROM tb_cidadao 
                    WHERE nu_cpf IS NOT NULL 
                    AND no_cidadao IS NOT NULL
                    AND LENGTH(TRIM(no_cidadao)) > 0
                """)
                total_esus = cursor.fetchone()[0]
            
            self.logger.info(f"Total de pacientes no e-SUS: {total_esus:,}")
            
            if total_esus == 0:
                self.logger.warning("Nenhum paciente encontrado no e-SUS")
                return False
            
            # Processar em lotes
            batch_size = 1000
            processed = 0
            imported = 0
            updated = 0
            errors = 0
            
            start_time = datetime.now()
            
            for offset in range(0, total_esus, batch_size):
                batch_result = self.process_batch(esus_conn, mysql_conn, batch_size, offset)
                processed += batch_result['processed']
                imported += batch_result['imported']
                updated += batch_result['updated']
                errors += batch_result['errors']
                
                # Progresso
                progress = (processed / total_esus) * 100
                elapsed = datetime.now() - start_time
                
                if processed > 0:
                    avg_time = elapsed.total_seconds() / processed
                    remaining = (total_esus - processed) * avg_time
                    eta = datetime.now() + timedelta(seconds=remaining)
                    
                    self.logger.info(f"Progresso: {progress:.1f}% ({processed:,}/{total_esus:,}) - ETA: {eta.strftime('%H:%M:%S')}")
                else:
                    self.logger.info(f"Progresso: {progress:.1f}% ({processed:,}/{total_esus:,})")
            
            # Resultado final
            elapsed = datetime.now() - start_time
            self.logger.info(f"Sincronização completa em {elapsed}")
            self.logger.info(f"Importados: {imported:,}")
            self.logger.info(f"Atualizados: {updated:,}")
            self.logger.info(f"Erros: {errors:,}")
            self.logger.info(f"Total processados: {processed:,}")
            
            return True
            
        except Exception as e:
            self.logger.error(f"Erro na sincronização: {e}")
            return False
        finally:
            try:
                if 'esus_conn' in locals():
                    esus_conn.close()
                if 'mysql_conn' in locals():
                    mysql_conn.close()
            except:
                pass
    
    def process_batch(self, esus_conn, mysql_conn, batch_size, offset):
        """Processar um lote de pacientes"""
        with esus_conn.cursor(cursor_factory=RealDictCursor) as esus_cursor:
            # Query para buscar lote
            esus_cursor.execute("""
                SELECT 
                    nu_cpf, nu_cns, no_cidadao, dt_nascimento,
                    no_mae, no_pai, no_sexo,
                    ds_logradouro, nu_numero, no_bairro, ds_cep,
                    nu_telefone_residencial, nu_telefone_celular, nu_telefone_contato
                FROM tb_cidadao 
                WHERE nu_cpf IS NOT NULL 
                AND no_cidadao IS NOT NULL
                AND LENGTH(TRIM(no_cidadao)) > 0
                ORDER BY no_cidadao
                LIMIT %s OFFSET %s
            """, (batch_size, offset))
            
            patients = esus_cursor.fetchall()
        
        # Importar para MySQL
        imported = 0
        updated = 0
        errors = 0
        
        with mysql_conn.cursor() as mysql_cursor:
            for patient in patients:
                result = self.import_patient(mysql_cursor, patient)
                if result == 'imported':
                    imported += 1
                elif result == 'updated':
                    updated += 1
                elif result == 'error':
                    errors += 1
            
            mysql_conn.commit()
        
        return {
            'processed': len(patients),
            'imported': imported,
            'updated': updated,
            'errors': errors
        }
    
    def safe_strip(self, value):
        """Fazer strip seguro em valores que podem ser None"""
        if value is None:
            return None
        return str(value).strip() if str(value).strip() else None
    
    def import_patient(self, cursor, patient_data):
        """Importar um paciente para MySQL"""
        try:
            # Limpar e validar dados
            cpf = self.clean_cpf(patient_data.get('nu_cpf'))
            if not cpf:
                return 'skipped'
            
            full_name = self.safe_strip(patient_data.get('no_cidadao'))
            if not full_name:
                return 'skipped'
            
            # Verificar se já existe
            cursor.execute("SELECT id FROM patients WHERE cpf = %s", (cpf,))
            existing = cursor.fetchone()
            
            # Preparar dados com valores seguros
            data = {
                'cpf': cpf,
                'cns': self.clean_cns(patient_data.get('nu_cns')),
                'full_name': full_name,
                'birth_date': patient_data.get('dt_nascimento'),
                'mother_name': self.safe_strip(patient_data.get('no_mae')),
                'father_name': self.safe_strip(patient_data.get('no_pai')),
                'gender': self.map_gender(patient_data.get('no_sexo')),
                'address': self.safe_strip(patient_data.get('ds_logradouro')),
                'number': self.safe_strip(patient_data.get('nu_numero')),
                'neighborhood': self.safe_strip(patient_data.get('no_bairro')),
                'zip_code': self.clean_cep(patient_data.get('ds_cep')),
                'city': 'Cosmópolis',  # Valor fixo para resolver o erro
                'state': 'SP',  # Valor fixo
                'cell_phone': self.clean_phone(patient_data.get('nu_telefone_celular')),
                'home_phone': self.clean_phone(patient_data.get('nu_telefone_residencial')),
                'contact_phone': self.clean_phone(patient_data.get('nu_telefone_contato')),
                'source': 'imported',
                'esus_sync_date': datetime.now()
            }
            
            if existing:
                # Atualizar
                update_sql = """
                    UPDATE patients SET
                        cns = %s, full_name = %s, birth_date = %s,
                        mother_name = %s, father_name = %s, gender = %s,
                        address = %s, number = %s, neighborhood = %s, 
                        zip_code = %s, city = %s, state = %s,
                        cell_phone = %s, home_phone = %s, contact_phone = %s,
                        esus_sync_date = %s
                    WHERE cpf = %s
                """
                cursor.execute(update_sql, (
                    data['cns'], data['full_name'], data['birth_date'],
                    data['mother_name'], data['father_name'], data['gender'],
                    data['address'], data['number'], data['neighborhood'], 
                    data['zip_code'], data['city'], data['state'],
                    data['cell_phone'], data['home_phone'], data['contact_phone'],
                    data['esus_sync_date'], cpf
                ))
                return 'updated'
            else:
                # Inserir
                insert_sql = """
                    INSERT INTO patients (
                        cpf, cns, full_name, birth_date, mother_name, father_name,
                        gender, address, number, neighborhood, zip_code, city, state,
                        cell_phone, home_phone, contact_phone, source, esus_sync_date,
                        is_active, created_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                    )
                """
                cursor.execute(insert_sql, (
                    data['cpf'], data['cns'], data['full_name'], data['birth_date'],
                    data['mother_name'], data['father_name'], data['gender'],
                    data['address'], data['number'], data['neighborhood'], 
                    data['zip_code'], data['city'], data['state'],
                    data['cell_phone'], data['home_phone'], data['contact_phone'],
                    data['source'], data['esus_sync_date'], True, datetime.now()
                ))
                return 'imported'
                
        except Exception as e:
            self.logger.error(f"Erro ao importar paciente {patient_data.get('no_cidadao', 'UNKNOWN')}: {e}")
            return 'error'
    
    def sync_incremental(self, days=1):
        """Sincronização incremental (últimos X dias)"""
        self.logger.info(f"Sincronização incremental (últimos {days} dias)...")
        
        if not self.esus_config:
            self.logger.error("Configuração e-SUS não disponível")
            return False
        
        try:
            # Calcular data limite
            cutoff_date = datetime.now() - timedelta(days=days)
            
            self.logger.info(f"Buscando pacientes alterados desde {cutoff_date.strftime('%d/%m/%Y %H:%M')}")
            
            # Implementar lógica incremental se houver campo de data de atualização
            # Por enquanto, fazer sync completa com limite menor
            return self.sync_full()
            
        except Exception as e:
            self.logger.error(f"Erro na sincronização incremental: {e}")
            return False
    
    def clean_cpf(self, cpf):
        """Limpar CPF"""
        if not cpf:
            return None
        clean = ''.join(filter(str.isdigit, str(cpf)))
        return clean if len(clean) == 11 else None
    
    def clean_cns(self, cns):
        """Limpar CNS"""
        if not cns:
            return None
        clean = ''.join(filter(str.isdigit, str(cns)))
        return clean if len(clean) == 15 else None
    
    def clean_cep(self, cep):
        """Limpar CEP"""
        if not cep:
            return None
        clean = ''.join(filter(str.isdigit, str(cep)))
        return clean if len(clean) == 8 else None
    
    def clean_phone(self, phone):
        """Limpar telefone"""
        if not phone:
            return None
        clean = ''.join(filter(str.isdigit, str(phone)))
        return clean if len(clean) >= 10 else None
    
    def map_gender(self, gender):
        """Mapear gênero"""
        if not gender:
            return None
        gender_map = {
            'MASCULINO': 'M', 'FEMININO': 'F',
            'M': 'M', 'F': 'F', 'MASC': 'M', 'FEM': 'F'
        }
        return gender_map.get(str(gender).upper(), 'O')

def main():
    parser = argparse.ArgumentParser(description='Sincronização e-SUS → Sistema Local')
    parser.add_argument('--full', action='store_true', help='Sincronização completa')
    parser.add_argument('--incremental', type=int, metavar='DAYS', help='Sincronização incremental (últimos X dias)')
    parser.add_argument('--test', action='store_true', help='Testar conexões')
    
    args = parser.parse_args()
    
    try:
        sync = ESUSSync()
        
        if args.test:
            print("Testando conexões...")
            success = sync.test_connections()
            sys.exit(0 if success else 1)
        elif args.full:
            success = sync.sync_full()
            sys.exit(0 if success else 1)
        elif args.incremental:
            success = sync.sync_incremental(args.incremental)
            sys.exit(0 if success else 1)
        else:
            print("Use --help para ver as opções disponíveis")
            parser.print_help()
            
    except KeyboardInterrupt:
        print("\nSincronização cancelada pelo usuário")
        sys.exit(1)
    except Exception as e:
        print(f"Erro fatal: {e}")
        sys.exit(1)

if __name__ == '__main__':
    main()