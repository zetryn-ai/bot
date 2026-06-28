import sys
from pathlib import Path
from loguru import logger


def setup_logger(level: str = "INFO", log_file: str = "") -> None:
    logger.remove()

    logger.add(
        sys.stderr,
        level=level,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{extra[component]}</cyan> | "
            "{message}"
        ),
        filter=lambda r: "component" in r["extra"],
        colorize=True,
    )

    if log_file:
        # odoo.conf-style single configurable log file: human-readable, size-rotated.
        path = Path(log_file)
        if str(path.parent) not in ("", "."):
            path.parent.mkdir(parents=True, exist_ok=True)
        logger.add(
            log_file,
            level="DEBUG",
            format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {extra[component]} | {message}",
            filter=lambda r: "component" in r["extra"],
            # rotation/retention DISABLED untuk extended dry-run testing — REVERT setelah selesai
            # rotation="50 MB",
            # retention="14 days",
            # compression="gz",
            enqueue=True,
        )
    else:
        # Default: structured daily JSON sink under logs/.
        Path("logs").mkdir(exist_ok=True)
        logger.add(
            "logs/engine_{time:YYYY-MM-DD}.log",
            level="DEBUG",
            format="{time} | {level} | {extra} | {message}",
            rotation="00:00",
            retention="14 days",
            compression="gz",
            serialize=True,
        )
