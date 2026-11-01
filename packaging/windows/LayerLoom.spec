import os
import sys
from pathlib import Path

root = Path(SPECPATH).parents[1]
shared = root / 'packaging/macos'
sys.path.insert(0, str(shared))
sys.path.insert(0, str(root / 'scripts'))
from bundle_config import EXCLUDES, gui_hidden_imports, native_dependencies, resource_files
from windows_native import concurrency_runtime

datas, binaries = native_dependencies()
binaries.append((str(concurrency_runtime()), '.'))
a = Analysis([str(shared / 'gui_entry.py')], pathex=[str(root / 'src'), str(shared)],
             binaries=binaries, datas=resource_files(root) + datas,
             hiddenimports=gui_hidden_imports(root), excludes=EXCLUDES + ['numba.np.ufunc.tbbpool'],
             hookspath=[], hooksconfig={}, runtime_hooks=[], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='LayerLoom',
          console=False, debug=False, strip=False, upx=False,
          icon=os.environ['LAYERLOOM_BUILD_ICON'])
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='LayerLoom')
