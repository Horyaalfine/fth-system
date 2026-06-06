import os, psycopg2, psycopg2.extras, json
from datetime import date

conn = psycopg2.connect(os.environ['DATABASE_URL'])
cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
today = str(date.today())

print("=== TODAY'S DATA: " + today + " ===\n")

# Sessions today
cur.execute("SELECT s.id, s.slot, s.subject, s.table_no, st.name as teacher, b.name as branch FROM sessions s LEFT JOIN staff st ON st.id=s.staff_id JOIN branches b ON b.id=s.branch_id WHERE s.date=%s ORDER BY s.slot", (today,))
sessions = cur.fetchall()
print(f"SESSIONS TODAY: {len(sessions)}")
for s in sessions: print(f"  [{s['id']}] {s['slot']} | {s['branch']} | {s['teacher']} | {s['subject']}")

# Attendance today
cur.execute("SELECT COUNT(*) as c, status FROM attendance a JOIN sessions s ON s.id=a.session_id WHERE s.date=%s GROUP BY status", (today,))
att = cur.fetchall()
print(f"\nATTENDANCE TODAY:")
for a in att: print(f"  {a['status']}: {a['c']}")

# Staff attendance today
cur.execute("SELECT sa.status, sa.sign_in, sa.sign_out, st.name, b.name as branch FROM staff_attendance sa JOIN staff st ON st.id=sa.staff_id JOIN branches b ON b.id=sa.branch_id WHERE sa.date=%s", (today,))
satts = cur.fetchall()
print(f"\nSTAFF ATTENDANCE TODAY: {len(satts)}")
for s in satts: print(f"  {s['name']} | {s['branch']} | {s['status']} | in:{s['sign_in']} out:{s['sign_out']}")

# Lesson reports today
cur.execute("SELECT COUNT(*) as c FROM lesson_reports WHERE date=%s", (today,))
lr = cur.fetchone()
print(f"\nLESSON REPORTS TODAY: {lr['c']}")

# Test records today
cur.execute("SELECT COUNT(*) as c FROM test_records WHERE test_date=%s", (today,))
tr = cur.fetchone()
print(f"TEST RECORDS TODAY: {tr['c']}")

# Recent audit log
cur.execute("SELECT user_name, action, table_name, timestamp FROM audit_log WHERE timestamp::date=%s ORDER BY timestamp DESC LIMIT 20", (today,))
audit = cur.fetchall()
print(f"\nAUDIT LOG TODAY ({len(audit)} entries):")
for a in audit: print(f"  {str(a['timestamp'])[11:19]} | {a['user_name']} | {a['action']} {a['table_name']}")

cur.close(); conn.close()
print("\n=== CHECK COMPLETE ===")
