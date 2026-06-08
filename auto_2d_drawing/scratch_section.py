
import sys
from step_reader import load_step
from view_projector import ViewProjector
from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeHalfSpace
from OCC.Core.gp import gp_Pln, gp_Pnt, gp_Dir
from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_MakeFace
from OCC.Core.BRepAlgoAPI import BRepAlgoAPI_Cut

print('Loading step...')
shape = load_step(r'f:/School/力致/app/models/BLADE_ASSY-1AC085000H-R01_part1_Node_0_1_1_2_1_front.stp')

print('Cutting...')
pln = gp_Pln(gp_Pnt(0,0,0), gp_Dir(1,0,0))
face = BRepBuilderAPI_MakeFace(pln).Face()
half_space = BRepPrimAPI_MakeHalfSpace(face, gp_Pnt(-1, 0, 0)).Solid()

cut_algo = BRepAlgoAPI_Cut(shape, half_space)
cut_algo.Build()
cut_shape = cut_algo.Shape()

print('Projecting...')
proj = ViewProjector()
vis, hid, out_v, out_h = proj.project(cut_shape, 'right')
vis_edges = proj.extract_edges(vis)
print('Visible edges after cut:', len(vis_edges))

