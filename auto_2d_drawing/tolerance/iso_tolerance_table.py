"""
ISO 286 / DIN 7157 標準極限與配合查表庫 (Standard ISO Fits & Tolerance Engine)
提供標準軸類 (Shaft) 與孔類 (Hole) 配合公差、卡簧槽標準公差與自訂偏差之查表與格式化輸出。
"""
from typing import Dict, Any, Optional, Tuple

# 常用軸類直徑公差帶 (單位: mm)
# 結構: (min_d, max_d]: { "fit_class": (upper_dev, lower_dev) }
SHAFT_FITS_TABLE = [
    (0.0, 3.0, {
        "h6": (0.000, -0.006),
        "g6": (-0.002, -0.008),
        "js6": (+0.003, -0.003),
        "p6": (+0.012, +0.006),
        "h11": (0.000, -0.060),
    }),
    (3.0, 6.0, {
        "h6": (0.000, -0.008),
        "g6": (-0.004, -0.012),
        "js6": (+0.004, -0.004),
        "p6": (+0.020, +0.012),
        "h11": (0.000, -0.075),
    }),
    (6.0, 10.0, {
        "h6": (0.000, -0.009),
        "g6": (-0.005, -0.014),
        "js6": (+0.0045, -0.0045),
        "p6": (+0.024, +0.015),
        "h11": (0.000, -0.090),
    }),
    (10.0, 18.0, {
        "h6": (0.000, -0.011),
        "g6": (-0.006, -0.017),
        "js6": (+0.0055, -0.0055),
        "p6": (+0.029, +0.018),
        "h11": (0.000, -0.110),
    }),
    (18.0, 30.0, {
        "h6": (0.000, -0.013),
        "g6": (-0.007, -0.020),
        "js6": (+0.0065, -0.0065),
        "p6": (+0.035, +0.022),
        "h11": (0.000, -0.130),
    }),
    (30.0, 50.0, {
        "h6": (0.000, -0.016),
        "g6": (-0.009, -0.025),
        "js6": (+0.008, -0.008),
        "p6": (+0.042, +0.026),
        "h11": (0.000, -0.160),
    })
]

# 常用孔類直徑公差帶 (單位: mm)
HOLE_FITS_TABLE = [
    (0.0, 3.0, {
        "H7": (+0.010, 0.000),
        "H8": (+0.014, 0.000),
        "JS7": (+0.005, -0.005),
        "P7": (-0.004, -0.014),
    }),
    (3.0, 6.0, {
        "H7": (+0.012, 0.000),
        "H8": (+0.018, 0.000),
        "JS7": (+0.006, -0.006),
        "P7": (-0.009, -0.021),
    }),
    (6.0, 10.0, {
        "H7": (+0.015, 0.000),
        "H8": (+0.022, 0.000),
        "JS7": (+0.0075, -0.0075),
        "P7": (-0.012, -0.027),
    }),
    (10.0, 18.0, {
        "H7": (+0.018, 0.000),
        "H8": (+0.027, 0.000),
        "JS7": (+0.009, -0.009),
        "P7": (-0.015, -0.033),
    }),
    (18.0, 30.0, {
        "H7": (+0.021, 0.000),
        "H8": (+0.033, 0.000),
        "JS7": (+0.0105, -0.0105),
        "P7": (-0.018, -0.039),
    }),
    (30.0, 50.0, {
        "H7": (+0.025, 0.000),
        "H8": (+0.039, 0.000),
        "JS7": (+0.0125, -0.0125),
        "P7": (-0.021, -0.046),
    })
]


def lookup_iso_fit_deviation(nominal: float, fit_class: str, is_hole: bool = False) -> Tuple[float, float]:
    """
    根據標稱直徑與 ISO 配合等級 (如 h6, p6, H7) 查出 (upper_dev, lower_dev)。
    """
    table = HOLE_FITS_TABLE if is_hole else SHAFT_FITS_TABLE
    upper_dev = 0.000
    lower_dev = 0.000
    found = False

    for (min_d, max_d, fits) in table:
        if min_d < nominal <= max_d or (min_d == 0.0 and nominal <= 0.0):
            if fit_class in fits:
                upper_dev, lower_dev = fits[fit_class]
                found = True
                break

    if not found:
        # 若超出範圍或未定義，提供合理近似
        if fit_class.lower().startswith("h"):
            upper_dev = 0.000
            lower_dev = -0.008
        elif fit_class.upper().startswith("h"):
            upper_dev = +0.012
            lower_dev = 0.000
        else:
            upper_dev = +0.010
            lower_dev = -0.010

    return (round(upper_dev, 4), round(lower_dev, 4))


def format_tolerance_dimension(
    nominal: float,
    is_diameter: bool = False,
    tol_config: Optional[Dict[str, Any]] = None
) -> str:
    """
    產生格式化尺寸標註字串，包含直徑符號與公差字串。
    tol_config 格式:
      - None or {"mode": "NONE"} -> "Φ3.00" / "3.00"
      - {"mode": "FIT", "fit_class": "h6", "is_hole": False} -> "Φ3.00 h6 (+0.000/-0.006)"
      - {"mode": "GROOVE"} -> "0.60 (+0.040/0.000)" (卡簧槽標準)
      - {"mode": "CUSTOM_SYMMETRIC", "dev": 0.05} -> "3.00 (±0.05)"
      - {"mode": "CUSTOM_LIMITS", "upper_dev": 0.02, "lower_dev": -0.01} -> "3.00 (+0.020/-0.010)"
    """
    prefix = "Φ" if is_diameter else ""
    nominal_str = f"{nominal:.2f}"
    
    if not tol_config or tol_config.get("mode") == "NONE":
        return f"{prefix}{nominal_str}"

    mode = tol_config.get("mode", "NONE")

    if mode == "FIT":
        fit_class = tol_config.get("fit_class", "h6")
        is_hole = tol_config.get("is_hole", False)
        upper_dev, lower_dev = lookup_iso_fit_deviation(nominal, fit_class, is_hole)
        
        upper_str = f"+{upper_dev:.3f}" if upper_dev > 0 else f"{upper_dev:.3f}"
        lower_str = f"+{lower_dev:.3f}" if lower_dev > 0 else f"{lower_dev:.3f}"
        return f"{prefix}{nominal_str} {fit_class} ({upper_str}/{lower_str})"

    elif mode == "GROOVE":
        upper_dev = tol_config.get("upper_dev", 0.040)
        lower_dev = tol_config.get("lower_dev", 0.000)
        upper_str = f"+{upper_dev:.3f}" if upper_dev > 0 else f"{upper_dev:.3f}"
        lower_str = f"+{lower_dev:.3f}" if lower_dev > 0 else f"{lower_dev:.3f}"
        return f"{nominal_str} ({upper_str}/{lower_str})"

    elif mode == "CUSTOM_SYMMETRIC":
        dev = tol_config.get("dev", 0.05)
        return f"{prefix}{nominal_str} (±{dev:.2f})"

    elif mode == "CUSTOM_LIMITS":
        upper_dev = float(tol_config.get("upper_dev", 0.02))
        lower_dev = float(tol_config.get("lower_dev", -0.02))
        upper_str = f"+{upper_dev:.3f}" if upper_dev > 0 else f"{upper_dev:.3f}"
        lower_str = f"+{lower_dev:.3f}" if lower_dev > 0 else f"{lower_dev:.3f}"
        return f"{prefix}{nominal_str} ({upper_str}/{lower_str})"

    return f"{prefix}{nominal_str}"


if __name__ == "__main__":
    print("Fit test 1:", format_tolerance_dimension(3.0, is_diameter=True, tol_config={"mode": "FIT", "fit_class": "h6"}))
    print("Fit test 2:", format_tolerance_dimension(1.5, is_diameter=False, tol_config={"mode": "FIT", "fit_class": "H7", "is_hole": True}))
    print("Groove test:", format_tolerance_dimension(0.60, is_diameter=False, tol_config={"mode": "GROOVE"}))
    print("Custom test:", format_tolerance_dimension(25.0, is_diameter=False, tol_config={"mode": "CUSTOM_SYMMETRIC", "dev": 0.1}))
