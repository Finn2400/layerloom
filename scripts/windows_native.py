"""Audit PE imports, including delay loads that a developer PC can mask."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess

# Windows components, not redistributables installed by Visual Studio/Python.
WINDOWS_DLLS = set('''user32.dll comctl32.dll kernel32.dll advapi32.dll gdi32.dll
ws2_32.dll crypt32.dll version.dll bcrypt.dll ole32.dll oleaut32.dll iphlpapi.dll
rpcrt4.dll propsys.dll ntdll.dll shell32.dll psapi.dll msvcrt.dll ucrtbase.dll
d3d9.dll imagehlp.dll mpr.dll userenv.dll netapi32.dll winmm.dll d3d11.dll dxgi.dll
dnsapi.dll winspool.drv comdlg32.dll uxtheme.dll dwmapi.dll imm32.dll wtsapi32.dll
opengl32.dll shlwapi.dll dbghelp.dll secur32.dll wintrust.dll setupapi.dll cfgmgr32.dll
normaliz.dll powrprof.dll hid.dll wldap32.dll wininet.dll winhttp.dll dwrite.dll
d2d1.dll shcore.dll msimg32.dll oleacc.dll'''.split())


def concurrency_runtime() -> Path:
    """Use the compiler's x64 redistributable directory, not an arbitrary DLL site."""
    vswhere = Path(os.environ.get('ProgramFiles(x86)', 'C:/Program Files (x86)')) / 'Microsoft Visual Studio/Installer/vswhere.exe'
    installation = subprocess.check_output([str(vswhere), '-latest', '-products', '*',
                                            '-property', 'installationPath'], text=True).strip()
    if not installation:
        raise RuntimeError('Visual Studio C++ redistributable build files are required on the builder.')
    candidates = sorted((Path(installation) / 'VC/Redist/MSVC').glob('*/x64/Microsoft.VC*.CRT/concrt140.dll'))
    if not candidates:
        raise RuntimeError('concrt140.dll was not found in the Visual Studio x64 redistributable directory.')
    return candidates[-1]


def audit_native_tree(app: Path) -> dict:
    import pefile
    report = {}
    for label, directory in (('gui', app), ('worker', app / 'worker')):
        files = [p for p in directory.rglob('*') if p.is_file() and p.suffix.lower() in {'.exe', '.dll', '.pyd'}
                 and (label == 'worker' or not p.is_relative_to(app / 'worker'))]
        available = {p.name.lower() for p in files}
        missing, system = {}, set()
        for path in files:
            with pefile.PE(str(path), fast_load=True) as pe:
                if pe.FILE_HEADER.Machine != 0x8664:
                    raise RuntimeError(f'Non-x64 binary in Windows x64 app: {path}')
                pe.parse_data_directories(directories=[
                    pefile.DIRECTORY_ENTRY['IMAGE_DIRECTORY_ENTRY_IMPORT'],
                    pefile.DIRECTORY_ENTRY['IMAGE_DIRECTORY_ENTRY_DELAY_IMPORT'],
                ])
                imports = getattr(pe, 'DIRECTORY_ENTRY_IMPORT', []) + getattr(pe, 'DIRECTORY_ENTRY_DELAY_IMPORT', [])
                for entry in imports:
                    dependency = entry.dll.decode('ascii').lower()
                    if dependency in available:
                        continue
                    if dependency in WINDOWS_DLLS or dependency.startswith(('api-ms-', 'ext-ms-')):
                        system.add(dependency)
                    else:
                        missing.setdefault(dependency, []).append(path.relative_to(app).as_posix())
        if missing:
            raise RuntimeError(f'{label} has unbundled non-system DLL imports (including delay loads): {missing}')
        report[label] = {'native_files': len(files), 'architecture': 'x64',
                         'system_dependencies': sorted(system), 'missing_non_system': {}}
    return report
