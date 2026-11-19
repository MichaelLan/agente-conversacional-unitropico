import logging

logger = logging.getLogger("unitropico_ingestion")

_handler = logging.StreamHandler()
_handler.setFormatter(logging.Formatter("%(levelname)s %(name)s %(message)s"))


def configure_logger(level: str) -> None:
    """Attach the stderr handler once; never log secrets or document text."""
    logger.setLevel(level)
    if _handler not in logger.handlers:
        logger.addHandler(_handler)
