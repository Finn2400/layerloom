"""Offscreen subprocess lifecycle checks; no source or result mesh is loaded."""
from __future__ import annotations

import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')

import time
from pathlib import Path

import pytest

QtWidgets=pytest.importorskip('qtpy.QtWidgets')
QtCore=pytest.importorskip('qtpy.QtCore')


@pytest.fixture(scope='module')
def app():
    instance=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield instance


def wait_until(app, predicate, seconds=5):
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        app.processEvents()
        if predicate():return
        time.sleep(.01)
    raise AssertionError('Qt worker did not reach expected state')


def configured_dialog(tmp_path):
    from layerloom.contour_gui import create_contour_dialog
    source=tmp_path/'source.3mf';source.write_bytes(b'fixture')
    output=tmp_path/'output.3mf'
    dialog=create_contour_dialog()
    dialog.input.setText(str(source));dialog.output.setText(str(output))
    return dialog,output


def test_worker_success_and_bounded_log(app,monkeypatch,tmp_path):
    from layerloom import contour_gui
    dialog,output=configured_dialog(tmp_path)
    script=f"from pathlib import Path; Path({str(output)!r}).write_bytes(b'fixture'); print('layer 1/1'); [print(i) for i in range(650)]"
    monkeypatch.setattr(contour_gui,'contour_command',lambda *a,**k:['-c',script])
    dialog.start()
    wait_until(app,lambda:dialog.run_button.isEnabled())
    assert dialog.progress.value()==100
    assert 'Saved' in dialog.status.text()
    assert dialog.log.document().blockCount()<=500
    assert output.exists()
    dialog.close();dialog.deleteLater();app.processEvents()


def test_exit_zero_without_output_not_success(app,monkeypatch,tmp_path):
    from layerloom import contour_gui
    dialog,_=configured_dialog(tmp_path)
    monkeypatch.setattr(contour_gui,'contour_command',lambda *a,**k:['-c',"print('nothing written')"])
    dialog.start()
    wait_until(app,lambda:dialog.run_button.isEnabled())
    assert dialog.progress.value()==0
    assert 'failed' in dialog.status.text()
    dialog.close();dialog.deleteLater();app.processEvents()


def test_cancel_worker(app,monkeypatch,tmp_path):
    from layerloom import contour_gui
    dialog,_=configured_dialog(tmp_path)
    monkeypatch.setattr(contour_gui,'contour_command',lambda *a,**k:['-c','import time; time.sleep(30)'])
    dialog.start()
    wait_until(app,lambda:dialog.process.state()==QtCore.QProcess.Running)
    dialog.cancel()
    wait_until(app,lambda:dialog.run_button.isEnabled())
    assert dialog.cancelled and 'cancelled' in dialog.status.text().lower()
    assert dialog.progress.value()==0
    dialog.close();dialog.deleteLater();app.processEvents()


def test_same_first_height_display_tracks_layer_height(app,tmp_path):
    dialog,_=configured_dialog(tmp_path)
    dialog.layer.setValue(.2)
    assert dialog.first.value()==.2 and not dialog.first.isEnabled()
    dialog.same_first.setChecked(False);dialog.first.setValue(.16)
    dialog.layer.setValue(.12)
    assert dialog.first.value()==.16 and dialog.first.isEnabled()
    dialog.same_first.setChecked(True)
    assert dialog.first.value()==.12 and not dialog.first.isEnabled()
    dialog.close();dialog.deleteLater();app.processEvents()
