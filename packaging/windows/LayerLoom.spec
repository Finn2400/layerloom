import os
import sys
from pathlib import Path

root = Path(SPECPATH).parents[1]
shared = root / 'packaging/macos'
sys.path.insert(0, str(shared))
from bundle_config import EXCLUDES, gui_hidden_imports, native_dependencies, resource_files

datas, binaries = native_dependencies()
a = Analysis([str(shared / 'gui_entry.py')], pathex=[str(root / 'src'), str(shared)],
             binaries=binaries, datas=resource_files(root) + datas,
             hiddenimports=gui_hidden_imports(root), excludes=EXCLUDES,
             hookspath=[], hooksconfig={}, runtime_hooks=[], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='LayerLoom',
          console=False, debug=False, strip=False, upx=False,
          icon=os.environ['LAYERLOOM_BUILD_ICON'])
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='LayerLoom')
