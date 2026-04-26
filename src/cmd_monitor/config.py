"""配置加载模块"""

import sys
from pathlib import Path
from typing import Any, Optional, Union

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib  # type: ignore[no-redef]

DEFAULT_CONFIG_PATH = Path(__file__).parent.parent.parent / "config" / "default.toml"


def load_config(path: Optional[Union[str, Path]] = None) -> dict[str, Any]:
    """加载 TOML 配置文件

    Args:
        path: 配置文件路径，None 则使用默认路径

    Returns:
        配置字典；文件不存在时返回空字典（允许 status/stop 等命令在任意目录运行）
    """
    config_path = Path(path) if path else DEFAULT_CONFIG_PATH
    if not config_path.exists():
        return {}
    with open(config_path, "rb") as f:
        return tomllib.load(f)
