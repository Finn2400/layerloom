from __future__ import annotations

import math
import sys
from types import SimpleNamespace

import pytest

from layerloom.contour_options import ContourOptions
from layerloom.contour_gui import contour_command


def test_defaults_and_zero_tolerance():
    options = ContourOptions().validate(.08)
    assert options.xy_tolerance_mm == .02
    assert options.first_layer_height is None
    assert options.memory_budget_mib == 1024
    assert ContourOptions(xy_tolerance_mm=0, first_layer_height=.16).validate(.08)


@pytest.mark.parametrize("kwargs", [
    {"xy_tolerance_mm": -1}, {"xy_tolerance_mm": math.nan},
    {"first_layer_height": 0}, {"first_layer_height": math.inf},
    {"memory_budget_mib": 63}, {"memory_budget_mib": 64.1},
    {"memory_budget_mib": True}, {"reserve_disk_gib": -1},
])
def test_invalid_options(kwargs):
    with pytest.raises(ValueError):ContourOptions(**kwargs).validate(.08)


def test_dispatch_skips_materializing_legacy_stages(monkeypatch, tmp_path):
    from layerloom import weave
    called={}
    def backend(*args,**kwargs):called.update(args=args,kwargs=kwargs);return "complete"
    monkeypatch.setitem(sys.modules,"layerloom.contour_weave",SimpleNamespace(weave_contours=backend))
    def forbidden(*args,**kwargs):raise AssertionError("legacy materialization was called")
    for name in ["read_parts","build_name_token_map","_auto_bake_if_needed"]:
        monkeypatch.setattr(weave,name,forbidden)
    source=tmp_path/'source.3mf'
    options=ContourOptions(first_layer_height=.16)
    result=weave.weave_pipeline(str(source),weave_mode='layer-contour',options=options)
    assert result=="complete"
    assert called['args']==(str(source),str(tmp_path/'source_woven.3mf'))
    assert called['kwargs']['options'] is options
    assert called['kwargs']['step']==.08


def test_legacy_rejects_unapplied_contour_options(tmp_path):
    from layerloom.weave import weave_pipeline
    with pytest.raises(ValueError,match="ContourOptions require"):
        weave_pipeline(str(tmp_path/'a.3mf'),options=ContourOptions())


def test_cli_legacy_default_and_explicit_backend(monkeypatch,tmp_path):
    from layerloom import weave
    calls=[]
    monkeypatch.setattr(weave,'weave_pipeline',lambda **kw:calls.append(kw))
    assert weave.main(['-i',str(tmp_path/'x.3mf'),'--no-export-stls'])==0
    assert 'weave_mode' not in calls[-1]
    assert 'options' not in calls[-1]
    assert weave.main(['-i',str(tmp_path/'x.3mf'),'--weave-mode','layer-contour',
                       '--first-layer-height','.16','--xy-tolerance-mm','.01',
                       '--memory-budget-mib','256','--scratch-dir',str(tmp_path)])==0
    assert calls[-1]['options']==ContourOptions(.01,.16,256,str(tmp_path))
    assert weave.main(['-i','x.3mf','--first-layer-height','.16'])==1


def test_headless_backend_selection():
    from layerloom.headless import build_parser,config_from_args
    parser=build_parser()
    legacy=config_from_args(parser.parse_args(['x.glb']))
    assert legacy.weave_mode=='legacy' and legacy.contour_options is None
    selected=config_from_args(parser.parse_args(['x.glb','--weave-mode','layer-contour',
                                                '--first-layer-height','.16']))
    assert selected.contour_options.first_layer_height==.16
    assert selected.weave_mode=='layer-contour'


def test_gui_command_preserves_paths_without_shell_and_disallows_source_overwrite(tmp_path):
    source=tmp_path/'input with spaces.3mf';output=tmp_path/'output.3mf'
    options=ContourOptions(first_layer_height=.16,scratch_dir=str(tmp_path/'scratch space'))
    args=contour_command(str(source),str(output),step=.08,first_layer_color='y',options=options)
    assert args[args.index('-i')+1]==str(source)
    assert args[args.index('--scratch-dir')+1]==options.scratch_dir
    assert '--no-export-stls' in args
    with pytest.raises(ValueError,match='different output'):
        contour_command(str(source),str(source),step=.08,first_layer_color='auto',options=options)
