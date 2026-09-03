from flask import Blueprint, request, jsonify, session
from werkzeug.security import generate_password_hash
from models.db import get_conn
from psycopg2.extras import RealDictCursor
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
    if role in ('super_admin', 'reports_viewer', 'head_of_branches'):
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
@require_roles('super_admin','branch_manager','head_of_centre','head_of_branches')
def add_branch():
    d = request.json
    conn = get_conn(); cur = conn.cursor()
    cur.execute("""
        INSERT INTO branches (name, prefix, address, phone, email, website, status)
        VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING *
    """, (d['name'], d['prefix'].upper(), d.get('address',''), d.get('phone',''), d.get('email',''), d.get('website',''), d.get('status','active')))
    r = row(cur); conn.commit(); cur.close(); conn.close()
    log_action('add', 'branches', r['id'])
    return jsonify(r), 201

@api_bp.route('/api/branches/<int:bid>', methods=['PUT'])
@require_roles('super_admin','branch_manager','head_of_centre','head_of_branches')
def update_branch(bid):
    d = request.json
    conn = get_conn(); cur = conn.cursor()
    cur.execute("""
        UPDATE branches SET name=%s, prefix=%s, address=%s, phone=%s, email=%s, website=%s, status=%s
        WHERE id=%s RETURNING *
    """, (d['name'], d['prefix'].upper(), d.get('address',''), d.get('phone',''), d.get('email',''), d.get('website',''), d.get('status','active'), bid))
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
#  STUDENT AGREED SLOTS
# ════════════════════════════════════════════
@api_bp.route('/api/students/<int:sid>/agreed-slots', methods=['GET'])
@require_auth
def get_agreed_slots(sid):
    conn = get_conn(); cur = conn.cursor()
    cur.execute("""
        SELECT sas.*, bs.day_of_week, bs.slot_start, bs.slot_end, bs.status as slot_status
        FROM student_agreed_slots sas
        JOIN branch_schedule bs ON bs.id = sas.branch_schedule_id
        WHERE sas.student_id = %s
        ORDER BY bs.day_of_week, bs.slot_start
    """, (sid,))
    rows = cur.fetchall() or []
    cur.close(); conn.close()
    return jsonify(rows)

@api_bp.route('/api/students/<int:sid>/agreed-slots', methods=['POST'])
@require_roles('super_admin','branch_manager','head_of_centre','head_of_branches','admin')
def save_agreed_slots(sid):
    """Replace all agreed slots for a student.
    Accepts {slots: [{schedule_id, subject}], effective_from} OR legacy {schedule_ids: [...]}."""
    d = request.json or {}
    effective_from = d.get('effective_from') or 'today'
    # Support new format {slots: [{schedule_id, subject}]} and legacy {schedule_ids: [...]}
    slots = d.get('slots')  # new format
    if slots is None:
        ids = d.get('schedule_ids', [])
        slots = [{'schedule_id': i, 'subject': ''} for i in ids]
    conn = get_conn(); cur = conn.cursor()
    try:
        cur.execute("DELETE FROM student_agreed_slots WHERE student_id=%s", (sid,))
        for sl in slots:
            cur.execute("""
                INSERT INTO student_agreed_slots (student_id, branch_schedule_id, effective_from, subject)
                VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING
            """, (sid, sl['schedule_id'], effective_from, sl.get('subject') or None))
        conn.commit()
        log_action('edit', 'students', sid)
    except Exception as e:
        conn.rollback(); cur.close(); conn.close()
        return jsonify({'error': str(e)}), 400
    cur.close(); conn.close()
    return jsonify({'ok': True, 'saved': len(slots)})

# ════════════════════════════════════════════
#  BRANCH SCHEDULE
# ════════════════════════════════════════════
@api_bp.route('/api/branch-schedule', methods=['GET'])
@require_auth
def get_branch_schedule():
    conn = get_conn(); cur = conn.cursor()
    b = branch_scope()
    if b:
        cur.execute("""
            SELECT bs.*, br.name as branch_name
            FROM branch_schedule bs
            JOIN branches br ON br.id = bs.branch_id
            WHERE bs.branch_id = %s
            ORDER BY bs.day_of_week, bs.slot_start
        """, (b,))
    else:
        cur.execute("""
            SELECT bs.*, br.name as branch_name
            FROM branch_schedule bs
            JOIN branches br ON br.id = bs.branch_id
            ORDER BY br.name, bs.day_of_week, bs.slot_start
        """)
    rows = cur.fetchall() or []
    cur.close(); conn.close()
    return jsonify(rows)

@api_bp.route('/api/branch-schedule', methods=['POST'])
@require_roles('super_admin','branch_manager','head_of_centre','head_of_branches')
def add_branch_schedule():
    d = request.json
    conn = get_conn(); cur = conn.cursor()
    try:
        cur.execute("""
            INSERT INTO branch_schedule (branch_id, day_of_week, slot_start, slot_end,
                status, effective_from, notes)
            VALUES (%s, %s, %s, %s, 'active', %s, %s)
            RETURNING *
        """, (d['branch_id'], d['day_of_week'], d['slot_start'], d['slot_end'],
              d.get('effective_from') or 'today', d.get('notes','')))
        r = row(cur); conn.commit()
    except Exception as e:
        conn.rollback(); cur.close(); conn.close()
        return jsonify({'error': str(e)}), 400
    cur.close(); conn.close()
    log_action('add', 'branch_schedule', r['id'] if r else 0)
    return jsonify(r), 201

@api_bp.route('/api/branch-schedule/<int:sid>', methods=['PUT'])
@require_roles('super_admin','branch_manager','head_of_centre','head_of_branches')
def update_branch_schedule(sid):
    d = request.json
    conn = get_conn(); cur = conn.cursor()
    try:
        cur.execute("""
            UPDATE branch_schedule SET day_of_week=%s, slot_start=%s, slot_end=%s,
                effective_from=%s, notes=%s
            WHERE id=%s RETURNING *
        """, (d['day_of_week'], d['slot_start'], d['slot_end'],
              d.get('effective_from') or 'today', d.get('notes',''), sid))
        r = row(cur); conn.commit()
    except Exception as e:
        conn.rollback(); cur.close(); conn.close()
        return jsonify({'error': str(e)}), 400
    cur.close(); conn.close()
    log_action('edit', 'branch_schedule', sid)
    return jsonify(r)

@api_bp.route('/api/branch-schedule/<int:sid>/close', methods=['PATCH'])
@require_roles('super_admin','branch_manager','head_of_centre','head_of_branches')
def close_branch_schedule(sid):
    d = request.json or {}
    effective_to = d.get('effective_to') or 'today'
    conn = get_conn(); cur = conn.cursor()
    cur.execute("""
        UPDATE branch_schedule SET status='closed', effective_to=%s
        WHERE id=%s RETURNING *
    """, (effective_to, sid))
    r = row(cur); conn.commit(); cur.close(); conn.close()
    log_action('edit', 'branch_schedule', sid)
    return jsonify(r)

@api_bp.route('/api/branch-schedule/<int:sid>/reopen', methods=['PATCH'])
@require_roles('super_admin','branch_manager','head_of_centre','head_of_branches')
def reopen_branch_schedule(sid):
    conn = get_conn(); cur = conn.cursor()
    cur.execute("""
        UPDATE branch_schedule SET status='active', effective_to=NULL
        WHERE id=%s RETURNING *
    """, (sid,))
    r = row(cur); conn.commit(); cur.close(); conn.close()
    log_action('edit', 'branch_schedule', sid)
    return jsonify(r)

@api_bp.route('/api/branch-schedule/<int:sid>', methods=['DELETE'])
@require_roles('super_admin','branch_manager','head_of_centre','head_of_branches')
def delete_branch_schedule(sid):
    conn = get_conn(); cur = conn.cursor()
    cur.execute("DELETE FROM branch_schedule WHERE id=%s", (sid,))
    conn.commit(); cur.close(); conn.close()
    log_action('delete', 'branch_schedule', sid)
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
            cur.execute("""SELECT s.*, b.name as branch_name,
                s.monthly_fee
                FROM students s JOIN branches b ON b.id=s.branch_id
                WHERE s.branch_id=%s AND (s.name ILIKE %s OR s.admission_id ILIKE %s)
                ORDER BY s.admission_id""", (b, f'%{q}%', f'%{q}%'))
        else:
            cur.execute("""SELECT s.*, b.name as branch_name,
                s.monthly_fee
                FROM students s JOIN branches b ON b.id=s.branch_id
                WHERE s.branch_id=%s ORDER BY s.admission_id""", (b,))
    else:
        if q:
            cur.execute("""SELECT s.*, b.name as branch_name,
                s.monthly_fee
                FROM students s JOIN branches b ON b.id=s.branch_id
                WHERE s.name ILIKE %s OR s.admission_id ILIKE %s
                ORDER BY s.admission_id""", (f'%{q}%', f'%{q}%'))
        else:
            cur.execute("""SELECT s.*, b.name as branch_name,
                s.monthly_fee
                FROM students s JOIN branches b ON b.id=s.branch_id ORDER BY s.admission_id""")
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
    dob_raw = d.get('date_of_birth')
    dob = dob_raw.strip() if isinstance(dob_raw, str) and dob_raw.strip() else None
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
        'monthly_fee': d.get('monthly_fee') if d.get('monthly_fee') not in (None,'','0') else None,
        'parent_contact': d.get('carer1_mobile') or d.get('parent_contact',''),
        'status': d.get('status','active'), 'notes': d.get('notes','')
    }


# ════════════════════════════════════════════
#  BULK IMPORT INACTIVE STUDENTS
# ════════════════════════════════════════════
@api_bp.route('/api/students/import-inactive', methods=['POST'])
@require_roles('super_admin','branch_manager','head_of_centre','head_of_branches','admin')
def import_inactive_students():
    rows_in = request.json
    if not isinstance(rows_in, list):
        return jsonify({'error': 'Expected a list of student rows'}), 400

    conn = get_conn(); cur = conn.cursor()

    cur.execute("SELECT id, name, prefix FROM branches WHERE status='active'")
    all_branches = cur.fetchall() or []
    branch_map = {}
    for b in all_branches:
        branch_map[b['name'].strip().lower()] = b
        branch_map[b['prefix'].strip().lower()] = b

    cur.execute("SELECT LOWER(TRIM(carer1_email)) AS e FROM students WHERE carer1_email IS NOT NULL AND carer1_email != ''")
    existing_emails = {r['e'] for r in (cur.fetchall() or []) if r['e']}
    cur.execute("SELECT LOWER(TRIM(carer2_email)) AS e FROM students WHERE carer2_email IS NOT NULL AND carer2_email != ''")
    existing_emails |= {r['e'] for r in (cur.fetchall() or []) if r['e']}

    imported = []; skipped = []; errors = []

    for i, row_in in enumerate(rows_in):
        row_num = i + 1
        name        = (row_in.get('name') or row_in.get('Student Name') or '').strip()
        carer_name  = (row_in.get('carer_name') or row_in.get('Parent/Carer Name') or '').strip()
        carer_email = (row_in.get('carer_email') or row_in.get('Parent Email') or '').strip().lower()
        year_group  = (row_in.get('year_group') or row_in.get('Year Group') or '').strip()
        branch_key  = (row_in.get('branch') or row_in.get('Branch') or '').strip().lower()
        notes       = (row_in.get('notes') or row_in.get('Notes') or '').strip()

        if not name:
            errors.append({'row': row_num, 'reason': 'Missing student name'})
            continue

        branch = branch_map.get(branch_key)
        if not branch:
            avail = ', '.join(b['name'] for b in all_branches)
            errors.append({'row': row_num, 'name': name, 'reason': 'Branch "' + branch_key + '" not found. Available: ' + avail})
            continue

        if carer_email and carer_email in existing_emails:
            skipped.append({'row': row_num, 'name': name, 'email': carer_email, 'reason': 'Email already in system'})
            continue

        try:
            branch_id    = branch['id']
            admission_id = next_admission_id(conn, branch_id)
            parts    = carer_name.split(' ', 1) if carer_name else ['', '']
            c1_first = parts[0]
            c1_last  = parts[1] if len(parts) > 1 else ''
            cur.execute("""
                INSERT INTO students (branch_id, admission_id, name, year_group,
                    carer1_first_name, carer1_last_name, carer1_email,
                    parent_contact, status, notes)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'inactive', %s)
                RETURNING id, admission_id, name
            """, (branch_id, admission_id, name, year_group,
                  c1_first, c1_last, carer_email, carer_email, notes))
            r = cur.fetchone()
            conn.commit()
            if carer_email:
                existing_emails.add(carer_email)
            imported.append({'row': row_num, 'name': name,
                             'admission_id': r['admission_id'] if r else admission_id,
                             'branch': branch['name']})
            log_action('add', 'students', r['id'] if r else 0)
        except Exception as ex:
            conn.rollback()
            errors.append({'row': row_num, 'name': name, 'reason': str(ex)})

    cur.close(); conn.close()
    return jsonify({'imported': imported, 'skipped': skipped, 'errors': errors})

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

@api_bp.route('/api/sessions/auto-create', methods=['POST'])
@require_roles('super_admin','branch_manager','head_of_centre','head_of_branches','supervisor')
def auto_create_sessions():
    """Create sessions for a date from branch_schedule (skips slots that already exist)."""
    from datetime import datetime
    d = request.json or {}
    branch_id = d.get('branch_id')
    date_str = d.get('date')
    if not branch_id or not date_str:
        return jsonify({'error': 'branch_id and date required'}), 400
    day_names = ['monday','tuesday','wednesday','thursday','friday','saturday','sunday']
    day_of_week = day_names[datetime.strptime(date_str, '%Y-%m-%d').weekday()]
    conn = get_conn(); cur = conn.cursor()
    try:
        cur.execute("""
            SELECT id, slot_start, slot_end,
                   ROW_NUMBER() OVER (ORDER BY slot_start) AS session_num
            FROM branch_schedule
            WHERE branch_id = %s AND day_of_week = %s AND status = 'active'
              AND (effective_from IS NULL OR effective_from <= %s)
              AND (effective_to IS NULL OR effective_to >= %s)
            ORDER BY slot_start
        """, (branch_id, day_of_week, date_str, date_str))
        slots = cur.fetchall()
        if not slots:
            return jsonify({'created': 0, 'message': f'No active schedule slots for {day_of_week}'})
        created = 0; sessions_out = []
        day_cap = day_of_week.capitalize()
        for s in slots:
            bs_id = s['id']
            raw_start = s['slot_start']; raw_end = s['slot_end']
            # Convert time objects to HH:MM (psycopg2 returns datetime.time)
            start = raw_start.strftime('%H:%M') if hasattr(raw_start, 'strftime') else str(raw_start)[:5]
            end   = raw_end.strftime('%H:%M')   if hasattr(raw_end,   'strftime') else str(raw_end)[:5]
            num = int(s['session_num'])
            slot_text = f"{day_cap} Session {num} ({start}–{end})"
            # Also check old-format variants (hyphen, 'Slot' keyword) to avoid duplicates
            slot_hyphen = f"{day_cap} Session {num} ({start}-{end})"
            slot_old1   = f"{day_cap} Slot {num} ({start}–{end})"
            slot_old2   = f"{day_cap} Slot {num} ({start}-{end})"
            cur.execute("""SELECT id FROM sessions WHERE branch_id=%s AND date=%s
                            AND slot IN (%s,%s,%s,%s)""",
                        (branch_id, date_str, slot_text, slot_hyphen, slot_old1, slot_old2))
            if cur.fetchone():
                continue
            cur.execute("""
                INSERT INTO sessions (branch_id, date, slot, table_no, branch_schedule_id)
                VALUES (%s, %s, %s, 1, %s) RETURNING id
            """, (branch_id, date_str, slot_text, bs_id))
            sid = cur.fetchone()['id']
            sessions_out.append({'id': sid, 'slot': slot_text})
            created += 1
        conn.commit()
        log_action('add', 'sessions', 0)
        return jsonify({'created': created, 'sessions': sessions_out, 'day_of_week': day_of_week})
    except Exception as e:
        conn.rollback(); return jsonify({'error': str(e)}), 400
    finally:
        cur.close(); conn.close()

@api_bp.route('/api/sessions/deduplicate', methods=['POST'])
@require_roles('super_admin','branch_manager','head_of_centre','head_of_branches')
def deduplicate_sessions():
    """Remove duplicate sessions (same branch+date, same slot time range).
    Keeps the session with the lowest id; deletes the rest (and their allocations)."""
    d = request.json or {}
    branch_id = d.get('branch_id')
    date_str = d.get('date')
    conn = get_conn(); cur = conn.cursor()
    try:
        where = []; params = []
        if branch_id: where.append("branch_id=%s"); params.append(branch_id)
        if date_str:  where.append("date=%s");      params.append(date_str)
        wc = ("WHERE " + " AND ".join(where)) if where else ""
        # Normalise slot by replacing en-dash and hyphen variants so we group correctly
        cur.execute(f"""
            SELECT id, branch_id, date,
                   REGEXP_REPLACE(REGEXP_REPLACE(slot, '–', '-'), 'Slot', 'Session') AS norm_slot
            FROM sessions {wc}
            ORDER BY branch_id, date, norm_slot, id
        """, params)
        all_sess = cur.fetchall()
        seen = {}; to_delete = []
        for s in all_sess:
            key = (s['branch_id'], str(s['date']), s['norm_slot'])
            if key in seen:
                to_delete.append(s['id'])
            else:
                seen[key] = s['id']
        deleted = 0
        for did in to_delete:
            cur.execute("DELETE FROM table_allocations WHERE session_id=%s", (did,))
            cur.execute("DELETE FROM attendance WHERE session_id=%s", (did,))
            cur.execute("DELETE FROM sessions WHERE id=%s", (did,))
            deleted += 1
        conn.commit()
        return jsonify({'deleted': deleted, 'kept': len(seen)})
    except Exception as e:
        conn.rollback(); return jsonify({'error': str(e)}), 400
    finally:
        cur.close(); conn.close()

@api_bp.route('/api/sessions/<int:sid>', methods=['PUT'])
@require_auth
def update_session(sid):
    d = request.json or {}
    conn = get_conn(); cur = conn.cursor()
    fields = []
    params = []
    if 'staff_id' in d:
        fields.append("staff_id=%s")
        params.append(d['staff_id'] if d['staff_id'] else None)
    if 'subject' in d:
        fields.append("subject=%s"); params.append(d['subject'])
    if 'slot' in d:
        fields.append("slot=%s"); params.append(d['slot'])
    if not fields:
        return jsonify({'error': 'Nothing to update'}), 400
    params.append(sid)
    cur.execute(f"UPDATE sessions SET {', '.join(fields)} WHERE id=%s", params)
    conn.commit(); cur.close(); conn.close()
    log_action('edit', 'sessions', sid)
    return jsonify({'ok': True})

@api_bp.route('/api/sessions/<int:sid>', methods=['DELETE'])
@require_auth
def delete_session(sid):
    conn = get_conn(); cur = conn.cursor()
    cur.execute("DELETE FROM sessions WHERE id=%s", (sid,))
    conn.commit(); cur.close(); conn.close()
    log_action('delete', 'sessions', sid)
    return jsonify({'ok': True})

@api_bp.route('/api/sessions/<int:sid>/add-student', methods=['POST'])
@require_auth
def add_student_to_session(sid):
    d = request.json or {}
    student_id = d.get('student_id')
    if not student_id:
        return jsonify({'error': 'student_id required'}), 400
    conn = get_conn(); cur = conn.cursor()
    # Find the table_allocation for this session
    cur.execute("SELECT id FROM table_allocations WHERE session_id=%s LIMIT 1", (sid,))
    alloc = cur.fetchone()
    if alloc:
        cur.execute("""
            INSERT INTO table_allocation_students (allocation_id, student_id, is_catchup)
            VALUES (%s, %s, false) ON CONFLICT DO NOTHING
        """, (alloc['id'], student_id))
    else:
        # Fallback: add to session_students
        cur.execute("""
            INSERT INTO session_students (session_id, student_id, added_by, is_catchup)
            VALUES (%s, %s, %s, false) ON CONFLICT DO NOTHING
        """, (sid, student_id, session.get('user_id')))
    conn.commit(); cur.close(); conn.close()
    log_action('edit', 'sessions', sid)
    return jsonify({'ok': True})



# ════════════════════════════════════════════
#  AUTO-PLAN SESSION
# ════════════════════════════════════════════
@api_bp.route('/api/sessions/auto-plan', methods=['POST'])
@require_roles('super_admin','branch_manager','head_of_centre','head_of_branches')
def auto_plan_session():
    """Auto-generate tables for a date+slot based on student timetables."""
    d = request.json
    plan_date = d.get('date')
    plan_slot = d.get('slot')
    replace = d.get('replace', False)  # If True, delete existing sessions first
    b = branch_scope()

    conn = get_conn(); cur = conn.cursor()

    # Check if sessions already exist for this date+slot
    bp = (b,) if b else ()
    bw = "AND s.branch_id=%s" if b else ""
    cur.execute(f"""
        SELECT id, table_no, subject FROM sessions
        WHERE date=%s AND slot=%s {bw}
        ORDER BY table_no
    """, (plan_date, plan_slot) + bp)
    existing = rows(cur)

    if existing and not replace:
        cur.close(); conn.close()
        return jsonify({
            'exists': True,
            'existing_count': len(existing),
            'message': f'{len(existing)} table(s) already exist for this slot.'
        })

    # If replace, delete existing sessions (cascades to allocations/students)
    if existing and replace:
        for sess in existing:
            cur.execute("DELETE FROM sessions WHERE id=%s", (sess['id'],))
        conn.commit()

    # Get students timetabled for this slot
    day_of_week = date.fromisoformat(plan_date).weekday()
    if day_of_week == 5:
        day_type = 'saturday'
    elif day_of_week == 6:
        day_type = 'sunday'
    else:
        day_type = 'weekday'

    bw2 = "AND st.branch_id=%s" if b else ""
    cur.execute(f"""
        SELECT st.id as student_id, st.name, st.admission_id, st.year_group, st.branch_id,
               stt.slot, stt.subject, stt.day_type
        FROM student_timetable stt
        JOIN students st ON st.id=stt.student_id
        WHERE stt.slot=%s AND stt.day_type=%s AND st.status='active' {bw2}
        ORDER BY st.year_group, st.name
    """, (plan_slot, day_type) + bp)
    timetabled = rows(cur)

    if not timetabled:
        cur.close(); conn.close()
        return jsonify({'tables_created': 0, 'students_placed': 0, 'unplaced': [], 'message': 'No students timetabled for this slot.'})

    # Year group → band mapping
    def get_band(year_group):
        if not year_group: return None
        yg = year_group.strip().lower().replace('year ', '').replace('yr ', '').strip()
        try:
            yr = int(yg)
            if yr <= 5: return 'primary'
            elif yr <= 8: return 'lower_secondary'
            elif yr <= 11: return 'upper_secondary'
            else: return 'sixth_form'
        except:
            return None

    band_labels = {
        'primary': 'Yr 1-5',
        'lower_secondary': 'Yr 6-8',
        'upper_secondary': 'Yr 9-11',
        'sixth_form': 'Yr 12-13'
    }

    # Group students by subject + band
    groups = {}
    unplaced = []
    for stu in timetabled:
        band = get_band(stu['year_group'])
        subject = stu.get('subject') or 'Other'
        if band is None:
            unplaced.append({'student_id': stu['student_id'], 'name': stu['name'],
                           'admission_id': stu['admission_id'], 'reason': 'No year group set'})
            continue
        key = (subject, band, stu['branch_id'])
        if key not in groups:
            groups[key] = []
        groups[key].append(stu)

    # Create tables from groups (max 5 per table)
    MAX_PER_TABLE = 5
    tables_to_create = []
    for (subject, band, branch_id), students in groups.items():
        # Split into chunks of MAX_PER_TABLE
        for i in range(0, len(students), MAX_PER_TABLE):
            chunk = students[i:i+MAX_PER_TABLE]
            tables_to_create.append({
                'subject': subject,
                'band': band,
                'band_label': band_labels.get(band, band),
                'branch_id': branch_id,
                'students': chunk
            })

    # Sort tables: by subject then band for consistent table numbering
    tables_to_create.sort(key=lambda t: (t['subject'], t['band']))

    # Create sessions and allocations
    tables_created = 0
    students_placed = 0
    table_no = 1
    for table in tables_to_create:
        # Create session
        cur.execute("""
            INSERT INTO sessions (branch_id, date, slot, subject, table_no)
            VALUES (%s,%s,%s,%s,%s) RETURNING id
        """, (table['branch_id'], plan_date, plan_slot, table['subject'], table_no))
        sess_id = cur.fetchone()['id']

        # Create table allocation
        cur.execute("""
            INSERT INTO table_allocations (session_id, table_no, subject, max_students, notes)
            VALUES (%s,%s,%s,%s,%s) RETURNING id
        """, (sess_id, table_no, table['subject'], MAX_PER_TABLE,
              f"{table['subject']} · {table['band_label']}"))
        alloc_id = cur.fetchone()['id']

        # Add students to allocation
        for stu in table['students']:
            cur.execute("""
                INSERT INTO table_allocation_students (allocation_id, student_id)
                VALUES (%s,%s) ON CONFLICT DO NOTHING
            """, (alloc_id, stu['student_id']))
            students_placed += 1

        tables_created += 1
        table_no += 1

    conn.commit()
    log_action('add', 'sessions', 'auto-plan')
    cur.close(); conn.close()

    return jsonify({
        'exists': False,
        'tables_created': tables_created,
        'students_placed': students_placed,
        'unplaced': unplaced,
        'message': f'Created {tables_created} tables for {students_placed} students.'
        + (f' {len(unplaced)} student(s) could not be placed (no year group set).' if unplaced else '')
    })


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
    student_id = request.args.get('student_id', type=int)
    if b:         where.append("i.branch_id=%s"); params.append(b)
    if status and status != 'all': where.append("i.status=%s"); params.append(status)
    if student_id: where.append("i.student_id=%s"); params.append(student_id)
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
    """Generate monthly fee invoices for all active students who have an agreed monthly fee set."""
    d = request.json
    month = d.get('month', date.today().strftime('%Y-%m'))
    b = branch_scope()
    conn = get_conn(); cur = conn.cursor()
    # Only include students with a monthly_fee set on their profile
    if b:
        cur.execute("SELECT id, branch_id, monthly_fee FROM students WHERE branch_id=%s AND status='active' AND monthly_fee IS NOT NULL AND monthly_fee > 0", (b,))
    else:
        cur.execute("SELECT id, branch_id, monthly_fee FROM students WHERE status='active' AND monthly_fee IS NOT NULL AND monthly_fee > 0")
    sts = rows(cur)
    added = 0
    skipped = 0
    for st in sts:
        cur.execute("""
            INSERT INTO invoices (student_id, branch_id, month, amount, fee_type, description, status, issued)
            VALUES (%s,%s,%s,%s,'monthly_fee','Monthly fee','due', CURRENT_DATE)
            ON CONFLICT (student_id, month, fee_type) DO NOTHING
        """, (st['id'], st['branch_id'], month, float(st['monthly_fee'])))
        if cur.rowcount:
            added += 1
        else:
            skipped += 1
    conn.commit(); cur.close(); conn.close()
    log_action('add', 'invoices', 'batch')
    return jsonify({'added': added, 'skipped': skipped, 'total_students': len(sts)})

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
@require_roles('super_admin','branch_manager','head_of_centre','head_of_branches')
def get_users():
    conn = get_conn(); cur = conn.cursor()
    role = session.get('role')
    branch_id = session.get('branch_id')
    if role in ('branch_manager', 'head_of_centre') and branch_id:
        cur.execute("""
            SELECT u.id, u.name, u.email, u.role, u.branch_id, u.status, u.last_login,
                   b.name as branch_name
            FROM users u LEFT JOIN branches b ON b.id=u.branch_id
            WHERE u.branch_id=%s ORDER BY u.name
        """, (branch_id,))
    else:
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
@require_roles('super_admin','branch_manager','head_of_centre','head_of_branches')
def add_user():
    d = request.json
    caller_role = session.get('role')
    caller_branch = session.get('branch_id')
    PRIVILEGED_ROLES = ('super_admin', 'branch_manager')
    if caller_role in ('branch_manager', 'head_of_centre'):
        if d.get('role') in PRIVILEGED_ROLES:
            return jsonify({'error': 'Cannot assign that role'}), 403
        if not caller_branch or str(d.get('branch_id')) != str(caller_branch):
            return jsonify({'error': 'Can only create users for your own branch'}), 403
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
@require_roles('super_admin','branch_manager','head_of_centre','head_of_branches')
def update_user(uid):
    d = request.json
    caller_role = session.get('role')
    caller_branch = session.get('branch_id')
    PRIVILEGED_ROLES = ('super_admin', 'branch_manager')
    if caller_role in ('branch_manager', 'head_of_centre'):
        conn = get_conn(); cur = conn.cursor()
        cur.execute("SELECT branch_id, role FROM users WHERE id=%s", (uid,))
        target = row(cur); cur.close(); conn.close()
        if not target or str(target.get('branch_id')) != str(caller_branch):
            return jsonify({'error': 'Cannot edit users outside your branch'}), 403
        if target.get('role') in PRIVILEGED_ROLES:
            return jsonify({'error': 'Cannot edit privileged users'}), 403
        if d.get('role') in PRIVILEGED_ROLES:
            return jsonify({'error': 'Cannot assign that role'}), 403
        if str(d.get('branch_id')) != str(caller_branch):
            return jsonify({'error': 'Cannot move user to another branch'}), 403
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
@require_roles('super_admin','branch_manager','head_of_centre','head_of_branches')
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
@require_roles('super_admin','branch_manager','head_of_centre','head_of_branches')
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
@require_roles('super_admin','branch_manager','head_of_centre','head_of_branches')
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
@require_roles('super_admin','branch_manager','head_of_centre','head_of_branches')
def delete_parent_user(pid):
    conn = get_conn(); cur = conn.cursor()
    cur.execute("DELETE FROM parent_users WHERE id=%s", (pid,))
    conn.commit(); cur.close(); conn.close()
    log_action('delete', 'parent_users', pid)
    return jsonify({'ok': True})

@api_bp.route('/api/students/<int:sid>/attendance-summary', methods=['GET'])
@require_auth
def student_attendance_summary(sid):
    """Get attendance summary for a specific student."""
    conn = get_conn(); cur = conn.cursor()
    cur.execute("""
        SELECT 
            COUNT(*) as total_sessions,
            SUM(CASE WHEN a.status='present' THEN 1 ELSE 0 END) as present,
            SUM(CASE WHEN a.status='absent' THEN 1 ELSE 0 END) as absent,
            s.slot, s.date, s.subject, a.status, a.notes
        FROM attendance a
        JOIN sessions s ON s.id=a.session_id
        WHERE a.student_id=%s
        GROUP BY s.slot, s.date, s.subject, a.status, a.notes
        ORDER BY s.date DESC
    """, (sid,))
    records = rows(cur)
    # Overall summary
    cur.execute("""
        SELECT 
            COUNT(*) as total,
            SUM(CASE WHEN a.status='present' THEN 1 ELSE 0 END) as present,
            SUM(CASE WHEN a.status='absent' THEN 1 ELSE 0 END) as absent
        FROM attendance a WHERE a.student_id=%s
    """, (sid,))
    summary = rows(cur)
    for r in records:
        if r.get('date'): r['date'] = str(r['date'])
    cur.close(); conn.close()
    return jsonify({'records': records, 'summary': summary[0] if summary else {}})

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
    import datetime as _dt
    b = branch_scope()
    date_from = request.args.get('date_from')
    date_to   = request.args.get('date_to')
    if not date_from and not date_to:
        today = _dt.date.today()
        yr = today.year if today.month >= 9 else today.year - 1
        date_from = f'{yr}-09-01'
        date_to   = f'{yr+1}-08-31'
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

    # Session count — date-filtered
    sess_date = []
    sess_p = list(params)
    if date_from: sess_date.append("s.date>=%s"); sess_p.append(date_from)
    if date_to:   sess_date.append("s.date<=%s"); sess_p.append(date_to)
    sess_extra = (' AND ' + ' AND '.join(sess_date)) if sess_date else ''
    cur.execute(f"SELECT COUNT(*) as c FROM sessions s {bw3}" + sess_extra, tuple(sess_p))
    session_count = cur.fetchone()['c']

    # Attendance rate — date-filtered
    att_where_parts = []
    att_params = list(params)
    if b: att_where_parts.append("s.branch_id=%s")
    if date_from: att_where_parts.append("s.date>=%s"); att_params.append(date_from)
    if date_to:   att_where_parts.append("s.date<=%s"); att_params.append(date_to)
    att_where = ('WHERE ' + ' AND '.join(att_where_parts)) if att_where_parts else ''
    cur.execute(f"""
        SELECT COUNT(*) FILTER (WHERE a.status='present') as present,
               COUNT(*) as total
        FROM attendance a
        JOIN sessions s ON s.id=a.session_id
        {att_where}
    """, tuple(att_params))
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

    # Year group breakdown — active students only
    if b:
        cur.execute("SELECT year_group, COUNT(*) as c FROM students s WHERE s.branch_id=%s AND s.status='active' GROUP BY year_group ORDER BY year_group", (b,))
    else:
        cur.execute("SELECT year_group, COUNT(*) as c FROM students s WHERE s.status='active' GROUP BY year_group ORDER BY year_group")
    year_groups = rows(cur)

    # Subject breakdown — date-filtered
    subj_where_parts = []
    subj_params = []
    if b: subj_where_parts.append("s.branch_id=%s"); subj_params.append(b)
    if date_from: subj_where_parts.append("s.date>=%s"); subj_params.append(date_from)
    if date_to:   subj_where_parts.append("s.date<=%s"); subj_params.append(date_to)
    subj_where = ('WHERE ' + ' AND '.join(subj_where_parts)) if subj_where_parts else ''
    cur.execute(f"SELECT subject, COUNT(*) as c FROM sessions s {subj_where} GROUP BY subject ORDER BY c DESC", tuple(subj_params))
    subjects = rows(cur)

    # Outstanding invoices
    if b:
        cur.execute("SELECT SUM(amount - COALESCE(amount_paid,0)) as total FROM invoices WHERE status!='paid' AND branch_id=%s", (b,))
    else:
        cur.execute("SELECT SUM(amount - COALESCE(amount_paid,0)) as total FROM invoices WHERE status!='paid'")
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
        'date_from': date_from,
        'date_to': date_to,
    })


@api_bp.route('/api/reports/enrolment', methods=['GET'])
@require_auth
def report_enrolment():
    b = branch_scope()
    conn = get_conn(); cur = conn.cursor()

    # Get agreed slots (primary source)
    b_filter = "AND stu.branch_id=%s" if b else ""
    b_params = [b] if b else []
    cur.execute(f"""
        SELECT stu.id as student_id, stu.name, stu.admission_id, stu.year_group,
               bs.day_of_week as day,
               (LEFT(bs.slot_start::text,5) || '–' || LEFT(bs.slot_end::text,5)) as slot,
               sas.subject
        FROM student_agreed_slots sas
        JOIN branch_schedule bs ON bs.id = sas.branch_schedule_id
        JOIN students stu ON stu.id = sas.student_id
        WHERE stu.status='active' AND bs.status='active'
          AND (bs.effective_from IS NULL OR bs.effective_from <= CURRENT_DATE)
          AND (bs.effective_to IS NULL OR bs.effective_to >= CURRENT_DATE)
          {b_filter}
        ORDER BY stu.admission_id, bs.day_of_week, bs.slot_start
    """, b_params)
    agreed_rows = rows(cur)

    agreed_student_ids = set(r['student_id'] for r in agreed_rows)

    # Fallback: old timetable (student_timetable) for students without agreed slots
    b_filter2 = "AND t.branch_id=%s" if b else ""
    b_params2 = [b] if b else []
    cur.execute(f"""
        SELECT stu.id as student_id, stu.name, stu.admission_id, stu.year_group,
               t.day_type as day, t.slot, t.subject
        FROM student_timetable t
        JOIN students stu ON stu.id = t.student_id
        WHERE stu.status='active' {b_filter2}
        ORDER BY stu.admission_id, t.day_type, t.slot
    """, b_params2)
    old_rows = [r for r in rows(cur) if r['student_id'] not in agreed_student_ids]

    all_rows = agreed_rows + old_rows

    # Build per-student structure
    from collections import defaultdict
    student_map = {}
    for r in all_rows:
        sid = r['student_id']
        if sid not in student_map:
            student_map[sid] = {
                'id': sid, 'name': r['name'],
                'admission_id': r['admission_id'],
                'year_group': r['year_group'] or '',
                'sessions': []
            }
        student_map[sid]['sessions'].append({
            'day': r['day'], 'slot': r['slot'], 'subject': r['subject'] or ''
        })

    students_out = sorted(student_map.values(), key=lambda x: x['admission_id'] or '')

    # Students with NO sessions at all
    cur.execute(f"""
        SELECT id, name, admission_id, year_group FROM students
        WHERE status='active' {"AND branch_id=%s" if b else ""}
        ORDER BY admission_id
    """, b_params)
    all_active = rows(cur)
    no_session_ids = set(s['id'] for s in all_active) - set(student_map.keys())
    for s in all_active:
        if s['id'] in no_session_ids:
            students_out.append({'id': s['id'], 'name': s['name'],
                'admission_id': s['admission_id'], 'year_group': s['year_group'] or '',
                'sessions': []})

    # Summary stats
    subject_counts = defaultdict(set)
    day_counts = defaultdict(set)
    slot_counts = defaultdict(set)
    for r in all_rows:
        subject_counts[r['subject'] or 'Unknown'].add(r['student_id'])
        day_counts[r['day']].add(r['student_id'])
        slot_counts[r['slot']].add(r['student_id'])

    cur.close(); conn.close()
    return jsonify({
        'students': students_out,
        'total_active': len(all_active),
        'total_with_sessions': len(student_map),
        'total_no_sessions': len(no_session_ids),
        'by_subject': sorted([{'subject': k, 'count': len(v)} for k,v in subject_counts.items()], key=lambda x: -x['count']),
        'by_day': [{'day': d, 'count': len(day_counts[d])} for d in ['monday','tuesday','wednesday','thursday','friday','saturday','sunday'] if d in day_counts],
        'by_slot': sorted([{'slot': k, 'count': len(v)} for k,v in slot_counts.items()], key=lambda x: x['slot']),
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
            # Without session — check for duplicate on same staff + date first
            cur.execute(
                "SELECT id FROM staff_attendance WHERE staff_id=%s AND date=%s AND session_id IS NULL",
                (d['staff_id'], d['date'])
            )
            existing = cur.fetchone()
            if existing and not d.get('force'):
                cur.close(); conn.close()
                return jsonify({'error': 'duplicate', 'message': 'A timesheet record already exists for this staff member on this date. Are you sure you want to add another?', 'existing_id': existing['id']}), 409
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

@api_bp.route('/api/staff-attendance/dedup', methods=['POST'])
@require_roles('super_admin','branch_manager','head_of_centre','head_of_branches')
def dedup_staff_attendance():
    """Delete exact duplicate staff_attendance rows (same staff_id, date, sign_in, sign_out, session_id IS NULL), keeping the lowest id."""
    conn = get_conn(); cur = conn.cursor()
    cur.execute("""
        DELETE FROM staff_attendance
        WHERE id IN (
            SELECT id FROM (
                SELECT id,
                       ROW_NUMBER() OVER (PARTITION BY staff_id, date, sign_in, sign_out
                                          ORDER BY id) AS rn
                FROM staff_attendance
                WHERE session_id IS NULL
            ) t WHERE rn > 1
        )
        RETURNING id
    """)
    deleted = [r['id'] for r in cur.fetchall()]
    conn.commit(); cur.close(); conn.close()
    return jsonify({'deleted': len(deleted), 'ids': deleted})

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
        SELECT DISTINCT s.id as student_id, s.name as student_name, s.admission_id, s.year_group,
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
        SELECT s.id as student_id, s.name as student_name, s.admission_id, s.year_group,
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
        SELECT s.id as student_id, s.name as student_name, s.admission_id, s.year_group,
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

@api_bp.route('/api/table-allocations/<int:aid>', methods=['PUT'])
@require_auth
def update_table_alloc(aid):
    d = request.json
    conn = get_conn(); cur = conn.cursor()
    cur.execute("""
        UPDATE table_allocations
        SET teacher_id=%s, max_students=%s, notes=%s
        WHERE id=%s
    """, (d.get('staff_id'), d.get('max_students',5), d.get('notes',''), aid))
    # Also update session staff_id
    cur.execute("UPDATE sessions SET staff_id=%s WHERE id=(SELECT session_id FROM table_allocations WHERE id=%s)", 
                (d.get('staff_id'), aid))
    conn.commit(); cur.close(); conn.close()
    log_action('edit','table_allocations',aid)
    return jsonify({'ok': True})

@api_bp.route('/api/table-allocations/<int:aid>/students', methods=['POST'])
@require_auth
def add_table_student(aid):
    d = request.json
    student_ids = d.get('student_ids', [])
    conn = get_conn(); cur = conn.cursor()
    # Get session_id for this allocation to check cross-table duplicates
    cur.execute("SELECT session_id, table_no FROM table_allocations WHERE id=%s", (aid,))
    alloc = row(cur)
    if not alloc:
        cur.close(); conn.close()
        return jsonify({'error': 'Allocation not found'}), 404
    added = 0
    blocked = []
    for sid in student_ids:
        # Check if student already on another table in this session
        cur.execute("""
            SELECT ta.table_no FROM table_allocation_students tas
            JOIN table_allocations ta ON ta.id=tas.allocation_id
            WHERE ta.session_id=%s AND tas.student_id=%s AND ta.id!=%s
        """, (alloc['session_id'], sid, aid))
        conflict = row(cur)
        if conflict:
            blocked.append({'student_id': sid, 'table_no': conflict['table_no']})
            continue
        cur.execute("""
            INSERT INTO table_allocation_students (allocation_id, student_id, is_catchup)
            VALUES (%s,%s,%s) ON CONFLICT DO NOTHING
        """, (aid, sid, d.get('is_catchup', False)))
        if cur.rowcount: added += 1
    conn.commit(); cur.close(); conn.close()
    if blocked:
        return jsonify({'added': added, 'blocked': blocked,
                        'error': f'{len(blocked)} student(s) already on another table in this session'}), 409
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
    retest_score = float(d['retest_score_pct']) if d.get('retest_score_pct') else None
    cur.execute("""
        INSERT INTO test_records
            (student_id, branch_id, staff_id, recorded_by,
             subject, book_unit, test_date, score_pct,
             revision_given, retest_date, retest_score_pct,
             action_plan, notes)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        RETURNING *
    """, (
        d['student_id'], d['branch_id'], d.get('staff_id'),
        session.get('user_id'),
        d['subject'], d['book_unit'],
        d.get('test_date', str(date.today())),
        score,
        d.get('revision_given', False),
        d.get('retest_date') or None,
        retest_score,
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
    retest_score = float(d['retest_score_pct']) if d.get('retest_score_pct') else None
    cur.execute("""
        UPDATE test_records SET
            subject=%s, book_unit=%s, test_date=%s, score_pct=%s,
            revision_given=%s, retest_date=%s, retest_score_pct=%s,
            action_plan=%s, notes=%s
        WHERE id=%s RETURNING *
    """, (
        d['subject'], d['book_unit'],
        d.get('test_date', str(date.today())), score,
        d.get('revision_given', False),
        d.get('retest_date') or None,
        retest_score,
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
            COALESCE(p.cash_collected, 0) as cash_collected,
            COALESCE(t.transferred_to_hq, 0) as transferred_to_hq,
            COALESCE(p.cash_collected, 0) - COALESCE(t.transferred_to_hq, 0) as held_at_branch
        FROM branches b
        LEFT JOIN (
            SELECT branch_id, SUM(amount) FILTER (WHERE method='cash') as cash_collected
            FROM payments GROUP BY branch_id
        ) p ON p.branch_id = b.id
        LEFT JOIN (
            SELECT branch_id, SUM(amount) as transferred_to_hq
            FROM hq_transfers GROUP BY branch_id
        ) t ON t.branch_id = b.id
        ORDER BY b.name
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
@require_roles('super_admin', 'branch_manager', 'head_of_centre', 'head_of_branches')
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
@require_roles('super_admin','branch_manager','head_of_centre','head_of_branches')
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
    cur.execute(f"SELECT COUNT(*) as c FROM students WHERE created_at::date BETWEEN %s AND %s {bw.replace('s.branch_id','branch_id')}", (yr_from, yr_to)+bp)
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
        ROUND(COALESCE(SUM(EXTRACT(EPOCH FROM (sa.sign_out - sa.sign_in))/3600),0)::numeric, 1) as total_hours
        FROM staff_attendance sa
        WHERE sa.date BETWEEN %s AND %s
        AND sa.sign_in IS NOT NULL AND sa.sign_out IS NOT NULL
        AND sa.sign_out > sa.sign_in
        AND sa.status='present' {bw.replace('s.branch_id','sa.branch_id')}""", (yr_from, yr_to)+bp)
    staff_hrs = cur.fetchone()

    # Fees
    cur.execute(f"SELECT COALESCE(SUM(amount - COALESCE(amount_paid,0)),0) as t FROM invoices WHERE status!='paid' {'AND branch_id=%s' if b else ''}", bp)
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
@require_roles('super_admin','branch_manager','head_of_centre','head_of_branches')
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
@require_roles('super_admin','branch_manager','head_of_centre','head_of_branches')
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
@require_roles('super_admin','branch_manager','head_of_centre','head_of_branches')
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


@api_bp.route('/api/dashboard/activity', methods=['GET'])
@require_roles('super_admin','branch_manager','head_of_centre','supervisor','admin')
def dashboard_activity():
    conn = get_conn(); cur = conn.cursor()
    b = branch_scope()
    params = (b,) if b else ()
    branch_filter = "AND branch_id=%s" if b else ""
    cur.execute(f"""
        SELECT id, user_name, action, table_name, record_id, timestamp, NULL as subject_name, NULL as session_subject
        FROM audit_log
        WHERE 1=1 {branch_filter}
        ORDER BY timestamp DESC
        LIMIT 20
    """, params)
    events = rows(cur)
    for e in events:
        if e.get('timestamp'): e['timestamp'] = str(e['timestamp'])
    cur.close(); conn.close()
    return jsonify(events)


# ── MEETING NOTES ──
NOTES_ROLES = ('super_admin','branch_manager','head_of_centre','supervisor','admin')

@api_bp.route('/api/meeting-notes', methods=['GET'])
@require_roles(*NOTES_ROLES)
def get_meeting_notes():
    role = session.get('role')
    branch_id = session.get('branch_id')
    conn = get_conn(); cur = conn.cursor()
    if role == 'super_admin':
        bid = request.args.get('branch_id')
        if bid:
            cur.execute("""
                SELECT mn.*, s.name as student_name, s.admission_id,
                       u.name as recorded_by_name
                FROM meeting_notes mn
                JOIN students s ON s.id=mn.student_id
                LEFT JOIN users u ON u.id=mn.recorded_by
                WHERE mn.branch_id=%s ORDER BY mn.meeting_date DESC, mn.created_at DESC
            """, (bid,))
        else:
            cur.execute("""
                SELECT mn.*, s.name as student_name, s.admission_id,
                       u.name as recorded_by_name
                FROM meeting_notes mn
                JOIN students s ON s.id=mn.student_id
                LEFT JOIN users u ON u.id=mn.recorded_by
                ORDER BY mn.meeting_date DESC, mn.created_at DESC
            """)
    else:
        bid2 = branch_scope()
        if bid2:
            cur.execute("""
                SELECT mn.*, s.name as student_name, s.admission_id,
                       u.name as recorded_by_name
                FROM meeting_notes mn
                JOIN students s ON s.id=mn.student_id
                LEFT JOIN users u ON u.id=mn.recorded_by
                WHERE mn.branch_id=%s ORDER BY mn.meeting_date DESC, mn.created_at DESC
            """, (bid2,))
        else:
            cur.execute("""
                SELECT mn.*, s.name as student_name, s.admission_id,
                       u.name as recorded_by_name
                FROM meeting_notes mn
                JOIN students s ON s.id=mn.student_id
                LEFT JOIN users u ON u.id=mn.recorded_by
                ORDER BY mn.meeting_date DESC, mn.created_at DESC
            """)
    data = rows(cur)
    for d in data:
        if d.get('meeting_date'): d['meeting_date'] = str(d['meeting_date'])
        if d.get('created_at'): d['created_at'] = str(d['created_at'])
    cur.close(); conn.close()
    return jsonify(data)

@api_bp.route('/api/meeting-notes', methods=['POST'])
@require_roles(*NOTES_ROLES)
def add_meeting_note():
    role = session.get('role')
    branch_id = session.get('branch_id')
    d = request.get_json()
    # branch_manager/etc can only add to their own branch
    raw_branch = d.get('branch_id') or branch_id or 0
    note_branch = int(raw_branch) if raw_branch else 0
    if role != 'super_admin' and note_branch != branch_id:
        return jsonify({'error': 'Cannot add note to another branch'}), 403
    conn = get_conn(); cur = conn.cursor()
    cur.execute("""
        INSERT INTO meeting_notes (student_id, branch_id, recorded_by,
            meeting_date, category, notes, shared_parent)
        VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING *
    """, (d['student_id'], note_branch, session.get('user_id'),
          d.get('meeting_date') or None, d.get('category','General'),
          d['notes'], d.get('shared_parent', False)))
    row = cur.fetchone()
    conn.commit(); cur.close(); conn.close()
    if row.get('meeting_date'): row['meeting_date'] = str(row['meeting_date'])
    if row.get('created_at'): row['created_at'] = str(row['created_at'])
    return jsonify(dict(row))

@api_bp.route('/api/meeting-notes/<int:nid>', methods=['PUT'])
@require_roles(*NOTES_ROLES)
def update_meeting_note(nid):
    role = session.get('role')
    branch_id = session.get('branch_id')
    d = request.get_json()
    conn = get_conn(); cur = conn.cursor()
    # Check note belongs to this branch (non-super_admin)
    if role != 'super_admin':
        cur.execute("SELECT branch_id FROM meeting_notes WHERE id=%s", (nid,))
        existing = cur.fetchone()
        if not existing or existing['branch_id'] != branch_id:
            cur.close(); conn.close()
            return jsonify({'error': 'Not found or forbidden'}), 403
    cur.execute("""
        UPDATE meeting_notes SET meeting_date=%s, category=%s, notes=%s,
            shared_parent=%s WHERE id=%s RETURNING *
    """, (d.get('meeting_date') or None, d.get('category','General'),
          d['notes'], d.get('shared_parent', False), nid))
    row = cur.fetchone()
    conn.commit(); cur.close(); conn.close()
    if not row: return jsonify({'error': 'Not found'}), 404
    if row.get('meeting_date'): row['meeting_date'] = str(row['meeting_date'])
    if row.get('created_at'): row['created_at'] = str(row['created_at'])
    return jsonify(dict(row))

@api_bp.route('/api/meeting-notes/<int:nid>', methods=['DELETE'])
@require_roles(*NOTES_ROLES)
def delete_meeting_note(nid):
    role = session.get('role')
    branch_id = session.get('branch_id')
    conn = get_conn(); cur = conn.cursor()
    if role != 'super_admin':
        cur.execute("SELECT branch_id FROM meeting_notes WHERE id=%s", (nid,))
        existing = cur.fetchone()
        if not existing or existing['branch_id'] != branch_id:
            cur.close(); conn.close()
            return jsonify({'error': 'Not found or forbidden'}), 403
    cur.execute("DELETE FROM meeting_notes WHERE id=%s", (nid,))
    conn.commit(); cur.close(); conn.close()
    return jsonify({'ok': True})

@api_bp.route('/api/parent/meeting-notes/<int:student_id>', methods=['GET'])
@require_parent
def parent_meeting_notes(student_id):
    pid = session['parent_id']
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT 1 FROM parent_students WHERE parent_id=%s AND student_id=%s", (pid, student_id))
    if not cur.fetchone(): cur.close(); conn.close(); return jsonify({'error':'Forbidden'}), 403
    cur.execute("""
        SELECT mn.meeting_date, mn.category, mn.notes, u.name as recorded_by_name
        FROM meeting_notes mn
        LEFT JOIN users u ON u.id=mn.recorded_by
        WHERE mn.student_id=%s AND mn.shared_parent=TRUE
        ORDER BY mn.meeting_date DESC
    """, (student_id,))
    data = rows(cur)
    for d in data:
        if d.get('meeting_date'): d['meeting_date'] = str(d['meeting_date'])
    cur.close(); conn.close()
    return jsonify(data)

# ── Announcements ─────────────────────────────────────────────────────────────
ANN_ROLES = ('super_admin','branch_manager','head_of_centre','head_of_branches','supervisor','admin')

@api_bp.route('/api/announcements', methods=['GET'])
@require_roles(*ANN_ROLES)
def get_announcements():
    conn = get_conn(); cur = conn.cursor()
    bid = branch_scope()
    if bid:
        cur.execute("""
            SELECT a.*, u.name as created_by_name,
                   s.name as student_name, s.admission_id,
                   b.name as branch_name
            FROM announcements a
            LEFT JOIN users u ON u.id=a.created_by
            LEFT JOIN students s ON s.id=a.student_id
            LEFT JOIN branches b ON b.id=a.branch_id
            WHERE (a.branch_id=%s OR a.branch_id IS NULL)
            ORDER BY a.created_at DESC
        """, (bid,))
    else:
        cur.execute("""
            SELECT a.*, u.name as created_by_name,
                   s.name as student_name, s.admission_id,
                   b.name as branch_name
            FROM announcements a
            LEFT JOIN users u ON u.id=a.created_by
            LEFT JOIN students s ON s.id=a.student_id
            LEFT JOIN branches b ON b.id=a.branch_id
            ORDER BY a.created_at DESC
        """)
    data = rows(cur)
    for d in data:
        if d.get('created_at'): d['created_at'] = str(d['created_at'])[:10]
    cur.close(); conn.close()
    return jsonify(data)

@api_bp.route('/api/announcements', methods=['POST'])
@require_roles(*ANN_ROLES)
def add_announcement():
    role = session.get('role'); branch_id = session.get('branch_id')
    user_id = session.get('user_id')
    d = request.get_json() or {}
    title = (d.get('title') or '').strip()
    body  = (d.get('body')  or '').strip()
    target_type = d.get('target_type', 'all')
    student_id  = d.get('student_id') or None
    active = d.get('active', True)
    if not title or not body:
        return jsonify({'error': 'Title and body are required'}), 400
    raw_branch = d.get('branch_id') or branch_id or None
    ann_branch = int(raw_branch) if raw_branch else None
    conn = get_conn(); cur = conn.cursor()
    cur.execute("""
        INSERT INTO announcements (branch_id, created_by, title, body, target_type, student_id, active)
        VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id
    """, (ann_branch, user_id, title, body, target_type, student_id, active))
    new_id = cur.fetchone()['id']
    conn.commit(); cur.close(); conn.close()
    return jsonify({'id': new_id, 'ok': True})

@api_bp.route('/api/announcements/<int:aid>', methods=['PUT'])
@require_roles(*ANN_ROLES)
def update_announcement(aid):
    role = session.get('role'); branch_id = session.get('branch_id')
    d = request.get_json() or {}
    conn = get_conn(); cur = conn.cursor()
    if role != 'super_admin':
        cur.execute("SELECT branch_id FROM announcements WHERE id=%s", (aid,))
        ex = cur.fetchone()
        if not ex or ex['branch_id'] != branch_id:
            cur.close(); conn.close(); return jsonify({'error':'Not found or forbidden'}), 403
    cur.execute("""
        UPDATE announcements SET title=%s, body=%s, target_type=%s, student_id=%s, active=%s
        WHERE id=%s
    """, (d.get('title'), d.get('body'), d.get('target_type','all'),
          d.get('student_id') or None, d.get('active', True), aid))
    conn.commit(); cur.close(); conn.close()
    return jsonify({'ok': True})

@api_bp.route('/api/announcements/<int:aid>', methods=['DELETE'])
@require_roles(*ANN_ROLES)
def delete_announcement(aid):
    role = session.get('role'); branch_id = session.get('branch_id')
    conn = get_conn(); cur = conn.cursor()
    if role != 'super_admin':
        cur.execute("SELECT branch_id FROM announcements WHERE id=%s", (aid,))
        ex = cur.fetchone()
        if not ex or ex['branch_id'] != branch_id:
            cur.close(); conn.close(); return jsonify({'error':'Not found or forbidden'}), 403
    cur.execute("DELETE FROM announcements WHERE id=%s", (aid,))
    conn.commit(); cur.close(); conn.close()
    return jsonify({'ok': True})

@api_bp.route('/api/parent/announcements/<int:student_id>', methods=['GET'])
@require_parent
def parent_announcements(student_id):
    pid = session['parent_id']
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT 1 FROM parent_students WHERE parent_id=%s AND student_id=%s", (pid, student_id))
    if not cur.fetchone(): cur.close(); conn.close(); return jsonify({'error':'Forbidden'}), 403
    cur.execute("SELECT branch_id FROM students WHERE id=%s", (student_id,))
    stu = cur.fetchone()
    if not stu: cur.close(); conn.close(); return jsonify([])
    cur.execute("""
        SELECT a.id, a.title, a.body, a.target_type, a.created_at
        FROM announcements a
        WHERE a.active=TRUE
          AND (a.branch_id IS NULL OR a.branch_id=%s)
          AND (a.target_type='all' OR (a.target_type='student' AND a.student_id=%s))
        ORDER BY a.created_at DESC
    """, (stu['branch_id'], student_id))
    data = rows(cur)
    for d in data:
        if d.get('created_at'): d['created_at'] = str(d['created_at'])[:10]
    cur.close(); conn.close()
    return jsonify(data)

# ── Email announcements ───────────────────────────────────────────────────────
import smtplib, os
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

@api_bp.route('/api/announcements/<int:aid>/send-email', methods=['POST'])
@require_roles(*ANN_ROLES)
def send_announcement_email(aid):
    smtp_email    = os.environ.get('SMTP_EMAIL', '')
    smtp_password = os.environ.get('SMTP_PASSWORD', '')
    if not smtp_email or not smtp_password:
        return jsonify({'error': 'Email not configured. Add SMTP_EMAIL and SMTP_PASSWORD to Railway variables.'}), 500

    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT * FROM announcements WHERE id=%s", (aid,))
    ann = cur.fetchone()
    if not ann:
        cur.close(); conn.close()
        return jsonify({'error': 'Announcement not found'}), 404

    # Get parent emails from student profiles (carer1/carer2) + portal accounts
    role = session.get('role'); branch_id = session.get('branch_id')
    is_all_branches = (role == 'super_admin' or not ann['branch_id'])
    is_specific_student = (ann['target_type'] == 'student' and ann['student_id'])
    is_inactive = (ann['target_type'] == 'inactive')
    status_filter = "status IN ('inactive','paused')" if is_inactive else "status='active'"

    # Fetch branch contact details for email footer
    branch_info = None
    if ann.get('branch_id'):
        cur.execute("SELECT name, address, phone, email, website FROM branches WHERE id=%s", (ann['branch_id'],))
        branch_info = cur.fetchone()

    # Pull ALL students (with and without emails) for full audit trail
    if is_specific_student:
        cur.execute("""
            SELECT name, carer1_email, carer2_email,
                   carer1_first_name, carer1_last_name,
                   carer2_first_name, carer2_last_name
            FROM students WHERE id=%s
        """, (ann['student_id'],))
    elif is_all_branches:
        cur.execute(f"""
            SELECT name, carer1_email, carer2_email,
                   carer1_first_name, carer1_last_name,
                   carer2_first_name, carer2_last_name
            FROM students WHERE {status_filter}
        """)
    else:
        cur.execute(f"""
            SELECT name, carer1_email, carer2_email,
                   carer1_first_name, carer1_last_name,
                   carer2_first_name, carer2_last_name
            FROM students
            WHERE branch_id=%s AND {status_filter}
        """, (ann['branch_id'] or branch_id,))

    student_rows = rows(cur)

    # Also pull portal account emails
    if is_specific_student:
        cur.execute("""
            SELECT DISTINCT p.email, p.name FROM parent_users p
            JOIN parent_students ps ON ps.parent_id=p.id
            WHERE ps.student_id=%s AND p.email IS NOT NULL AND p.email!=''
        """, (ann['student_id'],))
    elif is_all_branches:
        cur.execute("""
            SELECT DISTINCT email, name FROM parent_users
            WHERE email IS NOT NULL AND email!=''
        """)
    else:
        cur.execute("""
            SELECT DISTINCT p.email, p.name FROM parent_users p
            JOIN parent_students ps ON ps.parent_id=p.id
            JOIN students s ON s.id=ps.student_id
            WHERE s.branch_id=%s AND p.email IS NOT NULL AND p.email!=''
        """, (branch_id,))

    portal_rows = rows(cur)

    # Build deduplicated recipient list; track students with no email
    seen = set()
    recipients = []
    no_email_students = []
    for s in student_rows:
        found = False
        for email_field, fn_field, ln_field in [
            ('carer1_email','carer1_first_name','carer1_last_name'),
            ('carer2_email','carer2_first_name','carer2_last_name')
        ]:
            em = (s.get(email_field) or '').strip().lower()
            if em and em not in seen:
                seen.add(em)
                fn = s.get(fn_field) or ''
                ln = s.get(ln_field) or ''
                name = (fn + ' ' + ln).strip() or s.get('name','')
                recipients.append({'email': em, 'name': name})
                found = True
        if not found:
            no_email_students.append(s.get('name','Unknown student'))
    for p in portal_rows:
        em = (p.get('email') or '').strip().lower()
        if em and em not in seen:
            seen.add(em)
            recipients.append({'email': em, 'name': p.get('name','')})
    cur.close(); conn.close()

    if not recipients:
        return jsonify({'error': 'No parents with email addresses found'}), 400

    sent = 0; failed = 0
    failed_list = []; sent_list = []
    log_conn = get_conn(); log_cur = log_conn.cursor()
    try:
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(smtp_email, smtp_password)

        for r in recipients:
            try:
                msg = MIMEMultipart('alternative')
                msg['Subject'] = ann['title']
                sender_name = f"Fine Tutors - {branch_info['name']}" if branch_info else 'Fine Tutors'
                msg['From']    = f"{sender_name} <{smtp_email}>"
                msg['To']      = f"{sender_name} <{smtp_email}>"
                msg['Reply-To'] = branch_info.get('email', smtp_email) if branch_info else smtp_email

                # Build branch contact footer
                if branch_info:
                    b_name = branch_info.get('name','Fine Tutors')
                    b_addr = branch_info.get('address','') or ''
                    b_phone = branch_info.get('phone','') or ''
                    b_email = branch_info.get('email','') or ''
                    b_web = branch_info.get('website','') or ''
                    contact_lines = []
                    if b_addr: contact_lines.append(f'<span>📍 {b_addr}</span>')
                    if b_phone: contact_lines.append(f'<span>📞 {b_phone}</span>')
                    if b_email: contact_lines.append(f'<span>✉️ <a href="mailto:{b_email}" style="color:#2563eb;">{b_email}</a></span>')
                    if b_web: contact_lines.append(f'<span>🌐 <a href="{b_web}" style="color:#2563eb;">{b_web}</a></span>')
                    contact_html = '<br>'.join(contact_lines) if contact_lines else ''
                    footer_html = f"""<hr style="border:none;border-top:1px solid #e5e7eb;margin:20px 0;">
                    <p style="font-size:12px;color:#6b7280;line-height:1.8;">{contact_html}</p>
                    <p style="font-size:11px;color:#9ca3af;">This message was sent by {b_name}.</p>"""
                else:
                    footer_html = '<hr style="border:none;border-top:1px solid #e5e7eb;margin:20px 0;"><p style="font-size:12px;color:#9ca3af;">This message was sent via the Fine Tutors parent portal.</p>'

                html_body = f"""
<div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;">
  <div style="background:#2563eb;padding:20px 24px;border-radius:8px 8px 0 0;">
    <h2 style="color:#fff;margin:0;font-size:18px;">{sender_name}</h2>
  </div>
  <div style="background:#f9fafb;padding:24px;border:1px solid #e5e7eb;border-top:none;border-radius:0 0 8px 8px;">
    <h3 style="color:#111827;margin-top:0;">{ann['title']}</h3>
    <p style="color:#374151;line-height:1.6;">{ann['body']}</p>
    {footer_html}
  </div>
</div>"""
                msg.attach(MIMEText(html_body, 'html'))
                server.sendmail(smtp_email, r['email'], msg.as_string())
                sent += 1
                sent_list.append(r['email'])
                log_cur.execute(
                    "INSERT INTO announcement_email_log (announcement_id, recipient_email, recipient_name, status) VALUES (%s,%s,%s,'sent')",
                    (aid, r['email'], r.get('name',''))
                )
                log_conn.commit()
            except Exception as ex:
                failed += 1
                err_msg = str(ex)
                failed_list.append({'email': r['email'], 'error': err_msg})
                log_cur.execute(
                    "INSERT INTO announcement_email_log (announcement_id, recipient_email, recipient_name, status, error_msg) VALUES (%s,%s,%s,'failed',%s)",
                    (aid, r['email'], r.get('name',''), err_msg)
                )
                log_conn.commit()

        server.quit()
    except Exception as e:
        log_cur.close(); log_conn.close()
        return jsonify({'error': f'SMTP connection failed: {str(e)}'}), 500
    # Log students with no email address found
    for name in no_email_students:
        log_cur.execute(
            "INSERT INTO announcement_email_log (announcement_id, recipient_email, recipient_name, status, error_msg) VALUES (%s,%s,%s,'no_email',%s)",
            (aid, '', name, 'No email address on file')
        )
    log_conn.commit()
    log_cur.close(); log_conn.close()

    return jsonify({'ok': True, 'sent': sent, 'failed': failed, 'no_email': len(no_email_students), 'sent_list': sent_list, 'failed_list': failed_list})

@api_bp.route('/api/announcements/<int:aid>/email-log', methods=['GET'])
@require_roles(*ANN_ROLES)
def get_announcement_email_log(aid):
    conn = get_conn(); cur = conn.cursor()
    cur.execute(
        """SELECT id, recipient_email, recipient_name, status, error_msg,
                  to_char(sent_at, 'DD Mon YYYY HH24:MI') as sent_at
           FROM announcement_email_log
           WHERE announcement_id=%s
           ORDER BY sent_at DESC""",
        (aid,)
    )
    rows = cur.fetchall()
    cur.close(); conn.close()
    return jsonify(rows)

# ── Credit Control ────────────────────────────────────────────────────────────
CC_ROLES = ('super_admin','branch_manager','head_of_centre','head_of_branches','admin')

@api_bp.route('/api/credit-control', methods=['GET'])
@require_roles(*CC_ROLES)
def get_credit_control():
    conn = get_conn(); cur = conn.cursor()
    bid = branch_scope()
    bw  = "AND i.branch_id=%s" if bid else ""
    bp  = (bid,) if bid else ()
    cur.execute(f"""
        SELECT
            s.id as student_id, s.name as student_name, s.branch_id,
            b.name as branch_name,
            s.pause_reminders,
            COALESCE(pu.email, '') as parent_email,
            SUM(i.amount - i.amount_paid) as outstanding,
            COUNT(i.id) as invoice_count,
            MIN((SUBSTRING(i.month,1,4)||'-'||SUBSTRING(i.month,6,2)||'-07')::date) as oldest_due,
            MAX(rl.sent_at) as last_reminder
        FROM invoices i
        JOIN students s ON s.id = i.student_id
        JOIN branches b ON b.id = i.branch_id
        LEFT JOIN parent_students ps ON ps.student_id = s.id
        LEFT JOIN parent_users pu ON pu.id = ps.parent_id
        LEFT JOIN fee_reminder_log rl ON rl.student_id = s.id
        WHERE i.status IN ('due','overdue','partial')
          AND (i.amount - i.amount_paid) > 0
          {bw}
        GROUP BY s.id, s.name, s.branch_id, b.name, s.pause_reminders, pu.email
        ORDER BY outstanding DESC
    """, bp)
    rows_data = rows(cur)
    today = date.today()
    result = []
    for r in rows_data:
        oldest = r.get('oldest_due')
        if oldest:
            days_overdue = (today - oldest).days
        else:
            days_overdue = 0
        if days_overdue <= 0:
            aging = 'current'
        elif days_overdue <= 30:
            aging = '1-30'
        elif days_overdue <= 60:
            aging = '31-60'
        elif days_overdue <= 90:
            aging = '61-90'
        else:
            aging = '90+'
        r['days_overdue'] = days_overdue
        r['aging'] = aging
        r['outstanding'] = float(r['outstanding'] or 0)
        r['last_reminder'] = r['last_reminder'].strftime('%d %b %Y %H:%M') if r.get('last_reminder') else None
        result.append(r)
    cur.close(); conn.close()
    return jsonify(result)

@api_bp.route('/api/credit-control/send-reminder/<int:student_id>', methods=['POST'])
@require_roles(*CC_ROLES)
def send_fee_reminder(student_id):
    smtp_email    = os.environ.get('SMTP_EMAIL','')
    smtp_password = os.environ.get('SMTP_PASSWORD','')
    if not smtp_email or not smtp_password:
        return jsonify({'error':'Email not configured'}), 500

    conn = get_conn(); cur = conn.cursor()
    # Get student + outstanding invoices
    cur.execute("""
        SELECT s.name, s.pause_reminders, COALESCE(pu.email,'') as parent_email,
               b.name as branch_name
        FROM students s
        JOIN branches b ON b.id = s.branch_id
        LEFT JOIN parent_students ps ON ps.student_id = s.id
        LEFT JOIN parent_users pu ON pu.id = ps.parent_id
        WHERE s.id=%s LIMIT 1
    """, (student_id,))
    st = cur.fetchone()
    if not st:
        cur.close(); conn.close()
        return jsonify({'error':'Student not found'}), 404
    if st['pause_reminders']:
        cur.close(); conn.close()
        return jsonify({'error':'Reminders are paused for this student'}), 400

    cur.execute("""
        SELECT month, amount, amount_paid, (amount-amount_paid) as balance, status
        FROM invoices
        WHERE student_id=%s AND status IN ('due','overdue','partial')
          AND (amount-amount_paid) > 0
        ORDER BY month
    """, (student_id,))
    inv_rows = rows(cur)
    total = sum(float(r['balance']) for r in inv_rows)

    recipient_email = st['parent_email']
    if not recipient_email:
        cur.close(); conn.close()
        return jsonify({'error':'No parent email found for this student'}), 400

    inv_table = ''.join([
        f"<tr><td style='padding:6px 10px;border-bottom:1px solid #e5e7eb;'>{r['month']}</td>"
        f"<td style='padding:6px 10px;border-bottom:1px solid #e5e7eb;text-align:right;'>£{float(r['amount']):.2f}</td>"
        f"<td style='padding:6px 10px;border-bottom:1px solid #e5e7eb;text-align:right;color:#dc2626;'>£{float(r['balance']):.2f}</td></tr>"
        for r in inv_rows
    ])

    html_body = f"""
<div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;">
  <div style="background:#2563eb;padding:20px 24px;border-radius:8px 8px 0 0;">
    <h2 style="color:#fff;margin:0;font-size:18px;">Fine Tutors — Fee Reminder</h2>
  </div>
  <div style="background:#f9fafb;padding:24px;border:1px solid #e5e7eb;border-top:none;border-radius:0 0 8px 8px;">
    <p style="color:#374151;">Dear Parent/Guardian,</p>
    <p style="color:#374151;">This is a reminder that the following fees remain outstanding for <strong>{st['name']}</strong> at <strong>{st['branch_name']}</strong>.</p>
    <p style="color:#374151;">Our terms require fees to be paid <strong>no later than the 7th of each month</strong>.</p>
    <table style="width:100%;border-collapse:collapse;margin:16px 0;background:#fff;border-radius:6px;overflow:hidden;">
      <thead><tr style="background:#e5e7eb;">
        <th style="padding:8px 10px;text-align:left;font-size:13px;">Month</th>
        <th style="padding:8px 10px;text-align:right;font-size:13px;">Amount</th>
        <th style="padding:8px 10px;text-align:right;font-size:13px;">Balance Due</th>
      </tr></thead>
      <tbody>{inv_table}</tbody>
      <tfoot><tr style="background:#fef2f2;">
        <td colspan="2" style="padding:8px 10px;font-weight:700;">Total Outstanding</td>
        <td style="padding:8px 10px;text-align:right;font-weight:700;color:#dc2626;">£{total:.2f}</td>
      </tr></tfoot>
    </table>
    <p style="color:#374151;">Please make payment at your earliest convenience. If you have any questions or would like to discuss a payment arrangement, please contact us.</p>
    <hr style="border:none;border-top:1px solid #e5e7eb;margin:20px 0;">
    <p style="font-size:12px;color:#9ca3af;">Fine Tutors · {st['branch_name']}</p>
  </div>
</div>"""

    try:
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(smtp_email, smtp_password)
        msg = MIMEMultipart('alternative')
        msg['Subject'] = f"Fee Reminder — {st['name']} — £{total:.2f} outstanding"
        msg['From'] = f"Fine Tutors <{smtp_email}>"
        msg['To'] = msg['From']
        msg.attach(MIMEText(html_body, 'html'))
        server.sendmail(smtp_email, recipient_email, msg.as_string())
        server.quit()
    except Exception as e:
        cur.close(); conn.close()
        return jsonify({'error': str(e)}), 500

    cur.execute("""
        INSERT INTO fee_reminder_log (student_id, sent_by, type, recipient_email, outstanding_amt, invoices_count)
        VALUES (%s,%s,'manual',%s,%s,%s)
    """, (student_id, session.get('user_id'), recipient_email, total, len(inv_rows)))
    conn.commit()
    cur.close(); conn.close()
    return jsonify({'ok': True, 'sent_to': recipient_email, 'total': total})

@api_bp.route('/api/credit-control/pause/<int:student_id>', methods=['POST'])
@require_roles(*CC_ROLES)
def toggle_reminder_pause(student_id):
    data = request.json or {}
    pause = bool(data.get('pause', True))
    conn = get_conn(); cur = conn.cursor()
    cur.execute("UPDATE students SET pause_reminders=%s WHERE id=%s", (pause, student_id))
    conn.commit(); cur.close(); conn.close()
    return jsonify({'ok': True, 'paused': pause})

@api_bp.route('/api/credit-control/history/<int:student_id>', methods=['GET'])
@require_roles(*CC_ROLES)
def get_reminder_history(student_id):
    conn = get_conn(); cur = conn.cursor()
    cur.execute("""
        SELECT rl.id, to_char(rl.sent_at, 'DD Mon YYYY HH24:MI') as sent_at,
               rl.recipient_email, rl.outstanding_amt, rl.invoices_count, rl.type,
               u.name as sent_by_name
        FROM fee_reminder_log rl
        LEFT JOIN users u ON u.id = rl.sent_by
        WHERE rl.student_id=%s
        ORDER BY rl.sent_at DESC
    """, (student_id,))
    result = rows(cur)
    cur.close(); conn.close()
    return jsonify(result)

# ── Registration Form ─────────────────────────────────────────────────────────
REG_ROLES = ('super_admin','branch_manager','head_of_centre','head_of_branches','admin','receptionist')

@api_bp.route('/api/students/<int:sid>/registration-form', methods=['GET'])
@require_roles(*REG_ROLES)
def get_registration_form(sid):
    conn = get_conn(); cur = conn.cursor()
    cur.execute("""
        SELECT s.*, b.name as branch_name,
               (SELECT STRING_AGG(DISTINCT se.subject, ', ' ORDER BY se.subject)
                FROM session_students ss2
                JOIN sessions se ON se.id=ss2.session_id
                WHERE ss2.student_id=s.id) as subjects
        FROM students s
        JOIN branches b ON b.id=s.branch_id
        WHERE s.id=%s
    """, (sid,))
    st = cur.fetchone()
    cur.close(); conn.close()
    if not st:
        return jsonify({'error':'Student not found'}), 404
    data = dict(st)
    for k,v in data.items():
        if hasattr(v,'isoformat'): data[k]=str(v)
    return jsonify(data)

@api_bp.route('/api/students/<int:sid>/save-registration', methods=['POST'])
@require_roles(*REG_ROLES)
def save_registration_sent(sid):
    conn = get_conn(); cur = conn.cursor()
    cur.execute("""
        INSERT INTO registration_sent (student_id, sent_by, sent_at)
        VALUES (%s, %s, NOW())
        ON CONFLICT (student_id) DO UPDATE SET sent_by=EXCLUDED.sent_by, sent_at=NOW()
    """, (sid, session.get('user_id')))
    conn.commit(); cur.close(); conn.close()
    return jsonify({'ok': True})

@api_bp.route('/api/students/<int:sid>/email-registration', methods=['POST'])
@require_roles(*REG_ROLES)
def email_registration_form(sid):
    smtp_email    = os.environ.get('SMTP_EMAIL','')
    smtp_password = os.environ.get('SMTP_PASSWORD','')
    if not smtp_email or not smtp_password:
        return jsonify({'error':'Email not configured'}), 500

    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT s.*, b.name as branch_name FROM students s JOIN branches b ON b.id=s.branch_id WHERE s.id=%s", (sid,))
    st = cur.fetchone()
    if not st:
        cur.close(); conn.close()
        return jsonify({'error':'Student not found'}), 404

    recipients = []
    for em_field, nm_field in [('carer1_email','carer1_first_name'),('carer2_email','carer2_first_name')]:
        em = (st.get(em_field) or '').strip()
        if em:
            recipients.append({'email':em,'name':st.get(nm_field,'')})

    if not recipients:
        cur.close(); conn.close()
        return jsonify({'error':'No parent email found for this student'}), 400

    tc_html = """
<h3 style="color:#1e3a5f;margin-top:20px;">Terms and Conditions</h3>
<h4 style="color:#2563eb;">Lesson Plan</h4>
<ul>
<li>Only agreed lesson time will be given to the students.</li>
<li>Changes to a lesson plan can only be made on the 25th or before of the previous month to the next month and this can be done once a year ONLY.</li>
<li>Only when we are informed about your child's absence prior to the lesson starting, they will be entitled for a catch-up session. The catch-up session will expire within 30 days of their absence.</li>
<li>It is highly recommended not to change your child's lesson timings as this will lead to change of teacher and will affect the child's progress.</li>
<li>4 weeks' notice is required if the student is leaving.</li>
</ul>
<h4 style="color:#2563eb;">Materials and Resources</h4>
<ul>
<li>Children must bring their own stationery, for example: notebook, pencils, calculator etc.</li>
</ul>
<h4 style="color:#2563eb;">Fees</h4>
<ul>
<li>The admission fee is a one-off payment and is non-refundable.</li>
<li>The book fee is a one-off payment for the academic year and is non-refundable. If pupils lose their books there will be a charge of £9.95 for a new one.</li>
<li>Monthly fee must be paid on the 1st day of each month. We do not accept payment instalments.</li>
<li>Monthly fee will not be adjusted for missing lessons; you will need to book a catch-up session.</li>
</ul>"""

    html_body = f"""
<div style="font-family:Arial,sans-serif;max-width:700px;margin:0 auto;">
  <div style="background:#1e3a5f;padding:24px;border-radius:8px 8px 0 0;">
    <h2 style="color:#fff;margin:0;">Fine Tutors — Contract Form</h2>
    <div style="color:#93c5fd;margin-top:4px;">{st['branch_name']}</div>
  </div>
  <div style="background:#f9fafb;padding:24px;border:1px solid #e5e7eb;border-top:none;border-radius:0 0 8px 8px;">
    <table style="width:100%;border-collapse:collapse;margin-bottom:20px;">
      <tr><td style="padding:6px 0;color:#6b7280;width:40%;">Student Name</td><td style="padding:6px 0;font-weight:600;">{st['name']}</td></tr>
      <tr><td style="padding:6px 0;color:#6b7280;">Admission ID</td><td style="padding:6px 0;">{st['admission_id']}</td></tr>
      <tr><td style="padding:6px 0;color:#6b7280;">Date of Birth</td><td style="padding:6px 0;">{st.get('date_of_birth') or '—'}</td></tr>
      <tr><td style="padding:6px 0;color:#6b7280;">Year Group</td><td style="padding:6px 0;">{st.get('year_group') or '—'}</td></tr>
      <tr><td style="padding:6px 0;color:#6b7280;">Current School</td><td style="padding:6px 0;">{st.get('current_school') or '—'}</td></tr>
    </table>
    <hr style="border:none;border-top:1px solid #e5e7eb;">
    {tc_html}
    <div style="margin-top:30px;padding-top:20px;border-top:1px solid #e5e7eb;">
      <p style="font-size:13px;color:#374151;">By enrolling your child at Fine Tutors, you agree to the above Terms and Conditions.</p>
      <table style="width:100%;margin-top:20px;">
        <tr>
          <td style="width:45%;border-top:1px solid #374151;padding-top:8px;font-size:13px;color:#6b7280;">Parent/Guardian Signature</td>
          <td style="width:10%;"></td>
          <td style="width:45%;border-top:1px solid #374151;padding-top:8px;font-size:13px;color:#6b7280;">Date</td>
        </tr>
      </table>
    </div>
    <p style="font-size:11px;color:#9ca3af;margin-top:24px;">Fine Tutors · {st['branch_name']}</p>
  </div>
</div>"""

    try:
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(smtp_email, smtp_password)
        for r in recipients:
            msg = MIMEMultipart('alternative')
            msg['Subject'] = f"Contract Form — {st['name']} — Fine Tutors {st['branch_name']}"
            msg['From'] = f"Fine Tutors <{smtp_email}>"
            msg['To'] = msg['From']
            msg.attach(MIMEText(html_body, 'html'))
            server.sendmail(smtp_email, r['email'], msg.as_string())
        server.quit()
    except Exception as e:
        cur.close(); conn.close()
        return jsonify({'error': str(e)}), 500

    # Mark as sent
    cur.execute("""
        INSERT INTO registration_sent (student_id, sent_by, sent_at)
        VALUES (%s,%s,NOW())
        ON CONFLICT (student_id) DO UPDATE SET sent_by=EXCLUDED.sent_by, sent_at=NOW()
    """, (sid, session.get('user_id')))
    conn.commit(); cur.close(); conn.close()
    return jsonify({'ok': True, 'sent_to': [r['email'] for r in recipients]})

# ── Batch Invoice ─────────────────────────────────────────────────────────────
import uuid as _uuid

@api_bp.route('/api/invoices/batch', methods=['POST'])
@require_roles('super_admin','branch_manager','head_of_centre','head_of_branches','admin','receptionist')
def create_batch_invoice():
    data = request.json or {}
    student_id = data.get('student_id')
    month      = data.get('month')
    items      = data.get('items', [])
    if not student_id or not month or not items:
        return jsonify({'error':'student_id, month and items are required'}), 400

    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT id,branch_id,name,admission_id FROM students WHERE id=%s", (student_id,))
    st = cur.fetchone()
    if not st:
        cur.close(); conn.close()
        return jsonify({'error':'Student not found'}), 404

    batch_id = str(_uuid.uuid4())
    created_ids = []
    for item in items:
        fee_type    = item.get('fee_type','monthly_fee')
        amount      = float(item.get('amount',0))
        description = item.get('description','')
        due_date    = item.get('due_date') or None
        if amount <= 0:
            continue
        cur.execute("""
            INSERT INTO invoices (student_id, branch_id, month, amount, amount_paid,
                                  status, fee_type, description, due_date, batch_id, issued)
            VALUES (%s,%s,%s,%s,0,'due',%s,%s,%s,%s,CURRENT_DATE)
            RETURNING id
        """, (student_id, st['branch_id'], month, amount, fee_type, description, due_date, batch_id))
        created_ids.append(cur.fetchone()['id'])

    conn.commit()
    # Return full batch for printing/emailing
    cur.execute("""
        SELECT i.*, s.name as student_name, s.admission_id,
               b.name as branch_name,
               s.carer1_email, s.carer2_email,
               s.carer1_first_name, s.carer2_first_name
        FROM invoices i
        JOIN students s ON s.id=i.student_id
        JOIN branches b ON b.id=i.branch_id
        WHERE i.batch_id=%s ORDER BY i.id
    """, (batch_id,))
    batch = rows(cur)
    cur.close(); conn.close()
    for r in batch:
        for k,v in r.items():
            if hasattr(v,'isoformat'): r[k]=str(v)
    return jsonify({'ok':True,'batch_id':batch_id,'items':batch})

@api_bp.route('/api/invoices/batch/<batch_id>/email', methods=['POST'])
@require_roles('super_admin','branch_manager','head_of_centre','head_of_branches','admin','receptionist')
def email_batch_invoice(batch_id):
    smtp_email    = os.environ.get('SMTP_EMAIL','')
    smtp_password = os.environ.get('SMTP_PASSWORD','')
    if not smtp_email or not smtp_password:
        return jsonify({'error':'Email not configured'}), 500

    conn = get_conn(); cur = conn.cursor()
    cur.execute("""
        SELECT i.*, s.name as student_name, s.admission_id,
               b.name as branch_name,
               s.carer1_email, s.carer2_email,
               s.carer1_first_name, s.carer2_first_name
        FROM invoices i
        JOIN students s ON s.id=i.student_id
        JOIN branches b ON b.id=i.branch_id
        WHERE i.batch_id=%s ORDER BY i.id
    """, (batch_id,))
    batch = rows(cur)
    cur.close(); conn.close()
    if not batch:
        return jsonify({'error':'Invoice batch not found'}), 404

    st = batch[0]
    feeLabels = {'monthly_fee':'Monthly Fee','opening_balance':'Opening Balance',
                 'admission_fee':'Admission Fee','book_fee':'Book Fee',
                 'past_papers_fee':'Past Papers Fee','miscellaneous':'Miscellaneous'}
    total = sum(float(r['amount']) for r in batch)
    rows_html = ''.join([
        f"<tr><td style='padding:8px 12px;border-bottom:1px solid #e5e7eb;'>{feeLabels.get(r['fee_type'],r['fee_type'])}</td>"
        f"<td style='padding:8px 12px;border-bottom:1px solid #e5e7eb;'>{r.get('description') or '—'}</td>"
        f"<td style='padding:8px 12px;border-bottom:1px solid #e5e7eb;text-align:right;font-weight:600;'>£{float(r['amount']):.2f}</td></tr>"
        for r in batch
    ])
    html_body = f"""
<div style="font-family:Arial,sans-serif;max-width:650px;margin:0 auto;">
  <div style="background:#1e3a5f;padding:24px;border-radius:8px 8px 0 0;">
    <h2 style="color:#fff;margin:0;">Fine Tutors — Invoice</h2>
    <div style="color:#93c5fd;margin-top:4px;">{st['branch_name']}</div>
  </div>
  <div style="background:#f9fafb;padding:24px;border:1px solid #e5e7eb;border-top:none;border-radius:0 0 8px 8px;">
    <table style="width:100%;margin-bottom:16px;font-size:13px;">
      <tr><td style="color:#6b7280;padding:4px 0;">Student</td><td style="font-weight:600;">{st['student_name']} ({st['admission_id']})</td></tr>
      <tr><td style="color:#6b7280;padding:4px 0;">Month</td><td>{st['month']}</td></tr>
      <tr><td style="color:#6b7280;padding:4px 0;">Issued</td><td>{st.get('issued','')}</td></tr>
    </table>
    <table style="width:100%;border-collapse:collapse;background:#fff;border-radius:6px;overflow:hidden;">
      <thead><tr style="background:#e5e7eb;">
        <th style="padding:8px 12px;text-align:left;font-size:13px;">Charge</th>
        <th style="padding:8px 12px;text-align:left;font-size:13px;">Description</th>
        <th style="padding:8px 12px;text-align:right;font-size:13px;">Amount</th>
      </tr></thead>
      <tbody>{rows_html}</tbody>
      <tfoot><tr style="background:#eff6ff;">
        <td colspan="2" style="padding:10px 12px;font-weight:700;font-size:14px;">Total</td>
        <td style="padding:10px 12px;text-align:right;font-weight:700;font-size:16px;color:#2563eb;">£{total:.2f}</td>
      </tr></tfoot>
    </table>
    <p style="margin-top:16px;font-size:13px;color:#374151;">Monthly fees are due on the <strong>1st of each month</strong>. Please contact us if you have any questions.</p>
    <p style="font-size:11px;color:#9ca3af;margin-top:20px;">Fine Tutors · {st['branch_name']}</p>
  </div>
</div>"""

    recipients = []
    for em_f, nm_f in [('carer1_email','carer1_first_name'),('carer2_email','carer2_first_name')]:
        em = (st.get(em_f) or '').strip()
        if em: recipients.append({'email':em,'name':st.get(nm_f,'')})
    if not recipients:
        return jsonify({'error':'No parent email on file'}), 400

    try:
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls(); server.login(smtp_email, smtp_password)
        for r in recipients:
            msg = MIMEMultipart('alternative')
            msg['Subject'] = f"Invoice — {st['student_name']} — {st['month']} — £{total:.2f}"
            msg['From'] = f"Fine Tutors <{smtp_email}>"; msg['To'] = f"Fine Tutors <{smtp_email}>"
            msg.attach(MIMEText(html_body,'html'))
            server.sendmail(smtp_email, r['email'], msg.as_string())
        server.quit()
    except Exception as e:
        return jsonify({'error':str(e)}), 500
    return jsonify({'ok':True,'sent_to':[r['email'] for r in recipients],'total':total})

# ─────────────────────────────────────────────────────────
# TEACHER SCHEDULE / HOURS
# ─────────────────────────────────────────────────────────
TS_ROLES = ('super_admin','branch_manager','head_of_centre','head_of_branches','admin')

SLOT_DEFS = {
    'wd_main': {'label':'17:00 – 19:00','paid_mins':120,'day_type':'weekday'},
    'wd_2':    {'label':'19:00 – 21:00','paid_mins':120,'day_type':'weekday'},
    'we_1':    {'label':'09:00 – 11:00','paid_mins':120,'day_type':'weekend'},
    'we_2':    {'label':'11:15 – 13:15','paid_mins':120,'day_type':'weekend'},
    'we_3':    {'label':'14:00 – 16:00','paid_mins':120,'day_type':'weekend'},
    'we_4':    {'label':'16:15 – 18:15','paid_mins':120,'day_type':'weekend'},
}

@api_bp.route('/api/teacher-schedule', methods=['GET'])
@require_roles(*TS_ROLES)
def get_teacher_schedule():
    week_str = request.args.get('week','')  # YYYY-WW
    conn = get_conn(); cur = conn.cursor()
    try:
        branch_id = branch_scope()
        # parse week → mon..sun
        if week_str:
            import datetime as dt
            year, week = map(int, week_str.split('-W'))
            mon = dt.datetime.strptime(f'{year}-W{week:02d}-1','%G-W%V-%u').date()
        else:
            import datetime as dt
            today = dt.date.today()
            mon = today - dt.timedelta(days=today.weekday())
        sun = mon + dt.timedelta(days=6)

        if branch_id:
            cur.execute("SELECT id,name,role,subject,status FROM staff WHERE branch_id=%s AND status='active' ORDER BY name", (branch_id,))
        else:
            cur.execute("SELECT id,name,role,subject,status FROM staff WHERE status='active' ORDER BY name")
        teachers = [dict(r) for r in cur.fetchall()]

        if branch_id:
            cur.execute("""SELECT ts.*, s.name as teacher_name, s.branch_id
                           FROM teacher_sessions ts JOIN staff s ON s.id=ts.staff_id
                           WHERE ts.branch_id=%s AND ts.date BETWEEN %s AND %s
                           ORDER BY ts.date, ts.slot_key""", (branch_id, mon, sun))
        else:
            cur.execute("""SELECT ts.*, s.name as teacher_name, s.branch_id
                           FROM teacher_sessions ts JOIN staff s ON s.id=ts.staff_id
                           WHERE ts.date BETWEEN %s AND %s
                           ORDER BY ts.date, ts.slot_key""", (mon, sun))
        sessions = [dict(r) for r in cur.fetchall()]
        for s in sessions:
            s['date'] = str(s['date'])

        return jsonify({'week':str(mon), 'teachers':teachers, 'sessions':sessions, 'slots':SLOT_DEFS})
    finally:
        cur.close(); conn.close()

@api_bp.route('/api/teacher-sessions', methods=['POST'])
@require_roles(*TS_ROLES)
def add_teacher_session():
    d = request.json or {}
    staff_id = d.get('staff_id')
    date_str = d.get('date','')
    slot_key = d.get('slot_key','')
    notes = d.get('notes','')
    if not all([staff_id, date_str, slot_key]):
        return jsonify({'error':'Missing fields'}), 400
    if slot_key not in SLOT_DEFS:
        return jsonify({'error':'Invalid slot'}), 400
    paid_mins = SLOT_DEFS[slot_key]['paid_mins']
    conn = get_conn(); cur = conn.cursor()
    try:
        cur.execute("SELECT branch_id FROM staff WHERE id=%s", (staff_id,))
        row = cur.fetchone()
        if not row: return jsonify({'error':'Staff not found'}), 404
        branch_id = row['branch_id']
        cur.execute("""INSERT INTO teacher_sessions (branch_id,staff_id,date,slot_key,paid_mins,notes,created_by)
                       VALUES (%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (staff_id,date,slot_key) DO UPDATE SET notes=EXCLUDED.notes, paid_mins=EXCLUDED.paid_mins
                       RETURNING *""",
                    (branch_id, staff_id, date_str, slot_key, paid_mins, notes, session.get('user_id')))
        row = dict(cur.fetchone())
        row['date'] = str(row['date'])
        conn.commit()
        return jsonify(row)
    except Exception as e:
        conn.rollback(); return jsonify({'error':str(e)}), 500
    finally:
        cur.close(); conn.close()

@api_bp.route('/api/teacher-sessions/<int:sid>', methods=['DELETE'])
@require_roles(*TS_ROLES)
def delete_teacher_session(sid):
    conn = get_conn(); cur = conn.cursor()
    try:
        cur.execute("DELETE FROM teacher_sessions WHERE id=%s", (sid,))
        conn.commit()
        return jsonify({'ok':True})
    finally:
        cur.close(); conn.close()

@api_bp.route('/api/teacher-hours', methods=['GET'])
@require_roles(*TS_ROLES)
def get_teacher_hours():
    period = request.args.get('period','weekly')  # weekly | monthly
    ref_date = request.args.get('date','')
    conn = get_conn(); cur = conn.cursor()
    try:
        import datetime as dt
        if ref_date:
            ref = dt.date.fromisoformat(ref_date)
        else:
            ref = dt.date.today()

        if period == 'monthly':
            start = ref.replace(day=1)
            if ref.month == 12:
                end = ref.replace(year=ref.year+1, month=1, day=1) - dt.timedelta(days=1)
            else:
                end = ref.replace(month=ref.month+1, day=1) - dt.timedelta(days=1)
            label = start.strftime('%B %Y')
        else:
            start = ref - dt.timedelta(days=ref.weekday())
            end = start + dt.timedelta(days=6)
            label = f"Week {start.strftime('%d %b')} – {end.strftime('%d %b %Y')}"

        branch_id = branch_scope()
        if branch_id:
            cur.execute("""SELECT s.id, s.name, s.subject,
                                  COUNT(ts.id) as sessions_count,
                                  COALESCE(SUM(ts.paid_mins),0) as total_mins
                           FROM staff s
                           LEFT JOIN teacher_sessions ts ON ts.staff_id=s.id AND ts.date BETWEEN %s AND %s
                           WHERE s.branch_id=%s AND s.status='active'
                           GROUP BY s.id,s.name,s.subject ORDER BY s.name""",
                        (start, end, branch_id))
        else:
            cur.execute("""SELECT s.id, s.name, s.subject,
                                  COUNT(ts.id) as sessions_count,
                                  COALESCE(SUM(ts.paid_mins),0) as total_mins
                           FROM staff s
                           LEFT JOIN teacher_sessions ts ON ts.staff_id=s.id AND ts.date BETWEEN %s AND %s
                           WHERE s.status='active'
                           GROUP BY s.id,s.name,s.subject ORDER BY s.name""",
                        (start, end))
        rows = [dict(r) for r in cur.fetchall()]
        return jsonify({'period':label,'start':str(start),'end':str(end),'rows':rows})
    finally:
        cur.close(); conn.close()

# ─────────────────────────────────────────────────────────
# REPORTS HUB — 6 new reports
# ─────────────────────────────────────────────────────────
RPT_ROLES = ('super_admin','branch_manager','head_of_centre','head_of_branches','admin','reports_viewer')

@api_bp.route('/api/reports/fees-12month', methods=['GET'])
@require_roles(*RPT_ROLES)
def report_fees_12month():
    conn = get_conn(); cur = conn.cursor()
    try:
        import datetime as dt
        branch_id = branch_scope()
        today = dt.date.today()
        months = []
        for i in range(11, -1, -1):
            m = today.month - i
            y = today.year
            while m <= 0: m += 12; y -= 1
            months.append((y, m))

        if branch_id:
            cur.execute("""SELECT s.id, s.admission_id, s.name,
                                  COALESCE(SUM(i.amount),0) as total_invoiced,
                                  COALESCE(SUM(p.amount),0) as total_paid
                           FROM students s
                           LEFT JOIN invoices i ON i.student_id=s.id
                           LEFT JOIN payments p ON p.student_id=s.id
                           WHERE s.branch_id=%s AND s.status='active'
                           GROUP BY s.id,s.admission_id,s.name ORDER BY s.name""", (branch_id,))
        else:
            cur.execute("""SELECT s.id, s.admission_id, s.name, b.name as branch_name,
                                  COALESCE(SUM(i.amount),0) as total_invoiced,
                                  COALESCE(SUM(p.amount),0) as total_paid
                           FROM students s
                           LEFT JOIN branches b ON b.id=s.branch_id
                           LEFT JOIN invoices i ON i.student_id=s.id
                           LEFT JOIN payments p ON p.student_id=s.id
                           WHERE s.status='active'
                           GROUP BY s.id,s.admission_id,s.name,b.name ORDER BY b.name,s.name""")
        students = [dict(r) for r in cur.fetchall()]

        # Monthly breakdown per student
        if branch_id:
            cur.execute("""SELECT student_id, month, COALESCE(SUM(amount),0) as inv,
                                  COALESCE(SUM(CASE WHEN status='paid' THEN amount ELSE 0 END),0) as paid
                           FROM invoices WHERE branch_id=%s GROUP BY student_id,month""", (branch_id,))
        else:
            cur.execute("""SELECT student_id, month, COALESCE(SUM(amount),0) as inv,
                                  COALESCE(SUM(CASE WHEN status='paid' THEN amount ELSE 0 END),0) as paid
                           FROM invoices GROUP BY student_id,month""")
        breakdown = {}
        for r in cur.fetchall():
            breakdown.setdefault(r['student_id'], {})[r['month']] = {'inv': float(r['inv']), 'paid': float(r['paid'])}

        month_labels = [f"{y}-{m:02d}" for y, m in months]
        return jsonify({'months': month_labels, 'students': students, 'breakdown': breakdown})
    finally:
        cur.close(); conn.close()

@api_bp.route('/api/reports/daily-attendance', methods=['GET'])
@require_roles(*RPT_ROLES)
def report_daily_attendance():
    import datetime as dt
    date_str = request.args.get('date', str(dt.date.today()))
    conn = get_conn(); cur = conn.cursor()
    try:
        branch_id = branch_scope()
        if branch_id:
            cur.execute("""SELECT a.status, a.notes,
                                  s2.name as student_name, s2.admission_id, s2.year_group,
                                  sess.slot, st.name as teacher_name, b.name as branch_name
                           FROM attendance a
                           JOIN students s2 ON s2.id=a.student_id
                           JOIN sessions sess ON sess.id=a.session_id
                           LEFT JOIN staff st ON st.id=sess.staff_id
                           LEFT JOIN branches b ON b.id=sess.branch_id
                           WHERE sess.date=%s AND sess.branch_id=%s
                           ORDER BY sess.slot, s2.name""", (date_str, branch_id))
        else:
            cur.execute("""SELECT a.status, a.notes,
                                  s2.name as student_name, s2.admission_id, s2.year_group,
                                  sess.slot, st.name as teacher_name, b.name as branch_name
                           FROM attendance a
                           JOIN students s2 ON s2.id=a.student_id
                           JOIN sessions sess ON sess.id=a.session_id
                           LEFT JOIN staff st ON st.id=sess.staff_id
                           LEFT JOIN branches b ON b.id=sess.branch_id
                           WHERE sess.date=%s
                           ORDER BY b.name, sess.slot, s2.name""", (date_str,))
        rows = [dict(r) for r in cur.fetchall()]
        present = sum(1 for r in rows if r['status']=='present')
        absent  = sum(1 for r in rows if r['status']=='absent')
        return jsonify({'date': date_str, 'rows': rows, 'present': present, 'absent': absent, 'total': len(rows)})
    finally:
        cur.close(); conn.close()

@api_bp.route('/api/reports/staff-hours', methods=['GET'])
@require_roles(*RPT_ROLES)
def report_staff_hours():
    import datetime as dt
    period = request.args.get('period', 'weekly')
    ref_date = request.args.get('date', str(dt.date.today()))
    conn = get_conn(); cur = conn.cursor()
    try:
        ref = dt.date.fromisoformat(ref_date)
        if period == 'daily':
            start = end = ref; label = ref.strftime('%A, %d %B %Y')
        elif period == 'monthly':
            start = ref.replace(day=1)
            nxt = (start.replace(month=start.month % 12 + 1, day=1) if start.month < 12
                   else start.replace(year=start.year+1, month=1, day=1))
            end = nxt - dt.timedelta(days=1)
            label = start.strftime('%B %Y')
        else:
            start = ref - dt.timedelta(days=ref.weekday()); end = start + dt.timedelta(days=6)
            label = f"Week {start.strftime('%d %b')} – {end.strftime('%d %b %Y')}"

        branch_id = branch_scope()
        if branch_id:
            cur.execute("""SELECT s.id,s.name,s.subject,b.name as branch_name,
                                  COUNT(ts.id) as sessions_count,
                                  COALESCE(SUM(ts.paid_mins),0) as total_mins,
                                  STRING_AGG(DISTINCT ts.date::text,',' ORDER BY ts.date::text) as dates
                           FROM staff s
                           LEFT JOIN branches b ON b.id=s.branch_id
                           LEFT JOIN teacher_sessions ts ON ts.staff_id=s.id AND ts.date BETWEEN %s AND %s
                           WHERE s.branch_id=%s AND s.status='active'
                           GROUP BY s.id,s.name,s.subject,b.name ORDER BY s.name""",
                        (start, end, branch_id))
        else:
            cur.execute("""SELECT s.id,s.name,s.subject,b.name as branch_name,
                                  COUNT(ts.id) as sessions_count,
                                  COALESCE(SUM(ts.paid_mins),0) as total_mins,
                                  STRING_AGG(DISTINCT ts.date::text,',' ORDER BY ts.date::text) as dates
                           FROM staff s
                           LEFT JOIN branches b ON b.id=s.branch_id
                           LEFT JOIN teacher_sessions ts ON ts.staff_id=s.id AND ts.date BETWEEN %s AND %s
                           WHERE s.status='active'
                           GROUP BY s.id,s.name,s.subject,b.name ORDER BY b.name,s.name""",
                        (start, end))
        rows = [dict(r) for r in cur.fetchall()]
        return jsonify({'period': label, 'start': str(start), 'end': str(end), 'rows': rows})
    finally:
        cur.close(); conn.close()

@api_bp.route('/api/reports/student-list', methods=['GET'])
@require_roles(*RPT_ROLES)
def report_student_list():
    conn = get_conn(); cur = conn.cursor()
    try:
        branch_id = branch_scope()
        if branch_id:
            cur.execute("""SELECT s.admission_id, s.name, s.year_group, b.name as branch_name,
                                  t.day_type, t.slot, t.subject
                           FROM students s
                           JOIN branches b ON b.id=s.branch_id
                           LEFT JOIN student_timetable t ON t.student_id=s.id AND t.active=true
                           WHERE s.branch_id=%s AND s.status='active'
                           ORDER BY s.name, t.day_type, t.slot""", (branch_id,))
        else:
            cur.execute("""SELECT s.admission_id, s.name, s.year_group, b.name as branch_name,
                                  t.day_type, t.slot, t.subject
                           FROM students s
                           JOIN branches b ON b.id=s.branch_id
                           LEFT JOIN student_timetable t ON t.student_id=s.id AND t.active=true
                           WHERE s.status='active'
                           ORDER BY b.name, s.name, t.day_type, t.slot""")
        rows = [dict(r) for r in cur.fetchall()]
        return jsonify({'rows': rows})
    finally:
        cur.close(); conn.close()

@api_bp.route('/api/reports/teacher-planner', methods=['GET'])
@require_roles(*RPT_ROLES)
def report_teacher_planner():
    import datetime as dt
    date_str = request.args.get('date', str(dt.date.today()))
    conn = get_conn(); cur = conn.cursor()
    try:
        branch_id = branch_scope()
        # Get sessions for the date
        if branch_id:
            cur.execute("""SELECT sess.id, sess.slot, sess.branch_id, b.name as branch_name,
                                  st.name as teacher_name, st.subject as teacher_subject
                           FROM sessions sess
                           JOIN branches b ON b.id=sess.branch_id
                           LEFT JOIN staff st ON st.id=sess.staff_id
                           WHERE sess.date=%s AND sess.branch_id=%s
                           ORDER BY sess.slot, st.name""", (date_str, branch_id))
        else:
            cur.execute("""SELECT sess.id, sess.slot, sess.branch_id, b.name as branch_name,
                                  st.name as teacher_name, st.subject as teacher_subject
                           FROM sessions sess
                           JOIN branches b ON b.id=sess.branch_id
                           LEFT JOIN staff st ON st.id=sess.staff_id
                           WHERE sess.date=%s
                           ORDER BY b.name, sess.slot, st.name""", (date_str,))
        sessions = [dict(r) for r in cur.fetchall()]

        # Get students for each session via attendance or table_allocations
        for s in sessions:
            cur.execute("""SELECT st2.admission_id, st2.name, st2.year_group
                           FROM attendance a
                           JOIN students st2 ON st2.id=a.student_id
                           WHERE a.session_id=%s ORDER BY st2.name""", (s['id'],))
            s['students'] = [dict(r) for r in cur.fetchall()]

        return jsonify({'date': date_str, 'sessions': sessions})
    finally:
        cur.close(); conn.close()

@api_bp.route('/api/reports/progress', methods=['GET'])
@require_roles(*RPT_ROLES)
def report_progress():
    import datetime as dt
    month_str = request.args.get('month', dt.date.today().strftime('%Y-%m'))
    conn = get_conn(); cur = conn.cursor()
    try:
        branch_id = branch_scope()
        try:
            y, m = map(int, month_str.split('-'))
            start = dt.date(y, m, 1)
            end = (dt.date(y, m+1, 1) - dt.timedelta(days=1)) if m < 12 else dt.date(y, 12, 31)
        except:
            start = dt.date.today().replace(day=1); end = dt.date.today()

        if branch_id:
            cur.execute("""SELECT s.admission_id, s.name, s.year_group,
                                  p.subject, p.rating, p.comment, p.date,
                                  st.name as teacher_name, b.name as branch_name
                           FROM progress p
                           JOIN students s ON s.id=p.student_id
                           JOIN branches b ON b.id=s.branch_id
                           LEFT JOIN staff st ON st.id=p.staff_id
                           WHERE p.date BETWEEN %s AND %s AND s.branch_id=%s
                           ORDER BY s.name, p.date""", (start, end, branch_id))
        else:
            cur.execute("""SELECT s.admission_id, s.name, s.year_group,
                                  p.subject, p.rating, p.comment, p.date,
                                  st.name as teacher_name, b.name as branch_name
                           FROM progress p
                           JOIN students s ON s.id=p.student_id
                           JOIN branches b ON b.id=s.branch_id
                           LEFT JOIN staff st ON st.id=p.staff_id
                           WHERE p.date BETWEEN %s AND %s
                           ORDER BY b.name, s.name, p.date""", (start, end))
        rows = [dict(r) for r in cur.fetchall()]
        for r in rows:
            r['date'] = str(r['date'])
        return jsonify({'month': month_str, 'rows': rows})
    finally:
        cur.close(); conn.close()

@api_bp.route('/api/parent/contract/<int:student_id>', methods=['GET'])
@require_parent
def parent_contract(student_id):
    pid = session['parent_id']
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT 1 FROM parent_students WHERE parent_id=%s AND student_id=%s", (pid, student_id))
    if not cur.fetchone(): cur.close(); conn.close(); return jsonify({'error':'Forbidden'}), 403
    cur.execute(
        "SELECT s.*, b.name as branch_name, rs.sent_at as contract_sent_at "
        "FROM students s JOIN branches b ON b.id=s.branch_id "
        "LEFT JOIN registration_sent rs ON rs.student_id=s.id "
        "WHERE s.id=%s", (student_id,))
    row = cur.fetchone()
    cur.close(); conn.close()
    if not row: return jsonify({'available': False})
    data = dict(row)
    for k,v in data.items():
        if hasattr(v,'isoformat'): data[k]=str(v)
    data['available'] = data.get('contract_sent_at') is not None
    return jsonify(data)


# ── Admission Slip ─────────────────────────────────────────────────────────────

@api_bp.route('/api/students/<int:sid>/admission-slip', methods=['GET'])
@require_roles('super_admin','branch_manager','head_of_centre','head_of_branches','admin','receptionist')
def get_admission_slip(sid):
    conn = get_conn(); cur = conn.cursor()
    cur.execute("""
        SELECT s.*, b.name as branch_name
        FROM students s JOIN branches b ON b.id=s.branch_id
        WHERE s.id=%s
    """, (sid,))
    st = cur.fetchone()
    if not st:
        cur.close(); conn.close()
        return jsonify({'error':'Student not found'}), 404
    data = dict(st)
    for k,v in data.items():
        if hasattr(v,'isoformat'): data[k]=str(v)
    cur.execute("""
        SELECT day_type, slot, subject FROM student_timetable
        WHERE student_id=%s AND active=TRUE ORDER BY day_type, slot
    """, (sid,))
    data['timetable'] = rows(cur)
    # Also include agreed_slots for auto-filling the timetable grid
    cur.execute("""
        SELECT bs.day_of_week, bs.slot_start, bs.slot_end, sas.subject
        FROM student_agreed_slots sas
        JOIN branch_schedule bs ON bs.id = sas.branch_schedule_id
        WHERE sas.student_id = %s AND bs.status = 'active'
        ORDER BY bs.day_of_week, bs.slot_start
    """, (sid,))
    raw_as = cur.fetchall()
    # Convert time objects to HH:MM strings
    data['agreed_slots'] = [{
        'day_of_week': r['day_of_week'],
        'slot_start': r['slot_start'].strftime('%H:%M') if hasattr(r['slot_start'],'strftime') else str(r['slot_start'])[:5],
        'slot_end':   r['slot_end'].strftime('%H:%M')   if hasattr(r['slot_end'],  'strftime') else str(r['slot_end'])[:5],
        'subject':    r['subject'] or ''
    } for r in raw_as]
    cur.close(); conn.close()
    return jsonify(data)


def _norm_slot(s):
    return s.replace('–','-').replace('—','-').replace('−','-').replace(' ','').strip()

def _build_slip_html(st):
    import datetime
    agreed_sl = st.get('agreed_slots', [])
    tm = st.get('timetable', [])
    # Subjects from agreed slots (prefer) or old timetable
    subj_set = sorted({r['subject'] for r in agreed_sl if r.get('subject')} or
                      {r['subject'] for r in tm if r.get('subject')})
    start = (st.get('created_at') or '')[:10]
    today = datetime.date.today()
    end_year = today.year if today.month < 8 else today.year + 1
    end = f"{end_year}-07-31"

    def get_agreed(day, start_hhmm):
        for r in agreed_sl:
            if r['day_of_week'] == day and r['slot_start'].replace(' ','') == start_hhmm.replace(' ',''):
                return r.get('subject') or '✓'
        return ''

    wd_slots = ['17:00 - 19:00','19:00 - 21:00']
    we_slots = ['09:00 - 11:00','11:15 - 13:15','14:00 - 16:00','16:15 - 18:15']
    wd_days  = ['monday','tuesday','wednesday','thursday','friday']

    subj_rows = ''
    for i in range(4):
        v = subj_set[i] if i < len(subj_set) else ''
        subj_rows += f'<tr><td style="border:1px solid #000;padding:5px 8px;">{i+1}. {v}</td></tr>'

    wd_rows = ''
    for sl in wd_slots:
        start_t = sl.split(' - ')[0].strip()
        cells = ''.join(
            f'<td style="border:1px solid #000;padding:5px;text-align:center;font-size:12px;">{get_agreed(d, start_t)}</td>'
            for d in wd_days
        )
        wd_rows += f'<tr><td style="border:1px solid #000;padding:5px 8px;">{sl}</td>{cells}</tr>'

    we_rows = ''
    for sl in we_slots:
        start_t = sl.split(' - ')[0].strip()
        ss = get_agreed('saturday', start_t)
        su = get_agreed('sunday', start_t)
        we_rows += (f'<tr><td style="border:1px solid #000;padding:5px 8px;">{sl}</td>'
                    f'<td style="border:1px solid #000;padding:5px;text-align:center;font-size:12px;">{ss}</td>'
                    f'<td style="border:1px solid #000;padding:5px;text-align:center;font-size:12px;">{su}</td>'
                    f'<td style="border:1px solid #000;padding:5px;"></td></tr>')

    tc = """
<p style="text-decoration:underline;font-weight:bold;margin:8px 0 4px;">Lesson Plan:</p>
<ul style="margin:0 0 8px 18px;line-height:1.7;font-size:13px;">
  <li>Only agreed lesson time will be given to the students.</li>
  <li>Changes to a lesson plan can only be made on the 25th or before of the previous month to the next month and this can be done once a year ONLY.</li>
  <li>Only when we are informed about your child's absence prior to the lesson starting, they will be entitled for a catch-up session. The catch-up session will expire within 30 days of their absence.</li>
  <li>It is highly recommended not to change your child's lesson timings as this will lead to change of teacher and will affect the child's progress.</li>
  <li>4 weeks' notice is required if the student is leaving.</li>
</ul>
<p style="text-decoration:underline;font-weight:bold;margin:8px 0 4px;">Materials and Resources</p>
<ul style="margin:0 0 8px 18px;line-height:1.7;font-size:13px;">
  <li>Children must bring their own stationery, for example: notebook, pencils, calculator etc.</li>
</ul>
<p style="text-decoration:underline;font-weight:bold;margin:8px 0 4px;">Fees:</p>
<ul style="margin:0 0 8px 18px;line-height:1.7;font-size:13px;">
  <li>The admission fee is a one-off payment and is non-refundable.</li>
  <li>The book fee is a one-off payment for the academic year and is non-refundable. If pupils lose their books there will be a charge of &pound;9.95 for a new one.</li>
  <li>Monthly fee must be paid on the 1st day of each month. We do not accept payment instalments.</li>
  <li>Monthly fee will not be adjusted for missing lessons; you will need to book a catch-up session.</li>
</ul>"""

    return f"""<div style="font-family:Arial,sans-serif;max-width:780px;margin:0 auto;border:2px solid #000;padding:20px;box-sizing:border-box;">
  <div style="display:flex;justify-content:space-between;align-items:center;border-bottom:2px solid #000;padding-bottom:10px;margin-bottom:10px;">
    <h2 style="margin:0;font-size:22px;">Admission Slip</h2>
    <span style="font-size:16px;font-weight:bold;color:#1e3a5f;">Fine Tutors &mdash; {st.get('branch_name','')}</span>
  </div>
  <table style="width:100%;border-collapse:collapse;">
    <tr>
      <th style="border:1px solid #000;padding:6px 10px;width:25%;text-align:left;">Admission No:</th>
      <th style="border:1px solid #000;padding:6px 10px;width:40%;text-align:left;">Student Name:</th>
      <th style="border:1px solid #000;padding:6px 10px;text-align:left;">Subjects</th>
    </tr>
    <tr>
      <td rowspan="4" style="border:1px solid #000;padding:8px 10px;vertical-align:middle;font-weight:bold;font-size:15px;">{st.get('admission_id','')}</td>
      <td rowspan="4" style="border:1px solid #000;padding:8px 10px;vertical-align:middle;">{st.get('name','')}</td>
      {subj_rows}
    </tr>
  </table>
  <table style="width:100%;border-collapse:collapse;">
    <tr>
      <td style="border:1px solid #000;padding:6px 10px;font-weight:bold;width:25%;">Contract Period</td>
      <td style="border:1px solid #000;padding:6px 10px;">{start} &nbsp;&nbsp; TO &nbsp;&nbsp; {end}</td>
    </tr>
  </table>
  <div style="margin:10px 0;">{tc}</div>
  <table style="width:100%;border-collapse:collapse;margin-top:10px;">
    <tr style="background:#f0f0f0;">
      <th style="border:1px solid #000;padding:6px;">Weekdays</th>
      <th style="border:1px solid #000;padding:6px;">Monday</th>
      <th style="border:1px solid #000;padding:6px;">Tuesday</th>
      <th style="border:1px solid #000;padding:6px;">Wednesday</th>
      <th style="border:1px solid #000;padding:6px;">Thursday</th>
      <th style="border:1px solid #000;padding:6px;">Friday</th>
    </tr>
    {wd_rows}
  </table>
  <table style="width:100%;border-collapse:collapse;margin-top:10px;">
    <tr style="background:#f0f0f0;">
      <th style="border:1px solid #000;padding:6px;">Weekends</th>
      <th style="border:1px solid #000;padding:6px;">Saturday</th>
      <th style="border:1px solid #000;padding:6px;">Sunday</th>
      <th style="border:1px solid #000;padding:6px;">Additional Notes</th>
    </tr>
    {we_rows}
  </table>
  <table style="width:100%;border-collapse:collapse;margin-top:12px;">
    <tr>
      <td style="border:1px solid #000;padding:12px;width:50%;">
        <strong>Parent / Guardian Signature:</strong><br><br><br>
      </td>
      <td style="border:1px solid #000;padding:12px;width:50%;">
        <strong>Date:</strong><br><br><br>
      </td>
    </tr>
  </table>
</div>"""


@api_bp.route('/api/students/<int:sid>/email-admission-slip', methods=['POST'])
@require_roles('super_admin','branch_manager','head_of_centre','head_of_branches','admin','receptionist')
def email_admission_slip(sid):
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    smtp_email    = os.environ.get('SMTP_EMAIL','')
    smtp_password = os.environ.get('SMTP_PASSWORD','')
    if not smtp_email or not smtp_password:
        return jsonify({'error':'Email not configured'}), 500

    conn = get_conn(); cur = conn.cursor()
    cur.execute("""
        SELECT s.*, b.name as branch_name FROM students s
        JOIN branches b ON b.id=s.branch_id WHERE s.id=%s
    """, (sid,))
    st = cur.fetchone()
    if not st:
        cur.close(); conn.close()
        return jsonify({'error':'Student not found'}), 404
    data = dict(st)
    for k,v in data.items():
        if hasattr(v,'isoformat'): data[k]=str(v)
    cur.execute("SELECT day_type, slot, subject FROM student_timetable WHERE student_id=%s AND active=TRUE ORDER BY day_type, slot", (sid,))
    data['timetable'] = rows(cur)

    recipients = []
    for ef, nf in [('carer1_email','carer1_first_name'),('carer2_email','carer2_first_name')]:
        em = (data.get(ef) or '').strip()
        if em:
            recipients.append({'email':em,'name':data.get(nf,'')})
    if not recipients:
        cur.close(); conn.close()
        return jsonify({'error':'No parent email found'}), 400

    slip_html = _build_slip_html(data)
    html_body = f"""<div style="font-family:Arial,sans-serif;max-width:800px;margin:0 auto;">
      <div style="background:#1e3a5f;padding:20px;border-radius:8px 8px 0 0;">
        <h2 style="color:#fff;margin:0;">Fine Tutors &mdash; Admission Slip</h2>
        <div style="color:#93c5fd;">{data['branch_name']}</div>
      </div>
      <div style="padding:24px;border:1px solid #e5e7eb;border-top:none;border-radius:0 0 8px 8px;">
        <p>Dear Parent/Guardian,</p>
        <p>Please find your child's admission slip below. Please keep this for your records.</p>
        {slip_html}
        <p style="margin-top:20px;color:#666;font-size:13px;">Fine Tutors {data['branch_name']}</p>
      </div>
    </div>"""

    sent_to = []
    try:
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(smtp_email, smtp_password)
        for rec in recipients:
            msg = MIMEMultipart('alternative')
            msg['From']    = smtp_email
            msg['To']      = msg['From']
            msg['Subject'] = f"Admission Slip - {data['name']} - Fine Tutors {data['branch_name']}"
            msg.attach(MIMEText(html_body,'html'))
            server.sendmail(smtp_email, rec['email'], msg.as_string())
            sent_to.append(rec['email'])
        server.quit()
    except Exception as ex:
        cur.close(); conn.close()
        return jsonify({'error': str(ex)}), 500

    cur.close(); conn.close()
    return jsonify({'ok': True, 'sent_to': sent_to})

# ── Batch Payment ──────────────────────────────────────────────────────────────

@api_bp.route('/api/invoices/batch/<batch_id>/pay', methods=['POST'])
@require_roles('super_admin','branch_manager','head_of_centre','head_of_branches','admin','receptionist')
def batch_pay(batch_id):
    """Record payment against one or more invoices in a batch."""
    d = request.json or {}
    payments = d.get('payments', [])  # [{invoice_id, amount_paid, method, reference}]
    method    = d.get('method','cash')
    reference = d.get('reference','')
    if not payments:
        return jsonify({'error':'No payments provided'}), 400

    conn = get_conn(); cur = conn.cursor()
    results = []
    for p in payments:
        iid        = p.get('invoice_id')
        pay_amount = float(p.get('amount_paid', 0))
        if not iid or pay_amount <= 0:
            continue
        cur.execute("SELECT * FROM invoices WHERE id=%s AND batch_id=%s", (iid, batch_id))
        inv = row(cur)
        if not inv:
            continue
        total      = float(inv['amount'])
        already    = float(inv.get('amount_paid') or 0)
        new_paid   = round(already + pay_amount, 2)
        new_status = 'paid' if new_paid >= total else 'partial'
        if new_paid > total: new_paid = total
        cur.execute("""
            UPDATE invoices SET status=%s, amount_paid=%s,
                paid_date=CASE WHEN %s='paid' THEN CURRENT_DATE ELSE paid_date END
            WHERE id=%s
        """, (new_status, new_paid, new_status, iid))
        cur.execute("""
            INSERT INTO payments (student_id, branch_id, amount, payment_date, method, reference, notes, recorded_by)
            VALUES (%s,%s,%s,CURRENT_DATE,%s,%s,%s,%s)
        """, (inv['student_id'], inv['branch_id'], pay_amount, method, reference,
              f"Batch {batch_id[:8]} inv#{iid} ({inv.get('month','')})" + (' [partial]' if new_status=='partial' else ''),
              session.get('user_id')))
        results.append({'invoice_id': iid, 'status': new_status, 'amount_paid': new_paid, 'balance': round(total - new_paid, 2)})

    conn.commit()
    # Return updated batch
    cur.execute("""
        SELECT i.*, s.name as student_name, s.admission_id, b.name as branch_name,
               s.carer1_email, s.carer2_email, s.carer1_first_name, s.carer2_first_name
        FROM invoices i JOIN students s ON s.id=i.student_id JOIN branches b ON b.id=i.branch_id
        WHERE i.batch_id=%s ORDER BY i.id
    """, (batch_id,))
    batch = rows(cur)
    cur.close(); conn.close()
    for r in batch:
        for k,v in r.items():
            if hasattr(v,'isoformat'): r[k]=str(v)
    return jsonify({'ok': True, 'results': results, 'items': batch})

# ── Invoice Receipt Email ──────────────────────────────────────────────────────

@api_bp.route('/api/invoices/<int:iid>/email-receipt', methods=['POST'])
@require_roles('super_admin','branch_manager','head_of_centre','head_of_branches','admin','receptionist')
def email_invoice_receipt(iid):
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    smtp_email    = os.environ.get('SMTP_EMAIL','')
    smtp_password = os.environ.get('SMTP_PASSWORD','')
    if not smtp_email or not smtp_password:
        return jsonify({'error':'Email not configured'}), 500

    d = request.json or {}
    amount_paid = float(d.get('amount_paid', 0))
    method      = d.get('method', 'cash')
    reference   = d.get('reference', '')

    conn = get_conn(); cur = conn.cursor()
    cur.execute("""
        SELECT i.*, s.name as student_name, s.admission_id, b.name as branch_name,
               s.carer1_email, s.carer2_email, s.carer1_first_name, s.carer2_first_name,
               COALESCE(i.fee_type,'monthly_fee') as fee_type
        FROM invoices i JOIN students s ON s.id=i.student_id JOIN branches b ON b.id=i.branch_id
        WHERE i.id=%s
    """, (iid,))
    inv = row(cur)
    if not inv:
        cur.close(); conn.close()
        return jsonify({'error':'Invoice not found'}), 404
    data = dict(inv)
    for k,v in data.items():
        if hasattr(v,'isoformat'): data[k]=str(v)

    recipients = []
    for ef, nf in [('carer1_email','carer1_first_name'),('carer2_email','carer2_first_name')]:
        em = (data.get(ef) or '').strip()
        if em:
            recipients.append({'email':em,'name':data.get(nf,'')})
    if not recipients:
        cur.close(); conn.close()
        return jsonify({'error':'No parent email found'}), 400

    fee_labels = {'monthly_fee':'Monthly Fee','opening_balance':'Opening Balance',
                  'admission_fee':'Admission Fee','book_fee':'Book Fee',
                  'past_papers_fee':'Past Papers Fee','miscellaneous':'Miscellaneous'}
    method_labels = {'cash':'Cash','card':'Card','bank_transfer':'Bank Transfer',
                     'cheque':'Cheque','direct_debit':'Direct Debit',
                     'standing_order':'Standing Order','other':'Other'}

    total     = float(data.get('amount') or 0)
    total_paid= float(data.get('amount_paid') or 0)
    balance   = max(0, total - total_paid)
    is_paid   = data.get('status') == 'paid'

    stamp = ('<div style="text-align:center;margin:16px 0;"><span style="background:#d1fae5;color:#065f46;font-size:18px;font-weight:800;padding:8px 24px;border:3px solid #065f46;border-radius:8px;">PAID IN FULL</span></div>'
             if is_paid else
             f'<div style="text-align:center;margin:16px 0;"><span style="background:#fef3c7;color:#92400e;font-size:18px;font-weight:800;padding:8px 24px;border:3px solid #92400e;border-radius:8px;">PART PAID</span></div>'
             f'<p style="text-align:center;color:#92400e;">Balance remaining: <strong>£{balance:.2f}</strong></p>')

    from datetime import date
    today = date.today().strftime('%d/%m/%Y')

    html_body = f"""<div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;">
  <div style="background:#1e3a5f;padding:20px;border-radius:8px 8px 0 0;">
    <h2 style="color:#fff;margin:0;">Fine Tutors &mdash; Payment Receipt</h2>
    <div style="color:#93c5fd;">{data['branch_name']}</div>
  </div>
  <div style="padding:24px;border:1px solid #e5e7eb;border-top:none;border-radius:0 0 8px 8px;">
    {stamp}
    <table style="width:100%;border-collapse:collapse;font-size:14px;margin:16px 0;">
      <tr><td style="color:#6b7280;padding:6px 0;width:40%;">Student</td><td style="font-weight:600;">{data['student_name']}</td></tr>
      <tr><td style="color:#6b7280;padding:6px 0;">Admission ID</td><td>{data['admission_id']}</td></tr>
      <tr><td style="color:#6b7280;padding:6px 0;">Fee Type</td><td>{fee_labels.get(data['fee_type'], data['fee_type'])}</td></tr>
      <tr><td style="color:#6b7280;padding:6px 0;">Month</td><td>{data.get('month','')}</td></tr>
      <tr><td style="color:#6b7280;padding:6px 0;">Invoice Total</td><td>£{total:.2f}</td></tr>
      <tr><td style="color:#6b7280;padding:6px 0;">Amount Paid</td><td style="color:#059669;font-weight:600;">£{amount_paid:.2f}</td></tr>
      {'<tr><td style="color:#6b7280;padding:6px 0;">Balance Remaining</td><td style="color:#dc2626;font-weight:600;">£'+f'{balance:.2f}'+'</td></tr>' if not is_paid else ''}
      <tr><td style="color:#6b7280;padding:6px 0;">Payment Method</td><td>{method_labels.get(method, method)}</td></tr>
      {'<tr><td style="color:#6b7280;padding:6px 0;">Reference</td><td>'+reference+'</td></tr>' if reference else ''}
      <tr><td style="color:#6b7280;padding:6px 0;">Date</td><td>{today}</td></tr>
    </table>
    <p style="font-size:13px;color:#6b7280;border-top:1px solid #e5e7eb;padding-top:12px;">Thank you for your payment. Please retain this receipt for your records.</p>
    <p style="font-size:13px;color:#6b7280;">Fine Tutors &mdash; {data['branch_name']}</p>
  </div>
</div>"""

    sent_to = []
    try:
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(smtp_email, smtp_password)
        for rec in recipients:
            msg = MIMEMultipart('alternative')
            msg['From']    = smtp_email
            msg['To']      = msg['From']
            msg['Subject'] = f"Payment Receipt - {data['student_name']} - Fine Tutors {data['branch_name']}"
            msg.attach(MIMEText(html_body, 'html'))
            server.sendmail(smtp_email, rec['email'], msg.as_string())
            sent_to.append(rec['email'])
        server.quit()
    except Exception as ex:
        cur.close(); conn.close()
        return jsonify({'error': str(ex)}), 500

    cur.close(); conn.close()
    return jsonify({'ok': True, 'sent_to': sent_to})


# ── Student Progress Report Email ─────────────────────────────────────────────
@api_bp.route('/api/students/<int:sid>/send-progress-report', methods=['POST'])
@require_auth
def send_progress_report(sid):
    smtp_email    = os.environ.get('SMTP_EMAIL', '')
    smtp_password = os.environ.get('SMTP_PASSWORD', '')
    if not smtp_email or not smtp_password:
        return jsonify({'error': 'Email not configured on server.'}), 500

    conn = get_conn(); cur = conn.cursor()

    # Student info
    cur.execute("SELECT s.*, b.name as branch_name FROM students s JOIN branches b ON b.id=s.branch_id WHERE s.id=%s", (sid,))
    st = cur.fetchone()
    if not st:
        cur.close(); conn.close()
        return jsonify({'error': 'Student not found'}), 404

    # Attendance summary
    cur.execute("""
        SELECT COUNT(*) as total,
               SUM(CASE WHEN a.status='present' THEN 1 ELSE 0 END) as present,
               SUM(CASE WHEN a.status='absent'  THEN 1 ELSE 0 END) as absent
        FROM attendance a WHERE a.student_id=%s
    """, (sid,))
    att = cur.fetchone() or {}

    # Recent test records (last 5)
    cur.execute("""
        SELECT subject, book_unit, test_date, score_pct, action_plan
        FROM test_records WHERE student_id=%s ORDER BY test_date DESC LIMIT 5
    """, (sid,))
    tests = rows(cur)

    # Recent progress notes (last 5)
    cur.execute("""
        SELECT p.subject, p.rating, p.comment, p.date, st.name as staff_name
        FROM progress p LEFT JOIN staff st ON st.id=p.staff_id
        WHERE p.student_id=%s ORDER BY p.date DESC LIMIT 5
    """, (sid,))
    prog = rows(cur)

    cur.close(); conn.close()

    # Build recipients from carer emails
    recipients = []
    seen = set()
    for em_field, fn_field, ln_field in [
        ('carer1_email','carer1_first_name','carer1_last_name'),
        ('carer2_email','carer2_first_name','carer2_last_name'),
    ]:
        em = (st.get(em_field) or '').strip().lower()
        if em and em not in seen:
            seen.add(em)
            name = ((st.get(fn_field) or '') + ' ' + (st.get(ln_field) or '')).strip()
            recipients.append({'email': em, 'name': name or 'Parent/Carer'})

    if not recipients:
        return jsonify({'error': 'No parent email addresses found for this student.'}), 400

    total = int(att.get('total') or 0)
    present = int(att.get('present') or 0)
    absent  = int(att.get('absent')  or 0)
    pct = round(present / total * 100) if total else 0
    avg_score = round(sum(float(t['score_pct'] or 0) for t in tests) / len(tests)) if tests else None

    att_color = '#16a34a' if pct >= 80 else '#d97706' if pct >= 60 else '#dc2626'

    tests_html = ''
    for t in tests:
        sc = float(t['score_pct'] or 0)
        c = '#16a34a' if sc >= 70 else '#d97706' if sc >= 50 else '#dc2626'
        tests_html += f'<tr><td style="padding:6px 8px;border-bottom:1px solid #e5e7eb;">{t["test_date"]}</td><td style="padding:6px 8px;border-bottom:1px solid #e5e7eb;">{t["subject"]}</td><td style="padding:6px 8px;border-bottom:1px solid #e5e7eb;font-size:12px;">{t["book_unit"] or "—"}</td><td style="padding:6px 8px;border-bottom:1px solid #e5e7eb;font-weight:700;color:{c};">{sc}%</td></tr>'

    prog_html = ''
    for p in prog:
        stars = '★' * (p['rating'] or 0) + '☆' * (5 - (p['rating'] or 0))
        rc = '#16a34a' if (p['rating'] or 0) >= 4 else '#d97706' if (p['rating'] or 0) >= 3 else '#dc2626'
        prog_html += f'<div style="padding:10px 0;border-bottom:1px solid #e5e7eb;"><div style="display:flex;justify-content:space-between;"><span style="font-weight:600;color:#374151;">{p["subject"] or "General"}</span><span style="color:{rc};">{stars}</span></div><div style="font-size:13px;color:#4b5563;margin-top:4px;">{p["comment"] or ""}</div><div style="font-size:11px;color:#9ca3af;margin-top:2px;">{p["staff_name"] or ""} · {p["date"]}</div></div>'

    html_body = f"""
<div style="font-family:Arial,sans-serif;max-width:620px;margin:0 auto;">
  <div style="background:#2563eb;padding:20px 24px;border-radius:8px 8px 0 0;">
    <h2 style="color:#fff;margin:0;font-size:20px;">Fine Tutors — Student Progress Report</h2>
    <p style="color:#bfdbfe;margin:4px 0 0;font-size:13px;">{st['branch_name']}</p>
  </div>
  <div style="background:#f9fafb;padding:24px;border:1px solid #e5e7eb;border-top:none;border-radius:0 0 8px 8px;">
    <h3 style="color:#111827;margin-top:0;">{st['name']}</h3>
    <p style="font-size:13px;color:#6b7280;margin-top:-8px;">{st['admission_id']} · {st.get('year_group','') or ''}</p>

    <div style="display:flex;gap:16px;margin-bottom:24px;flex-wrap:wrap;">
      <div style="flex:1;min-width:130px;background:#fff;border:1px solid #e5e7eb;border-radius:8px;padding:14px;text-align:center;border-top:3px solid {att_color};">
        <div style="font-size:11px;color:#6b7280;text-transform:uppercase;font-weight:600;">Attendance</div>
        <div style="font-size:28px;font-weight:700;color:{att_color};">{pct}%</div>
        <div style="font-size:11px;color:#9ca3af;">{present} present / {absent} absent</div>
      </div>
      <div style="flex:1;min-width:130px;background:#fff;border:1px solid #e5e7eb;border-radius:8px;padding:14px;text-align:center;border-top:3px solid #2563eb;">
        <div style="font-size:11px;color:#6b7280;text-transform:uppercase;font-weight:600;">Avg Test Score</div>
        <div style="font-size:28px;font-weight:700;color:#2563eb;">{str(avg_score)+'%' if avg_score is not None else '—'}</div>
        <div style="font-size:11px;color:#9ca3af;">{len(tests)} test{'s' if len(tests)!=1 else ''} recorded</div>
      </div>
      <div style="flex:1;min-width:130px;background:#fff;border:1px solid #e5e7eb;border-radius:8px;padding:14px;text-align:center;border-top:3px solid #059669;">
        <div style="font-size:11px;color:#6b7280;text-transform:uppercase;font-weight:600;">Progress Notes</div>
        <div style="font-size:28px;font-weight:700;color:#059669;">{len(prog)}</div>
        <div style="font-size:11px;color:#9ca3af;">from teachers</div>
      </div>
    </div>

    {'<h4 style="color:#1e3a5f;border-bottom:2px solid #2563eb;padding-bottom:6px;">Recent Test Results</h4><table style="width:100%;border-collapse:collapse;font-size:13px;"><thead><tr style="background:#f3f4f6;"><th style="padding:6px 8px;text-align:left;font-size:11px;color:#6b7280;">Date</th><th style="padding:6px 8px;text-align:left;font-size:11px;color:#6b7280;">Subject</th><th style="padding:6px 8px;text-align:left;font-size:11px;color:#6b7280;">Book/Unit</th><th style="padding:6px 8px;text-align:left;font-size:11px;color:#6b7280;">Score</th></tr></thead><tbody>'+tests_html+'</tbody></table>' if tests else ''}

    {'<h4 style="color:#1e3a5f;border-bottom:2px solid #2563eb;padding-bottom:6px;margin-top:20px;">Teacher Progress Notes</h4>'+prog_html if prog else ''}

    <hr style="border:none;border-top:1px solid #e5e7eb;margin:20px 0;">
    <p style="font-size:12px;color:#9ca3af;">This report was generated by the Fine Tutors management system. For queries, please contact your branch directly.</p>
  </div>
</div>"""

    sent_to = []
    try:
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(smtp_email, smtp_password)
        for r in recipients:
            msg = MIMEMultipart('alternative')
            msg['From']    = f"Fine Tutors <{smtp_email}>"
            msg['To']      = msg['From']
            msg['Subject'] = f"Progress Report — {st['name']} — Fine Tutors"
            msg.attach(MIMEText(html_body, 'html'))
            server.sendmail(smtp_email, r['email'], msg.as_string())
            sent_to.append(r['email'])
        server.quit()
    except Exception as ex:
        return jsonify({'error': str(ex)}), 500

    return jsonify({'ok': True, 'sent_to': sent_to})


# ── Dashboard Action Items ─────────────────────────────────────────────────────
@api_bp.route('/api/dashboard/action-items', methods=['GET'])
@require_auth
def dashboard_action_items():
    b = branch_scope()
    conn = get_conn(); cur = conn.cursor()
    bw = "AND s.branch_id=%s" if b else ""
    bw2 = "AND branch_id=%s" if b else ""
    p = (b,) if b else ()
    items = []

    # 1. Overdue invoices (unpaid, count + total)
    cur.execute(f"SELECT COUNT(*) as c, COALESCE(SUM(amount - COALESCE(amount_paid,0)),0) as total FROM invoices WHERE status!='paid' {bw2}", p)
    inv = cur.fetchone()
    if inv and inv['c']:
        items.append({
            'type': 'invoices',
            'severity': 'high' if float(inv['total']) > 500 else 'medium',
            'title': f"{inv['c']} unpaid invoice{'s' if inv['c']>1 else ''}",
            'detail': f"£{int(inv['total'])} outstanding",
            'nav': 'invoices',
        })

    # 2. Catch-up lessons owed
    cur.execute(f"SELECT COUNT(*) as c FROM catchup_lessons WHERE status='owed' {bw2}", p)
    cu = cur.fetchone()
    if cu and cu['c']:
        items.append({
            'type': 'catchup',
            'severity': 'medium',
            'title': f"{cu['c']} catch-up lesson{'s' if cu['c']>1 else ''} owed",
            'detail': "Students missed sessions without a scheduled catch-up",
            'nav': 'catchup',
        })

    # 3. Students with attendance below 75% (min 4 sessions)
    cur.execute(f"""
        SELECT COUNT(*) as c FROM (
            SELECT a.student_id,
                   COUNT(*) as total,
                   SUM(CASE WHEN a.status='present' THEN 1 ELSE 0 END) as present
            FROM attendance a
            JOIN sessions s ON s.id=a.session_id
            WHERE 1=1 {bw}
            GROUP BY a.student_id
            HAVING COUNT(*) >= 4
               AND SUM(CASE WHEN a.status='present' THEN 1 ELSE 0 END)::float/COUNT(*) < 0.75
        ) sub
    """, p)
    low_att = cur.fetchone()
    if low_att and low_att['c']:
        items.append({
            'type': 'attendance',
            'severity': 'high' if low_att['c'] > 3 else 'medium',
            'title': f"{low_att['c']} student{'s' if low_att['c']>1 else ''} with low attendance",
            'detail': "Below 75% — may need follow-up",
            'nav': 'student_progress',
        })

    # 4. Unchecked lesson reports (supervisor not yet reviewed)
    cur.execute(f"""
        SELECT COUNT(*) as c FROM lesson_reports lr
        JOIN sessions s ON s.id=lr.session_id
        WHERE lr.supervisor_checked=false {bw}
    """, p)
    unchecked = cur.fetchone()
    if unchecked and unchecked['c']:
        items.append({
            'type': 'lesson_reports',
            'severity': 'low',
            'title': f"{unchecked['c']} lesson report{'s' if unchecked['c']>1 else ''} awaiting review",
            'detail': "Supervisor check not completed",
            'nav': 'lesson_reports',
        })

    # 5. Students with test score below 60% in last 30 days
    cur.execute(f"""
        SELECT COUNT(*) as c FROM test_records
        WHERE score_pct < 60 AND test_date >= CURRENT_DATE - INTERVAL '30 days'
        {bw2.replace('branch_id','branch_id')}
    """, p)
    low_scores = cur.fetchone()
    if low_scores and low_scores['c']:
        items.append({
            'type': 'test_scores',
            'severity': 'medium',
            'title': f"{low_scores['c']} test result{'s' if low_scores['c']>1 else ''} below 60% (last 30 days)",
            'detail': "Review action plans and consider extra support",
            'nav': 'test_records',
        })

    cur.close(); conn.close()
    return jsonify({'items': items, 'count': len(items)})


# ── Dashboard Today Stats (injected via separate endpoint) ──────────────────
@api_bp.route('/api/dashboard/today', methods=['GET'])
@require_auth
def dashboard_today():
    import datetime as _dt
    b = branch_scope()
    conn = get_conn(); cur = conn.cursor()
    try:
        p = (b,) if b else ()
        bw  = "AND s.branch_id=%s" if b else ""
        bw2 = "AND branch_id=%s" if b else ""

        cur.execute("SELECT COUNT(*) as c FROM sessions s WHERE s.date=CURRENT_DATE" + (" AND s.branch_id=%s" if b else ""), p)
        today_sessions = cur.fetchone()['c']

        cur.execute(("SELECT s.id, s.slot, s.subject, s.table_no, b.name as branch_name, st.name as staff_name FROM sessions s JOIN branches b ON b.id=s.branch_id LEFT JOIN staff st ON st.id=s.staff_id WHERE s.date=CURRENT_DATE" + (" AND s.branch_id=%s" if b else "") + " ORDER BY s.slot, s.branch_name"), p)
        today_list = rows(cur)

        # If no sessions in sessions table, fall back to branch_schedule for today
        if today_sessions == 0:
            if b:
                cur.execute("""
                    SELECT bs.id, CONCAT(TO_CHAR(CURRENT_DATE,'Day'), ' Session ',
                           ROW_NUMBER() OVER (ORDER BY bs.slot_start),
                           ' (', TO_CHAR(bs.slot_start,'HH24:MI'), '–', TO_CHAR(bs.slot_end,'HH24:MI'), ')') as slot,
                           '' as subject, '' as table_no, br.name as branch_name, NULL as staff_name
                    FROM branch_schedule bs
                    JOIN branches br ON br.id=bs.branch_id
                    WHERE bs.branch_id=%s AND bs.status='active'
                      AND bs.day_of_week=%s
                      AND (bs.effective_from IS NULL OR bs.effective_from <= CURRENT_DATE)
                      AND (bs.effective_to IS NULL OR bs.effective_to >= CURRENT_DATE)
                    ORDER BY bs.slot_start
                """, (b, today_dow))
            else:
                cur.execute("""
                    SELECT bs.id, CONCAT(TO_CHAR(CURRENT_DATE,'Day'), ' Session ',
                           ROW_NUMBER() OVER (PARTITION BY bs.branch_id ORDER BY bs.slot_start),
                           ' (', TO_CHAR(bs.slot_start,'HH24:MI'), '–', TO_CHAR(bs.slot_end,'HH24:MI'), ')') as slot,
                           '' as subject, '' as table_no, br.name as branch_name, NULL as staff_name
                    FROM branch_schedule bs
                    JOIN branches br ON br.id=bs.branch_id
                    WHERE bs.status='active' AND bs.day_of_week=%s
                      AND (bs.effective_from IS NULL OR bs.effective_from <= CURRENT_DATE)
                      AND (bs.effective_to IS NULL OR bs.effective_to >= CURRENT_DATE)
                    ORDER BY br.name, bs.slot_start
                """, (today_dow,))
            sched_slots = rows(cur)
            today_sessions = len(sched_slots)
            today_list = sched_slots

        # Count expected from student_agreed_slots (QA uses agreed slots, not session_students)
        today_dow = ['monday','tuesday','wednesday','thursday','friday','saturday','sunday'][_dt.date.today().weekday()]
        if b:
            cur.execute("""
                SELECT COUNT(DISTINCT sas.student_id) as c
                FROM student_agreed_slots sas
                JOIN branch_schedule bs ON bs.id = sas.branch_schedule_id
                JOIN students stu ON stu.id = sas.student_id
                WHERE bs.branch_id=%s AND bs.day_of_week=%s AND bs.status='active'
                  AND stu.status='active' AND stu.branch_id=%s
                  AND (bs.effective_from IS NULL OR bs.effective_from <= CURRENT_DATE)
                  AND (bs.effective_to IS NULL OR bs.effective_to >= CURRENT_DATE)
            """, (b, today_dow, b))
        else:
            cur.execute("""
                SELECT COUNT(DISTINCT sas.student_id) as c
                FROM student_agreed_slots sas
                JOIN branch_schedule bs ON bs.id = sas.branch_schedule_id
                JOIN students stu ON stu.id = sas.student_id
                WHERE bs.day_of_week=%s AND bs.status='active'
                  AND stu.status='active'
                  AND (bs.effective_from IS NULL OR bs.effective_from <= CURRENT_DATE)
                  AND (bs.effective_to IS NULL OR bs.effective_to >= CURRENT_DATE)
            """, (today_dow,))
        today_expected = cur.fetchone()['c']

        cur.execute(("SELECT SUM(CASE WHEN a.status='present' THEN 1 ELSE 0 END) as present, COUNT(*) as marked FROM attendance a JOIN sessions s ON s.id=a.session_id WHERE s.date=CURRENT_DATE" + bw), p)
        today_att = cur.fetchone() or {}

        cur.execute(("SELECT COALESCE(SUM(amount),0) as total FROM payments WHERE TO_CHAR(payment_date,'YYYY-MM')=TO_CHAR(CURRENT_DATE,'YYYY-MM')" + bw2), p)
        month_revenue = int(cur.fetchone()['total'] or 0)

        cur.execute(("SELECT COUNT(*) as c FROM students WHERE TO_CHAR(created_at,'YYYY-MM')=TO_CHAR(CURRENT_DATE,'YYYY-MM') AND status='active'" + bw2), p)
        new_enrolments = cur.fetchone()['c']

        return jsonify({
            'today_sessions': today_sessions,
            'today_list': today_list,
            'today_expected': today_expected,
            'today_present': int(today_att.get('present') or 0),
            'today_marked': int(today_att.get('marked') or 0),
            'month_revenue': month_revenue,
            'new_enrolments': new_enrolments,
        })
    except Exception as e:
        conn.rollback()
        return jsonify({'today_sessions':0,'today_list':[],'today_expected':0,'today_present':0,'today_marked':0,'month_revenue':0,'new_enrolments':0,'error':str(e)})
    finally:
        cur.close(); conn.close()

@api_bp.route('/api/session-plan-students', methods=['GET'])
@require_auth
def get_session_plan_students():
    """Return students for lesson plan based on student_agreed_slots + branch_schedule.
    Query params: branch_id, date (YYYY-MM-DD)"""
    from datetime import datetime
    branch_id = request.args.get('branch_id', type=int) or branch_scope()
    date_str = request.args.get('date')
    if not branch_id or not date_str:
        return jsonify([])
    day_names = ['monday','tuesday','wednesday','thursday','friday','saturday','sunday']
    day_of_week = day_names[datetime.strptime(date_str, '%Y-%m-%d').weekday()]
    # weekday/saturday/sunday for backward compat with lesson plan filter
    day_type = 'saturday' if day_of_week == 'saturday' else 'sunday' if day_of_week == 'sunday' else 'weekday'
    conn = get_conn(); cur = conn.cursor()
    try:
        # Get students with agreed slots for this branch + day_of_week
        cur.execute("""
            WITH sched AS (
                SELECT id, slot_start, slot_end,
                       ROW_NUMBER() OVER (ORDER BY slot_start) AS session_num
                FROM branch_schedule
                WHERE branch_id = %s AND day_of_week = %s AND status = 'active'
                  AND (effective_from IS NULL OR effective_from <= %s)
                  AND (effective_to IS NULL OR effective_to >= %s)
            )
            SELECT
                s.id AS student_id,
                s.name AS student_name,
                s.admission_id,
                s.year_group,
                sc.id AS branch_schedule_id,
                sc.slot_start, sc.slot_end, sc.session_num,
                sas.subject AS agreed_subject,
                %s AS day_type,
                %s AS day_of_week
            FROM student_agreed_slots sas
            JOIN students s ON s.id = sas.student_id
            JOIN sched sc ON sc.id = sas.branch_schedule_id
            WHERE s.status = 'active' AND s.branch_id = %s
            ORDER BY s.admission_id, sc.slot_start
        """, (branch_id, day_of_week, date_str, date_str, day_type, day_of_week, branch_id))
        base_rows = rows(cur)
        if not base_rows:
            cur.close(); conn.close()
            return jsonify([])
        # Try to get subjects from student_timetable by matching slot start time
        cur.execute("""
            SELECT student_id, slot, subject
            FROM student_timetable
            WHERE branch_id = %s AND day_type = %s
        """, (branch_id, day_type))
        tt_rows = cur.fetchall()
        import re as _re
        # Build lookup: student_id -> {slot_start -> [subjects]}
        tt_map = {}
        for tr in tt_rows:
            sid = tr['student_id']
            slot_str = tr['slot'] or ''
            subj = tr['subject'] or ''
            m = _re.search(r'\((\d{1,2}:\d{2})', slot_str)
            if m:
                start = m.group(1).zfill(5)
                if sid not in tt_map: tt_map[sid] = {}
                if start not in tt_map[sid]: tt_map[sid][start] = []
                if subj and subj not in tt_map[sid][start]:
                    tt_map[sid][start].append(subj)
        # Build result: one row per student per slot per subject
        result = []
        day_cap = day_of_week.capitalize()
        for r2 in base_rows:
            sid = r2['student_id']
            raw_start = r2['slot_start']
            raw_end = r2['slot_end']
            # Convert time objects to HH:MM strings (psycopg2 returns datetime.time)
            start = raw_start.strftime('%H:%M') if hasattr(raw_start, 'strftime') else str(raw_start)[:5]
            end_str = raw_end.strftime('%H:%M') if hasattr(raw_end, 'strftime') else str(raw_end)[:5]
            num = int(r2['session_num'])
            slot_text = f"{day_cap} Session {num} ({start}\u2013{end_str})"
            row_base = {
                'student_id': sid, 'student_name': r2['student_name'],
                'admission_id': r2['admission_id'], 'year_group': r2['year_group'],
                'day_type': r2['day_type'], 'slot': slot_text,
                'branch_schedule_id': r2['branch_schedule_id'],
                'slot_start': start, 'slot_end': end_str
            }
            # Prefer subject stored on agreed_slot; fall back to student_timetable lookup
            agreed_subj = r2.get('agreed_subject') or ''
            if agreed_subj:
                result.append({**row_base, 'subject': agreed_subj})
            else:
                subjects = tt_map.get(sid, {}).get(start, [])
                if subjects:
                    for subj in subjects:
                        result.append({**row_base, 'subject': subj})
                else:
                    result.append({**row_base, 'subject': ''})
        cur.close(); conn.close()
        return jsonify(result)
    except Exception as e:
        cur.close(); conn.close()
        return jsonify({'error': str(e)}), 400

# ════════════════════════════════════════════
#  ATTENDANCE REPORT
# ════════════════════════════════════════════
@api_bp.route('/api/attendance/report', methods=['GET'])
@require_auth
def attendance_report():
    """Attendance report with filters.
    Params: branch_id, date_from, date_to, student_id, day_type, subject, status"""
    b = branch_scope()
    branch_id = request.args.get('branch_id', type=int) or b
    date_from  = request.args.get('date_from')  or '2020-01-01'
    date_to    = request.args.get('date_to')    or '2099-12-31'
    student_id = request.args.get('student_id', type=int)
    day_type   = request.args.get('day_type')   # saturday/sunday/weekday
    subject    = request.args.get('subject')
    status     = request.args.get('status')     # present/absent

    conn = get_conn(); cur = conn.cursor()
    try:
        conditions = ["sess.date BETWEEN %s AND %s"]
        params     = [date_from, date_to]

        if branch_id:
            conditions.append("sess.branch_id = %s"); params.append(branch_id)
        if student_id:
            conditions.append("a.student_id = %s"); params.append(student_id)
        if subject:
            conditions.append("sess.subject ILIKE %s"); params.append(f'%{subject}%')
        if status:
            conditions.append("a.status = %s"); params.append(status)
        if day_type:
            if day_type == 'saturday':
                conditions.append("EXTRACT(DOW FROM sess.date) = 6")
            elif day_type == 'sunday':
                conditions.append("EXTRACT(DOW FROM sess.date) = 0")
            else:
                conditions.append("EXTRACT(DOW FROM sess.date) BETWEEN 1 AND 5")

        where = ' AND '.join(conditions)
        cur.execute(f"""
            SELECT
                a.student_id,
                s.name AS student_name,
                s.admission_id,
                s.year_group,
                s.branch_id,
                br.name AS branch_name,
                sess.id  AS session_id,
                sess.date,
                sess.slot,
                sess.subject,
                sess.table_no,
                a.status,
                a.notes
            FROM attendance a
            JOIN students s   ON s.id   = a.student_id
            JOIN sessions sess ON sess.id = a.session_id
            JOIN branches br  ON br.id  = sess.branch_id
            WHERE {where}
            ORDER BY sess.date DESC, sess.slot, s.admission_id
        """, params)
        result = cur.fetchall()
        data = []
        for r in result:
            row = dict(r)
            for k, v in row.items():
                if hasattr(v, 'isoformat'): row[k] = str(v)
            data.append(row)
        cur.close(); conn.close()
        return jsonify(data)
    except Exception as e:
        cur.close(); conn.close()
        return jsonify({'error': str(e)}), 400
