# 🏥 FarmaCuidar - Cosmópolis

Sistema de Gestão Farmacêutica Municipal desenvolvido para a Prefeitura de Cosmópolis - SP.

![Python](https://img.shields.io/badge/Python-3.8+-blue.svg)
![Flask](https://img.shields.io/badge/Flask-2.3+-green.svg)
![MySQL](https://img.shields.io/badge/MySQL-8.0+-orange.svg)
![Bootstrap](https://img.shields.io/badge/Bootstrap-5.3+-purple.svg)
![License](https://img.shields.io/badge/License-MIT-yellow.svg)

## 📋 Índice

- [Sobre o Projeto](#sobre-o-projeto)
- [Funcionalidades](#funcionalidades)
- [Tecnologias](#tecnologias)
- [Instalação](#instalação)
- [Configuração](#configuração)
- [Uso](#uso)
- [Estrutura do Projeto](#estrutura-do-projeto)
- [API](#api)
- [Contribuição](#contribuição)
- [Licença](#licença)
- [Suporte](#suporte)

## 🎯 Sobre o Projeto

O **FarmaCuidar** é um sistema completo de gestão farmacêutica desenvolvido especificamente para farmácias municipais. O sistema oferece controle total sobre o inventário de medicamentos, dispensação, processos de alto custo e relatórios gerenciais.

### Objetivos

- ✅ Digitalizar e modernizar o controle farmacêutico municipal
- ✅ Garantir rastreabilidade completa dos medicamentos
- ✅ Agilizar o processo de dispensação
- ✅ Facilitar o controle de medicamentos de alto custo
- ✅ Gerar relatórios para gestão e auditoria
- ✅ Melhorar o atendimento aos munícipes

## 🚀 Funcionalidades

### 📊 Dashboard Inteligente
- Visão geral das operações diárias
- Alertas de estoque baixo e validade
- Estatísticas em tempo real
- Gráficos e indicadores de performance

### 👥 Gestão de Pacientes
- Cadastro completo com CPF e CNS
- Histórico de dispensações
- Busca rápida e eficiente
- Validação de dados em tempo real

### 💊 Controle de Inventário
- Cadastro de medicamentos com classificação
- Controle de lotes e validades
- Alertas automáticos de estoque baixo
- Movimentação de estoque rastreável

### 🏥 Dispensação Inteligente
- Processo guiado de dispensação
- Verificação automática de estoque
- Controle de medicamentos controlados
- Impressão de comprovantes

### ⭐ Medicamentos Alto Custo
- Fluxo completo de solicitação
- Avaliação farmacêutica
- Processo de aprovação hierárquico
- Upload de documentos obrigatórios
- Acompanhamento de pacientes

### 📈 Relatórios Gerenciais
- Relatórios de consumo
- Controle financeiro
- Relatórios de validade
- Auditoria de ações
- Exportação em PDF/Excel

### 🔐 Segurança e Auditoria
- Controle de acesso por perfis
- Log completo de ações
- Backup automático
- Validação de dados

## 🛠 Tecnologias

### Backend
- **Python 3.8+** - Linguagem principal
- **Flask 2.3+** - Framework web
- **SQLAlchemy** - ORM para banco de dados
- **Flask-Login** - Autenticação de usuários
- **Flask-WTF** - Formulários e validação
- **Waitress** - Servidor WSGI para produção

### Frontend
- **HTML5** - Estrutura das páginas
- **CSS3** - Estilização personalizada
- **JavaScript** - Interatividade
- **Bootstrap 5.3** - Framework CSS responsivo
- **Font Awesome** - Ícones
- **jQuery** - Manipulação DOM

### Banco de Dados
- **MySQL 8.0+** - Banco principal
- **Alembic** - Migrações (futuro)

### Infraestrutura
- **Docker** - Containerização (opcional)
- **Nginx** - Proxy reverso (produção)
- **Linux** - Sistema operacional recomendado

## 📦 Instalação

### Pré-requisitos

```bash
# Python 3.8 ou superior
python --version

# MySQL 8.0 ou superior
mysql --version

# Git
git --version