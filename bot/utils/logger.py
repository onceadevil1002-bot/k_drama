import logging
import sys
import time
import functools
from contextlib import contextmanager

def setup_logger(name="k_drama_bot"):
    """Setup a standard logger for the bot."""
    logger = logging.getLogger(name)
    if not logger.handlers:
        logger.setLevel(logging.INFO)
        
        # Console handler
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        ))
        logger.addHandler(handler)
        
    return logger

# Default logger instance
logger = setup_logger()

def track_performance(operation_name):
    """Decorator to measure and log execution time of async functions."""
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            start_time = time.perf_counter()
            try:
                result = await func(*args, **kwargs)
                return result
            finally:
                duration = (time.perf_counter() - start_time) * 1000
                logger.info(f"PERF: {operation_name} took {duration:.2f}ms")
        return wrapper
    return decorator

@contextmanager
def time_block(name):
    """Context manager to measure and log execution time of a block of code."""
    start_time = time.perf_counter()
    yield
    duration = (time.perf_counter() - start_time) * 1000
    logger.info(f"PERF: {name} took {duration:.2f}ms")
