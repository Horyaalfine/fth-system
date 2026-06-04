from flask import Blueprint, request, jsonify, session
from werkzeug.security import check_password_hash
from models.db import get_conn
from datetime import datetime

auth_bp = Blueprint('auth', __name__)

def log_action(action, table=None, record_id=None):
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO audit_log (user_id, user_name, branch_id, action, table_name, record_id, ip_address)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (
            session.get('user_id'),
            session.get('user_name'),
            session.get('branch_id'),
            action, table, str(record_id) if record_id else None,
            request.remote_addr
        ))
        conn.commit()
        cur.close()
        conn.close()
    except Exception:
        pass

@auth_bp.route('/api/login', methods=['POST'])
def login():
    data = request.json
    email = data.get('email','').strip().lower()
    password = data.get('password','')

    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE LOWER(email)=%s AND status='active'", (email,))
    user = cur.fetchone()
    cur.close()
    conn.close()

    if not user or not check_password_hash(user['password_hash'], password):
        return jsonify({'error': 'Invalid email or password'}), 401

    # Update last login
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("UPDATE users SET last_login=%s WHERE id=%s", (datetime.now(), user['id']))
    conn.commit()
    cur.close()
    conn.close()

    session.permanent = True
    session['user_id']   = user['id']
    session['user_name'] = user['name']
    session['role']      = user['role']
    session['branch_id'] = user['branch_id']

    log_action('login', 'users', user['id'])

    return jsonify({
        'id':        user['id'],
        'name':      user['name'],
        'email':     user['email'],
        'role':      user['role'],
        'branch_id': user['branch_id'],
    })

@auth_bp.route('/api/parent-login', methods=['POST'])
def parent_login():
    data = request.json
    email    = data.get('email','').strip().lower()
    password = data.get('password','')

    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM parent_users WHERE LOWER(email)=%s AND status='active'", (email,))
    pu = cur.fetchone()
    if pu:
        cur.execute("SELECT student_id FROM parent_students WHERE parent_id=%s", (pu['id'],))
        pu = dict(pu)
        pu['student_ids'] = [r['student_id'] for r in cur.fetchall()]
    cur.close()
    conn.close()

    if not pu or not check_password_hash(pu['password_hash'], password):
        return jsonify({'error': 'Invalid parent email or password'}), 401

    session['parent_id']   = pu['id']
    session['parent_name'] = pu['name']

    return jsonify({
        'id':          pu['id'],
        'name':        pu['name'],
        'email':       pu['email'],
        'student_ids': pu['student_ids'],
    })

@auth_bp.route('/api/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({'ok': True})

@auth_bp.route('/api/me', methods=['GET'])
def me():
    if 'user_id' in session:
        return jsonify({'type':'staff', 'user_id': session['user_id'], 'role': session['role'], 'branch_id': session['branch_id']})
    if 'parent_id' in session:
        return jsonify({'type':'parent', 'parent_id': session['parent_id']})
    return jsonify({'type': None}), 401
