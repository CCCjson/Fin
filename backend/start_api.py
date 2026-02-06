"""
启动API服务器
"""
import sys
from pathlib import Path

# 确保能导入项目模块
sys.path.insert(0, str(Path(__file__).parent))

import uvicorn
from loguru import logger

# 配置日志
logger.remove()
logger.add(
    sys.stdout,
    format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
    level="INFO"
)

if __name__ == "__main__":
    logger.info("启动量化交易系统API...")
    logger.info("本地访问地址: http://127.0.0.1:8000")
    logger.info("API文档: http://127.0.0.1:8000/docs")
    logger.info("按 Ctrl+C 停止服务器")

    uvicorn.run(
        "api.main:app",
        host="127.0.0.1",
        port=8000,
        reload=True,
        log_level="info"
    )
