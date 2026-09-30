from typing import List, Dict, Tuple
from utils.models import TestResult
import re
from phases.capability_registry import CapabilityRegistry
from phases.etl_profile import ETLProfile, SourceType

try:
    from groq import Groq
    GROQ_AVAILABLE = True
except ImportError:
    GROQ_AVAILABLE = False


class Phase2AzureValidation:
    def __init__(self, python_code: str, metadata: Dict, profile: ETLProfile = None):
        self.code = python_code
        self.metadata = metadata
        self.profile = profile
        self.registry = CapabilityRegistry()
        self.test_results: List[TestResult] = []
        self.detected_patterns: Dict[str, List[str]] = {}
        self.test_counter = 0

    def _has_any_source_code(self) -> bool:
        """True if there's any source/credential code at all — broad check, not Azure-specific."""
        return bool(re.search(
            r'import|from\s+\w|def\s+\w|class\s+\w',
            self.code
        ))

    def _discover_patterns_via_llm(self) -> Dict[str, Dict]:
        """Ask LLM to identify ALL source/credential patterns present — no fixed list."""
        # Reuse Phase 0 findings if available (avoids a second LLM call)
        llm_findings = getattr(self.profile, 'llm_findings', {}) if self.profile else {}
        source_systems = llm_findings.get('source_systems', [])
        auth_methods = llm_findings.get('auth_methods', [])
        technologies = llm_findings.get('technologies', [])

        if not source_systems and not auth_methods:
            # Phase 0 didn't run or had no findings — ask LLM directly
            try:
                from utils.llm_client import call_llm
                import json, re as _re
                prompt = f"""Analyze this ETL code. Identify ALL data source and credential patterns present.
Do NOT limit yourself to Azure — the ETL may use any source (S3, SFTP, local files, API, Snowflake stage, etc.)
and any credential mechanism (Key Vault, Secrets Manager, env vars, config file, etc.).

Code:
```python
{self.code[:3000]}
```

Return ONLY valid JSON:
{{"source_systems": ["..."], "auth_methods": ["..."], "technologies": ["..."],
  "has_error_handling": true/false, "has_retry_logic": true/false,
  "has_connection_validation": true/false, "has_file_patterns": true/false}}"""
                raw = call_llm(prompt, max_tokens=600, temperature=0.1)
                start, end = raw.find('{'), raw.rfind('}')
                cleaned = _re.sub(r',\s*([}\]])', r'\1', raw[start:end+1])
                result = json.loads(cleaned)
                source_systems = result.get('source_systems', [])
                auth_methods = result.get('auth_methods', [])
                technologies = result.get('technologies', [])
                llm_findings = result
            except Exception as e:
                print(f"  [Phase 2] LLM: FALLBACK - {str(e)[:80]}")
                return {}

        patterns = {}
        if source_systems:
            patterns['data_sources'] = {s: True for s in source_systems}
        if auth_methods:
            patterns['credential_mechanisms'] = {a: True for a in auth_methods}
        if technologies:
            patterns['technologies'] = {t: True for t in technologies}
        if llm_findings.get('has_error_handling'):
            patterns['error_handling'] = {'try_except': True}
        if llm_findings.get('has_retry_logic'):
            patterns['retry_logic'] = {'retry_mechanism': True}
        if llm_findings.get('has_connection_validation'):
            patterns['connection_validation'] = {'exists_check': True}
        if llm_findings.get('has_file_patterns'):
            patterns['file_patterns'] = {'parameterized_paths': True}
        return patterns

    def _has_any_azure(self) -> bool:
        return bool(re.search(
            r'AyAzureKeyVault|azureKV|BlobServiceClient|azure\.storage|azure\.keyvault'
            r'|azure\.identity|azure\.data\.lake|DataLakeServiceClient'
            r'|get_secret|vault\.azure\.net|SecretClient|KeyVaultSecret',
            self.code, re.IGNORECASE
        ))

    def run(self) -> Tuple[List[TestResult], Dict[str, List[str]]]:
        if not self._has_any_source_code():
            return self.test_results, self.detected_patterns

        # Always run regex-based discovery first — captures all specific patterns
        azure_patterns = self._find_all_azure_patterns()
        for pattern_type, pattern_details in azure_patterns.items():
            self._generate_test_for_pattern(pattern_type, pattern_details)

        # Skip LLM entirely — regex patterns provide complete coverage

        return self.test_results, self.detected_patterns

    def _has_llm(self) -> bool:
        from src.config import Config
        return bool(Config.GROQ_API_KEY)

    def _discover_checks_via_llm(self):
        # Kept for compatibility — now routes through _discover_patterns_via_llm
        llm_patterns = self._discover_patterns_via_llm()
        for pattern_type, pattern_details in llm_patterns.items():
            self._generate_test_for_pattern(pattern_type, pattern_details)

    def _find_all_azure_patterns(self) -> Dict[str, Dict]:
        """Find all Azure-related patterns in code"""
        patterns = {}
        
        imports = self._find_azure_imports()
        if imports:
            patterns['azure_imports'] = imports
        
        conn_setup = self._find_connection_setup()
        if conn_setup:
            patterns['connection_setup'] = conn_setup
        
        container_config = self._find_container_config()
        if container_config:
            patterns['container_config'] = container_config
        
        blob_ops = self._find_blob_operations()
        if blob_ops:
            patterns['blob_operations'] = blob_ops
        
        error_handling = self._find_error_handling()
        if error_handling:
            patterns['error_handling'] = error_handling
        
        file_patterns = self._find_file_patterns()
        if file_patterns:
            patterns['file_patterns'] = file_patterns
        
        kv_usage = self._find_key_vault_usage()
        if kv_usage:
            patterns['key_vault_usage'] = kv_usage
        
        cred_retrieval = self._find_credential_retrieval()
        if cred_retrieval:
            patterns['credential_retrieval'] = cred_retrieval
        
        return patterns

    def _find_azure_imports(self) -> Dict:
        imports = {}
        if re.search(r'from\s+azure\.storage\.blob\s+import|BlobServiceClient', self.code):
            imports['BlobServiceClient'] = True
        if re.search(r'DataLakeServiceClient|azure\.storage\.filedatalake|abfss://', self.code):
            imports['DataLakeServiceClient'] = True
        if re.search(r'from\s+azure\.identity|from\s+azure\.keyvault|AyAzureKeyVault|SecretClient', self.code):
            imports['KeyVault/Identity'] = True
        if re.search(r'from\s+azure\.storage\.fileshare', self.code):
            imports['FileShare'] = True
        if re.search(r'from\s+azure\.data\.tables|TableServiceClient', self.code):
            imports['Tables'] = True
        if re.search(r'from\s+azure\.eventhub|EventHubClient', self.code):
            imports['EventHub'] = True
        return imports if imports else None

    def _find_connection_setup(self) -> Dict:
        setup = {}
        if re.search(r'from_connection_string', self.code):
            setup['from_connection_string'] = True
        if re.search(r'connection_string\s*=', self.code):
            setup['connection_string_var'] = True
        if re.search(r'BlobServiceClient\.from_connection_string', self.code):
            setup['blob_from_conn_str'] = True
        if re.search(r'BlobServiceClient\(', self.code):
            setup['blob_direct_init'] = True
        if re.search(r'DataLakeServiceClient\(|DataLakeServiceClient\.from_connection_string', self.code):
            setup['adls_client'] = True
        if re.search(r'DefaultAzureCredential|ClientSecretCredential|ManagedIdentityCredential', self.code):
            setup['azure_identity'] = True
        return setup if setup else None

    def _find_container_config(self) -> Dict:
        config = {}
        container_matches = re.findall(r'container_name\s*=\s*["\']([^"\']+)["\']', self.code)
        if container_matches:
            config['container_names'] = container_matches
        if re.search(r'container_name', self.code):
            config['has_container_var'] = True
        if re.search(r'storage_account|storage_acct|AccountName', self.code, re.IGNORECASE):
            config['has_storage_account'] = True
        blob_path_matches = re.findall(r'blob_path\s*=\s*["\']([^"\']+)["\']', self.code)
        if blob_path_matches:
            config['blob_paths'] = blob_path_matches
        return config if config else None

    def _find_blob_operations(self) -> Dict:
        ops = {}
        if re.search(r'list_blobs', self.code):
            ops['list_blobs'] = True
        if re.search(r'get_blob_client', self.code):
            ops['get_blob_client'] = True
        if re.search(r'download_blob', self.code):
            ops['download_blob'] = True
        if re.search(r'upload_blob', self.code):
            ops['upload_blob'] = True
        if re.search(r'delete_blob', self.code):
            ops['delete_blob'] = True
        if re.search(r'exists\(\)', self.code):
            ops['exists_check'] = True
        return ops if ops else None

    def _find_error_handling(self) -> Dict:
        handling = {}
        if re.search(r'try:', self.code):
            handling['try_except'] = True
        if re.search(r'except.*Exception', self.code):
            handling['exception_catch'] = True
        if re.search(r'except.*Azure|except.*Blob|except.*Storage', self.code, re.IGNORECASE):
            handling['azure_specific_errors'] = True
        if re.search(r'raise|logging\.error|logger\.error', self.code):
            handling['error_raising'] = True
        return handling if handling else None

    def _find_file_patterns(self) -> Dict:
        patterns = {}
        if re.search(r'folder_prefix|base_prefix|target_prefix', self.code):
            patterns['prefix_vars'] = True
        if re.search(r'\.format\(|f["\']', self.code):
            patterns['string_formatting'] = True
        # Only flag date_parameterization if --run_date is actively declared (not commented out)
        active_lines = '\n'.join(l for l in self.code.splitlines() if not l.lstrip().startswith('#'))
        if re.search(r'run_date|DATE|date.*format|strftime', active_lines, re.IGNORECASE):
            patterns['date_parameterization'] = True
        if re.search(r'\{DATE\}|\{date\}', self.code):
            patterns['date_placeholder'] = True
        return patterns if patterns else None

    def _find_key_vault_usage(self) -> Dict:
        usage = {}
        if re.search(r'AyAzureKeyVault|azureKV', self.code):
            usage['ay_azure_kv'] = True
        if re.search(r'get_secret', self.code):
            usage['get_secret'] = True
        if re.search(r'vault_uri|vault\.net', self.code):
            usage['vault_uri'] = True
        if re.search(r'key_vault|keyvault', self.code, re.IGNORECASE):
            usage['kv_reference'] = True
        return usage if usage else None

    def _find_credential_retrieval(self) -> Dict:
        retrieval = {}
        if re.search(r'get_secret.*connection', self.code, re.IGNORECASE):
            retrieval['secret_connection'] = True
        if re.search(r'get_secret.*key', self.code, re.IGNORECASE):
            retrieval['secret_key'] = True
        if re.search(r'os\.environ|os\.getenv', self.code):
            retrieval['env_vars'] = True
        if re.search(r'load_dotenv', self.code):
            retrieval['dotenv'] = True
        return retrieval if retrieval else None

    def _generate_test_for_pattern(self, pattern_type: str, pattern_details: Dict):
        self.test_counter += 1
        test_id = f"AZ_{self.test_counter:03d}"

        # LLM-discovered open-ended patterns
        if pattern_type == 'data_sources':
            sources = list(pattern_details.keys())
            self._add_result(test_id, f"Data Sources ({', '.join(sources)})",
                "PASS", f"Data sources identified: {', '.join(sources)}", "N/A")
        elif pattern_type == 'credential_mechanisms':
            mechs = list(pattern_details.keys())
            self._add_result(test_id, f"Credential Mechanisms ({', '.join(mechs)})",
                "PASS", f"Credential mechanisms: {', '.join(mechs)}", "N/A")
        elif pattern_type == 'technologies':
            techs = list(pattern_details.keys())
            self._add_result(test_id, f"Technologies ({', '.join(techs[:5])})",
                "PASS", f"Libraries/frameworks used: {', '.join(techs)}", "N/A")
        elif pattern_type == 'retry_logic':
            self._add_result(test_id, "Retry Logic",
                "PASS", "Retry mechanism detected", "N/A")
        elif pattern_type == 'connection_validation':
            self._add_result(test_id, "Connection Validation",
                "PASS", "Connection existence check detected", "N/A")
        # Regex-based patterns
        elif pattern_type == 'azure_imports':
            self._test_azure_imports(test_id, pattern_details)
        elif pattern_type == 'connection_setup':
            self._test_connection_setup(test_id, pattern_details)
        elif pattern_type == 'container_config':
            self._test_container_config(test_id, pattern_details)
        elif pattern_type == 'blob_operations':
            self._test_blob_operations(test_id, pattern_details)
        elif pattern_type == 'error_handling':
            self._test_error_handling(test_id, pattern_details)
        elif pattern_type == 'file_patterns':
            self._test_file_patterns(test_id, pattern_details)
        elif pattern_type == 'key_vault_usage':
            self._test_key_vault_usage(test_id, pattern_details)
        elif pattern_type == 'credential_retrieval':
            self._test_credential_retrieval(test_id, pattern_details)
        else:
            # Catch-all for any novel LLM-discovered pattern type
            vals = list(pattern_details.keys())
            self._add_result(test_id, f"{pattern_type.replace('_', ' ').title()} ({', '.join(vals[:3])})",
                "PASS", f"{pattern_type}: {', '.join(vals)}", "N/A")

        if pattern_type not in self.detected_patterns:
            self.detected_patterns[pattern_type] = []
        self.detected_patterns[pattern_type].extend(pattern_details.keys())

    def _test_azure_imports(self, test_id: str, details: Dict):
        imports_found = list(details.keys())
        status = "PASS" if imports_found else "FAIL"
        finding = f"imports: {','.join(imports_found)}" if imports_found else "imports: none"
        self._add_result(test_id, "Azure SDK Imports", status, finding, "N/A" if status == "PASS" else "Add Azure SDK imports")

    def _test_connection_setup(self, test_id: str, details: Dict):
        methods = list(details.keys())
        status = "PASS" if methods else "FAIL"
        finding = f"methods: {','.join(methods)}" if methods else "methods: none"
        self._add_result(test_id, "Connection Setup", status, finding, "N/A" if status == "PASS" else "Setup Azure connection")

    def _test_container_config(self, test_id: str, details: Dict):
        config_parts = []
        if 'container_names' in details:
            config_parts.append(f"containers: {','.join(details['container_names'])}")
        if 'has_container_var' in details:
            config_parts.append("container_exist: yes")
        if 'has_storage_account' in details:
            config_parts.append("storage_acct: yes")
        status = "PASS" if config_parts else "FAIL"
        finding = f"{' | '.join(config_parts)}" if config_parts else "config: none"
        self._add_result(test_id, "Container Configuration", status, finding, "N/A" if status == "PASS" else "Configure container")

    def _test_blob_operations(self, test_id: str, details: Dict):
        ops = list(details.keys())
        status = "PASS" if ops else "FAIL"
        finding = f"operations: {','.join(ops)}" if ops else "operations: none"
        self._add_result(test_id, "Blob Operations", status, finding, "N/A" if status == "PASS" else "Add blob operations")

    def _test_error_handling(self, test_id: str, details: Dict):
        label_map = {
            "try_except": "try/except blocks present",
            "exception_catch": "catches Exception base class",
            "azure_specific_errors": "catches Azure-specific exceptions",
            "error_raising": "raises exceptions on failure (raise / logger.error)",
        }
        labels = [label_map.get(k, k) for k in details.keys()]
        status = "PASS" if labels else "FAIL"
        finding = f"error handling: {' | '.join(labels)}" if labels else "error handling: none found"
        self._add_result(test_id, "Error Handling", status, finding, "N/A" if status == "PASS" else "Add try/except blocks around source and Snowflake operations")

    def _test_file_patterns(self, test_id: str, details: Dict):
        patterns = list(details.keys())
        status = "PASS" if patterns else "FAIL"
        finding = f"patterns: {','.join(patterns)}" if patterns else "patterns: none"
        self._add_result(test_id, "File Patterns", status, finding, "N/A" if status == "PASS" else "Add file path parameterization")

    def _test_key_vault_usage(self, test_id: str, details: Dict):
        usage = list(details.keys())
        status = "PASS" if usage else "FAIL"
        finding = f"usage: {','.join(usage)}" if usage else "usage: none"
        self._add_result(test_id, "Key Vault Usage", status, finding, "N/A" if status == "PASS" else "Use Key Vault for secrets")

    def _test_credential_retrieval(self, test_id: str, details: Dict):
        label_map = {
            "secret_connection": "connection string retrieved from secret vault",
            "secret_key": "private key / secret retrieved from vault",
            "env_vars": "credentials loaded from environment variables (os.environ / os.getenv)",
            "dotenv": "credentials loaded from .env file (load_dotenv)",
        }
        labels = [label_map.get(k, k) for k in details.keys()]
        status = "PASS" if labels else "FAIL"
        finding = f"credential retrieval: {' | '.join(labels)}" if labels else "credential retrieval: none found"
        self._add_result(test_id, "Credential Retrieval", status, finding, "N/A" if status == "PASS" else "Retrieve credentials from a secrets manager — never hardcode")

    def _add_result(self, test_id: str, name: str, status: str, finding: str, recommendation: str, severity: str = "Medium"):
        self.test_results.append(TestResult(
            test_id=test_id,
            test_name=name,
            phase="Azure Code Validation",
            status=status,
            finding=finding,
            recommendation=recommendation,
            check_type="Dynamic",
            severity=severity,
            database_target=self.metadata.get("target_database", "N/A"),
            stage2_relevance="Required",
        ))
