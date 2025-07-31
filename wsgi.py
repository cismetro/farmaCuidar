"""
FarmaCuidar - Cosmópolis
WSGI Entry Point para Produção
"""

import os
from app import create_app

# Criar aplicação
application = create_app()

if __name__ == "__main__":
    # Para desenvolvimento apenas
    application.run(debug=False)