import logging


def configure_logger(level: str) -> logging.Logger:
    """Configura solo el logger del API, reutilizando su handler de stderr."""
    logger = logging.getLogger("unitropico_api")
    if not any(h.name == "unitropico_api.stderr" for h in logger.handlers):
        handler = logging.StreamHandler()
        handler.set_name("unitropico_api.stderr")
        handler.setFormatter(logging.Formatter("%(levelname)s %(name)s %(message)s"))
        logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False
    return logger
