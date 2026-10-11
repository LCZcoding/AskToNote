"""T1.1 应用配置：从 .env 读取 LLM 配置，并集中定义数据目录路径。

路径均相对运行目录 backend/（见 Plan §1/§2），即 backend/data/。
模型名从配置读取、不写死在业务代码（Spec §9.1）。
"""
import os
from pathlib import Path

from dotenv import load_dotenv

# backend/ 目录（config.py 位于 backend/app/config.py）
BASE_DIR = Path(__file__).resolve().parent.parent

# 尽早加载 .env；override=False，不覆盖已存在的环境变量
load_dotenv(BASE_DIR / ".env")

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-flash"


class Settings:
    """进程级配置对象；字段在实例化时从环境变量读取。"""

    def __init__(self) -> None:
        self.deepseek_api_key: str = os.getenv("DEEPSEEK_API_KEY", "")
        self.deepseek_base_url: str = os.getenv("DEEPSEEK_BASE_URL", DEFAULT_BASE_URL)
        self.model: str = os.getenv("MODEL", DEFAULT_MODEL)
        self.data_dir: Path = BASE_DIR / "data"
        self.notes_dir: Path = self.data_dir / "notes"


settings = Settings()
