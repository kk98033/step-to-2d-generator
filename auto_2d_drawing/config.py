"""
自動 2D 工程圖繪製系統 — 全域配置
"""
import os

# === 路徑設定 ===
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MODELS_DIR = os.path.join(os.path.dirname(BASE_DIR), "models")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
REFERENCE_DIR = os.path.join(BASE_DIR, "reference")
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")

# === 圖面尺寸 (mm) ===
PAPER_SIZES = {
    "A4": (297, 210),
    "A3": (420, 297),
    "A2": (594, 420),
}
DEFAULT_PAPER = "A3"
MARGIN = 10  # 圖框邊距

# === 標題欄設定 ===
COMPANY_NAME = "FORCECON"
COMPANY_FULL = "TECH. CO., LTD."
DEFAULT_UNIT = "MM"
DEFAULT_SCALE = "1:1"
DEFAULT_REVISION = "R00"

# === 一般公差表 (力致標準) ===
TOLERANCE_TABLE = [
    ("201 ABOVE", "±0.3"),
    ("101~200",   "±0.25"),
    ("51~100",    "±0.2"),
    ("31~50",     "±0.15"),
    ("30 BELOW",  "±0.1"),
]

# === 圖層定義 ===
LAYERS = {
    "VISIBLE":     {"color": 3,  "linetype": "Continuous"},   # 綠色-可見輪廓
    "HIDDEN":      {"color": 9,  "linetype": "DASHED"},       # 灰色-隱藏線
    "CENTER":      {"color": 1,  "linetype": "CENTER2"},      # 紅色-中心線
    "DIM":         {"color": 2,  "linetype": "Continuous"},   # 黃色-尺寸標註
    "DIM_TEXT":    {"color": 7,  "linetype": "Continuous"},   # 白色-尺寸文字
    "TOLERANCE":   {"color": 4,  "linetype": "Continuous"},   # 青色-公差
    "BORDER":      {"color": 7,  "linetype": "Continuous"},   # 白色-圖框
    "TEXT":        {"color": 7,  "linetype": "Continuous"},   # 白色-一般文字
    "NOTES":       {"color": 4,  "linetype": "Continuous"},   # 青色-註記
    "HATCH":       {"color": 3,  "linetype": "Continuous"},   # 綠色-剖面線
    "TITLE_LABEL": {"color": 7,  "linetype": "Continuous"},   # 白色-標題標籤
    "TITLE_VALUE": {"color": 7,  "linetype": "Continuous"},   # 白色-標題數值
    "LEADER":      {"color": 2,  "linetype": "Continuous"},   # 黃色-引線
    "FEATURE":     {"color": 4,  "linetype": "Continuous"},   # 青色-特徵圖層
}

# === 標註樣式 ===
DIM_STYLE = {
    "name": "FORCECON_DIM",
    "dimtxt": 2.5,     # 文字高度
    "dimasz": 1.8,     # 箭頭大小
    "dimexe": 1.5,     # 延伸線超出
    "dimexo": 1.0,     # 延伸線起始偏移
    "dimtmove": 2,     # 文字移動規則
    "dimdec": 2,       # 小數位數
    "dimclrd": 2,      # 尺寸線顏色(黃色)
    "dimclre": 2,      # 延伸線顏色
    "dimclrt": 7,      # 文字顏色(白色)
    "dimgap": 0.8,     # 文字間隙
}

# === 三視圖配置 (第三角投影法) ===
VIEW_CONFIG = {
    "front": {"direction": (0, 0, 1),  "up": (0, 1, 0),  "label": "前視圖 FRONT VIEW"},
    "back":  {"direction": (0, 0, -1), "up": (0, 1, 0),  "label": "背面視圖 BACK VIEW"},
    "top":   {"direction": (0, 1, 0),  "up": (0, 0, -1), "label": "俯視圖 TOP VIEW"},
    "right": {"direction": (-1, 0, 0), "up": (0, 1, 0),  "label": "右側視圖 RIGHT VIEW"},
    "left":  {"direction": (1, 0, 0),  "up": (0, 1, 0),  "label": "左側視圖 LEFT VIEW"},
}

# === 特徵提取參數 ===
MIN_HOLE_RADIUS = 0.1      # 最小孔洞半徑 (mm)
MIN_FILLET_RADIUS = 0.05   # 最小圓角半徑 (mm)
HLR_DEFLECTION = 0.1       # HLR 投影精度
MESH_DEFLECTION = 0.05     # 網格化精度

# === 中文字型 ===
CN_FONT_PATH = r"C:\Windows\Fonts\msjh.ttc"
CN_FONT_NAME = "Microsoft JhengHei"
DXF_FONT_NAME = "msjh.ttc"

# === 顏色 (DXF ACI 色碼) ===
COLOR_WHITE = 7
COLOR_RED = 1
COLOR_YELLOW = 2
COLOR_GREEN = 3
COLOR_CYAN = 4
COLOR_BLUE = 5
COLOR_MAGENTA = 6
COLOR_GRAY = 9
