"""SubtitleFlow 转录服务：独立于主程序运行，见 ADR-0001。"""

# 主程序与服务之间 HTTP 接口的版本；只有不兼容的改动才递增。
API_VERSION = 1
