import os
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv()

def get_conn():
    return psycopg2.connect(
        os.environ['DATABASE_URL'],
        cursor_factory=psycopg2.extras.RealDictCursor
    )

SCHEMA = """
-- ── BRANCHES ──
CREATE TABLE IF NOT EXISTS branches (
    id          SERIAL PRIMARY KEY,
    name        TEXT NOT NULL,
    prefix      CHAR(1) NOT NULL UNIQUE,
    address     TEXT,
    phone       TEXT,
    email       TEXT,
    status      TEXT NOT NULL DEFAULT 'active',
    created_at  TIMESTAMP DEFAULT NOW()
);

-- ── USERS (staff logins) ──
CREATE TABLE IF NOT EXISTS users (
    id            SERIAL PRIMARY KEY,
    branch_id     INT REFERENCES branches(id) ON DELETE SET NULL,
    name          TEXT NOT NULL,
    email         TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL CHECK (role IN ('super_admin','branch_manager','teacher','receptionist')),
    status        TEXT NOT NULL DEFAULT 'active',
    last_login    TIMESTAMP,
    created_at    TIMESTAMP DEFAULT NOW()
);

-- ── PARENT ACCOUNTS ──
CREATE TABLE IF NOT EXISTS parent_users (
    id            SERIAL PRIMARY KEY,
    name          TEXT NOT NULL,
    email         TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'active',
    created_at    TIMESTAMP DEFAULT NOW()
);

-- ── PARENT ↔ STUDENT LINK ──
CREATE TABLE IF NOT EXISTS parent_students (
    parent_id  INT REFERENCES parent_users(id) ON DELETE CASCADE,
    student_id INT,
    PRIMARY KEY (parent_id, student_id)
);

-- ── STUDENTS ──
CREATE TABLE IF NOT EXISTS students (
    id           SERIAL PRIMARY KEY,
    branch_id    INT NOT NULL REFERENCES branches(id) ON DELETE CASCADE,
    admission_id TEXT NOT NULL UNIQUE,
    name         TEXT NOT NULL,
    year_group   TEXT,
    parent_contact TEXT,
    status       TEXT NOT NULL DEFAULT 'active',
    notes        TEXT,
    created_at   TIMESTAMP DEFAULT NOW()
);

-- ── STAFF ──
CREATE TABLE IF NOT EXISTS staff (
    id         SERIAL PRIMARY KEY,
    branch_id  INT NOT NULL REFERENCES branches(id) ON DELETE CASCADE,
    name       TEXT NOT NULL,
    role       TEXT NOT NULL DEFAULT 'teacher',
    subject    TEXT,
    contact    TEXT,
    status     TEXT NOT NULL DEFAULT 'active',
    created_at TIMESTAMP DEFAULT NOW()
);

-- ── SESSIONS ──
CREATE TABLE IF NOT EXISTS sessions (
    id         SERIAL PRIMARY KEY,
    branch_id  INT NOT NULL REFERENCES branches(id) ON DELETE CASCADE,
    staff_id   INT REFERENCES staff(id) ON DELETE SET NULL,
    date       DATE NOT NULL,
    slot       TEXT NOT NULL,
    subject    TEXT,
    table_no   INT DEFAULT 1,
    created_at TIMESTAMP DEFAULT NOW()
);

-- ── ATTENDANCE ──
CREATE TABLE IF NOT EXISTS attendance (
    id         SERIAL PRIMARY KEY,
    session_id INT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    student_id INT NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    status     TEXT NOT NULL CHECK (status IN ('present','absent')) DEFAULT 'present',
    notes      TEXT,
    UNIQUE (session_id, student_id)
);

-- ── INVOICES ──
CREATE TABLE IF NOT EXISTS invoices (
    id         SERIAL PRIMARY KEY,
    student_id INT NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    branch_id  INT NOT NULL REFERENCES branches(id) ON DELETE CASCADE,
    month      TEXT NOT NULL,
    amount     INT NOT NULL DEFAULT 120,
    status     TEXT NOT NULL DEFAULT 'due' CHECK (status IN ('due','paid','overdue')),
    issued     DATE NOT NULL DEFAULT CURRENT_DATE,
    paid_date  DATE,
    notes      TEXT,
    UNIQUE (student_id, month)
);

-- ── PROGRESS NOTES ──
CREATE TABLE IF NOT EXISTS progress (
    id         SERIAL PRIMARY KEY,
    student_id INT NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    session_id INT REFERENCES sessions(id) ON DELETE SET NULL,
    staff_id   INT REFERENCES staff(id) ON DELETE SET NULL,
    subject    TEXT,
    rating     INT CHECK (rating BETWEEN 1 AND 5),
    comment    TEXT,
    date       DATE NOT NULL DEFAULT CURRENT_DATE,
    created_at TIMESTAMP DEFAULT NOW()
);

-- ── PAYMENTS ──
CREATE TABLE IF NOT EXISTS payments (
    id             SERIAL PRIMARY KEY,
    student_id     INT NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    branch_id      INT NOT NULL REFERENCES branches(id) ON DELETE CASCADE,
    amount         NUMERIC(10,2) NOT NULL,
    payment_date   DATE NOT NULL DEFAULT CURRENT_DATE,
    method         TEXT NOT NULL DEFAULT 'cash' CHECK (method IN ('cash','bank_transfer','cheque','card','other')),
    reference      TEXT,
    notes          TEXT,
    recorded_by    INT REFERENCES users(id) ON DELETE SET NULL,
    created_at     TIMESTAMP DEFAULT NOW()
);

-- ── INSTALMENT PLANS ──
CREATE TABLE IF NOT EXISTS instalment_plans (
    id             SERIAL PRIMARY KEY,
    student_id     INT NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    branch_id      INT NOT NULL REFERENCES branches(id) ON DELETE CASCADE,
    total_amount   NUMERIC(10,2) NOT NULL,
    description    TEXT NOT NULL,
    start_date     DATE NOT NULL,
    end_date       DATE,
    status         TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','completed','cancelled')),
    notes          TEXT,
    created_by     INT REFERENCES users(id) ON DELETE SET NULL,
    created_at     TIMESTAMP DEFAULT NOW()
);

-- ── INSTALMENT SCHEDULE ──
CREATE TABLE IF NOT EXISTS instalment_schedule (
    id             SERIAL PRIMARY KEY,
    plan_id        INT NOT NULL REFERENCES instalment_plans(id) ON DELETE CASCADE,
    student_id     INT NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    due_date       DATE NOT NULL,
    amount         NUMERIC(10,2) NOT NULL,
    status         TEXT NOT NULL DEFAULT 'due' CHECK (status IN ('due','paid','overdue')),
    paid_date      DATE,
    payment_id     INT REFERENCES payments(id) ON DELETE SET NULL,
    notes          TEXT
);

-- ── STAFF ATTENDANCE ──
CREATE TABLE IF NOT EXISTS staff_attendance (
    id           SERIAL PRIMARY KEY,
    session_id   INT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    staff_id     INT NOT NULL REFERENCES staff(id) ON DELETE CASCADE,
    branch_id    INT NOT NULL REFERENCES branches(id) ON DELETE CASCADE,
    date         DATE NOT NULL,
    sign_in      TIME,
    sign_out     TIME,
    status       TEXT NOT NULL DEFAULT 'present' CHECK (status IN ('present','absent','late','no_sign_out')),
    cover_for    INT REFERENCES staff(id) ON DELETE SET NULL,
    absence_reason TEXT,
    notes        TEXT,
    created_at   TIMESTAMP DEFAULT NOW(),
    UNIQUE (session_id, staff_id)
);

-- ── AUDIT LOG ──
CREATE TABLE IF NOT EXISTS audit_log (
    id          SERIAL PRIMARY KEY,
    user_id     INT,
    user_name   TEXT,
    branch_id   INT,
    action      TEXT NOT NULL,
    table_name  TEXT,
    record_id   TEXT,
    ip_address  TEXT,
    timestamp   TIMESTAMP DEFAULT NOW()
);

-- ── INDEXES ──
CREATE INDEX IF NOT EXISTS idx_students_branch ON students(branch_id);
CREATE INDEX IF NOT EXISTS idx_staff_branch ON staff(branch_id);
CREATE INDEX IF NOT EXISTS idx_sessions_branch ON sessions(branch_id);
CREATE INDEX IF NOT EXISTS idx_sessions_date ON sessions(date);
CREATE INDEX IF NOT EXISTS idx_attendance_session ON attendance(session_id);
CREATE INDEX IF NOT EXISTS idx_attendance_student ON attendance(student_id);
CREATE INDEX IF NOT EXISTS idx_invoices_student ON invoices(student_id);
CREATE INDEX IF NOT EXISTS idx_progress_student ON progress(student_id);
CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_log(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_payments_student ON payments(student_id);
CREATE INDEX IF NOT EXISTS idx_payments_date ON payments(payment_date DESC);
CREATE INDEX IF NOT EXISTS idx_instalment_plans_student ON instalment_plans(student_id);
CREATE INDEX IF NOT EXISTS idx_instalment_schedule_plan ON instalment_schedule(plan_id);
CREATE INDEX IF NOT EXISTS idx_instalment_schedule_student ON instalment_schedule(student_id);
CREATE INDEX IF NOT EXISTS idx_staff_att_session ON staff_attendance(session_id);
CREATE INDEX IF NOT EXISTS idx_staff_att_staff ON staff_attendance(staff_id);
CREATE INDEX IF NOT EXISTS idx_staff_att_date ON staff_attendance(date);
"""

SEED = """
-- ── SEED DATA ──
INSERT INTO branches (name, prefix, address, phone, email) VALUES
  ('Harlesden', 'H', 'Station Road, Harlesden, NW10', '020 8000 0001', 'harlesden@ftharlesden.co.uk'),
  ('Wembley',   'W', 'High Road, Wembley, HA9',       '020 8000 0002', 'wembley@ftharlesden.co.uk'),
  ('Brent',     'B', 'Brent Cross, NW2',               '020 8000 0003', 'brent@ftharlesden.co.uk')
ON CONFLICT DO NOTHING;

-- password hashes are bcrypt of the demo passwords
-- using werkzeug pbkdf2 here for simplicity (set in init_db.py)
"""

def init_db():
    """Create all tables and seed initial data."""
    from werkzeug.security import generate_password_hash
    conn = get_conn()
    cur = conn.cursor()

    # Create schema
    cur.execute(SCHEMA)

    # Seed branches
    cur.execute("""
        INSERT INTO branches (name, prefix, address, phone, email) VALUES
          ('Harlesden', 'H', 'Station Road, Harlesden, NW10', '020 8000 0001', 'harlesden@ftharlesden.co.uk'),
          ('Wembley',   'W', 'High Road, Wembley, HA9',       '020 8000 0002', 'wembley@ftharlesden.co.uk'),
          ('Brent',     'B', 'Brent Cross, NW2',               '020 8000 0003', 'brent@ftharlesden.co.uk')
        ON CONFLICT DO NOTHING
    """)

    # Seed staff users
    staff_users = [
        (None, 'M. Rahman',  'm.rahman@ftharlesden.co.uk',  'admin123',   'super_admin'),
        (1,    'A. Khan',    'a.khan@ftharlesden.co.uk',    'manager123', 'branch_manager'),
        (2,    'R. Patel',   'r.patel@ftharlesden.co.uk',   'manager123', 'branch_manager'),
        (1,    'S. Ahmed',   's.ahmed@ftharlesden.co.uk',   'teacher123', 'teacher'),
        (1,    'Luaay H.',   'luaay@ftharlesden.co.uk',     'teacher123', 'teacher'),
        (1,    'Ruwayda M.', 'ruwayda@ftharlesden.co.uk',   'teacher123', 'teacher'),
        (2,    'T. Hussain', 't.hussain@ftharlesden.co.uk', 'teacher123', 'teacher'),
        (1,    'F. Omar',    'f.omar@ftharlesden.co.uk',    'recep123',   'receptionist'),
    ]
    for branch_id, name, email, password, role in staff_users:
        cur.execute("""
            INSERT INTO users (branch_id, name, email, password_hash, role)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (email) DO NOTHING
        """, (branch_id, name, email, generate_password_hash(password), role))

    # Seed staff records
    staff_records = [
        (1, 'S. Ahmed',    'teacher',     'Maths',   '07700 900001'),
        (1, 'Luaay H.',    'teacher',     'English', '07700 900002'),
        (1, 'Ruwayda M.',  'teacher',     'Science', '07700 900003'),
        (1, 'F. Omar',     'receptionist','-',       '07700 900004'),
        (2, 'T. Hussain',  'teacher',     'Maths',   '07700 900005'),
        (2, 'N. Rahman',   'teacher',     'English', '07700 900006'),
        (3, 'B. Osei',     'teacher',     'Maths',   '07700 900007'),
    ]
    for branch_id, name, role, subject, contact in staff_records:
        cur.execute("""
            INSERT INTO staff (branch_id, name, role, subject, contact)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT DO NOTHING
        """, (branch_id, name, role, subject, contact))

    # Seed students
    students = [
        (1,'H100','Omed Masjidi',   'Year 11','07700 000100','1-to-1 student'),
        (1,'H101','Zara Hussain',   'Year 9', '07700 000101',''),
        (1,'H102','Adam Malik',     'Year 8', '07700 000102',''),
        (1,'H103','Fatima Noor',    'Year 10','07700 000103',''),
        (1,'H104','Yusuf Ali',      'Year 7', '07700 000104',''),
        (1,'H110','Sara Ahmed',     'Year 9', '07700 000110','Sibling'),
        (1,'H110a','Omar Ahmed',    'Year 7', '07700 000110','Sibling'),
        (1,'H110b','Leila Ahmed',   'Year 6', '07700 000110','Sibling'),
        (2,'W100','James Okonkwo',  'Year 10','07700 000200',''),
        (2,'W101','Priya Sharma',   'Year 8', '07700 000201',''),
        (2,'W102','Daniel Foster',  'Year 9', '07700 000202',''),
        (3,'B100','Amara Diallo',   'Year 11','07700 000300',''),
        (3,'B101','Chen Wei',       'Year 10','07700 000301',''),
    ]
    for branch_id, adm_id, name, year, parent, notes in students:
        cur.execute("""
            INSERT INTO students (branch_id, admission_id, name, year_group, parent_contact, notes)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (admission_id) DO NOTHING
        """, (branch_id, adm_id, name, year, parent, notes))

    # Seed sessions
    cur.execute("""
        INSERT INTO sessions (branch_id, staff_id, date, slot, subject, table_no) VALUES
          (1, 1, '2026-05-02', 'Slot 1 (09:00-11:00)', 'Maths',   1),
          (1, 2, '2026-05-02', 'Slot 2 (11:30-13:30)', 'English', 2),
          (1, 3, '2026-05-02', 'Slot 3 (14:00-16:00)', 'Science', 3),
          (1, 1, '2026-05-09', 'Slot 1 (09:00-11:00)', 'Maths',   1),
          (2, 5, '2026-05-09', 'Slot 1 (09:00-11:00)', 'Maths',   1)
        ON CONFLICT DO NOTHING
    """)

    # Seed attendance (get session/student IDs dynamically)
    cur.execute("SELECT id FROM sessions ORDER BY id LIMIT 5")
    sess_ids = [r['id'] for r in cur.fetchall()]
    cur.execute("SELECT id FROM students ORDER BY id LIMIT 11")
    st_ids = [r['id'] for r in cur.fetchall()]

    if len(sess_ids) >= 5 and len(st_ids) >= 10:
        att = [
            (sess_ids[0], st_ids[0], 'present',''),
            (sess_ids[0], st_ids[1], 'present',''),
            (sess_ids[0], st_ids[2], 'absent', 'Unwell'),
            (sess_ids[0], st_ids[3], 'present',''),
            (sess_ids[0], st_ids[4], 'present',''),
            (sess_ids[1], st_ids[1], 'present',''),
            (sess_ids[1], st_ids[5], 'present',''),
            (sess_ids[1], st_ids[6], 'present',''),
            (sess_ids[3], st_ids[0], 'present',''),
            (sess_ids[4], st_ids[8], 'present',''),
            (sess_ids[4], st_ids[9], 'present',''),
        ]
        for sid, stid, status, notes in att:
            cur.execute("""
                INSERT INTO attendance (session_id, student_id, status, notes)
                VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING
            """, (sid, stid, status, notes))

    # Seed invoices
    cur.execute("SELECT id, branch_id FROM students ORDER BY id LIMIT 10")
    sts = cur.fetchall()
    inv_data = [
        ('paid','2026-05-03'),('paid','2026-05-07'),('overdue',None),
        ('paid','2026-05-04'),('due',None),('paid','2026-05-10'),
        ('paid','2026-05-10'),('due',None),('paid','2026-05-05'),('overdue',None),
    ]
    for i, st in enumerate(sts):
        status, paid_date = inv_data[i] if i < len(inv_data) else ('due', None)
        amt = 100 if i in [6,7] else 120
        cur.execute("""
            INSERT INTO invoices (student_id, branch_id, month, amount, status, issued, paid_date)
            VALUES (%s, %s, '2026-05', %s, %s, '2026-05-01', %s)
            ON CONFLICT DO NOTHING
        """, (st['id'], st['branch_id'], amt, status, paid_date))

    # Seed progress notes
    if len(sess_ids) >= 1 and len(st_ids) >= 5:
        cur.execute("SELECT id FROM staff ORDER BY id LIMIT 3")
        staff_ids = [r['id'] for r in cur.fetchall()]
        if staff_ids:
            prog = [
                (st_ids[0], sess_ids[0], staff_ids[0], 'Maths',   5, 'Excellent work on quadratics. Ready for next topic.'),
                (st_ids[1], sess_ids[0], staff_ids[0], 'Maths',   4, 'Good understanding. Needs more practice on surds.'),
                (st_ids[1], sess_ids[1], staff_ids[1], 'English', 4, 'Strong essay structure. Work on vocabulary range.'),
                (st_ids[3], sess_ids[0], staff_ids[0], 'Maths',   3, 'Struggling with algebra. Extra homework set.'),
                (st_ids[4], sess_ids[0], staff_ids[0], 'Maths',   4, 'Consistent progress. Algebra improving well.'),
            ]
            for stid, sessid, sfid, subj, rating, comment in prog:
                cur.execute("""
                    INSERT INTO progress (student_id, session_id, staff_id, subject, rating, comment, date)
                    VALUES (%s, %s, %s, %s, %s, %s, '2026-05-02')
                    ON CONFLICT DO NOTHING
                """, (stid, sessid, sfid, subj, rating, comment))

    # Seed parent users
    parent_accounts = [
        ('Mr Masjidi',  'parent.h100@ftharlesden.co.uk', 'parent123'),
        ('Mrs Hussain', 'parent.h101@ftharlesden.co.uk', 'parent123'),
        ('Mr Ahmed',    'parent.h110@ftharlesden.co.uk', 'parent123'),
    ]
    for name, email, password in parent_accounts:
        cur.execute("""
            INSERT INTO parent_users (name, email, password_hash)
            VALUES (%s, %s, %s) ON CONFLICT (email) DO NOTHING
        """, (name, email, generate_password_hash(password)))

    # Link parents to students
    cur.execute("SELECT id FROM parent_users ORDER BY id LIMIT 3")
    pids = [r['id'] for r in cur.fetchall()]
    cur.execute("SELECT id, admission_id FROM students WHERE admission_id IN ('H100','H101','H110','H110a','H110b') ORDER BY admission_id")
    st_map = {r['admission_id']: r['id'] for r in cur.fetchall()}

    links = []
    if len(pids) >= 3:
        if 'H100' in st_map:  links.append((pids[0], st_map['H100']))
        if 'H101' in st_map:  links.append((pids[1], st_map['H101']))
        for aid in ['H110','H110a','H110b']:
            if aid in st_map: links.append((pids[2], st_map[aid]))
    for pid, stid in links:
        cur.execute("INSERT INTO parent_students (parent_id, student_id) VALUES (%s,%s) ON CONFLICT DO NOTHING", (pid, stid))

    # Seed staff attendance
    if len(sess_ids) >= 2 and staff_ids:
        staff_att = [
            (sess_ids[0], staff_ids[0], 1, '2026-05-02', '09:00', '11:05', 'present', None, ''),
            (sess_ids[1], staff_ids[1] if len(staff_ids)>1 else staff_ids[0], 1, '2026-05-02', '11:35', '13:30', 'present', None, ''),
            (sess_ids[2], staff_ids[2] if len(staff_ids)>2 else staff_ids[0], 1, '2026-05-02', '14:00', None, 'no_sign_out', None, 'No sign-out recorded'),
            (sess_ids[3], staff_ids[0], 1, '2026-05-09', '08:55', '11:10', 'present', None, ''),
            (sess_ids[4], staff_ids[4] if len(staff_ids)>4 else staff_ids[0], 2, '2026-05-09', '09:15', '11:00', 'late', None, 'Arrived 15 mins late'),
        ]
        for sessid, sfid, brid, dt, sin, sout, status, cover, notes in staff_att:
            cur.execute("""
                INSERT INTO staff_attendance (session_id, staff_id, branch_id, date, sign_in, sign_out, status, cover_for, notes)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING
            """, (sessid, sfid, brid, dt, sin, sout, status, cover, notes))

    # Seed payments
    if len(st_ids) >= 5:
        payments_data = [
            (st_ids[0], 1, 120.00, '2026-05-03', 'bank_transfer', 'REF001', 'May tuition - full'),
            (st_ids[1], 1,  60.00, '2026-05-07', 'cash',          '',      'Part payment May'),
            (st_ids[2], 1, 120.00, '2026-05-04', 'bank_transfer', 'REF003', 'May tuition - full'),
            (st_ids[3], 1,  80.00, '2026-05-10', 'cash',          '',      'Part payment - balance c/f'),
            (st_ids[4], 1, 120.00, '2026-05-06', 'bank_transfer', 'REF005', 'May tuition - full'),
            (st_ids[5], 1, 100.00, '2026-05-10', 'cash',          '',      'Family payment - siblings'),
            (st_ids[8], 2, 120.00, '2026-05-05', 'bank_transfer', 'REF009', 'May tuition'),
        ]
        for stid, brid, amt, dt, method, ref, notes in payments_data:
            cur.execute("""
                INSERT INTO payments (student_id, branch_id, amount, payment_date, method, reference, notes)
                VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING
            """, (stid, brid, amt, dt, method, ref, notes))

        # Seed instalment plan
        cur.execute("""
            INSERT INTO instalment_plans (student_id, branch_id, total_amount, description, start_date, end_date, status, notes)
            VALUES (%s,%s,360.00,'3-month instalment plan - Maths & English','2026-05-01','2026-07-31','active','Agreed with parent 01 May 2026')
            ON CONFLICT DO NOTHING
        """, (st_ids[3], 1))
        cur.execute("SELECT id FROM instalment_plans WHERE student_id=%s LIMIT 1", (st_ids[3],))
        plan = cur.fetchone()
        if plan:
            for dt, amt in [('2026-05-01',120),('2026-06-01',120),('2026-07-01',120)]:
                cur.execute("""
                    INSERT INTO instalment_schedule (plan_id, student_id, due_date, amount, status)
                    VALUES (%s,%s,%s,%s,'due') ON CONFLICT DO NOTHING
                """, (plan['id'], st_ids[3], dt, amt))

    conn.commit()
    cur.close()
    conn.close()
    print("Database initialised and seeded successfully.")

if __name__ == '__main__':
    init_db()
