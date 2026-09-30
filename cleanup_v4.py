import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.absolute()))
from utils.snowflake_connector import get_snowflake_cursor
import yaml

def cleanup_v4():
    config_path = Path(__file__).parent / "test_samples" / "config.yml"
    with open(config_path) as f:
        config = yaml.safe_load(f)

    cursor = get_snowflake_cursor(config.get("dev", {}))
    if not cursor:
        print("[ERROR] Failed to connect to Snowflake")
        return False

    database = "AGS_RAW"
    schema   = "PUBLIC"
    tables   = ["METRICS", "STATISTICS", "DICTIONARY"]

    print("=" * 70)
    print("V4 ETL CLEANUP - DELETING EXISTING DATA")
    print("=" * 70)

    for table in tables:
        try:
            cursor.execute(f"DELETE FROM {database}.{schema}.{table}")
            print(f"  [OK] {database}.{schema}.{table}: {cursor.rowcount} rows deleted")
        except Exception as e:
            print(f"  [ERROR] {database}.{schema}.{table}: {e}")
            return False

    print("\nVerifying record counts:")
    for table in tables:
        cursor.execute(f"SELECT COUNT(*) FROM {database}.{schema}.{table}")
        print(f"  {table}: {cursor.fetchone()[0]} records")

    print("=" * 70)
    print("CLEANUP COMPLETE")
    print("=" * 70)
    return True

if __name__ == "__main__":
    sys.exit(0 if cleanup_v4() else 1)
