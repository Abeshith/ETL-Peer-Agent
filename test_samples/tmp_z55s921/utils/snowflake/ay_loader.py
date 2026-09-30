import snowflake.connector
from cryptography.hazmat.primitives.serialization import load_pem_private_key
from cryptography.hazmat.backends import default_backend

class AYSnowflakeLoader:
    def __init__(self, snowflake_account, snowflake_user, snowflake_role, snowflake_private_key, snowflake_warehouse, snowflake_database, snowflake_schema, snowflake_password=None):
        # Build connect kwargs — prefer private key, fall back to password
        connect_kwargs = dict(
            account=snowflake_account,
            user=snowflake_user,
            role=snowflake_role,
            warehouse=snowflake_warehouse,
            database=snowflake_database,
            schema=snowflake_schema,
            insecure_mode=True,
            ocsp_fail_open=True,
            login_timeout=15,
            network_timeout=15,
        )
        if snowflake_private_key:
            if isinstance(snowflake_private_key, (str, bytes)):
                raw = snowflake_private_key if isinstance(snowflake_private_key, bytes) else snowflake_private_key.encode()
                private_key_obj = load_pem_private_key(raw, password=None, backend=default_backend())
                from cryptography.hazmat.primitives.serialization import Encoding, PrivateFormat, NoEncryption
                connect_kwargs["private_key"] = private_key_obj.private_bytes(
                    encoding=Encoding.DER,
                    format=PrivateFormat.PKCS8,
                    encryption_algorithm=NoEncryption()
                )
            else:
                connect_kwargs["private_key"] = snowflake_private_key
        elif snowflake_password:
            connect_kwargs["password"] = snowflake_password
        self.conn = snowflake.connector.connect(**connect_kwargs)
        self.conn.autocommit(True)
        self.cursor = self.conn.cursor()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.cursor:
            self.cursor.close()
        if self.conn:
            if not self.conn.autocommit:
                self.conn.commit()
            self.conn.close()

    def truncate(self, snowflake_tablename: str):
        try:
            self.cursor.execute(f"TRUNCATE TABLE {snowflake_tablename}")
            return "SUCCESS"
        except Exception as e:
            return f"FAILED: {e}"

    def load_csv(self, snowflake_tablename: str, snowflake_stage: str, snowflake_copy_pattern: str, snowflake_copy_file_format: str, snowflake_copy_on_error: str):
        try:
            copy_sql = f"""
                COPY INTO {snowflake_tablename}
                FROM @{snowflake_stage}/{snowflake_copy_pattern}
                FILE_FORMAT = (FORMAT_NAME = '{snowflake_copy_file_format}', error_on_column_count_mismatch=false)
                ON_ERROR = '{snowflake_copy_on_error}'
                FORCE = TRUE
            """
            self.cursor.execute(copy_sql)
            result = self.cursor.fetchall()
            if result:
                return f"SUCCESS: {result[0][2]} rows loaded"
            return "SUCCESS"
        except Exception as e:
            return f"FAILED: {e}"

    def run_query(self, query: str, num_statements: int = 1):
        try:
            if num_statements > 1:
                self.cursor.execute(query, num_statements=num_statements)
            else:
                self.cursor.execute(query)
            result = self.cursor.fetchall()
            return result
        except Exception as e:
            raise Exception(f"Query execution failed: {e}")

    def run_query_no_fetch(self, query: str, num_statements: int = 1):
        try:
            if num_statements > 1:
                self.cursor.execute(query, num_statements=num_statements)
            else:
                self.cursor.execute(query)
            return self.cursor.rowcount
        except Exception as e:
            raise Exception(f"Query execution failed: {e}")
