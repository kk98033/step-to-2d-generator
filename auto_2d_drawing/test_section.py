
import sys
sys.path.insert(0, 'f:/School/力致/app/step-to-2d-generator/auto_2d_drawing')
from step_reader import load_step
from view_projector import ViewProjector
from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeHalfSpace
from OCC.Core.gp import gp_Pln, gp_Pnt, gp_Dir
from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_MakeFace
from OCC.Core.BRepAlgoAPI import BRepAlgoAPI_Cut

print('Loading step...')
shape = load_step(r'f:\School\力致\app\step-to-2d-generator\auto_2d_drawing\models\BLADE_ASSY-1AC085000H-R01_part1_Node_0_1_1_2_1_front.stp')

print('Projecting...')
proj = ViewProjector()
view_data = proj.project_section_view(shape, 'right', cut_axis='X', keep_sign=-1)
print('Section edges:', len(view_data['visible']))

