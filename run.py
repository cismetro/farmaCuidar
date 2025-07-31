from waitress import serve
from app import create_app
import os

app = create_app()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5454))
    host = os.environ.get('HOST', '0.0.0.0')
    
    if os.environ.get('FLASK_ENV') == 'development':
        print(f"🚀 Servidor de desenvolvimento rodando em http://{host}:{port}")
        app.run(host=host, port=port, debug=True)
    else:
        print(f"🏭 Servidor de produção (Waitress) rodando em http://{host}:{port}")
        serve(app, host=host, port=port, threads=6)