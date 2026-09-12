# PyInstaller onedir build of the transcription service (Windows + CUDA). See ADR-0001.
# The model is NOT bundled; it is downloaded separately and pinned by hash in transcriber.json.
import os
from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

datas = collect_data_files('transformers', include_py_files=True)
for package in ('torch', 'transformers', 'tokenizers', 'huggingface_hub', 'safetensors', 'numpy', 'tqdm',
                'regex', 'requests', 'packaging', 'filelock', 'pyyaml'):
    try:
        datas += copy_metadata(package)
    except Exception:
        pass
binaries = []
ffmpeg_dir = os.environ.get('SUBTITLEFLOW_FFMPEG_DIR')
if ffmpeg_dir:
    # LGPL ffmpeg build, placed next to the service executable (see transcriber/__main__.py).
    binaries.append((os.path.join(ffmpeg_dir, 'ffmpeg.exe'), '.'))
    # LGPL requires shipping the licence text with the binary. It sits beside bin/ in the official archive.
    for candidate in (os.path.join(ffmpeg_dir, 'LICENSE.txt'),
                      os.path.join(os.path.dirname(ffmpeg_dir), 'LICENSE.txt')):
        if os.path.isfile(candidate):
            datas.append((candidate, 'legal'))
            break
    else:
        raise SystemExit('ffmpeg LICENSE.txt not found next to ffmpeg.exe; refusing to build without it')
hidden = collect_submodules('transformers.models.qwen3') + collect_submodules('transformers.models.whisper')
hidden += ['transcriber.engine', 'transcriber.media', 'transcriber.fakes']
a = Analysis(['run_transcriber.py'], pathex=[], binaries=binaries, datas=datas, hiddenimports=hidden,
             excludes=['tkinter', 'PySide6', 'matplotlib', 'IPython', 'jupyter', 'pytest'],
             # torch and transformers read their own source at runtime (TorchScript, remote model code).
             module_collection_mode={'torch': 'pyz+py', 'transformers': 'pyz+py'},
             noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='SubtitleFlow-Transcriber',
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False, console=True)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='SubtitleFlow-Transcriber')
