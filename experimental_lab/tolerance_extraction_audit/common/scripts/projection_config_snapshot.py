"""Minimal immutable configuration for the isolated HLR experiment."""

VIEW_CONFIG = {
    "front": {"direction": (0, 0, 1), "up": (0, 1, 0)},
    "back": {"direction": (0, 0, -1), "up": (0, 1, 0)},
    "top": {"direction": (0, 1, 0), "up": (0, 0, -1)},
    "right": {"direction": (-1, 0, 0), "up": (0, 1, 0)},
    "left": {"direction": (1, 0, 0), "up": (0, 1, 0)},
}

HLR_DEFLECTION = 0.1
