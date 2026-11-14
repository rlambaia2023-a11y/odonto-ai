import logging, time, functools

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("odontoai")


def retry(times=3, delay=2.0):
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*a, **kw):
            last = None
            for i in range(times):
                try:
                    return fn(*a, **kw)
                except Exception as e:
                    last = e
                    logger.warning("retry %d/%d for %s: %s", i+1, times, fn.__name__, e)
                    time.sleep(delay)
            raise last
        return wrapper
    return deco


def require_env():
    from .config import LLM_PROVIDER, OPENAI_API_KEY, ANTHROPIC_API_KEY
    if LLM_PROVIDER == "openai" and not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY não definido no .env")
    if LLM_PROVIDER == "anthropic" and not ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY não definido no .env")
