import os
from flask import Flask, send_from_directory
from flask_cors import CORS
from dotenv import load_dotenv
from datetime import timedelta

load_dotenv()

app = Flask(__name__, static_folder='static')
app.secret_key = os.environ.get('SECRET_KEY', 'fth-secret-2026')
app.permanent_session_lifetime = timedelta(hours=8)
app.config['SESSION_COOKIE_SECURE'] = True
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'None'

CORS(app, supports_credentials=True)

from routes.auth import auth_bp
from routes.api import api_bp
app.register_blueprint(auth_bp)
app.register_blueprint(api_bp)

@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def serve(path):
    if path and os.path.exists(os.path.join(app.static_folder, path)):
        return send_from_directory(app.static_folder, path)
    return send_from_directory(app.static_folder, 'index.html')

@app.route('/health')
def health():
    return {'status': 'ok', 'service': 'FT Harlesden API'}

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
