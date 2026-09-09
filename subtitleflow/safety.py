"""Bounded reads and safe local file operations."""
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import time

SRT_LIMIT = 32 * 1024 * 1024
JSON_LIMIT = 8 * 1024 * 1024

def no_links(path):
    path = Path(os.path.abspath(path))
    for part in [*reversed(path.parents), path]:
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if sys.platform == 'darwin' and str(part) in ('/var', '/tmp') and part.resolve() == Path('/private' + str(part)):
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('路径包含符号链接或重解析点，已停止操作')
    return path

def safe_tree(root):
    root = no_links(root)
    for parent, dirs, files in os.walk(root, followlinks=False):
        for name in dirs + files:
            no_links(Path(parent) / name)
    return root

def read_bytes(path, limit):
    path = no_links(path)
    if not stat.S_ISREG(path.stat().st_mode):
        raise ValueError('只允许读取普通文件')
    with path.open('rb') as stream:
        value = stream.read(limit + 1)
    if len(value) > limit:
        raise ValueError('文件超过允许的体积上限')
    return value

def read_json(path):
    return json.loads(read_bytes(path, JSON_LIMIT))

def atomic_write(path, content):
    path = no_links(path)
    fd, name = tempfile.mkstemp(prefix='.subtitleflow-', dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(content)
        no_links(path)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)

def bounded_response(response, limit=JSON_LIMIT, cancel=None, seconds=60):
    deadline = time.monotonic() + seconds
    chunks, size = [], 0
    while True:
        if cancel is not None and cancel.is_set():
            raise RuntimeError('任务已取消')
        if time.monotonic() > deadline:
            raise ValueError('响应超过总读取时限')
        chunk = getattr(response, 'read1', response.read)(min(65536, limit + 1 - size))
        if not chunk:
            return b''.join(chunks)
        size += len(chunk)
        if size > limit:
            raise ValueError('响应超过允许的体积上限')
        chunks.append(chunk)

def response_json(response, cancel=None):
    return json.loads(bounded_response(response, cancel=cancel))
