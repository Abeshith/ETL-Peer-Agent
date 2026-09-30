import random
import re
from typing import List, Dict
from pathlib import Path
from datetime import datetime
import yaml
import os
from utils.models import TestResult
from src.config import Config
from phases.etl_profile import ETLProfile

MAX_POOL = 15
MIN_EXECUTED = 5

class TestGenerationAgent:
    def __init__(self, python_code: str, metadata: dict, etl_file_path: str = None, env: str = None, profile: ETLProfile = None, test_pool: List[Dict] = None, context=None, business_rules: list = None):
        self.code = python_code
        self.metadata = metadata
        self.etl_file_path = etl_file_path
        self.env = env
        self.profile = profile
        self.test_pool = test_pool or []
        self.context = context
        self.business_rules = business_rules or []
        self.test_results: List[TestResult] = []
        self.generated_tests: List[dict] = []
        self._etl_config = self._load_etl_config()
        self.detected_patterns: Dict = {}

    def _load_etl_config(self) -> dict:
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
                            return {self.env: cfg[self.env]}
                        if isinstance(cfg, dict):
                            first_key, first_val = next(((k, v) for k, v in cfg.items() if isinstance(v, dict)), (None, {}))
                            return {first_key: first_val} if first_key else cfg
        except Exception:
            pass
        return {}

    def run(self) -> tuple:
        if self.test_pool:
            facts = self._introspect_etl()
            env_key = facts['env_key'] or 'dev'
            
            cli_args = []
            if facts['has_env']:
                cli_args.append(f"--env:{env_key}")
            if facts['has_run_date']:
                cli_args.append("--run_date:20260721")
            
            for test in self.test_pool:
                action = test.get("action", "").strip()
                if action.startswith("subprocess_args"):
                    parts = action.split(":")
                    existing_args = parts[1:] if len(parts) > 1 else []
                    
                    for arg in cli_args:
                        if arg not in existing_args and not any(a.startswith(arg.split(":")[0]) for a in existing_args):
                            existing_args.append(arg)
                    
                    test["action"] = f"subprocess_args:{':'.join(existing_args)}" if existing_args else f"subprocess_args:{':'.join(cli_args)}"
            
            self.generated_tests = self.test_pool
        else:
            self._generate_unique_functional_tests()
        
        return self.generated_tests, self.test_results

    def _generate_unique_functional_tests(self) -> None:
        facts = self._introspect_etl()
        tests = []
        env_key = facts['env_key'] or 'dev'

        action_args = []
        if facts['has_env']:
            action_args.append(f"--env:{env_key}")
        if facts['has_run_date']:
            action_args.append("--run_date:20260721")
        if not action_args:
            action_args = [f"--env:{env_key}", "--run_date:20260721"]

        action_base = f"subprocess_args:{':'.join(action_args)}"

        tests.append({
            "name": "Valid ETL Execution",
            "type": "Functional",
            "scenario": "ETL runs end-to-end with valid config and environment",
            "expected": "Exit code 0, data loads to target",
            "action": action_base
        })
        tests.append({
            "name": "Data Volume Validation",
            "type": "Functional",
            "scenario": "ETL processes multiple files and aggregates row counts",
            "expected": "All files processed, total row count correct",
            "action": action_base
        })

        # Use profile capabilities (set by LLM in Phase 0) instead of re-running regex
        profile = self.profile
        code = self.code

        has_dedup = (profile.has_duplicate_detection if profile else False) or facts['has_dedup']
        if has_dedup:
            tests.append({
                "name": "Duplicate Prevention Validation",
                "type": "Data Validation",
                "scenario": "ETL runs twice with same source data",
                "expected": "Second run detects existing records, no duplicates inserted",
                "action": action_base
            })

        if facts['has_date_sub']:
            tests.append({
                "name": "Date Substitution Pattern Validation",
                "type": "Data Validation",
                "scenario": "ETL substitutes date patterns in file paths and queries",
                "expected": "Correct files selected based on date pattern",
                "action": action_base
            })

        # Use LLM-discovered technologies to generate relevant tests
        llm_techs = [t.lower() for t in facts.get('llm_technologies', [])]
        llm_findings = getattr(profile, 'llm_findings', {}) if profile else {}

        # COPY INTO / stage — from profile or LLM findings
        if re.search(r'COPY\s+INTO|snowflake_stage|@\w+|stage', code, re.I) or \
                any('copy' in t or 'stage' in t for t in llm_techs):
            tests.append({
                "name": "COPY INTO Stage Validation",
                "type": "Data Validation",
                "scenario": "ETL copies files from Snowflake stage with correct file format",
                "expected": "Files staged correctly, COPY executed, row count matches",
                "action": action_base
            })

        # Stored procedures — from profile or LLM findings
        if re.search(r'CALL\s+\w+|run_query.*PROCEDURE|execute.*procedure', code, re.I) or \
                any('procedure' in t or 'stored_proc' in t for t in llm_techs):
            tests.append({
                "name": "Stored Procedure Execution Validation",
                "type": "Data Validation",
                "scenario": "ETL calls procedures in correct order with expected parameters",
                "expected": "All procedures execute, each returns expected result set",
                "action": action_base
            })

        # Semi-structured / JSON
        if re.search(r'LATERAL\s+FLATTEN|json|geojson|semi.*struct', code, re.I) or \
                any('json' in t or 'flatten' in t for t in llm_techs):
            tests.append({
                "name": "Semi-Structured Data Flattening Validation",
                "type": "Data Validation",
                "scenario": "ETL uses LATERAL FLATTEN to denormalize nested JSON/GeoJSON",
                "expected": "Nested records flattened correctly, array fields exploded",
                "action": action_base
            })

        if re.search(r'MERGE\s+INTO|ON.*MATCHED|UPDATE.*WHERE', code, re.I):
            tests.append({
                "name": "MERGE / Upsert Validation",
                "type": "Data Validation",
                "scenario": "ETL uses MERGE to update existing and insert new records",
                "expected": "MATCHED records updated, UNMATCHED records inserted",
                "action": action_base
            })

        if re.search(r'TRUNCATE\s+TABLE|truncate_table', code, re.I):
            tests.append({
                "name": "TRUNCATE+LOAD Full Refresh Validation",
                "type": "Data Validation",
                "scenario": "ETL truncates old data and loads new data in single transaction",
                "expected": "Table contains only new data, old data completely removed",
                "action": action_base
            })

        has_uuid = (profile.has_uuid_generation if profile else False) or \
            bool(re.search(r'UUID|uuid_string|uuid4|generate_id|newid', code, re.I))
        if has_uuid:
            tests.append({
                "name": "UUID Uniqueness Validation",
                "type": "Data Validation",
                "scenario": "ETL generates unique UUIDs for each record",
                "expected": "No duplicate UUIDs, UUID format valid",
                "action": action_base
            })

        for idx, rule in enumerate(self.business_rules[:5], start=1):
            tests.append({
                "name": f"Business Rule #{idx}: {rule[:50]}",
                "type": "Business Logic",
                "scenario": f"ETL validates business rule: {rule}",
                "expected": "Data conforms to rule, invalid records logged/rejected",
                "action": action_base
            })

        while len(tests) < 5:
            tests.append({
                "name": f"Functional Test {len(tests)-4}",
                "type": "Functional",
                "scenario": "ETL executes with valid configuration",
                "expected": "Exit code 0, data loads successfully",
                "action": action_base
            })

        self.generated_tests = tests[:MAX_POOL]

    def _introspect_etl(self) -> dict:
        """Reuse Phase 0 profile instead of re-running regex detection."""
        code = self.code or ""
        env_key = self.env or "dev"

        # CLI args — still need to scan code for these
        has_env = bool(re.search(r'add_argument.*["\']--env["\']', code))
        has_run_date = bool(re.search(r'add_argument.*["\']--run_date["\']', code))

        valid_envs, invalid_env = [], None
        match = re.search(r"not\s+in\s+\[([^\]]+)\]", code)
        if match:
            valid_envs = re.findall(r"['\"](\w+)['\"]", match.group(1))
            invalid_env = next((e for e in ["staging", "uat", "test", "qa"] if e not in valid_envs), "invalid_env")

        # Source/credential/dedup — read from Phase 0 profile, no re-detection
        profile = self.profile
        source = profile.source_type.value if profile else "unknown"
        cred = profile.credential_type.value if profile else "unknown"
        has_dedup = profile.has_duplicate_detection if profile else bool(
            re.search(r'WHERE\s+NOT\s+EXISTS|MERGE|DISTINCT|duplicate', code, re.IGNORECASE)
        )
        has_date_sub = bool(re.search(r'\{DATE\}|format.*DATE|run_date', code))

        # LLM findings from Phase 0 for richer context
        llm_findings = getattr(profile, 'llm_findings', {}) if profile else {}
        kv_name = ""
        kv_match = re.search(r'["\']key_vault["\']?\s*:\s*["\']([^"\']+)["\']', code)
        if kv_match:
            kv_name = kv_match.group(1)

        table_names = re.findall(r'["\']name["\']?\s*:\s*["\']([^"\']+)["\']', code)
        blob_paths = re.findall(r'["\']blob_path["\']?\s*:\s*["\']([^"\']+)["\']', code)

        return {
            "has_env": has_env, "has_run_date": has_run_date,
            "valid_envs": valid_envs, "invalid_env": invalid_env or "invalid_env",
            "has_dedup": has_dedup, "has_date_sub": has_date_sub,
            "cred": cred, "source": source,
            "env_key": env_key, "kv_name": kv_name,
            "table_names": table_names, "blob_paths": blob_paths,
            "has_config": bool(re.search(r"yaml\.safe_load|config\.yml|config\.yaml", code)),
            "llm_technologies": llm_findings.get("technologies", []),
        }
