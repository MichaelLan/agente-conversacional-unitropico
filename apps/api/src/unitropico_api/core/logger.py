import logging

LOGGER_NAME = "unitropico_api"


def get_logger() -> logging.Logger:
    """Devuelve el logger del API; es la única dependencia transversal."""
    return logging.getLogger(LOGGER_NAME)


def configure_logger(level: str) -> logging.Logger:
    """Configura solo el logger del API, reutilizando su handler de stderr."""
    logger = get_logger()
    if not any(h.name == "unitropico_api.stderr" for h in logger.handlers):
        handler = logging.StreamHandler()
        handler.set_name("unitropico_api.stderr")
        handler.setFormatter(logging.Formatter("%(levelname)s %(name)s %(message)s"))
        logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False
    return logger
