"""
Shared Snowflake connection helper.
Primary: fetches private key from Azure Key Vault (same as the ETL does).
Fallback: connects using env-var credentials (SNOWFLAKE_ACCOUNT/USER/PASSWORD).
"""
import snowflake.connector
from cryptography.hazmat.primitives.serialization import (
    load_pem_private_key, Encoding, PrivateFormat, NoEncryption
)
from cryptography.hazmat.backends import default_backend


def get_snowflake_cursor(etl_config: dict):
    """
    etl_config: the env-level config dict (e.g. config['dev'])
    Returns a snowflake cursor or None on failure.
    """
    try:
        sf_list = etl_config.get("snowflake", [])
        if not isinstance(sf_list, list) or not sf_list:
            return None
        sf_conf = sf_list[0]

        kv_name   = etl_config.get("key_vault", "")
        pk_conf   = sf_conf.get("private_key") or {}
        pk_secret = pk_conf.get("secret_name", "")
        pk_file   = pk_conf.get("local_file", "")

        pem_key = None

        # 1. Try Key Vault first (authoritative)
        if kv_name and pk_secret:
            vault_uri = f"https://{kv_name}.vault.azure.net/"
            from utils.ay_akv import AyAzureKeyVault
            akv = AyAzureKeyVault()
            pem_key = akv.get_secret(vault_uri, pk_secret)

        # 2. Fall back to local file
        if not pem_key and pk_file:
            from pathlib import Path as _P
            import os
            candidates = [
                _P(pk_file),
                _P(__file__).parent.parent / pk_file,
                _P(os.getcwd()) / pk_file,
            ]
            for candidate in candidates:
                if candidate.exists():
                    pem_key = candidate.read_text(encoding="utf-8")
                    break

        if not pem_key:
            return None

        raw = pem_key.encode("utf-8") if isinstance(pem_key, str) else pem_key
        private_key_obj = load_pem_private_key(raw, password=None, backend=default_backend())
        private_key_der = private_key_obj.private_bytes(
            encoding=Encoding.DER,
            format=PrivateFormat.PKCS8,
            encryption_algorithm=NoEncryption()
        )

        conn = snowflake.connector.connect(
            account=sf_conf.get("account"),
            user=sf_conf.get("user"),
            role=sf_conf.get("role"),
            warehouse=sf_conf.get("warehouse"),
            database=sf_conf.get("database"),
            schema=sf_conf.get("schema"),
            private_key=private_key_der,
            insecure_mode=True,
            ocsp_fail_open=True,
            login_timeout=15,
            network_timeout=15,
            max_connection_pool=1,
        )
        return conn.cursor()
    except Exception as e:
        print(f"  [Snowflake] Connection failed: {e}")
        return None


def get_snowflake_cursor_env(etl_config: dict):
    """
    Fallback connection using env-var credentials when Key Vault is unreachable.
    Reads SNOWFLAKE_ACCOUNT/USER/PASSWORD/ROLE/WAREHOUSE from .env via Config.
    Uses account/database/schema from etl_config if available, else falls back to Config.
    """
    try:
        from src.config import Config
        account = Config.SNOWFLAKE_ACCOUNT
        user = Config.SNOWFLAKE_USER
        password = Config.SNOWFLAKE_PASSWORD
        if not (account and user and password):
            return None
        sf_list = etl_config.get("snowflake", [])
        sf_conf = sf_list[0] if isinstance(sf_list, list) and sf_list else {}
        conn = snowflake.connector.connect(
            account=sf_conf.get("account") or account,
            user=user,
            password=password,
            role=sf_conf.get("role") or Config.SNOWFLAKE_ROLE,
            warehouse=sf_conf.get("warehouse") or Config.SNOWFLAKE_WAREHOUSE,
            database=sf_conf.get("database") or Config.SNOWFLAKE_DATABASE,
            schema=sf_conf.get("schema") or Config.SNOWFLAKE_SCHEMA,
            insecure_mode=True,
            ocsp_fail_open=True,
            login_timeout=15,
            network_timeout=15,
        )
        return conn.cursor()
    except Exception as e:
        print(f"  [Snowflake] Env fallback connection failed: {e}")
        return None
