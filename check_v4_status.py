import snowflake.connector, sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.absolute()))
from utils.ay_akv import AyAzureKeyVault
from cryptography.hazmat.primitives.serialization import load_pem_private_key, Encoding, PrivateFormat, NoEncryption
from cryptography.hazmat.backends import default_backend

akv = AyAzureKeyVault()
pem = akv.get_secret('https://etl-test-agent-kv.vault.azure.net/', 'snowflake-private-key').encode('utf-8')
pk_obj = load_pem_private_key(pem, password=None, backend=default_backend())
pk_der = pk_obj.private_bytes(Encoding.DER, PrivateFormat.PKCS8, NoEncryption())

conn = snowflake.connector.connect(
    account='CUPZNIQ-BF41726', user='ABESHITH', private_key=pk_der,
    warehouse='COMPUTE_WH', role='ACCOUNTADMIN',
    database='AGS_RAW', schema='PUBLIC',
    insecure_mode=True, ocsp_fail_open=True, login_timeout=15, network_timeout=15
)
conn.autocommit(True)
cur = conn.cursor()

try:
    print('=' * 70)
    print('V4 ETL STATUS CHECK')
    print('=' * 70)
    print('Account: CUPZNIQ-BF41726')
    print('Database: AGS_RAW')
    print('Schema: PUBLIC')
    print('=' * 70)

    for table in ['METRICS', 'STATISTICS', 'DICTIONARY']:
        print(f'\n[{table}]')
        print('-' * 70)
        cur.execute(f'SELECT COUNT(*) FROM {table}')
        count = cur.fetchone()[0]
        print(f'Total Records: {count}')
        if count > 0:
            print('Status: SUCCESS - Data loaded')
            cur.execute(f'SELECT * FROM {table} LIMIT 3')
            for i, row in enumerate(cur.fetchall(), 1):
                print(f'  {i}. {row}')
        else:
            print('Status: EMPTY - No data loaded')

    print('\n' + '=' * 70)
    print('V4 ETL STATUS: COMPLETE')
    print('=' * 70)

except Exception as e:
    print(f'Error: {e}')
    import traceback
    traceback.print_exc()
finally:
    cur.close()
    conn.close()
