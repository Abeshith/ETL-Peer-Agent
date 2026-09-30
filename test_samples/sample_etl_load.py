import logging
from azure.storage.blob import BlobServiceClient
from azure.core.exceptions import ResourceNotFoundError, ServiceRequestError, ContainerNotFound
import snowflake.connector
from datetime import datetime
import json
import time
from hashlib import md5


class JSONFormatter(logging.Formatter):
    def format(self, record):
        import json
        # Handle dict messages from logger.info({...})
        # Check if msg is already a dict
        if isinstance(record.msg, dict):
            message_data = record.msg.copy()
        else:
            # Try to parse as JSON if it's a string that looks like JSON
            msg_str = record.getMessage()
            try:
                if msg_str.startswith('{'):
                    message_data = json.loads(msg_str)
                else:
                    message_data = {"message": msg_str}
            except (json.JSONDecodeError, ValueError):
                message_data = {"message": msg_str}
        
        log_data = {
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            **message_data,  # Merge message data
            "module": record.module
        }
        return json.dumps(log_data)

# Configure logger with only JSON handler (fix TC033)
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
json_handler = logging.StreamHandler()
json_handler.setFormatter(JSONFormatter())
logger.addHandler(json_handler)


class AzureToSnowflakeLandingLoader:
    def __init__(self, azure_conn_string: str, snowflake_config: dict):
        # Input Validation (TC007)
        if not azure_conn_string or not isinstance(azure_conn_string, str) or not azure_conn_string.strip():
            raise ValueError("azure_conn_string must be a non-empty string")
        if not snowflake_config or not isinstance(snowflake_config, dict):
            raise ValueError("snowflake_config must be a non-empty dictionary")
            
        self.azure_conn_string = azure_conn_string
        self.snowflake_config = snowflake_config
        self.container_name = "hospitaldata"
        self.load_batch_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.processed_files_cache = set()  # Initialize cache for TC031
        
    def setup_connections(self):
        try:
            self.blob_service_client = BlobServiceClient.from_connection_string(
                self.azure_conn_string
            )
            logger.info({"message": "Azure BlobServiceClient initialized"})
            
            self.snowflake_conn = snowflake.connector.connect(
                user=self.snowflake_config.get("user"),
                password=self.snowflake_config.get("password"),
                account=self.snowflake_config.get("account"),
                database="LANDING",
                schema="LND"
            )
            logger.info({"message": "Snowflake connection established to LANDING database"})
            self.cursor = self.snowflake_conn.cursor()
            
            # Schema Validation (TC013)
            self.cursor.execute("SHOW SCHEMAS LIKE 'LND'")
            schema_exists = self.cursor.fetchone()
            if not schema_exists:
                raise ValueError("Target schema LND does not exist in database LANDING")
            logger.info({"message": "Target schema LND validated"})

            # Table Validation (TC014)
            self.cursor.execute("SHOW TABLES LIKE 'RAW_DATA'")
            table_exists = self.cursor.fetchone()
            if not table_exists:
                raise ValueError("Target table raw_data does not exist in schema LND")
            logger.info({"message": "Target table raw_data validated"})

            self.cursor.execute("SHOW TABLES LIKE 'AUDIT_LOG'")
            audit_exists = self.cursor.fetchone()
            if not audit_exists:
                raise ValueError("Target table audit_log does not exist in schema LND")
            logger.info({"message": "Target table audit_log validated"})
            
        except snowflake.connector.Error as db_error:
            logger.error({"message": f"Snowflake connection or validation failed: {str(db_error)}"})
            raise
        except Exception as e:
            logger.error({"message": f"Connection setup failed: {str(e)}"})
            raise
    
    def validate_azure_container(self):
        try:
            container_client = self.blob_service_client.get_container_client(
                self.container_name
            )
            if not container_client.exists():
                logger.error({"message": f"Container '{self.container_name}' does not exist"})
                return False
            logger.info({"message": f"Container '{self.container_name}' exists"})
            return True
        except Exception as e:
            logger.error({"message": f"Container validation failed: {str(e)}"})
            return False
    
    def list_source_files(self):
        try:
            from azure.core.exceptions import ResourceNotFoundError
            container_client = self.blob_service_client.get_container_client(
                self.container_name
            )
            blobs = list(container_client.list_blobs())
            
            if not blobs:
                logger.warning({"message": "No files found in container - empty container"})
                return []
            
            logger.info({"message": f"Found {len(blobs)} files in container"})
            return [blob.name for blob in blobs]
            
        except ResourceNotFoundError:
            logger.error({"message": f"Container '{self.container_name}' not found"})
            return []
        except Exception as e:
            logger.error({"message": f"Error listing blobs: {str(e)}"})
            return []
    
    def get_file_checksum(self, blob_data):
        return md5(blob_data).hexdigest()
    
    def is_file_duplicate(self, file_checksum):
        """Check if file is in the processed cache (TC031: use cache instead of DB query)"""
        return file_checksum in self.processed_files_cache
    
    def load_with_retry(self, file_name, max_retries=3, backoff_factor=2):
        for attempt in range(max_retries):
            try:
                container_client = self.blob_service_client.get_container_client(
                    self.container_name
                )
                blob_client = container_client.get_blob_client(file_name)
                
                if not blob_client.exists():
                    logger.warning({"message": f"File '{file_name}' not found in blob storage"})
                    return None, 0, "File not found"
                
                blob_data = blob_client.download_blob().readall()
                return blob_data, len(blob_data), ""
                
            except ServiceRequestError as e:
                logger.error({"message": f"Attempt {attempt+1}: Service error", "error": str(e)})  
                if attempt < max_retries - 1:
                    wait_time = backoff_factor ** attempt
                    logger.info({"message": f"Retrying in {wait_time} seconds..."})
                    time.sleep(wait_time)
                else:
                    return None, 0, str(e)
            except ContainerNotFound:
                logger.error({"message": f"Container not found: {self.container_name}"})
                return None, 0, "Container not found"
            except Exception as e:
                logger.error({"message": f"Attempt {attempt+1}: Error loading file", "error": str(e)})  
                if attempt < max_retries - 1:
                    time.sleep(backoff_factor ** attempt)
                else:
                    return None, 0, str(e)
        
        return None, 0, "Max retries exceeded"
    
    def load_to_landing(self, file_list):
        self.processed_files_cache = set()
        audit_batch = []
        
        # Disable autocommit to manage transaction boundaries (TC016)
        try:
            self.snowflake_conn.autocommit = False
        except Exception:
            pass
        
        try:
            processed_query = "SELECT DISTINCT file_hash FROM LANDING.LND.audit_log WHERE load_status='SUCCESS'"
            self.cursor.execute(processed_query)
            self.processed_files_cache = set(row[0] for row in self.cursor.fetchall())
            logger.info({"message": f"Cached {len(self.processed_files_cache)} processed file hashes", "count": len(self.processed_files_cache)})
        except Exception as e:
            logger.warning({"message": f"Could not cache processed files: {e}"})
        
        for file_name in file_list:
            load_status = "FAILED"
            row_count = 0
            error_message = ""
            file_hash = ""
            
            try:
                # Explicit transaction boundary for each file load operation (TC016)
                self.cursor.execute("BEGIN TRANSACTION")
                
                blob_data, data_size, error = self.load_with_retry(file_name)
                
                if blob_data is None:
                    logger.error({"message": f"Failed to load '{file_name}': {error}"})
                    audit_batch.append((file_name, "FAILED", 0, error, ""))
                    self.cursor.execute("ROLLBACK")
                    continue
                
                file_hash = self.get_file_checksum(blob_data)
                
                # TC031: Use cached check instead of database query
                if self.is_file_duplicate(file_hash):
                    logger.warning({"message": f"File '{file_name}' already processed (hash: {file_hash})"})
                    audit_batch.append((file_name, "SKIPPED_DUPLICATE", 0, f"Hash: {file_hash}", file_hash))
                    self.cursor.execute("COMMIT")
                    continue
                
                try:
                    # Construct proper COPY INTO syntax (TC015) using shared key from connection string
                    conn_parts = dict(part.split('=', 1) for part in self.azure_conn_string.split(';') if '=' in part)
                    account_name = conn_parts.get("AccountName")
                    account_key = conn_parts.get("AccountKey")
                    
                    load_sql = f"""
                    COPY INTO LANDING.LND.raw_data
                    FROM 'azure://{account_name}.blob.core.windows.net/{self.container_name}/{file_name}'
                    CREDENTIALS = (AZURE_SHARED_KEY = '{account_key}')
                    FILE_FORMAT = (TYPE = 'JSON')
                    ON_ERROR = CONTINUE
                    """
                    
                    self.cursor.execute(load_sql)
                    row_count = self.cursor.rowcount
                    load_status = "SUCCESS"
                    self.processed_files_cache.add(file_hash)
                    logger.info({"message": f"File '{file_name}' loaded successfully", "rows": row_count})
                    audit_batch.append((file_name, load_status, row_count, "", file_hash))
                    self.cursor.execute("COMMIT")  # Commit transaction on success
                    
                except snowflake.connector.Error as db_error:  # Snowflake error handling (TC017)
                    error_message = f"Snowflake database error: {str(db_error)}"
                    load_status = "FAILED"
                    logger.error({"message": f"Database error loading file '{file_name}': {error_message}"})
                    audit_batch.append((file_name, load_status, row_count, error_message, file_hash))
                    self.cursor.execute("ROLLBACK")  # Rollback on database failure
                except Exception as load_error:
                    error_message = f"Load error: {str(load_error)}"
                    load_status = "FAILED"
                    logger.error({"message": f"Error loading file '{file_name}': {error_message}"})
                    audit_batch.append((file_name, load_status, row_count, error_message, file_hash))
                    self.cursor.execute("ROLLBACK")  # Rollback on general failure
                
            except snowflake.connector.Error as outer_db_error:  # Snowflake error handling (TC017)
                error_message = f"Snowflake transaction error: {str(outer_db_error)}"
                logger.error({"message": f"Database transaction error for '{file_name}': {error_message}"})
                audit_batch.append((file_name, "FAILED", 0, error_message, ""))
                try:
                    self.cursor.execute("ROLLBACK")
                except Exception:
                    pass
            except ServiceRequestError as e:
                error_message = f"Service error: {str(e)}"
                logger.error({"message": f"Service request error for '{file_name}': {error_message}"})
                audit_batch.append((file_name, "FAILED", 0, error_message, ""))
                try:
                    self.cursor.execute("ROLLBACK")
                except Exception:
                    pass
            except ContainerNotFound as e:
                error_message = f"Container error: {str(e)}"
                logger.error({"message": f"Container error for '{file_name}': {error_message}"})
                audit_batch.append((file_name, "FAILED", 0, error_message, ""))
                try:
                    self.cursor.execute("ROLLBACK")
                except Exception:
                    pass
            except Exception as e:
                error_message = f"Unexpected error: {str(e)}"
                logger.error({"message": f"Unexpected error for '{file_name}': {error_message}"})
                audit_batch.append((file_name, "FAILED", 0, error_message, ""))
                try:
                    self.cursor.execute("ROLLBACK")
                except Exception:
                    pass
        
        self._log_audit_batch(audit_batch)
    
    def _log_audit_batch(self, audit_batch: list):
        """TC032: Use batch insert instead of row-by-row"""
        if not audit_batch:
            return
        
        try:
            # Build single batch INSERT with multiple VALUES clauses
            values_clauses = []
            for file_name, status, row_count, error_msg, file_hash in audit_batch:
                # Escape single quotes in strings
                file_name_escaped = file_name.replace("'", "''")
                error_msg_escaped = error_msg.replace("'", "''")
                file_hash_escaped = file_hash.replace("'", "''")
                
                values_clauses.append(
                    f"('{file_name_escaped}', CURRENT_TIMESTAMP(), {row_count}, '{status}', "
                    f"'{self.load_batch_id}', '{error_msg_escaped}', '{file_hash_escaped}')"
                )
            
            # Execute single batch INSERT
            audit_sql = f"""
            INSERT INTO LANDING.LND.audit_log 
            (file_name, load_timestamp, row_count, load_status, batch_id, error_message, file_hash)
            VALUES {','.join(values_clauses)}
            """
            self.cursor.execute("BEGIN TRANSACTION")
            self.cursor.execute(audit_sql)
            self.cursor.execute("COMMIT")
            logger.info({"message": f"Batch inserted {len(audit_batch)} audit records"})
        except snowflake.connector.Error as db_error:  # Snowflake error handling (TC017)
            logger.error({"message": f"Batch audit logging database failed: {str(db_error)}"})
            try:
                self.cursor.execute("ROLLBACK")
            except Exception:
                pass
        except Exception as e:
            logger.error({"message": f"Batch audit logging failed: {str(e)}"})
            try:
                self.cursor.execute("ROLLBACK")
            except Exception:
                pass  
    
    def run(self):
        try:
            self.setup_connections()
            
            if not self.validate_azure_container():
                raise Exception("Azure container validation failed")
            
            files = self.list_source_files()
            
            if not files:
                logger.warning({"message": "No files to process"})
                return
            
            self.load_to_landing(files)
            
            logger.info({"message": "Load process completed"})
            
        except Exception as e:
            logger.error({"message": f"Load failed: {str(e)}"})  
            raise
        finally:
            if hasattr(self, 'cursor'):
                self.cursor.close()
            if hasattr(self, 'snowflake_conn'):
                self.snowflake_conn.close()


if __name__ == "__main__":
    azure_connection_string = "DefaultEndpointsProtocol=https;AccountName=...;AccountKey=...;EndpointSuffix=core.windows.net"
    
    snowflake_config = {
        "user": "your_user",
        "password": "your_password",
        "account": "your_account"
    }
    
    loader = AzureToSnowflakeLandingLoader(azure_connection_string, snowflake_config)
    loader.run()
