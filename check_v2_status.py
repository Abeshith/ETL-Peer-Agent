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
snowflake_private_key = azureKV.get_secret(vault_uri, 'snowflake-private-key-v2').encode("utf-8")

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
    insecure_mode=True,
    ocsp_fail_open=True,
    login_timeout=15,
    network_timeout=15
)

cursor = conn.cursor()

try:
    print("="*70)
    print("V2 ETL STATUS CHECK")
    print("="*70)
    print(f"Account: CUPZNIQ-BF41726")
    print(f"Database: ETL_RAW")
    print(f"Schema: LIGHTBOX")
    print("="*70)
    
    # Check PARCELS_UUID table
    print("\n[1] ETL_RAW.LIGHTBOX.PARCELS_UUID")
    print("-" * 70)
    cursor.execute("SELECT COUNT(*) FROM ETL_RAW.LIGHTBOX.PARCELS_UUID")
    count = cursor.fetchone()[0]
    print(f"Total Records: {count}")
    
    if count > 0:
        print(f"Status: SUCCESS - Data loaded with UUIDs")
        cursor.execute("SELECT * FROM ETL_RAW.LIGHTBOX.PARCELS_UUID")
        rows = cursor.fetchall()
        print(f"\nRecords:")
        for i, row in enumerate(rows, 1):
            print(f"  {i}. UUID: {row[0][:8]}... | Vendor ID: {row[1]}")
    else:
        print(f"Status: FAILED - No data loaded")
    
    # Check ASSESSMENTS_UUID table
    print("\n[2] ETL_RAW.LIGHTBOX.ASSESSMENTS_UUID")
    print("-" * 70)
    cursor.execute("SELECT COUNT(*) FROM ETL_RAW.LIGHTBOX.ASSESSMENTS_UUID")
    count = cursor.fetchone()[0]
    print(f"Total Records: {count}")
    
    if count > 0:
        print(f"Status: SUCCESS - Data loaded with UUIDs")
        cursor.execute("SELECT * FROM ETL_RAW.LIGHTBOX.ASSESSMENTS_UUID")
        rows = cursor.fetchall()
        print(f"\nRecords:")
        for i, row in enumerate(rows, 1):
            print(f"  {i}. UUID: {row[0][:8]}... | Vendor ID: {row[1]}")
    else:
        print(f"Status: FAILED - No data loaded")
    
    # Check Share
    print("\n[3] SNOWFLAKE SHARE")
    print("-" * 70)
    cursor.execute("SHOW SHARES")
    shares = cursor.fetchall()
    share_found = False
    for share in shares:
        if 'TERMINAL' in share[1]:
            print(f"Share Name: {share[1]}")
            print(f"Database: {share[2]}")
            print(f"Status: {share[3]}")
            print(f"Status: SUCCESS - Share created")
            share_found = True
    
    if not share_found:
        print("Status: NO SHARE - Share not found")
    
    print("\n" + "="*70)
    print("V2 ETL STATUS: COMPLETE")
    print("="*70)
    
except Exception as e:
    print(f"Error: {e}")
    import traceback
    traceback.print_exc()
    
finally:
    cursor.close()
    conn.close()
