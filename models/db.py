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
    id                  SERIAL PRIMARY KEY,
    branch_id           INT NOT NULL REFERENCES branches(id) ON DELETE CASCADE,
    admission_id        TEXT NOT NULL UNIQUE,
    name                TEXT NOT NULL,
    first_name          TEXT,
    last_name           TEXT,
    date_of_birth       DATE,
    gender              TEXT,
    year_group          TEXT,
    current_school      TEXT,
    medical_notes       TEXT,
    sen_notes           TEXT,
    -- Parent / Carer 1
    carer1_first_name   TEXT,
    carer1_last_name    TEXT,
    carer1_address      TEXT,
    carer1_telephone    TEXT,
    carer1_mobile       TEXT,
    carer1_email        TEXT,
    carer1_occupation   TEXT,
    -- Parent / Carer 2
    carer2_first_name   TEXT,
    carer2_last_name    TEXT,
    carer2_address      TEXT,
    carer2_telephone    TEXT,
    carer2_mobile       TEXT,
    carer2_email        TEXT,
    carer2_occupation   TEXT,
    -- Emergency contact
    emergency_name      TEXT,
    emergency_telephone TEXT,
    emergency_relation  TEXT,
    -- Reference
    referred_by         TEXT,
    referral_admission  TEXT,
    -- Legacy fields kept for compatibility
    parent_contact      TEXT,
    status              TEXT NOT NULL DEFAULT 'active',
    notes               TEXT,
    created_at          TIMESTAMP DEFAULT NOW()
);

-- Migration: add new columns if they don't exist (safe to run multiple times)
DO $$ BEGIN
    ALTER TABLE students ADD COLUMN IF NOT EXISTS first_name TEXT;
    ALTER TABLE students ADD COLUMN IF NOT EXISTS last_name TEXT;
    ALTER TABLE students ADD COLUMN IF NOT EXISTS date_of_birth DATE;
    ALTER TABLE students ADD COLUMN IF NOT EXISTS gender TEXT;
    ALTER TABLE students ADD COLUMN IF NOT EXISTS current_school TEXT;
    ALTER TABLE students ADD COLUMN IF NOT EXISTS medical_notes TEXT;
    ALTER TABLE students ADD COLUMN IF NOT EXISTS sen_notes TEXT;
    ALTER TABLE students ADD COLUMN IF NOT EXISTS carer1_first_name TEXT;
    ALTER TABLE students ADD COLUMN IF NOT EXISTS carer1_last_name TEXT;
    ALTER TABLE students ADD COLUMN IF NOT EXISTS carer1_address TEXT;
    ALTER TABLE students ADD COLUMN IF NOT EXISTS carer1_telephone TEXT;
    ALTER TABLE students ADD COLUMN IF NOT EXISTS carer1_mobile TEXT;
    ALTER TABLE students ADD COLUMN IF NOT EXISTS carer1_email TEXT;
    ALTER TABLE students ADD COLUMN IF NOT EXISTS carer1_occupation TEXT;
    ALTER TABLE students ADD COLUMN IF NOT EXISTS carer2_first_name TEXT;
    ALTER TABLE students ADD COLUMN IF NOT EXISTS carer2_last_name TEXT;
    ALTER TABLE students ADD COLUMN IF NOT EXISTS carer2_address TEXT;
    ALTER TABLE students ADD COLUMN IF NOT EXISTS carer2_telephone TEXT;
    ALTER TABLE students ADD COLUMN IF NOT EXISTS carer2_mobile TEXT;
    ALTER TABLE students ADD COLUMN IF NOT EXISTS carer2_email TEXT;
    ALTER TABLE students ADD COLUMN IF NOT EXISTS carer2_occupation TEXT;
    ALTER TABLE students ADD COLUMN IF NOT EXISTS emergency_name TEXT;
    ALTER TABLE students ADD COLUMN IF NOT EXISTS emergency_telephone TEXT;
    ALTER TABLE students ADD COLUMN IF NOT EXISTS emergency_relation TEXT;
    ALTER TABLE students ADD COLUMN IF NOT EXISTS referred_by TEXT;
    ALTER TABLE students ADD COLUMN IF NOT EXISTS referral_admission TEXT;
    ALTER TABLE students ADD COLUMN IF NOT EXISTS carer1_postcode TEXT;
    ALTER TABLE students ADD COLUMN IF NOT EXISTS carer2_postcode TEXT;
    ALTER TABLE students ADD COLUMN IF NOT EXISTS gcse_maths_board TEXT;
    ALTER TABLE students ADD COLUMN IF NOT EXISTS gcse_maths_paper TEXT;
    ALTER TABLE students ADD COLUMN IF NOT EXISTS gcse_maths_exam_date TEXT;
    ALTER TABLE students ADD COLUMN IF NOT EXISTS gcse_maths_current_grade TEXT;
    ALTER TABLE students ADD COLUMN IF NOT EXISTS gcse_maths_predicted_grade TEXT;
    ALTER TABLE students ADD COLUMN IF NOT EXISTS gcse_english_board TEXT;
    ALTER TABLE students ADD COLUMN IF NOT EXISTS gcse_english_paper TEXT;
    ALTER TABLE students ADD COLUMN IF NOT EXISTS gcse_english_exam_date TEXT;
    ALTER TABLE students ADD COLUMN IF NOT EXISTS gcse_english_current_grade TEXT;
    ALTER TABLE students ADD COLUMN IF NOT EXISTS gcse_english_predicted_grade TEXT;
    ALTER TABLE students ADD COLUMN IF NOT EXISTS gcse_science_board TEXT;
    ALTER TABLE students ADD COLUMN IF NOT EXISTS gcse_science_paper TEXT;
    ALTER TABLE students ADD COLUMN IF NOT EXISTS gcse_science_exam_date TEXT;
    ALTER TABLE students ADD COLUMN IF NOT EXISTS gcse_science_current_grade TEXT;
    ALTER TABLE students ADD COLUMN IF NOT EXISTS gcse_science_predicted_grade TEXT;
    ALTER TABLE students ADD COLUMN IF NOT EXISTS assess_maths_pct TEXT;
    ALTER TABLE students ADD COLUMN IF NOT EXISTS assess_maths_book TEXT;
    ALTER TABLE students ADD COLUMN IF NOT EXISTS assess_english_pct TEXT;
    ALTER TABLE students ADD COLUMN IF NOT EXISTS assess_english_book TEXT;
    ALTER TABLE students ADD COLUMN IF NOT EXISTS assess_science_pct TEXT;
    ALTER TABLE students ADD COLUMN IF NOT EXISTS assess_science_book TEXT;
    ALTER TABLE students ADD COLUMN IF NOT EXISTS hours_per_week TEXT;
    -- Update payment method constraint to include direct_debit and standing_order
    -- Student timetable and table allocation tables (handled by CREATE TABLE IF NOT EXISTS)
    -- Session students and catchup tables (handled by CREATE TABLE IF NOT EXISTS)
    ALTER TABLE sessions ADD COLUMN IF NOT EXISTS cover_staff_id INT REFERENCES staff(id) ON DELETE SET NULL;
    ALTER TABLE sessions ADD COLUMN IF NOT EXISTS cover_notes TEXT;
    -- Make staff_attendance.session_id nullable
    ALTER TABLE staff_attendance ALTER COLUMN session_id DROP NOT NULL;
    -- Create hq_transfers if not exists (handled by CREATE TABLE IF NOT EXISTS above)
    -- Lesson reports and test records (handled by CREATE TABLE IF NOT EXISTS)
    ALTER TABLE payments DROP CONSTRAINT IF EXISTS payments_method_check;
    ALTER TABLE payments ADD CONSTRAINT payments_method_check
        CHECK (method IN ('cash','bank_transfer','cheque','card','direct_debit','standing_order','other'));
END $$;

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
    method         TEXT NOT NULL DEFAULT 'cash' CHECK (method IN ('cash','bank_transfer','cheque','card','direct_debit','standing_order','other')),
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

-- ── HQ TRANSFERS ──
CREATE TABLE IF NOT EXISTS hq_transfers (
    id             SERIAL PRIMARY KEY,
    branch_id      INT NOT NULL REFERENCES branches(id) ON DELETE CASCADE,
    amount         NUMERIC(10,2) NOT NULL,
    transfer_date  DATE NOT NULL DEFAULT CURRENT_DATE,
    method         TEXT NOT NULL DEFAULT 'cash' CHECK (method IN ('cash','bank_transfer','cheque','other')),
    reference      TEXT,
    notes          TEXT,
    recorded_by    INT REFERENCES users(id) ON DELETE SET NULL,
    created_at     TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_hq_transfers_branch ON hq_transfers(branch_id);
CREATE INDEX IF NOT EXISTS idx_hq_transfers_date ON hq_transfers(transfer_date DESC);

-- ── STUDENT TIMETABLE (which slots/subjects a student attends) ──
CREATE TABLE IF NOT EXISTS student_timetable (
    id          SERIAL PRIMARY KEY,
    student_id  INT NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    branch_id   INT NOT NULL REFERENCES branches(id) ON DELETE CASCADE,
    day_type    TEXT NOT NULL CHECK (day_type IN ('weekday','saturday','sunday')),
    slot        TEXT NOT NULL,
    subject     TEXT NOT NULL,
    active      BOOLEAN DEFAULT TRUE,
    notes       TEXT,
    created_at  TIMESTAMP DEFAULT NOW(),
    UNIQUE (student_id, day_type, slot, subject)
);
CREATE INDEX IF NOT EXISTS idx_st_student ON student_timetable(student_id);
CREATE INDEX IF NOT EXISTS idx_st_branch  ON student_timetable(branch_id);
CREATE INDEX IF NOT EXISTS idx_st_slot    ON student_timetable(day_type, slot);

-- ── TABLE ALLOCATION (supervisor assigns table/teacher/students per session) ──
CREATE TABLE IF NOT EXISTS table_allocations (
    id          SERIAL PRIMARY KEY,
    session_id  INT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    table_no    INT NOT NULL,
    teacher_id  INT REFERENCES staff(id) ON DELETE SET NULL,
    subject     TEXT,
    max_students INT DEFAULT 5,
    notes       TEXT,
    created_at  TIMESTAMP DEFAULT NOW(),
    UNIQUE (session_id, table_no)
);
CREATE INDEX IF NOT EXISTS idx_ta_session ON table_allocations(session_id);

-- ── TABLE ALLOCATION STUDENTS ──
CREATE TABLE IF NOT EXISTS table_allocation_students (
    id              SERIAL PRIMARY KEY,
    allocation_id   INT NOT NULL REFERENCES table_allocations(id) ON DELETE CASCADE,
    student_id      INT NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    is_catchup      BOOLEAN DEFAULT FALSE,
    created_at      TIMESTAMP DEFAULT NOW(),
    UNIQUE (allocation_id, student_id)
);
CREATE INDEX IF NOT EXISTS idx_tas_alloc   ON table_allocation_students(allocation_id);
CREATE INDEX IF NOT EXISTS idx_tas_student ON table_allocation_students(student_id);

-- ── SESSION STUDENTS (pre-assignment) ──
CREATE TABLE IF NOT EXISTS session_students (
    id          SERIAL PRIMARY KEY,
    session_id  INT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    student_id  INT NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    added_by    INT REFERENCES users(id) ON DELETE SET NULL,
    is_catchup  BOOLEAN DEFAULT FALSE,
    created_at  TIMESTAMP DEFAULT NOW(),
    UNIQUE (session_id, student_id)
);
CREATE INDEX IF NOT EXISTS idx_ss_session ON session_students(session_id);
CREATE INDEX IF NOT EXISTS idx_ss_student ON session_students(student_id);

-- ── CATCH-UP LESSONS ──
CREATE TABLE IF NOT EXISTS catchup_lessons (
    id                  SERIAL PRIMARY KEY,
    student_id          INT NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    branch_id           INT NOT NULL REFERENCES branches(id) ON DELETE CASCADE,
    missed_session_id   INT REFERENCES sessions(id) ON DELETE SET NULL,
    missed_date         DATE NOT NULL,
    subject             TEXT,
    notified_in_advance BOOLEAN DEFAULT FALSE,
    notification_notes  TEXT,
    status              TEXT NOT NULL DEFAULT 'owed'
                        CHECK (status IN ('owed','scheduled','completed','waived')),
    catchup_session_id  INT REFERENCES sessions(id) ON DELETE SET NULL,
    scheduled_date      DATE,
    completed_date      DATE,
    notes               TEXT,
    created_by          INT REFERENCES users(id) ON DELETE SET NULL,
    created_at          TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_catchup_student ON catchup_lessons(student_id);
CREATE INDEX IF NOT EXISTS idx_catchup_status  ON catchup_lessons(status);
CREATE INDEX IF NOT EXISTS idx_catchup_date    ON catchup_lessons(missed_date DESC);

-- ── SESSION COVER ──
-- Track when a cover teacher takes over a session
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS cover_staff_id INT REFERENCES staff(id) ON DELETE SET NULL;
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS cover_notes TEXT;

-- ── LESSON REPORTS ──
CREATE TABLE IF NOT EXISTS lesson_reports (
    id                    SERIAL PRIMARY KEY,
    session_id            INT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    student_id            INT NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    branch_id             INT NOT NULL REFERENCES branches(id) ON DELETE CASCADE,
    staff_id              INT REFERENCES staff(id) ON DELETE SET NULL,
    date                  DATE NOT NULL,
    -- Teacher fields
    classwork_completed   TEXT,
    homework_marked       BOOLEAN DEFAULT FALSE,
    homework_set          TEXT,
    diary_entry           TEXT,
    www                   TEXT,
    ebi                   TEXT,
    -- Supervisor check
    supervisor_checked    BOOLEAN DEFAULT FALSE,
    supervisor_id         INT REFERENCES users(id) ON DELETE SET NULL,
    supervisor_checked_at TIMESTAMP,
    supervisor_notes      TEXT,
    created_at            TIMESTAMP DEFAULT NOW(),
    UNIQUE (session_id, student_id)
);

-- ── TEST RECORDS ──
CREATE TABLE IF NOT EXISTS test_records (
    id                    SERIAL PRIMARY KEY,
    student_id            INT NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    branch_id             INT NOT NULL REFERENCES branches(id) ON DELETE CASCADE,
    staff_id              INT REFERENCES staff(id) ON DELETE SET NULL,
    recorded_by           INT REFERENCES users(id) ON DELETE SET NULL,
    subject               TEXT NOT NULL,
    book_unit             TEXT NOT NULL,
    test_date             DATE NOT NULL DEFAULT CURRENT_DATE,
    score_pct             NUMERIC(5,2) NOT NULL,
    passed                BOOLEAN,
    revision_given        BOOLEAN DEFAULT FALSE,
    retest_date           DATE,
    retest_score_pct      NUMERIC(5,2),
    retest_passed         BOOLEAN,
    action_plan           TEXT,
    notes                 TEXT,
    created_at            TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_lesson_reports_session ON lesson_reports(session_id);
CREATE INDEX IF NOT EXISTS idx_lesson_reports_student ON lesson_reports(student_id);
CREATE INDEX IF NOT EXISTS idx_test_records_student ON test_records(student_id);
CREATE INDEX IF NOT EXISTS idx_test_records_date ON test_records(test_date DESC);

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

    conn.commit()
    cur.close()
    conn.close()
    print("Database initialised successfully.")

if __name__ == '__main__':
    init_db()
