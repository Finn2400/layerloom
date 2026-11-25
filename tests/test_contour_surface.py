from __future__ import annotations
import numpy as np
import pytest
import shapely
import trimesh
from shapely.geometry import Polygon,box,Point,MultiPolygon
from layerloom.contour_surface import LayerSurfaceEmitter


class MemorySink:
    def __init__(self):self.v=[];self.f=[];self.vertices=0
    def append_vertices(self,v):
        start=self.vertices;self.vertices+=len(v);self.v.extend(v.tolist());return start
    def append_faces(self,f):self.f.extend(f.tolist())
    def mesh(self):return trimesh.Trimesh(np.asarray(self.v),np.asarray(self.f),process=False)


def build(profiles,step=.2):
    sink=MemorySink();emitter=LayerSurfaceEmitter(sink)
    for i,p in enumerate(profiles):emitter.push(p,i*step,(i+1)*step)
    emitter.finish();return sink.mesh(),emitter


def assert_sections(mesh,profiles,step=.2):
    from manifold3d import Manifold,Mesh64
    q=Manifold(Mesh64(mesh.vertices,np.asarray(mesh.faces,dtype=np.uint64)))
    assert str(q.status())=='Error.NoError'
    for i,p in enumerate(profiles):
        polygons=q.slice((i+.5)*step).to_polygons()
        # CrossSection.area includes signed holes.
        assert abs(q.slice((i+.5)*step).area()-p.area)<.001


def test_equal_boxes_remove_internal_caps():
    profiles=[box(0,0,1,1)]*5
    mesh,emitter=build(profiles)
    assert mesh.is_volume
    # Four side quads per layer + only the two exposed caps.
    assert len(mesh.faces)==5*8+4
    assert len(mesh.faces)<5*12
    assert not emitter.coords and not emitter.planes
    assert_sections(mesh,profiles)


@pytest.mark.parametrize('profiles',[
    [box(0,0,2,2),box(.5,.5,2.5,2.5),box(.25,.25,2.25,2.25)],
    [box(0,0,3,3).difference(box(1,1,2,2)),box(0,0,3,3).difference(box(.75,.75,2.25,2.25))],
    [MultiPolygon([box(0,0,1,1),box(2,0,3,1)]),box(0,0,3,1),MultiPolygon([box(0,0,1,1),box(2,0,3,1)])],
    [box(0,0,1,1),box(1,1,2,2)],
    [box(0,0,1,1),box(1,0,2,1)],
    [box(0,0,1,1),Polygon(),box(0,0,1,1)],
    [Polygon([(0,0),(2,0),(1,2)]),Polygon([(0,1),(2,1),(1,-1)])],
])
def test_transitions_holes_bridges_and_contacts(profiles):
    mesh,emitter=build(profiles)
    assert mesh.is_volume
    assert_sections(mesh,profiles)
    assert not emitter.coords


def test_collinear_interface_nodes_no_tjunctions():
    a=Polygon([(0,0),(1,0),(2,0),(2,2),(0,2)])
    b=Polygon([(0,0),(2,0),(2,1),(2,2),(0,2),(0,1)])
    mesh,_=build([a,b]);assert mesh.is_volume
    assert_sections(mesh,[a,b])


def test_budget_and_bad_intervals():
    emitter=LayerSurfaceEmitter(MemorySink(),max_interface_vertices=3)
    with pytest.raises(MemoryError):emitter.push(box(0,0,1,1),0,.2)
    emitter=LayerSurfaceEmitter(MemorySink());emitter.push(box(0,0,1,1),0,.2)
    with pytest.raises(ValueError,match='contiguous'):emitter.push(box(0,0,1,1),.3,.5)


def test_frontier_does_not_grow_with_layers():
    _,short=build([box(0,0,1,1)]*4)
    _,long=build([box(0,0,1,1)]*40)
    assert long.peak_frontier_vertices==short.peak_frontier_vertices


def test_pinched_hole_rejected_instead_of_exporting_nonmanifold_frontier():
    # OGC-valid rings may touch at one point. Coordinate-only frontier welding
    # cannot yet distinguish their incident surface fans. This is a deliberate
    # capability gate, not a successful supported example.
    profile=Polygon([(0,0),(3,0),(3,3),(0,3)],
                    [[(0,1.5),(1,1),(1,2)]])
    assert profile.is_valid
    with pytest.raises(ValueError,match='interface vertex link'):
        build([profile,profile])
