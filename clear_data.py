import os, psycopg2, psycopg2.extras
conn = psycopg2.connect(os.environ['DATABASE_URL'])
cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
tables = ['instalment_schedule','instalment_plans','payments','staff_attendance',
          'audit_log','progress','attendance','invoices','sessions',
          'parent_students','parent_users','students','staff','users','branches']
for t in tables:
    cur.execute(f'DELETE FROM {t}')
    print(f'Cleared {t}: {cur.rowcount} rows')
conn.commit()
cur.close()
conn.close()
print('All demo data cleared successfully')
