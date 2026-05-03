"""日志配置模块"""

import io
import logging
import sys
from pathlib import Path
from typing import Optional, Union


def setup_logging(level: str = "INFO", log_file: Optional[Union[str, Path]] = None) -> None:
    """配置日志系统

    Args:
        level: 日志级别 (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_file: 日志文件路径，None 则只输出到控制台
    """
    # Windows 控制台默认 GBK 编码，无法输出 Unicode 字符（如 WT 标题中的盲文）。
    # 强制将 stdout 重配置为 UTF-8，避免 logger 因 UnicodeEncodeError 崩溃。
    if sys.stdout.encoding != "utf-8":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]

    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_path, encoding="utf-8"))

    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=handlers,
        force=True,
    )
