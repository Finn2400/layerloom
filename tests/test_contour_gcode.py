from io import StringIO
import json
import pytest
from shapely.geometry import LineString, MultiLineString
from layerloom.contour_gcode import iter_wall_layers, compare_gcode, hausdorff_bounds

HEADER='G90\nM82\nG92 E0\nG1 Z0.2\n;TYPE:Outer wall\n;WIDTH:0.42\n'

def write(tmp_path,name,text):
    p=tmp_path/name;p.write_text(text);return p

def test_parser_modes_retraction_travel_tools_and_compact_words():
    s=HEADER+'G1X10Y0E1\nG1 E0.5\nG1 E1\nG0 X20\nM83\nG91\nG1 X5 E.5\nT2\nG1 X5 E.5\nG90\nG1 Z.4\n;TYPE:Inner wall\nG1 X40 E1\n'
    layers=list(iter_wall_layers(StringIO(s)))
    assert [x.z for x in layers]==[.2,.4]
    assert len(layers[0].segments)==3
    assert layers[0].segments[0].end==(10.,0.)
    assert layers[0].segments[1].start==(20.,0.)
    assert layers[0].segments[1].end==(25.,0.)
    assert layers[0].segments[2].tool==2
    assert layers[1].segments[0].feature=='inner'

def test_equivalent_subdivision_passes_without_identical_text(tmp_path):
    a=write(tmp_path,'a.gcode',HEADER+'G1 X10 E1\n')
    b=write(tmp_path,'b.gcode',HEADER+'G1 X5 E.5\nG1 X10 E1\n')
    r=compare_gcode(a,b,report_path=tmp_path/'layers.jsonl')
    assert r['wall_paths_validated']
    assert r['max_displacement_upper_mm']==0
    assert len((tmp_path/'layers.jsonl').read_text().splitlines())==1

def test_translated_wall_reports_threshold(tmp_path):
    a=write(tmp_path,'a.gcode',HEADER+'G1 X10 E1\n')
    b=write(tmp_path,'b.gcode',HEADER+'G0 Y.01\nG1 X10 E1\n')
    assert compare_gcode(a,b)['wall_paths_validated']
    b.write_text(HEADER+'G0 Y.03\nG1 X10 E1\n')
    assert not compare_gcode(a,b)['wall_paths_validated']

def test_same_outline_wrong_tool_or_width_fails(tmp_path):
    a=write(tmp_path,'a.gcode',HEADER+'G1 X10 E1\n')
    b=write(tmp_path,'b.gcode',HEADER+'T1\nG1 X10 E1\n')
    assert not compare_gcode(a,b)['wall_paths_validated']
    b.write_text(HEADER+';WIDTH:.50\nG1 X10 E1\n')
    assert not compare_gcode(a,b)['wall_paths_validated']

def test_widths_cannot_swap_between_disjoint_walls(tmp_path):
    a=write(tmp_path,'a.gcode',HEADER+'G1 X10 E1\nG0 Y10\n;WIDTH:.6\nG1 X0 E2\n')
    b=write(tmp_path,'b.gcode',HEADER+';WIDTH:.6\nG1 X10 E1\nG0 Y10\n;WIDTH:.42\nG1 X0 E2\n')
    assert not compare_gcode(a,b)['wall_paths_validated']

def test_missing_layer_and_unknown_width_fail(tmp_path):
    a=write(tmp_path,'a.gcode',HEADER+'G1 X10 E1\nG1 Z.4\nG1 X0 E2\n')
    b=write(tmp_path,'b.gcode',HEADER+'G1 X10 E1\n')
    assert compare_gcode(a,b)['failed_layers']==1
    b.write_text(HEADER.replace(';WIDTH:0.42\n','')+'G1 X10 E1\nG1 Z.4\nG1 X0 E2\n')
    assert not compare_gcode(a,b)['wall_paths_validated']

@pytest.mark.parametrize('cmd',['G2 X10 I5 E1','G1 X10 Z.21 E1','G20','M200 D1.75'])
def test_unsupported_wall_modes_fail_closed(cmd):
    with pytest.raises(ValueError):list(iter_wall_layers(StringIO(HEADER+cmd+'\n')))

def test_continuous_distance_catches_gap_missed_by_vertex_sampling():
    a=LineString([(0,0),(10,0)])
    b=MultiLineString([[(0,0),(4,0)],[(6,0),(10,0)]])
    low,high=hausdorff_bounds(a,b)
    assert .999<=high<=1.002
    assert low<=high and high-low<=.00100001

def test_layer_budget_and_empty_files(tmp_path):
    with pytest.raises(MemoryError):
        list(iter_wall_layers(StringIO(HEADER+'G1 X1 E1\nG1 X2 E2'),max_layer_segments=1))
    a=write(tmp_path,'a.gcode','G28\n')
    assert not compare_gcode(a,a)['wall_paths_validated']


def test_large_distance_bound_includes_buffer_arc_approximation():
    a=LineString([(0,0),(100,0)])
    b=MultiLineString([[(0,0),(35,0)],[(65,0),(100,0)]])
    low,high=hausdorff_bounds(a,b)
    assert low<=15<=high and high-low<=.00100001

def test_feature_reclassification_is_separate_from_physical_deposition(tmp_path):
    a=write(tmp_path,'a.gcode',HEADER+'G1 X10 E1\n')
    b=write(tmp_path,'b.gcode',HEADER.replace('Outer wall','Overhang wall')+'G1 X10 E1\n')
    result=compare_gcode(a,b)
    assert not result['wall_paths_validated']
    assert result['max_combined_feature_displacement_upper_mm']==0
    assert result['combined_feature_geometry_failed_layers']==0

def test_filled_footprint_distance_handles_a_small_hole():
    from shapely.geometry import Polygon,box
    full=box(0,0,10,10)
    hole=Polygon(full.exterior.coords,[[(4,4),(6,4),(6,6),(4,6),(4,4)]])
    low,high=hausdorff_bounds(full,hole)
    assert low<=1<=high and high-low<=.00100001
