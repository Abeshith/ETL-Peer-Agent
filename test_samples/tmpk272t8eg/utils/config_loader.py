import yaml
import os
from pathlib import Path


def load_config():
    root = Path(__file__).parent.parent
    candidates = [
        root / "config.yaml",
        root / "config.yml",
        root / "test_samples" / "config.yaml",
        root / "test_samples" / "config.yml",
    ]
    config_path = next((p for p in candidates if p.exists()), None)
    if not config_path:
        raise FileNotFoundError(f"Config file not found in {root}")
    with open(config_path, "r") as f:
        return yaml.safe_load(f)


def _get_env_block(config: dict) -> dict:
    """Return the first env-keyed block (dev/prd/etc.) or first dict value."""
    for k in ['dev', 'prd', 'development', 'production', 'staging', 'uat', 'test']:
        if k in config and isinstance(config[k], dict):
            return config[k]
    for v in config.values():
        if isinstance(v, dict):
            return v
    return config


def get_azure_config():
    """Return source/storage config — works for Azure Blob, S3, SFTP, or any source."""
    config = load_config()
    # Explicit azure root key (legacy)
    if "azure" in config:
        return config["azure"]
    env = _get_env_block(config)
    # Walk known source section names
    for key in ["blob_storage", "s3", "sftp", "ftp", "source", "storage"]:
        if key in env:
            return env[key]
    return {}


def get_snowflake_config():
    """Return Snowflake connection config from any YAML structure."""
    config = load_config()
    # Explicit snowflake root key (flat structure)
    if "snowflake" in config and isinstance(config["snowflake"], dict):
        return config["snowflake"]
    env = _get_env_block(config)
    for k, v in env.items():
        if "snowflake" in k.lower():
            if isinstance(v, list) and v:
                return v[0]
            if isinstance(v, dict):
                return v
    return {}


def get_landing_db_config():
    """Return landing DB config — falls back to deriving from snowflake block."""
    config = load_config()
    if "landing_db" in config:
        return config["landing_db"]
    sf = get_snowflake_config()
    result = {}
    if sf.get("database"):
        result["database"] = sf["database"]
    if sf.get("schema"):
        result["schema"] = sf["schema"]
    tables = sf.get("tables", [])
    for t in tables:
        if not isinstance(t, dict):
            continue
        name = t.get("name", "")
        if "audit" in name.lower() and not result.get("audit_table"):
            result["audit_table"] = name
        elif name and not result.get("raw_data_table"):
            result["raw_data_table"] = name
    return result
