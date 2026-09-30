import sys
import subprocess
import time
import logging
import os
logging.getLogger('azure').setLevel(logging.ERROR)
logging.getLogger('snowflake').setLevel(logging.ERROR)
logging.getLogger('urllib3').setLevel(logging.ERROR)
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).parent / ".env", override=True)
sys.path.append(str(Path(__file__).parent.absolute()))

from utils.debug_logger import debug_logger
from phases.phase0_profile_analyzer import Phase0ProfileAnalyzer
from phases.phase1_metadata import Phase1MetadataExtraction
from phases.phase1_5_context_mapper import Phase1_5PatternContextMapper
from phases.phase2_azure_validation import Phase2AzureValidation
from phases.phase3_snowflake_validation import Phase3SnowflakeValidation
from phases.phase4_resources import Phase4ResourceValidation
from phases.phase5_5_test_pool_calculator import Phase5_5DynamicTestPoolCalculator
from phases.phase7_pre_batch_validation import Phase7PreBatchValidation
from phases.phase8_post_batch_validation import Phase8PostBatchValidation
from phases.phase5_test_generation import TestGenerationAgent
from phases.phase5b_test_executor import TestExecutorAgent
from phases.phase5c_execution_summary import ExecutionSummaryAgent
from phases.phase6_result_analysis import ResultAnalysisAgent
from phases.phase9_failure_correlator import Phase9FailurePatternCorrelator
from utils.report_generator import ReportGenerator
from utils.models import CodeAnalysis, Metadata, TestResult, ETLContext


def _build_etl_context(python_code: str, metadata, phase2_patterns: dict, phase3_patterns: dict,
                       etl_file_path: str, env: str) -> "ETLContext":
    """
    Build the shared ETLContext from already-discovered Phase 1-3 data.
    Never re-reads the ETL file — uses what the phases already found.
    """
    import re, yaml
    from pathlib import Path as _P

    ctx = ETLContext(etl_file=etl_file_path, env_key=env)

    # ── From Phase 1 metadata ──────────────────────────────────────────────────────────────
    m = metadata.__dict__ if hasattr(metadata, '__dict__') else metadata
    ctx.target_database  = m.get('target_database')
    ctx.target_schema    = m.get('target_schema')
    ctx.target_table     = m.get('target_table')
    ctx.audit_table      = m.get('audit_table')
    ctx.source_identifier = m.get('azure_container')

    # CLI args
    active = "\n".join(l for l in python_code.splitlines() if not l.lstrip().startswith('#'))
    ctx.has_env_arg      = bool(re.search(r'add_argument.*["\']--env["\']', active))
    ctx.has_run_date_arg = bool(re.search(r'add_argument.*["\']--run_date["\']', active))
    valid_match = re.findall(r"not\s+in\s+\[([^\]]+)\]", active)
    ctx.valid_envs = re.findall(r"['\"](\w+)['\"]", valid_match[0]) if valid_match else []

    # Credential mechanism
    cred_checks = [
        ("AzureKeyVault",    r"AyAzureKeyVault|azureKV\.get_secret|AzureKeyVault"),
        ("AzureSecretClient",r"SecretClient|azure\.keyvault"),
        ("AWSSecretsManager",r"boto3.*secret|secretsmanager"),
        ("HashiCorpVault",   r"hvac|vault\.read"),
        ("GCPSecretManager", r"google\.cloud\.secretmanager|SecretManagerServiceClient"),
        ("EnvVars",          r"os\.environ|os\.getenv|load_dotenv"),
        ("ConfigFile",       r"yaml\.safe_load"),
    ]
    for name, pat in cred_checks:
        if re.search(pat, python_code, re.IGNORECASE):
            ctx.credential_mechanism = name
            break

    # Source type
    src_checks = [
        ("AzureBlob",       r"BlobServiceClient|blob_path|azure\.storage\.blob"),
        ("ADLS",            r"DataLakeServiceClient|adls|abfss://"),
        ("S3",              r"boto3.*s3|s3\.get_object|s3://"),
        ("GCS",             r"google\.cloud\.storage|gs://"),
        ("SFTP",            r"paramiko|SFTPClient"),
        ("LocalFiles",      r"os\.listdir|isfile.*\.csv|open.*\.csv"),
        ("API",             r"requests\.(get|post)"),
        ("SnowflakeStage",  r"snowflake_stage|COPY\s+INTO"),
    ]
    for name, pat in src_checks:
        if re.search(pat, python_code, re.IGNORECASE):
            ctx.source_type = name
            break

    # Table names + blob paths from config
    try:
        for base in [_P(etl_file_path).parent, _P(etl_file_path).parent.parent]:
            for fname in ["config.yaml", "config.yml"]:
                p = base / fname
                if p.exists():
                    cfg = yaml.safe_load(p.read_text(encoding="utf-8", errors="ignore")) or {}
                    env_block = cfg.get(env) if env and env in cfg else next((v for v in cfg.values() if isinstance(v, dict)), None)
                    sf = (env_block or {}).get('snowflake', [{}])
                    sf_block = sf[0] if isinstance(sf, list) and sf else (sf if isinstance(sf, dict) else {})
                    ctx.kv_name = (env_block or {}).get('key_vault', '')
                    for t in sf_block.get('tables', []):
                        if isinstance(t, dict):
                            if t.get('name'): ctx.table_names.append(t['name'])
                            if t.get('blob_path'): ctx.blob_paths.append(t['blob_path'])
                    if not ctx.table_names:
                        for t in sf_block.get('tables_to_copy', []):
                            n = (t.get('filename') or t.get('name')) if isinstance(t, dict) else str(t)
                            if n: ctx.table_names.append(n)
                    raise StopIteration  # found config, stop searching
    except StopIteration:
        pass
    except Exception:
        pass

    # ── From Phase 2 patterns ──────────────────────────────────────────────────────────────
    if 'connection_setup' in phase2_patterns:
        cs = phase2_patterns['connection_setup']
        if 'azure_identity' in cs or 'DefaultAzureCredential' in str(cs):
            ctx.azure_auth_method = 'managed_identity'
        elif 'blob_from_conn_str' in cs or 'from_connection_string' in cs:
            ctx.azure_auth_method = 'connection_string'
        elif 'ay_azure_kv' in phase2_patterns.get('key_vault_usage', {}):
            ctx.azure_auth_method = 'key_vault'
    if 'azure_imports' in phase2_patterns:
        imp = phase2_patterns['azure_imports']
        if 'BlobServiceClient' in imp:      ctx.azure_source = 'blob_storage'
        elif 'DataLakeServiceClient' in imp: ctx.azure_source = 'data_lake'
    ctx.azure_retry_logic          = 'retry_logic' in phase2_patterns or bool(re.search(r'max_retries|backoff|time\.sleep', python_code))
    ctx.azure_error_handling       = 'error_handling' in phase2_patterns
    ctx.azure_connection_validation = bool(re.search(r'\.exists\(\)', python_code))
    ctx.azure_duplicate_detection  = bool(re.search(r'duplicate|already.*processed|processed.*cache', python_code, re.I))

    # ── From Phase 3 patterns ──────────────────────────────────────────────────────────────
    lm = phase3_patterns.get('load_methods', {})
    if 'COPY_INTO'   in lm: ctx.sf_load_strategy = 'copy_into'
    elif 'MERGE_INTO' in lm: ctx.sf_load_strategy = 'merge'
    elif 'INSERT_INTO' in lm: ctx.sf_load_strategy = 'insert'
    cs3 = phase3_patterns.get('connection_setup', {})
    if 'private_key_auth' in cs3:  ctx.sf_connection_type = 'private_key'
    elif 'ay_loader' in cs3:       ctx.sf_connection_type = 'loader_utility'
    elif 'direct_connect' in cs3:  ctx.sf_connection_type = 'password'
    txn = phase3_patterns.get('transaction_management', {})
    ctx.sf_transaction_management = bool(txn)
    ctx.sf_rollback_on_failure    = 'ROLLBACK' in txn
    ctx.sf_batch_operations       = bool(re.search(r'executemany|batch.*insert', python_code, re.I))
    ctx.sf_error_handling         = bool(phase3_patterns.get('transaction_management')) or bool(re.search(r'try:', python_code))
    ctx.sf_audit_logging          = bool(phase3_patterns.get('audit_logging'))
    ctx.sf_structured_logging     = bool(re.search(r'json\.dumps|JSONFormatter|logger\.info\(\{', python_code))

    return ctx


def _detect_etl_cli_args(etl_file_path: str, env: str, run_date: str) -> list:
    """
    Reads the ETL source and builds a CLI arg list using only the args
    the ETL actually declares via argparse. Generic — works for any ETL.
    """
    import re
    try:
        code = Path(etl_file_path).read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return []

    args = []
    value_candidates = {
        "--env":            env,
        "--run_date":       run_date,
        "--run-date":       run_date,
        "--date":           run_date,
        "--execution_date": run_date,
        "--start_date":     run_date,
        "--period":         run_date,
    }
    for arg_name, value in value_candidates.items():
        if value is None:
            continue
        pattern = rf"^(?!\s*#).*add_argument\s*\(\s*['\"]({re.escape(arg_name)})['\"]" 
        if re.search(pattern, code, re.MULTILINE):
            args.extend([arg_name, value])

    return args


def run_validated_etl(etl_file_path: str, validate_resources: bool = False,
                      run_date: str = None, env: str = "dev", debug: bool = False,
                      business_rules: list = None):
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    debug_logger.setup(debug_enabled=debug, timestamp=timestamp)
    
    if debug:
        print(f"[DEBUG MODE ENABLED] Logs will be written to: {debug_logger.get_log_file()}")
    
    print("=" * 80)
    print("ETL VALIDATION & EXECUTION PIPELINE")
    print("=" * 80)
    
    all_test_results = []
    
    with open(etl_file_path, 'r', encoding='utf-8') as f:
        python_code = f.read()
    
    print("\n[Phase 0] Profile Analyzer...")
    phase0 = Phase0ProfileAnalyzer(python_code, etl_file_path, env=env)
    profile = phase0.run()
    print(f"  ETL Profile: {profile.complexity_level.value} | "
          f"Source: {profile.source_type.value} | Target: {profile.target_type.value}")
    print(f"  Capabilities: {len(profile.discovered_capabilities)} | "
          f"Gaps: {len(profile.get_capability_gaps())}")
    print(f"  Test Count: {profile.get_test_count()}")
    
    debug_logger.log_phase_start("Phase1", "Metadata Extraction")
    print("\n[Phase 1] Metadata Extraction...")
    phase1 = Phase1MetadataExtraction(python_code, etl_file_path, env=env)
    metadata, phase1_results = phase1.run()
    all_test_results.extend(phase1_results)
    debug_logger.log_phase_end("Phase1", len(phase1_results), 
                               sum(1 for r in phase1_results if r.status == "PASS"),
                               sum(1 for r in phase1_results if r.status == "FAIL"))
    print(f"  [DONE] Phase 1: {len(phase1_results)} tests")
    
    debug_logger.log_phase_start("Phase1.5", "Pattern Context Mapping")
    print("\n[Phase 1.5] Pattern Context Mapper...")
    phase1_5 = Phase1_5PatternContextMapper(python_code, profile)
    pattern_context, operation_graph = phase1_5.run()
    print(f"  Operations: {pattern_context.get('operation_coverage', {}).get('total_operations', 0)}")
    print(f"  Error Handlers: {len(pattern_context.get('error_handling', {}).get('handlers', []))}")
    print(f"  Transactions: {len(pattern_context.get('transaction_management', {}).get('patterns', []))}")
    
    debug_logger.log_phase_start("Phase2", "Azure Validation")
    print("\n[Phase 2] Azure Validation...")
    phase2 = Phase2AzureValidation(python_code, metadata.__dict__, profile)
    phase2_results, phase2_patterns = phase2.run()
    all_test_results.extend(phase2_results)
    debug_logger.log_phase_end("Phase2", len(phase2_results),
                               sum(1 for r in phase2_results if r.status == "PASS"),
                               sum(1 for r in phase2_results if r.status == "FAIL"))
    print(f"  [DONE] Phase 2: {len(phase2_results)} tests")
    
    debug_logger.log_phase_start("Phase3", "Snowflake Validation")
    print("\n[Phase 3] Snowflake Validation...")
    phase3 = Phase3SnowflakeValidation(python_code, metadata.__dict__, profile)
    phase3_results, phase3_patterns = phase3.run()
    all_test_results.extend(phase3_results)
    debug_logger.log_phase_end("Phase3", len(phase3_results),
                               sum(1 for r in phase3_results if r.status == "PASS"),
                               sum(1 for r in phase3_results if r.status == "FAIL"))
    print(f"  [DONE] Phase 3: {len(phase3_results)} tests")
    
    combined_patterns = {**phase2_patterns, **phase3_patterns}
    
    debug_logger.log_phase_start("Phase4", "Resource Validation")
    print("\n[Phase 4] Resource Validation...")
    phase4 = Phase4ResourceValidation(metadata.__dict__, validate_resources, etl_file_path, env=env)
    phase4_results = phase4.run()
    all_test_results.extend(phase4_results)
    debug_logger.log_phase_end("Phase4", len(phase4_results),
                               sum(1 for r in phase4_results if r.status == "PASS"),
                               sum(1 for r in phase4_results if r.status == "FAIL"))
    print(f"  [DONE] Phase 4: {len(phase4_results)} tests")
    
    debug_logger.log_phase_start("Phase5.5", "Dynamic Test Pool Calculator")
    print("\n[Phase 5.5] Dynamic Test Pool Calculator...")
    phase5_5 = Phase5_5DynamicTestPoolCalculator(profile, pattern_context)
    test_pool, test_weights = phase5_5.run()
    print(f"  Test Pool: {len(test_pool)} tests | Max Priority: {max(test_weights.values()) if test_weights else 0}")
    
    debug_logger.log_phase_start("Phase7", "Pre-Batch Validation")
    print("\n[Phase 7] Pre-Batch Validation...")
    phase7 = Phase7PreBatchValidation(metadata.__dict__, etl_file_path, validate_resources, run_date=run_date, env=env)
    pre_batch_metrics, phase7_results = phase7.run()
    all_test_results.extend(phase7_results)
    debug_logger.log_phase_end("Phase7", len(phase7_results),
                               sum(1 for r in phase7_results if r.status == "PASS"),
                               sum(1 for r in phase7_results if r.status == "FAIL"))
    print(f"  [DONE] Phase 7: {len(phase7_results)} tests")
    if validate_resources:
        print(f"    Tables: {pre_batch_metrics.get('config_table_count', 0)} | "
              f"Pre-batch: {pre_batch_metrics.get('pre_batch_total_records', 0)}")
    
    print("\n" + "=" * 80)
    print("EXECUTING ETL: " + Path(etl_file_path).name)
    print("=" * 80)
    
    etl_start = time.time()
    etl_execution_result = {"exit_code": -1, "duration_ms": 0, "stdout": "", "stderr": ""}
    
    try:
        detected_args = _detect_etl_cli_args(etl_file_path, env, run_date)
        
        cmd = [sys.executable, etl_file_path] + detected_args
        print(f"Command: {' '.join(cmd)}")
        
        # Build subprocess environment with all current variables
        subprocess_env = dict(os.environ)
        # Explicitly ensure Azure credentials are passed
        for key in ['AZURE_TENANT_ID', 'AZURE_CLIENT_ID', 'AZURE_CLIENT_SECRET']:
            if key in os.environ:
                subprocess_env[key] = os.environ[key]
        
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600,
            cwd=str(Path(etl_file_path).parent),
            env=subprocess_env
        )
        
        etl_duration = int((time.time() - etl_start) * 1000)
        etl_execution_result["exit_code"] = result.returncode
        etl_execution_result["duration_ms"] = etl_duration
        etl_execution_result["stdout"] = result.stdout
        etl_execution_result["stderr"] = result.stderr
        
        if result.returncode == 0:
            print(f"\n[PASS] ETL COMPLETED in {etl_duration}ms")
        else:
            print(f"\n[FAIL] ETL FAILED with exit code {result.returncode}")
            print(f"Error:\n{result.stderr}")
        
        if result.stdout:
            print(f"\nOutput:\n{result.stdout}")
        
    except subprocess.TimeoutExpired:
        etl_duration = int((time.time() - etl_start) * 1000)
        etl_execution_result["exit_code"] = -999
        etl_execution_result["duration_ms"] = etl_duration
        etl_execution_result["stderr"] = "ETL execution timed out after 600 seconds"
        print(f"\n[FAIL] ETL TIMEOUT after {etl_duration}ms")
    
    except Exception as e:
        etl_duration = int((time.time() - etl_start) * 1000)
        etl_execution_result["exit_code"] = -998
        etl_execution_result["duration_ms"] = etl_duration
        etl_execution_result["stderr"] = str(e)
        print(f"\n[FAIL] ETL ERROR: {e}")
    
    debug_logger.log_phase_start("Phase8", "Post-Batch Validation")
    print("\n[Phase 8] Post-Batch Validation...")

    # --- DATA SNAPSHOT: capture record counts immediately after ETL, before Phase 5B
    # can re-run the ETL (which truncates tables) and wipe the loaded data.
    if validate_resources and etl_execution_result.get("exit_code") == 0:
        try:
            import yaml as _yaml
            from utils.snowflake_connector import get_snowflake_cursor
            _cfg_path = None
            for _base in [Path(etl_file_path).parent, Path(etl_file_path).parent.parent]:
                for _fname in ["config.yaml", "config.yml"]:
                    _p = _base / _fname
                    if _p.exists():
                        _cfg_path = _p
                        break
                if _cfg_path:
                    break
            if _cfg_path:
                with open(_cfg_path) as _f:
                    _cfg_all = _yaml.safe_load(_f)
                _env_cfg = _cfg_all.get(env) or next(iter(_cfg_all.values()), {})
                _cursor = get_snowflake_cursor(_env_cfg)
                if _cursor:
                    _sf_block = next(
                        (v[0] if isinstance(v, list) else v
                         for k, v in _env_cfg.items() if "snowflake" in k.lower()),
                        {}
                    )
                    _db     = (_sf_block.get("database") or "").upper()
                    _schema = (_sf_block.get("schema")   or "").upper()
                    # Collect all target tables: config tables list → INSERT INTO → CALL inference
                    _table_names = []
                    for _t in (_sf_block.get("tables") or []):
                        if isinstance(_t, dict) and _t.get("name"):
                            _table_names.append(_t["name"])
                    if not _table_names:
                        import re as _re
                        _code = Path(etl_file_path).read_text(encoding="utf-8", errors="ignore")
                        _table_names = list(dict.fromkeys(_re.findall(
                            r'INSERT\s+INTO\s+(?:\w+\.\w+\.)?([A-Za-z0-9_]+)', _code, _re.IGNORECASE
                        )))
                    if not _table_names:
                        import re as _re
                        _code = Path(etl_file_path).read_text(encoding="utf-8", errors="ignore")
                        _call_procs = _re.findall(r"CALL\s+([A-Za-z0-9_]+)\s*\(", _code, _re.IGNORECASE)
                        for _proc in _call_procs:
                            _m = _re.match(r'LOAD_([A-Za-z0-9_]+?)(?:_CSV|_SQL)$', _proc, _re.IGNORECASE)
                            if _m:
                                _tname = _m.group(1).upper()
                                if _tname not in _table_names:
                                    _table_names.append(_tname)

                    def _do_snapshot(_cur, _db, _schema, _table_names):
                        _snap_total, _snap_per = 0, {}
                        for _tn in _table_names:
                            try:
                                _cur.execute(f"SELECT COUNT(*) FROM {_db}.{_schema}.{_tn}")
                                _cnt = (_cur.fetchone() or [0])[0]
                                _snap_per[_tn] = _cnt
                                _snap_total += _cnt
                            except Exception:
                                _snap_per[_tn] = 0
                        return _snap_total, _snap_per

                    _snap_total, _snap_per = _do_snapshot(_cursor, _db, _schema, _table_names)

                    # Retry once after a short delay if all counts are 0
                    # (stored procedures may have a brief commit lag)
                    if _snap_total == 0 and _table_names:
                        print(f"  [Snapshot] All counts 0 — retrying in 3s...")
                        time.sleep(3)
                        _snap_total, _snap_per = _do_snapshot(_cursor, _db, _schema, _table_names)

                    pre_batch_metrics["post_etl_snapshot_records"]   = _snap_total
                    pre_batch_metrics["post_etl_snapshot_per_table"] = _snap_per
                    pre_batch_metrics["post_batch_total_records"]    = _snap_total
                    pre_batch_metrics["post_batch_table_records"]    = _snap_per
                    pre_batch_metrics["records_loaded"] = (
                        _snap_total - pre_batch_metrics.get("pre_batch_total_records", 0)
                    )
                    print(f"  [Snapshot] Post-ETL record counts: {_snap_per} | Total: {_snap_total}")
        except Exception as _snap_err:
            print(f"  [Snapshot] Could not capture post-ETL counts: {_snap_err}")
    # --- END SNAPSHOT ---

    phase8 = Phase8PostBatchValidation(
        metadata.__dict__, etl_file_path, validate_resources,
        pre_batch_metrics, etl_execution_result, env=env, business_rules=business_rules,
        pattern_context=pattern_context
    )
    post_batch_metrics, phase8_results = phase8.run()
    all_test_results.extend(phase8_results)
    debug_logger.log_phase_end("Phase8", len(phase8_results),
                               sum(1 for r in phase8_results if r.status == "PASS"),
                               sum(1 for r in phase8_results if r.status == "FAIL"))
    print(f"  [DONE] Phase 8: {len(phase8_results)} tests")
    if validate_resources:
        print(f"    Records: {post_batch_metrics.get('post_batch_total_records', 0)} | "
              f"Loaded: {post_batch_metrics.get('records_loaded', 0)}")

    # ── Build shared ETL context from Phase 1-3 results ──────────────────────
    context = _build_etl_context(python_code, metadata, phase2_patterns, phase3_patterns, etl_file_path, env)
    print(f"\n[Context] ETL context built: source={context.source_type}, db={context.target_database}.{context.target_schema}, tables={context.table_names}")

    debug_logger.log_phase_start("Phase5A", "Test Generation")
    print("\n[Phase 5A] Test Generation...")
    phase5 = TestGenerationAgent(python_code, metadata.__dict__, etl_file_path, env=env, profile=profile, test_pool=test_pool, context=context, business_rules=business_rules)
    generated_tests, phase5a_results = phase5.run()
    all_test_results.extend(phase5a_results)
    debug_logger.log_phase_end("Phase5A", len(phase5a_results),
                               sum(1 for r in phase5a_results if r.status == "PASS"),
                               sum(1 for r in phase5a_results if r.status == "FAIL"))
    print(f"  [DONE] Phase 5A: {len(phase5a_results)} tests")

    debug_logger.log_phase_start("Phase5B", "Test Cases Execution")
    print("\n[Phase 5B] Test Cases Execution...")
    phase5b = TestExecutorAgent(generated_tests, etl_file_path, metadata.__dict__)
    phase5b_results = phase5b.run()
    # Phase 5B results are passed to 5C — do NOT add to all_test_results here to avoid duplicates
    debug_logger.log_phase_end("Phase5B", len(phase5b_results),
                               sum(1 for r in phase5b_results if r.status == "PASS"),
                               sum(1 for r in phase5b_results if r.status == "FAIL"))
    print(f"  [DONE] Phase 5B: {len(phase5b_results)} tests")

    debug_logger.log_phase_start("Phase5C", "Execution Summary")
    print("\n[Phase 5C] Execution Summary...")
    phase5c = ExecutionSummaryAgent(phase5b_results, metadata.__dict__)
    phase5c_results = phase5c.run()
    phase5c_summary = phase5c.get_summary_dict()
    all_test_results.extend(phase5c_results)
    debug_logger.log_phase_end("Phase5C", len(phase5c_results),
                               sum(1 for r in phase5c_results if r.status == "PASS"),
                               sum(1 for r in phase5c_results if r.status == "FAIL"))
    print(phase5c.get_console_summary())
    
    debug_logger.log_phase_start("Phase6", "Result Analysis")
    print("\n[Phase 6] Result Analysis...")
    phase6 = ResultAnalysisAgent(python_code, metadata.__dict__, all_test_results, phase5c_summary, context=context, business_rules=business_rules)
    phase6_results = phase6.run()
    all_test_results.extend(phase6_results)
    debug_logger.log_phase_end("Phase6", len(phase6_results),
                               sum(1 for r in phase6_results if r.status == "PASS"),
                               sum(1 for r in phase6_results if r.status == "FAIL"))
    print(f"  [DONE] Phase 6: {len(phase6_results)} tests")
    
    debug_logger.log_phase_start("Phase9", "Failure Pattern Correlation")
    print("\n[Phase 9] Failure Pattern Correlator...")
    phase9 = Phase9FailurePatternCorrelator(all_test_results, profile)
    failure_analysis = phase9.run()
    print(f"  Total Failures: {failure_analysis.get('total_failures', 0)}")
    print(f"  Systemic Issues: {len(failure_analysis.get('systemic_issues', []))}")
    print(f"  Risk Level: {failure_analysis.get('risk_level', 'UNKNOWN')}")
    if failure_analysis.get('root_cause_clusters'):
        for cluster in failure_analysis['root_cause_clusters'][:3]:
            print(f"    [{cluster.get('confidence','?')}] {cluster.get('cluster','')}: {cluster.get('root_cause','')}")
    elif failure_analysis.get('recommendations'):
        for rec in failure_analysis['recommendations'][:3]:
            print(f"    - {rec}")
    
    print("\n" + "=" * 80)
    print("GENERATING VALIDATION REPORT")
    print("=" * 80)
    
    output_dir = Path(__file__).parent / "outputs"
    output_dir.mkdir(exist_ok=True)

    analysis = CodeAnalysis(
        metadata=metadata,
        test_results=all_test_results,
        file_path=etl_file_path,
        phase5c_summary=phase5c_summary,
    )
    generator = ReportGenerator(analysis)
    csv_file = generator.generate_csv(f"etl_validation_report_{timestamp}.csv")
    json_file = generator.generate_json(f"etl_analysis_{timestamp}.json")
    excel_file = generator.generate_excel(f"etl_validation_report_{timestamp}.xlsx")
    
    print(f"\n[CSV] {csv_file}")
    print(f"[JSON] {json_file}")
    if excel_file:
        print(f"[EXCEL] {excel_file}")
    if debug and debug_logger.get_log_file():
        print(f"[DEBUG LOG] {debug_logger.get_log_file()}")
    
    total_tests = len(all_test_results)
    passed = sum(1 for r in all_test_results if r.status == "PASS")
    failed = sum(1 for r in all_test_results if r.status == "FAIL")
    skipped = sum(1 for r in all_test_results if r.status == "SKIPPED")
    
    print("\n" + "=" * 80)
    print("VALIDATION SUMMARY")
    print("=" * 80)
    print(f"Total Tests: {total_tests}")
    print(f"  Passed : {passed}")
    print(f"  Failed : {failed}")
    print(f"  Skipped: {skipped}")
    if (total_tests - skipped) > 0:
        print(f"Pass Rate: {(passed / (total_tests - skipped) * 100):.1f}%")
    
    print(f"\nETL Profile Risk: {failure_analysis.get('risk_level', 'UNKNOWN')}")
    
    if validate_resources and pre_batch_metrics and post_batch_metrics:
        print("\n" + "=" * 80)
        print("BATCH EXECUTION METRICS")
        print("=" * 80)
        print(f"Tables: {pre_batch_metrics.get('config_table_count', 0)}")
        print(f"Pre-Batch: {pre_batch_metrics.get('pre_batch_total_records', 0)}")
        print(f"Post-Batch: {post_batch_metrics.get('post_batch_total_records', 0)}")
        print(f"Loaded: {post_batch_metrics.get('records_loaded', 0)}")
        print(f"Duration: {etl_execution_result.get('duration_ms', 0)}ms")
    
    print("\n" + "=" * 80)
    
    return all_test_results, csv_file, json_file


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Run ETL with validation and generate report")
    parser.add_argument("etl_file", type=str, help="Path to ETL Python file (etl_load.py)")
    parser.add_argument("--validate-resources", action="store_true", 
                        help="Enable resource validation (requires Azure/Snowflake credentials)")
    parser.add_argument("--run-date", type=str, help="Run date for ETL (YYYYMMDD format)")
    parser.add_argument("--env", type=str, default="dev", help="Environment (dev/prd)")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    
    args = parser.parse_args()
    
    # Resolve ETL path relative to wrapper's directory if not absolute
    etl_path = Path(args.etl_file)
    if not etl_path.is_absolute():
        etl_path = Path(__file__).parent / etl_path
    etl_path = etl_path.resolve()
    
    if not etl_path.exists():
        print(f"Error: ETL file not found: {etl_path}")
        sys.exit(1)
    
    run_validated_etl(
        str(etl_path),
        validate_resources=args.validate_resources,
        run_date=args.run_date,
        env=args.env,
        debug=args.debug,
        business_rules=None,
    )
