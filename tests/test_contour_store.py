from pathlib import Path
import shutil
import zipfile

import numpy as np
import pytest

from layerloom.contour_store import TriangleStore

CORE = 'http://schemas.microsoft.com/3dmanufacturing/core/2015/02'
PROD = 'http://schemas.microsoft.com/3dmanufacturing/production/2015/06'
IDENTITY = '1 0 0 0 1 0 0 0 1 0 0 0'


def mesh(oid='1', label='mesh__PAT_cmy__', vertices=None, faces=None, metadata=''):
    vertices = vertices if vertices is not None else [(0, 0, 0), (1, 0, 0), (0, 1, 1)]
    faces = faces if faces is not None else [(0, 1, 2)]
    return (f'<object id="{oid}" name="{label}">{metadata}<mesh><vertices>' +
            ''.join(f'<vertex x="{x}" y="{y}" z="{z}"/>' for x, y, z in vertices) +
            '</vertices><triangles>' + ''.join(f'<triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in faces) +
            '</triangles></mesh></object>')


def model(objects, build='<item objectid="1"/>', unit='millimeter'):
    return f'<model xmlns="{CORE}" xmlns:p="{PROD}" unit="{unit}"><resources>{objects}</resources><build>{build}</build></model>'


def package(path, xml, extra=None):
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('3D/3dmodel.model', xml)
        for name, text in (extra or {}).items(): z.writestr(name, text)
    return path


def test_float64_transform_index_and_cleanup(tmp_path):
    x = 1.1234567890123
    path = package(tmp_path / 'source.3mf', model(mesh(vertices=[(x, 0, 0), (x+1, 0, 0), (x, 1, 1)]),
        '<item objectid="1" transform="1 0 0 0 1 0 0 0 1 10 20 30"/>'))
    with TriangleStore(path, tmp_path / 'scratch', reserve_disk_bytes=0) as store:
        assert store.triangle_count == 1
        part = store.parts[0]
        assert part.run == 'cmy'
        tris = np.concatenate(list(store.iter_triangles(part.id, 30.5, 1)))
        assert tris.shape == (1, 3, 3)
        assert tris[0, 0, 0] == pytest.approx(x+10, abs=1e-13)
        assert list(store.iter_triangles(part.id, 29.9)) == []
        assert list(store.iter_triangles(part.id, 31.1)) == []
        assert len(list(store.iter_triangles(part.id, 30))) == 1
        assert len(list(store.iter_triangles(part.id, 31))) == 1
        assert np.allclose(store.bounds, [[x+10, 20, 30], [x+11, 21, 31]])
        directory = store._directory
        assert directory.exists()
        assert len(store.source_sha256) == 64
    assert not directory.exists()
    with pytest.raises(RuntimeError, match='closed'):
        list(store.iter_triangles(1))


def test_nested_external_units_and_inherited_recipe(tmp_path):
    parent = '<object id="5" name="assembly__PAT_ccy__"><components><component objectid="9" p:path="/3D/child.model" transform="1 0 0 0 1 0 0 0 1 1 0 0"/></components></object>'
    child = '<object id="9"><components><component objectid="10" transform="1 0 0 0 1 0 0 0 1 0 1 0"/></components></object>' + mesh('10', 'leaf')
    p = package(tmp_path / 'external.3mf', model(parent, '<item objectid="5" transform="1 0 0 0 1 0 0 0 1 0 0 1"/>', 'inch'),
                {'3D/child.model': model(child, '', 'centimeter')})
    with TriangleStore(p, tmp_path / 'scratch', reserve_disk_bytes=0) as store:
        assert store.parts[0].run == 'ccy'
        assert np.allclose(store.bounds, [[25.4, 10, 25.4], [35.4, 20, 35.4]])


def test_reflection_winding_and_source_iterators(tmp_path):
    p = package(tmp_path / 'reflection.3mf', model(mesh(label='keep__NOWEAVE__'),
         '<item objectid="1" transform="-1 0 0 0 1 0 0 0 1 0 0 0"/>'))
    with TriangleStore(p, tmp_path / 'scratch', reserve_disk_bytes=0) as store:
        part = store.parts[0]
        assert part.noweave and part.run is None
        vertices = np.concatenate(list(store.iter_vertices(1, chunk_size=2)))
        faces = np.concatenate(list(store.iter_faces(1, chunk_size=1)))
        triangles = np.concatenate(list(store.iter_triangles(1)))
        assert faces.tolist() == [[0, 2, 1]]
        assert np.array_equal(vertices[faces], triangles)


def test_metadata_recipe_vendor_name_and_build_fallback(tmp_path):
    objects = mesh(label='plain', metadata='<metadata name="layerloom:stack_token">ym</metadata>')
    p = package(tmp_path / 'metadata.3mf', model(objects))
    with TriangleStore(p, tmp_path / 'scratch', reserve_disk_bytes=0) as store:
        assert store.parts[0].run == 'ym'
    p = package(tmp_path / 'vendor.3mf', model('<object id="5"><components><component objectid="1"/></components></object>'+mesh(label='plain'), '<item objectid="5"/>'),
        {'Metadata/model_settings.config': '<config><object id="5"><part id="1"><metadata key="name" value="peptide__PAT_ccy__"/></part></object></config>'})
    with TriangleStore(p, tmp_path / 'scratch', reserve_disk_bytes=0) as store:
        assert store.parts[0].run == 'ccy'
        assert store.parts[0].label == 'peptide__PAT_ccy__'
    p = package(tmp_path / 'fallback.3mf', model(mesh(), ''))
    with TriangleStore(p, tmp_path / 'scratch', reserve_disk_bytes=0) as store:
        assert len(store.parts) == 1


def test_repeated_instances_and_chunked_z_queries(tmp_path):
    vertices = []; faces = []
    for i in range(51):
        vertices += [(0, 0, i), (1, 0, i), (0, 1, i+1)]
        faces.append((3*i, 3*i+1, 3*i+2))
    build = '<item objectid="1"/><item objectid="1" transform="1 0 0 0 1 0 0 0 1 100 0 0"/>'
    p = package(tmp_path / 'instances.3mf', model(mesh(vertices=vertices, faces=faces), build))
    with TriangleStore(p, tmp_path / 'scratch', reserve_disk_bytes=0) as store:
        assert len(store.parts) == 2 and store.triangle_count == 102
        assert all(len(chunk) <= 7 for chunk in store.iter_triangles(1, chunk_size=7))
        for part in store.parts:
            for z in [.0, .5, 20, 20.5, 51, 52]:
                all_tri = np.concatenate(list(store.iter_triangles(part.id)))
                expected = all_tri[(all_tri[:,:,2].min(1)<=z) & (all_tri[:,:,2].max(1)>=z)]
                queried = list(store.iter_triangles(part.id, z, chunk_size=1))
                actual = np.concatenate(queried) if queried else np.empty((0,3,3))
                assert np.array_equal(actual, expected)


def test_model_xml_is_not_read_as_a_whole(tmp_path, monkeypatch):
    p = package(tmp_path / 'source.3mf', model(mesh()))
    original = zipfile.ZipFile.read
    def guarded(self, name, *args, **kwargs):
        assert not str(name).endswith('.model'), 'whole-model read forbidden'
        return original(self, name, *args, **kwargs)
    monkeypatch.setattr(zipfile.ZipFile, 'read', guarded)
    with TriangleStore(p, tmp_path / 'scratch', reserve_disk_bytes=0) as store:
        assert store.triangle_count == 1


@pytest.mark.parametrize('xml,match', [
    (model(mesh(faces=[(0,1,999)])), 'out-of-range'),
    (model(mesh(faces=[(0,1,1)])), 'Degenerate'),
    (model(mesh(vertices=[(float('nan'),0,0),(1,0,0),(0,1,1)])), 'Non-finite'),
    (model('<object id="1"><components><component objectid="1"/></components></object>'), 'Cycle'),
    (model('<object id="1"><components><component objectid="9"/></components></object>'), 'Missing referenced'),
    (model(mesh(), '<item objectid="1" transform="1 2 3"/>'), 'twelve'),
    (model(mesh(), unit='kilometer'), 'Unsupported 3MF unit'),
    (model('<object id="1"><mesh><beamlattice/></mesh></object>'), 'Unsupported'),
])
def test_invalid_input_leaves_no_private_spool(tmp_path, xml, match):
    p = package(tmp_path / 'invalid.3mf', xml)
    scratch = tmp_path / 'scratch'
    with pytest.raises(ValueError, match=match):
        TriangleStore(p, scratch, reserve_disk_bytes=0)
    assert list(scratch.iterdir()) == []


def test_disk_reserve_and_bad_budget(tmp_path):
    p = package(tmp_path / 'source.3mf', model(mesh()))
    with pytest.raises(OSError, match='scratch disk'):
        TriangleStore(p, tmp_path / 'scratch', reserve_disk_bytes=shutil.disk_usage(tmp_path).free+1)
    with pytest.raises(ValueError, match='memory budget'):
        TriangleStore(p, tmp_path / 'scratch', memory_budget_mib=1)


@pytest.mark.parametrize('printable', ['0', 'false', 'no'])
def test_explicit_nonprintable_build_does_not_restore_resource_meshes(tmp_path, printable):
    p = package(tmp_path / 'nonprintable.3mf', model(mesh(),
        f'<item objectid="1" printable="{printable}"/>'))
    scratch = tmp_path / 'scratch'
    with pytest.raises(ValueError, match='no printable mesh parts'):
        TriangleStore(p, scratch, reserve_disk_bytes=0)
    assert list(scratch.iterdir()) == []


def test_mixed_build_only_includes_printable_items(tmp_path):
    objects = mesh('1', 'excluded__PAT_c__') + mesh('2', 'included__PAT_m__')
    p = package(tmp_path / 'mixed.3mf', model(objects,
        '<item objectid="1" printable="0"/><item objectid="2" printable="1"/>'))
    with TriangleStore(p, tmp_path / 'scratch', reserve_disk_bytes=0) as store:
        assert [part.label for part in store.parts] == ['included__PAT_m__']
        assert store.triangle_count == 1


def test_noweave_uniform_source_and_material_colors(tmp_path):
    materials = ('<basematerials id="8"><base displaycolor="#FF0000FF"/>'
                 '<base displaycolor="#0000FFFF"/></basematerials>')
    objects = mesh(label='detail__NOWEAVE__', metadata='<metadata name="layerloom:source_hex">#ff2200</metadata>')
    objects = objects.replace('<object ', '<object pid="8" pindex="1" ', 1)
    p = package(tmp_path/'source.3mf', model(materials + objects))
    with TriangleStore(p, tmp_path/'scratch', reserve_disk_bytes=0) as store:
        assert store.parts[0].source_color == '#FF2200FF'
    # Uniform face overrides can also preserve a base-material display color.
    objects = mesh(label='detail__NOWEAVE__').replace('<triangle ', '<triangle pid="8" p1="1" ', 1)
    p = package(tmp_path/'face.3mf', model(materials + objects))
    with TriangleStore(p, tmp_path/'scratch', reserve_disk_bytes=0) as store:
        assert store.parts[0].source_color == '#0000FFFF'
    # An ordinary object-level material needs no face override.
    objects = mesh(label='detail__NOWEAVE__').replace('<object ', '<object pid="8" pindex="0" ', 1)
    p = package(tmp_path/'object.3mf', model(materials + objects))
    with TriangleStore(p, tmp_path/'scratch', reserve_disk_bytes=0) as store:
        assert store.parts[0].source_color == '#FF0000FF'


def test_noweave_vendor_extruder_is_provenance(tmp_path):
    objects = '<object id="5"><components><component objectid="1"/></components></object>' + mesh(label='detail__NOWEAVE__')
    config = '<config><object id="5"><metadata key="extruder" value="1"/><part id="1"><metadata key="extruder" value="4"/></part></object></config>'
    p = package(tmp_path/'source.3mf', model(objects, '<item objectid="5"/>'),
                {'Metadata/model_settings.config': config})
    with TriangleStore(p, tmp_path/'scratch', reserve_disk_bytes=0) as store:
        assert store.parts[0].source_extruder == '4'
        assert store.parts[0].source_color is None


def test_inherited_noweave_keeps_leaf_name_and_appearance(tmp_path):
    objects = ('<object id="5" name="parent__NOWEAVE__"><components>'
               '<component objectid="1"/></components></object>')
    objects += mesh(label='unchanged_leaf', metadata='<metadata name="layerloom:source_hex">#1100CC</metadata>')
    p = package(tmp_path/'source.3mf', model(objects, '<item objectid="5"/>'))
    with TriangleStore(p, tmp_path/'scratch', reserve_disk_bytes=0) as store:
        assert store.parts[0].noweave
        assert store.parts[0].label == 'unchanged_leaf'
        assert store.parts[0].source_color == '#1100CCFF'


@pytest.mark.parametrize('properties', ['pid="8" p1="0" p2="1"', 'pid="999" p1="0"'])
def test_noweave_unpreservable_face_properties_fail_explicitly(tmp_path, properties):
    materials = '<basematerials id="8"><base displaycolor="#FF0000FF"/><base displaycolor="#0000FFFF"/></basematerials>'
    objects = mesh(label='detail__NOWEAVE__').replace('<triangle ', f'<triangle {properties} ', 1)
    p = package(tmp_path/'source.3mf', model(materials + objects))
    with pytest.raises(ValueError, match='NOWEAVE per-face material properties'):
        TriangleStore(p, tmp_path/'scratch', reserve_disk_bytes=0)


def test_missing_child_and_invalid_indices(tmp_path):
    p = package(tmp_path / 'missing.3mf', model('<object id="1"><components><component objectid="2" p:path="/3D/missing.model"/></components></object>'))
    with pytest.raises(ValueError, match='Missing referenced'):
        TriangleStore(p, tmp_path / 'scratch', reserve_disk_bytes=0)
    p = package(tmp_path / 'valid.3mf', model(mesh()))
    with TriangleStore(p, tmp_path / 'scratch', reserve_disk_bytes=0) as s:
        with pytest.raises(ValueError, match='chunk_size'): list(s.iter_triangles(1, chunk_size=0))
        with pytest.raises(ValueError, match='finite'): list(s.iter_triangles(1, z=float('inf')))
        with pytest.raises(KeyError): list(s.iter_triangles(2))
