"""測試 STEP 匯出為 Web 支援的格式 (STL)"""
import sys, os
from OCC.Core.STEPControl import STEPControl_Reader
from OCC.Core.StlAPI import StlAPI_Writer
from OCC.Core.BRepMesh import BRepMesh_IncrementalMesh

def export_stl():
    step_file = r"C:\MCAS_LAB\力致\app\models\BLADE_ASSY-1AC085000H-R01.stp"
    out_stl = r"C:\MCAS_LAB\力致\app\auto_2d_drawing\test.stl"
    
    reader = STEPControl_Reader()
    reader.ReadFile(step_file)
    reader.TransferRoots()
    shape = reader.OneShape()
    
    # Triangulate
    BRepMesh_IncrementalMesh(shape, 0.1)
    
    writer = StlAPI_Writer()
    writer.Write(shape, out_stl)
    print("Exported to:", out_stl)

if __name__ == "__main__":
    export_stl()
