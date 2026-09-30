import re
import ast
from typing import Dict, List
from dataclasses import dataclass, field

@dataclass
class AzureCapabilityModel:
    source: str = None
    auth_method: str = None
    retry_logic: bool = False
    error_handling: bool = False
    connection_validation: bool = False
    file_listing: bool = False
    duplicate_detection: bool = False
    checksum_validation: bool = False

@dataclass
class SnowflakeCapabilityModel:
    load_strategy: str = None
    truncate_before_load: bool = False
    transaction_management: bool = False
    rollback_on_failure: bool = False
    batch_operations: bool = False
    error_handling: bool = False
    audit_logging: bool = False
    structured_logging: bool = False
    connection_type: str = None

class CapabilityDiscovery:
    def __init__(self, code: str):
        self.code = code
        self.tree = ast.parse(code)
        self.azure_model = AzureCapabilityModel()
        self.snowflake_model = SnowflakeCapabilityModel()

    def discover_azure_capabilities(self) -> AzureCapabilityModel:
        self._detect_azure_source()
        self._detect_azure_auth()
        self._detect_retry_logic()
        self._detect_error_handling()
        self._detect_connection_validation()
        self._detect_file_listing()
        self._detect_duplicate_detection()
        self._detect_checksum_validation()
        return self.azure_model

    def discover_snowflake_capabilities(self) -> SnowflakeCapabilityModel:
        self._detect_load_strategy()
        self._detect_truncate()
        self._detect_transaction_management()
        self._detect_rollback()
        self._detect_batch_operations()
        self._detect_sf_error_handling()
        self._detect_audit_logging()
        self._detect_structured_logging()
        self._detect_sf_connection_type()
        return self.snowflake_model

    def _detect_azure_source(self):
        if "BlobServiceClient" in self.code:
            self.azure_model.source = "blob_storage"
        elif "DataLakeServiceClient" in self.code:
            self.azure_model.source = "data_lake"
        elif "QueueServiceClient" in self.code:
            self.azure_model.source = "queue"

    def _detect_azure_auth(self):
        if "AyAzureKeyVault" in self.code or "get_secret" in self.code:
            self.azure_model.auth_method = "key_vault"
        elif "DefaultAzureCredential" in self.code:
            self.azure_model.auth_method = "managed_identity"
        elif "from_connection_string" in self.code:
            self.azure_model.auth_method = "connection_string"

    def _detect_retry_logic(self):
        patterns = [
            r"max_retries",
            r"for\s+\w+\s+in\s+range\(\s*\d+\s*\)",
            r"backoff",
            r"exponential",
            r"attempt\s*\+=",
            r"retry.*for",
            r"time\.sleep"
        ]
        for pattern in patterns:
            if re.search(pattern, self.code, re.IGNORECASE):
                self.azure_model.retry_logic = True
                break

    def _detect_error_handling(self):
        try:
            for node in ast.walk(self.tree):
                if isinstance(node, ast.Try):
                    handlers = [h for h in node.handlers if h.type]
                    if handlers:
                        self.azure_model.error_handling = True
                        break
        except:
            pass

    def _detect_connection_validation(self):
        if re.search(r"\.exists\(\)", self.code):
            self.azure_model.connection_validation = True

    def _detect_file_listing(self):
        if re.search(r"list_blobs|list_containers", self.code):
            self.azure_model.file_listing = True

    def _detect_duplicate_detection(self):
        patterns = [
            r"duplicate",
            r"processed.*cache",
            r"cache.*processed",
            r"already.*processed"
        ]
        for pattern in patterns:
            if re.search(pattern, self.code, re.IGNORECASE):
                self.azure_model.duplicate_detection = True
                break

    def _detect_checksum_validation(self):
        patterns = [
            r"md5|sha256|checksum|hash",
            r"get_file_checksum"
        ]
        for pattern in patterns:
            if re.search(pattern, self.code, re.IGNORECASE):
                self.azure_model.checksum_validation = True
                break

    def _detect_load_strategy(self):
        if "COPY INTO" in self.code or "copy_into" in self.code.lower():
            self.snowflake_model.load_strategy = "copy_into"
        elif "MERGE INTO" in self.code or "merge_into" in self.code.lower():
            self.snowflake_model.load_strategy = "merge"
        elif "INSERT INTO" in self.code or "insert_into" in self.code.lower():
            self.snowflake_model.load_strategy = "insert"

    def _detect_truncate(self):
        if re.search(r"TRUNCATE|truncate\(", self.code):
            self.snowflake_model.truncate_before_load = True

    def _detect_transaction_management(self):
        patterns = [
            r"BEGIN\s+TRANSACTION",
            r"autocommit\s*=\s*False",
            r"begin_transaction"
        ]
        for pattern in patterns:
            if re.search(pattern, self.code, re.IGNORECASE):
                self.snowflake_model.transaction_management = True
                break

    def _detect_rollback(self):
        if re.search(r"ROLLBACK|rollback\(\)", self.code):
            self.snowflake_model.rollback_on_failure = True

    def _detect_batch_operations(self):
        patterns = [
            r"executemany",
            r"batch.*insert",
            r"VALUES.*,.*VALUES",
            r"batch.*operation"
        ]
        for pattern in patterns:
            if re.search(pattern, self.code, re.IGNORECASE):
                self.snowflake_model.batch_operations = True
                break

    def _detect_sf_error_handling(self):
        try:
            for node in ast.walk(self.tree):
                if isinstance(node, ast.Try):
                    handlers = [h for h in node.handlers if h.type]
                    if handlers:
                        self.snowflake_model.error_handling = True
                        break
        except:
            pass

    def _detect_audit_logging(self):
        if re.search(r"audit|audit_table|log", self.code, re.IGNORECASE):
            self.snowflake_model.audit_logging = True

    def _detect_structured_logging(self):
        patterns = [
            r"json\.dumps|JSONFormatter",
            r"logger\.info\(\{",
            r"structured.*log"
        ]
        for pattern in patterns:
            if re.search(pattern, self.code, re.IGNORECASE):
                self.snowflake_model.structured_logging = True
                break

    def _detect_sf_connection_type(self):
        if "private_key" in self.code.lower():
            self.snowflake_model.connection_type = "private_key"
        elif "password" in self.code.lower() and "snowflake" in self.code.lower():
            self.snowflake_model.connection_type = "password"
        elif "DefaultAzureCredential" in self.code:
            self.snowflake_model.connection_type = "managed_identity"
