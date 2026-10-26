# ============================================================================
# config.py — 项目级统一配置中心 (Single Source of Truth)
#
# 设计原则：
#   1. 所有环境差异（本地 / Docker / CI）一律通过环境变量或 .env 注入，代码零硬编码；
#   2. 被测服务 (mock_server)、测试套件 (tests/)、压测脚本 (locustfile)、AI 工具链 (ai_agent/)
#      全部从这里读取配置，杜绝 BASE_URL / 数据库口令 / Webhook 密钥四处漂移。
#
# 环境变量优先级：系统环境变量 > .env 文件 > 下方默认值 (pydantic-settings 内置行为)
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # ---------------- 运行环境 ----------------
    ENV: str = "LOCAL"
    BASE_URL: str = "http://127.0.0.1:8000"

    # ---------------- MySQL 白盒落库 ----------------
    DB_HOST: str = "127.0.0.1"
    DB_PORT: int = 3306
    DB_USER: str = "qa_user"
    DB_PASS: str = "qa_pass"
    DB_NAME: str = "automation_shop"

    # ---------------- Redis 缓存 / 分布式锁 / MQ ----------------
    REDIS_HOST: str = "127.0.0.1"
    REDIS_PORT: int = 6379
    REDIS_PASS: str = "qa_redis_pass"

    # ---------------- 被测服务内部参数 ----------------
    # 性能调优实验开关: "slow" (优化前: 锁粒度过大+慢查询) | "fast" (优化后: 细粒度锁+索引加速)
    PERF_MODE: str = "fast"
    # 第三方支付回调密钥：仅作测试靶场用途，生产环境务必注入强随机值
    WEBHOOK_SECRET: str = "super_secret_key_from_alipay"

    # ---------------- LLM 质效工具链 (DeepSeek-V3 等 OpenAI 兼容接口) ----------------
    OPENAI_API_KEY: str = "sk-ipkfsfhspqoqdtuxnahvoxnevsvfbrgkpugyfcraowlwsryi"
    OPENAI_API_BASE: str = "https://api.siliconflow.cn/v1"
    LLM_MODEL: str = "deepseek-ai/DeepSeek-V3"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()

# 启动时打印当前环境，方便排障（配置中心唯一一处启动提示，其他模块不重复打印）
print(f"当前运行环境: {settings.ENV} | 目标服务器: {settings.BASE_URL}")
