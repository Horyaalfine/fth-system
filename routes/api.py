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
    """Return branch_id filter: None if super_admin/reports_viewer viewing all, else branch_id."""
    role = session.get('role')
    if role in ('super_admin', 'reports_viewer'):
        # Can filter by query param or see all branches
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

# ── PAID HOURS HELPER ──
def calc_paid_hours(sign_in, sign_out, work_date):
    """Paid hours after unpaid break deductions.
    Weekend afternoon staff (sign-in >= 14:00): deduct 15min if sign-out > 16:15
    Weekend full/morning staff: deduct 15min (>11:15), 75min (>13:15), 90min (>16:15)
    Weekday: no deduction.
    """
    if not sign_in or not sign_out:
        return None
    try:
        from datetime import datetime
        si_str = str(sign_in)[:5]
        so_str = str(sign_out)[:5]
        si_mins = int(si_str[:2])*60 + int(si_str[3:5])
        so_mins = int(so_str[:2])*60 + int(so_str[3:5])
        total_mins = so_mins - si_mins
        if total_mins <= 0:
            return 0.0
        deduct = 0
        if work_date:
            if isinstance(work_date, str):
                d = datetime.strptime(work_date, '%Y-%m-%d').date()
            else:
                d = work_date
            if d.weekday() >= 5:  # weekend
                if si_mins >= 14 * 60:  # afternoon only (Slots 3+4)
                    if so_mins > 16*60+15:
                        deduct = 15  # Slot 4 break only
                else:  # full day or morning
                    if so_mins > 16*60+15:
                        deduct = 90  # Slot2+gap+Slot4
                    elif so_mins > 13*60+15:
                        deduct = 75  # Slot2+gap
                    elif so_mins > 11*60+15:
                        deduct = 15  # Slot2 break only
        return round((total_mins - deduct) / 60, 2)
    except Exception:
        return None

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
        SELECT MAX(CAST(REGEXP_REPLACE(admission_id, '[^0-9]', '', 'g') AS INT)) AS max_num
        FROM students WHERE branch_id=%s AND admission_id ~ '^[A-Z][0-9]+'
    """, (branch_id,))
    r = cur.fetchone()
    cur.close()
    max_num = r['max_num'] if r and r.get('max_num') else 99
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
@require_roles('super_admin','branch_manager','head_of_centre')
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
@require_roles('super_admin','branch_manager','head_of_centre')
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

def get_student_fields(d):
    dob = d.get('date_of_birth') or None
    # Ensure required fields have fallbacks
    name = d.get('name') or (str(d.get('first_name','')) + ' ' + str(d.get('last_name',''))).strip() or 'Unknown'
    branch_id = d.get('branch_id')
    admission_id = d.get('admission_id','')
    if not branch_id:
        raise ValueError("branch_id is required")
    if not admission_id:
        raise ValueError("admission_id is required")
    if not name:
        raise ValueError("Student name is required")
    return {
        'branch_id': branch_id, 'admission_id': admission_id,
        'name': name, 'first_name': d.get('first_name',''),
        'last_name': d.get('last_name',''), 'date_of_birth': dob,
        'gender': d.get('gender',''), 'year_group': d.get('year_group',''),
        'current_school': d.get('current_school',''),
        'medical_notes': d.get('medical_notes',''), 'sen_notes': d.get('sen_notes',''),
        'carer1_first_name': d.get('carer1_first_name',''), 'carer1_last_name': d.get('carer1_last_name',''),
        'carer1_address': d.get('carer1_address',''), 'carer1_postcode': d.get('carer1_postcode',''),
        'carer1_telephone': d.get('carer1_telephone',''), 'carer1_mobile': d.get('carer1_mobile',''),
        'carer1_email': d.get('carer1_email',''), 'carer1_occupation': d.get('carer1_occupation',''),
        'carer2_first_name': d.get('carer2_first_name',''), 'carer2_last_name': d.get('carer2_last_name',''),
        'carer2_address': d.get('carer2_address',''), 'carer2_postcode': d.get('carer2_postcode',''),
        'carer2_telephone': d.get('carer2_telephone',''), 'carer2_mobile': d.get('carer2_mobile',''),
        'carer2_email': d.get('carer2_email',''), 'carer2_occupation': d.get('carer2_occupation',''),
        'emergency_name': d.get('emergency_name',''), 'emergency_telephone': d.get('emergency_telephone',''),
        'emergency_relation': d.get('emergency_relation',''),
        'referred_by': d.get('referred_by',''), 'referral_admission': d.get('referral_admission',''),
        'gcse_maths_board': d.get('gcse_maths_board',''), 'gcse_maths_paper': d.get('gcse_maths_paper',''),
        'gcse_maths_exam_date': d.get('gcse_maths_exam_date',''),
        'gcse_maths_current_grade': d.get('gcse_maths_current_grade',''),
        'gcse_maths_predicted_grade': d.get('gcse_maths_predicted_grade',''),
        'gcse_english_board': d.get('gcse_english_board',''), 'gcse_english_paper': d.get('gcse_english_paper',''),
        'gcse_english_exam_date': d.get('gcse_english_exam_date',''),
        'gcse_english_current_grade': d.get('gcse_english_current_grade',''),
        'gcse_english_predicted_grade': d.get('gcse_english_predicted_grade',''),
        'gcse_science_board': d.get('gcse_science_board',''), 'gcse_science_paper': d.get('gcse_science_paper',''),
        'gcse_science_exam_date': d.get('gcse_science_exam_date',''),
        'gcse_science_current_grade': d.get('gcse_science_current_grade',''),
        'gcse_science_predicted_grade': d.get('gcse_science_predicted_grade',''),
        'assess_maths_pct': d.get('assess_maths_pct',''), 'assess_maths_book': d.get('assess_maths_book',''),
        'assess_english_pct': d.get('assess_english_pct',''), 'assess_english_book': d.get('assess_english_book',''),
        'assess_science_pct': d.get('assess_science_pct',''), 'assess_science_book': d.get('assess_science_book',''),
        'hours_per_week': d.get('hours_per_week',''),
        'parent_contact': d.get('carer1_mobile') or d.get('parent_contact',''),
        'status': d.get('status','active'), 'notes': d.get('notes','')
    }

@api_bp.route('/api/students', methods=['POST'])
@require_auth
def add_student():
    d = request.json
    conn = get_conn(); cur = conn.cursor()
    try:
        fields = get_student_fields(d)
        placeholders = ','.join(['%s'] * len(fields))
        cols = ','.join(fields.keys())
        cur.execute(f"INSERT INTO students ({cols}) VALUES ({placeholders}) RETURNING *", list(fields.values()))
        r = row(cur); conn.commit()
        if r:
            if r.get('date_of_birth'): r['date_of_birth'] = str(r['date_of_birth'])
            if r.get('created_at'): r['created_at'] = str(r['created_at'])
        cur.close(); conn.close()
        log_action('add','students',r['id'])
        return jsonify(r), 201
    except Exception as e:
        conn.rollback()
        cur.close(); conn.close()
        import traceback
        print(f"add_student error: {e}")
        traceback.print_exc()
        return jsonify({'error': str(e)}), 400
@api_bp.route('/api/students/<int:sid>', methods=['PUT'])
@require_auth
def update_student(sid):
    d = request.json
    conn = get_conn(); cur = conn.cursor()
    try:
        fields = get_student_fields(d)
        set_clause = ','.join([f"{k}=%s" for k in fields.keys()])
        vals = list(fields.values()) + [sid]
        cur.execute(f"UPDATE students SET {set_clause} WHERE id=%s RETURNING *", vals)
        r = row(cur); conn.commit(); cur.close(); conn.close()
        if r:
            if r.get('date_of_birth'): r['date_of_birth'] = str(r['date_of_birth'])
            if r.get('created_at'): r['created_at'] = str(r['created_at'])
        log_action('edit','students',sid)
        return jsonify(r)
    except Exception as e:
        conn.rollback(); cur.close(); conn.close()
        return jsonify({'error': str(e)}), 400

@api_bp.route('/api/students/<int:sid>/opening-balance', methods=['PUT'])
@require_auth
def update_opening_balance(sid):
    d = request.json
    conn = get_conn(); cur = conn.cursor()
    try:
        cur.execute("""
            UPDATE students SET opening_balance=%s WHERE id=%s
            RETURNING id, opening_balance
        """, (d.get('opening_balance', 0), sid))
        r = row(cur); conn.commit()
    except Exception as e:
        conn.rollback(); cur.close(); conn.close()
        return jsonify({'error': str(e)}), 400
    cur.close(); conn.close()
    if r and r.get('opening_balance') is not None:
        r['opening_balance'] = float(r['opening_balance'])
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
    try:
        cur.execute("""
            UPDATE staff SET branch_id=%s, name=%s, role=%s, subject=%s, contact=%s, status=%s
            WHERE id=%s RETURNING *
        """, (d['branch_id'], d['name'], d.get('role','teacher'), d.get('subject',''), d.get('contact',''), d.get('status','active'), sid))
        r = row(cur); conn.commit(); cur.close(); conn.close()
        log_action('edit', 'staff', sid)
        return jsonify(r)
    except Exception as e:
        conn.rollback(); cur.close(); conn.close()
        return jsonify({'error': str(e)}), 400

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
    """Bulk upsert attendance for a session. Also deletes records for unmarked students."""
    d = request.json
    session_id = d['session_id']
    records    = d['records']  # [{student_id, status, notes}]
    delete_ids = d.get('delete_student_ids', [])  # students to unmark (remove from DB)
    conn = get_conn(); cur = conn.cursor()
    # Delete unmarked students
    for sid in delete_ids:
        cur.execute("DELETE FROM attendance WHERE session_id=%s AND student_id=%s", (session_id, sid))
    # Upsert present/absent records
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
        SELECT i.*, s.name as student_name, s.admission_id, b.name as branch_name,
               COALESCE(i.fee_type,'monthly_fee') as fee_type
        FROM invoices i JOIN students s ON s.id=i.student_id
        JOIN branches b ON b.id=i.branch_id
        {wc} ORDER BY i.issued DESC
    """, params)
    data = rows(cur)
    for d in data:
        if d.get('issued'):    d['issued']    = str(d['issued'])
        if d.get('paid_date'): d['paid_date'] = str(d['paid_date'])
        if d.get('due_date'):  d['due_date']  = str(d['due_date'])
        amt = float(d.get('amount') or 0)
        paid = float(d.get('amount_paid') or 0)
        d['amount'] = amt
        d['amount_paid'] = paid
        d['balance'] = round(amt - paid, 2)
    cur.close(); conn.close()
    return jsonify(data)

@api_bp.route('/api/invoices', methods=['POST'])
@require_auth
def add_invoice():
    d = request.json
    conn = get_conn(); cur = conn.cursor()
    try:
        cur.execute("""
            INSERT INTO invoices (student_id, branch_id, amount, month, status,
                                  due_date, notes, fee_type, description)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *
        """, (d['student_id'], d['branch_id'], d['amount'], d['month'],
                d.get('status','due'), d.get('due_date') or None,
                d.get('notes',''), d.get('fee_type','monthly_fee'),
                d.get('description','')))
        r = row(cur); conn.commit()
        if r:
            if r.get('issued'): r['issued'] = str(r['issued'])
            if r.get('due_date'): r['due_date'] = str(r['due_date'])
            if r.get('amount') is not None:      r['amount']      = float(r['amount'])
            if r.get('amount_paid') is not None: r['amount_paid'] = float(r['amount_paid'])
        cur.close(); conn.close()
        log_action('add', 'invoices', r['id'] if r else 0)
        return jsonify(r), 201
    except Exception as e:
        conn.rollback(); cur.close(); conn.close()
        return jsonify({'error': str(e)}), 400

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
    try:
        cur.execute("""
            UPDATE invoices SET amount=%s, amount_paid=%s, status=%s, month=%s,
                fee_type=%s, due_date=%s, paid_date=%s, notes=%s, description=%s
            WHERE id=%s RETURNING *
        """, (d.get('amount',0), d.get('amount_paid',0), d.get('status','due'), d.get('month'),
                d.get('fee_type','monthly_fee'),
                d.get('due_date') or None, d.get('paid_date') or None,
                d.get('notes',''), d.get('description',''), iid))
        r = row(cur); conn.commit()
    except Exception as e:
        conn.rollback(); cur.close(); conn.close()
        return jsonify({'error': str(e)}), 400
    cur.close(); conn.close()
    if r:
        if r.get('issued'):    r['issued']    = str(r['issued'])
        if r.get('paid_date'): r['paid_date'] = str(r['paid_date'])
        if r.get('due_date'):  r['due_date']  = str(r['due_date'])
        if r.get('amount') is not None:      r['amount']      = float(r['amount'])
        if r.get('amount_paid') is not None: r['amount_paid'] = float(r['amount_paid'])
    log_action('edit', 'invoices', iid)
    return jsonify(r)

@api_bp.route('/api/invoices/<int:iid>', methods=['DELETE'])
@require_auth
def delete_invoice(iid):
    conn = get_conn(); cur = conn.cursor()
    cur.execute("DELETE FROM invoices WHERE id=%s", (iid,))
    conn.commit(); cur.close(); conn.close()
    log_action('delete', 'invoices', iid)
    return jsonify({'ok': True})

@api_bp.route('/api/invoices/<int:iid>/mark-paid', methods=['POST'])
@require_auth
def mark_invoice_paid(iid):
    d = request.json or {}
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT * FROM invoices WHERE id=%s", (iid,))
    inv = row(cur)
    if not inv:
        cur.close(); conn.close()
        return jsonify({'error': 'Invoice not found'}), 404

    total_amount = float(inv['amount'])
    already_paid = float(inv.get('amount_paid') or 0)
    # Allow specifying a payment amount (for partial payments); default = remaining balance
    pay_amount = d.get('amount')
    if pay_amount is None:
        pay_amount = total_amount - already_paid
    pay_amount = round(float(pay_amount), 2)
    if pay_amount <= 0:
        cur.close(); conn.close()
        return jsonify({'error': 'Payment amount must be greater than zero'}), 400

    new_paid = round(already_paid + pay_amount, 2)
    if new_paid >= total_amount:
        new_status = 'paid'
        new_paid = total_amount
    else:
        new_status = 'partial'

    cur.execute("""
        UPDATE invoices SET status=%s, amount_paid=%s,
            paid_date=CASE WHEN %s='paid' THEN CURRENT_DATE ELSE paid_date END
        WHERE id=%s RETURNING *
    """, (new_status, new_paid, new_status, iid))
    r = row(cur); conn.commit()

    # Also create a payment record so Finance totals match
    method = d.get('method', 'cash')
    reference = d.get('reference', '')
    notes = d.get('notes', '')
    cur.execute("""
        INSERT INTO payments (student_id, branch_id, amount, payment_date, method, reference, notes, recorded_by)
        VALUES (%s,%s,%s,CURRENT_DATE,%s,%s,%s,%s) RETURNING id
    """, (inv['student_id'], inv['branch_id'], pay_amount, method, reference,
            notes or f"Invoice #{iid} ({inv.get('month','')})" + (' (partial)' if new_status=='partial' else ''),
            session.get('user_id')))
    payment_id = cur.fetchone()['id']
    conn.commit()
    cur.close(); conn.close()
    log_action('edit', 'invoices', iid)
    log_action('add', 'payments', payment_id)
    return jsonify({'ok': True, 'payment_id': payment_id, 'status': new_status, 'amount_paid': new_paid, 'balance': round(total_amount - new_paid, 2)})

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
@require_roles('super_admin','branch_manager','head_of_centre')
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
@require_roles('super_admin','branch_manager','head_of_centre')
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
@require_roles('super_admin','branch_manager','head_of_centre')
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
@require_roles('super_admin','branch_manager','head_of_centre')
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
@require_roles('super_admin','branch_manager','head_of_centre')
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
    bw  = "WHERE s.branch_id=%s" if b else ""
    bw2 = "WHERE s.branch_id=%s" if b else ""
    bw3 = "WHERE s.branch_id=%s" if b else ""

    # Student count
    if b:
        cur.execute("SELECT COUNT(*) as c FROM students s WHERE s.branch_id=%s AND s.status='active'", (b,))
    else:
        cur.execute("SELECT COUNT(*) as c FROM students s WHERE s.status='active'")
    student_count = cur.fetchone()['c']

    # Staff count
    cur.execute(f"SELECT COUNT(*) as c FROM staff s {bw2}", params)
    staff_count = cur.fetchone()['c']

    # Session count
    cur.execute(f"SELECT COUNT(*) as c FROM sessions s {bw3}", params)
    session_count = cur.fetchone()['c']

    # Attendance rate
    cur.execute("""
        SELECT COUNT(*) FILTER (WHERE a.status='present') as present,
               COUNT(*) as total
        FROM attendance a
        JOIN sessions s ON s.id=a.session_id
    """ + (f" WHERE s.branch_id=%s" if b else ""), params)
    att = cur.fetchone()
    att_rate = round(att['present'] / att['total'] * 100) if att['total'] else 0

    # Per branch stats — filter to the user's branch when scoped
    branch_filter = "WHERE b.id=%s" if b else ""
    branch_params = (b,) if b else ()
    stu_filter  = "WHERE status='active' AND branch_id=%s" if b else "WHERE status='active'"
    sess_filter = "WHERE branch_id=%s" if b else ""
    cur.execute(f"""
        SELECT b.name, b.id,
          COALESCE(stu.c,0) as students,
          COALESCE(sess.c,0) as sessions,
          COALESCE(att.present,0) as present,
          COALESCE(att.total,0) as att_total
        FROM branches b
        LEFT JOIN (SELECT branch_id, COUNT(*) as c FROM students {stu_filter} GROUP BY branch_id) stu ON stu.branch_id=b.id
        LEFT JOIN (SELECT branch_id, COUNT(*) as c FROM sessions {sess_filter} GROUP BY branch_id) sess ON sess.branch_id=b.id
        LEFT JOIN (SELECT s.branch_id,
            COUNT(*) FILTER (WHERE a.status='present') as present,
            COUNT(*) as total
            FROM attendance a JOIN sessions s ON s.id=a.session_id GROUP BY s.branch_id) att ON att.branch_id=b.id
        {branch_filter}
        ORDER BY b.name
    """, branch_params * 3 if b else ())
    branch_stats = rows(cur)

    # Year group breakdown
    if b:
        cur.execute("SELECT year_group, COUNT(*) as c FROM students s WHERE s.branch_id=%s GROUP BY year_group ORDER BY year_group", (b,))
    else:
        cur.execute("SELECT year_group, COUNT(*) as c FROM students s GROUP BY year_group ORDER BY year_group")
    year_groups = rows(cur)

    # Subject breakdown
    if b:
        cur.execute("SELECT subject, COUNT(*) as c FROM sessions s WHERE s.branch_id=%s GROUP BY subject ORDER BY c DESC", (b,))
    else:
        cur.execute("SELECT subject, COUNT(*) as c FROM sessions s GROUP BY subject ORDER BY c DESC")
    subjects = rows(cur)

    # Outstanding invoices
    if b:
        cur.execute("SELECT SUM(amount) as total FROM invoices WHERE status!='paid' AND branch_id=%s", (b,))
    else:
        cur.execute("SELECT SUM(amount) as total FROM invoices WHERE status!='paid'")
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
        si = str(d['sign_in'])[:5]  if d.get('sign_in')  else None
        so = str(d['sign_out'])[:5] if d.get('sign_out') else None
        d['sign_in']  = si
        d['sign_out'] = so
        d['paid_hours'] = calc_paid_hours(si, so, d.get('date'))
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
    cur.execute(
        "SELECT st.id, st.name, st.role, st.subject, b.name as branch_name,"
        " COUNT(*) FILTER (WHERE sa.status='present') as present,"
        " COUNT(*) FILTER (WHERE sa.status='absent') as absent,"
        " COUNT(*) FILTER (WHERE sa.status='late') as late,"
        " COUNT(*) FILTER (WHERE sa.status='no_sign_out') as no_sign_out,"
        " COUNT(*) as total_sessions"
        " FROM staff st JOIN branches b ON b.id=st.branch_id"
        " LEFT JOIN staff_attendance sa ON sa.staff_id=st.id"
        " AND TO_CHAR(sa.date,'YYYY-MM')=%s " + bw +
        " GROUP BY st.id, st.name, st.role, st.subject, b.name ORDER BY st.name",
        params)
    data = rows(cur)
    cur.execute(
        "SELECT sa.staff_id, sa.sign_in, sa.sign_out, sa.date"
        " FROM staff_attendance sa"
        " WHERE TO_CHAR(sa.date,'YYYY-MM')=%s"
        " AND sa.sign_in IS NOT NULL AND sa.sign_out IS NOT NULL"
        " AND sa.status='present' " + bw,
        params)
    records = cur.fetchall()
    cur.close(); conn.close()
    paid_by_staff = {}
    clock_by_staff = {}
    for r in records:
        sid = r['staff_id']
        si = str(r['sign_in'])[:5] if r['sign_in'] else None
        so = str(r['sign_out'])[:5] if r['sign_out'] else None
        paid = calc_paid_hours(si, so, r['date'])
        try:
            si_m = int(si[:2])*60+int(si[3:5])
            so_m = int(so[:2])*60+int(so[3:5])
            clock = round((so_m-si_m)/60, 2)
        except Exception:
            clock = 0
        if paid is not None:
            paid_by_staff[sid] = round((paid_by_staff.get(sid) or 0) + paid, 2)
        clock_by_staff[sid] = round((clock_by_staff.get(sid) or 0) + clock, 2)
    for d in data:
        d['total_clock_hours'] = clock_by_staff.get(d['id'], 0)
        d['total_paid_hours'] = paid_by_staff.get(d['id'], 0)
    return jsonify(data)


@api_bp.route('/api/staff-attendance', methods=['POST'])
@require_auth
def save_staff_attendance():
    d = request.json
    conn = get_conn(); cur = conn.cursor()
    session_id = d.get('session_id') or None
    try:
        if session_id:
            # With session — use upsert
            cur.execute("""
                INSERT INTO staff_attendance
                    (session_id, staff_id, branch_id, date, sign_in, sign_out,
                     status, cover_for, absence_reason, notes)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (session_id, staff_id)
                DO UPDATE SET
                    sign_in=EXCLUDED.sign_in, sign_out=EXCLUDED.sign_out,
                    status=EXCLUDED.status, cover_for=EXCLUDED.cover_for,
                    absence_reason=EXCLUDED.absence_reason, notes=EXCLUDED.notes
                RETURNING *
            """, (session_id, d['staff_id'], d['branch_id'], d['date'],
                  d.get('sign_in') or None, d.get('sign_out') or None,
                  d.get('status','present'), d.get('cover_for') or None,
                  d.get('absence_reason',''), d.get('notes','')))
        else:
            # Without session — plain insert (no upsert, session_id is NULL)
            cur.execute("""
                INSERT INTO staff_attendance
                    (session_id, staff_id, branch_id, date, sign_in, sign_out,
                     status, cover_for, absence_reason, notes)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                RETURNING *
            """, (None, d['staff_id'], d['branch_id'], d['date'],
                  d.get('sign_in') or None, d.get('sign_out') or None,
                  d.get('status','present'), d.get('cover_for') or None,
                  d.get('absence_reason',''), d.get('notes','')))
        r = row(cur); conn.commit(); cur.close(); conn.close()
        if r:
            if r.get('date'):     r['date']     = str(r['date'])
            if r.get('sign_in'):  r['sign_in']  = str(r['sign_in'])
            if r.get('sign_out'): r['sign_out'] = str(r['sign_out'])
        log_action('edit', 'staff_attendance', d.get('staff_id'))
        return jsonify(r), 201
    except Exception as e:
        conn.rollback(); cur.close(); conn.close()
        return jsonify({'error': str(e)}), 400


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


@api_bp.route('/api/adjustments', methods=['GET'])
@require_auth
def get_adjustments():
    b = branch_scope()
    student_id = request.args.get('student_id')
    conn = get_conn(); cur = conn.cursor()
    where = []; params = []
    if b:          where.append("a.branch_id=%s"); params.append(b)
    if student_id: where.append("a.student_id=%s"); params.append(int(student_id))
    wc = ('WHERE '+' AND '.join(where)) if where else ''
    cur.execute(f"""
        SELECT a.*, s.name as student_name, s.admission_id, b.name as branch_name,
               u.name as recorded_by_name
        FROM adjustments a
        JOIN students s ON s.id=a.student_id
        JOIN branches b ON b.id=a.branch_id
        LEFT JOIN users u ON u.id=a.recorded_by
        {wc} ORDER BY a.adj_date DESC, a.created_at DESC
    """, params)
    data = rows(cur)
    for d in data:
        if d.get('adj_date'): d['adj_date'] = str(d['adj_date'])
        if d.get('amount') is not None: d['amount'] = float(d['amount'])
    cur.close(); conn.close()
    return jsonify(data)

@api_bp.route('/api/adjustments', methods=['POST'])
@require_auth
def add_adjustment():
    d = request.json
    conn = get_conn(); cur = conn.cursor()
    try:
        cur.execute("""
            INSERT INTO adjustments (student_id, branch_id, amount, adj_type, adj_date, notes, recorded_by)
            VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING *
        """, (d['student_id'], d['branch_id'], d['amount'], d.get('adj_type','discount'),
                d.get('adj_date') or None, d.get('notes',''), session.get('user_id')))
        r = row(cur); conn.commit()
    except Exception as e:
        conn.rollback(); cur.close(); conn.close()
        return jsonify({'error': str(e)}), 400
    cur.close(); conn.close()
    if r:
        if r.get('adj_date'): r['adj_date'] = str(r['adj_date'])
        if r.get('amount') is not None: r['amount'] = float(r['amount'])
    log_action('add', 'adjustments', r['id'] if r else 0)
    return jsonify(r), 201

@api_bp.route('/api/adjustments/<int:aid>', methods=['PUT'])
@require_auth
def update_adjustment(aid):
    d = request.json
    conn = get_conn(); cur = conn.cursor()
    try:
        cur.execute("""
            UPDATE adjustments SET amount=%s, adj_type=%s, adj_date=%s, notes=%s
            WHERE id=%s RETURNING *
        """, (d['amount'], d.get('adj_type','discount'), d.get('adj_date') or None,
                d.get('notes',''), aid))
        r = row(cur); conn.commit()
    except Exception as e:
        conn.rollback(); cur.close(); conn.close()
        return jsonify({'error': str(e)}), 400
    cur.close(); conn.close()
    if r:
        if r.get('adj_date'): r['adj_date'] = str(r['adj_date'])
        if r.get('amount') is not None: r['amount'] = float(r['amount'])
    log_action('edit', 'adjustments', aid)
    return jsonify(r)

@api_bp.route('/api/adjustments/<int:aid>', methods=['DELETE'])
@require_auth
def delete_adjustment(aid):
    conn = get_conn(); cur = conn.cursor()
    cur.execute("DELETE FROM adjustments WHERE id=%s", (aid,))
    conn.commit(); cur.close(); conn.close()
    log_action('delete', 'adjustments', aid)
    return jsonify({'ok': True})

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
               CASE COALESCE(fee_type,'monthly_fee')
                   WHEN 'monthly_fee' THEN 'Tuition fee — ' || month
                   WHEN 'opening_balance' THEN 'Opening balance (brought forward)'
                   WHEN 'admission_fee' THEN 'Admission fee'
                   WHEN 'book_fee' THEN 'Book fee'
                   WHEN 'past_papers_fee' THEN 'Past papers fee'
                   ELSE 'Miscellaneous fee'
               END
               || CASE WHEN COALESCE(description,'')!='' THEN ' — ' || description ELSE '' END as description,
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

    # Adjustments (discounts/credits = positive debit reduces balance via negative amount, or credit field)
    cur.execute("""
        SELECT id, adj_date as date, 'adjustment' as type,
               CASE WHEN amount >= 0 THEN 'Adjustment — ' || adj_type
                    ELSE 'Adjustment — ' || adj_type || ' (credit)' END
               || CASE WHEN notes != '' THEN ': ' || notes ELSE '' END as description,
               CASE WHEN amount >= 0 THEN amount ELSE 0 END as debit,
               CASE WHEN amount < 0 THEN -amount ELSE 0 END as credit,
               adj_type as status, notes
        FROM adjustments WHERE student_id=%s ORDER BY adj_date
    """, (student_id,))
    adjustments_list = rows(cur)

    # Combine and sort all transactions
    all_txns = []
    for t in invoices + payments_list + instalments + adjustments_list:
        t['date'] = str(t['date']) if t.get('date') else ''
        t['debit'] = float(t.get('debit') or 0)
        t['credit'] = float(t.get('credit') or 0)
        all_txns.append(t)

    all_txns.sort(key=lambda x: x['date'])

    # Opening balance as the starting line
    opening_balance = float(student.get('opening_balance') or 0)
    balance = opening_balance
    if opening_balance != 0:
        all_txns.insert(0, {
            'id': 0, 'date': '', 'type': 'opening_balance',
            'description': 'Opening balance (brought forward)',
            'debit': opening_balance if opening_balance > 0 else 0,
            'credit': -opening_balance if opening_balance < 0 else 0,
            'status': '', 'notes': '', 'balance': round(opening_balance, 2)
        })

    # Calculate running balance for the rest
    for t in all_txns:
        if t['type'] == 'opening_balance':
            continue
        balance += t['debit'] - t['credit']
        t['balance'] = round(balance, 2)

    # Summary
    total_charged = sum(t['debit'] for t in all_txns)
    total_paid    = sum(t['credit'] for t in all_txns)
    closing_balance = round(opening_balance + total_charged - total_paid - opening_balance + opening_balance, 2)
    closing_balance = round(balance, 2)

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
    if student.get('opening_balance') is not None:
        student['opening_balance'] = float(student['opening_balance'])

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
#  STUDENT TIMETABLE
# ════════════════════════════════════════════
@api_bp.route('/api/student-timetable', methods=['GET'])
@require_auth
def get_student_timetable():
    b = branch_scope()
    student_id = request.args.get('student_id', type=int)
    conn = get_conn(); cur = conn.cursor()
    where = []; params = []
    if b: where.append("st.branch_id=%s"); params.append(b)
    if student_id: where.append("st.student_id=%s"); params.append(student_id)
    wc = ("WHERE " + " AND ".join(where)) if where else ""
    cur.execute(f"""
        SELECT st.*, s.name as student_name, s.admission_id, s.year_group
        FROM student_timetable st
        JOIN students s ON s.id=st.student_id
        {wc} ORDER BY s.admission_id, st.day_type, st.slot
    """, params)
    data = rows(cur); cur.close(); conn.close()
    return jsonify(data)

@api_bp.route('/api/student-timetable', methods=['POST'])
@require_auth
def save_student_timetable():
    """Bulk save timetable entries for a student."""
    d = request.json
    student_id = d['student_id']
    entries = d.get('entries', [])
    conn = get_conn(); cur = conn.cursor()
    # Delete existing entries and re-insert the full list atomically
    cur.execute("DELETE FROM student_timetable WHERE student_id=%s", (student_id,))
    for e in entries:
        cur.execute("""
            INSERT INTO student_timetable (student_id, branch_id, day_type, slot, subject, notes)
            VALUES (%s,%s,%s,%s,%s,%s)
        """, (student_id, e['branch_id'], e['day_type'], e['slot'], e['subject'], e.get('notes','')))
    conn.commit(); cur.close(); conn.close()
    log_action('edit', 'student_timetable', student_id)
    return jsonify({'ok': True, 'saved': len(entries)})

@api_bp.route('/api/student-timetable/<int:tid>', methods=['DELETE'])
@require_auth
def delete_timetable_entry(tid):
    conn = get_conn(); cur = conn.cursor()
    cur.execute("DELETE FROM student_timetable WHERE id=%s", (tid,))
    conn.commit(); cur.close(); conn.close()
    return jsonify({'ok': True})


@api_bp.route('/api/sessions/<int:session_id>/students', methods=['GET'])
@require_auth
def get_session_attendance_students(session_id):
    """Get students for attendance.
    Priority: 1) table_allocation_students  2) session_students  3) already-marked attendance records.
    Never falls back to full timetable/branch list.
    """
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT * FROM sessions WHERE id=%s", (session_id,))
    sess = row(cur)
    if not sess:
        cur.close(); conn.close()
        return jsonify([])
    # Source 1: table_allocation_students (Session Planner)
    cur.execute("""
        SELECT DISTINCT s.id as student_id, s.name as student_name, s.admission_id,
               ta.table_no, 'allocation' as source
        FROM table_allocation_students tas
        JOIN table_allocations ta ON ta.id=tas.allocation_id
        JOIN students s ON s.id=tas.student_id
        WHERE ta.session_id=%s
        ORDER BY ta.table_no, s.admission_id
    """, (session_id,))
    alloc_students = rows(cur)
    if alloc_students:
        cur.close(); conn.close()
        return jsonify(alloc_students)
    # Source 2: session_students (directly assigned)
    cur.execute("""
        SELECT s.id as student_id, s.name as student_name, s.admission_id,
               %s as table_no, 'session' as source
        FROM session_students ss
        JOIN students s ON s.id=ss.student_id
        WHERE ss.session_id=%s AND s.status='active'
        ORDER BY s.admission_id
    """, (sess.get('table_no',1), session_id))
    sess_students = rows(cur)
    if sess_students:
        cur.close(); conn.close()
        return jsonify(sess_students)
    # Source 3: already-marked attendance records only
    cur.execute("""
        SELECT s.id as student_id, s.name as student_name, s.admission_id,
               %s as table_no, 'attendance' as source
        FROM attendance a
        JOIN students s ON s.id=a.student_id
        WHERE a.session_id=%s
        ORDER BY s.admission_id
    """, (sess.get('table_no',1), session_id))
    att_students = rows(cur)
    cur.close(); conn.close()
    return jsonify(att_students)

@api_bp.route('/api/sessions/<int:session_id>/students/<int:student_id>', methods=['DELETE'])
@require_auth
def remove_student_from_session(session_id, student_id):
    conn = get_conn(); cur = conn.cursor()
    cur.execute('''
        DELETE FROM table_allocation_students tas
        USING table_allocations ta
        WHERE tas.allocation_id=ta.id AND ta.session_id=%s AND tas.student_id=%s
    ''', (session_id, student_id))
    cur.execute("DELETE FROM session_students WHERE session_id=%s AND student_id=%s", (session_id, student_id))
    conn.commit(); cur.close(); conn.close()
    log_action('edit', 'sessions', session_id)
    return jsonify({'ok': True})

# ════════════════════════════════════════════
#  TABLE ALLOCATIONS
# ════════════════════════════════════════════
@api_bp.route('/api/table-allocations/<int:session_id>', methods=['GET'])
@require_auth
def get_table_allocations(session_id):
    conn = get_conn(); cur = conn.cursor()
    cur.execute("""
        SELECT ta.*, st.name as teacher_name,
               COUNT(tas.id) as student_count
        FROM table_allocations ta
        LEFT JOIN staff st ON st.id=ta.teacher_id
        LEFT JOIN table_allocation_students tas ON tas.allocation_id=ta.id
        WHERE ta.session_id=%s
        GROUP BY ta.id, st.name
        ORDER BY ta.table_no
    """, (session_id,))
    tables = rows(cur)
    # Get students per table
    for t in tables:
        cur.execute("""
            SELECT tas.*, s.name as student_name, s.admission_id, s.year_group
            FROM table_allocation_students tas
            JOIN students s ON s.id=tas.student_id
            WHERE tas.allocation_id=%s ORDER BY s.admission_id
        """, (t['id'],))
        t['students'] = rows(cur)
    cur.close(); conn.close()
    return jsonify(tables)

@api_bp.route('/api/table-allocations', methods=['POST'])
@require_auth
def save_table_allocation():
    d = request.json
    conn = get_conn(); cur = conn.cursor()
    cur.execute("""
        INSERT INTO table_allocations (session_id, table_no, teacher_id, subject, max_students, notes)
        VALUES (%s,%s,%s,%s,%s,%s)
        ON CONFLICT (session_id, table_no) DO UPDATE SET
            teacher_id=EXCLUDED.teacher_id, subject=EXCLUDED.subject,
            max_students=EXCLUDED.max_students, notes=EXCLUDED.notes
        RETURNING *
    """, (d['session_id'], d['table_no'], d.get('teacher_id') or None,
          d.get('subject',''), d.get('max_students',5), d.get('notes','')))
    r = row(cur); conn.commit(); cur.close(); conn.close()
    log_action('edit', 'table_allocations', r['id'])
    return jsonify(r), 201

@api_bp.route('/api/table-allocations/<int:aid>', methods=['DELETE'])
@require_auth
def delete_table_allocation(aid):
    conn = get_conn(); cur = conn.cursor()
    cur.execute("DELETE FROM table_allocations WHERE id=%s", (aid,))
    conn.commit(); cur.close(); conn.close()
    return jsonify({'ok': True})

@api_bp.route('/api/table-allocations/<int:aid>/students', methods=['POST'])
@require_auth
def add_table_student(aid):
    d = request.json
    student_ids = d.get('student_ids', [])
    conn = get_conn(); cur = conn.cursor()
    added = 0
    for sid in student_ids:
        cur.execute("""
            INSERT INTO table_allocation_students (allocation_id, student_id, is_catchup)
            VALUES (%s,%s,%s) ON CONFLICT DO NOTHING
        """, (aid, sid, d.get('is_catchup', False)))
        if cur.rowcount: added += 1
    conn.commit(); cur.close(); conn.close()
    return jsonify({'added': added})

@api_bp.route('/api/table-allocations/<int:aid>/students/<int:sid>', methods=['DELETE'])
@require_auth
def remove_table_student(aid, sid):
    conn = get_conn(); cur = conn.cursor()
    cur.execute("DELETE FROM table_allocation_students WHERE allocation_id=%s AND student_id=%s", (aid, sid))
    conn.commit(); cur.close(); conn.close()
    return jsonify({'ok': True})

# ════════════════════════════════════════════
#  SESSION REPORT (formatted like Excel sample)
# ════════════════════════════════════════════
@api_bp.route('/api/session-report/<date_str>', methods=['GET'])
@require_auth
def get_session_report(date_str):
    b = branch_scope()
    conn = get_conn(); cur = conn.cursor()
    bp = (b,) if b else ()
    bw = "AND s.branch_id=%s" if b else ""

    # Get all sessions for the date
    cur.execute(f"""
        SELECT s.*, st.name as staff_name, st.subject as staff_subject,
               b.name as branch_name, cv.name as cover_name
        FROM sessions s
        JOIN branches b ON b.id=s.branch_id
        LEFT JOIN staff st ON st.id=s.staff_id
        LEFT JOIN staff cv ON cv.id=s.cover_staff_id
        WHERE s.date=%s {bw} ORDER BY s.slot, st.name
    """, (date_str,)+bp)
    sessions = rows(cur)

    report_slots = {}
    for sess in sessions:
        slot = sess['slot']
        if slot not in report_slots:
            report_slots[slot] = {
                'slot': slot, 'tables': [],
                'session_ids': [], 'branch_name': sess['branch_name']
            }
        report_slots[slot]['session_ids'].append(sess['id'])

        # Check if table allocations exist for this session
        cur.execute("SELECT COUNT(*) as c FROM table_allocations WHERE session_id=%s", (sess['id'],))
        has_allocs = cur.fetchone()['c'] > 0

        if has_allocs:
            # ── Use table allocations ──
            cur.execute("""
                SELECT ta.*, st.name as teacher_name,
                       tas.student_id, s.name as student_name, s.admission_id,
                       s.year_group, tas.is_catchup,
                       a.status as att_status, a.notes as att_notes
                FROM table_allocations ta
                LEFT JOIN staff st ON st.id=ta.teacher_id
                LEFT JOIN table_allocation_students tas ON tas.allocation_id=ta.id
                LEFT JOIN students s ON s.id=tas.student_id
                LEFT JOIN attendance a ON a.session_id=ta.session_id
                    AND a.student_id=tas.student_id
                WHERE ta.session_id=%s ORDER BY ta.table_no, s.admission_id
            """, (sess['id'],))
            alloc_rows = cur.fetchall()
            tables = {}
            for r in alloc_rows:
                tid = r['id']
                if tid not in tables:
                    tables[tid] = {
                        'table_no': r['table_no'],
                        'teacher_name': r['teacher_name'] or sess['staff_name'] or '—',
                        'subject': r['subject'] or sess['staff_subject'] or '—',
                        'max_students': r['max_students'],
                        'students': []
                    }
                if r['student_id']:
                    tables[tid]['students'].append({
                        'student_id': r['student_id'],
                        'student_name': r['student_name'],
                        'admission_id': r['admission_id'],
                        'year_group': r['year_group'],
                        'is_catchup': r['is_catchup'],
                        'att_status': r['att_status'],
                        'att_notes': r['att_notes'],
                    })
            report_slots[slot]['tables'].extend(list(tables.values()))
        else:
            # ── Fallback: use direct attendance records ──
            cur.execute("""
                SELECT a.student_id, a.status as att_status, a.notes as att_notes,
                       s.name as student_name, s.admission_id, s.year_group
                FROM attendance a
                JOIN students s ON s.id=a.student_id
                WHERE a.session_id=%s ORDER BY s.admission_id
            """, (sess['id'],))
            att_rows = rows(cur)
            if att_rows:
                # Group all attendance under one virtual table per session
                report_slots[slot]['tables'].append({
                    'table_no': sess.get('table_no') or '—',
                    'teacher_name': sess['staff_name'] or '—',
                    'subject': sess['staff_subject'] or '—',
                    'max_students': 6,
                    'students': [{
                        'student_id': r['student_id'],
                        'student_name': r['student_name'],
                        'admission_id': r['admission_id'],
                        'year_group': r['year_group'],
                        'is_catchup': False,
                        'att_status': r['att_status'],
                        'att_notes': r['att_notes'],
                    } for r in att_rows]
                })

    cur.close(); conn.close()

    # Build sorted slot list with summary stats
    result = sorted(report_slots.values(), key=lambda x: x['slot'])
    for slot in result:
        all_students = []
        for t in slot['tables']:
            all_students.extend(t['students'])
        unique_ids = set(s['student_id'] for s in all_students if s.get('student_id'))
        slot['total_students'] = len(unique_ids)
        slot['present'] = sum(1 for s in all_students if s.get('att_status') == 'present')
        slot['absent'] = sum(1 for s in all_students if s.get('att_status') == 'absent')
        slot['tables_used'] = len(slot['tables'])

    return jsonify({
        'date': date_str,
        'slots': result,
        'total_tables': 8,
        'max_per_table': 5,
    })


@api_bp.route('/api/session-students/<int:session_id>', methods=['GET'])
@require_auth
def get_session_students(session_id):
    conn = get_conn(); cur = conn.cursor()
    cur.execute("""
        SELECT ss.*, s.name as student_name, s.admission_id, s.year_group,
               s.status as student_status
        FROM session_students ss
        JOIN students s ON s.id=ss.student_id
        WHERE ss.session_id=%s ORDER BY s.admission_id
    """, (session_id,))
    data = rows(cur); cur.close(); conn.close()
    return jsonify(data)

@api_bp.route('/api/session-students/<int:session_id>', methods=['POST'])
@require_auth
def assign_students(session_id):
    """Bulk assign students to a session."""
    d = request.json
    student_ids = d.get('student_ids', [])
    conn = get_conn(); cur = conn.cursor()
    added = 0
    for sid in student_ids:
        cur.execute("""
            INSERT INTO session_students (session_id, student_id, added_by, is_catchup)
            VALUES (%s,%s,%s,%s) ON CONFLICT (session_id, student_id) DO NOTHING
        """, (session_id, sid, session.get('user_id'), d.get('is_catchup', False)))
        if cur.rowcount: added += 1
    conn.commit(); cur.close(); conn.close()
    log_action('edit', 'session_students', session_id)
    return jsonify({'added': added})

@api_bp.route('/api/session-students/<int:session_id>/<int:student_id>', methods=['DELETE'])
@require_auth
def remove_session_student(session_id, student_id):
    conn = get_conn(); cur = conn.cursor()
    cur.execute("DELETE FROM session_students WHERE session_id=%s AND student_id=%s",
                (session_id, student_id))
    conn.commit(); cur.close(); conn.close()
    log_action('edit', 'session_students', session_id)
    return jsonify({'ok': True})

@api_bp.route('/api/session-students/<int:session_id>/capacity', methods=['GET'])
@require_auth
def session_capacity(session_id):
    """Get session student count and capacity info."""
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT COUNT(*) as assigned FROM session_students WHERE session_id=%s", (session_id,))
    assigned = cur.fetchone()['assigned']
    cur.execute("SELECT table_no FROM sessions WHERE id=%s", (session_id,))
    sess = cur.fetchone()
    # Max 6 students per table by default
    capacity = 6
    cur.close(); conn.close()
    return jsonify({'assigned': assigned, 'capacity': capacity, 'spaces': max(0, capacity - assigned)})

# ════════════════════════════════════════════
#  CATCH-UP LESSONS
# ════════════════════════════════════════════
@api_bp.route('/api/catchup', methods=['GET'])
@require_auth
def get_catchup():
    b = branch_scope()
    status = request.args.get('status')
    conn = get_conn(); cur = conn.cursor()
    where = []; params = []
    if b: where.append("c.branch_id=%s"); params.append(b)
    if status: where.append("c.status=%s"); params.append(status)
    wc = ('WHERE '+' AND '.join(where)) if where else ''
    cur.execute(f"""
        SELECT c.*, s.name as student_name, s.admission_id,
               ms.date as missed_date_actual, ms.slot as missed_slot,
               cs.date as catchup_date_actual, cs.slot as catchup_slot,
               b.name as branch_name
        FROM catchup_lessons c
        JOIN students s ON s.id=c.student_id
        JOIN branches b ON b.id=c.branch_id
        LEFT JOIN sessions ms ON ms.id=c.missed_session_id
        LEFT JOIN sessions cs ON cs.id=c.catchup_session_id
        {wc} ORDER BY c.created_at DESC
    """, params)
    data = rows(cur)
    for d in data:
        for f in ['missed_date','scheduled_date','completed_date',
                  'missed_date_actual','catchup_date_actual']:
            if d.get(f): d[f] = str(d[f])
    cur.close(); conn.close()
    return jsonify(data)

@api_bp.route('/api/catchup', methods=['POST'])
@require_auth
def add_catchup():
    d = request.json
    conn = get_conn(); cur = conn.cursor()
    cur.execute("""
        INSERT INTO catchup_lessons
            (student_id, branch_id, missed_session_id, missed_date, subject,
             notified_in_advance, notification_notes, status, notes, created_by)
        VALUES (%s,%s,%s,%s,%s,%s,%s,'owed',%s,%s) RETURNING *
    """, (d['student_id'], d['branch_id'],
          d.get('missed_session_id') or None,
          d['missed_date'], d.get('subject',''),
          d.get('notified_in_advance', False),
          d.get('notification_notes',''),
          d.get('notes',''), session.get('user_id')))
    r = row(cur); conn.commit(); cur.close(); conn.close()
    if r and r.get('missed_date'): r['missed_date'] = str(r['missed_date'])
    log_action('add', 'catchup_lessons', r['id'])
    return jsonify(r), 201

@api_bp.route('/api/catchup/<int:cid>', methods=['PUT'])
@require_auth
def update_catchup(cid):
    d = request.json
    conn = get_conn(); cur = conn.cursor()
    cur.execute("""
        UPDATE catchup_lessons SET
            status=%s, catchup_session_id=%s, scheduled_date=%s,
            completed_date=%s, notes=%s
        WHERE id=%s RETURNING *
    """, (d.get('status','owed'),
          d.get('catchup_session_id') or None,
          d.get('scheduled_date') or None,
          d.get('completed_date') or None,
          d.get('notes',''), cid))
    r = row(cur); conn.commit(); cur.close(); conn.close()
    for f in ['missed_date','scheduled_date','completed_date']:
        if r and r.get(f): r[f] = str(r[f])
    log_action('edit', 'catchup_lessons', cid)
    return jsonify(r)

@api_bp.route('/api/catchup/<int:cid>', methods=['DELETE'])
@require_auth
def delete_catchup(cid):
    conn = get_conn(); cur = conn.cursor()
    cur.execute("DELETE FROM catchup_lessons WHERE id=%s", (cid,))
    conn.commit(); cur.close(); conn.close()
    log_action('delete', 'catchup_lessons', cid)
    return jsonify({'ok': True})

# Auto-create catch-up when student marked absent with notification
@api_bp.route('/api/catchup/from-absence', methods=['POST'])
@require_auth
def catchup_from_absence():
    """Create catch-up record from an absence notification."""
    d = request.json
    conn = get_conn(); cur = conn.cursor()
    # Get session details
    cur.execute("""
        SELECT s.date, s.subject, s.branch_id FROM sessions s WHERE s.id=%s
    """, (d['session_id'],))
    sess = cur.fetchone()
    if not sess:
        cur.close(); conn.close()
        return jsonify({'error': 'Session not found'}), 404
    cur.execute("""
        INSERT INTO catchup_lessons
            (student_id, branch_id, missed_session_id, missed_date, subject,
             notified_in_advance, notification_notes, status, created_by)
        VALUES (%s,%s,%s,%s,%s,TRUE,%s,'owed',%s)
        ON CONFLICT DO NOTHING RETURNING *
    """, (d['student_id'], sess['branch_id'], d['session_id'],
          sess['date'], sess['subject'] or '',
          d.get('notification_notes',''), session.get('user_id')))
    r = row(cur); conn.commit(); cur.close(); conn.close()
    if r and r.get('missed_date'): r['missed_date'] = str(r['missed_date'])
    return jsonify(r or {'already_exists': True}), 201

# ════════════════════════════════════════════
#  SESSION COVER TEACHER
# ════════════════════════════════════════════
@api_bp.route('/api/sessions/<int:sid>/cover', methods=['POST'])
@require_auth
def set_cover_teacher(sid):
    d = request.json
    conn = get_conn(); cur = conn.cursor()
    cur.execute("""
        UPDATE sessions SET cover_staff_id=%s, cover_notes=%s
        WHERE id=%s RETURNING *
    """, (d.get('cover_staff_id') or None, d.get('cover_notes',''), sid))
    r = row(cur); conn.commit(); cur.close(); conn.close()
    if r and r.get('date'): r['date'] = str(r['date'])
    log_action('edit', 'sessions', sid)
    return jsonify(r)

# ════════════════════════════════════════════
#  LESSON REPORTS
# ════════════════════════════════════════════
@api_bp.route('/api/lesson-reports/<int:session_id>', methods=['GET'])
@require_auth
def get_lesson_reports(session_id):
    conn = get_conn(); cur = conn.cursor()
    cur.execute("""
        SELECT lr.*, s.name as student_name, s.admission_id,
               st.name as staff_name, u.name as supervisor_name
        FROM lesson_reports lr
        JOIN students s ON s.id=lr.student_id
        LEFT JOIN staff st ON st.id=lr.staff_id
        LEFT JOIN users u ON u.id=lr.supervisor_id
        WHERE lr.session_id=%s ORDER BY s.admission_id
    """, (session_id,))
    data = rows(cur)
    for d in data:
        if d.get('date'): d['date'] = str(d['date'])
        if d.get('supervisor_checked_at'): d['supervisor_checked_at'] = str(d['supervisor_checked_at'])
    cur.close(); conn.close()
    return jsonify(data)

@api_bp.route('/api/lesson-reports', methods=['POST'])
@require_auth
def save_lesson_report():
    d = request.json
    conn = get_conn(); cur = conn.cursor()
    cur.execute("""
        INSERT INTO lesson_reports
            (session_id, student_id, branch_id, staff_id, date,
             classwork_completed, homework_marked, homework_set,
             diary_entry, www, ebi)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (session_id, student_id) DO UPDATE SET
            classwork_completed=EXCLUDED.classwork_completed,
            homework_marked=EXCLUDED.homework_marked,
            homework_set=EXCLUDED.homework_set,
            diary_entry=EXCLUDED.diary_entry,
            www=EXCLUDED.www, ebi=EXCLUDED.ebi
        RETURNING *
    """, (
        d['session_id'], d['student_id'], d['branch_id'],
        d.get('staff_id'), d.get('date', str(date.today())),
        d.get('classwork_completed',''), d.get('homework_marked', False),
        d.get('homework_set',''), d.get('diary_entry',''),
        d.get('www',''), d.get('ebi','')
    ))
    r = row(cur); conn.commit(); cur.close(); conn.close()
    if r and r.get('date'): r['date'] = str(r['date'])
    log_action('edit', 'lesson_reports', d.get('student_id'))
    return jsonify(r), 201

@api_bp.route('/api/lesson-reports/<int:rid>/supervisor-check', methods=['POST'])
@require_auth
def supervisor_check(rid):
    d = request.json
    conn = get_conn(); cur = conn.cursor()
    cur.execute("""
        UPDATE lesson_reports SET
            supervisor_checked=%s, supervisor_id=%s,
            supervisor_checked_at=NOW(), supervisor_notes=%s
        WHERE id=%s RETURNING *
    """, (
        d.get('checked', True), session.get('user_id'),
        d.get('notes',''), rid
    ))
    r = row(cur); conn.commit(); cur.close(); conn.close()
    if r and r.get('date'): r['date'] = str(r['date'])
    log_action('edit', 'lesson_reports', rid)
    return jsonify(r)

@api_bp.route('/api/lesson-reports/student/<int:student_id>', methods=['GET'])
@require_auth
def get_student_diary(student_id):
    conn = get_conn(); cur = conn.cursor()
    cur.execute("""
        SELECT lr.*, sess.date as session_date, sess.slot,
               st.name as staff_name, b.name as branch_name
        FROM lesson_reports lr
        JOIN sessions sess ON sess.id=lr.session_id
        LEFT JOIN staff st ON st.id=lr.staff_id
        LEFT JOIN branches b ON b.id=lr.branch_id
        WHERE lr.student_id=%s
        ORDER BY sess.date DESC
        LIMIT 50
    """, (student_id,))
    data = rows(cur)
    for d in data:
        if d.get('date'): d['date'] = str(d['date'])
        if d.get('session_date'): d['session_date'] = str(d['session_date'])
    cur.close(); conn.close()
    return jsonify(data)

# ════════════════════════════════════════════
#  TEST RECORDS
# ════════════════════════════════════════════
@api_bp.route('/api/test-records', methods=['GET'])
@require_auth
def get_test_records():
    b = branch_scope()
    student_id = request.args.get('student_id')
    conn = get_conn(); cur = conn.cursor()
    where = []; params = []
    if b: where.append("t.branch_id=%s"); params.append(b)
    if student_id: where.append("t.student_id=%s"); params.append(int(student_id))
    wc = ('WHERE '+' AND '.join(where)) if where else ''
    cur.execute(f"""
        SELECT t.*, s.name as student_name, s.admission_id,
               b.name as branch_name, u.name as recorded_by_name
        FROM test_records t
        JOIN students s ON s.id=t.student_id
        JOIN branches b ON b.id=t.branch_id
        LEFT JOIN users u ON u.id=t.recorded_by
        {wc} ORDER BY t.test_date DESC, t.created_at DESC
    """, params)
    data = rows(cur)
    for d in data:
        if d.get('test_date'): d['test_date'] = str(d['test_date'])
        if d.get('retest_date'): d['retest_date'] = str(d['retest_date'])
        if d.get('score_pct'): d['score_pct'] = float(d['score_pct'])
        if d.get('retest_score_pct'): d['retest_score_pct'] = float(d['retest_score_pct'])
    cur.close(); conn.close()
    return jsonify(data)

@api_bp.route('/api/test-records', methods=['POST'])
@require_auth
def add_test_record():
    d = request.json
    conn = get_conn(); cur = conn.cursor()
    score = float(d['score_pct'])
    passed = score >= 70
    retest_score = float(d['retest_score_pct']) if d.get('retest_score_pct') else None
    retest_passed = (retest_score >= 70) if retest_score is not None else None
    cur.execute("""
        INSERT INTO test_records
            (student_id, branch_id, staff_id, recorded_by,
             subject, book_unit, test_date, score_pct, passed,
             revision_given, retest_date, retest_score_pct, retest_passed,
             action_plan, notes)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        RETURNING *
    """, (
        d['student_id'], d['branch_id'], d.get('staff_id'),
        session.get('user_id'),
        d['subject'], d['book_unit'],
        d.get('test_date', str(date.today())),
        score, passed,
        d.get('revision_given', False),
        d.get('retest_date') or None,
        retest_score, retest_passed,
        d.get('action_plan',''), d.get('notes','')
    ))
    r = row(cur); conn.commit(); cur.close(); conn.close()
    if r:
        if r.get('test_date'): r['test_date'] = str(r['test_date'])
        if r.get('retest_date'): r['retest_date'] = str(r['retest_date'])
        if r.get('score_pct'): r['score_pct'] = float(r['score_pct'])
    log_action('add', 'test_records', r['id'])
    return jsonify(r), 201

@api_bp.route('/api/test-records/<int:tid>', methods=['PUT'])
@require_auth
def update_test_record(tid):
    d = request.json
    conn = get_conn(); cur = conn.cursor()
    score = float(d['score_pct'])
    passed = score >= 70
    retest_score = float(d['retest_score_pct']) if d.get('retest_score_pct') else None
    retest_passed = (retest_score >= 70) if retest_score is not None else None
    cur.execute("""
        UPDATE test_records SET
            subject=%s, book_unit=%s, test_date=%s, score_pct=%s, passed=%s,
            revision_given=%s, retest_date=%s, retest_score_pct=%s, retest_passed=%s,
            action_plan=%s, notes=%s
        WHERE id=%s RETURNING *
    """, (
        d['subject'], d['book_unit'],
        d.get('test_date', str(date.today())), score, passed,
        d.get('revision_given', False),
        d.get('retest_date') or None,
        retest_score, retest_passed,
        d.get('action_plan',''), d.get('notes',''), tid
    ))
    r = row(cur); conn.commit(); cur.close(); conn.close()
    if r:
        if r.get('test_date'): r['test_date'] = str(r['test_date'])
        if r.get('retest_date'): r['retest_date'] = str(r['retest_date'])
        if r.get('score_pct'): r['score_pct'] = float(r['score_pct'])
        if r.get('retest_score_pct'): r['retest_score_pct'] = float(r['retest_score_pct'])
    log_action('edit', 'test_records', tid)
    return jsonify(r)

@api_bp.route('/api/test-records/<int:tid>', methods=['DELETE'])
@require_auth
def delete_test_record(tid):
    conn = get_conn(); cur = conn.cursor()
    cur.execute("DELETE FROM test_records WHERE id=%s", (tid,))
    conn.commit(); cur.close(); conn.close()
    log_action('delete', 'test_records', tid)
    return jsonify({'ok': True})

# Parent portal — test records
@api_bp.route('/api/parent/test-records/<int:student_id>', methods=['GET'])
@require_parent
def parent_test_records(student_id):
    pid = session['parent_id']
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT 1 FROM parent_students WHERE parent_id=%s AND student_id=%s",(pid,student_id))
    if not cur.fetchone(): cur.close(); conn.close(); return jsonify({'error':'Forbidden'}), 403
    cur.execute("""
        SELECT t.subject, t.book_unit, t.test_date, t.score_pct,
               t.passed, t.revision_given, t.retest_date,
               t.retest_score_pct, t.retest_passed, t.action_plan
        FROM test_records t
        WHERE t.student_id=%s ORDER BY t.test_date DESC
    """, (student_id,))
    data = rows(cur)
    for d in data:
        if d.get('test_date'): d['test_date'] = str(d['test_date'])
        if d.get('retest_date'): d['retest_date'] = str(d['retest_date'])
        if d.get('score_pct'): d['score_pct'] = float(d['score_pct'])
        if d.get('retest_score_pct'): d['retest_score_pct'] = float(d['retest_score_pct'])
    cur.close(); conn.close()
    return jsonify(data)

# Parent portal — diary
@api_bp.route('/api/parent/diary/<int:student_id>', methods=['GET'])
@require_parent
def parent_diary(student_id):
    pid = session['parent_id']
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT 1 FROM parent_students WHERE parent_id=%s AND student_id=%s",(pid,student_id))
    if not cur.fetchone(): cur.close(); conn.close(); return jsonify({'error':'Forbidden'}), 403
    cur.execute("""
        SELECT lr.diary_entry, lr.homework_set, sess.date, sess.slot,
               st.name as staff_name
        FROM lesson_reports lr
        JOIN sessions sess ON sess.id=lr.session_id
        LEFT JOIN staff st ON st.id=lr.staff_id
        WHERE lr.student_id=%s AND lr.diary_entry IS NOT NULL
              AND lr.diary_entry != ''
        ORDER BY sess.date DESC LIMIT 20
    """, (student_id,))
    data = rows(cur)
    for d in data:
        if d.get('date'): d['date'] = str(d['date'])
    cur.close(); conn.close()
    return jsonify(data)

# ════════════════════════════════════════════
#  HQ TRANSFERS
# ════════════════════════════════════════════
@api_bp.route('/api/hq-transfers', methods=['GET'])
@require_auth
def get_hq_transfers():
    b = branch_scope()
    conn = get_conn(); cur = conn.cursor()
    where = []; params = []
    if b: where.append("t.branch_id=%s"); params.append(b)
    wc = ('WHERE '+' AND '.join(where)) if where else ''
    cur.execute(f"""
        SELECT t.*, b.name as branch_name, u.name as recorded_by_name
        FROM hq_transfers t
        JOIN branches b ON b.id=t.branch_id
        LEFT JOIN users u ON u.id=t.recorded_by
        {wc} ORDER BY t.transfer_date DESC, t.created_at DESC
    """, params)
    data = rows(cur)
    for d in data:
        if d.get('transfer_date'): d['transfer_date'] = str(d['transfer_date'])
        if d.get('amount'): d['amount'] = float(d['amount'])
    cur.close(); conn.close()
    return jsonify(data)

@api_bp.route('/api/hq-transfers/summary', methods=['GET'])
@require_auth
def hq_transfer_summary():
    conn = get_conn(); cur = conn.cursor()
    cur.execute("""
        SELECT b.id, b.name as branch_name,
            COALESCE(SUM(p.amount) FILTER (WHERE p.method='cash'), 0) as cash_collected,
            COALESCE(SUM(t.amount), 0) as transferred_to_hq,
            COALESCE(SUM(p.amount) FILTER (WHERE p.method='cash'), 0) -
            COALESCE(SUM(t.amount), 0) as held_at_branch
        FROM branches b
        LEFT JOIN payments p ON p.branch_id=b.id
        LEFT JOIN hq_transfers t ON t.branch_id=b.id
        GROUP BY b.id, b.name ORDER BY b.name
    """)
    data = rows(cur)
    for d in data:
        for k in ['cash_collected','transferred_to_hq','held_at_branch']:
            if d.get(k) is not None: d[k] = float(d[k])
    cur.close(); conn.close()
    return jsonify(data)

@api_bp.route('/api/hq-transfers', methods=['POST'])
@require_auth
def add_hq_transfer():
    d = request.json
    conn = get_conn(); cur = conn.cursor()
    cur.execute("""
        INSERT INTO hq_transfers (branch_id, amount, transfer_date, method, reference, notes, recorded_by)
        VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING *
    """, (d['branch_id'], d['amount'],
          d.get('transfer_date', str(date.today())),
          d.get('method','cash'), d.get('reference',''),
          d.get('notes',''), session.get('user_id')))
    r = row(cur); conn.commit()
    if r:
        if r.get('transfer_date'): r['transfer_date'] = str(r['transfer_date'])
        if r.get('amount'): r['amount'] = float(r['amount'])
    cur.close(); conn.close()
    log_action('add', 'hq_transfers', r['id'])
    return jsonify(r), 201

@api_bp.route('/api/hq-transfers/<int:tid>', methods=['DELETE'])
@require_auth
def delete_hq_transfer(tid):
    conn = get_conn(); cur = conn.cursor()
    cur.execute("DELETE FROM hq_transfers WHERE id=%s", (tid,))
    conn.commit(); cur.close(); conn.close()
    log_action('delete', 'hq_transfers', tid)
    return jsonify({'ok': True})

# ════════════════════════════════════════════
#  FINANCIAL — PAYMENT SUMMARY
# ════════════════════════════════════════════
@api_bp.route('/api/payments/summary', methods=['GET'])
@require_auth
def payment_summary():
    b = branch_scope()
    month = request.args.get('month')
    conn = get_conn(); cur = conn.cursor()
    where = []; params = []
    if b: where.append("branch_id=%s"); params.append(b)
    if month: where.append("TO_CHAR(payment_date,'YYYY-MM')=%s"); params.append(month)
    wc = ('WHERE '+' AND '.join(where)) if where else ''

    # Summary by method
    cur.execute(f"""
        SELECT method,
            COUNT(*) as count,
            SUM(amount) as total
        FROM payments {wc}
        GROUP BY method ORDER BY total DESC
    """, params)
    by_method = rows(cur)
    for r in by_method:
        if r.get('total'): r['total'] = float(r['total'])

    # Monthly breakdown by method
    cur.execute(f"""
        SELECT TO_CHAR(payment_date,'YYYY-MM') as month,
            method, SUM(amount) as total, COUNT(*) as count
        FROM payments {wc}
        GROUP BY TO_CHAR(payment_date,'YYYY-MM'), method
        ORDER BY month DESC, total DESC
    """, params)
    by_month = rows(cur)
    for r in by_month:
        if r.get('total'): r['total'] = float(r['total'])

    # Overall total
    cur.execute(f"SELECT SUM(amount) as total, COUNT(*) as count FROM payments {wc}", params)
    overall = row(cur)
    if overall and overall.get('total'): overall['total'] = float(overall['total'])

    cur.close(); conn.close()
    return jsonify({
        'by_method': by_method,
        'by_month': by_month,
        'overall': overall or {'total': 0, 'count': 0}
    })


@api_bp.route('/api/me/change-password', methods=['POST'])
@require_auth
def change_password():
    d = request.json
    current = d.get('current_password','')
    new_pw = d.get('new_password','')
    if not current or not new_pw:
        return jsonify({'error': 'Current and new password are required'}), 400
    if len(new_pw) < 6:
        return jsonify({'error': 'New password must be at least 6 characters'}), 400
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT password_hash FROM users WHERE id=%s", (session['user_id'],))
    u = cur.fetchone()
    if not u:
        cur.close(); conn.close()
        return jsonify({'error': 'User not found'}), 404
    from werkzeug.security import check_password_hash, generate_password_hash
    if not check_password_hash(u['password_hash'], current):
        cur.close(); conn.close()
        return jsonify({'error': 'Current password is incorrect'}), 400
    cur.execute("UPDATE users SET password_hash=%s WHERE id=%s",
                (generate_password_hash(new_pw), session['user_id']))
    conn.commit(); cur.close(); conn.close()
    log_action('edit', 'users', session['user_id'])
    return jsonify({'ok': True})


@api_bp.route('/api/users/<int:uid>/reset-password', methods=['POST'])
@require_roles('super_admin', 'branch_manager', 'head_of_centre')
def reset_user_password(uid):
    d = request.json
    new_pw = d.get('new_password', '')
    if not new_pw or len(new_pw) < 6:
        return jsonify({'error': 'Password must be at least 6 characters'}), 400
    from werkzeug.security import generate_password_hash
    conn = get_conn(); cur = conn.cursor()
    cur.execute("UPDATE users SET password_hash=%s WHERE id=%s RETURNING id, name, email",
                (generate_password_hash(new_pw), uid))
    r = row(cur); conn.commit(); cur.close(); conn.close()
    if not r:
        return jsonify({'error': 'User not found'}), 404
    log_action('edit', 'users', uid)
    return jsonify({'ok': True, 'name': r['name']})


# ════════════════════════════════════════════
#  MANAGEMENT REPORTS
# ════════════════════════════════════════════
def academic_year_bounds(year_start=None):
    """Return start and end dates for an academic year (Sep-Aug)."""
    from datetime import date
    today = date.today()
    if not year_start:
        year_start = today.year if today.month >= 9 else today.year - 1
    return f"{year_start}-09-01", f"{year_start+1}-08-31"

@api_bp.route('/api/reports/management/summary', methods=['GET'])
@require_roles('super_admin','branch_manager','head_of_centre')
def mgmt_summary():
    b = branch_scope()
    year_start = request.args.get('year_start', type=int)
    yr_from, yr_to = academic_year_bounds(year_start)
    conn = get_conn(); cur = conn.cursor()
    bp = (b,) if b else ()
    bw = "AND s.branch_id=%s" if b else ""
    bw2 = "WHERE branch_id=%s" if b else ""
    bw3 = "WHERE s.branch_id=%s" if b else ""

    # Students
    cur.execute(f"SELECT COUNT(*) as c FROM students WHERE status='active' {bw2.replace('WHERE','AND') if bw2 else ''}", bp)
    active_students = cur.fetchone()['c']
    cur.execute(f"SELECT COUNT(*) as c FROM students WHERE created_at::date BETWEEN %s AND %s {bw}", (yr_from, yr_to)+bp)
    new_enrolments = cur.fetchone()['c']

    # Sessions this year
    cur.execute(f"SELECT COUNT(*) as c FROM sessions s WHERE s.date BETWEEN %s AND %s {bw}", (yr_from, yr_to)+bp)
    total_sessions = cur.fetchone()['c']

    # Attendance this year
    cur.execute(f"""SELECT
        COUNT(*) FILTER (WHERE a.status='present') as present,
        COUNT(*) FILTER (WHERE a.status='absent') as absent,
        COUNT(*) as total
        FROM attendance a JOIN sessions s ON s.id=a.session_id
        WHERE s.date BETWEEN %s AND %s {bw}""", (yr_from, yr_to)+bp)
    att = cur.fetchone()
    att_rate = round(att['present']/att['total']*100) if att['total'] else 0

    # Staff hours this year
    cur.execute(f"""SELECT
        COUNT(DISTINCT sa.staff_id) as staff_count,
        ROUND(SUM(EXTRACT(EPOCH FROM (sa.sign_out - sa.sign_in))/3600)::numeric, 1) as total_hours
        FROM staff_attendance sa
        WHERE sa.date BETWEEN %s AND %s
        AND sa.sign_in IS NOT NULL AND sa.sign_out IS NOT NULL
        AND sa.status='present' {bw.replace('s.branch_id','sa.branch_id')}""", (yr_from, yr_to)+bp)
    staff_hrs = cur.fetchone()

    # Fees
    cur.execute(f"SELECT COALESCE(SUM(amount),0) as t FROM invoices WHERE status!='paid' {bw2.replace('branch_id','branch_id')}", bp)
    outstanding = float(cur.fetchone()['t'])
    cur.execute(f"SELECT COALESCE(SUM(amount),0) as t FROM payments WHERE payment_date BETWEEN %s AND %s {bw.replace('s.branch_id','branch_id')}", (yr_from, yr_to)+bp)
    collected = float(cur.fetchone()['t'])

    # Catch-ups
    cur.execute(f"SELECT status, COUNT(*) as c FROM catchup_lessons WHERE 1=1 {bw.replace('s.branch_id','branch_id')} GROUP BY status", bp)
    catchups = {r['status']:r['c'] for r in cur.fetchall()}

    # Tests
    cur.execute(f"""SELECT
        COUNT(*) as total,
        COUNT(*) FILTER (WHERE passed=true) as passed,
        COUNT(*) FILTER (WHERE passed=false) as failed
        FROM test_records WHERE 1=1 {bw.replace('s.branch_id','branch_id')}""", bp)
    tests = cur.fetchone()

    cur.close(); conn.close()
    return jsonify({
        'academic_year': f"{year_start or (date.today().year if date.today().month>=9 else date.today().year-1)}/{(year_start or (date.today().year if date.today().month>=9 else date.today().year-1))+1}",
        'yr_from': yr_from, 'yr_to': yr_to,
        'active_students': active_students,
        'new_enrolments': new_enrolments,
        'total_sessions': total_sessions,
        'att_rate': att_rate,
        'att_present': att['present'],
        'att_absent': att['absent'],
        'att_total': att['total'],
        'staff_count': staff_hrs['staff_count'] or 0,
        'total_staff_hours': float(staff_hrs['total_hours'] or 0),
        'fees_outstanding': outstanding,
        'fees_collected': collected,
        'catchups_owed': catchups.get('owed',0),
        'catchups_scheduled': catchups.get('scheduled',0),
        'catchups_completed': catchups.get('completed',0),
        'tests_total': tests['total'],
        'tests_passed': tests['passed'],
        'tests_failed': tests['failed'],
    })

@api_bp.route('/api/reports/management/daily', methods=['GET'])
@require_roles('super_admin','branch_manager','head_of_centre')
def mgmt_daily():
    b = branch_scope()
    report_date = request.args.get('date', str(date.today()))
    conn = get_conn(); cur = conn.cursor()
    bp = (b,) if b else ()
    bw = "AND s.branch_id=%s" if b else ""

    # Sessions
    cur.execute(f"""SELECT s.*, b.name as branch_name, st.name as staff_name,
        cv.name as cover_name,
        COUNT(a.id) FILTER (WHERE a.status='present') as present,
        COUNT(a.id) FILTER (WHERE a.status='absent') as absent,
        COUNT(a.id) as total_marked
        FROM sessions s
        JOIN branches b ON b.id=s.branch_id
        LEFT JOIN staff st ON st.id=s.staff_id
        LEFT JOIN staff cv ON cv.id=s.cover_staff_id
        LEFT JOIN attendance a ON a.session_id=s.id
        WHERE s.date=%s {bw}
        GROUP BY s.id, b.name, st.name, cv.name
        ORDER BY s.slot""", (report_date,)+bp)
    sessions = rows(cur)

    # Staff attendance
    cur.execute(f"""SELECT sa.*, st.name as staff_name, st.role,
        ROUND(EXTRACT(EPOCH FROM (sa.sign_out - sa.sign_in))/3600::numeric, 2) as hours
        FROM staff_attendance sa
        JOIN staff st ON st.id=sa.staff_id
        WHERE sa.date=%s {bw.replace('s.branch_id','sa.branch_id')}
        ORDER BY sa.sign_in""", (report_date,)+bp)
    staff_att = rows(cur)
    for r in staff_att:
        if r.get('sign_in'): r['sign_in'] = str(r['sign_in'])
        if r.get('sign_out'): r['sign_out'] = str(r['sign_out'])
        if r.get('hours'): r['hours'] = float(r['hours'])

    # Lesson reports
    cur.execute(f"""SELECT lr.student_id, lr.supervisor_checked,
        s.name as student_name, sess.slot
        FROM lesson_reports lr
        JOIN students s ON s.id=lr.student_id
        JOIN sessions sess ON sess.id=lr.session_id
        WHERE lr.date=%s {bw.replace('s.branch_id','lr.branch_id')}""", (report_date,)+bp)
    lesson_reports = rows(cur)

    # Catch-ups scheduled for this date
    cur.execute(f"""SELECT c.*, s.name as student_name, s.admission_id
        FROM catchup_lessons c JOIN students s ON s.id=c.student_id
        WHERE c.scheduled_date=%s {bw.replace('s.branch_id','c.branch_id')}""", (report_date,)+bp)
    catchups = rows(cur)
    for r in catchups:
        if r.get('missed_date'): r['missed_date'] = str(r['missed_date'])
        if r.get('scheduled_date'): r['scheduled_date'] = str(r['scheduled_date'])

    cur.close(); conn.close()
    return jsonify({
        'date': report_date,
        'sessions': sessions,
        'staff_attendance': staff_att,
        'lesson_reports': lesson_reports,
        'catchups': catchups,
    })

@api_bp.route('/api/reports/management/monthly', methods=['GET'])
@require_roles('super_admin','branch_manager','head_of_centre')
def mgmt_monthly():
    b = branch_scope()
    month = request.args.get('month', date.today().strftime('%Y-%m'))
    conn = get_conn(); cur = conn.cursor()
    bp = (b,) if b else ()
    bw = "AND s.branch_id=%s" if b else ""

    # Weekly breakdown
    cur.execute(f"""SELECT
        DATE_TRUNC('week', s.date)::date as week_start,
        COUNT(DISTINCT s.id) as sessions,
        COUNT(a.id) FILTER (WHERE a.status='present') as present,
        COUNT(a.id) FILTER (WHERE a.status='absent') as absent
        FROM sessions s
        LEFT JOIN attendance a ON a.session_id=s.id
        WHERE TO_CHAR(s.date,'YYYY-MM')=%s {bw}
        GROUP BY DATE_TRUNC('week', s.date) ORDER BY week_start""", (month,)+bp)
    weekly = rows(cur)
    for r in weekly:
        if r.get('week_start'): r['week_start'] = str(r['week_start'])

    # Staff hours
    cur.execute(f"""SELECT st.name, st.role,
        COUNT(sa.id) as sessions_worked,
        ROUND(SUM(EXTRACT(EPOCH FROM (sa.sign_out-sa.sign_in))/3600)::numeric,1) as hours
        FROM staff_attendance sa JOIN staff st ON st.id=sa.staff_id
        WHERE TO_CHAR(sa.date,'YYYY-MM')=%s
        AND sa.sign_in IS NOT NULL AND sa.sign_out IS NOT NULL
        AND sa.status='present' {bw.replace('s.branch_id','sa.branch_id')}
        GROUP BY st.name, st.role ORDER BY hours DESC NULLS LAST""", (month,)+bp)
    staff_hours = rows(cur)
    for r in staff_hours:
        if r.get('hours'): r['hours'] = float(r['hours'])

    # New enrolments
    cur.execute(f"""SELECT COUNT(*) as c FROM students
        WHERE TO_CHAR(created_at,'YYYY-MM')=%s
        {bw.replace('s.branch_id','branch_id').replace('AND ','AND ')}""", (month,)+bp)
    new_students = cur.fetchone()['c']

    # Fees
    cur.execute(f"""SELECT
        COALESCE(SUM(amount) FILTER (WHERE status='paid'),0) as collected,
        COALESCE(SUM(amount) FILTER (WHERE status!='paid'),0) as outstanding
        FROM invoices WHERE month=%s {bw.replace('s.branch_id','branch_id')}""", (month,)+bp)
    fees = cur.fetchone()

    # Tests
    cur.execute(f"""SELECT
        COUNT(*) as total,
        COUNT(*) FILTER (WHERE passed=true) as passed,
        subject
        FROM test_records
        WHERE TO_CHAR(test_date,'YYYY-MM')=%s {bw.replace('s.branch_id','branch_id')}
        GROUP BY subject ORDER BY subject""", (month,)+bp)
    tests = rows(cur)

    # Catch-ups
    cur.execute(f"""SELECT status, COUNT(*) as c FROM catchup_lessons
        WHERE TO_CHAR(created_at,'YYYY-MM')=%s {bw.replace('s.branch_id','branch_id')}
        GROUP BY status""", (month,)+bp)
    catchups = {r['status']:r['c'] for r in cur.fetchall()}

    cur.close(); conn.close()
    return jsonify({
        'month': month,
        'weekly_breakdown': weekly,
        'staff_hours': staff_hours,
        'new_enrolments': new_students,
        'fees_collected': float(fees['collected'] or 0),
        'fees_outstanding': float(fees['outstanding'] or 0),
        'tests_by_subject': tests,
        'catchups': catchups,
    })

@api_bp.route('/api/reports/management/yearly', methods=['GET'])
@require_roles('super_admin','branch_manager','head_of_centre')
def mgmt_yearly():
    b = branch_scope()
    year_start = request.args.get('year_start', type=int)
    yr_from, yr_to = academic_year_bounds(year_start)
    conn = get_conn(); cur = conn.cursor()
    bp = (b,) if b else ()
    bw = "AND s.branch_id=%s" if b else ""

    # Monthly trend
    cur.execute(f"""SELECT TO_CHAR(s.date,'YYYY-MM') as month,
        COUNT(DISTINCT s.id) as sessions,
        COUNT(a.id) FILTER (WHERE a.status='present') as present,
        COUNT(a.id) FILTER (WHERE a.status='absent') as absent,
        COUNT(a.id) as total
        FROM sessions s LEFT JOIN attendance a ON a.session_id=s.id
        WHERE s.date BETWEEN %s AND %s {bw}
        GROUP BY TO_CHAR(s.date,'YYYY-MM') ORDER BY month""", (yr_from, yr_to)+bp)
    monthly_trend = rows(cur)

    # Staff hours per teacher for year
    cur.execute(f"""SELECT st.name, st.role,
        COUNT(sa.id) as sessions_worked,
        ROUND(SUM(EXTRACT(EPOCH FROM (sa.sign_out-sa.sign_in))/3600)::numeric,1) as total_hours
        FROM staff_attendance sa JOIN staff st ON st.id=sa.staff_id
        WHERE sa.date BETWEEN %s AND %s
        AND sa.sign_in IS NOT NULL AND sa.sign_out IS NOT NULL
        {bw.replace('s.branch_id','sa.branch_id').replace('AND ','AND ')}
        GROUP BY st.name, st.role ORDER BY total_hours DESC NULLS LAST""", (yr_from, yr_to)+bp)
    staff_yearly = rows(cur)
    for r in staff_yearly:
        if r.get('total_hours'): r['total_hours'] = float(r['total_hours'])

    # Monthly fees
    cur.execute(f"""SELECT month,
        COALESCE(SUM(amount) FILTER (WHERE status='paid'),0) as collected,
        COALESCE(SUM(amount) FILTER (WHERE status!='paid'),0) as outstanding
        FROM invoices WHERE issued BETWEEN %s AND %s
        {bw.replace('s.branch_id','branch_id').replace('AND ','AND ')}
        GROUP BY month ORDER BY month""", (yr_from, yr_to)+bp)
    monthly_fees = rows(cur)
    for r in monthly_fees:
        r['collected'] = float(r['collected'])
        r['outstanding'] = float(r['outstanding'])

    cur.close(); conn.close()
    return jsonify({
        'academic_year': f"{year_start or (date.today().year if date.today().month>=9 else date.today().year-1)}/{(year_start or (date.today().year if date.today().month>=9 else date.today().year-1))+1}",
        'yr_from': yr_from, 'yr_to': yr_to,
        'monthly_trend': monthly_trend,
        'staff_yearly': staff_yearly,
        'monthly_fees': monthly_fees,
    })

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
