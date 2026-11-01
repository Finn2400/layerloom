import sys
from pathlib import Path

root = Path(SPECPATH).parents[1]
shared = root / 'packaging/macos'
sys.path.insert(0, str(shared))
from bundle_config import EXCLUDES, native_dependencies, worker_resource_files

datas, binaries = native_dependencies()
a = Analysis([str(shared / 'worker_entry.py')], pathex=[str(root / 'src')],
             binaries=binaries, datas=datas + worker_resource_files(root),
             hiddenimports=[
                 'layerloom.weave', 'layerloom.bake_in_memory_stlroundtrip',
                 'layerloom.desktop_glb_import', 'layerloom.region_weave',
                 'supporting_scripts.glb_split_by_vertexcolor_v3_3mf',
                 'supporting_scripts.3mf_meshlab_repair_3mf', 'layerloom.repair',
                 'lib3mf', 'pygltflib', 'scipy.spatial', 'scipy.spatial.transform',
             ], excludes=EXCLUDES + ['pyvista', 'pyvistaqt', 'vtk', 'vtkmodules', 'PyQt5', 'qtpy', 'matplotlib', 'numba.np.ufunc.tbbpool'],
             hookspath=[], hooksconfig={}, runtime_hooks=[], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='layerloom-worker',
          console=True, debug=False, strip=False, upx=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='worker')
