#!/usr/bin/env python3
"""
Cleanup script for V3 ETL - Deletes existing data from LANDING.LND tables
Run this before testing V3 ETL to ensure fresh data load
"""
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.absolute()))

from utils.snowflake_connector import get_snowflake_cursor
import yaml

def cleanup_v3():
    """Delete all records from V3 tables"""
    try:
        # Load config
        config_path = Path(__file__).parent / "test_samples" / "config.yml"
        with open(config_path) as f:
            config = yaml.safe_load(f)
        
        env_config = config.get("dev_v3", {})
        
        # Get cursor
        cursor = get_snowflake_cursor(env_config)
        
        if not cursor:
            print("[ERROR] Failed to connect to Snowflake")
            return False
        
        database = "LANDING"
        schema = "LND"
        tables = ["LNG_TERMINALS", "GAS_PIPELINES"]
        
        print("=" * 70)
        print("V3 ETL CLEANUP - DELETING EXISTING DATA")
        print("=" * 70)
        
        for table in tables:
            try:
                query = f"DELETE FROM {database}.{schema}.{table}"
                cursor.execute(query)
                rows_deleted = cursor.rowcount
                print(f"[OK] {database}.{schema}.{table}: {rows_deleted} rows deleted")
            except Exception as e:
                print(f"[ERROR] {database}.{schema}.{table}: {e}")
                return False
        
        print("=" * 70)
        print("CLEANUP COMPLETE - Tables are now empty")
        print("=" * 70)
        
        # Verify
        print("\nVerifying record counts:")
        for table in tables:
            try:
                query = f"SELECT COUNT(*) FROM {database}.{schema}.{table}"
                cursor.execute(query)
                count = cursor.fetchone()[0]
                print(f"  {table}: {count} records")
            except Exception as e:
                print(f"  {table}: ERROR - {e}")
        
        return True
        
    except Exception as e:
        print(f"[ERROR] Cleanup failed: {e}")
        return False

if __name__ == "__main__":
    success = cleanup_v3()
    sys.exit(0 if success else 1)
