from flask import Blueprint, request, jsonify, session
from werkzeug.security import generate_password_hash
from models.db import get_conn
from functools import wraps
from datetime import date

api_bp = Blueprint('api', __name__)

# ── AUTH GUARDS ──
def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return jsonify({'error': 'Not authenticated'}), 401
        return f(*args, **kwargs)
    return decorated

def require_roles(*roles):
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if 'user_id' not in session:
                return jsonify({'error': 'Not authenticated'}), 401
            if session.get('role') not in roles:
                return jsonify({'error': 'Insufficient permissions'}), 403
            return f(*args, **kwargs)
        return decorated
    return decorator

def require_parent(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'parent_id' not in session:
            return jsonify({'error': 'Not authenticated'}), 401
        return f(*args, **kwargs)
    return decorated

def branch_scope():
    """Return branch_id filter: None if super_admin viewing all, else branch_id."""
    role = session.get('role')
    if role == 'super_admin':
        # Super admin can filter by query param or see all
        b = request.args.get('branch_id')
        return int(b) if b else None
    return session.get('branch_id')

def log_action(action, table=None, record_id=None):
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO audit_log (user_id, user_name, branch_id, action, table_name, record_id, ip_address)
            VALUES (%s,%s,%s,%s,%s,%s,%s)
        """, (session.get('user_id'), session.get('user_name'), session.get('branch_id'),
              action, table, str(record_id) if record_id else None, request.remote_addr))
        conn.commit()
        cur.close()
        conn.close()
    except Exception:
        pass

# ── HELPERS ──
def rows(cur): return [dict(r) for r in cur.fetchall()]
def row(cur):  r = cur.fetchone(); return dict(r) if r else None

def next_admission_id(conn, branch_id):
    cur = conn.cursor()
    cur.execute("SELECT prefix FROM branches WHERE id=%s", (branch_id,))
    b = cur.fetchone()
    if not b:
        cur.close()
        return '?'
    prefix = b['prefix']
    cur.execute("""
        SELECT MAX(CAST(REGEXP_REPLACE(admission_id, '[^0-9]', '', 'g') AS INT))
        FROM students WHERE branch_id=%s AND admission_id ~ '^[A-Z][0-9]+'
    """, (branch_id,))
    r = cur.fetchone()
    cur.close()
    max_num = r[0] if r and r[0] else 99
    return f"{prefix}{max_num + 1}"

# ════════════════════════════════════════════
#  BRANCHES
# ════════════════════════════════════════════
@api_bp.route('/api/branches', methods=['GET'])
@require_auth
def get_branches():
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT * FROM branches ORDER BY name")
    data = rows(cur); cur.close(); conn.close()
    return jsonify(data)

@api_bp.route('/api/branches', methods=['POST'])
@require_roles('super_admin')
def add_branch():
    d = request.json
    conn = get_conn(); cur = conn.cursor()
    cur.execute("""
        INSERT INTO branches (name, prefix, address, phone, email, status)
        VALUES (%s,%s,%s,%s,%s,%s) RETURNING *
    """, (d['name'], d['prefix'].upper(), d.get('address',''), d.get('phone',''), d.get('email',''), d.get('status','active')))
    r = row(cur); conn.commit(); cur.close(); conn.close()
    log_action('add', 'branches', r['id'])
    return jsonify(r), 201

@api_bp.route('/api/branches/<int:bid>', methods=['PUT'])
@require_roles('super_admin')
def update_branch(bid):
    d = request.json
    conn = get_conn(); cur = conn.cursor()
    cur.execute("""
        UPDATE branches SET name=%s, prefix=%s, address=%s, phone=%s, email=%s, status=%s
        WHERE id=%s RETURNING *
    """, (d['name'], d['prefix'].upper(), d.get('address',''), d.get('phone',''), d.get('email',''), d.get('status','active'), bid))
    r = row(cur); conn.commit(); cur.close(); conn.close()
    log_action('edit', 'branches', bid)
    return jsonify(r)

@api_bp.route('/api/branches/<int:bid>', methods=['DELETE'])
@require_roles('super_admin')
def delete_branch(bid):
    conn = get_conn(); cur = conn.cursor()
    cur.execute("DELETE FROM branches WHERE id=%s", (bid,))
    conn.commit(); cur.close(); conn.close()
    log_action('delete', 'branches', bid)
    return jsonify({'ok': True})

# ════════════════════════════════════════════
#  STUDENTS
# ════════════════════════════════════════════
@api_bp.route('/api/students', methods=['GET'])
@require_auth
def get_students():
    conn = get_conn(); cur = conn.cursor()
    b = branch_scope()
    q = request.args.get('q','')
    if b:
        if q:
            cur.execute("""SELECT s.*, b.name as branch_name FROM students s
                JOIN branches b ON b.id=s.branch_id
                WHERE s.branch_id=%s AND (s.name ILIKE %s OR s.admission_id ILIKE %s)
                ORDER BY s.admission_id""", (b, f'%{q}%', f'%{q}%'))
        else:
            cur.execute("""SELECT s.*, b.name as branch_name FROM students s
                JOIN branches b ON b.id=s.branch_id
                WHERE s.branch_id=%s ORDER BY s.admission_id""", (b,))
    else:
        if q:
            cur.execute("""SELECT s.*, b.name as branch_name FROM students s
                JOIN branches b ON b.id=s.branch_id
                WHERE s.name ILIKE %s OR s.admission_id ILIKE %s
                ORDER BY s.admission_id""", (f'%{q}%', f'%{q}%'))
        else:
            cur.execute("""SELECT s.*, b.name as branch_name FROM students s
                JOIN branches b ON b.id=s.branch_id ORDER BY s.admission_id""")
    data = rows(cur); cur.close(); conn.close()
    return jsonify(data)

@api_bp.route('/api/students/next-id/<int:branch_id>', methods=['GET'])
@require_auth
def get_next_id(branch_id):
    conn = get_conn()
    nid = next_admission_id(conn, branch_id)
    conn.close()
    return jsonify({'next_id': nid})

@api_bp.route('/api/students', methods=['POST'])
@require_auth
def add_student():
    d = request.json
    conn = get_conn(); cur = conn.cursor()
    cur.execute("""
        INSERT INTO students (branch_id, admission_id, name, year_group, parent_contact, status, notes)
        VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING *
    """, (d['branch_id'], d['admission_id'], d['name'], d.get('year_group',''), d.get('parent_contact',''), d.get('status','active'), d.get('notes','')))
    r = row(cur); conn.commit(); cur.close(); conn.close()
    log_action('add', 'students', r['id'])
    return jsonify(r), 201

@api_bp.route('/api/students/<int:sid>', methods=['PUT'])
@require_auth
def update_student(sid):
    d = request.json
    conn = get_conn(); cur = conn.cursor()
    cur.execute("""
        UPDATE students SET branch_id=%s, admission_id=%s, name=%s, year_group=%s,
        parent_contact=%s, status=%s, notes=%s WHERE id=%s RETURNING *
    """, (d['branch_id'], d['admission_id'], d['name'], d.get('year_group',''),
          d.get('parent_contact',''), d.get('status','active'), d.get('notes',''), sid))
    r = row(cur); conn.commit(); cur.close(); conn.close()
    log_action('edit', 'students', sid)
    return jsonify(r)

@api_bp.route('/api/students/<int:sid>', methods=['DELETE'])
@require_auth
def delete_student(sid):
    conn = get_conn(); cur = conn.cursor()
    cur.execute("DELETE FROM students WHERE id=%s", (sid,))
    conn.commit(); cur.close(); conn.close()
    log_action('delete', 'students', sid)
    return jsonify({'ok': True})

# ════════════════════════════════════════════
#  STAFF
# ════════════════════════════════════════════
@api_bp.route('/api/staff', methods=['GET'])
@require_auth
def get_staff():
    conn = get_conn(); cur = conn.cursor()
    b = branch_scope()
    if b:
        cur.execute("""SELECT s.*, b.name as branch_name FROM staff s
            JOIN branches b ON b.id=s.branch_id WHERE s.branch_id=%s ORDER BY s.name""", (b,))
    else:
        cur.execute("""SELECT s.*, b.name as branch_name FROM staff s
            JOIN branches b ON b.id=s.branch_id ORDER BY s.name""")
    data = rows(cur); cur.close(); conn.close()
    return jsonify(data)

@api_bp.route('/api/staff', methods=['POST'])
@require_auth
def add_staff():
    d = request.json
    conn = get_conn(); cur = conn.cursor()
    cur.execute("""
        INSERT INTO staff (branch_id, name, role, subject, contact, status)
        VALUES (%s,%s,%s,%s,%s,%s) RETURNING *
    """, (d['branch_id'], d['name'], d.get('role','teacher'), d.get('subject',''), d.get('contact',''), d.get('status','active')))
    r = row(cur); conn.commit(); cur.close(); conn.close()
    log_action('add', 'staff', r['id'])
    return jsonify(r), 201

@api_bp.route('/api/staff/<int:sid>', methods=['PUT'])
@require_auth
def update_staff(sid):
    d = request.json
    conn = get_conn(); cur = conn.cursor()
    cur.execute("""
        UPDATE staff SET branch_id=%s, name=%s, role=%s, subject=%s, contact=%s, status=%s
        WHERE id=%s RETURNING *
    """, (d['branch_id'], d['name'], d.get('role','teacher'), d.get('subject',''), d.get('contact',''), d.get('status','active'), sid))
    r = row(cur); conn.commit(); cur.close(); conn.close()
    log_action('edit', 'staff', sid)
    return jsonify(r)

@api_bp.route('/api/staff/<int:sid>', methods=['DELETE'])
@require_auth
def delete_staff(sid):
    conn = get_conn(); cur = conn.cursor()
    cur.execute("DELETE FROM staff WHERE id=%s", (sid,))
    conn.commit(); cur.close(); conn.close()
    log_action('delete', 'staff', sid)
    return jsonify({'ok': True})

# ════════════════════════════════════════════
#  SESSIONS
# ════════════════════════════════════════════
@api_bp.route('/api/sessions', methods=['GET'])
@require_auth
def get_sessions():
    conn = get_conn(); cur = conn.cursor()
    b = branch_scope()
    if b:
        cur.execute("""SELECT ss.*, b.name as branch_name, st.name as staff_name,
            (SELECT COUNT(*) FROM attendance a WHERE a.session_id=ss.id AND a.status='present') as present_count,
            (SELECT COUNT(*) FROM attendance a WHERE a.session_id=ss.id) as total_count
            FROM sessions ss JOIN branches b ON b.id=ss.branch_id
            LEFT JOIN staff st ON st.id=ss.staff_id
            WHERE ss.branch_id=%s ORDER BY ss.date DESC, ss.slot""", (b,))
    else:
        cur.execute("""SELECT ss.*, b.name as branch_name, st.name as staff_name,
            (SELECT COUNT(*) FROM attendance a WHERE a.session_id=ss.id AND a.status='present') as present_count,
            (SELECT COUNT(*) FROM attendance a WHERE a.session_id=ss.id) as total_count
            FROM sessions ss JOIN branches b ON b.id=ss.branch_id
            LEFT JOIN staff st ON st.id=ss.staff_id
            ORDER BY ss.date DESC, ss.slot""")
    data = rows(cur); cur.close(); conn.close()
    # Convert date objects to strings
    for d in data:
        if d.get('date'): d['date'] = str(d['date'])
    return jsonify(data)

@api_bp.route('/api/sessions', methods=['POST'])
@require_auth
def add_session():
    d = request.json
    conn = get_conn(); cur = conn.cursor()
    cur.execute("""
        INSERT INTO sessions (branch_id, staff_id, date, slot, subject, table_no)
        VALUES (%s,%s,%s,%s,%s,%s) RETURNING *
    """, (d['branch_id'], d.get('staff_id'), d['date'], d['slot'], d.get('subject',''), d.get('table_no',1)))
    r = row(cur); conn.commit(); cur.close(); conn.close()
    if r: r['date'] = str(r['date'])
    log_action('add', 'sessions', r['id'])
    return jsonify(r), 201

@api_bp.route('/api/sessions/<int:sid>', methods=['DELETE'])
@require_auth
def delete_session(sid):
    conn = get_conn(); cur = conn.cursor()
    cur.execute("DELETE FROM sessions WHERE id=%s", (sid,))
    conn.commit(); cur.close(); conn.close()
    log_action('delete', 'sessions', sid)
    return jsonify({'ok': True})

# ════════════════════════════════════════════
#  ATTENDANCE
# ════════════════════════════════════════════
@api_bp.route('/api/attendance/<int:session_id>', methods=['GET'])
@require_auth
def get_attendance(session_id):
    conn = get_conn(); cur = conn.cursor()
    cur.execute("""
        SELECT a.*, s.name as student_name, s.admission_id
        FROM attendance a JOIN students s ON s.id=a.student_id
        WHERE a.session_id=%s ORDER BY s.admission_id
    """, (session_id,))
    data = rows(cur); cur.close(); conn.close()
    return jsonify(data)

@api_bp.route('/api/attendance', methods=['POST'])
@require_auth
def save_attendance():
    """Bulk upsert attendance for a session."""
    d = request.json
    session_id = d['session_id']
    records    = d['records']  # [{student_id, status, notes}]
    conn = get_conn(); cur = conn.cursor()
    for rec in records:
        cur.execute("""
            INSERT INTO attendance (session_id, student_id, status, notes)
            VALUES (%s,%s,%s,%s)
            ON CONFLICT (session_id, student_id)
            DO UPDATE SET status=EXCLUDED.status, notes=EXCLUDED.notes
        """, (session_id, rec['student_id'], rec['status'], rec.get('notes','')))
    conn.commit(); cur.close(); conn.close()
    log_action('edit', 'attendance', session_id)
    return jsonify({'ok': True})

# ════════════════════════════════════════════
#  INVOICES
# ════════════════════════════════════════════
@api_bp.route('/api/invoices', methods=['GET'])
@require_auth
def get_invoices():
    conn = get_conn(); cur = conn.cursor()
    b = branch_scope()
    status = request.args.get('status')
    params = []
    where  = []
    if b:    where.append("i.branch_id=%s"); params.append(b)
    if status and status != 'all': where.append("i.status=%s"); params.append(status)
    wc = ('WHERE ' + ' AND '.join(where)) if where else ''
    cur.execute(f"""
        SELECT i.*, s.name as student_name, s.admission_id, b.name as branch_name
        FROM invoices i JOIN students s ON s.id=i.student_id
        JOIN branches b ON b.id=i.branch_id
        {wc} ORDER BY i.issued DESC
    """, params)
    data = rows(cur)
    for d in data:
        if d.get('issued'):    d['issued']    = str(d['issued'])
        if d.get('paid_date'): d['paid_date'] = str(d['paid_date'])
    cur.close(); conn.close()
    return jsonify(data)

@api_bp.route('/api/invoices/generate', methods=['POST'])
@require_auth
def generate_invoices():
    """Generate due invoices for all active students in scope for given month."""
    d = request.json
    month = d.get('month', date.today().strftime('%Y-%m'))
    b = branch_scope()
    conn = get_conn(); cur = conn.cursor()
    if b:
        cur.execute("SELECT id, branch_id FROM students WHERE branch_id=%s AND status='active'", (b,))
    else:
        cur.execute("SELECT id, branch_id FROM students WHERE status='active'")
    sts = rows(cur)
    added = 0
    for st in sts:
        cur.execute("""
            INSERT INTO invoices (student_id, branch_id, month, amount, status, issued)
            VALUES (%s,%s,%s,120,'due', CURRENT_DATE)
            ON CONFLICT (student_id, month) DO NOTHING
        """, (st['id'], st['branch_id'], month))
        if cur.rowcount: added += 1
    conn.commit(); cur.close(); conn.close()
    log_action('add', 'invoices', 'batch')
    return jsonify({'added': added})

@api_bp.route('/api/invoices/<int:iid>', methods=['PUT'])
@require_auth
def update_invoice(iid):
    d = request.json
    conn = get_conn(); cur = conn.cursor()
    cur.execute("""
        UPDATE invoices SET amount=%s, status=%s, paid_date=%s, notes=%s
        WHERE id=%s RETURNING *
    """, (d.get('amount',120), d['status'], d.get('paid_date') or None, d.get('notes',''), iid))
    r = row(cur); conn.commit(); cur.close(); conn.close()
    if r:
        if r.get('issued'):    r['issued']    = str(r['issued'])
        if r.get('paid_date'): r['paid_date'] = str(r['paid_date'])
    log_action('edit', 'invoices', iid)
    return jsonify(r)

@api_bp.route('/api/invoices/<int:iid>/mark-paid', methods=['POST'])
@require_auth
def mark_invoice_paid(iid):
    conn = get_conn(); cur = conn.cursor()
    cur.execute("""
        UPDATE invoices SET status='paid', paid_date=CURRENT_DATE WHERE id=%s RETURNING *
    """, (iid,))
    r = row(cur); conn.commit(); cur.close(); conn.close()
    log_action('edit', 'invoices', iid)
    return jsonify({'ok': True})

# ════════════════════════════════════════════
#  PROGRESS NOTES
# ════════════════════════════════════════════
@api_bp.route('/api/progress', methods=['GET'])
@require_auth
def get_progress():
    conn = get_conn(); cur = conn.cursor()
    session_id  = request.args.get('session_id')
    student_id  = request.args.get('student_id')
    where = []; params = []
    if session_id: where.append("p.session_id=%s"); params.append(int(session_id))
    if student_id: where.append("p.student_id=%s"); params.append(int(student_id))
    wc = ('WHERE '+' AND '.join(where)) if where else ''
    cur.execute(f"""
        SELECT p.*, s.name as student_name, s.admission_id, st.name as staff_name
        FROM progress p JOIN students s ON s.id=p.student_id
        LEFT JOIN staff st ON st.id=p.staff_id
        {wc} ORDER BY p.date DESC
    """, params)
    data = rows(cur)
    for d in data:
        if d.get('date'): d['date'] = str(d['date'])
    cur.close(); conn.close()
    return jsonify(data)

@api_bp.route('/api/progress', methods=['POST'])
@require_auth
def add_progress():
    d = request.json
    conn = get_conn(); cur = conn.cursor()
    cur.execute("""
        INSERT INTO progress (student_id, session_id, staff_id, subject, rating, comment, date)
        VALUES (%s,%s,%s,%s,%s,%s, CURRENT_DATE) RETURNING *
    """, (d['student_id'], d.get('session_id'), d.get('staff_id', session.get('user_id')),
          d.get('subject',''), d.get('rating',4), d.get('comment','')))
    r = row(cur); conn.commit(); cur.close(); conn.close()
    if r and r.get('date'): r['date'] = str(r['date'])
    log_action('add', 'progress', r['id'])
    return jsonify(r), 201

# ════════════════════════════════════════════
#  USERS
# ════════════════════════════════════════════
@api_bp.route('/api/users', methods=['GET'])
@require_roles('super_admin','branch_manager')
def get_users():
    conn = get_conn(); cur = conn.cursor()
    cur.execute("""
        SELECT u.id, u.name, u.email, u.role, u.branch_id, u.status, u.last_login,
               b.name as branch_name
        FROM users u LEFT JOIN branches b ON b.id=u.branch_id ORDER BY u.name
    """)
    data = rows(cur)
    for d in data:
        if d.get('last_login'): d['last_login'] = str(d['last_login'])
    cur.close(); conn.close()
    return jsonify(data)

@api_bp.route('/api/users', methods=['POST'])
@require_roles('super_admin')
def add_user():
    d = request.json
    conn = get_conn(); cur = conn.cursor()
    cur.execute("""
        INSERT INTO users (branch_id, name, email, password_hash, role, status)
        VALUES (%s,%s,%s,%s,%s,%s) RETURNING id, name, email, role, branch_id, status
    """, (d.get('branch_id'), d['name'], d['email'],
          generate_password_hash(d['password']), d['role'], d.get('status','active')))
    r = row(cur); conn.commit(); cur.close(); conn.close()
    log_action('add', 'users', r['id'])
    return jsonify(r), 201

@api_bp.route('/api/users/<int:uid>', methods=['PUT'])
@require_roles('super_admin')
def update_user(uid):
    d = request.json
    conn = get_conn(); cur = conn.cursor()
    if d.get('password'):
        cur.execute("""UPDATE users SET name=%s, email=%s, role=%s, branch_id=%s, status=%s, password_hash=%s
            WHERE id=%s RETURNING id, name, email, role, branch_id, status""",
            (d['name'], d['email'], d['role'], d.get('branch_id'), d.get('status','active'),
             generate_password_hash(d['password']), uid))
    else:
        cur.execute("""UPDATE users SET name=%s, email=%s, role=%s, branch_id=%s, status=%s
            WHERE id=%s RETURNING id, name, email, role, branch_id, status""",
            (d['name'], d['email'], d['role'], d.get('branch_id'), d.get('status','active'), uid))
    r = row(cur); conn.commit(); cur.close(); conn.close()
    log_action('edit', 'users', uid)
    return jsonify(r)

@api_bp.route('/api/users/<int:uid>', methods=['DELETE'])
@require_roles('super_admin')
def delete_user(uid):
    conn = get_conn(); cur = conn.cursor()
    cur.execute("DELETE FROM users WHERE id=%s", (uid,))
    conn.commit(); cur.close(); conn.close()
    log_action('delete', 'users', uid)
    return jsonify({'ok': True})

# ════════════════════════════════════════════
#  PARENT USERS (admin management)
# ════════════════════════════════════════════
@api_bp.route('/api/parent-users', methods=['GET'])
@require_roles('super_admin','branch_manager')
def get_parent_users():
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT * FROM parent_users ORDER BY name")
    pus = rows(cur)
    for pu in pus:
        cur.execute("SELECT student_id FROM parent_students WHERE parent_id=%s", (pu['id'],))
        pu['student_ids'] = [r['student_id'] for r in cur.fetchall()]
        del pu['password_hash']
    cur.close(); conn.close()
    return jsonify(pus)

@api_bp.route('/api/parent-users', methods=['POST'])
@require_roles('super_admin','branch_manager')
def add_parent_user():
    d = request.json
    conn = get_conn(); cur = conn.cursor()
    cur.execute("""
        INSERT INTO parent_users (name, email, password_hash, status)
        VALUES (%s,%s,%s,%s) RETURNING id, name, email, status
    """, (d['name'], d['email'], generate_password_hash(d['password']), d.get('status','active')))
    pu = row(cur)
    for stid in d.get('student_ids',[]):
        cur.execute("INSERT INTO parent_students (parent_id,student_id) VALUES (%s,%s) ON CONFLICT DO NOTHING", (pu['id'], stid))
    conn.commit(); cur.close(); conn.close()
    log_action('add', 'parent_users', pu['id'])
    return jsonify(pu), 201

@api_bp.route('/api/parent-users/<int:pid>', methods=['PUT'])
@require_roles('super_admin','branch_manager')
def update_parent_user(pid):
    d = request.json
    conn = get_conn(); cur = conn.cursor()
    if d.get('password'):
        cur.execute("UPDATE parent_users SET name=%s,email=%s,password_hash=%s,status=%s WHERE id=%s RETURNING id,name,email,status",
            (d['name'],d['email'],generate_password_hash(d['password']),d.get('status','active'),pid))
    else:
        cur.execute("UPDATE parent_users SET name=%s,email=%s,status=%s WHERE id=%s RETURNING id,name,email,status",
            (d['name'],d['email'],d.get('status','active'),pid))
    pu = row(cur)
    cur.execute("DELETE FROM parent_students WHERE parent_id=%s", (pid,))
    for stid in d.get('student_ids',[]):
        cur.execute("INSERT INTO parent_students (parent_id,student_id) VALUES (%s,%s) ON CONFLICT DO NOTHING", (pid, stid))
    conn.commit(); cur.close(); conn.close()
    log_action('edit', 'parent_users', pid)
    return jsonify(pu)

@api_bp.route('/api/parent-users/<int:pid>', methods=['DELETE'])
@require_roles('super_admin','branch_manager')
def delete_parent_user(pid):
    conn = get_conn(); cur = conn.cursor()
    cur.execute("DELETE FROM parent_users WHERE id=%s", (pid,))
    conn.commit(); cur.close(); conn.close()
    log_action('delete', 'parent_users', pid)
    return jsonify({'ok': True})

# ════════════════════════════════════════════
#  PARENT PORTAL (read-only, own children only)
# ════════════════════════════════════════════
@api_bp.route('/api/parent/children', methods=['GET'])
@require_parent
def parent_children():
    pid = session['parent_id']
    conn = get_conn(); cur = conn.cursor()
    cur.execute("""
        SELECT s.*, b.name as branch_name FROM students s
        JOIN parent_students ps ON ps.student_id=s.id
        JOIN branches b ON b.id=s.branch_id
        WHERE ps.parent_id=%s
    """, (pid,))
    data = rows(cur); cur.close(); conn.close()
    return jsonify(data)

@api_bp.route('/api/parent/attendance/<int:student_id>', methods=['GET'])
@require_parent
def parent_attendance(student_id):
    pid = session['parent_id']
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT 1 FROM parent_students WHERE parent_id=%s AND student_id=%s", (pid, student_id))
    if not cur.fetchone(): cur.close(); conn.close(); return jsonify({'error':'Forbidden'}), 403
    cur.execute("""
        SELECT a.*, sess.date, sess.slot, sess.subject, b.name as branch_name
        FROM attendance a JOIN sessions sess ON sess.id=a.session_id
        JOIN branches b ON b.id=sess.branch_id
        WHERE a.student_id=%s ORDER BY sess.date DESC LIMIT 20
    """, (student_id,))
    data = rows(cur)
    for d in data:
        if d.get('date'): d['date'] = str(d['date'])
    cur.close(); conn.close()
    return jsonify(data)

@api_bp.route('/api/parent/invoices/<int:student_id>', methods=['GET'])
@require_parent
def parent_invoices(student_id):
    pid = session['parent_id']
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT 1 FROM parent_students WHERE parent_id=%s AND student_id=%s", (pid, student_id))
    if not cur.fetchone(): cur.close(); conn.close(); return jsonify({'error':'Forbidden'}), 403
    cur.execute("SELECT * FROM invoices WHERE student_id=%s ORDER BY issued DESC", (student_id,))
    data = rows(cur)
    for d in data:
        if d.get('issued'):    d['issued']    = str(d['issued'])
        if d.get('paid_date'): d['paid_date'] = str(d['paid_date'])
    cur.close(); conn.close()
    return jsonify(data)

@api_bp.route('/api/parent/progress/<int:student_id>', methods=['GET'])
@require_parent
def parent_progress(student_id):
    pid = session['parent_id']
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT 1 FROM parent_students WHERE parent_id=%s AND student_id=%s", (pid, student_id))
    if not cur.fetchone(): cur.close(); conn.close(); return jsonify({'error':'Forbidden'}), 403
    cur.execute("""
        SELECT p.*, st.name as staff_name FROM progress p
        LEFT JOIN staff st ON st.id=p.staff_id
        WHERE p.student_id=%s ORDER BY p.date DESC
    """, (student_id,))
    data = rows(cur)
    for d in data:
        if d.get('date'): d['date'] = str(d['date'])
    cur.close(); conn.close()
    return jsonify(data)

# ════════════════════════════════════════════
#  REPORTS / ANALYTICS
# ════════════════════════════════════════════
@api_bp.route('/api/reports/summary', methods=['GET'])
@require_auth
def report_summary():
    b = branch_scope()
    conn = get_conn(); cur = conn.cursor()
    params = (b,) if b else ()
    bw = "WHERE s.branch_id=%s" if b else ""

    cur.execute(f"SELECT COUNT(*) as c FROM students s {bw} AND s.status='active'", params)
    student_count = cur.fetchone()['c']

    bw2 = "WHERE s.branch_id=%s" if b else ""
    cur.execute(f"SELECT COUNT(*) as c FROM staff s {bw2}", params)
    staff_count = cur.fetchone()['c']

    bw3 = "WHERE s.branch_id=%s" if b else ""
    cur.execute(f"SELECT COUNT(*) as c FROM sessions s {bw3}", params)
    session_count = cur.fetchone()['c']

    cur.execute("""
        SELECT COUNT(*) FILTER (WHERE a.status='present') as present,
               COUNT(*) as total
        FROM attendance a
        JOIN sessions s ON s.id=a.session_id
    """ + (f" WHERE s.branch_id=%s" if b else ""), params)
    att = cur.fetchone()
    att_rate = round(att['present'] / att['total'] * 100) if att['total'] else 0

    # Per branch stats
    cur.execute("""
        SELECT b.name, b.id,
          (SELECT COUNT(*) FROM students WHERE branch_id=b.id AND status='active') as students,
          (SELECT COUNT(*) FROM sessions WHERE branch_id=b.id) as sessions,
          (SELECT COUNT(*) FROM attendance a JOIN sessions s ON s.id=a.session_id
           WHERE s.branch_id=b.id AND a.status='present') as present,
          (SELECT COUNT(*) FROM attendance a JOIN sessions s ON s.id=a.session_id
           WHERE s.branch_id=b.id) as att_total
        FROM branches b ORDER BY b.name
    """)
    branch_stats = rows(cur)

    # Year group breakdown
    cur.execute(f"""
        SELECT year_group, COUNT(*) as c FROM students s {bw}
        GROUP BY year_group ORDER BY year_group
    """, params)
    year_groups = rows(cur)

    # Subject breakdown
    cur.execute(f"""
        SELECT subject, COUNT(*) as c FROM sessions s {bw3}
        GROUP BY subject ORDER BY c DESC
    """, params)
    subjects = rows(cur)

    # Outstanding invoices
    cur.execute("SELECT SUM(amount) as total FROM invoices WHERE status!='paid'" + (f" AND branch_id=%s" if b else ""), params)
    outstanding = cur.fetchone()['total'] or 0

    cur.close(); conn.close()
    return jsonify({
        'student_count': student_count,
        'staff_count': staff_count,
        'session_count': session_count,
        'att_rate': att_rate,
        'att_present': att['present'],
        'att_total': att['total'],
        'branch_stats': branch_stats,
        'year_groups': year_groups,
        'subjects': subjects,
        'outstanding_fees': int(outstanding),
    })

# ════════════════════════════════════════════
#  STAFF ATTENDANCE
# ════════════════════════════════════════════
@api_bp.route('/api/staff-attendance', methods=['GET'])
@require_auth
def get_staff_attendance():
    b = branch_scope()
    month = request.args.get('month')
    session_id = request.args.get('session_id')
    staff_id = request.args.get('staff_id')
    conn = get_conn(); cur = conn.cursor()
    where = []; params = []
    if b:          where.append("sa.branch_id=%s");   params.append(b)
    if month:      where.append("TO_CHAR(sa.date,'YYYY-MM')=%s"); params.append(month)
    if session_id: where.append("sa.session_id=%s");  params.append(int(session_id))
    if staff_id:   where.append("sa.staff_id=%s");    params.append(int(staff_id))
    wc = ('WHERE '+' AND '.join(where)) if where else ''
    cur.execute(f"""
        SELECT sa.*,
            st.name as staff_name, st.role as staff_role, st.subject,
            b.name as branch_name,
            sess.slot, sess.subject as session_subject,
            cf.name as cover_for_name
        FROM staff_attendance sa
        JOIN staff st ON st.id=sa.staff_id
        JOIN branches b ON b.id=sa.branch_id
        LEFT JOIN sessions sess ON sess.id=sa.session_id
        LEFT JOIN staff cf ON cf.id=sa.cover_for
        {wc}
        ORDER BY sa.date DESC, sa.sign_in
    """, params)
    data = rows(cur)
    for d in data:
        if d.get('date'):     d['date']     = str(d['date'])
        if d.get('sign_in'):  d['sign_in']  = str(d['sign_in'])
        if d.get('sign_out'): d['sign_out'] = str(d['sign_out'])
    cur.close(); conn.close()
    return jsonify(data)

@api_bp.route('/api/staff-attendance/summary', methods=['GET'])
@require_auth
def staff_attendance_summary():
    b = branch_scope()
    month = request.args.get('month', date.today().strftime('%Y-%m'))
    conn = get_conn(); cur = conn.cursor()
    bw = "AND sa.branch_id=%s" if b else ""
    params = [month] + ([b] if b else [])
    cur.execute(f"""
        SELECT
            st.id, st.name, st.role, st.subject, b.name as branch_name,
            COUNT(*) FILTER (WHERE sa.status='present') as present,
            COUNT(*) FILTER (WHERE sa.status='absent') as absent,
            COUNT(*) FILTER (WHERE sa.status='late') as late,
            COUNT(*) FILTER (WHERE sa.status='no_sign_out') as no_sign_out,
            COUNT(*) as total_sessions,
            SUM(EXTRACT(EPOCH FROM (sa.sign_out - sa.sign_in))/3600)
                FILTER (WHERE sa.sign_in IS NOT NULL AND sa.sign_out IS NOT NULL) as total_hours
        FROM staff st
        JOIN branches b ON b.id=st.branch_id
        LEFT JOIN staff_attendance sa ON sa.staff_id=st.id
            AND TO_CHAR(sa.date,'YYYY-MM')=%s {bw}
        GROUP BY st.id, st.name, st.role, st.subject, b.name
        ORDER BY st.name
    """, params)
    data = rows(cur)
    for d in data:
        if d.get('total_hours'): d['total_hours'] = round(float(d['total_hours']), 1)
    cur.close(); conn.close()
    return jsonify(data)

@api_bp.route('/api/staff-attendance', methods=['POST'])
@require_auth
def save_staff_attendance():
    d = request.json
    conn = get_conn(); cur = conn.cursor()
    cur.execute("""
        INSERT INTO staff_attendance
            (session_id, staff_id, branch_id, date, sign_in, sign_out, status, cover_for, absence_reason, notes)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (session_id, staff_id)
        DO UPDATE SET sign_in=EXCLUDED.sign_in, sign_out=EXCLUDED.sign_out,
            status=EXCLUDED.status, cover_for=EXCLUDED.cover_for,
            absence_reason=EXCLUDED.absence_reason, notes=EXCLUDED.notes
        RETURNING *
    """, (
        d.get('session_id'), d['staff_id'], d['branch_id'], d['date'],
        d.get('sign_in') or None, d.get('sign_out') or None,
        d.get('status','present'), d.get('cover_for') or None,
        d.get('absence_reason',''), d.get('notes','')
    ))
    r = row(cur); conn.commit(); cur.close(); conn.close()
    if r:
        if r.get('date'):     r['date']     = str(r['date'])
        if r.get('sign_in'):  r['sign_in']  = str(r['sign_in'])
        if r.get('sign_out'): r['sign_out'] = str(r['sign_out'])
    log_action('edit', 'staff_attendance', d.get('staff_id'))
    return jsonify(r), 201

@api_bp.route('/api/staff-attendance/<int:aid>', methods=['PUT'])
@require_auth
def update_staff_attendance(aid):
    d = request.json
    conn = get_conn(); cur = conn.cursor()
    cur.execute("""
        UPDATE staff_attendance SET sign_in=%s, sign_out=%s, status=%s,
            cover_for=%s, absence_reason=%s, notes=%s
        WHERE id=%s RETURNING *
    """, (
        d.get('sign_in') or None, d.get('sign_out') or None,
        d.get('status','present'), d.get('cover_for') or None,
        d.get('absence_reason',''), d.get('notes',''), aid
    ))
    r = row(cur); conn.commit(); cur.close(); conn.close()
    if r:
        if r.get('date'):     r['date']     = str(r['date'])
        if r.get('sign_in'):  r['sign_in']  = str(r['sign_in'])
        if r.get('sign_out'): r['sign_out'] = str(r['sign_out'])
    log_action('edit', 'staff_attendance', aid)
    return jsonify(r)

@api_bp.route('/api/staff-attendance/<int:aid>', methods=['DELETE'])
@require_auth
def delete_staff_attendance(aid):
    conn = get_conn(); cur = conn.cursor()
    cur.execute("DELETE FROM staff_attendance WHERE id=%s", (aid,))
    conn.commit(); cur.close(); conn.close()
    log_action('delete', 'staff_attendance', aid)
    return jsonify({'ok': True})

# ════════════════════════════════════════════
#  FINANCIAL — PAYMENTS
# ════════════════════════════════════════════
@api_bp.route('/api/payments', methods=['GET'])
@require_auth
def get_payments():
    b = branch_scope()
    student_id = request.args.get('student_id')
    conn = get_conn(); cur = conn.cursor()
    where = []; params = []
    if b:          where.append("p.branch_id=%s"); params.append(b)
    if student_id: where.append("p.student_id=%s"); params.append(int(student_id))
    wc = ('WHERE '+' AND '.join(where)) if where else ''
    cur.execute(f"""
        SELECT p.*, s.name as student_name, s.admission_id, b.name as branch_name,
               u.name as recorded_by_name
        FROM payments p
        JOIN students s ON s.id=p.student_id
        JOIN branches b ON b.id=p.branch_id
        LEFT JOIN users u ON u.id=p.recorded_by
        {wc} ORDER BY p.payment_date DESC, p.created_at DESC
    """, params)
    data = rows(cur)
    for d in data:
        if d.get('payment_date'): d['payment_date'] = str(d['payment_date'])
        if d.get('amount'): d['amount'] = float(d['amount'])
    cur.close(); conn.close()
    return jsonify(data)

@api_bp.route('/api/payments', methods=['POST'])
@require_auth
def add_payment():
    d = request.json
    conn = get_conn(); cur = conn.cursor()
    cur.execute("""
        INSERT INTO payments (student_id, branch_id, amount, payment_date, method, reference, notes, recorded_by)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *
    """, (d['student_id'], d['branch_id'], d['amount'],
          d.get('payment_date', str(date.today())),
          d.get('method','cash'), d.get('reference',''),
          d.get('notes',''), session.get('user_id')))
    r = row(cur); conn.commit()
    if r:
        if r.get('payment_date'): r['payment_date'] = str(r['payment_date'])
        if r.get('amount'): r['amount'] = float(r['amount'])
    cur.close(); conn.close()
    log_action('add', 'payments', r['id'])
    return jsonify(r), 201

@api_bp.route('/api/payments/<int:pid>', methods=['PUT'])
@require_auth
def update_payment(pid):
    d = request.json
    conn = get_conn(); cur = conn.cursor()
    cur.execute("""
        UPDATE payments SET amount=%s, payment_date=%s, method=%s, reference=%s, notes=%s
        WHERE id=%s RETURNING *
    """, (d['amount'], d.get('payment_date', str(date.today())),
          d.get('method','cash'), d.get('reference',''), d.get('notes',''), pid))
    r = row(cur); conn.commit()
    if r:
        if r.get('payment_date'): r['payment_date'] = str(r['payment_date'])
        if r.get('amount'): r['amount'] = float(r['amount'])
    cur.close(); conn.close()
    log_action('edit', 'payments', pid)
    return jsonify(r)

@api_bp.route('/api/payments/<int:pid>', methods=['DELETE'])
@require_auth
def delete_payment(pid):
    conn = get_conn(); cur = conn.cursor()
    cur.execute("DELETE FROM payments WHERE id=%s", (pid,))
    conn.commit(); cur.close(); conn.close()
    log_action('delete', 'payments', pid)
    return jsonify({'ok': True})

# ════════════════════════════════════════════
#  FINANCIAL — STATEMENT OF ACCOUNT
# ════════════════════════════════════════════
@api_bp.route('/api/statement/<int:student_id>', methods=['GET'])
@require_auth
def get_statement(student_id):
    conn = get_conn(); cur = conn.cursor()

    # Student info
    cur.execute("""
        SELECT s.*, b.name as branch_name FROM students s
        JOIN branches b ON b.id=s.branch_id WHERE s.id=%s
    """, (student_id,))
    student = row(cur)
    if not student:
        cur.close(); conn.close()
        return jsonify({'error': 'Student not found'}), 404

    # All invoices (charges)
    cur.execute("""
        SELECT id, issued as date, 'invoice' as type,
               'Tuition fee — ' || month as description,
               amount as debit, 0 as credit, status, notes
        FROM invoices WHERE student_id=%s ORDER BY issued
    """, (student_id,))
    invoices = rows(cur)

    # All payments (credits)
    cur.execute("""
        SELECT id, payment_date as date, 'payment' as type,
               'Payment received (' || method || ')' ||
               CASE WHEN reference!='' THEN ' — Ref: ' || reference ELSE '' END as description,
               0 as debit, amount as credit, 'paid' as status, notes
        FROM payments WHERE student_id=%s ORDER BY payment_date
    """, (student_id,))
    payments_list = rows(cur)

    # Instalment schedules
    cur.execute("""
        SELECT sch.id, sch.due_date as date, 'instalment' as type,
               'Instalment — ' || p.description as description,
               sch.amount as debit, 0 as credit, sch.status, sch.notes
        FROM instalment_schedule sch
        JOIN instalment_plans p ON p.id=sch.plan_id
        WHERE sch.student_id=%s ORDER BY sch.due_date
    """, (student_id,))
    instalments = rows(cur)

    # Combine and sort all transactions
    all_txns = []
    for t in invoices + payments_list + instalments:
        t['date'] = str(t['date']) if t.get('date') else ''
        t['debit'] = float(t.get('debit') or 0)
        t['credit'] = float(t.get('credit') or 0)
        all_txns.append(t)

    all_txns.sort(key=lambda x: x['date'])

    # Calculate running balance
    balance = 0.0
    for t in all_txns:
        balance += t['debit'] - t['credit']
        t['balance'] = round(balance, 2)

    # Summary
    total_charged = sum(t['debit'] for t in all_txns)
    total_paid    = sum(t['credit'] for t in all_txns)
    closing_balance = round(total_charged - total_paid, 2)

    # Instalment plans
    cur.execute("""
        SELECT ip.*, COUNT(sch.id) as total_instalments,
               COUNT(sch.id) FILTER (WHERE sch.status='paid') as paid_instalments,
               SUM(sch.amount) FILTER (WHERE sch.status='paid') as amount_paid,
               SUM(sch.amount) FILTER (WHERE sch.status!='paid') as amount_remaining
        FROM instalment_plans ip
        LEFT JOIN instalment_schedule sch ON sch.plan_id=ip.id
        WHERE ip.student_id=%s
        GROUP BY ip.id ORDER BY ip.created_at
    """, (student_id,))
    plans = rows(cur)
    for p in plans:
        if p.get('start_date'): p['start_date'] = str(p['start_date'])
        if p.get('end_date'):   p['end_date']   = str(p['end_date'])
        if p.get('total_amount'): p['total_amount'] = float(p['total_amount'])
        if p.get('amount_paid'): p['amount_paid'] = float(p['amount_paid'])
        if p.get('amount_remaining'): p['amount_remaining'] = float(p['amount_remaining'])

    # Siblings (same family)
    cur.execute("""
        SELECT id, name, admission_id FROM students
        WHERE admission_id LIKE %s AND id != %s AND branch_id=%s
    """, (student['admission_id'].rstrip('ab') + '%', student_id, student['branch_id']))
    siblings = rows(cur)

    cur.close(); conn.close()

    for f in ['created_at']:
        if student.get(f): student[f] = str(student[f])

    return jsonify({
        'student': student,
        'transactions': all_txns,
        'summary': {
            'total_charged': round(total_charged, 2),
            'total_paid': round(total_paid, 2),
            'closing_balance': closing_balance,
        },
        'instalment_plans': plans,
        'siblings': siblings,
    })

# ════════════════════════════════════════════
#  FINANCIAL — INSTALMENT PLANS
# ════════════════════════════════════════════
@api_bp.route('/api/instalment-plans', methods=['GET'])
@require_auth
def get_instalment_plans():
    b = branch_scope()
    conn = get_conn(); cur = conn.cursor()
    where = []; params = []
    if b: where.append("ip.branch_id=%s"); params.append(b)
    wc = ('WHERE '+' AND '.join(where)) if where else ''
    cur.execute(f"""
        SELECT ip.*, s.name as student_name, s.admission_id, b.name as branch_name,
               COUNT(sch.id) as total_instalments,
               COUNT(sch.id) FILTER (WHERE sch.status='paid') as paid_count,
               SUM(sch.amount) FILTER (WHERE sch.status='paid') as paid_amount,
               SUM(sch.amount) FILTER (WHERE sch.status!='paid') as remaining
        FROM instalment_plans ip
        JOIN students s ON s.id=ip.student_id
        JOIN branches b ON b.id=ip.branch_id
        LEFT JOIN instalment_schedule sch ON sch.plan_id=ip.id
        {wc} GROUP BY ip.id, s.name, s.admission_id, b.name
        ORDER BY ip.created_at DESC
    """, params)
    data = rows(cur)
    for d in data:
        if d.get('start_date'):    d['start_date']    = str(d['start_date'])
        if d.get('end_date'):      d['end_date']      = str(d['end_date'])
        if d.get('total_amount'):  d['total_amount']  = float(d['total_amount'])
        if d.get('paid_amount'):   d['paid_amount']   = float(d['paid_amount'])
        if d.get('remaining'):     d['remaining']     = float(d['remaining'])
    cur.close(); conn.close()
    return jsonify(data)

@api_bp.route('/api/instalment-plans', methods=['POST'])
@require_auth
def add_instalment_plan():
    d = request.json
    conn = get_conn(); cur = conn.cursor()
    cur.execute("""
        INSERT INTO instalment_plans (student_id, branch_id, total_amount, description, start_date, end_date, status, notes, created_by)
        VALUES (%s,%s,%s,%s,%s,%s,'active',%s,%s) RETURNING *
    """, (d['student_id'], d['branch_id'], d['total_amount'], d['description'],
          d['start_date'], d.get('end_date'), d.get('notes',''), session.get('user_id')))
    plan = row(cur)
    # Create schedule entries
    for sch in d.get('schedule', []):
        cur.execute("""
            INSERT INTO instalment_schedule (plan_id, student_id, due_date, amount, status, notes)
            VALUES (%s,%s,%s,%s,'due',%s)
        """, (plan['id'], d['student_id'], sch['due_date'], sch['amount'], sch.get('notes','')))
    conn.commit()
    if plan.get('start_date'): plan['start_date'] = str(plan['start_date'])
    if plan.get('end_date'):   plan['end_date']   = str(plan['end_date'])
    if plan.get('total_amount'): plan['total_amount'] = float(plan['total_amount'])
    cur.close(); conn.close()
    log_action('add', 'instalment_plans', plan['id'])
    return jsonify(plan), 201

@api_bp.route('/api/instalment-plans/<int:pid>', methods=['DELETE'])
@require_auth
def delete_instalment_plan(pid):
    conn = get_conn(); cur = conn.cursor()
    cur.execute("DELETE FROM instalment_plans WHERE id=%s", (pid,))
    conn.commit(); cur.close(); conn.close()
    log_action('delete', 'instalment_plans', pid)
    return jsonify({'ok': True})

@api_bp.route('/api/instalment-schedule/<int:sid>/mark-paid', methods=['POST'])
@require_auth
def mark_instalment_paid(sid):
    conn = get_conn(); cur = conn.cursor()
    cur.execute("""
        UPDATE instalment_schedule SET status='paid', paid_date=CURRENT_DATE
        WHERE id=%s RETURNING *
    """, (sid,))
    r = row(cur); conn.commit(); cur.close(); conn.close()
    return jsonify({'ok': True})

# ════════════════════════════════════════════
#  AUDIT LOG
# ════════════════════════════════════════════
@api_bp.route('/api/audit', methods=['GET'])
@require_roles('super_admin')
def get_audit():
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT * FROM audit_log ORDER BY timestamp DESC LIMIT 200")
    data = rows(cur)
    for d in data:
        if d.get('timestamp'): d['timestamp'] = str(d['timestamp'])
    cur.close(); conn.close()
    return jsonify(data)
