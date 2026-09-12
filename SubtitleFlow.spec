# PyInstaller onedir: keep LGPL modules and libraries replaceable.
import sys
from PyInstaller.utils.hooks import copy_metadata
datas = [('legal', 'legal'), ('assets', 'assets')]
for package in ('keyring', 'jaraco.classes', 'jaraco.context', 'jaraco.functools', 'more-itertools'):
    datas += copy_metadata(package)
hidden = ['keyring.backends.Windows'] if sys.platform == 'win32' else ['keyring.backends.macOS']
hidden += ['_cffi_backend']
a = Analysis(['run_app.py'], pathex=[], binaries=[], datas=datas, hiddenimports=hidden,
             excludes=['tkinter', 'PySide6.QtQml', 'PySide6.QtQuick', 'PySide6.QtNetwork',
                       'PySide6.QtOpenGL', 'PySide6.QtSvg', 'PySide6.QtTest',
                       'PySide6.QtDBus', 'PySide6.QtPdf', 'PySide6.QtPrintSupport',
                       'PySide6.QtSql', 'PySide6.QtXml', 'PySide6.QtDesigner'],
             module_collection_mode={'PySide6': 'py', 'shiboken6': 'py'},
             noarchive=False)
# Platform + style plugins only. No SVG/PDF/QML/designer plugins.
a.binaries = [entry for entry in a.binaries
              if '/plugins/' not in entry[0].replace('\\', '/')
              or any('/plugins/' + group + '/' in entry[0].replace('\\', '/')
                     for group in ('platforms', 'styles'))]
# Drop unused transitive libraries collected before plugin filtering.
a.binaries = [e for e in a.binaries if not e[0].replace('\\', '/').endswith(('Qt6Svg.dll', 'Qt6Network.dll')) and '/QtSvg.framework/' not in e[0] and '/QtNetwork.framework/' not in e[0]]
a.datas = [e for e in a.datas if not e[0].endswith('release-audit.json')]
if sys.platform == 'win32':
    # Qt uses Windows system ICU; never bundle unrelated PATH-provided ICU builds.
    a.binaries = [e for e in a.binaries if not e[0].lower().startswith(('icuuc', 'icudt', 'icuin'))]
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='SubtitleFlow',
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
          console=False, icon='assets/app.ico' if sys.platform == 'win32' else 'assets/app.icns', entitlements_file='packaging/macos-entitlements.plist' if sys.platform == 'darwin' else None)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='SubtitleFlow')
if sys.platform == 'darwin':
    app = BUNDLE(coll, name='SubtitleFlow.app', icon='assets/app.icns',
                 bundle_identifier='app.subtitleflow.desktop',
                 info_plist={'NSHighResolutionCapable': True, 'CFBundleShortVersionString': '0.2.1', 'LSMinimumSystemVersion': '13.0'})
