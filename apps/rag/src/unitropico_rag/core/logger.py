import logging

logger = logging.getLogger("unitropico_rag")


def configure_logger(level: str) -> None:
    """Configure the component logger without recording request content."""
    logger.setLevel(level)
    if not logger.handlers:
        logger.addHandler(logging.NullHandler())
