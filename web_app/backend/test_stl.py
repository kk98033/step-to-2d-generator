import requests
import struct

r = requests.get('http://127.0.0.1:8000/api/files/BLADE_ASSY_batch/_parts/Node_0_1_1_2_1.stl')
print('Status:', r.status_code)
content = r.content
print('Size:', len(content))
if len(content) > 80:
    print('Header:', content[:80])
    num_triangles = struct.unpack('<I', content[80:84])[0]
    print('Num triangles:', num_triangles)
