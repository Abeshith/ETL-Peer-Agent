#!/usr/bin/env python3
"""
Cleanup script for V2 ETL - Deletes existing data from ETL_RAW.LIGHTBOX tables
"""
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.absolute()))

from utils.snowflake_connector import get_snowflake_cursor
import yaml

def cleanup_v2():
    try:
        config_path = Path(__file__).parent / "test_samples" / "config.yml"
        with open(config_path) as f:
            config = yaml.safe_load(f)

        env_config = (config or {}).get("dev_v2", {})
        cursor = get_snowflake_cursor(env_config)

        if not cursor:
            print("[ERROR] Failed to connect to Snowflake")
            return False

        database = "ETL_RAW"
        schema = "LIGHTBOX"
        tables = ["PARCELS_UUID", "ASSESSMENTS_UUID"]

        print("=" * 70)
        print("V2 ETL CLEANUP - DELETING EXISTING DATA")
        print("=" * 70)

        for table in tables:
            try:
                cursor.execute(f"DELETE FROM {database}.{schema}.{table}")
                print(f"[OK] {database}.{schema}.{table}: {cursor.rowcount} rows deleted")
            except Exception as e:
                print(f"[ERROR] {database}.{schema}.{table}: {e}")
                return False

        print("=" * 70)
        print("CLEANUP COMPLETE - Tables are now empty")
        print("=" * 70)

        print("\nVerifying record counts:")
        for table in tables:
            cursor.execute(f"SELECT COUNT(*) FROM {database}.{schema}.{table}")
            print(f"  {table}: {cursor.fetchone()[0]} records")

        return True

    except Exception as e:
        print(f"[ERROR] Cleanup failed: {e}")
        return False

if __name__ == "__main__":
    success = cleanup_v2()
    sys.exit(0 if success else 1)
