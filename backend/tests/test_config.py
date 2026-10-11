"""T1.1 配置骨架单测：验证 .env 读取、默认值与数据目录路径。

说明：`Settings` 在实例化时从环境变量读取，故可通过 monkeypatch 控制
输入，测试不依赖真实 .env 内容（见 Tasks.md T1.1 / Plan §2）。
"""
from app.config import BASE_DIR, Settings, settings


def test_singleton_is_settings_instance() -> None:
    """模块级 settings 应为 Settings 实例。"""
    assert isinstance(settings, Settings)


def test_defaults_when_env_absent(monkeypatch) -> None:
    """环境变量缺失时应使用默认值（模型 deepseek-flash、官方 base_url、空 key）。"""
    monkeypatch.delenv("MODEL", raising=False)
    monkeypatch.delenv("DEEPSEEK_BASE_URL", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    s = Settings()

    assert s.model == "deepseek-flash"
    assert s.deepseek_base_url == "https://api.deepseek.com"
    assert s.deepseek_api_key == ""


def test_reads_values_from_env(monkeypatch) -> None:
    """环境变量存在时应覆盖默认值。"""
    monkeypatch.setenv("MODEL", "deepseek-chat")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://example.com")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")

    s = Settings()

    assert s.model == "deepseek-chat"
    assert s.deepseek_base_url == "https://example.com"
    assert s.deepseek_api_key == "sk-test"


def test_data_paths_point_to_backend_data_dir() -> None:
    """数据目录应相对运行目录 backend/，即 backend/data/notes。"""
    assert settings.data_dir == BASE_DIR / "data"
    assert settings.notes_dir == settings.data_dir / "notes"
    assert settings.notes_dir.name == "notes"
