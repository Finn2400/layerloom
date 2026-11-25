"""Indexed, layer-frontier surface construction without a whole-solid Boolean.

Unintegrated experimental building block: valid polygons with touching hole
rings currently fail the interface-link check and are NOT supported. The normal
weaver must retain closed-band export until fan-aware frontier splitting exists.

A sink implements ``append_vertices(array) -> first
index`` and ``append_faces(global_indices)``. The emitter retains two interface
maps, not all emitted vertices or faces. Call ``finish`` before accepting output.
Point/edge-only inter-layer contacts are distinct topological components. Every
retired interface must have a single closed oriented vertex link at each vertex;
unsupported pinches raise instead of exporting an invalid solid.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
import numpy as np
import shapely
from shapely.geometry import Polygon, Point, LineString
from shapely.geometry.polygon import orient
from shapely.ops import unary_union
from shapely.strtree import STRtree


def _polygons(g):
    if g.is_empty:return []
    if g.geom_type=='Polygon':return [orient(g,sign=1.)]
    return [p for child in getattr(g,'geoms',()) for p in _polygons(child)]


def _rings(p):return [p.exterior,*p.interiors]


def _key(xy):return tuple(float(v) for v in xy)


@dataclass
class _Group:
    nodes: np.ndarray
    tree: object
    keys: list
    ids: dict = field(default_factory=dict)


class _Interface:
    def __init__(self, owner, previous, current, z):
        self.owner=owner;self.z=float(z)
        self.previous=_polygons(previous);self.current=_polygons(current)
        all_polys=self.previous+self.current;npast=len(self.previous)
        parents=list(range(len(all_polys)))
        def root(i):
            while parents[i]!=i:
                parents[i]=parents[parents[i]];i=parents[i]
            return i
        # Volume connectivity requires positive area; an edge/point touch is
        # deliberately not welded into a nonmanifold edge/vertex.
        if self.current:
            tree=STRtree(self.current)
            for i,a in enumerate(self.previous):
                for j in tree.query(a):
                    j=int(j)
                    if a.intersection(self.current[j]).area>0:
                        x,y=root(i),root(npast+j)
                        if x!=y:parents[y]=x
        self.previous_group=[root(i) for i in range(npast)]
        self.current_group=[root(npast+i) for i in range(len(self.current))]
        self.groups={};self.caps=[];self.links={};self.vertex_ids=[]
        for group_id in sorted({root(i) for i in range(len(all_polys))}):
            ps=[p for i,p in enumerate(self.previous) if root(i)==group_id]
            cs=[p for i,p in enumerate(self.current) if root(npast+i)==group_id]
            p=unary_union(ps) if ps else Polygon();c=unary_union(cs) if cs else Polygon()
            top=shapely.difference(p,c,grid_size=owner.grid);bottom=shapely.difference(c,p,grid_size=owner.grid)
            lines=[poly.boundary for poly in ps+cs]
            lines.extend(poly.boundary for poly in _polygons(top)+_polygons(bottom))
            network=shapely.union_all(lines,grid_size=owner.grid)
            coords=shapely.get_coordinates(network)
            coords=np.unique(coords,axis=0)
            if len(coords)>owner.max_interface_vertices:
                raise MemoryError('Layer interface exceeds vertex budget')
            keys=[owner.key(xy) for xy in coords]
            points=[Point(xy) for xy in coords]
            self.groups[group_id]=_Group(coords,STRtree(points),keys)
            self.caps.extend((poly,group_id,True) for poly in _polygons(top))
            self.caps.extend((poly,group_id,False) for poly in _polygons(bottom))

    def segment_nodes(self, group_id, a, b):
        group=self.groups[group_id];a=np.asarray(a);b=np.asarray(b);direction=b-a
        length2=float(direction@direction)
        if length2==0:raise ValueError('Zero-length contour edge')
        edge=LineString([a,b]);epsilon=self.owner.grid*.01
        selected=[]
        for i in group.tree.query(edge.buffer(epsilon,cap_style='square')):
            xy=group.nodes[int(i)];u=float((xy-a)@direction/length2)
            if -1e-10<=u<=1+1e-10 and edge.distance(Point(xy))<=epsilon:
                selected.append((min(1.,max(0.,u)),group.keys[int(i)]))
        # All original endpoints belong to the interface, including those not
        # exposed by a cap. Explicitly retain them if GEOS noding omitted one.
        selected += [(0.,self.owner.key(a)),(1.,self.owner.key(b))]
        by_key={key:u for u,key in selected}
        return sorted((u,key) for key,u in by_key.items())

    def vertex(self, group_id, key):
        group=self.groups[group_id]
        if key not in group.ids:
            coords=np.array([[key[0],key[1],self.z]],dtype=np.float64)
            i=int(self.owner.sink.append_vertices(coords));group.ids[key]=i
            self.owner.coords[i]=coords[0];self.owner.planes[i]=self
            self.vertex_ids.append(i)
        return group.ids[key]

    def cap_polygon(self, poly, group_id):
        rings=[]
        for ring in _rings(poly):
            coords=list(ring.coords);out=[]
            for a,b in zip(coords,coords[1:]):
                out.extend(key for _,key in self.segment_nodes(group_id,a,b)[:-1])
            rings.append(out)
        result=Polygon(rings[0],rings[1:])
        if not result.is_valid or result.is_empty:
            raise ValueError('Noded cap polygon is invalid')
        return result

    def emit_caps(self):
        for poly,group_id,up in self.caps:
            noded=self.cap_polygon(poly,group_id)
            triangles=shapely.constrained_delaunay_triangles(noded)
            faces=[]
            for triangle in triangles.geoms:
                xy=np.asarray(triangle.exterior.coords)[:3]
                cross=np.cross(xy[1]-xy[0],xy[2]-xy[0])
                if (cross>0)!=up:xy=xy[::-1]
                faces.append([self.vertex(group_id,self.owner.key(point)) for point in xy])
            if faces:self.owner.emit_faces(np.asarray(faces,dtype=np.int64))

    def retire(self):
        # The link of each closed 2-manifold vertex is exactly one oriented
        # cycle. Two coincident solids touching at a vertex must not share ID.
        for vertex,edges in self.links.items():
            next_node={};incoming={}
            for a,b in edges:
                if a in next_node or b in incoming:
                    raise ValueError('Nonmanifold interface vertex link')
                next_node[a]=b;incoming[b]=a
            if set(next_node)!=set(incoming):
                raise ValueError('Open interface vertex link (possible cap T-junction)')
            if not next_node:continue
            first=next(iter(next_node));node=first;visited=set()
            while node not in visited:
                visited.add(node);node=next_node[node]
            if node!=first or len(visited)!=len(next_node):
                raise ValueError('Disconnected interface vertex link (point-contact pinch)')
        for vertex in self.vertex_ids:
            self.owner.coords.pop(vertex,None);self.owner.planes.pop(vertex,None)
        self.links.clear();self.groups.clear();self.caps.clear()


class LayerSurfaceEmitter:
    """Construct a union surface for piecewise-constant layer profiles.

    Profiles are snapped to ``grid`` first, and ``max_snap_error_mm`` bounds the
    resulting two-way boundary distance. The caller must account for this tiny
    encoding allowance in its overall contour budget. Rejecting a topology is
    intentional: callers can discard the candidate and retain closed-band mode.
    """
    def __init__(self,sink,*,grid=2**-16,max_snap_error_mm=0.00003,max_interface_vertices=200000):
        if not math.isfinite(grid) or grid<=0:raise ValueError('Grid must be positive')
        self.sink=sink;self.grid=float(grid);self.max_snap_error_mm=max_snap_error_mm
        self.max_interface_vertices=max_interface_vertices
        self.pending=None;self.coords={};self.planes={};self.finished=False
        self.triangles=0;self.layer_count=0;self.peak_frontier_vertices=0

    def key(self,xy):
        return _key(np.rint(np.asarray(xy)/self.grid)*self.grid)

    def _prepare(self,g):
        if not g.is_valid:raise ValueError('Input layer polygon is invalid')
        if g.is_empty:return Polygon()
        result=shapely.set_precision(g,self.grid)
        original=_polygons(g);snapped=_polygons(result)
        if (len(original),sum(len(p.interiors) for p in original))!=(len(snapped),sum(len(p.interiors) for p in snapped)):
            raise ValueError('Grid snapping changes layer topology')
        if not result.boundary.covered_by(g.boundary.buffer(self.max_snap_error_mm,quad_segs=16)) or not g.boundary.covered_by(result.boundary.buffer(self.max_snap_error_mm,quad_segs=16)):
            raise ValueError('Grid snapping exceeds contour error allowance')
        return result

    def emit_faces(self,faces):
        if not len(faces):return
        points=np.array([[self.coords[int(v)] for v in f] for f in faces])
        for p in [points,points.astype(np.float32).astype(float)]:
            cross=np.cross(p[:,1]-p[:,0],p[:,2]-p[:,0])
            if np.any(np.einsum('ij,ij->i',cross,cross)==0):
                raise ValueError(f'Collapsed surface triangle at serialized precision: {p[np.flatnonzero(np.einsum("ij,ij->i",cross,cross)==0)[0]].tolist()}')
        for face in faces:
            for i in range(3):
                a,b,c=map(int,(face[i],face[(i+1)%3],face[(i+2)%3]))
                self.planes[a].links.setdefault(a,[]).append((b,c))
        self.sink.append_faces(faces);self.triangles+=len(faces)
        self.peak_frontier_vertices=max(self.peak_frontier_vertices,len(self.coords))

    def _sides(self,g,bottom,top):
        for i,poly in enumerate(_polygons(g)):
            lower_group=bottom.current_group[i];upper_group=top.previous_group[i]
            for ring in _rings(poly):
                coords=list(ring.coords)
                for a,b in zip(coords,coords[1:]):
                    lower=bottom.segment_nodes(lower_group,a,b);upper=top.segment_nodes(upper_group,a,b)
                    li=ti=0;faces=[]
                    while li+1<len(lower) or ti+1<len(upper):
                        l=bottom.vertex(lower_group,lower[li][1]);t=top.vertex(upper_group,upper[ti][1])
                        if li+1<len(lower) and (ti+1==len(upper) or lower[li+1][0]<=upper[ti+1][0]):
                            lnext=bottom.vertex(lower_group,lower[li+1][1]);faces.append([l,lnext,t]);li+=1
                        else:
                            tnext=top.vertex(upper_group,upper[ti+1][1]);faces.append([l,tnext,t]);ti+=1
                    self.emit_faces(np.asarray(faces,dtype=np.int64))

    def push(self,geometry,low,high):
        if self.finished:raise ValueError('Emitter already finished')
        if not all(math.isfinite(z) for z in [low,high]) or high<=low:raise ValueError('Invalid layer interval')
        geometry=self._prepare(geometry)
        if self.pending is None:
            interface=_Interface(self,Polygon(),geometry,low)
        else:
            previous,prev_low,prev_high,bottom=self.pending
            if abs(low-prev_high)>1e-9:raise ValueError('Layer intervals must be contiguous')
            interface=_Interface(self,previous,geometry,low)
            self._sides(previous,bottom,interface);bottom.retire()
        interface.emit_caps()
        self.pending=(geometry,low,high,interface);self.layer_count+=1

    def finish(self):
        if self.finished:return
        if self.pending is not None:
            geometry,low,high,bottom=self.pending
            top=_Interface(self,geometry,Polygon(),high)
            self._sides(geometry,bottom,top);bottom.retire()
            top.emit_caps();top.retire()
        self.pending=None;self.finished=True
