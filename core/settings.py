from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "Breakglass"
    debug: bool = True

    backend_url: str = "http://localhost:8000"
    database_url: str = "sqlite+aiosqlite:///./networkdevice.db"
    # later: "postgresql+asyncpg://user:pass@host/db"
    
    # Vault details
    vault_addr: str = "http://localhost:8200"
    vault_token: str = "changeme"

    # Github or Ansible credential
    rotation_api_url: str = "http://localhost:8000"
    rotation_api_token: str = "Some_secret_key"
    rotation_max_retries: int = 3
    rotation_backoff_seconds: int = 60
    rotation_failure_window_minutes: int = 15


    # Email approval secret
    email_approval_secret: str = "Very Strong Secret Key Remember to change me"

    # SMTP Server
    smtp_host: str = "localhost"
    smtp_port: int = 25
    smtp_user: str | None = None
    smtp_password: str | None = None
    email_from: str = "breakglass@example.com"
    email_approval_domain: str = "http://localhost:8000"
    email_approval_token_expiry_minutes: int = 1
    email_group_notification: str = "user1@domain.com, user2@domain.com"
    email_simulation: bool = True

    jwt_secret: str = "super-secret"
    jwt_algorithm: str = "HS256"
    jwt_exp_minutes: int = 60

    # # Nagios XI / Core API
    # nagios_url: str = "https://nagios.example.com"
    # nagios_api_token: str | None = None  # or username/password if you prefer
    # nagios_verify_ssl: bool = True

    # Audit Logging Configuration
    log_folder: str = "logs"
    log_file: str = "audit.log"
    log_rotation: str = "10 MB"       # 10 MB - rotate when file reaches 10MB
    #log_rotation: str = "1 minute"       # 1 minute - rotate when file reaches 1 min
    log_retention: str = "14 days"    # keep logs for 14 days
    log_compression: str = "zip"      # compress rotated logs

    class Config:
        env_file = ".env"

settings = Settings()
