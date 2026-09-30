import snowflake.connector
from pathlib import Path
import sys

sys.path.append(str(Path(__file__).parent.parent.absolute()))
from utils.ay_akv import AyAzureKeyVault
from cryptography.hazmat.primitives.serialization import load_pem_private_key
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.serialization import Encoding, PrivateFormat, NoEncryption

azureKV = AyAzureKeyVault()
vault_uri = 'https://etl-test-agent-kv.vault.azure.net/'
snowflake_private_key = azureKV.get_secret(vault_uri, 'snowflake-private-key').encode("utf-8")

private_key_obj = load_pem_private_key(snowflake_private_key, password=None, backend=default_backend())
private_key_der = private_key_obj.private_bytes(
    encoding=Encoding.DER,
    format=PrivateFormat.PKCS8,
    encryption_algorithm=NoEncryption()
)

conn = snowflake.connector.connect(
    account='CUPZNIQ-BF41726',
    user='ABESHITH',
    private_key=private_key_der,
    warehouse='COMPUTE_WH',
    role='ACCOUNTADMIN',
    database='LANDING',
    schema='LND',
    insecure_mode=True,
    ocsp_fail_open=True,
    login_timeout=15,
    network_timeout=15
)
conn.autocommit(True)

cursor = conn.cursor()

try:
    print("="*70)
    print("V1 ETL STATUS CHECK")
    print("="*70)
    print(f"Account: CUPZNIQ-BF41726")
    print(f"Database: LANDING")
    print(f"Schema: LND")
    print("="*70)
    
    # Check raw_data table
    print("\n[1] RAW_DATA TABLE")
    print("-" * 70)
    cursor.execute("SELECT COUNT(*) FROM raw_data")
    count = cursor.fetchone()[0]
    print(f"Total Records: {count}")
    
    if count > 0:
        print(f"Status: SUCCESS - Data loaded")
        cursor.execute("SELECT * FROM raw_data")
        rows = cursor.fetchall()
        print(f"\nRecords:")
        for i, row in enumerate(rows, 1):
            print(f"  {i}. {row}")
    else:
        print(f"Status: FAILED - No data loaded")
    
    # Check audit_log table
    print("\n[2] AUDIT_LOG TABLE")
    print("-" * 70)
    cursor.execute("SELECT COUNT(*) FROM audit_log")
    count = cursor.fetchone()[0]
    print(f"Total Records: {count}")
    
    if count > 0:
        print(f"Status: SUCCESS - Data loaded")
        cursor.execute("SELECT * FROM audit_log")
        rows = cursor.fetchall()
        print(f"\nRecords:")
        for i, row in enumerate(rows, 1):
            print(f"  {i}. {row}")
    else:
        print(f"Status: NO DATA - Audit log empty")
    
    print("\n" + "="*70)
    print("V1 ETL STATUS: COMPLETE")
    print("="*70)
    
except Exception as e:
    print(f"Error: {e}")
    import traceback
    traceback.print_exc()
    
finally:
    cursor.close()
    conn.close()
