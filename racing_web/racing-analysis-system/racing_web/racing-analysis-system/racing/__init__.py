"""
Racing Value Finder + Dutching Engine Package
"""

from . import config
from .model import ProbabilityModel, RaceAnalysis, HorseAnalysis, analyze_races
from .dutching import DutchingCalculator, DutchResult, find_value_dutch_opportunities
from .odds_provider import create_odds_provider, OddsProvider
from .name_matcher import normalize_name, match_name
from .report import ReportGenerator, generate_quick_discord_message
from .value_finder import ValueFinder

__all__ = [
    'config',
    'ProbabilityModel',
    'RaceAnalysis',
    'HorseAnalysis',
    'analyze_races',
    'DutchingCalculator',
    'DutchResult',
    'find_value_dutch_opportunities',
    'create_odds_provider',
    'OddsProvider',
    'normalize_name',
    'match_name',
    'ReportGenerator',
    'generate_quick_discord_message',
    'ValueFinder'
]
