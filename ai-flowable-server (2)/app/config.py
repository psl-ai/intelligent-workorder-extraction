"""
应用配置 —— 集中管理所有环境变量配置

配置项从 .env 文件或环境变量读取，字段名不区分大小写：
    LLM_API_KEY   已部署大模型服务的 API Key
    LLM_BASE_URL  已部署大模型服务的 OpenAI 兼容地址
    LLM_MODEL     已部署模型名称
    LLM_TIMEOUT   调用超时时间（秒）
"""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """全局配置项，模型连接信息必须由环境配置提供"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    llm_api_key: str
    llm_base_url: str
    llm_model: str
    llm_timeout: float = 60.0


@lru_cache
def get_settings() -> Settings:
    """获取全局配置单例（进程内只解析一次 .env）"""
    return Settings()
