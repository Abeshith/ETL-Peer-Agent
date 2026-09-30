from typing import List, Dict, Tuple
from utils.models import TestResult
import re
from phases.capability_registry import CapabilityRegistry
from phases.etl_profile import ETLProfile, TargetType

try:
    from groq import Groq
    GROQ_AVAILABLE = True
except ImportError:
    GROQ_AVAILABLE = False

class Phase3SnowflakeValidation:
    def __init__(self, python_code: str, metadata: Dict, profile: ETLProfile = None):
        self.code = python_code
        self.metadata = metadata
        self.profile = profile
        self.registry = CapabilityRegistry()
        self.test_results: List[TestResult] = []
        self.detected_patterns: Dict[str, List[str]] = {}
        self.test_counter = 0

    def run(self) -> Tuple[List[TestResult], Dict[str, List[str]]]:
        if self.profile and self.profile.target_type != TargetType.SNOWFLAKE:
            if not re.search(r'snowflake|AYSnowflakeLoader|COPY\s+INTO|run_query', self.code, re.IGNORECASE):
                return self.test_results, self.detected_patterns

        # DDL check always runs first — independent of everything else
        self._check_ddl_files()

        # Always run regex-based discovery — captures all specific Snowflake patterns
        snowflake_patterns = self._find_all_snowflake_patterns()
        for pattern_type, pattern_details in snowflake_patterns.items():
            self._generate_test_for_pattern(pattern_type, pattern_details)

        # Skip LLM entirely — regex patterns provide complete coverage

        return self.test_results, self.detected_patterns

    def _check_ddl_files(self):
        """Check DDL execution pattern — 3 variants:
        1. External DDL files referenced via config (tables_to_copy)
        2. Inline SQL strings in the ETL code (INSERT INTO, CREATE TABLE, etc.)
        3. No DDL at all (COPY INTO only)
        """
        from pathlib import Path as _Path
        import yaml as _yaml

        self.test_counter += 1
        test_id = f"SF_{self.test_counter:03d}"
        target_db = self.metadata.get("target_database", "N/A")

        # --- Detect which DDL pattern is used ---
        has_inline_ddl = bool(re.search(
            r'CREATE\s+(?:OR\s+REPLACE\s+)?TABLE|INSERT\s+INTO|MERGE\s+INTO|COPY\s+INTO',
            self.code, re.IGNORECASE
        ))
        # Abstracted SQL: any utility method call that executes SQL
        has_abstracted_sql = bool(re.search(
            r'load_csv\s*\(|run_query\s*\(',
            self.code, re.IGNORECASE
        ))
        # Loader utility class — any class whose name ends in Loader/Writer/Client used for Snowflake
        has_loader_utility = bool(re.search(
            r'AYSnowflakeLoader|SnowflakeLoader|SnowflakeWriter|SnowflakeClient'
            r'|snowflake_loader|load_to_snowflake|SnowflakeConnector',
            self.code, re.IGNORECASE
        ))
        # --- Pattern 0: Loader utility class — emit test but DO NOT return early ---
        if has_loader_utility and not has_inline_ddl:
            loader_calls = re.findall(r'(AYSnowflakeLoader|SnowflakeLoader|SnowflakeWriter|SnowflakeClient|\w+Loader)', self.code)
            unique_loaders = list(dict.fromkeys(loader_calls))
            self._add_result(test_id, f"SQL via Loader Utility ({', '.join(unique_loaders)})",
                "PASS",
                f"SQL execution fully delegated to loader utility: {unique_loaders} — DDL/DML handled internally",
                "N/A")
            # Detect stored procedure CALL statements (both direct and inside string literals)
            call_procs = re.findall(r"CALL\s+([A-Za-z0-9_]+)\s*\(", self.code, re.IGNORECASE)
            call_in_strings = re.findall(r"['\"]\s*CALL\s+([A-Za-z0-9_]+)\s*\(", self.code, re.IGNORECASE)
            all_procs = list(dict.fromkeys(call_procs + call_in_strings))
            if all_procs:
                self.test_counter += 1
                self._add_result(
                    f"SF_{self.test_counter:03d}",
                    f"Stored Procedure Calls ({', '.join(all_procs)})",
                    "PASS",
                    f"ETL calls stored procedures via run_query: {all_procs} — data load delegated to Snowflake procedures",
                    "N/A"
                )
            return  # DDL file check not applicable for loader utility ETLs
        _has_tables_to_copy_in_config = False
        if self.metadata.get("_etl_file_path"):
            try:
                from pathlib import Path as _P
                import yaml as _y
                for _base in [_P(self.metadata["_etl_file_path"]).parent, _P(self.metadata["_etl_file_path"]).parent.parent]:
                    for _fn in ["config.yaml", "config.yml"]:
                        _cp = _base / _fn
                        if _cp.exists():
                            _cd = _y.safe_load(_cp.read_text(encoding="utf-8", errors="ignore")) or {}
                            _env = self.metadata.get("_env")
                            _eb = _cd.get(_env) if _env and _env in _cd else None
                            if _eb is None and _cd:
                                _eb = next((v for v in _cd.values() if isinstance(v, dict)), None)
                            for _sf in (_eb or {}).get('snowflake', []) if isinstance((_eb or {}), dict) else []:
                                if isinstance(_sf, dict) and _sf.get('tables_to_copy'):
                                    _has_tables_to_copy_in_config = True
                            break
                    if _has_tables_to_copy_in_config:
                        break
            except Exception:
                pass
        has_ddl_file_ref = _has_tables_to_copy_in_config or bool(re.search(
            r'tables_to_copy|open\s*\(.*\.sql|read\s*\(.*\.sql|\.sql[\'"]',
            self.code, re.IGNORECASE
        ))
        # --- Pattern 1: External DDL files ---
        if has_ddl_file_ref:
            ddl_files = re.findall(r"['\"]([^'\"]+\.sql)['\"]|filename['\"]?\s*:\s*['\"]([^'\"]+)['\"]" , self.code)
            referenced = [f[0] or f[1] for f in ddl_files if (f[0] or f[1]).endswith('.sql')]

            # Also read from config yaml tables_to_copy
            etl_dir = None
            if self.metadata.get("_etl_file_path"):
                etl_dir = _Path(self.metadata["_etl_file_path"]).parent
            elif self.metadata.get("_etl_dir"):
                etl_dir = _Path(self.metadata["_etl_dir"])

            # If referenced is empty but tables_to_copy exists in config, read filenames from YAML
            if not referenced and etl_dir:
                try:
                    for fname in ["config.yaml", "config.yml"]:
                        cfg_path = etl_dir / fname
                        if not cfg_path.exists():
                            cfg_path = etl_dir.parent / fname
                        if cfg_path.exists():
                            cfg_data = _yaml.safe_load(cfg_path.read_text(encoding="utf-8", errors="ignore")) or {}
                            env_key = self.metadata.get("_env")
                            env_block = cfg_data.get(env_key) if env_key and env_key in cfg_data else None
                            if env_block is None and cfg_data:
                                env_block = next((v for v in cfg_data.values() if isinstance(v, dict)), None)
                            sf = env_block.get('snowflake', []) if isinstance(env_block, dict) else []
                            sf_block = sf[0] if isinstance(sf, list) and sf else (sf if isinstance(sf, dict) else {})
                            for t in sf_block.get('tables_to_copy', []):
                                fn = t.get('filename') if isinstance(t, dict) else str(t)
                                if fn and fn.endswith('.sql'):
                                    referenced.append(fn)
                            break
                except Exception:
                    pass

            missing, found, tables_in_ddl = [], [], []
            if etl_dir:
                for ddl_file in list(dict.fromkeys(referenced)):
                    ddl_path = etl_dir / ddl_file
                    if ddl_path.exists():
                        found.append(ddl_file)
                        content = ddl_path.read_text(encoding="utf-8", errors="ignore")
                        tnames = re.findall(
                            r'CREATE\s+(?:OR\s+REPLACE\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?'
                            r'(?:\w+\.)?(?:\w+\.)?([A-Za-z0-9_]+)',
                            content, re.IGNORECASE
                        )
                        tables_in_ddl.extend(tnames)
                    else:
                        missing.append(ddl_file)

                if missing:
                    self._add_result(test_id, f"DDL Files Exist ({len(missing)} missing)",
                        "FAIL",
                        f"Missing DDL files: {missing} | Found: {found} | Tables defined: {tables_in_ddl}",
                        f"Create missing DDL files: {missing}")
                else:
                    self._add_result(test_id, f"DDL Files Exist ({len(found)} files, {len(tables_in_ddl)} tables)",
                        "PASS",
                        f"All DDL files present: {found} | Tables defined: {tables_in_ddl}",
                        "N/A")
            else:
                # No path available — check if loader utility handles DDL internally
                if has_loader_utility:
                    loader_calls = re.findall(r'(AYSnowflakeLoader|SnowflakeLoader|SnowflakeWriter|SnowflakeClient|\w+Loader)', self.code)
                    unique_loaders = list(dict.fromkeys(loader_calls))
                    self._add_result(test_id, f"SQL via Loader Utility ({', '.join(unique_loaders)})",
                        "PASS",
                        f"DDL/DML delegated to loader utility: {unique_loaders} — tables_to_copy executed internally",
                        "N/A")
                elif referenced:
                    self._add_result(test_id, f"DDL File References ({len(referenced)} found)",
                        "PASS",
                        f"DDL files referenced in code: {referenced}",
                        "N/A")
                else:
                    self._add_result(test_id, f"DDL File References (0 found)",
                        "FAIL",
                        "No DDL file references found",
                        "Add DDL file references or inline SQL")

            # Check shares DDL subdirectory if shares are used
            if re.search(r'shares|SHARE', self.code, re.IGNORECASE) and etl_dir:
                self.test_counter += 1
                shares_dir = etl_dir / "ddl" / "shares"
                if shares_dir.exists():
                    share_files = list(shares_dir.glob("*.sql"))
                    self._add_result(
                        f"SF_{self.test_counter:03d}",
                        f"Shares DDL Directory ({len(share_files)} files)",
                        "PASS" if share_files else "FAIL",
                        f"ddl/shares/ exists with {len(share_files)} SQL file(s): {[f.name for f in share_files]}",
                        "N/A" if share_files else "Add share SQL files to ddl/shares/"
                    )
                else:
                    self._add_result(
                        f"SF_{self.test_counter:03d}",
                        "Shares DDL Directory Missing",
                        "FAIL",
                        f"ddl/shares/ directory not found at {shares_dir} — ETL will crash on listdir()",
                        "Create ddl/shares/ directory with share SQL files"
                    )
            return

        # --- Pattern 2: Inline SQL only ---
        if has_inline_ddl:
            # Extract all CREATE TABLE targets
            create_tables = re.findall(
                r'CREATE\s+(?:OR\s+REPLACE\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?'
                r'(?:[\w.{}]+\.)?(?:[\w.{}]+\.)?([\w{}]+)',
                self.code, re.IGNORECASE
            )
            # Resolve f-string variable names to config key names
            resolved_creates = []
            for t in create_tables:
                if '{' in t:
                    var_names = re.findall(r'\b(\w+_target_table|\w+_table|term_target|pipe_target)\b', self.code)
                    resolved_creates.extend(list(dict.fromkeys(var_names)) or ['<dynamic>'])
                else:
                    resolved_creates.append(t)
            resolved_creates = list(dict.fromkeys(resolved_creates))

            # Extract all INSERT INTO targets
            insert_targets = re.findall(
                r'INSERT\s+INTO\s+(?:[\w.{}]+\.)?(?:[\w.{}]+\.)?([\w{}]+)',
                self.code, re.IGNORECASE
            )
            resolved_inserts = list(dict.fromkeys(
                re.findall(r'\b(\w+_target_table|\w+_table|term_target|pipe_target)\b', self.code)
                if any('{' in t for t in insert_targets) else insert_targets
            ))

            self._add_result(test_id, f"Inline DDL: CREATE TABLE ({len(resolved_creates)} tables)",
                "PASS",
                f"CREATE OR REPLACE TABLE targets: {resolved_creates}",
                "N/A")

            # INSERT INTO check
            if resolved_inserts:
                self.test_counter += 1
                self._add_result(f"SF_{self.test_counter:03d}",
                    f"Inline DML: INSERT INTO ({len(resolved_inserts)} targets)",
                    "PASS",
                    f"INSERT INTO targets: {resolved_inserts}",
                    "N/A")

            # Stage-based SELECT (LATERAL FLATTEN / FROM @STAGE)
            stage_refs = re.findall(r'FROM\s+@([\w.{}]+)', self.code, re.IGNORECASE)
            lateral_flatten = bool(re.search(r'LATERAL\s+FLATTEN', self.code, re.IGNORECASE))
            if stage_refs or lateral_flatten:
                self.test_counter += 1
                details = []
                if stage_refs:
                    details.append(f"FROM @{', @'.join(list(dict.fromkeys(stage_refs)))}")
                if lateral_flatten:
                    details.append("LATERAL FLATTEN (semi-structured data)")
                self._add_result(f"SF_{self.test_counter:03d}",
                    "Stage-Based SELECT (LATERAL FLATTEN)",
                    "PASS",
                    f"Data read from Snowflake stage: {' | '.join(details)}",
                    "N/A")

            # FILE_FORMAT reference in SQL
            ff_refs = re.findall(r'FILE_FORMAT\s*=>\s*([\w.{}]+)', self.code, re.IGNORECASE)
            if ff_refs:
                self.test_counter += 1
                self._add_result(f"SF_{self.test_counter:03d}",
                    f"FILE_FORMAT Reference ({', '.join(list(dict.fromkeys(ff_refs)))})",
                    "PASS",
                    f"FILE_FORMAT used in COPY/SELECT: {list(dict.fromkeys(ff_refs))}",
                    "N/A")

            # METADATA$FILENAME filter
            if re.search(r'METADATA\$FILENAME', self.code, re.IGNORECASE):
                patterns_found = re.findall(r"METADATA\$FILENAME\s+ILIKE\s+'([^']+)'", self.code, re.IGNORECASE)
                self.test_counter += 1
                self._add_result(f"SF_{self.test_counter:03d}",
                    f"METADATA$FILENAME Filter ({len(patterns_found)} patterns)",
                    "PASS",
                    f"File filtering via METADATA$FILENAME: {patterns_found}",
                    "N/A")

            # CREATE OR REPLACE risk check
            if re.search(r'CREATE\s+OR\s+REPLACE\s+TABLE', self.code, re.IGNORECASE):
                count = len(re.findall(r'CREATE\s+OR\s+REPLACE\s+TABLE', self.code, re.IGNORECASE))
                self.test_counter += 1
                self._add_result(f"SF_{self.test_counter:03d}",
                    f"CREATE OR REPLACE TABLE ({count} statements)",
                    "PASS",
                    f"{count} CREATE OR REPLACE TABLE statement(s) — existing data will be dropped on each run",
                    "N/A",
                    severity="High")

            # PRIMARY KEY constraints
            pk_tables = re.findall(
                r'PRIMARY\s+KEY\s*\(([^)]+)\)',
                self.code, re.IGNORECASE
            )
            if pk_tables:
                self.test_counter += 1
                self._add_result(f"SF_{self.test_counter:03d}",
                    f"PRIMARY KEY Constraints ({len(pk_tables)} tables)",
                    "PASS",
                    f"PRIMARY KEY defined on {len(pk_tables)} table(s): columns {[p.strip() for p in pk_tables]}",
                    "N/A")

            return

        # --- Pattern 3: Abstracted SQL via utility methods (load_csv / run_query) ---
        if has_abstracted_sql:
            calls = re.findall(r'(load_csv|run_query|COPY_SQL|STATISTICS_SQL|DICTIONARY_SQL)', self.code, re.IGNORECASE)
            unique_calls = list(dict.fromkeys(c.lower() for c in calls))
            self._add_result(test_id, f"SQL via Utility Methods ({', '.join(unique_calls)})",
                "PASS",
                f"SQL execution abstracted via: {unique_calls} — COPY/CALL statements executed inside utility",
                "N/A")
            return

        # --- Pattern 4: No DDL at all ---
        self._add_result(test_id, "DDL/DML Check",
            "FAIL",
            "No DDL files referenced and no inline SQL (INSERT/CREATE/COPY) found in ETL code",
            "Add inline SQL or reference DDL files via tables_to_copy in config")

    def _has_llm(self) -> bool:
        from src.config import Config
        return bool(Config.GROQ_API_KEY)

    def _discover_patterns_via_llm(self) -> Dict[str, Dict]:
        """Ask LLM to identify ALL Snowflake-side patterns — no fixed list."""
        llm_findings = getattr(self.profile, 'llm_findings', {}) if self.profile else {}
        capabilities = llm_findings.get('capabilities', [])
        technologies = llm_findings.get('technologies', [])

        if not capabilities and not technologies:
            try:
                from utils.llm_client import call_llm
                import json, re as _re
                prompt = f"""Analyze this ETL code. Identify ALL Snowflake-side patterns present.
Do NOT limit yourself to known patterns — identify whatever is actually in the code.

Code:
```python
{self.code[:3000]}
```

Return ONLY valid JSON:
{{"load_methods": ["..."],
  "ddl_operations": ["..."],
  "dedup_patterns": ["..."],
  "transaction_patterns": ["..."],
  "audit_patterns": ["..."],
  "stage_operations": ["..."],
  "cross_db_refs": ["..."],
  "capabilities": ["..."],
  "technologies": ["..."]}}"""
                raw = call_llm(prompt, max_tokens=700, temperature=0.1)
                start, end = raw.find('{'), raw.rfind('}')
                cleaned = _re.sub(r',\s*([}\]])', r'\1', raw[start:end+1])
                llm_findings = json.loads(cleaned)
                capabilities = llm_findings.get('capabilities', [])
                technologies = llm_findings.get('technologies', [])
            except Exception as e:
                print(f"  [Phase 3] LLM: FALLBACK - {str(e)[:80]}")
                return {}

        patterns = {}
        if llm_findings.get('load_methods'):
            patterns['load_methods'] = {m: True for m in llm_findings['load_methods']}
        if llm_findings.get('ddl_operations'):
            patterns['ddl_execution'] = {d: True for d in llm_findings['ddl_operations']}
        if llm_findings.get('dedup_patterns'):
            patterns['deduplication'] = {d: True for d in llm_findings['dedup_patterns']}
        if llm_findings.get('transaction_patterns'):
            patterns['transaction_management'] = {t: True for t in llm_findings['transaction_patterns']}
        if llm_findings.get('audit_patterns'):
            patterns['audit_logging'] = {a: True for a in llm_findings['audit_patterns']}
        if llm_findings.get('stage_operations'):
            patterns['stage_operations'] = {s: True for s in llm_findings['stage_operations']}
        if llm_findings.get('cross_db_refs'):
            patterns['cross_database_ops'] = {c: True for c in llm_findings['cross_db_refs']}
        if technologies:
            patterns['technologies'] = {t: True for t in technologies}
        return patterns

    def _discover_checks_via_llm(self):
        # Kept for compatibility
        llm_patterns = self._discover_patterns_via_llm()
        for pattern_type, pattern_details in llm_patterns.items():
            self._generate_test_for_pattern(pattern_type, pattern_details)

    def _find_all_snowflake_patterns(self) -> Dict[str, Dict]:
        patterns = {}
        imports = self._find_snowflake_imports()
        if imports:
            patterns['snowflake_imports'] = imports
        conn_setup = self._find_connection_setup()
        if conn_setup:
            patterns['connection_setup'] = conn_setup
        target_config = self._find_target_config()
        if target_config:
            patterns['target_config'] = target_config
        load_methods = self._find_load_methods()
        if load_methods:
            patterns['load_methods'] = load_methods
        ddl_exec = self._find_ddl_execution()
        if ddl_exec:
            patterns['ddl_execution'] = ddl_exec
        dedup = self._find_deduplication()
        if dedup:
            patterns['deduplication'] = dedup
        txn = self._find_transaction_management()
        if txn:
            patterns['transaction_management'] = txn
        audit = self._find_audit_logging()
        if audit:
            patterns['audit_logging'] = audit
        stages = self._find_stage_operations()
        if stages:
            patterns['stage_operations'] = stages
        cross_db = self._find_cross_database_ops()
        if cross_db:
            patterns['cross_database_ops'] = cross_db
        uuid_gen = self._find_uuid_generation()
        if uuid_gen:
            patterns['uuid_generation'] = uuid_gen
        shares = self._find_share_operations()
        if shares:
            patterns['share_operations'] = shares
        return patterns

    def _find_snowflake_imports(self) -> Dict:
        imports = {}
        if re.search(r'import\s+snowflake\.connector|from\s+snowflake', self.code):
            imports['snowflake.connector'] = 'snowflake-connector-python'
        if re.search(r'AYSnowflakeLoader|from\s+utils\.snowflake', self.code):
            m = re.search(r'from\s+([\w.]+)\s+import\s+(AYSnowflakeLoader|\w+Loader)', self.code)
            imports['AYSnowflakeLoader'] = m.group(0) if m else 'AYSnowflakeLoader'
        return imports if imports else None

    def _find_connection_setup(self) -> Dict:
        setup = {}
        if re.search(r'snowflake\.connector\.connect', self.code):
            setup['direct_connect'] = 'snowflake.connector.connect()'
        if re.search(r'AYSnowflakeLoader\(', self.code):
            # Extract actual kwargs passed to AYSnowflakeLoader
            m = re.search(r'AYSnowflakeLoader\(([^)]{0,200})', self.code, re.DOTALL)
            kwargs = re.findall(r'(\w+)\s*=', m.group(1)) if m else []
            setup['ay_loader'] = f"AYSnowflakeLoader({', '.join(kwargs)})"
        if re.search(r'private_key', self.code):
            m = re.search(r"private_key.*?['\"]([^'\"]+)['\"]|secret_name.*?['\"]([^'\"]+)['\"]", self.code)
            setup['private_key_auth'] = m.group(1) or m.group(2) if m else 'private_key detected'
        if re.search(r'account\s*=|snowflake_account', self.code):
            m = re.search(r"snowflake_account\s*=\s*(\w+)", self.code)
            setup['account_config'] = f"account={m.group(1)}" if m else 'account configured'
        if re.search(r'warehouse\s*=|snowflake_warehouse', self.code):
            m = re.search(r"snowflake_warehouse\s*=\s*(\w+)", self.code)
            setup['warehouse_config'] = f"warehouse={m.group(1)}" if m else 'warehouse configured'
        return setup if setup else None

    def _find_target_config(self) -> Dict:
        config = {}
        # Extract actual DB/schema values from code variables
        db_vars = re.findall(r'snowflake_database\s*=\s*(\w+)', self.code)
        if db_vars:
            config['database'] = f"snowflake_database={db_vars[0]} (from config)"
        elif re.search(r'database|snowflake_database', self.code):
            config['database'] = 'database read from config'
        schema_vars = re.findall(r'snowflake_schema\s*=\s*(\w+)', self.code)
        if schema_vars:
            config['schema'] = f"snowflake_schema={schema_vars[0]} (from config)"
        elif re.search(r'schema|snowflake_schema', self.code):
            config['schema'] = 'schema read from config'
        # Detect table source pattern
        if re.search(r'tables_to_copy', self.code):
            config['tables'] = 'tables_to_copy (DDL files)'
        elif re.search(r"etl_configuration\['snowflake'\]", self.code):
            config['tables'] = 'snowflake config block iterated'
        elif re.search(r'target_table|snowflake_tablename', self.code):
            m = re.search(r"(\w+_target_table|snowflake_tablename)\s*=\s*['\"]([^'\"]+)['\"]", self.code)
            config['tables'] = m.group(0) if m else 'table name configured'
        return config if config else None

    def _find_load_methods(self) -> Dict:
        methods = {}
        if re.search(r'COPY\s+INTO|load_csv', self.code, re.IGNORECASE):
            m = re.search(r'COPY\s+INTO\s+([\w.@]+)', self.code, re.IGNORECASE)
            methods['COPY_INTO'] = f"COPY INTO {m.group(1)}" if m else 'COPY INTO detected'
        if re.search(r'run_query\s*\(', self.code, re.IGNORECASE):
            calls = re.findall(r'run_query\s*\(\s*(\w+)', self.code)
            methods['run_query'] = f"run_query({', '.join(calls[:3])})" if calls else 'run_query() calls detected'
        if re.search(r'INSERT\s+INTO', self.code, re.IGNORECASE):
            m = re.search(r'INSERT\s+INTO\s+([\w.{}]+)', self.code, re.IGNORECASE)
            methods['INSERT_INTO'] = f"INSERT INTO {m.group(1)}" if m else 'INSERT INTO detected'
        if re.search(r'MERGE\s+INTO', self.code, re.IGNORECASE):
            m = re.search(r'MERGE\s+INTO\s+([\w.{}]+)', self.code, re.IGNORECASE)
            methods['MERGE_INTO'] = f"MERGE INTO {m.group(1)}" if m else 'MERGE INTO detected'
        return methods if methods else None

    def _find_ddl_execution(self) -> Dict:
        ddl = {}
        create_matches = re.findall(
            r'CREATE\s+(?:OR\s+REPLACE\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([\w.{}]+)',
            self.code, re.IGNORECASE
        )
        if create_matches:
            ddl['CREATE_TABLE'] = f"CREATE TABLE: {list(dict.fromkeys(create_matches))[:3]}"
        if re.search(r'ALTER\s+TABLE', self.code, re.IGNORECASE):
            m = re.search(r'ALTER\s+TABLE\s+([\w.]+)', self.code, re.IGNORECASE)
            ddl['ALTER_TABLE'] = f"ALTER TABLE {m.group(1)}" if m else 'ALTER TABLE'
        if re.search(r'DROP\s+TABLE', self.code, re.IGNORECASE):
            m = re.search(r'DROP\s+TABLE\s+(?:IF\s+EXISTS\s+)?([\w.]+)', self.code, re.IGNORECASE)
            ddl['DROP_TABLE'] = f"DROP TABLE {m.group(1)}" if m else 'DROP TABLE'
        if re.search(r'\.sql[\'"]|tables_to_copy', self.code, re.IGNORECASE):
            sql_files = re.findall(r"['\"]([^'\"]+\.sql)['\"]", self.code)
            ddl['DDL_from_files'] = f"SQL files: {sql_files}" if sql_files else 'DDL from .sql files'
        if re.search(r'run_query|cursor\.execute', self.code, re.IGNORECASE):
            count = len(re.findall(r'run_query\s*\(|cursor\.execute\s*\(', self.code))
            ddl['query_execution'] = f"{count} run_query/execute call(s)"
        if re.search(r'LATERAL\s+FLATTEN', self.code, re.IGNORECASE):
            ddl['LATERAL_FLATTEN'] = 'LATERAL FLATTEN (semi-structured JSON/GeoJSON)'
        if re.search(r'METADATA\$FILENAME', self.code, re.IGNORECASE):
            patterns = re.findall(r"METADATA\$FILENAME\s+ILIKE\s+'([^']+)'", self.code, re.IGNORECASE)
            ddl['METADATA_FILENAME_FILTER'] = f"ILIKE patterns: {patterns}"
        return ddl if ddl else None

    def _find_deduplication(self) -> Dict:
        dedup = {}
        if re.search(r'NOT\s+EXISTS', self.code, re.IGNORECASE):
            dedup['NOT_EXISTS'] = 'WHERE NOT EXISTS pattern'
        if re.search(r'MERGE\s+INTO', self.code, re.IGNORECASE):
            dedup['MERGE'] = 'MERGE INTO (upsert)'
        if re.search(r'\bDISTINCT\b', self.code):
            dedup['DISTINCT'] = 'SELECT DISTINCT'
        if re.search(r'WHERE.*NOT\s+IN', self.code, re.IGNORECASE):
            dedup['NOT_IN'] = 'WHERE NOT IN filter'
        return dedup if dedup else None

    def _find_transaction_management(self) -> Dict:
        txn = {}
        if re.search(r'\bCOMMIT\b', self.code):
            txn['COMMIT'] = 'explicit COMMIT'
        if re.search(r'\bROLLBACK\b', self.code):
            txn['ROLLBACK'] = 'explicit ROLLBACK'
        if re.search(r'autocommit|auto_commit', self.code, re.IGNORECASE):
            m = re.search(r'autocommit\s*=\s*(\w+)', self.code, re.IGNORECASE)
            txn['autocommit'] = f"autocommit={m.group(1)}" if m else 'autocommit set'
        # Only match context managers that wrap a known DB/connection class — never file open
        db_context_pattern = re.compile(
            r'with\s+(AYSnowflakeLoader|SnowflakeLoader|SnowflakeConnector|snowflake\.connector'
            r'|\w+Loader|\w+Connection|\w+Cursor|\w+Session|\w+Transaction)\s*\(',
            re.IGNORECASE
        )
        m = db_context_pattern.search(self.code)
        if m:
            txn['context_manager'] = f"with {m.group(1)}"
        if re.search(r'try:\s', self.code):
            txn['try_except'] = 'try/except error handling'
        return txn if txn else None

    def _find_audit_logging(self) -> Dict:
        audit = {}
        if re.search(r'logging\.|logger\.', self.code):
            m = re.search(r'(logging|logger)\.(\w+)\s*\(', self.code)
            audit['logging'] = f"{m.group(0).strip()}" if m else 'logging calls'
        if re.search(r'audit_log|AUDIT', self.code):
            m = re.search(r"['\"]([^'\"]*audit[^'\"]*)['\"]|audit_log", self.code, re.IGNORECASE)
            audit['audit_table'] = m.group(1) if (m and m.group(1)) else 'audit_log reference'
        if re.search(r'print\(', self.code):
            count = len(re.findall(r'print\s*\(', self.code))
            audit['print_logging'] = f"{count} print() statement(s)"
        return audit if audit else None

    def _find_stage_operations(self) -> Dict:
        stages = {}
        if re.search(r'CREATE\s+(?:OR\s+REPLACE\s+)?STAGE', self.code, re.IGNORECASE):
            m = re.search(r'CREATE\s+(?:OR\s+REPLACE\s+)?STAGE\s+([\w.]+)', self.code, re.IGNORECASE)
            stages['CREATE_STAGE'] = f"CREATE STAGE {m.group(1)}" if m else 'CREATE STAGE'
        # Extract actual stage name from @STAGE references or config
        stage_refs = re.findall(r'@([A-Za-z0-9_]+)', self.code)
        if stage_refs:
            stages['stage_reference'] = f"@{', @'.join(list(dict.fromkeys(stage_refs)))}"
        if re.search(r'STORAGE_INTEGRATION', self.code):
            m = re.search(r"STORAGE_INTEGRATION\s*=\s*['\"]?([\w]+)", self.code)
            stages['storage_integration'] = m.group(1) if m else 'STORAGE_INTEGRATION set'
        if re.search(r'FILE_FORMAT', self.code):
            m = re.search(r"FILE_FORMAT\s*=\s*['\"]?([\w]+)", self.code)
            stages['file_format'] = m.group(1) if m else 'FILE_FORMAT set'
        return stages if stages else None

    def _find_cross_database_ops(self) -> Dict:
        cross_db = {}
        all_fqn = re.findall(r'([A-Z][A-Z0-9_]+)\.([A-Z][A-Z0-9_]+)\.([A-Z][A-Z0-9_]+)', self.code, re.IGNORECASE)
        if all_fqn:
            unique = list(dict.fromkeys(f"{a}.{b}.{c}" for a, b, c in all_fqn))
            cross_db['fully_qualified_names'] = ', '.join(unique[:3])
        dbs = list(dict.fromkeys(a.upper() for a, b, c in all_fqn)) if all_fqn else []
        if len(dbs) > 1:
            cross_db['multi_database'] = f"Databases referenced: {dbs}"
        return cross_db if cross_db else None

    def _find_uuid_generation(self) -> Dict:
        uuid = {}
        if re.search(r'UUID_STRING|uuid_string', self.code, re.IGNORECASE):
            uuid['UUID_STRING'] = 'UUID_STRING() Snowflake function'
        if re.search(r'uuid\.uuid4|uuid4\(\)', self.code):
            uuid['python_uuid'] = 'uuid.uuid4() Python generation'
        if re.search(r'SEQUENCE', self.code, re.IGNORECASE):
            m = re.search(r'SEQUENCE\s+([\w.]+)', self.code, re.IGNORECASE)
            uuid['SEQUENCE'] = f"SEQUENCE {m.group(1)}" if m else 'SEQUENCE used'
        return uuid if uuid else None

    def _find_share_operations(self) -> Dict:
        shares = {}
        if re.search(r'CREATE\s+SHARE', self.code, re.IGNORECASE):
            m = re.search(r'CREATE\s+SHARE\s+([\w]+)', self.code, re.IGNORECASE)
            shares['CREATE_SHARE'] = f"CREATE SHARE {m.group(1)}" if m else 'CREATE SHARE'
        if re.search(r'GRANT.*SHARE', self.code, re.IGNORECASE):
            shares['GRANT_SHARE'] = 'GRANT ... TO SHARE'
        if re.search(r'ALTER\s+SHARE', self.code, re.IGNORECASE):
            shares['ALTER_SHARE'] = 'ALTER SHARE'
        return shares if shares else None

    def _generate_test_for_pattern(self, pattern_type: str, pattern_details: Dict):
        self.test_counter += 1
        test_id = f"SF_{self.test_counter:03d}"
        if pattern_type == 'snowflake_imports':
            self._test_snowflake_imports(test_id, pattern_details)
        elif pattern_type == 'connection_setup':
            self._test_connection_setup(test_id, pattern_details)
        elif pattern_type == 'target_config':
            self._test_target_config(test_id, pattern_details)
        elif pattern_type == 'load_methods':
            self._test_load_methods(test_id, pattern_details)
        elif pattern_type == 'ddl_execution':
            self._test_ddl_execution(test_id, pattern_details)
        elif pattern_type == 'deduplication':
            self._test_deduplication(test_id, pattern_details)
        elif pattern_type == 'transaction_management':
            self._test_transaction_management(test_id, pattern_details)
        elif pattern_type == 'audit_logging':
            self._test_audit_logging(test_id, pattern_details)
        elif pattern_type == 'stage_operations':
            self._test_stage_operations(test_id, pattern_details)
        elif pattern_type == 'cross_database_ops':
            self._test_cross_database_ops(test_id, pattern_details)
        elif pattern_type == 'uuid_generation':
            self._test_uuid_generation(test_id, pattern_details)
        elif pattern_type == 'share_operations':
            self._test_share_operations(test_id, pattern_details)
        elif pattern_type == 'technologies':
            techs = list(pattern_details.keys())
            self._add_result(test_id, f"Technologies ({', '.join(techs[:5])})",
                "PASS", f"Libraries/frameworks used: {', '.join(techs)}", "N/A")
        else:
            # Catch-all for any novel LLM-discovered pattern type
            vals = list(pattern_details.keys())
            self._add_result(test_id, f"{pattern_type.replace('_', ' ').title()} ({', '.join(vals[:3])})",
                "PASS", f"{pattern_type}: {', '.join(vals)}", "N/A")
        if pattern_type not in self.detected_patterns:
            self.detected_patterns[pattern_type] = []
        self.detected_patterns[pattern_type].extend(pattern_details.keys())

    def _test_snowflake_imports(self, test_id: str, details: Dict):
        vals = list(details.keys())
        self._add_result(test_id, "Snowflake Imports", "PASS",
            f"imports: {','.join(vals)}", "N/A")

    def _test_connection_setup(self, test_id: str, details: Dict):
        vals = list(details.keys())
        self._add_result(test_id, "Connection Setup", "PASS",
            f"setup: {','.join(vals)}", "N/A")

    def _test_target_config(self, test_id: str, details: Dict):
        label_map = {
            "database": f"database: {details.get('database', 'from config')}",
            "schema": f"schema: {details.get('schema', 'from config')}",
            "tables": f"tables: {details.get('tables', 'from config')}",
        }
        parts = [label_map[k] for k in details.keys() if k in label_map]
        finding = " | ".join(parts) if parts else "target config: not found"
        self._add_result(test_id, "Target Configuration", "PASS" if parts else "FAIL", finding, "N/A" if parts else "Define database, schema and tables in config")

    def _test_load_methods(self, test_id: str, details: Dict):
        label_map = {
            "COPY_INTO": "COPY INTO (bulk load from stage)",
            "run_query": "run_query() — SQL executed via loader utility",
            "INSERT_INTO": "INSERT INTO (row-by-row or batch insert)",
            "MERGE_INTO": "MERGE INTO (upsert — update existing + insert new)",
        }
        labels = [label_map.get(k, k) for k in details.keys()]
        finding = f"load method: {' | '.join(labels)}" if labels else "load method: none found"
        self._add_result(test_id, "Load Methods", "PASS" if labels else "FAIL", finding, "N/A" if labels else "Add a load method (COPY INTO, INSERT INTO, or MERGE INTO)")

    def _test_ddl_execution(self, test_id: str, details: Dict):
        label_map = {
            "CREATE_TABLE": f"CREATE TABLE: {details.get('CREATE_TABLE', 'detected')}",
            "ALTER_TABLE": f"ALTER TABLE: {details.get('ALTER_TABLE', 'detected')}",
            "DROP_TABLE": f"DROP TABLE: {details.get('DROP_TABLE', 'detected')}",
            "DDL_from_files": f"DDL from .sql files: {details.get('DDL_from_files', 'detected')}",
            "query_execution": f"{details.get('query_execution', 'query execution via run_query/execute')}",
            "LATERAL_FLATTEN": "LATERAL FLATTEN (semi-structured JSON parsing)",
            "METADATA_FILENAME_FILTER": f"METADATA$FILENAME filter: {details.get('METADATA_FILENAME_FILTER', 'detected')}",
        }
        parts = [label_map[k] for k in details.keys() if k in label_map]
        finding = " | ".join(parts) if parts else "ddl/query execution: none found"
        self._add_result(test_id, "DDL / Query Execution", "PASS" if parts else "FAIL", finding, "N/A" if parts else "Add DDL or query execution logic")

    def _test_deduplication(self, test_id: str, details: Dict):
        vals = list(details.keys())
        self._add_result(test_id, "Deduplication Strategy", "PASS",
            f"dedupe: {','.join(vals)}", "N/A")

    def _test_transaction_management(self, test_id: str, details: Dict):
        label_map = {
            "COMMIT": "explicit COMMIT statement",
            "ROLLBACK": "explicit ROLLBACK statement",
            "autocommit": f"autocommit setting: {details.get('autocommit', 'configured')}",
            "context_manager": f"context manager (with block): {details.get('context_manager', 'detected')} — auto-commit/rollback on exit",
            "try_except": "try/except wraps DB operations — rollback on exception",
        }
        parts = [label_map[k] for k in details.keys() if k in label_map]
        finding = " | ".join(parts) if parts else "transaction management: none found"
        self._add_result(test_id, "Transaction Management", "PASS" if parts else "FAIL", finding, "N/A" if parts else "Wrap Snowflake operations in a context manager or explicit COMMIT/ROLLBACK")

    def _test_audit_logging(self, test_id: str, details: Dict):
        vals = list(details.keys())
        self._add_result(test_id, "Audit / Logging", "PASS",
            f"logging: {','.join(vals)}", "N/A")

    def _test_stage_operations(self, test_id: str, details: Dict):
        vals = list(details.keys())
        self._add_result(test_id, "Stage Operations", "PASS",
            f"stage: {','.join(vals)}", "N/A")

    def _test_cross_database_ops(self, test_id: str, details: Dict):
        vals = list(details.keys())
        self._add_result(test_id, "Cross-Database Operations", "PASS",
            f"cross_db: {','.join(vals)}", "N/A")

    def _test_uuid_generation(self, test_id: str, details: Dict):
        vals = list(details.keys())
        self._add_result(test_id, "UUID / Key Generation", "PASS",
            f"uuid: {','.join(vals)}", "N/A")

    def _test_share_operations(self, test_id: str, details: Dict):
        vals = list(details.keys())
        self._add_result(test_id, "Share Operations", "PASS",
            f"share: {','.join(vals)}", "N/A")

    def _add_result(self, test_id: str, name: str, status: str, finding: str, recommendation: str, severity: str = "Medium"):
        self.test_results.append(TestResult(
            test_id=test_id,
            test_name=name,
            phase="Snowflake Code Validation",
            status=status,
            finding=finding,
            recommendation=recommendation,
            check_type="Dynamic",
            severity=severity,
            database_target=self.metadata.get("target_database", "N/A"),
            stage2_relevance="Required",
        ))
