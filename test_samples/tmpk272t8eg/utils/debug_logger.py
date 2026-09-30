import logging
import sys
from pathlib import Path
from datetime import datetime

class DebugLogger:
    """Centralized debug logging for all validation phases"""
    
    _instance = None
    _loggers = {}
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        self.debug_enabled = False
        self.log_file = None
        self.output_dir = Path(__file__).parent.parent / "outputs"
        self.output_dir.mkdir(exist_ok=True)
    
    def setup(self, debug_enabled=False, timestamp=None):
        """Initialize debug logging"""
        self.debug_enabled = debug_enabled
        
        if debug_enabled:
            if timestamp is None:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            self.log_file = self.output_dir / f"etl_debug_{timestamp}.log"
            
            # Create root logger
            root_logger = logging.getLogger("etl_validation")
            root_logger.setLevel(logging.DEBUG)
            
            # File handler
            fh = logging.FileHandler(self.log_file, mode='w')
            fh.setLevel(logging.DEBUG)
            
            # Console handler
            ch = logging.StreamHandler(sys.stdout)
            ch.setLevel(logging.INFO)
            
            # Formatter
            formatter = logging.Formatter(
                '%(asctime)s | %(name)s | %(levelname)-8s | %(message)s',
                datefmt='%Y-%m-%d %H:%M:%S'
            )
            fh.setFormatter(formatter)
            ch.setFormatter(formatter)
            
            # Add handlers
            if not root_logger.handlers:
                root_logger.addHandler(fh)
                root_logger.addHandler(ch)
    
    def get_logger(self, phase_name):
        """Get or create a logger for a specific phase"""
        if phase_name not in self._loggers:
            logger = logging.getLogger(f"etl_validation.{phase_name}")
            logger.setLevel(logging.DEBUG)
            self._loggers[phase_name] = logger
        return self._loggers[phase_name]
    
    def log_phase_start(self, phase_name, description=""):
        """Log phase start"""
        if not self.debug_enabled:
            return
        logger = self.get_logger(phase_name)
        logger.info(f"{'='*80}")
        logger.info(f"PHASE START: {phase_name}")
        if description:
            logger.info(f"Description: {description}")
        logger.info(f"{'='*80}")
    
    def log_phase_end(self, phase_name, test_count=0, passed=0, failed=0):
        """Log phase end with summary"""
        if not self.debug_enabled:
            return
        logger = self.get_logger(phase_name)
        logger.info(f"PHASE END: {phase_name}")
        logger.info(f"  Tests: {test_count} | Passed: {passed} | Failed: {failed}")
        logger.info(f"{'='*80}\n")
    
    def log_test(self, phase_name, test_id, test_name, status, finding, details=""):
        """Log individual test result"""
        if not self.debug_enabled:
            return
        logger = self.get_logger(phase_name)
        logger.info(f"[{status}] {test_id}: {test_name}")
        logger.debug(f"  Finding: {finding}")
        if details:
            logger.debug(f"  Details: {details}")
    
    def log_detection(self, phase_name, category, items, details=""):
        """Log detected patterns/items"""
        if not self.debug_enabled:
            return
        logger = self.get_logger(phase_name)
        logger.debug(f"Detected {category}: {items}")
        if details:
            logger.debug(f"  {details}")
    
    def log_validation_check(self, phase_name, check_name, result, expected, actual, details=""):
        """Log validation check details"""
        if not self.debug_enabled:
            return
        logger = self.get_logger(phase_name)
        status = "PASS" if result else "FAIL"
        logger.debug(f"[{status}] {check_name}")
        logger.debug(f"  Expected: {expected}")
        logger.debug(f"  Actual: {actual}")
        if details:
            logger.debug(f"  Details: {details}")
    
    def log_error(self, phase_name, error_msg, exception=None):
        """Log errors"""
        if not self.debug_enabled:
            return
        logger = self.get_logger(phase_name)
        logger.error(f"ERROR: {error_msg}")
        if exception:
            logger.error(f"Exception: {str(exception)}")
    
    def log_metric(self, phase_name, metric_name, value, unit=""):
        """Log metrics"""
        if not self.debug_enabled:
            return
        logger = self.get_logger(phase_name)
        logger.debug(f"Metric: {metric_name} = {value} {unit}")
    
    def log_code_analysis(self, phase_name, code_snippet, analysis):
        """Log code analysis details"""
        if not self.debug_enabled:
            return
        logger = self.get_logger(phase_name)
        logger.debug(f"Code Analysis:")
        logger.debug(f"  Snippet: {code_snippet[:100]}...")
        logger.debug(f"  Analysis: {analysis}")
    
    def get_log_file(self):
        """Get the debug log file path"""
        return self.log_file if self.debug_enabled else None


# Singleton instance
debug_logger = DebugLogger()
