"""Independent geometry checks for the experimental streaming weave.

Small fixtures deliberately use ordinary in-memory mesh helpers in the tests.
The production path is guarded against calling those full-mesh importers.
"""
import json
from pathlib import Path
import zipfile
import xml.etree.ElementTree as ET
from types import SimpleNamespace

import numpy as np
import pytest
import shapely
from shapely.geometry import Polygon, box
from shapely.ops import unary_union
import trimesh
from manifold3d import Manifold, Mesh64, CrossSection

from layerloom.contour_options import ContourOptions
from layerloom.contour_store import TriangleStore
from layerloom.contour_weave import slice_part, layer_regions, simplify_regions, weave_contours
from layerloom.export import write_basic_3mf

pytestmark = pytest.mark.skipif(not hasattr(shapely, 'constrained_delaunay_triangles'), reason='requires Shapely 2.1 / GEOS 3.13')
NS = {'m': 'http://schemas.microsoft.com/3dmanufacturing/core/2015/02'}


def solid_box(x=2, y=2, height=.45, offset=(0, 0, 0)):
    m = trimesh.creation.box(extents=(x, y, height))
    m.apply_translation((offset[0], offset[1], offset[2] + height/2))
    return m


def extruded(poly, height=.4):
    return trimesh.creation.extrude_polygon(poly, height, engine='earcut')


def source(tmp_path, parts):
    p = tmp_path / 'source.3mf'
    write_basic_3mf(parts, str(p))
    return p


def options(tmp_path, **kw):
    args = dict(xy_tolerance_mm=0., first_layer_height=.16, reserve_disk_gib=0., scratch_dir=str(tmp_path))
    args.update(kw)
    return ContourOptions(**args)


def load_solids(path):
    """Independent exact-serialized-coordinate parser (small test files only)."""
    with zipfile.ZipFile(path) as z:
        root = ET.fromstring(z.read('3D/3dmodel.model'))
        manifest = json.loads(z.read('Metadata/layerloom_contour.json'))
    out = {}
    for obj in root.findall('m:resources/m:object', NS):
        mesh = obj.find('m:mesh', NS)
        if mesh is None: continue
        v = np.array([[float(el.get(a)) for a in ('x','y','z')] for el in mesh.findall('m:vertices/m:vertex', NS)])
        f = np.array([[int(el.get(a)) for a in ('v1','v2','v3')] for el in mesh.findall('m:triangles/m:triangle', NS)], dtype=np.uint64)
        assert np.isfinite(v).all()
        assert f.max() < len(v)
        for coords in (v, v.astype(np.float32).astype(float)):
            a = coords[f]
            cross = np.cross(a[:,1]-a[:,0], a[:,2]-a[:,0])
            assert np.all(np.einsum('ij,ij->i', cross, cross) > 0)
        m = trimesh.Trimesh(v, f, process=False)
        assert m.is_watertight and m.is_winding_consistent
        q = Manifold(Mesh64(v, f))
        assert str(q.status()) == 'Error.NoError'
        out[obj.get('name')] = q
    return out, manifest


def color_solids(path):
    loaded, manifest = load_solids(path)
    return {n.split('__PAT_')[-1].split('__')[0]: q for n, q in loaded.items()}, manifest


def assert_slice_area(q, z, expected, tol=2e-5):
    assert q.slice(z).area() == pytest.approx(expected, abs=tol)


class _TriangleChunks:
    memory_budget_mib=1024

    def __init__(self, triangles):
        self.triangles=triangles

    def iter_triangles(self, part_id, z=None):
        # Force paired sidewall triangles to cross iteration boundaries.
        for triangle in self.triangles:
            yield triangle[None,:,:]


def test_vertical_quad_recovers_original_edges_without_diagonal_points():
    poly=Polygon([(.2,.3),(4.7,.47),(4.5,3.2),(.1,2.8)])
    mesh=extruded(poly,height=.4)
    store=_TriangleChunks(mesh.triangles)
    for z in (0,.173926,.39999):
        section=slice_part(store,1,z)
        assert len(section.exterior.coords)==5
        assert section.symmetric_difference(poly).area<1e-12


def test_lone_vertical_triangle_is_not_expanded_to_a_rectangle():
    mesh=trimesh.convex.convex_hull(np.array([[0,0,0],[2,0,0],[0,0,2],[0,2,0]]))
    section=slice_part(_TriangleChunks(mesh.triangles),1,.5)
    expected=Polygon([(0,0),(1.5,0),(0,1.5)])
    assert section.symmetric_difference(expected).area<1e-12


def test_vertical_triangles_with_different_diagonals_are_not_paired():
    left=trimesh.convex.convex_hull(np.array([[0,0,0],[0,0,1],[2,0,0],[0,1,0]]))
    right=trimesh.convex.convex_hull(np.array([[2,0,0],[2,0,1],[0,0,0],[2,1,0]]))
    section=slice_part(_TriangleChunks(np.concatenate([left.triangles,right.triangles])),1,.75)
    assert section.geom_type=='MultiPolygon' and len(section.geoms)==2
    assert section.area==pytest.approx(.125)


def test_vertical_quad_pairing_preserves_winding_multiplicity():
    poly=Polygon([(.2,.3),(4.7,.47),(4.5,3.2),(.1,2.8)])
    triangles=extruded(poly,height=.4).triangles
    positive=np.concatenate([triangles,triangles,triangles[:,::-1]])
    assert slice_part(_TriangleChunks(positive),1,.173926).symmetric_difference(poly).area<1e-12
    cancel=np.concatenate([triangles,triangles[:,::-1]])
    assert slice_part(_TriangleChunks(cancel),1,.173926).is_empty


def test_complete_vertical_quads_preserve_a_sub_grid_width_hole():
    from layerloom.contour_weave import EXPORT_GRID
    g=EXPORT_GRID;n=32768
    hole=[(1,1),(1+n*g,1+(n+1)*g),(1+(n+1)*g,1+(n+2)*g)]
    poly=Polygon([(0,0),(3,0),(3,3),(0,3)],[hole])
    # Direct closed sidewall rings isolate slicing from cap triangulation.
    triangles=[]
    for ring in [poly.exterior,*poly.interiors]:
        xy=list(ring.coords)
        for a,b in zip(xy,xy[1:]):
            a0=(*a,0);a1=(*a,.4);b0=(*b,0);b1=(*b,.4)
            triangles.extend([(a0,b0,b1),(a0,b1,a1)])
    section=slice_part(_TriangleChunks(np.asarray(triangles)),1,.173926)
    assert len(section.interiors)==1
    assert section.boundary.equals(poly.boundary)
    assert shapely.get_precision(section)==EXPORT_GRID
    assert shapely.set_precision(section,EXPORT_GRID).boundary.equals(poly.boundary)


def test_layer_regions_preserves_single_prism_part_with_thin_hole():
    from layerloom.contour_weave import EXPORT_GRID, extrude_polygon, polygons, _signature
    # The three hole vertices are from Pam's layer 226. Two simple surrounding
    # rectangles retain the MultiPolygon condition that makes a redundant
    # fixed-precision union discard the valid, long, sub-grid-width hole.
    fixture=Path(__file__).parent/'fixtures/contour_single_part_thin_hole.wkb'
    expected=shapely.from_wkb(fixture.read_bytes())
    triangles=[]
    for poly in polygons(expected):
        vertices,faces=extrude_polygon(poly,0.,.4)
        triangles.append(vertices[faces])
    store=_TriangleChunks(np.concatenate(triangles))
    store.parts=[SimpleNamespace(id=1,run='y',noweave=False,bounds=None,label='yellow')]
    section=slice_part(store,1,.173926)
    assert _signature(section)==(2,1)
    assert shapely.get_precision(section)==EXPORT_GRID
    assert section.boundary.equals(expected.boundary)
    actual=layer_regions(store,.173926,0)['y']
    assert _signature(actual)==(2,1)
    assert actual.boundary.equals(expected.boundary)
    # Fixed-precision symmetric_difference alone can erase the lost sliver
    # from the comparison, falsely reporting zero error.
    assert shapely.set_precision(actual,0).symmetric_difference(expected).area==0


def test_layer_regions_still_unions_multiple_regularized_parts():
    from layerloom.contour_weave import extrude_polygon, _signature
    triangles={}
    for part_id,poly in enumerate([box(0,0,2,2),box(1,0,3,2)],1):
        vertices,faces=extrude_polygon(poly,0.,.4)
        triangles[part_id]=vertices[faces]
    class Parts:
        memory_budget_mib=1024
        parts=[SimpleNamespace(id=i,run='y',noweave=False,bounds=None,label='yellow') for i in triangles]
        def iter_triangles(self,part_id,z=None):
            yield triangles[part_id]
    actual=layer_regions(Parts(),.173926,0)['y']
    expected=box(0,0,3,2)
    assert _signature(actual)==(1,0)
    assert actual.boundary.equals(expected.boundary)
    assert actual.area==expected.area


def test_boundary_certificate_does_not_round_buffers_outward():
    from layerloom.contour_weave import EXPORT_GRID,_within
    reference=shapely.set_precision(box(0,0,1,1),EXPORT_GRID)
    # 1,311 lattice steps are .02000427246 mm: outside a .02-mm allowance.
    # A buffer retaining fixed precision rounds outward and used to pass this.
    outside=1311*EXPORT_GRID;inside=1310*EXPORT_GRID
    assert not _within(reference,shapely.set_precision(box(0,outside,1,1+outside),EXPORT_GRID),.02)
    assert _within(reference,shapely.set_precision(box(0,inside,1,1+inside),EXPORT_GRID),.02)
    assert _within(reference,reference,0)


def test_box_unequal_first_height_actual_planes_and_phase(tmp_path):
    p = source(tmp_path, [('box__PAT_ccy__', solid_box())])
    dest = tmp_path / 'woven.3mf'
    report = weave_contours(p, dest, step=.08, first_layer_color='m', options=options(tmp_path), verbose=False)
    q, manifest = color_solids(dest)
    rows = [json.loads(line) for line in dest.with_suffix('.layers.jsonl').read_text().splitlines()]
    assert [r['slice_z'] for r in rows] == pytest.approx([.08, .2, .28, .36, .44])
    assert [r['phase'] for r in rows] == [1, 2, 3, 4, 5]
    for row, token in zip(rows, ['m', 'y', 'c', 'c', 'y']):
        for c, mesh in q.items():
            assert_slice_area(mesh, row['slice_z'], 4 if c == token else 0)
    assert report['layers'] == 5
    assert report['contour_validated'] and not report['toolpaths_validated']
    assert not report['full_slice_tested']
    assert manifest['first_layer_height'] == .16
    assert not list(tmp_path.glob('layerloom_contour_*'))


def test_auto_first_color_and_source_translation(tmp_path):
    parts = [('small__PAT_cy__', solid_box(x=1, offset=(0,0,5))),
             ('large__PAT_my__', solid_box(x=3, offset=(10,0,5)))]
    p = source(tmp_path, parts); out = tmp_path/'woven.3mf'
    report = weave_contours(p, out, first_layer_color='auto', options=options(tmp_path), verbose=False)
    assert report['first_layer_color'] == 'm'
    q,_ = color_solids(out)
    assert_slice_area(q['m'], 5.08, 8)
    assert report['source_bounds'][0][2] == pytest.approx(5)


def test_slice_part_holes_vertex_plane_and_point_contact(tmp_path):
    ring = Polygon([(-3,-3),(3,-3),(3,3),(-3,3)], holes=[[(-1,-1),(-1,1),(1,1),(1,-1)]])
    ring_mesh = extruded(ring, 1.)
    left = solid_box(x=1,y=1,height=1.,offset=(-.5,-.5,0))
    right = solid_box(x=1,y=1,height=1.,offset=(.5,.5,0))
    p = source(tmp_path, [('ring__PAT_cy__', ring_mesh), ('touching__PAT_my__', trimesh.util.concatenate([left,right]))])
    with TriangleStore(p, tmp_path/'scratch', reserve_disk_bytes=0) as store:
        for z in (0., .5, .999999):
            section = slice_part(store, 1, z)
            assert section.symmetric_difference(ring).area < 1e-6
            assert len(section.interiors) == 1
        assert slice_part(store, 1, 1.).is_empty
        touch = slice_part(store, 2, .5)
        assert touch.area == pytest.approx(2)
        assert len(touch.geoms) == 2


def test_directed_slice_respects_cavity_orientation(tmp_path):
    outer = solid_box(x=4, y=4, height=2.)
    inner = solid_box(x=2, y=2, height=1., offset=(0,0,.5))
    inner.invert()
    p = source(tmp_path, [('cavity__PAT_cy__', trimesh.util.concatenate([outer, inner]))])
    with TriangleStore(p, tmp_path/'scratch', reserve_disk_bytes=0) as store:
        assert slice_part(store, 1, .25).area == pytest.approx(16)
        section = slice_part(store, 1, 1.)
        assert section.area == pytest.approx(12)
        assert len(section.interiors) == 1


def test_shared_boundary_overlap_and_neck_preserved():
    # Intentional overlap must survive rather than receive a guessed priority.
    c = box(0,0,3,2); m = box(2,0,5,2)
    source_regions = {'c': c, 'm': m}
    output, metrics = simplify_regions(source_regions,.02)
    assert output['c'].symmetric_difference(c).area == pytest.approx(0)
    assert output['m'].symmetric_difference(m).area == pytest.approx(0)
    assert output['c'].intersection(output['m']).area == pytest.approx(2)
    assert metrics['max_boundary_distance_mm'] <= .02
    neck = unary_union([box(-2,-1,-.5,1),box(.5,-1,2,1),box(-.5,-.004,.5,.004)])
    reduced,_ = simplify_regions({'c':neck},.02)
    assert reduced['c'].geom_type == 'Polygon'
    assert reduced['c'].covers(Polygon([(-.4,-.003),(.4,-.003),(.4,.003),(-.4,.003)]))


def test_cylinder_simplification_and_serialized_midsections(tmp_path):
    m = trimesh.creation.cylinder(radius=3.,height=.4,sections=96)
    m.apply_translation((0,0,.2))
    p = source(tmp_path,[('cylinder__PAT_cy__',m)]);out=tmp_path/'woven.3mf'
    report=weave_contours(p,out,step=.1,first_layer_color='off',options=options(tmp_path,first_layer_height=.1,xy_tolerance_mm=.02),verbose=False)
    q,_=color_solids(out)
    with TriangleStore(p,tmp_path/'scratch',reserve_disk_bytes=0) as store:
        for layer,z in enumerate([.05,.15,.25,.35]):
            expected=slice_part(store,1,z)
            # Independent Manifold slice from exact output coordinates.
            rings=q['cy'[layer%2]].slice(z).to_polygons()
            output=Polygon(rings[0])
            assert expected.boundary.hausdorff_distance(output.boundary)<=.020001
            assert len(output.interiors)==0
    assert report['max_contour_distance_mm']<=.02


def test_woven_hole_and_overlapping_parts_are_not_lost(tmp_path):
    hole = Polygon([(-3,-3),(3,-3),(3,3),(-3,3)], holes=[[(-1,-1),(-1,1),(1,1),(1,-1)]])
    p=source(tmp_path,[('ring__PAT_cy__',extruded(hole,.24)),('overlap__PAT_my__',solid_box(x=2,y=2,height=.24,offset=(2,0,0)))])
    out=tmp_path/'woven.3mf'
    weave_contours(p,out,step=.08,first_layer_color='off',options=options(tmp_path,first_layer_height=.08),verbose=False)
    q,_=color_solids(out)
    # Original overlaps intentionally remain in c/m layer zero.
    assert_slice_area(q['c'],.04,32)
    assert_slice_area(q['m'],.04,4)
    assert (q['c'].slice(.04)^q['m'].slice(.04)).area()==pytest.approx(4)
    # Both regions are yellow at layer one, so same-filament coverage is unioned.
    assert_slice_area(q['y'],.12,32)


def test_no_weave_copy_and_no_full_source_import(tmp_path, monkeypatch):
    import layerloom.ingest
    import layerloom.contour_weave as backend
    p=source(tmp_path,[('woven__PAT_cy__',solid_box(height=.24)),('detail__NOWEAVE__',solid_box(x=.4,y=.6,height=.3,offset=(5,2,1)))])
    def forbidden(*a,**kw): raise AssertionError('Whole-mesh ingestion is forbidden')
    monkeypatch.setattr(layerloom.ingest,'read_parts',forbidden)
    out=tmp_path/'woven.3mf'
    weave_contours(p,out,step=.08,options=options(tmp_path),verbose=False)
    q,_=load_solids(out)
    assert 'detail__NOWEAVE__' in q
    assert q['detail__NOWEAVE__'].volume()==pytest.approx(.4*.6*.3,abs=1e-7)
    assert np.allclose(q['detail__NOWEAVE__'].bounding_box(), [4.8,1.7,1,5.2,2.3,1.3])


def test_noweave_export_preserves_colors_and_extruder_provenance(tmp_path):
    from layerloom.export import write_metadata_assembly_3mf
    p = tmp_path/'source.3mf'
    write_metadata_assembly_3mf([
        ('woven__PAT_cy__', solid_box(height=.24), {}),
        ('red__NOWEAVE__', solid_box(height=.24, offset=(5, 0, 0)),
         {'source_hex': '#FF2200', 'source_extruder': '3'}),
        ('blue__NOWEAVE__', solid_box(height=.24, offset=(10, 0, 0)),
         {'source_hex': '#2233FF'}),
        ('unknown__NOWEAVE__', solid_box(height=.24, offset=(15, 0, 0)), {}),
        ('metadata_only', solid_box(height=.24, offset=(20, 0, 0)),
         {'noweave': 'true', 'source_hex': '#2211AA'}),
    ], str(p))
    out = tmp_path/'woven.3mf'
    report = weave_contours(p, out, step=.08, first_layer_color='off',
        options=options(tmp_path, first_layer_height=.08), verbose=False)
    with zipfile.ZipFile(out) as package:
        root = ET.fromstring(package.read('3D/3dmodel.model'))
        assert 'Metadata/model_settings.config' not in package.namelist()
    colors = [el.get('displaycolor') for el in root.findall('m:resources/m:basematerials/m:base', NS)]
    objects = {el.get('name'): el for el in root.findall('m:resources/m:object', NS)}
    assert all(obj.find('m:metadata', NS) is None for obj in objects.values())
    metadata = {el.get('name'): el.text for el in objects['red__NOWEAVE__'].findall('m:metadatagroup/m:metadata', NS)}
    assert metadata == {'layerloom:noweave': 'true', 'layerloom:source_hex': '#FF2200FF', 'layerloom:source_extruder': '3'}
    assert colors[int(objects['red__NOWEAVE__'].get('pindex'))] == '#FF2200FF'
    assert colors[int(objects['blue__NOWEAVE__'].get('pindex'))] == '#2233FFFF'
    assert objects['unknown__NOWEAVE__'].get('pid') is None
    with TriangleStore(out, tmp_path/'readback', reserve_disk_bytes=0) as store:
        parts = {part.label: part for part in store.parts}
        assert parts['red__NOWEAVE__'].source_color == '#FF2200FF'
        assert parts['red__NOWEAVE__'].source_extruder == '3'
        assert parts['blue__NOWEAVE__'].source_color == '#2233FFFF'
        assert parts['unknown__NOWEAVE__'].source_color is None
        assert parts['metadata_only'].noweave
        assert parts['metadata_only'].source_color == '#2211AAFF'
    assert len(report['passthrough_appearance']) == 4
    assert all(not row['printer_assignment_preserved'] for row in report['passthrough_appearance'])


def test_noweave_export_is_accepted_by_strict_lib3mf(tmp_path):
    lib3mf = pytest.importorskip('lib3mf')
    from layerloom.export import write_metadata_assembly_3mf
    p = tmp_path/'source.3mf'
    write_metadata_assembly_3mf([
        ('detail__NOWEAVE__', solid_box(height=.24),
         {'source_hex': '#2200FF', 'source_extruder': '4'}),
    ], str(p))
    out = tmp_path/'woven.3mf'
    weave_contours(p, out, step=.08, first_layer_color='off',
        options=options(tmp_path, first_layer_height=.08), verbose=False)
    wrapper = lib3mf.get_wrapper()
    model = wrapper.CreateModel()
    reader = model.QueryReader('3mf')
    reader.SetStrictModeActive(True)
    reader.ReadFromFile(str(out))
    assert reader.GetWarningCount() == 0
    objects = model.GetMeshObjects()
    assert objects.MoveNext()
    assert objects.GetCurrentMeshObject().GetName() == 'detail__NOWEAVE__'


def test_layer_budget_rejects_without_global_fallback():
    class FakeStore:
        memory_budget_mib=.001
        def iter_triangles(self,*args,**kwargs):
            tri=np.array([[[0,0,0],[1,0,1],[0,1,1]]],float)
            yield np.repeat(tri,10,axis=0)
    with pytest.raises(MemoryError,match='working set'):
        slice_part(FakeStore(),1,.5)


def test_cancellation_and_export_failure_preserve_previous_output(tmp_path,monkeypatch):
    import layerloom.contour_weave as backend
    p=source(tmp_path,[('box__PAT_cy__',solid_box(height=.24))]);out=tmp_path/'woven.3mf'
    out.write_bytes(b'previous deliverable')
    old_slice=backend.slice_part
    def cancel(*args,**kwargs):raise KeyboardInterrupt('test cancellation')
    monkeypatch.setattr(backend,'slice_part',cancel)
    with pytest.raises(KeyboardInterrupt):
        weave_contours(p,out,options=options(tmp_path),verbose=False)
    assert out.read_bytes()==b'previous deliverable'
    assert not list(tmp_path.glob('layerloom_contour_*'))
    monkeypatch.setattr(backend,'slice_part',old_slice)
    def fail_export(spools,path,manifest):
        path.write_bytes(b'partial candidate')
        raise OSError('simulated disk failure')
    monkeypatch.setattr(backend,'_export',fail_export)
    with pytest.raises(OSError,match='simulated'):
        weave_contours(p,out,options=options(tmp_path),verbose=False)
    assert out.read_bytes()==b'previous deliverable'
    assert not list(tmp_path.glob('layerloom_contour_*'))
    assert not list(tmp_path.glob('.woven_*.3mf'))


def test_disk_reserve_preserves_previous_output(tmp_path):
    p=source(tmp_path,[('box__PAT_cy__',solid_box())]);out=tmp_path/'woven.3mf'
    out.write_bytes(b'old')
    with pytest.raises(OSError,match='scratch space'):
        weave_contours(p,out,options=options(tmp_path,reserve_disk_gib=100000.),verbose=False)
    assert out.read_bytes()==b'old'


def test_nonzero_grid_placement_float32_remains_valid(tmp_path):
    # Translation changes float32 precision, so check exact placed export too.
    p=source(tmp_path,[('placed__PAT_cy__',solid_box(x=1.2,y=.8,height=.32,offset=(135.5,136.,0)))])
    out=tmp_path/'woven.3mf'
    weave_contours(p,out,options=options(tmp_path),verbose=False)
    q,_=load_solids(out)
    assert q


def test_last_layer_uses_actual_slicer_plane_on_taper(tmp_path):
    m = trimesh.creation.cone(radius=1., height=.45, sections=64)
    p = source(tmp_path, [('cone__PAT_cy__', m)])
    out = tmp_path/'woven.3mf'
    weave_contours(p,out,step=.08,first_layer_color='off',options=options(tmp_path),verbose=False)
    q,_=color_solids(out)
    expected=Manifold(Mesh64(np.asarray(m.vertices), np.asarray(m.faces,dtype=np.uint64))).slice(.44)
    actual=q['y'].slice(.44)
    assert (expected-actual).area()+(actual-expected).area()<1e-6
    rows=[json.loads(x) for x in out.with_suffix('.layers.jsonl').read_text().splitlines()]
    assert rows[-1]['slice_z']==pytest.approx(.44)


def test_no_extra_short_final_layer(tmp_path):
    p=source(tmp_path,[('short__PAT_cy__',solid_box(height=.41))])
    out=tmp_path/'woven.3mf'
    report=weave_contours(p,out,step=.08,first_layer_color='off',options=options(tmp_path),verbose=False)
    assert report['layers']==4
    rows=[json.loads(x) for x in out.with_suffix('.layers.jsonl').read_text().splitlines()]
    assert rows[-1]['slice_z']==pytest.approx(.36)


def test_plane_through_vertex_ring_and_coincident_internal_surfaces(tmp_path):
    vertices=np.array([(1,0,0),(-1,0,0),(0,1,0),(0,-1,0),(0,0,1),(0,0,-1)],float)
    octahedron=trimesh.convex.convex_hull(vertices)
    a=solid_box(x=2,y=2,height=1.,offset=(0,0,0))
    b=solid_box(x=2,y=2,height=1.,offset=(1,0,0))
    p=source(tmp_path,[('vertex_ring__PAT_cy__',octahedron),('overlap_solid__PAT_my__',trimesh.util.concatenate([a,b]))])
    with TriangleStore(p,tmp_path/'scratch',reserve_disk_bytes=0) as s:
        section=slice_part(s,1,0.)
        expected=Polygon([(1,0),(0,1),(-1,0),(0,-1)])
        assert section.symmetric_difference(expected).area<1e-7
        assert slice_part(s,2,.5).area==pytest.approx(6)


def test_open_contour_is_rejected(tmp_path):
    mesh=trimesh.Trimesh(vertices=[[0,0,0],[1,0,1],[0,1,1]],faces=[[0,1,2]],process=False)
    p=source(tmp_path,[('open__PAT_cy__',mesh)])
    with TriangleStore(p,tmp_path/'scratch',reserve_disk_bytes=0) as s:
        with pytest.raises(ValueError,match='Open/unbalanced'):
            slice_part(s,1,.5)


@pytest.mark.parametrize('first_color', ['off','c','auto'])
def test_direct_singleton_preserves_source_without_layer_amplification(tmp_path,first_color):
    mesh=solid_box(x=2,y=3,height=.4)
    p=source(tmp_path,[('solid__PAT_c__',mesh)]);out=tmp_path/'woven.3mf'
    report=weave_contours(p,out,step=.08,first_layer_color=first_color,options=options(tmp_path),verbose=False)
    assert report['direct_copy_parts']==1
    assert report['output_triangles']==len(mesh.faces)
    q,_=color_solids(out)
    assert set(q)=={'c'}
    assert q['c'].volume()==pytest.approx(mesh.volume,abs=1e-7)
    for z in [.08,.2,.28,.36]:assert_slice_area(q['c'],z,6)


def test_nearby_direct_color_part_participates_in_shared_topology(tmp_path):
    # Removing this tiny outward bend is within the XY error allowance, but
    # disconnects the two colors. The pure-color mesh must not be fast-copied
    # out of the shared topology check.
    fixed = box(0, 0, 1, 1)
    woven = Polygon([(1.01, 0), (2, 0), (2, 1), (1.01, 1),
                     (1.01, .6), (.999, .5), (1.01, .4)])
    p = source(tmp_path, [('fixed__PAT_c__', extruded(fixed, .24)),
                          ('woven__PAT_my__', extruded(woven, .24))])
    out = tmp_path/'woven.3mf'
    report = weave_contours(p, out, step=.08, first_layer_color='off',
        options=options(tmp_path, first_layer_height=.08, xy_tolerance_mm=.02), verbose=False)
    assert report['direct_copy_parts'] == 0
    q, _ = color_solids(out)
    c, m = q['c'].slice(.04), q['m'].slice(.04)
    assert (c ^ m).area() > 0
    assert len((c + m).to_polygons()) == 1


@pytest.mark.parametrize('gap,expected_copies', [(0., 0), (.01, 0), (.1, 1)])
def test_direct_copy_requires_clearance_from_other_woven_bounds(tmp_path, gap, expected_copies):
    p = source(tmp_path, [('fixed__PAT_c__', extruded(box(0, 0, 1, 1), .24)),
                          ('woven__PAT_my__', extruded(box(1 + gap, 0, 2 + gap, 1), .24))])
    report = weave_contours(p, tmp_path/'woven.3mf', step=.08, first_layer_color='off',
        options=options(tmp_path, first_layer_height=.08, xy_tolerance_mm=.02), verbose=False)
    assert report['direct_copy_parts'] == expected_copies


def test_direct_singleton_respects_conflicting_first_color(tmp_path):
    p=source(tmp_path,[('solid__PAT_c__',solid_box(x=2,y=3,height=.4))]);out=tmp_path/'woven.3mf'
    report=weave_contours(p,out,step=.08,first_layer_color='m',options=options(tmp_path),verbose=False)
    assert report['direct_copy_parts']==0
    q,_=color_solids(out)
    assert set(q)=={'c','m'}
    assert_slice_area(q['m'],.08,6)
    assert_slice_area(q['c'],.08,0)
    for z in [.2,.28,.36]:assert_slice_area(q['c'],z,6)


def test_combined_material_topology_gate_and_cleanup_precision():
    from layerloom.contour_weave import _regions_within, _numerical_cleanup, EXPORT_GRID
    reference = {'c': box(0, 0, 1, 1), 'm': box(1.01, 0, 2.01, 1)}
    moved = {'c': reference['c'], 'm': box(.995, 0, 1.995, 1)}
    # Each material remains a rectangle displaced less than .02 mm, but joining
    # two disconnected components changes the combined printable outline.
    assert not _regions_within(reference, moved, .02)
    original = shapely.set_precision(box(.001, .001, 2.001, 2.001), EXPORT_GRID)
    cleaned, area, count = _numerical_cleanup(original)
    assert cleaned.equals(original)
    assert shapely.get_precision(cleaned) == EXPORT_GRID
    assert (area, count) == (0, 0)


def test_noweave_neighbor_constrains_woven_boundary(tmp_path):
    fixed = box(0, 0, 1, 1)
    touching = Polygon([(1.01,0),(2,0),(2,1),(1.01,1),
                        (1.01,.6),(.9999,.5),(1.01,.4)])
    p = source(tmp_path, [('detail__NOWEAVE__', extruded(fixed, .4)),
                          ('woven__PAT_my__', extruded(touching, .4))])
    out = tmp_path/'constrained.3mf'
    weave_contours(p, out, step=.08, first_layer_color='off',
                   options=options(tmp_path, xy_tolerance_mm=.02), verbose=False)
    with TriangleStore(out, tmp_path/'verify', reserve_disk_bytes=0) as store:
        sections = [slice_part(store, part.id, .08) for part in store.parts]
        combined = unary_union(sections)
        # Removing the shallow spur would detach the colored part from the
        # unchanged detail. Their original positive-area contact must survive.
        assert combined.geom_type == 'Polygon'
        present = [s for s in sections if not s.is_empty]
        assert present[0].intersection(present[1]).area > 0


def test_many_small_parts_budget_checked_before_union(monkeypatch):
    from types import SimpleNamespace
    import layerloom.contour_weave as backend
    store = SimpleNamespace(memory_budget_mib=.02,
                            parts=[SimpleNamespace(id=i, noweave=False, run='cy', bounds=None) for i in range(10)])
    monkeypatch.setattr(backend, 'slice_part', lambda *args: box(0, 0, 1, 1))
    with pytest.raises(MemoryError, match='Combined layer sections'):
        layer_regions(store, .04, 0)
