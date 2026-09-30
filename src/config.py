import os
from pathlib import Path
from dotenv import load_dotenv

env_path = Path(__file__).parent.parent / ".env"
load_dotenv(dotenv_path=env_path, override=True)

try:
    from utils.config_loader import get_azure_config, get_snowflake_config, get_landing_db_config
    AZURE_CONFIG = get_azure_config()
    SNOWFLAKE_CONFIG = get_snowflake_config()
    LANDING_DB_CONFIG = get_landing_db_config()
except Exception as e:
    print(f"Warning: Could not load config file: {e}")
    AZURE_CONFIG = {}
    SNOWFLAKE_CONFIG = {}
    LANDING_DB_CONFIG = {}


class Config:
    PROJECT_ROOT = Path(__file__).parent.parent
    OUTPUTS_DIR = PROJECT_ROOT / "outputs"
    TEST_SAMPLES_DIR = PROJECT_ROOT / "test_samples"
    
    GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
    GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant")
    
    VALIDATE_RESOURCES = os.getenv("VALIDATE_RESOURCES", "false").lower() == "true"
    ENVIRONMENT = os.getenv("ENVIRONMENT", "development")
    
    CSV_OUTPUT_FILE = OUTPUTS_DIR / "test_results.csv"
    JSON_OUTPUT_FILE = OUTPUTS_DIR / "test_analysis.json"
    
    LANDING_DB = LANDING_DB_CONFIG.get("database", "LANDING")
    LANDING_SCHEMA = LANDING_DB_CONFIG.get("schema", "LND")
    LANDING_TABLE = LANDING_DB_CONFIG.get("raw_data_table", "raw_data")
    AUDIT_TABLE = LANDING_DB_CONFIG.get("audit_table", "audit_log")
    
    AZURE_CONNECTION_STRING = AZURE_CONFIG.get("connection_string", "")
    AZURE_CONTAINER = AZURE_CONFIG.get("container_name", "")
    
    AZURE_TENANT_ID = os.getenv("AZURE_TENANT_ID", "")
    AZURE_CLIENT_ID = os.getenv("AZURE_CLIENT_ID", "")
    AZURE_CLIENT_SECRET = os.getenv("AZURE_CLIENT_SECRET", "")
    
    SNOWFLAKE_ACCOUNT = SNOWFLAKE_CONFIG.get("account", "")
    SNOWFLAKE_USER = SNOWFLAKE_CONFIG.get("user", "")
    SNOWFLAKE_PASSWORD = SNOWFLAKE_CONFIG.get("password", "")
    SNOWFLAKE_ROLE = SNOWFLAKE_CONFIG.get("role", "ACCOUNTADMIN")
    SNOWFLAKE_DATABASE = SNOWFLAKE_CONFIG.get("database", "LANDING")
    SNOWFLAKE_SCHEMA = SNOWFLAKE_CONFIG.get("schema", "LND")
    SNOWFLAKE_WAREHOUSE = SNOWFLAKE_CONFIG.get("warehouse", "COMPUTE_WH")
