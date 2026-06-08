import sys
sys.path.insert(0, 'f:/School/力致/app/step-to-2d-generator/auto_2d_drawing')
from batch_generate import generate_single

try:
    generate_single(
        r'f:\School\力致\app\step-to-2d-generator\models\BLADE_ASSY-1AC085000H-R01.stp',
        r'f:\School\力致\app\step-to-2d-generator\output\test_fan_batch_single',
        'test_single_fan'
    )
except Exception as e:
    import traceback
    traceback.print_exc()
