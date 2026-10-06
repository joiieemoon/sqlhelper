import sys, json, os
from dotenv import load_dotenv
import importlib.util

BASE = r'c:\Team G\Jainil K task SQLopt\backend'
DB = os.path.join(BASE, 'db_explain.py')
DU = os.path.join(BASE, 'db_utils.py')

def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod

db_explain = load('db_explain', DB)
db_utils = load('db_utils', DU)

load_dotenv(os.path.join(BASE, '.env'))

cfg = {
    'host': 'mysql-sql-optimizer-test01-sql-optimizer-test01.l.aivencloud.com',
    'port': 17027,
    'user': 'avnadmin',
    'password': os.environ.get('DB_PASSWORD'),
    'database': 'sql_optimizer_test'
}

query = "SELECT id, name FROM customers WHERE country = 'USA' AND status = 'active'"

if not cfg['password']:
    print(json.dumps({'success': False, 'error': 'DB_PASSWORD not set in backend/.env'}, indent=2))
    raise SystemExit(0)

try:
    conn = db_utils.connect_mysql(cfg)
    res = db_explain.run_diagnostics_pipeline(conn, query)
    print(json.dumps({
        'success': True,
        'database': cfg['database'],
        'query_tables': res.get('query_tables'),
        'explain': res.get('explain'),
        'diagnostics': res.get('diagnostics'),
        'optimizer_state': res.get('optimizer_state'),
        'warnings': res.get('warnings'),
        'query_stats': res.get('query_stats'),
        'elapsed_ms': res.get('elapsed_ms'),
    }, default=str, indent=2))
    conn.close()
except Exception as e:
    print(json.dumps({'success': False, 'error': str(e)}, indent=2))

