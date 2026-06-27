import datetime
import os
from decimal import Decimal
from flask import Flask, send_from_directory, request
from flask.json.provider import DefaultJSONProvider
from flask_cors import CORS
from dotenv import load_dotenv
from datetime import timedelta

load_dotenv()

class DecimalJSONProvider(DefaultJSONProvider):
    """Convert Decimal and date/datetime (from PostgreSQL) to JSON-safe types."""
    @staticmethod
    def default(obj):
        if isinstance(obj, Decimal):
            return float(obj)
        if isinstance(obj, (datetime.date, datetime.datetime)):
            return obj.isoformat()
        return DefaultJSONProvider.default(obj)

app = Flask(__name__, static_folder='static')
app.json = DecimalJSONProvider(app)
app.secret_key = os.environ.get('SECRET_KEY', 'fth-secret-2026')
app.permanent_session_lifetime = timedelta(hours=8)
app.config['SESSION_COOKIE_SECURE'] = False
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

CORS(app, supports_credentials=True, origins='*')

from flask import session, jsonify

@app.before_request
def block_readonly_writes():
    """Reports-viewer role: read-only access. Block any non-GET request to
    /api/ endpoints, except auth routes (login/logout) which must always work."""
    if request.path.startswith('/api/') and request.method != 'GET':
        if request.path in ('/api/login', '/api/logout', '/api/parent-login'):
            return
        if session.get('role') == 'reports_viewer':
            return jsonify({'error': 'Your account has read-only access to reports.'}), 403

from routes.auth import auth_bp
from routes.api import api_bp
app.register_blueprint(auth_bp)
app.register_blueprint(api_bp)

@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def serve(path):
    if path and os.path.exists(os.path.join(app.static_folder, path)) and path != 'index.html':
        return send_from_directory(app.static_folder, path)
    response = send_from_directory(app.static_folder, 'index.html')
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response

@app.route('/health')
def health():
    return {'status': 'ok', 'service': 'FTApp API'}

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
