"""Cross-platform checks; frozen execution and installer lifecycle run on Windows CI."""
import importlib.util
import json
from pathlib import Path
import sys

import pytest

from layerloom import app_logging

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('system,executable,metadata', [
    ('win32', 'LayerLoom.exe', 'build-info.json'),
    ('darwin', 'LayerLoom.app/Contents/MacOS/LayerLoom', 'LayerLoom.app/Contents/Resources/build-info.json'),
])
def test_bundle_identity_resolves_beside_correct_platform_executable(tmp_path, monkeypatch, system, executable, metadata):
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, 'platform', system)
    monkeypatch.setattr(sys, 'executable', str(tmp_path / executable))
    path = tmp_path / metadata
    path.parent.mkdir(parents=True, exist_ok=True)
    expected = {'version': 'test-build', 'runtime_versions': {'lib3mf': '2.5.0'}}
    path.write_text(json.dumps(expected))
    assert app_logging.packaged_build_info() == expected
    path.write_text('broken')
    assert 'read_error' in app_logging.packaged_build_info()


def test_native_inventory_collects_windows_dlls_at_package_relative_locations(monkeypatch):
    from types import ModuleType
    captured = []
    hooks = ModuleType('PyInstaller.utils.hooks')
    def collect(package, search_patterns):
        captured.append((package, search_patterns))
        return [(package + '/native.dll', package)]
    hooks.collect_dynamic_libs = collect
    hooks.copy_metadata = lambda name: [(name + '.dist-info', name + '.dist-info')]
    monkeypatch.setitem(sys.modules, 'PyInstaller.utils.hooks', hooks)
    spec = importlib.util.spec_from_file_location('windows_bundle_inventory', ROOT / 'packaging/macos/bundle_config.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _, binaries = module.native_dependencies()
    assert ('lib3mf/native.dll', 'lib3mf') in binaries
    assert all('*.dll' in patterns for _, patterns in captured)


def test_installer_stays_per_user_and_never_deletes_unrecognized_files():
    text = (ROOT / 'packaging/windows/LayerLoom.iss').read_text()
    assert 'PrivilegesRequired=lowest' in text
    assert 'DefaultDirName={localappdata}\\Programs\\LayerLoom' in text
    assert '[UninstallDelete]' not in text and '[InstallDelete]' not in text
    assert '{userprograms}\\LayerLoom' in text
    assert 'skipifsilent' in text


def test_native_audit_rejects_unbundled_delayed_runtime(tmp_path, monkeypatch):
    from types import SimpleNamespace
    spec = importlib.util.spec_from_file_location('windows_native_test', ROOT / 'scripts/windows_native.py')
    native = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(native)
    (tmp_path / 'worker').mkdir()
    (tmp_path / 'LayerLoom.exe').touch()
    class PE:
        FILE_HEADER = SimpleNamespace(Machine=0x8664)
        DIRECTORY_ENTRY_IMPORT = [SimpleNamespace(dll=b'KERNEL32.dll')]
        def __init__(self, path, **kwargs):
            self.DIRECTORY_ENTRY_DELAY_IMPORT = ([SimpleNamespace(dll=b'CONCRT140.dll')]
                                                 if path.endswith('.exe') else [])
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def parse_data_directories(self, **kwargs): pass
    fake = SimpleNamespace(PE=PE, DIRECTORY_ENTRY={'IMAGE_DIRECTORY_ENTRY_IMPORT': 1, 'IMAGE_DIRECTORY_ENTRY_DELAY_IMPORT': 13})
    monkeypatch.setitem(sys.modules, 'pefile', fake)
    with pytest.raises(RuntimeError, match='concrt140.dll'):
        native.audit_native_tree(tmp_path)
    # A runtime in the independent worker must not mask a missing GUI runtime.
    (tmp_path / 'worker/concrt140.dll').touch()
    with pytest.raises(RuntimeError, match='concrt140.dll'):
        native.audit_native_tree(tmp_path)
    (tmp_path / 'concrt140.dll').touch()
    assert native.audit_native_tree(tmp_path)['gui']['missing_non_system'] == {}
    PE.FILE_HEADER.Machine = 0x14c
    with pytest.raises(RuntimeError, match='Non-x64'):
        native.audit_native_tree(tmp_path)
