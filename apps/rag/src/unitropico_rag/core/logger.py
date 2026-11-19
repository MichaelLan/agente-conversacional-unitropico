import logging

logger = logging.getLogger("unitropico_rag")

_handler = logging.StreamHandler()
_handler.setFormatter(logging.Formatter("%(levelname)s %(name)s %(message)s"))


def configure_logger(level: str) -> None:
    """Attach the stderr handler once and set the level; never log request content."""
    logger.setLevel(level)
    if _handler not in logger.handlers:
        logger.addHandler(_handler)
