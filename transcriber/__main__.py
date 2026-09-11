"""启动转录服务：python -m transcriber [--engine moss|fake] [--parent-pid PID]

启动后在标准输出打印一行 JSON：{"port": ..., "token": ..., "api_version": ...}。
给出 --parent-pid 时，该进程（主程序）退出后服务随之退出，不留下孤儿进程。
"""
import argparse
import json
import os
import secrets
import sys
import threading
import time

from . import API_VERSION
from .server import create_server
from .service import TranscriptionService


def wait_for_exit(pid):
    # 不能靠阻塞读取 stdin 感知主程序退出：Windows 上一个线程同步读管道时，
    # 其他线程创建子进程（ffmpeg）会被卡住。
    if sys.platform == "win32":
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
        if handle:
            kernel32.WaitForSingleObject(handle, 0xFFFFFFFF)
            kernel32.CloseHandle(handle)
        return
    while os.getppid() == pid:
        time.sleep(1)


def build_service(args):
    if args.engine == "fake":
        from .fakes import FakeEngine, FakeMedia
        return TranscriptionService(FakeEngine(), FakeMedia())
    from .engine import MossEngine, MODEL_ID
    from .media import FfmpegMedia
    return TranscriptionService(MossEngine(args.model or MODEL_ID), FfmpegMedia(args.ffmpeg))


def main(argv=None):
    parser = argparse.ArgumentParser(prog="transcriber", description="SubtitleFlow 转录服务")
    parser.add_argument("--engine", choices=("moss", "fake"), default="moss")
    parser.add_argument("--model", help="模型目录或 HuggingFace 模型 ID（默认在线模型）")
    parser.add_argument("--ffmpeg", default="ffmpeg", help="ffmpeg 可执行文件路径")
    parser.add_argument("--parent-pid", type=int, help="主程序进程号，它退出后服务随之退出")
    args = parser.parse_args(argv)

    token = secrets.token_urlsafe(32)
    server = create_server(build_service(args), token)
    if args.parent_pid:
        def watch_parent():
            wait_for_exit(args.parent_pid)
            server.shutdown()
        threading.Thread(target=watch_parent, daemon=True).start()
    print(json.dumps({"port": server.server_address[1], "token": token, "api_version": API_VERSION}), flush=True)
    server.serve_forever()
    server.server_close()


if __name__ == "__main__":
    main()
