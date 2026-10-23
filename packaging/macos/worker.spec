import os
import sys
from pathlib import Path
sys.path.insert(0, SPECPATH)
from bundle_config import EXCLUDES, bundle_short_version, native_dependencies

root = Path(SPECPATH).parents[1]
native_data, native_binaries = native_dependencies()
a = Analysis(
    [str(Path(SPECPATH) / 'worker_entry.py')], pathex=[str(root / 'src')],
    binaries=native_binaries, datas=native_data,
    hiddenimports=[
        'layerloom.weave', 'layerloom.bake_in_memory_stlroundtrip',
        'supporting_scripts.glb_split_by_vertexcolor_v3_3mf', 'supporting_scripts.3mf_meshlab_repair_3mf', 'layerloom.repair',
        'lib3mf', 'pygltflib', 'scipy.spatial', 'scipy.spatial.transform',
    ],
    hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=EXCLUDES + ['pyvista', 'pyvistaqt', 'vtk', 'vtkmodules', 'PyQt5', 'qtpy', 'matplotlib'],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True, name='layerloom-worker',
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
    console=True, target_arch=os.environ['LAYERLOOM_BUILD_ARCH'], codesign_identity=None,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='layerloom-worker')
# A real nested bundle is required in Contents/Helpers: a plain onedir tree
# makes codesign interpret dotted metadata directories as malformed bundles.
app = BUNDLE(
    coll, name='LayerLoomWorker.app', bundle_identifier='org.layerloom.worker',
    info_plist={
        'CFBundleName': 'LayerLoom Worker',
        'CFBundleShortVersionString': bundle_short_version(os.environ['LAYERLOOM_BUILD_VERSION']),
        'CFBundleVersion': os.environ['LAYERLOOM_BUILD_VERSION'],
        'LSMinimumSystemVersion': os.environ['MACOSX_DEPLOYMENT_TARGET'],
        'LSUIElement': True,
    },
)
