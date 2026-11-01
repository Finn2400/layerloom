#!/usr/bin/env python3
"""Test the actual Windows installer, relocated portable app and frozen workflows.

Run on disposable Windows CI. Silent setup verifies the payload/lifecycle, not
SmartScreen, a real desktop GPU, or a novice's manual first-use experience.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import tempfile
import zipfile
import uuid
from pathlib import Path

from validate_macos import command, validate_3mf, validate_desktop_glb


def payload_hashes(app):
    return {p.relative_to(app).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(app.rglob('*')) if p.is_file()}


def verify_payload(app, expected):
    for relative, digest in expected.items():
        path = app / relative
        assert path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == digest, relative


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('artifacts', type=Path)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--gui', action='store_true')
    parser.add_argument('--offline', action='store_true', help='Block tested executables with Windows Firewall; disposable elevated CI only')
    args = parser.parse_args(argv)
    if os.name != 'nt':
        parser.error('Run on Windows.')
    import winreg
    key_name = r'Software\Microsoft\Windows\CurrentVersion\Uninstall\org.layerloom.LayerLoom_is1'
    shortcut = Path(os.environ['APPDATA']) / 'Microsoft/Windows/Start Menu/Programs/LayerLoom.lnk'
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_name, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY):
            parser.error('An existing LayerLoom installation is registered. Use a disposable account.')
    except FileNotFoundError:
        pass
    if shortcut.exists():
        parser.error('An existing LayerLoom Start-menu shortcut exists. Use a disposable account.')
    report_dir = args.report.resolve()
    report_dir.mkdir(parents=True, exist_ok=True)
    artifacts = args.artifacts.resolve()
    installer, = artifacts.glob('*-setup.exe')
    portable, = artifacts.glob('*-portable.zip')
    for path in (installer, portable):
        digest, name = path.with_suffix(path.suffix + '.sha256').read_text().split()
        assert name == path.name and hashlib.sha256(path.read_bytes()).hexdigest() == digest
    report = {'status': 'running', 'windows': platform.version(), 'architecture': platform.machine(),
              'manual_smartscreen_test': 'not performed; silent CI setup is not a browser-download test',
              'graphics': 'Hosted software renderer, not a physical PC performance test'}
    env = os.environ.copy()
    for key in ('PYTHONPATH', 'PYTHONHOME', 'VIRTUAL_ENV', 'QT_PLUGIN_PATH', 'QT_QPA_PLATFORM_PLUGIN_PATH'):
        env.pop(key, None)
    env.update(PATH=os.pathsep.join([str(Path(os.environ['SystemRoot']) / 'System32'), os.environ['SystemRoot']]),
               PYTHONNOUSERSITE='1', PYTHONUTF8='1', LAYERLOOM_LOG_DIR=str(report_dir / 'logs'),
               MPLCONFIGDIR=str(report_dir / 'matplotlib'))
    firewall_group = 'LayerLoom-validation-' + uuid.uuid4().hex
    powershell = Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    def ps(script):
        return subprocess.check_output([str(powershell), '-NoProfile', '-NonInteractive', '-Command',
                                        "$ErrorActionPreference='Stop'; " + script], text=True)
    try:
        with tempfile.TemporaryDirectory(prefix='LayerLoom Windows é ') as directory:
            work = Path(directory)
            # The archive must extract to one self-contained folder, never outside it.
            with zipfile.ZipFile(portable) as archive:
                for name in archive.namelist():
                    target = (work / name).resolve()
                    assert target.is_relative_to(work.resolve()) and Path(name).parts[0] == 'LayerLoom'
                archive.extractall(work)
            app = work / 'LayerLoom'
            expected = payload_hashes(app)
            assert 'worker/layerloom-worker.exe' in expected and '_internal/python312.dll' in expected
            assert not any('pymeshlab' in p or '_tkinter' in p for p in expected)
            info = json.loads((app / 'build-info.json').read_text())
            report['build'] = info
            # /DIR is test isolation only: normal setup defaults to LocalAppData.
            installed = work / 'installed café' / 'LayerLoom'
            if args.offline:
                programs = [installer, installed / 'LayerLoom.exe', installed / 'worker/layerloom-worker.exe',
                            app / 'LayerLoom.exe', app / 'worker/layerloom-worker.exe']
                ps("if ((Get-Service MpsSvc).Status -ne 'Running') { throw 'Firewall service is not running' }; "
                   "if (@(Get-NetFirewallProfile | Where-Object {-not $_.Enabled}).Count) { throw 'Firewall profiles must already be enabled' }")
                for i, program in enumerate(programs):
                    quoted = str(program).replace("'", "''")
                    ps(f"New-NetFirewallRule -Name '{firewall_group}-{i}' -DisplayName '{firewall_group}' "
                       f"-Group '{firewall_group}' -Direction Outbound -Action Block -Enabled True -Profile Any -Program '{quoted}' | Out-Null")
                count = int(ps(f"@(Get-NetFirewallRule -Group '{firewall_group}' | Where-Object {{$_.Enabled -eq 'True' -and $_.Action -eq 'Block'}}).Count").strip())
                assert count == len(programs)
                report['offline'] = 'Installer, installed/portable GUI and workers blocked by enabled outbound firewall rules'
            setup = [installer, '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-', f'/DIR={installed}']
            command([*setup, f'/LOG={report_dir / "install.log"}'], cwd=work, env=env, log=report_dir / 'setup.log')
            verify_payload(installed, expected)
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_name, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
                location = winreg.QueryValueEx(key, 'InstallLocation')[0]
                assert Path(location).resolve() == installed.resolve()
            assert shortcut.is_file(), 'Missing Start menu shortcut'
            report['per_user_install'] = 'passed; HKCU registration and Start menu shortcut verified'
            sentinel = installed / 'user-test-do-not-delete.txt'
            sentinel.write_text('preserve user data', encoding='utf-8')
            # Reinstall repairs missing runtime files without removing user data.
            (installed / 'worker/layerloom-worker.exe').unlink()
            command([*setup, f'/LOG={report_dir / "reinstall.log"}'], cwd=work, env=env, log=report_dir / 'setup-repair.log')
            verify_payload(installed, expected)
            assert sentinel.read_text() == 'preserve user data'
            report['reinstall_repair'] = 'passed; missing worker restored and user file preserved'
            unrelated = work / 'unrelated working folder'
            unrelated.mkdir()
            helper = installed / 'worker/layerloom-worker.exe'
            runtime = json.loads(command([helper, '--diagnostics'], cwd=unrelated, env=env,
                                         log=report_dir / 'worker-runtime.log'))
            assert runtime['frozen'] and runtime['lib3mf']['ok']
            assert Path(runtime['executable']).resolve() == helper.resolve()
            for key in ('version', 'python', 'architecture'):
                assert runtime[key] == info[key], (key, runtime, info)
            for name, version in runtime['packages'].items():
                assert version == info['runtime_versions'][name]
            report['worker_runtime'] = runtime
            capabilities = json.loads(command([helper, '--capabilities'], cwd=unrelated, env=env,
                                             log=report_dir / 'capabilities.log'))
            assert capabilities['repair'] is False
            source = installed / '_internal/layerloom/examples/tutorial_cmy_cubes.3mf'
            output = unrelated / 'woven café.3mf'
            command([helper, 'weave', '-i', source, '-o', output, '--step', '0.16', '--no-export-stls'],
                    cwd=unrelated, env=env, log=report_dir / 'weave.log')
            report['woven'] = validate_3mf(output)
            report['desktop_glb'] = validate_desktop_glb([helper], work=unrelated, env=env, report_dir=report_dir)
            if args.gui:
                for label, folder in [('installed', installed), ('portable', app)]:
                    target = report_dir / label
                    target.mkdir()
                    command([folder / 'LayerLoom.exe', '--desktop-smoke-test', target], cwd=unrelated, env=env,
                            log=target / 'gui.log', timeout=900)
                    gui = json.loads((target / 'gui-smoke.json').read_text())
                    assert gui['status'] == 'passed' and gui['frozen'], gui
                    report[label + '_gui'] = gui
            # Examples and the complete installed/portable payload must remain unchanged.
            verify_payload(installed, expected)
            verify_payload(app, expected)
            uninstaller = installed / 'unins000.exe'
            command([uninstaller, '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART',
                     f'/LOG={report_dir / "uninstall.log"}'], cwd=work, env=env, log=report_dir / 'uninstaller.log')
            assert not (installed / 'LayerLoom.exe').exists() and not shortcut.exists()
            assert sentinel.read_text() == 'preserve user data'
            assert output.is_file(), 'Uninstall removed an exported model'
            report['uninstall'] = 'passed; exported models and unrecognized user file preserved'
            report['status'] = 'passed'
    except Exception as exc:
        report.update(status='failed', error=f'{type(exc).__name__}: {exc}')
        raise
    finally:
        if args.offline:
            ps(f"Get-NetFirewallRule -Group '{firewall_group}' -ErrorAction SilentlyContinue | Remove-NetFirewallRule")
        (report_dir / 'validation.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
