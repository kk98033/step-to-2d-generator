import requests
import re

r = requests.get('http://127.0.0.1:8000/api/files/BLADE_ASSY_batch/_parts/Node_0_1_1_2_1.stl')
lines = r.text.split('\n')[:20]
for line in lines:
    print(line)
