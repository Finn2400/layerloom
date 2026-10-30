import numpy as np
import pytest
import trimesh

from layerloom.paint_sub_layer import prepare_sub_layer_mesh


def test_tiny_solid_is_retained_with_exact_topology_and_shared_translation():
    mesh=trimesh.creation.box(extents=(.0001,.0002,.0003))
    mesh.apply_translation((120,180,37))
    source=mesh.copy()
    result,audit=prepare_sub_layer_mesh(mesh,[-120,-180,0])
    assert np.array_equal(result.faces,source.faces)
    assert np.array_equal(mesh.vertices,source.vertices)
    assert audit['output_triangles']==len(source.faces)
    assert audit['placed_centered_signed_volume_mm3']>0
    assert not any(audit['encoded_defects'].values())
    assert not any(audit['placed_defects'].values())
    assert audit['maximum_placed_float32_displacement_mm']<=.001


def test_open_shell_fails_instead_of_disappearing():
    mesh=trimesh.creation.box(extents=(.001,.001,.001))
    mesh.update_faces(np.arange(len(mesh.faces))!=0)
    with pytest.raises(ValueError,match='closed'):
        prepare_sub_layer_mesh(mesh,[0,0,0])


def test_displacement_limit_is_enforced():
    mesh=trimesh.creation.box(extents=(.000001,.000001,.000001))
    with pytest.raises(ValueError,match='displacement|bounded'):
        prepare_sub_layer_mesh(mesh,[0,0,0],max_displacement_mm=1e-10)


def test_thin_positive_shell_survives_double_rounding_without_inversion():
    # A six-vertex, eight-face sliver from a precision-grid intersection. An
    # unnecessary inward collision repair would invert it.
    vertices=np.array([[10,4,2],[10,0,0],[0,4,1],[8,8,3],[16,8,4],[10,8,3]],dtype=float)/262144
    vertices+=np.array([118.34294891357422,159.6875762939453,36.721397399902344])
    faces=np.array([[0,2,1],[0,3,2],[0,1,4],[0,4,5],
                    [0,5,3],[1,2,3],[1,5,4],[1,3,5]])
    mesh=trimesh.Trimesh(vertices,faces,process=False)
    result,audit=prepare_sub_layer_mesh(mesh,[-133.16685104370117,-178.39824676513672,0])
    assert np.array_equal(result.faces,faces)
    assert np.array_equal(mesh.vertices,vertices)
    assert audit['maximum_vertex_displacement_mm']<1.01e-6
    assert audit['encoded_centered_signed_volume_mm3']>0
    assert audit['placed_centered_signed_volume_mm3']>0
    assert not any(audit['encoded_defects'].values())
    assert not any(audit['placed_defects'].values())


@pytest.mark.parametrize('origin,offsets,faces',[
    ([117.12285614013672,159.9327392578125,37.2369270324707],
     [[4,0,2],[2,0,1],[0,0,2],[2,4,7],[4,0,0],[8,4,2],[6,4,5]],
     [[0,2,1],[0,1,4],[0,3,2],[0,4,5],[0,5,6],[0,6,3],[1,2,3],[4,6,5],[1,3,4],[3,6,4]]),
    ([117.30984497070312,160.34774780273438,37.664615631103516],
     [[4,0,2],[2,0,1],[4,0,0],[0,0,2],[2,4,5],[4,0,3],[6,0,2]],
     [[0,3,1],[0,1,2],[0,2,6],[0,4,3],[0,5,4],[0,6,5],[1,3,4],[1,4,5],[2,5,6],[1,5,2]])])
def test_actual_xml_rounding_preserves_thin_shell(tmp_path,origin,offsets,faces):
    import zipfile
    from xml.etree import ElementTree as ET
    from layerloom.export import LeafMeshSpec,write_orca_multipart_3mf
    from layerloom.paint_sub_layer import _signed_volume
    from layerloom.surface_field_weave import mesh_defects

    # Direct float32 placement passes for these shells, but decimal XML rounding
    # changes a later float32 tie and collapses a face unless explicitly checked.
    vertices=np.asarray(offsets,dtype=float)/262144+origin
    mesh=trimesh.Trimesh(vertices,faces,process=False)
    result,audit=prepare_sub_layer_mesh(mesh,[-133.16685104370117,-178.39824676513672,0])
    assert audit['repair_needed']
    path=tmp_path/'tiny.3mf'
    write_orca_multipart_3mf([LeafMeshSpec('tiny',result,{'stack_token':'c'})],str(path))
    ns={'m':'http://schemas.microsoft.com/3dmanufacturing/core/2015/02'}
    with zipfile.ZipFile(path) as archive:
        root=ET.fromstring(archive.read('3D/3dmodel.model'))
        child=ET.fromstring(archive.read('3D/Objects/object_1.model'))
    encoded=np.array([[float(v.get(axis)) for axis in ('x','y','z')]
                      for v in child.findall('.//m:vertex',ns)]).astype(np.float32).astype(float)
    actual_faces=np.array([[int(t.get(axis)) for axis in ('v1','v2','v3')]
                           for t in child.findall('.//m:triangle',ns)])
    transform=root.find('.//m:component',ns).get('transform')
    center=np.array(list(map(float,transform.split()))).reshape(4,3)[3]
    placed=(encoded+center+[128,128,0]).astype(np.float32).astype(float)
    assert np.array_equal(actual_faces,faces)
    for points in (encoded,placed):
        actual=trimesh.Trimesh(points,actual_faces,process=False)
        defects=mesh_defects(actual)
        assert all(value==0 for key,value in defects.items() if key!='not_closed_oriented_volume')
        assert actual.is_watertight and actual.is_winding_consistent
        assert _signed_volume(points,actual_faces)>0
