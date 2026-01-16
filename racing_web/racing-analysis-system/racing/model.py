"""
Probability model for horse racing value detection
Converts form scores to probabilities using softmax
"""

import math
from dataclasses import dataclass, field
from typing import List, Optional, Dict

from . import config


@dataclass
class HorseAnalysis:
    """Complete analysis for a single horse"""
    number: int
    name: str
    barrier: int
    form: str
    form_score: float
    
    # Model outputs
    strength: float = 0.0
    model_prob: float = 0.0
    model_fair_odds: float = 0.0
    
    # Market data (if available)
    market_odds: Optional[float] = None
    implied_prob: Optional[float] = None
    
    # Value metrics
    edge: Optional[float] = None
    value_roi: Optional[float] = None
    is_value: bool = False
    
    # Position flags
    is_favourite: bool = False
    is_dud_favourite: bool = False
    
    def __post_init__(self):
        """Calculate derived fields"""
        if self.market_odds and self.market_odds > 1:
            self.implied_prob = 1.0 / self.market_odds
            self.edge = self.model_prob - self.implied_prob
            self.value_roi = (self.model_prob * self.market_odds) - 1


@dataclass 
class RaceAnalysis:
    """Complete analysis for a race"""
    venue: str
    race_number: int
    race_name: str
    field_size: int
    horses: List[HorseAnalysis] = field(default_factory=list)
    
    # Race-level metrics
    overround: Optional[float] = None
    favourite: Optional[HorseAnalysis] = None
    has_dud_favourite: bool = False
    
    # Value findings
    value_backs: List[HorseAnalysis] = field(default_factory=list)
    dutch_recommendation: Optional[Dict] = None
    
    def __post_init__(self):
        """Identify favourite and calculate overround"""
        if self.horses:
            # Find favourite (lowest odds)
            horses_with_odds = [h for h in self.horses if h.market_odds]
            if horses_with_odds:
                self.favourite = min(horses_with_odds, key=lambda h: h.market_odds)
                self.favourite.is_favourite = True
                
                # Calculate overround
                self.overround = sum(h.implied_prob for h in horses_with_odds if h.implied_prob)
            
            # Find value backs
            self.value_backs = [h for h in self.horses if h.is_value]


class ProbabilityModel:
    """
    Converts form scores to probabilities using softmax.
    
    The model uses:
    - strength = max(form_score, 0) + strength_floor
    - probability = softmax(strength / temperature)
    """
    
    def __init__(self, 
                 temperature: float = None,
                 strength_floor: float = None,
                 fav_bias_correction: float = None):
        """
        Initialize the probability model.
        
        Args:
            temperature: Softmax temperature (higher = more spread)
            strength_floor: Minimum strength to add
            fav_bias_correction: Amount to reduce favourite probability
        """
        self.temperature = temperature or config.TEMP
        self.strength_floor = strength_floor or config.STRENGTH_FLOOR
        self.fav_bias_correction = fav_bias_correction or config.FAV_BIAS_CORRECTION
    
    def calculate_strength(self, form_score: float) -> float:
        """Convert form score to strength value"""
        return max(form_score, 0) + self.strength_floor
    
    def softmax(self, strengths: List[float]) -> List[float]:
        """
        Apply softmax to convert strengths to probabilities.
        
        Uses temperature scaling for calibration.
        """
        if not strengths:
            return []
        
        # Scale by temperature
        scaled = [s / self.temperature for s in strengths]
        
        # Subtract max for numerical stability
        max_scaled = max(scaled)
        exp_values = [math.exp(s - max_scaled) for s in scaled]
        
        # Normalize
        total = sum(exp_values)
        if total == 0:
            return [1.0 / len(strengths)] * len(strengths)
        
        return [e / total for e in exp_values]
    
    def analyze_race(self, race_data: dict, odds_lookup: dict = None) -> RaceAnalysis:
        """
        Analyze a race and calculate probabilities for all horses.
        
        Args:
            race_data: Race dict with 'venue', 'race_number', 'race_name', 'horses'
            odds_lookup: Dict mapping normalized horse name to odds
        
        Returns:
            RaceAnalysis with all horses analyzed
        """
        if odds_lookup is None:
            odds_lookup = {}
        
        horses = race_data.get('horses', [])
        
        # Calculate strengths
        horse_analyses = []
        strengths = []
        
        for horse in horses:
            form_score = horse.get('form_score', 0)
            strength = self.calculate_strength(form_score)
            strengths.append(strength)
            
            # Look up odds
            from .name_matcher import normalize_name, match_name
            horse_name = horse.get('name', '')
            
            market_odds = None
            if odds_lookup:
                matched, score = match_name(horse_name, list(odds_lookup.keys()))
                if matched:
                    market_odds = odds_lookup[matched]
            
            analysis = HorseAnalysis(
                number=horse.get('barrier', 0),
                name=horse_name,
                barrier=horse.get('barrier', 0),
                form=horse.get('form', ''),
                form_score=form_score,
                strength=strength,
                market_odds=market_odds
            )
            horse_analyses.append(analysis)
        
        # Calculate probabilities via softmax
        probs = self.softmax(strengths)
        
        for i, analysis in enumerate(horse_analyses):
            analysis.model_prob = probs[i]
            analysis.model_fair_odds = 1.0 / probs[i] if probs[i] > 0 else 999.0
            
            # Calculate value metrics if we have odds
            if analysis.market_odds:
                analysis.implied_prob = 1.0 / analysis.market_odds
                analysis.edge = analysis.model_prob - analysis.implied_prob
                analysis.value_roi = (analysis.model_prob * analysis.market_odds) - 1
                
                # Check if this is a value bet
                analysis.is_value = self._is_value_bet(analysis)
        
        # Create race analysis
        race_analysis = RaceAnalysis(
            venue=race_data.get('venue', ''),
            race_number=race_data.get('race_number', 0),
            race_name=race_data.get('race_name', ''),
            field_size=len(horses),
            horses=horse_analyses
        )
        
        # Apply favourite bias correction if needed
        if self.fav_bias_correction > 0 and race_analysis.favourite:
            self._apply_favourite_correction(race_analysis)
        
        # Check for dud favourite
        self._detect_dud_favourite(race_analysis)
        
        return race_analysis
    
    def _is_value_bet(self, horse: HorseAnalysis) -> bool:
        """Check if a horse qualifies as a value bet"""
        if not horse.market_odds or not horse.edge:
            return False
        
        # Check edge threshold
        if horse.edge < config.EDGE_MIN:
            return False
        
        # Check odds range
        if horse.market_odds < config.ODDS_MIN or horse.market_odds > config.ODDS_MAX:
            return False
        
        # Check ROI threshold
        if horse.value_roi and horse.value_roi < config.VALUE_ROI_MIN:
            return False
        
        return True
    
    def _apply_favourite_correction(self, race: RaceAnalysis):
        """
        Reduce favourite probability and redistribute.
        Helps counteract public overconfidence in favourites.
        """
        if not race.favourite:
            return
        
        correction = self.fav_bias_correction
        fav = race.favourite
        
        # Take from favourite
        old_prob = fav.model_prob
        fav.model_prob = max(0.01, fav.model_prob - correction)
        fav.model_fair_odds = 1.0 / fav.model_prob
        
        # Redistribute to others proportionally
        others = [h for h in race.horses if h != fav]
        if others:
            redistrib = correction / len(others)
            for h in others:
                h.model_prob += redistrib
                h.model_fair_odds = 1.0 / h.model_prob if h.model_prob > 0 else 999.0
        
        # Recalculate value metrics
        for h in race.horses:
            if h.market_odds:
                h.edge = h.model_prob - h.implied_prob
                h.value_roi = (h.model_prob * h.market_odds) - 1
                h.is_value = self._is_value_bet(h)
    
    def _detect_dud_favourite(self, race: RaceAnalysis):
        """Check if favourite is overbet relative to model"""
        if not race.favourite:
            return
        
        fav = race.favourite
        
        if not fav.market_odds or not fav.implied_prob:
            return
        
        # Check if favourite odds are within range
        if fav.market_odds > config.FAV_ODDS_MAX:
            return
        
        # Calculate gap
        gap = fav.implied_prob - fav.model_prob
        
        if gap >= config.FAV_DUD_GAP:
            fav.is_dud_favourite = True
            race.has_dud_favourite = True


def analyze_races(all_races: list, odds_provider, 
                  model: ProbabilityModel = None) -> List[RaceAnalysis]:
    """
    Analyze all races with the probability model.
    
    Args:
        all_races: List of race dicts from PDF parser
        odds_provider: OddsProvider instance
        model: ProbabilityModel instance (creates default if None)
    
    Returns:
        List of RaceAnalysis objects
    """
    if model is None:
        model = ProbabilityModel()
    
    analyses = []
    
    for race in all_races:
        venue = race.get('venue', '')
        race_number = race.get('race_number', 0)
        field_size = len(race.get('horses', []))
        
        # Skip races outside field size range
        if field_size < config.FIELD_MIN or field_size > config.FIELD_MAX:
            continue
        
        # Get odds for this race
        odds_lookup = odds_provider.get_odds(venue, '', race_number)
        
        # Analyze
        analysis = model.analyze_race(race, odds_lookup)
        analyses.append(analysis)
    
    return analyses
