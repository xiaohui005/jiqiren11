# config.py

# 群发检测间隔（秒），仅在 SEND_MODE='interval' 时生效
SEND_INTERVAL = 600  # 10分钟

# 定时模式：'interval' 表示每隔N秒，'daily' 表示每天固定时间
SEND_MODE = 'daily'  # 可选 'interval' 或 'daily'

# 每天定时发送时间（仅在SEND_MODE='daily'时生效），格式 'HH:MM'
SEND_TIME = '12:38'

# Telegram Bot Token
TOKEN = '7984491260:AAFs21_GdVJOj9-_Rlgc9aZVOp1cqfDBQ9Q'

# 数据库配置
DB_CONFIG = {
    'host': 'localhost',
    'user': 'root',
    'password': 'root',
    'database': 'lottery',
    'charset': 'utf8mb4',
    'cursorclass': None  # 主程序里再指定 DictCursor
}

# Telegram API URL
TELEGRAM_API_BASE = 'https://api.telegram.org/bot'

# 数据库表名
TABLE_TELEGRAM_CHATS = 'telegram_chats'
TABLE_SEND_QUEUE = 'bot_send_queue'

# 字段名（如需统一管理，可在此处定义）
FIELD_CHAT_ID = 'chat_id'
FIELD_TITLE = 'title'
FIELD_TYPE = 'type'
FIELD_CONTENT = 'content'
FIELD_STATUS = 'status'
FIELD_SEND_TIME = 'send_time'
FIELD_CREATE_TIME = 'create_time'
FIELD_UPDATE_TIME = 'update_time'
FIELD_ALLOW_SEND = 'allow_send' 
FLASK_PORT = 9999