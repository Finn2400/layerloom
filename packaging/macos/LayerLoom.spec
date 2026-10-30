# Build through scripts/build_macos.py to apply target, notices and final signing.
import os
import sys
from pathlib import Path
sys.path.insert(0, SPECPATH)
from bundle_config import EXCLUDES, bundle_short_version, gui_hidden_imports, native_dependencies, resource_files

root = Path(SPECPATH).parents[1]
arch = os.environ['LAYERLOOM_BUILD_ARCH']
minimum = os.environ['MACOSX_DEPLOYMENT_TARGET']
version = os.environ['LAYERLOOM_BUILD_VERSION']
native_data, native_binaries = native_dependencies()
a = Analysis(
    [str(Path(SPECPATH) / 'gui_entry.py')],
    pathex=[str(root / 'src'), SPECPATH],
    binaries=native_binaries,
    datas=resource_files(root) + native_data,
    hiddenimports=gui_hidden_imports(root),
    hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=EXCLUDES,
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True, name='LayerLoom',
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
    console=False, target_arch=arch, codesign_identity=None,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='LayerLoom')
app = BUNDLE(
    coll, name='LayerLoom.app', bundle_identifier='org.layerloom.LayerLoom',
    icon=os.environ['LAYERLOOM_BUILD_ICON'],
    info_plist={
        'CFBundleName': 'LayerLoom', 'CFBundleDisplayName': 'LayerLoom',
        'CFBundleShortVersionString': bundle_short_version(version), 'CFBundleVersion': version,
        'LSMinimumSystemVersion': minimum,
        'NSHighResolutionCapable': True,
        'NSPrincipalClass': 'NSApplication',
        'NSHumanReadableCopyright': 'LayerLoom contributors; see THIRD_PARTY_NOTICES.',
        'LayerLoomArchitecture': arch,
    },
)
