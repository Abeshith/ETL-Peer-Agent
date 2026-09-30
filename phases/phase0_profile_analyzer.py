import re
import ast
from pathlib import Path
from typing import Dict, Any, List
import yaml
from phases.etl_profile import (
    ETLProfile, ComplexityLevel, LoadStrategy, SourceType, TargetType, CredentialType
)
from phases.capability_registry import CapabilityRegistry

try:
    from groq import Groq
    GROQ_AVAILABLE = True
except ImportError:
    GROQ_AVAILABLE = False


class Phase0ProfileAnalyzer:
    def __init__(self, code: str, etl_file_path: str = None, env: str = None):
        self.code = code
        self.etl_file_path = etl_file_path
        self.env = env
        self.profile = ETLProfile()
        self.registry = CapabilityRegistry()
        self.config = self._load_config()

    def _load_config(self) -> Dict[str, Any]:
        if not self.etl_file_path:
            return {}
        try:
            for base in [Path(self.etl_file_path).parent, Path(self.etl_file_path).parent.parent]:
                for fname in ["config.yaml", "config.yml"]:
                    p = base / fname
                    if p.exists():
                        with open(p) as f:
                            cfg = yaml.safe_load(f) or {}
                        if self.env and self.env in cfg:
                            return cfg[self.env]
                        if cfg:
                            return next(v for v in cfg.values() if isinstance(v, dict)) if isinstance(cfg, dict) else {}
        except Exception as e:
            print(f"  [Phase 0] LLM: FALLBACK - {type(e).__name__}: {str(e)[:100]}")
        return {}

    def run(self) -> ETLProfile:
        # LLM runs first — open-ended discovery, no fixed pattern list
        if self._has_llm():
            self._enhance_profile_with_llm()
        # Regex fills any gaps the LLM left (or covers LLM-unavailable runs)
        self._detect_source_type()
        self._detect_target_type()
        self._detect_credential_type()
        self._detect_load_strategy()
        self._detect_capabilities()
        self._detect_complexity()
        self._extract_cli_arguments()
        self._discover_tables_and_sources()
        self.profile.config_structure = self.config
        return self.profile

    def _has_llm(self) -> bool:
        from src.config import Config
        return bool(Config.GROQ_API_KEY)

    def _enhance_profile_with_llm(self):
        try:
            from groq import Groq
            from src.config import Config
            import json, re as _re
            print("  [Phase 0] LLM: Attempting Groq...")
            client = Groq(api_key=Config.GROQ_API_KEY)
            prompt = f"""You are an ETL profiling agent. Read the ETL code and identify EVERYTHING present — do not limit yourself to known libraries or patterns.

1. ETL architecture — describe what this ETL actually does (e.g. blob_etl, stored_proc_etl, api_etl, dbt_etl, etc.)
2. Load strategy — ONE of: FULL_REFRESH, INCREMENTAL, MERGE, CDC, APPEND, TRUNCATE_LOAD, DDL, AUDIT, UPSERT
3. Source systems — list ALL data sources actually used (Azure Blob, S3, SFTP, API, local files, Snowflake stage, database, stream, etc.)
4. Target systems — list ALL targets (Snowflake, BigQuery, Redshift, Postgres, Delta, etc.)
5. Auth methods — list ALL credential mechanisms (Azure Key Vault, AWS Secrets Manager, GCP Secret Manager, HashiCorp Vault, env vars, config file, managed identity, etc.)
6. Technologies — list ALL libraries/frameworks imported or used
7. Capabilities present — list ONLY what is actually in the code:
   retry_logic, error_handling, transaction_management, batch_operations, audit_logging,
   duplicate_detection, connection_validation, data_transformation, cross_database_ops,
   uuid_generation, structured_logging
8. Complexity: simple, medium, or complex
9. source_type — ONE of: blob, s3, sftp, local_fs, database, api, stream, snowflake_stage, unknown
10. target_type — ONE of: snowflake, bigquery, redshift, postgres, delta, unknown
11. credential_type — ONE of: keyvault, secrets_manager, env_vars, config_file, managed_identity, direct, unknown

Do NOT infer anything not present in the code. Do NOT generate recommendations.

Code:
```python
{self.code[:4000]}
```

Return ONLY valid JSON (no trailing commas):
{{"etl_architecture": "...", "load_strategy": "...", "source_systems": [...], "target_systems": [...], "auth_methods": [...], "technologies": [...], "capabilities": [...], "complexity": "...", "source_type": "...", "target_type": "...", "credential_type": "..."}}"""
            response = client.chat.completions.create(
                model=Config.GROQ_MODEL,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=1200, temperature=0.1,
            )
            text = response.choices[0].message.content.strip()
            start = text.find('{')
            end = text.rfind('}')
            raw = text[start:end + 1] if start != -1 and end != -1 else text
            raw = _re.sub(r',\s*([}\]])', r'\1', raw)
            try:
                llm_result = json.loads(raw)
            except json.JSONDecodeError:
                llm_result = {}
                for field in ["load_strategy", "complexity", "source_type", "target_type", "credential_type"]:
                    m = _re.search(rf'"{field}"\s*:\s*"([^"]+)"', raw)
                    if m: llm_result[field] = m.group(1)
                caps = _re.findall(r'"(retry_logic|error_handling|transaction_management|batch_operations|audit_logging|duplicate_detection|connection_validation|data_transformation|cross_database_ops|uuid_generation|structured_logging)"', raw)
                if caps: llm_result["capabilities"] = caps

            # Apply LLM results — these override defaults, regex only fills remaining gaps
            if llm_result.get("load_strategy"):
                try:
                    self.profile.load_strategy = LoadStrategy[llm_result["load_strategy"].upper()]
                except KeyError:
                    pass
            if llm_result.get("complexity"):
                try:
                    self.profile.complexity_level = ComplexityLevel[llm_result["complexity"].upper()]
                except KeyError:
                    pass
            if llm_result.get("source_type"):
                try:
                    self.profile.source_type = SourceType[llm_result["source_type"].upper()]
                except KeyError:
                    pass
            if llm_result.get("target_type"):
                try:
                    self.profile.target_type = TargetType[llm_result["target_type"].upper()]
                except KeyError:
                    pass
            if llm_result.get("credential_type"):
                try:
                    self.profile.credential_type = CredentialType[llm_result["credential_type"].upper()]
                except KeyError:
                    pass
            for cap in llm_result.get("capabilities", []):
                if hasattr(self.profile, f"has_{cap}"):
                    setattr(self.profile, f"has_{cap}", True)
                if cap not in self.profile.discovered_capabilities:
                    self.profile.discovered_capabilities.append(cap)
            # Store raw LLM findings for Phase 2/3 to reuse
            self.profile.llm_findings = llm_result
            print("  [Phase 0] LLM: SUCCESS")
        except Exception as e:
            print(f"  [Phase 0] LLM: FALLBACK - {str(e)[:100]}")
            self.profile.llm_findings = {}

    def _detect_source_type(self):
        # Only fills gap if LLM didn't already set it
        if self.profile.source_type != SourceType.UNKNOWN:
            return
        if re.search(r"BlobServiceClient|blob\.core\.windows|azure\.storage\.blob", self.code, re.I):
            self.profile.source_type = SourceType.BLOB
        elif re.search(r"boto3.*s3|s3\.get_object", self.code, re.I):
            self.profile.source_type = SourceType.S3
        elif re.search(r"paramiko|SFTPClient", self.code, re.I):
            self.profile.source_type = SourceType.SFTP
        elif re.search(r"os\.listdir|open.*\.csv|local", self.code, re.I):
            self.profile.source_type = SourceType.LOCAL_FS
        elif re.search(r"requests\.(get|post)|http", self.code, re.I):
            self.profile.source_type = SourceType.API
        elif re.search(r"sqlalchemy|pymysql|psycopg", self.code, re.I):
            self.profile.source_type = SourceType.DATABASE

    def _detect_target_type(self):
        if self.profile.target_type != TargetType.UNKNOWN:
            return
        if re.search(r"snowflake\.connector|AYSnowflakeLoader", self.code, re.I):
            self.profile.target_type = TargetType.SNOWFLAKE
        elif re.search(r"bigquery|google\.cloud", self.code, re.I):
            self.profile.target_type = TargetType.BIGQUERY
        elif re.search(r"redshift|psycopg2.*redshift", self.code, re.I):
            self.profile.target_type = TargetType.REDSHIFT
        elif re.search(r"psycopg2|postgresql", self.code, re.I):
            self.profile.target_type = TargetType.POSTGRES
        elif re.search(r"delta|databricks", self.code, re.I):
            self.profile.target_type = TargetType.DELTA

    def _detect_credential_type(self):
        if self.profile.credential_type != CredentialType.UNKNOWN:
            return
        if re.search(r"AyAzureKeyVault|SecretClient|azure\.keyvault", self.code, re.I):
            self.profile.credential_type = CredentialType.KEYVAULT
        elif re.search(r"secretsmanager|get_secret_value", self.code, re.I):
            self.profile.credential_type = CredentialType.SECRETS_MANAGER
        elif re.search(r"os\.environ|os\.getenv|load_dotenv", self.code, re.I):
            self.profile.credential_type = CredentialType.ENV_VARS
        elif re.search(r"yaml\.safe_load|config\.yml", self.code, re.I):
            self.profile.credential_type = CredentialType.CONFIG_FILE
        elif re.search(r"DefaultAzureCredential|managed.*identity", self.code, re.I):
            self.profile.credential_type = CredentialType.MANAGED_IDENTITY

    def _detect_load_strategy(self):
        if re.search(r"MERGE\s+INTO|MERGE\s+\w", self.code, re.I):
            self.profile.load_strategy = LoadStrategy.MERGE
        elif re.search(r"UPSERT|upsert", self.code, re.I):
            self.profile.load_strategy = LoadStrategy.UPSERT
        elif re.search(r"CDC|change.data.capture|binlog|debezium|watermark.*timestamp", self.code, re.I):
            self.profile.load_strategy = LoadStrategy.CDC
        elif re.search(r"CREATE\s+(OR\s+REPLACE\s+)?TABLE|ALTER\s+TABLE|DROP\s+TABLE", self.code, re.I) and not re.search(r"INSERT|COPY INTO", self.code, re.I):
            self.profile.load_strategy = LoadStrategy.DDL
        elif re.search(r"audit|audit.*insert|INSERT.*audit", self.code, re.I) and not re.search(r"COPY INTO|INSERT INTO.*(?!audit)", self.code, re.I):
            self.profile.load_strategy = LoadStrategy.AUDIT
        elif re.search(r"WHERE.*last_modified|WHERE.*updated_at|WHERE.*run_date|incremental", self.code, re.I):
            self.profile.load_strategy = LoadStrategy.INCREMENTAL
        elif re.search(r"TRUNCATE|truncate\(", self.code):
            if re.search(r"COPY INTO|copy_into|INSERT INTO", self.code, re.I):
                self.profile.load_strategy = LoadStrategy.TRUNCATE_LOAD
            else:
                self.profile.load_strategy = LoadStrategy.FULL_REFRESH
        else:
            self.profile.load_strategy = LoadStrategy.APPEND

    def _detect_capabilities(self):
        registry = self.registry
        
        if re.search(r"try:|except", self.code):
            self.profile.has_error_handling = True
            self.profile.discovered_capabilities.append("error_handling")
        
        if re.search(r"max_retries|backoff|for.*range.*sleep|retry", self.code, re.I):
            self.profile.has_retry_logic = True
            self.profile.discovered_capabilities.append("retry_logic")
        
        if re.search(r"BEGIN|COMMIT|ROLLBACK|autocommit", self.code, re.I):
            self.profile.has_transaction_management = True
            self.profile.discovered_capabilities.append("transaction_management")
        
        if re.search(r"executemany|batch|bulk", self.code, re.I):
            self.profile.has_batch_operations = True
            self.profile.discovered_capabilities.append("batch_operations")
        
        if re.search(r"audit|audit_table|audit.*table", self.code, re.I):
            self.profile.has_audit_logging = True
            self.profile.discovered_capabilities.append("audit_logging")
        
        if re.search(r"duplicate|cache.*process|already.*process", self.code, re.I):
            self.profile.has_duplicate_detection = True
            self.profile.discovered_capabilities.append("duplicate_detection")
        
        if re.search(r"UUID|uuid_string|generate_id", self.code, re.I):
            self.profile.has_uuid_generation = True
            self.profile.discovered_capabilities.append("uuid_generation")
        
        if re.search(r"fully.*qualified|cross_db|schema.*schema", self.code, re.I):
            self.profile.has_cross_db_ops = True
            self.profile.discovered_capabilities.append("cross_database_ops")
        
        if re.search(r"\.exists\(\)", self.code):
            self.profile.discovered_capabilities.append("connection_validation")
        
        critical_caps = registry.get_critical_capabilities()
        for cap in critical_caps:
            if cap not in self.profile.discovered_capabilities:
                self.profile.critical_capabilities_missing.append(cap)

    def _detect_complexity(self):
        table_count = len(self._count_tables())
        has_dedup = self.profile.has_duplicate_detection
        has_uuid = self.profile.has_uuid_generation
        has_cross_db = self.profile.has_cross_db_ops
        custom_sql_count = len(re.findall(r"INSERT|UPDATE|DELETE|CREATE", self.code, re.I))
        
        if table_count > 2 or has_uuid or has_cross_db or custom_sql_count > 5:
            self.profile.complexity_level = ComplexityLevel.COMPLEX
        elif table_count == 2 or has_dedup or custom_sql_count > 2:
            self.profile.complexity_level = ComplexityLevel.MEDIUM
        else:
            self.profile.complexity_level = ComplexityLevel.SIMPLE

    def _extract_cli_arguments(self):
        arg_patterns = {
            "--env": r"add_argument\s*\(\s*['\"]--env['\"]",
            "--run_date": r"add_argument\s*\(\s*['\"]--run_date['\"]",
            "--run-date": r"add_argument\s*\(\s*['\"]--run-date['\"]",
            "--date": r"add_argument\s*\(\s*['\"]--date['\"]",
        }
        for arg, pattern in arg_patterns.items():
            if re.search(pattern, self.code):
                self.profile.cli_arguments[arg] = True

    def _count_tables(self) -> List[str]:
        table_matches = re.findall(r"table[_\s]*name\s*=\s*['\"]([^'\"]+)['\"]", self.code)
        if not table_matches and self.config:
            if isinstance(self.config, dict) and "snowflake" in self.config:
                sf_cfg = self.config.get("snowflake", {})
                if isinstance(sf_cfg, list):
                    for cfg in sf_cfg:
                        if isinstance(cfg, dict):
                            tables = cfg.get("tables", [])
                            if tables:
                                table_matches.extend([t.get("name", "") for t in tables if isinstance(t, dict)])
        return list(set(table_matches))

    def _discover_tables_and_sources(self):
        self.profile.discovered_tables = self._count_tables()
        
        blob_paths = re.findall(r"blob_path\s*=\s*['\"]([^'\"]+)['\"]", self.code)
        container_names = re.findall(r"container_name\s*=\s*['\"]([^'\"]+)['\"]", self.code)
        
        self.profile.discovered_sources = list(set(
            blob_paths + container_names + 
            re.findall(r"bucket|prefix|path", self.code, re.I)
        ))[:5]
