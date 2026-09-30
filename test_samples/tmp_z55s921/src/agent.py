import sys
from pathlib import Path
from dotenv import load_dotenv

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

env_file = project_root / ".env"
load_dotenv(dotenv_path=env_file, override=True)

from utils.models import CodeAnalysis, Metadata
from phases.phase1_metadata import Phase1MetadataExtraction
from phases.phase2_capability_discovery import Phase2CapabilityDiscovery
from phases.phase3_capability_discovery import Phase3CapabilityDiscovery
from phases.phase4_resources import Phase4ResourceValidation
from phases.phase5_test_generation import TestGenerationAgent
from phases.dynamic_test_generator import DynamicTestGenerator
from phases.phase5b_test_executor import TestExecutorAgent
from phases.phase5c_execution_summary import ExecutionSummaryAgent
from phases.phase6_result_analysis import ResultAnalysisAgent
from utils.report_generator import ReportGenerator
from src.config import Config


class ETLTestCaseAgent:
    def __init__(self, python_file_path: str, validate_resources: bool = False):
        self.file_path = python_file_path
        self.validate_resources = validate_resources
        self.code = None
        self.analysis = None
        self.azure_model = None
        self.snowflake_model = None
        self._phase5a_tests = []
        self._phase5c_summary = {}
        self._env = self._detect_env()

    def _detect_env(self) -> str:
        """Detect the runtime env key from the ETL's config file — picks the first env block key."""
        try:
            import yaml
            for base in [Path(self.file_path).parent, Path(self.file_path).parent.parent]:
                for fname in ["config.yaml", "config.yml"]:
                    p = base / fname
                    if p.exists():
                        cfg = yaml.safe_load(p.read_text(encoding="utf-8", errors="ignore")) or {}
                        # Return first key whose value is a dict (env block)
                        for k, v in cfg.items():
                            if isinstance(v, dict):
                                return k
        except Exception:
            pass
        return None

    def read_python_file(self) -> str:
        try:
            with open(self.file_path, "r", encoding="utf-8") as f:
                self.code = f.read()
            return self.code
        except FileNotFoundError:
            raise FileNotFoundError(f"Python file not found: {self.file_path}")
        except Exception as e:
            raise Exception(f"Error reading file: {e}")

    # ─── Phase runners ────────────────────────────────────────────────────────

    def run_phase_1(self) -> tuple:
        print("[Phase 1] Metadata Extraction (AST + YAML + LLM)...")
        phase1 = Phase1MetadataExtraction(self.code, self.file_path, env=self._env)
        metadata, test_results = phase1.run()
        p = sum(1 for r in test_results if r.status == "PASS")
        f = sum(1 for r in test_results if r.status == "FAIL")
        print(f"  [OK] Phase 1 Complete: {p} PASS / {f} FAIL")
        return metadata, test_results

    def run_phase_2(self, metadata) -> list:
        print("[Phase 2] Azure Capability Discovery...")
        phase2 = Phase2CapabilityDiscovery(self.code)
        test_results, azure_model = phase2.run()
        self.azure_model = azure_model
        p = sum(1 for r in test_results if r.status == "PASS")
        f = sum(1 for r in test_results if r.status == "FAIL")
        print(f"  [OK] Phase 2 Complete: {p} PASS / {f} FAIL")
        return test_results

    def run_phase_3(self, metadata) -> list:
        print("[Phase 3] Snowflake Capability Discovery...")
        phase3 = Phase3CapabilityDiscovery(self.code)
        test_results, snowflake_model = phase3.run()
        self.snowflake_model = snowflake_model
        p = sum(1 for r in test_results if r.status == "PASS")
        f = sum(1 for r in test_results if r.status == "FAIL")
        print(f"  [OK] Phase 3 Complete: {p} PASS / {f} FAIL")
        return test_results

    def run_phase_4(self, metadata) -> list:
        print("[Phase 4] Environment Resource Validation (Dynamic Table Checks)...")
        phase4 = Phase4ResourceValidation(
            metadata.__dict__,
            self.validate_resources,
            etl_file_path=self.file_path,
            env=self._env
        )
        test_results = phase4.run()
        status_text = "Skipped" if not self.validate_resources else "Complete"
        p = sum(1 for r in test_results if r.status == "PASS")
        f = sum(1 for r in test_results if r.status == "FAIL")
        s = sum(1 for r in test_results if r.status == "SKIPPED")
        dynamic = sum(1 for r in test_results if r.test_id.startswith("TC_TABLE_"))
        print(f"  [OK] Phase 4 {status_text}: {p} PASS / {f} FAIL / {s} SKIPPED | Dynamic table checks: {dynamic}")
        return test_results

    def run_phase_5a(self, metadata) -> list:
        print("[Phase 5A] Dynamic Test Generator (capability-driven)...")
        generator = DynamicTestGenerator(self.azure_model, self.snowflake_model,
                                          code=self.code, etl_file_path=self.file_path, env=self._env)
        dynamic_tests = generator.generate_tests()
        self._phase5a_tests = dynamic_tests
        summary = generator.get_capability_summary()
        print(f"  [OK] Phase 5A Complete: {len(dynamic_tests)} tests generated based on capabilities")
        print(f"  Azure capabilities: {sum(1 for k,v in summary['azure']['capabilities'].items() if v)} / 6")
        print(f"  Snowflake capabilities: {sum(1 for k,v in summary['snowflake']['capabilities'].items() if v)} / 7")
        return []

    def run_phase_5b(self, metadata) -> list:
        """Phase 5B: Python executor runs each generated test case."""
        print("[Phase 5B] Test Executor (running generated tests)...")
        if not self._phase5a_tests:
            print("  [SKIP] No generated tests from Phase 5A - Phase 5B skipped")
            return []
        metadata_dict = metadata.__dict__ if hasattr(metadata, '__dict__') else (metadata if isinstance(metadata, dict) else {})
        phase5b = TestExecutorAgent(self._phase5a_tests, self.file_path, metadata_dict)
        test_results = phase5b.run()
        p = sum(1 for r in test_results if r.status == "PASS")
        f = sum(1 for r in test_results if r.status == "FAIL")
        s = sum(1 for r in test_results if r.status == "SKIPPED")
        print(f"  [OK] Phase 5B Complete: {p} PASS / {f} FAIL / {s} SKIPPED")
        return test_results

    def run_phase_5c(self, phase5b_results, metadata) -> list:
        """Phase 5C: Execution summary — Executed/Passed/Failed/Skipped/Risk."""
        print("[Phase 5C] Execution Summary...")
        metadata_dict = metadata.__dict__ if hasattr(metadata, '__dict__') else (metadata if isinstance(metadata, dict) else {})
        phase5c = ExecutionSummaryAgent(phase5b_results, metadata_dict)
        test_results = phase5c.run()
        self._phase5c_summary = phase5c.get_summary_dict()
        # Print the rich console summary
        print(phase5c.get_console_summary())
        return test_results

    def run_phase_6(self, all_results, metadata) -> list:
        """Phase 6: Final risk analysis with rich Executed/Passed/Failed/Risk summary."""
        print("[Phase 6] Result Analysis Agent...")
        metadata_dict = self.analysis.metadata.__dict__ if self.analysis else {}
        phase6 = ResultAnalysisAgent(
            self.code,
            metadata_dict,
            all_results,
            phase5c_summary=self._phase5c_summary,
        )
        test_results = phase6.run()
        p = sum(1 for r in test_results if r.status == "PASS")
        f = sum(1 for r in test_results if r.status == "FAIL")
        print(f"  [OK] Phase 6 Complete: {p} PASS / {f} FAIL")
        return test_results

    # ─── Main analysis pipeline ───────────────────────────────────────────────

    def run_analysis(self) -> CodeAnalysis:
        print(f"\nStarting ETL Test Case Agent Analysis...")
        print(f"File: {self.file_path}\n")

        self.read_python_file()

        # Phase 1 — Metadata Extraction
        metadata, phase1_results = self.run_phase_1()

        # Phase 2 — Azure Validation Discovery
        phase2_results = self.run_phase_2(metadata)

        # Phase 3 — Snowflake Validation Discovery (+ Transaction Safety)
        phase3_results = self.run_phase_3(metadata)

        # Phase 4 — Resource Validation (dynamic table checks)
        phase4_results = self.run_phase_4(metadata)

        # Phase 5A — LLM generates 5–8 tests
        phase5a_results = self.run_phase_5a(metadata)

        # Phase 5B — Python executes generated tests
        phase5b_results = self.run_phase_5b(metadata)

        # Phase 5C — Collect execution summary
        phase5c_results = self.run_phase_5c(phase5b_results, metadata)

        # All phase results combined (used by Phase 6 for holistic analysis)
        all_phase_results = (
            phase1_results + phase2_results + phase3_results +
            phase4_results + phase5a_results + phase5b_results + phase5c_results
        )

        # Phase 6 — Final risk analysis (consumes Phase 5C summary)
        phase6_results = self.run_phase_6(all_phase_results, metadata)

        # Assemble CodeAnalysis object
        self.analysis = CodeAnalysis(
            metadata=metadata,
            file_path=self.file_path,
            phase5c_summary=self._phase5c_summary,
        )

        for result in all_phase_results + phase6_results:
            self.analysis.add_test_result(result)

        return self.analysis

    def generate_reports(self):
        print("\nGenerating Reports...")

        generator = ReportGenerator(self.analysis)

        csv_path = generator.generate_csv()
        print(f"  [OK] CSV Report: {csv_path}")

        json_path = generator.generate_json()
        print(f"  [OK] JSON Report: {json_path}")
        
        excel_path = generator.generate_excel()
        if excel_path:
            print(f"  [OK] EXCEL Report: {excel_path}")

        summary = generator.generate_summary_report()
        print(summary)

        return csv_path, json_path

    def execute(self):
        try:
            self.run_analysis()
            self.generate_reports()
            return True
        except Exception as e:
            print(f"Error during execution: {e}")
            import traceback
            traceback.print_exc()
            return False


def main():
    import argparse

    parser = argparse.ArgumentParser(description="ETL Test Case Agent for Azure to Snowflake Pipeline")
    parser.add_argument("file", help="Path to Python ETL file to analyze")
    parser.add_argument(
        "--validate-resources",
        action="store_true",
        help="Enable Phase 4 resource validation (requires Azure and Snowflake credentials)"
    )
    parser.add_argument("--groq-key", help="Groq API key (or set GROQ_API_KEY environment variable)")

    args = parser.parse_args()

    if args.groq_key:
        Config.GROQ_API_KEY = args.groq_key

    agent = ETLTestCaseAgent(args.file, validate_resources=args.validate_resources)
    success = agent.execute()

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
