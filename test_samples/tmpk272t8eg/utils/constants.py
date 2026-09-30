TEST_CASES = {
    "Phase1": [
        {"id": "TC001", "name": "Azure Connection Config", "phase": "Phase1"},
        {"id": "TC002", "name": "Container Configured", "phase": "Phase1"},
        {"id": "TC003", "name": "Snowflake Connection Config", "phase": "Phase1"},
        {"id": "TC004", "name": "Target Database Check (LANDING)", "phase": "Phase1"},
        {"id": "TC005", "name": "Target Schema Check (LND)", "phase": "Phase1"},
        {"id": "TC006", "name": "Target Table Identified", "phase": "Phase1"},
    ],
    "Phase2": [
        {"id": "TC007", "name": "Container Existence Validation", "phase": "Phase2"},
        {"id": "TC008", "name": "File Listing Logic Present", "phase": "Phase2"},
        {"id": "TC009", "name": "Empty Container Handling", "phase": "Phase2"},
        {"id": "TC010", "name": "Invalid Credentials Handling", "phase": "Phase2"},
        {"id": "TC011", "name": "Exception Handling for Azure", "phase": "Phase2"},
    ],
    "Phase3": [
        {"id": "TC012", "name": "Snowflake LANDING Database Connection", "phase": "Phase3"},
        {"id": "TC013", "name": "Schema LND Referenced", "phase": "Phase3"},
        {"id": "TC014", "name": "Table raw_data Identified", "phase": "Phase3"},
        {"id": "TC015", "name": "COPY INTO Statement Present", "phase": "Phase3"},
        {"id": "TC016", "name": "Load Error Handling", "phase": "Phase3"},
        {"id": "TC017", "name": "Audit Log Table Referenced", "phase": "Phase3"},
    ],
    "Phase4": [
        {"id": "TC018", "name": "Azure Container Exists", "phase": "Phase4"},
        {"id": "TC019", "name": "Source File Exists in Azure", "phase": "Phase4"},
        {"id": "TC020", "name": "Snowflake LANDING DB Exists", "phase": "Phase4"},
        {"id": "TC021", "name": "Snowflake LND Schema Exists", "phase": "Phase4"},
        {"id": "TC022", "name": "Snowflake raw_data Table Exists", "phase": "Phase4"},
        {"id": "TC023", "name": "Snowflake audit_log Table Exists", "phase": "Phase4"},
        {"id": "TC024", "name": "Safety Check - MAIN DB Not Targeted", "phase": "Phase4"},
    ],
    "Phase5": [
        {"id": "TC025", "name": "Duplicate Prevention Logic", "phase": "Phase5"},
        {"id": "TC026", "name": "Retry Mechanism", "phase": "Phase5"},
        {"id": "TC027", "name": "Audit Logging to Table", "phase": "Phase5"},
        {"id": "TC028", "name": "Failure Logging with Details", "phase": "Phase5"},
        {"id": "TC029", "name": "Batch Tracking", "phase": "Phase5"},
    ],
    "Phase6": [
        {"id": "TC030", "name": "SELECT Star Usage", "phase": "Phase6"},
        {"id": "TC031", "name": "Excessive DB Calls", "phase": "Phase6"},
        {"id": "TC032", "name": "Batch Insert Usage", "phase": "Phase6"},
        {"id": "TC033", "name": "Structured Logging", "phase": "Phase6"},
    ],
}

DATABASE_STRUCTURE = {
    "landing_db": "LANDING",
    "landing_schema": "LND",
    "landing_table": "raw_data",
    "audit_table": "audit_log",
    "main_db": "MAIN",
}

AZURE_PATTERNS = {
    "blob_service": r"BlobServiceClient",
    "container_client": r"get_container_client",
    "list_blobs": r"list_blobs",
    "container_exists": r"\.exists\(\)|container\.exists\(\)|container_client\.exists\(\)",
    "get_blob_client": r"get_blob_client",
}

SNOWFLAKE_PATTERNS = {
    "snowflake_connect": r"snowflake\.connector\.connect",
    "copy_into": r"COPY\s+INTO",
    "insert": r"INSERT\s+INTO",
    "execute": r"execute|executemany",
}

GROQ_MODELS = {
    "default": "mixtral-8x7b-32768",
    "faster": "gemma-7b-it",
}

LOGGING_LEVELS = {
    "INFO": "info",
    "WARNING": "warning",
    "ERROR": "error",
    "CRITICAL": "critical",
}
