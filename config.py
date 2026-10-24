from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    # 默认值：如果什么都不配，它就默认打本地环境
    ENV: str = "LOCAL"
    BASE_URL: str = "http://127.0.0.1:8000"
    DB_NAME: str = "test_business.db"
    
    # 允许从系统的环境变量或者 .env 文件中自动读取配置来覆盖上面的默认值
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

# 实例化一个全局的 settings 对象，供全项目调用
settings = Settings()

# 启动时打印一下当前环境，方便排障
print(f"当前运行环境: {settings.ENV} | 目标服务器: {settings.BASE_URL}")