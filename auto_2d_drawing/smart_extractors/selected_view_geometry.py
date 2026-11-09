"""Project native measurement geometry into an explicitly selected view."""
import copy
import math
from auto_2d_drawing.drawing_projection import project_point, view_basis

VIEWS = ('front', 'top', 'right')


def supported_intent_views(intent):
    feature = intent.get('feature') or {}
    measurement = intent.get('measurement') or {}
    preferred = (intent.get('projection') or {}).get('preferred_view') or 'front'
    family = str(feature.get('family') or '')
    if family != 'SOLID_SHAFT' and 'ANNULAR' not in family:
        return [preferred]
    if measurement.get('kind') == 'DIAMETER' or measurement.get('role') == 'RADIAL_WALL_THICKNESS':
        return list(VIEWS)
    axis = feature.get('axis') or []
    if len(axis) != 3:
        return [preferred]
    norm = math.sqrt(sum(float(value) ** 2 for value in axis))
    if norm <= 1e-12:
        return [preferred]
    return [name for name in VIEWS if abs(sum(float(axis[i]) * view_basis(name)['normal'][i] / norm for i in range(3))) < 1e-5]


def reproject_measurement(candidate, target_view, *, annular_feature=None):
    task = copy.deepcopy(candidate)
    if target_view == task.get('view'):
        return task
    geometry = task.get('display_geometry') or {}
    if annular_feature:
        center = list(annular_feature['center'])
        axis = list(annular_feature['axis'])
        nominal = float(task['nominal_value'])
        if 'axial_thickness' in str(task.get('semantic')):
            start = [center[i] - axis[i] * nominal / 2 for i in range(3)]
            end = [center[i] + axis[i] * nominal / 2 for i in range(3)]
        else:
            # Diameter/radial wall measurements use the radial direction that
            # lies in the target image plane, derived from the cylinder axis.
            start, end = center, [center[i] + axis[i] for i in range(3)]
    elif geometry.get('start') is not None and geometry.get('end') is not None:
        start, end = geometry['start'], geometry['end']
        center = [(float(start[i]) + float(end[i])) / 2 for i in range(3)]
    else:
        raise ValueError('VIEW_REPROJECTION_REQUIRES_NATIVE_3D_GEOMETRY')
    first, last = project_point(start, target_view), project_point(end, target_view)
    nominal = float(task.get('nominal_value') or 0)
    diameter = str(task.get('dim_type') or '').upper() == 'DIAMETER'
    radial_wall = 'radial_wall_thickness' in str(task.get('semantic'))
    if diameter or radial_wall:
        dx, dy = last[0] - first[0], last[1] - first[1]
        span = math.hypot(dx, dy)
        radial = (-dy / span, dx / span) if span > 1e-9 else (1.0, 0.0)
        point = project_point(center, target_view)
        if diameter:
            low, high = -nominal / 2, nominal / 2
        else:
            low, high = float(annular_feature['inner_radius']), float(annular_feature['outer_radius'])
        anchors = [[point[i] + radial[i] * offset for i in range(2)] for offset in (low, high)]
    else:
        anchors = [list(first), list(last)]
        span = math.dist(first, last)
        if not math.isclose(span, nominal, rel_tol=1e-5, abs_tol=1e-5):
            raise ValueError('MEASUREMENT_NOT_VISIBLE_AT_TRUE_LENGTH_IN_SELECTED_VIEW')
    task['anchors'] = anchors
    task['view'] = target_view
    task.pop('orientation', None)
    return task
