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
    role          TEXT NOT NULL CHECK (role IN ('super_admin','branch_manager','head_of_centre','head_of_branches','supervisor','teacher','receptionist','admin','reports_viewer')),
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
    -- Postcode
    carer1_postcode     TEXT,
    carer2_postcode     TEXT,
    -- GCSE details
    gcse_maths_board         TEXT,
    gcse_maths_paper         TEXT,
    gcse_maths_exam_date     TEXT,
    gcse_maths_current_grade TEXT,
    gcse_maths_predicted_grade TEXT,
    gcse_english_board         TEXT,
    gcse_english_paper         TEXT,
    gcse_english_exam_date     TEXT,
    gcse_english_current_grade TEXT,
    gcse_english_predicted_grade TEXT,
    gcse_science_board         TEXT,
    gcse_science_paper         TEXT,
    gcse_science_exam_date     TEXT,
    gcse_science_current_grade TEXT,
    gcse_science_predicted_grade TEXT,
    -- Assessment
    assess_maths_pct    TEXT,
    assess_maths_book   TEXT,
    assess_english_pct  TEXT,
    assess_english_book TEXT,
    assess_science_pct  TEXT,
    assess_science_book TEXT,
    hours_per_week      TEXT,
    -- Reference
    referred_by         TEXT,
    referral_admission  TEXT,
    -- Legacy fields kept for compatibility
    parent_contact      TEXT,
    status              TEXT NOT NULL DEFAULT 'active',
    notes               TEXT,
    opening_balance     NUMERIC(10,2) NOT NULL DEFAULT 0,
    created_at          TIMESTAMP DEFAULT NOW()
);

-- Migrations (individual statements - safe to run multiple times)
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
ALTER TABLE students ADD COLUMN IF NOT EXISTS monthly_fee NUMERIC(10,2);
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS cover_staff_id INT REFERENCES staff(id) ON DELETE SET NULL;
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS cover_notes TEXT;
ALTER TABLE staff_attendance ALTER COLUMN session_id DROP NOT NULL;
ALTER TABLE payments DROP CONSTRAINT IF EXISTS payments_method_check;
ALTER TABLE payments ADD CONSTRAINT payments_method_check CHECK (method IN ('cash','bank_transfer','cheque','card','direct_debit','standing_order','other'));

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
    amount     NUMERIC(10,2) NOT NULL DEFAULT 120,
    amount_paid NUMERIC(10,2) NOT NULL DEFAULT 0,
    status     TEXT NOT NULL DEFAULT 'due' CHECK (status IN ('due','paid','overdue','partial')),
    issued     DATE NOT NULL DEFAULT CURRENT_DATE,
    paid_date  DATE,
    notes      TEXT,
    UNIQUE (student_id, month, fee_type)
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

CREATE TABLE IF NOT EXISTS adjustments (
    id             SERIAL PRIMARY KEY,
    student_id     INT NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    branch_id      INT NOT NULL REFERENCES branches(id) ON DELETE CASCADE,
    amount         NUMERIC(10,2) NOT NULL,
    adj_type       TEXT NOT NULL DEFAULT 'discount' CHECK (adj_type IN ('discount','credit','correction','write_off','other')),
    adj_date       DATE NOT NULL DEFAULT CURRENT_DATE,
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


-- ── SESSION SLOT NAME MIGRATION (rename Slot→Session, fix times) ──
UPDATE student_timetable SET slot='Weekday Session 1 (17:00-19:00)' WHERE slot IN ('Weekday Slot 2 (17:00-19:00)','Weekday Session 1 (17:00-19:00)') AND slot!='Weekday Session 1 (17:00-19:00)';
UPDATE student_timetable SET slot='Weekday Session 2 (19:00-21:00)' WHERE slot IN ('Weekday Slot 3 (19:00-21:00)','Weekday Session 2 (19:00-21:00)') AND slot!='Weekday Session 2 (19:00-21:00)';
UPDATE student_timetable SET slot='Saturday Session 1 (09:00-11:00)' WHERE slot IN ('Saturday Slot 1 (09:00-11:00)') AND slot!='Saturday Session 1 (09:00-11:00)';
UPDATE student_timetable SET slot='Saturday Session 2 (11:15-13:15)' WHERE slot IN ('Saturday Slot 2 (11:15-13:15)') AND slot!='Saturday Session 2 (11:15-13:15)';
UPDATE student_timetable SET slot='Saturday Session 3 (14:00-16:00)' WHERE slot IN ('Saturday Slot 3 (14:15-16:15)','Saturday Slot 3 (14:00-16:00)') AND slot!='Saturday Session 3 (14:00-16:00)';
UPDATE student_timetable SET slot='Saturday Session 4 (16:15-18:15)' WHERE slot IN ('Saturday Slot 4 (16:30-18:30)','Saturday Slot 4 (16:15-18:15)') AND slot!='Saturday Session 4 (16:15-18:15)';
DELETE FROM student_timetable WHERE slot='Weekday Slot 1 (12:00-16:00)';
UPDATE sessions SET slot='Weekday Session 1 (17:00-19:00)' WHERE slot IN ('Weekday Slot 2 (17:00-19:00)') AND slot!='Weekday Session 1 (17:00-19:00)';
UPDATE sessions SET slot='Weekday Session 2 (19:00-21:00)' WHERE slot IN ('Weekday Slot 3 (19:00-21:00)') AND slot!='Weekday Session 2 (19:00-21:00)';
UPDATE sessions SET slot='Saturday Session 1 (09:00-11:00)' WHERE slot IN ('Saturday Slot 1 (09:00-11:00)') AND slot!='Saturday Session 1 (09:00-11:00)';
UPDATE sessions SET slot='Saturday Session 2 (11:15-13:15)' WHERE slot IN ('Saturday Slot 2 (11:15-13:15)') AND slot!='Saturday Session 2 (11:15-13:15)';
UPDATE sessions SET slot='Saturday Session 3 (14:00-16:00)' WHERE slot IN ('Saturday Slot 3 (14:15-16:15)','Saturday Slot 3 (14:00-16:00)') AND slot!='Saturday Session 3 (14:00-16:00)';
UPDATE sessions SET slot='Saturday Session 4 (16:15-18:15)' WHERE slot IN ('Saturday Slot 4 (16:30-18:30)','Saturday Slot 4 (16:15-18:15)') AND slot!='Saturday Session 4 (16:15-18:15)';
DELETE FROM sessions WHERE slot='Weekday Slot 1 (12:00-16:00)';

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
    session_id   INT REFERENCES sessions(id) ON DELETE CASCADE,
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

-- ── MEETING NOTES ──
CREATE TABLE IF NOT EXISTS meeting_notes (
    id              SERIAL PRIMARY KEY,
    student_id      INT NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    branch_id       INT NOT NULL REFERENCES branches(id) ON DELETE CASCADE,
    recorded_by     INT REFERENCES users(id) ON DELETE SET NULL,
    meeting_date    DATE NOT NULL DEFAULT CURRENT_DATE,
    category        TEXT NOT NULL DEFAULT 'General',
    notes           TEXT NOT NULL,
    shared_parent   BOOLEAN NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_meeting_notes_student ON meeting_notes(student_id);
CREATE INDEX IF NOT EXISTS idx_meeting_notes_branch ON meeting_notes(branch_id);
CREATE INDEX IF NOT EXISTS idx_meeting_notes_date ON meeting_notes(meeting_date DESC);

CREATE TABLE IF NOT EXISTS announcements (
    id              SERIAL PRIMARY KEY,
    branch_id       INT REFERENCES branches(id) ON DELETE CASCADE,
    created_by      INT REFERENCES users(id) ON DELETE SET NULL,
    title           TEXT NOT NULL,
    body            TEXT NOT NULL,
    target_type     TEXT NOT NULL DEFAULT 'all',
    student_id      INT REFERENCES students(id) ON DELETE CASCADE,
    active          BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_announcements_branch ON announcements(branch_id);
CREATE INDEX IF NOT EXISTS idx_announcements_student ON announcements(student_id);
CREATE INDEX IF NOT EXISTS idx_announcements_created ON announcements(created_at DESC);
CREATE TABLE IF NOT EXISTS announcement_email_log (
    id              SERIAL PRIMARY KEY,
    announcement_id INT REFERENCES announcements(id) ON DELETE CASCADE,
    recipient_email TEXT NOT NULL,
    recipient_name  TEXT,
    status          TEXT NOT NULL DEFAULT 'sent',
    error_msg       TEXT,
    sent_at         TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_ann_email_log_ann ON announcement_email_log(announcement_id);

-- ── CREDIT CONTROL ──
ALTER TABLE students ADD COLUMN IF NOT EXISTS pause_reminders BOOLEAN DEFAULT FALSE;
ALTER TABLE invoices ADD COLUMN IF NOT EXISTS fee_type TEXT NOT NULL DEFAULT 'monthly_fee';
ALTER TABLE invoices ADD COLUMN IF NOT EXISTS batch_id TEXT;
ALTER TABLE invoices ADD COLUMN IF NOT EXISTS description TEXT;
ALTER TABLE invoices ADD COLUMN IF NOT EXISTS due_date DATE;
CREATE INDEX IF NOT EXISTS idx_invoices_batch ON invoices(batch_id);
CREATE TABLE IF NOT EXISTS fee_reminder_log (
    id               SERIAL PRIMARY KEY,
    student_id       INT NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    sent_at          TIMESTAMP DEFAULT NOW(),
    sent_by          INT REFERENCES users(id) ON DELETE SET NULL,
    type             TEXT NOT NULL DEFAULT 'manual',
    recipient_email  TEXT,
    outstanding_amt  NUMERIC(10,2),
    invoices_count   INT
);
CREATE INDEX IF NOT EXISTS idx_fee_reminder_student ON fee_reminder_log(student_id);

-- ── Registration sent log ──
CREATE TABLE IF NOT EXISTS registration_sent (
    student_id  INT PRIMARY KEY REFERENCES students(id) ON DELETE CASCADE,
    sent_by     INT REFERENCES users(id) ON DELETE SET NULL,
    sent_at     TIMESTAMP DEFAULT NOW()
);



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

-- ── TEACHER SESSIONS (hours tracking) ──
CREATE TABLE IF NOT EXISTS teacher_sessions (
    id          SERIAL PRIMARY KEY,
    branch_id   INT NOT NULL REFERENCES branches(id) ON DELETE CASCADE,
    staff_id    INT NOT NULL REFERENCES staff(id) ON DELETE CASCADE,
    date        DATE NOT NULL,
    slot_key    TEXT NOT NULL,
    paid_mins   INT NOT NULL DEFAULT 0,
    notes       TEXT,
    created_by  INT REFERENCES users(id) ON DELETE SET NULL,
    created_at  TIMESTAMP DEFAULT NOW(),
    UNIQUE(staff_id, date, slot_key)
);
CREATE INDEX IF NOT EXISTS idx_teacher_sessions_staff ON teacher_sessions(staff_id);
CREATE INDEX IF NOT EXISTS idx_teacher_sessions_date  ON teacher_sessions(date);
CREATE INDEX IF NOT EXISTS idx_teacher_sessions_branch ON teacher_sessions(branch_id);
ALTER TABLE users DROP CONSTRAINT IF EXISTS users_role_check;
ALTER TABLE branches ADD COLUMN IF NOT EXISTS website TEXT;
ALTER TABLE users ADD CONSTRAINT users_role_check CHECK (role IN ('super_admin','branch_manager','head_of_centre','head_of_branches','supervisor','teacher','receptionist','admin','reports_viewer'));
-- ── BRANCH SCHEDULE (recurring session slots) ──
CREATE TABLE IF NOT EXISTS branch_schedule (
    id             SERIAL PRIMARY KEY,
    branch_id      INT NOT NULL REFERENCES branches(id) ON DELETE CASCADE,
    day_of_week    TEXT NOT NULL CHECK (day_of_week IN ('monday','tuesday','wednesday','thursday','friday','saturday','sunday')),
    slot_start     TEXT NOT NULL,
    slot_end       TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','closed')),
    effective_from DATE NOT NULL DEFAULT CURRENT_DATE,
    effective_to   DATE,
    notes          TEXT,
    created_at     TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_bsched_branch ON branch_schedule(branch_id);
CREATE INDEX IF NOT EXISTS idx_bsched_day    ON branch_schedule(branch_id, day_of_week);
-- ── STUDENT AGREED SLOTS (links students to branch schedule slots) ──
CREATE TABLE IF NOT EXISTS student_agreed_slots (
    id                  SERIAL PRIMARY KEY,
    student_id          INT NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    branch_schedule_id  INT NOT NULL REFERENCES branch_schedule(id) ON DELETE CASCADE,
    effective_from      DATE NOT NULL DEFAULT CURRENT_DATE,
    notes               TEXT,
    created_at          TIMESTAMP DEFAULT NOW(),
    UNIQUE (student_id, branch_schedule_id)
);
CREATE INDEX IF NOT EXISTS idx_sas_student  ON student_agreed_slots(student_id);
CREATE INDEX IF NOT EXISTS idx_sas_schedule ON student_agreed_slots(branch_schedule_id);
ALTER TABLE branches ADD COLUMN IF NOT EXISTS website TEXT;
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

def migrate_timetable_to_agreed_slots():
    """One-time migration: populate student_agreed_slots from legacy student_timetable data.
    Safe to run multiple times — uses ON CONFLICT DO NOTHING."""
    import re
    conn = get_conn(); cur = conn.cursor()
    try:
        cur.execute("SELECT DISTINCT student_id, slot, branch_id FROM student_timetable WHERE slot IS NOT NULL")
        rows = cur.fetchall()
        if not rows:
            cur.close(); conn.close(); return
        def parse_slot(slot):
            import re as _re
            m = _re.search(r'\((\d{2}:\d{2})', slot)
            start = m.group(1) if m else None
            sl = slot.lower()
            if 'saturday' in sl: days = ['saturday']
            elif 'sunday' in sl: days = ['sunday']
            else: days = ['monday','tuesday','wednesday','thursday','friday']
            return days, start
        migrated = 0
        today = '2026-09-01'
        for student_id, slot, branch_id in rows:
            days, start = parse_slot(slot)
            if not start: continue
            cur.execute("""SELECT id FROM branch_schedule WHERE day_of_week = ANY(%s) AND slot_start = %s AND (branch_id = %s OR %s IS NULL) AND status = 'active'""", (days, start, branch_id, branch_id))
            for (sched_id,) in cur.fetchall():
                cur.execute("INSERT INTO student_agreed_slots (student_id, branch_schedule_id, effective_from) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING", (student_id, sched_id, today))
                migrated += 1
        conn.commit()
        print(f"Timetable migration: inserted {migrated} agreed slot entries.")
    except Exception as e:
        conn.rollback(); print(f"Timetable migration error: {e}")
    finally:
        cur.close(); conn.close()


def init_db():
    """Create all tables and seed initial data."""
    conn = get_conn()
    cur = conn.cursor()
    # Split schema into individual statements and run each one
    # This means one failing ALTER won't block everything
    statements = [s.strip() for s in SCHEMA.split(';') if s.strip()]
    ok = 0
    for stmt in statements:
        try:
            cur.execute(stmt)
            conn.commit()
            ok += 1
        except Exception as e:
            conn.rollback()
            # IF NOT EXISTS failures are expected on re-runs - only log real errors
            err = str(e).lower()
            if 'already exists' not in err and 'does not exist' not in err:
                print(f"Schema warning: {e}")
    cur.close()
    conn.close()
    print(f"Database initialised successfully ({ok}/{len(statements)} statements OK).")
    migrate_timetable_to_agreed_slots()

if __name__ == '__main__':
    init_db()

