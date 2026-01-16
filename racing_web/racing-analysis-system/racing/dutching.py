"""
Dutching and arbitrage calculations for horse racing
"""

import math
from dataclasses import dataclass, field
from typing import List, Optional, Tuple
from itertools import combinations

from . import config
from .model import HorseAnalysis, RaceAnalysis


@dataclass
class DutchStake:
    """Stake calculation for a single horse in a dutch"""
    horse_name: str
    horse_number: int
    odds: float
    stake: float
    profit_if_wins: float
    model_prob: float


@dataclass
class DutchResult:
    """Complete dutching calculation result"""
    stakes: List[DutchStake] = field(default_factory=list)
    total_stake: float = 0.0
    dutch_book: float = 0.0  # sum(1/odds) - if < 1, guaranteed profit
    is_arb: bool = False     # dutch_book < 1
    guaranteed_profit: float = 0.0
    expected_value: float = 0.0
    combined_model_prob: float = 0.0
    worst_case: float = 0.0  # Profit if none win (always negative)
    roi_percent: float = 0.0
    
    def __str__(self):
        arb_status = "✓ ARB" if self.is_arb else "Overlay"
        return (f"Dutch: {len(self.stakes)} runners, "
                f"Book: {self.dutch_book:.3f} ({arb_status}), "
                f"EV: ${self.expected_value:.2f}")


class DutchingCalculator:
    """
    Calculates optimal dutching stakes and finds best combinations.
    """
    
    def __init__(self, bankroll: float = None):
        self.bankroll = bankroll or config.BANKROLL
    
    def calculate_equal_profit_dutch(self, 
                                     horses: List[HorseAnalysis],
                                     total_stake: float = None) -> DutchResult:
        """
        Calculate stakes for equal profit if any selected horse wins.
        
        Classic dutching formula:
        stake_i = total_stake * (1/odds_i) / sum(1/odds_j)
        
        Args:
            horses: List of HorseAnalysis with market_odds
            total_stake: Total amount to stake (defaults to bankroll)
        
        Returns:
            DutchResult with calculated stakes
        """
        if total_stake is None:
            total_stake = self.bankroll
        
        # Filter to horses with valid odds
        valid_horses = [h for h in horses if h.market_odds and h.market_odds > 1]
        
        if not valid_horses:
            return DutchResult()
        
        # Calculate dutch book
        dutch_book = sum(1.0 / h.market_odds for h in valid_horses)
        
        # Calculate stakes
        stakes = []
        for horse in valid_horses:
            implied = 1.0 / horse.market_odds
            stake = total_stake * implied / dutch_book
            profit_if_wins = (stake * horse.market_odds) - total_stake
            
            stakes.append(DutchStake(
                horse_name=horse.name,
                horse_number=horse.number,
                odds=horse.market_odds,
                stake=round(stake, 2),
                profit_if_wins=round(profit_if_wins, 2),
                model_prob=horse.model_prob
            ))
        
        # Calculate combined model probability
        combined_prob = sum(h.model_prob for h in valid_horses)
        
        # Calculate expected value
        ev = sum(
            s.model_prob * s.profit_if_wins 
            for s in stakes
        ) - (1 - combined_prob) * total_stake
        
        # Guaranteed profit (only if arb)
        is_arb = dutch_book < 1.0
        guaranteed_profit = 0.0
        if is_arb and stakes:
            guaranteed_profit = stakes[0].profit_if_wins  # All same when arb
        
        return DutchResult(
            stakes=stakes,
            total_stake=total_stake,
            dutch_book=round(dutch_book, 4),
            is_arb=is_arb,
            guaranteed_profit=round(guaranteed_profit, 2),
            expected_value=round(ev, 2),
            combined_model_prob=round(combined_prob, 4),
            worst_case=round(-total_stake, 2),
            roi_percent=round((ev / total_stake) * 100, 2) if total_stake > 0 else 0
        )
    
    def find_best_dutch_combination(self, 
                                    race: RaceAnalysis,
                                    exclude_favourite: bool = False,
                                    min_runners: int = None,
                                    max_runners: int = None,
                                    min_combined_prob: float = None) -> Optional[DutchResult]:
        """
        Find the best dutching combination for a race.
        
        Tries all valid combinations and selects based on:
        1. If any combination has dutch_book < 1 (arb), choose highest profit
        2. Otherwise, choose highest EV with reasonable combined probability
        
        Args:
            race: RaceAnalysis with horses
            exclude_favourite: If True, exclude the favourite from combinations
            min_runners: Minimum horses in combination
            max_runners: Maximum horses in combination
            min_combined_prob: Minimum combined model probability
        
        Returns:
            Best DutchResult or None if no valid combination
        """
        if min_runners is None:
            min_runners = config.DUTCH_MIN_RUNNERS
        if max_runners is None:
            max_runners = config.DUTCH_MAX_RUNNERS
        if min_combined_prob is None:
            min_combined_prob = config.DUTCH_MIN_COMBINED_PROB
        
        # Get horses with valid odds
        candidates = [
            h for h in race.horses 
            if h.market_odds and h.market_odds > 1
        ]
        
        # Optionally exclude favourite
        if exclude_favourite and race.favourite:
            candidates = [h for h in candidates if not h.is_favourite]
        
        if len(candidates) < min_runners:
            return None
        
        # Try all combinations
        best_arb = None
        best_ev = None
        
        for n in range(min_runners, min(max_runners + 1, len(candidates) + 1)):
            for combo in combinations(candidates, n):
                result = self.calculate_equal_profit_dutch(list(combo))
                
                # Skip if below minimum combined probability
                if result.combined_model_prob < min_combined_prob:
                    continue
                
                # Track best arb (if any)
                if result.is_arb:
                    if best_arb is None or result.guaranteed_profit > best_arb.guaranteed_profit:
                        best_arb = result
                
                # Track best EV
                if best_ev is None or result.expected_value > best_ev.expected_value:
                    best_ev = result
        
        # Prefer arb if available, otherwise best EV
        return best_arb if best_arb else best_ev
    
    def find_dud_favourite_dutch(self, race: RaceAnalysis) -> Optional[DutchResult]:
        """
        Find best dutch excluding the (dud) favourite.
        
        Use when favourite appears overbet relative to model.
        """
        if not race.favourite:
            return None
        
        return self.find_best_dutch_combination(
            race,
            exclude_favourite=True,
            min_combined_prob=config.DUTCH_MIN_COMBINED_PROB
        )
    
    def calculate_lay_stake(self,
                           odds: float,
                           liability: float = None,
                           stake: float = None) -> dict:
        """
        Calculate lay betting stake and liability.
        
        For exchange betting (e.g., Betfair).
        
        Args:
            odds: Lay odds
            liability: Max liability (what you could lose)
            stake: Backer's stake you want to match
        
        Returns:
            Dict with lay_stake, liability, profit_if_loses
        """
        if liability is not None:
            # Calculate stake from liability
            lay_stake = liability / (odds - 1)
            return {
                'lay_odds': odds,
                'lay_stake': round(lay_stake, 2),
                'liability': round(liability, 2),
                'profit_if_loses': round(lay_stake, 2)
            }
        
        elif stake is not None:
            # Calculate liability from stake
            liability = stake * (odds - 1)
            return {
                'lay_odds': odds,
                'lay_stake': round(stake, 2),
                'liability': round(liability, 2),
                'profit_if_loses': round(stake, 2)
            }
        
        return {}


def find_value_dutch_opportunities(races: List[RaceAnalysis],
                                   calculator: DutchingCalculator = None) -> List[Tuple[RaceAnalysis, DutchResult]]:
    """
    Find dutching opportunities across all races.
    
    Returns list of (race, dutch_result) tuples sorted by EV.
    """
    if calculator is None:
        calculator = DutchingCalculator()
    
    opportunities = []
    
    for race in races:
        # Standard dutch
        dutch = calculator.find_best_dutch_combination(race)
        if dutch and dutch.expected_value > 0:
            race.dutch_recommendation = {
                'type': 'standard',
                'result': dutch
            }
            opportunities.append((race, dutch))
        
        # Dud favourite dutch
        if race.has_dud_favourite:
            dud_dutch = calculator.find_dud_favourite_dutch(race)
            if dud_dutch and dud_dutch.expected_value > 0:
                # Prefer dud favourite dutch if better EV
                if not dutch or dud_dutch.expected_value > dutch.expected_value:
                    race.dutch_recommendation = {
                        'type': 'dud_favourite',
                        'result': dud_dutch
                    }
                    opportunities.append((race, dud_dutch))
    
    # Sort by EV
    opportunities.sort(key=lambda x: x[1].expected_value, reverse=True)
    
    return opportunities
