"""
特化提取器套件 — 每種零件類型一個提取器模組
"""
from extractors.base_extractor import BaseExtractor
from extractors.shaft_extractor import ShaftExtractor
from extractors.fan_extractor import FanExtractor
from extractors.fan_housing_extractor import FanHousingExtractor
from extractors.stamped_fan_base_extractor import StampedFanBaseExtractor
from extractors.generic_extractor import GenericExtractor

__all__ = [
    "BaseExtractor",
    "ShaftExtractor",
    "FanExtractor",
    "FanHousingExtractor",
    "StampedFanBaseExtractor",
    "GenericExtractor",
]
