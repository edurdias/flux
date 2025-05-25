import logging
import sys

from flux.config import Configuration


def get_logger(name, parent="flux"):
    """Get a logger for a specific component that inherits from the parent logger.

    Args:
        name: The name of the component or module (can be __name__)
        parent: The parent logger name (default: "flux")

    Returns:
        A configured logger instance
    """
    # If name already starts with the parent prefix, don't add it again
    if name.startswith(f"{parent}."):
        logger_name = name
    elif name == parent:
        logger_name = name
    else:
        logger_name = f"{parent}.{name}"

    # Get or create the logger
    logger = logging.getLogger(logger_name)

    # The logger will inherit level and handlers from the parent logger
    # due to the hierarchical nature of the logging system
    return logger


def configure_logging():
    """Configure logging for the Flux framework.

    Returns:
        logging.Logger: The configured root logger.
    """
    settings = Configuration.get().settings

    # Configure root logger for flux
    root_logger = logging.getLogger("flux")
    root_logger.setLevel(settings.log_level)
    root_logger.handlers = []  # Clear any existing handlers

    # Create formatter
    formatter = logging.Formatter(settings.log_format, datefmt=settings.log_date_format)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    root_logger.info(f"Flux logging initialized at level {settings.log_level}")

    return root_logger
