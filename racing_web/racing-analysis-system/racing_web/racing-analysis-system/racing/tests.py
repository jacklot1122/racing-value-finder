"""
Tests for the Value Finder + Dutching Engine
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from racing.name_matcher import normalize_name, match_name, create_name_variants
from racing.dutching import DutchingCalculator
from racing.model import ProbabilityModel, HorseAnalysis
import math


def test_normalize_name():
    """Test horse name normalization"""
    print("Testing normalize_name...")
    
    tests = [
        ("Flying Wahine", "FLYING WAHINE"),
        ("O'Brien's Star", "OBRIENS STAR"),
        ("Mr. Ed", "MR ED"),
        ("High-Flyer", "HIGH FLYER"),
        ("  Extra   Spaces  ", "EXTRA SPACES"),
        ("Café Royal", "CAFE ROYAL"),
    ]
    
    all_passed = True
    for input_name, expected in tests:
        result = normalize_name(input_name)
        if result != expected:
            print(f"  ✗ normalize_name('{input_name}') = '{result}', expected '{expected}'")
            all_passed = False
        else:
            print(f"  ✓ normalize_name('{input_name}') = '{result}'")
    
    return all_passed


def test_name_variants():
    """Test name variant generation"""
    print("\nTesting create_name_variants...")
    
    variants = create_name_variants("The Chosen One")
    print(f"  Variants for 'The Chosen One': {variants}")
    
    assert "THE CHOSEN ONE" in variants
    assert "CHOSEN ONE" in variants  # Without "THE"
    
    variants = create_name_variants("Mister Ed")
    print(f"  Variants for 'Mister Ed': {variants}")
    
    assert "MISTER ED" in variants
    assert "MR ED" in variants
    
    print("  ✓ All variant tests passed")
    return True


def test_name_matching():
    """Test fuzzy name matching"""
    print("\nTesting match_name...")
    
    candidates = [
        "FLYING WAHINE",
        "RAPID RUNNER",
        "THE CHOSEN ONE",
        "MISTER ED"
    ]
    
    # Exact match
    result, score = match_name("Flying Wahine", candidates)
    assert result == "FLYING WAHINE"
    assert score == 100
    print(f"  ✓ Exact match: 'Flying Wahine' -> '{result}' (score: {score})")
    
    # Variant match (with THE prefix)
    result, score = match_name("The Chosen One", candidates)
    assert result == "THE CHOSEN ONE"
    print(f"  ✓ Variant match: 'The Chosen One' -> '{result}' (score: {score})")
    
    # Fuzzy match test
    result, score = match_name("Mr Ed", candidates)
    assert result == "MISTER ED"
    print(f"  ✓ Fuzzy match: 'Mr Ed' -> '{result}' (score: {score})")
    
    # No match
    result, score = match_name("Unknown Horse XYZ", candidates, threshold=95)
    assert result is None
    print(f"  ✓ No match: 'Unknown Horse XYZ' -> {result}")
    
    return True


def test_dutch_calculations():
    """Test dutching stake calculations"""
    print("\nTesting DutchingCalculator...")
    
    calc = DutchingCalculator(bankroll=100.0)
    
    # Create test horses
    horses = [
        HorseAnalysis(
            number=1, name="Horse A", barrier=1, form="123",
            form_score=50, strength=55, model_prob=0.4,
            model_fair_odds=2.5, market_odds=3.0
        ),
        HorseAnalysis(
            number=2, name="Horse B", barrier=2, form="234",
            form_score=40, strength=45, model_prob=0.3,
            model_fair_odds=3.33, market_odds=4.0
        ),
    ]
    
    result = calc.calculate_equal_profit_dutch(horses)
    
    print(f"  Dutch book: {result.dutch_book}")
    print(f"  Total stake: ${result.total_stake}")
    print(f"  Is arb: {result.is_arb}")
    
    # Verify stakes
    for stake in result.stakes:
        print(f"    {stake.horse_name}: ${stake.stake:.2f} @ ${stake.odds} -> +${stake.profit_if_wins:.2f}")
    
    # Check all profits are equal (within rounding)
    profits = [s.profit_if_wins for s in result.stakes]
    profit_diff = max(profits) - min(profits)
    assert profit_diff < 0.02, f"Profits not equal: {profits}"
    print(f"  ✓ Equal profit verified (diff: ${profit_diff:.2f})")
    
    # Check total stake
    stake_sum = sum(s.stake for s in result.stakes)
    assert abs(stake_sum - result.total_stake) < 0.02
    print(f"  ✓ Total stake verified: ${stake_sum:.2f}")
    
    return True


def test_arb_detection():
    """Test arbitrage detection"""
    print("\nTesting arbitrage detection...")
    
    calc = DutchingCalculator(bankroll=100.0)
    
    # Create arb opportunity (book < 1)
    horses = [
        HorseAnalysis(
            number=1, name="Horse A", barrier=1, form="1",
            form_score=60, strength=65, model_prob=0.5,
            model_fair_odds=2.0, market_odds=2.2  # implied = 0.45
        ),
        HorseAnalysis(
            number=2, name="Horse B", barrier=2, form="2",
            form_score=50, strength=55, model_prob=0.4,
            model_fair_odds=2.5, market_odds=2.4  # implied = 0.42
        ),
    ]
    # Book = 0.45 + 0.42 = 0.87 < 1 = ARB!
    
    result = calc.calculate_equal_profit_dutch(horses)
    
    print(f"  Dutch book: {result.dutch_book}")
    print(f"  Is arb: {result.is_arb}")
    print(f"  Guaranteed profit: ${result.guaranteed_profit}")
    
    assert result.dutch_book < 1.0
    assert result.is_arb is True
    assert result.guaranteed_profit > 0
    
    print(f"  ✓ Arbitrage correctly detected with ${result.guaranteed_profit:.2f} profit")
    
    return True


def test_softmax_probabilities():
    """Test softmax probability calculation"""
    print("\nTesting softmax probabilities...")
    
    model = ProbabilityModel(temperature=15.0)
    
    # Test with varying form scores
    strengths = [50, 40, 30, 20, 10]
    probs = model.softmax(strengths)
    
    print(f"  Strengths: {strengths}")
    print(f"  Probabilities: {[f'{p:.3f}' for p in probs]}")
    
    # Check sum to 1
    prob_sum = sum(probs)
    assert abs(prob_sum - 1.0) < 0.001, f"Probabilities don't sum to 1: {prob_sum}"
    print(f"  ✓ Sum to 1: {prob_sum:.6f}")
    
    # Check ordering (higher strength = higher prob)
    for i in range(len(probs) - 1):
        assert probs[i] > probs[i+1], "Probabilities not in order"
    print(f"  ✓ Ordering correct")
    
    # Check highest prob horse
    assert probs[0] == max(probs)
    print(f"  ✓ Highest strength has highest probability")
    
    return True


def test_value_detection():
    """Test value bet detection"""
    print("\nTesting value detection...")
    
    model = ProbabilityModel()
    
    race = {
        'venue': 'Test',
        'race_number': 1,
        'race_name': 'Test Race',
        'horses': [
            {'barrier': 1, 'name': 'Value Horse', 'form': '123', 'form_score': 60},
            {'barrier': 2, 'name': 'Fair Horse', 'form': '234', 'form_score': 40},
            {'barrier': 3, 'name': 'No Value', 'form': '345', 'form_score': 20},
        ]
    }
    
    # Odds where horse 1 has value, others don't
    odds = {
        'VALUE HORSE': 3.5,   # If model gives ~35% = fair 2.85, this is VALUE
        'FAIR HORSE': 2.5,    # Model ~30%, fair = 3.3, NO value
        'NO VALUE': 10.0,     # Model ~15%, fair = 6.6, NO value
    }
    
    analysis = model.analyze_race(race, odds)
    
    print(f"  Field size: {analysis.field_size}")
    for h in analysis.horses:
        value_str = "VALUE ✓" if h.is_value else ""
        odds_str = f"${h.market_odds:.2f}" if h.market_odds else "N/A"
        print(f"    {h.name}: Model {h.model_prob*100:.1f}% | Fair ${h.model_fair_odds:.2f} | "
              f"Odds {odds_str} | Edge {h.edge*100 if h.edge else 0:.1f}% {value_str}")
    
    print(f"  ✓ Value detection completed")
    return True


def run_all_tests():
    """Run all tests"""
    print("=" * 60)
    print("RUNNING VALUE FINDER TESTS")
    print("=" * 60)
    
    tests = [
        test_normalize_name,
        test_name_variants,
        test_name_matching,
        test_dutch_calculations,
        test_arb_detection,
        test_softmax_probabilities,
        test_value_detection,
    ]
    
    passed = 0
    failed = 0
    
    for test in tests:
        try:
            if test():
                passed += 1
            else:
                failed += 1
                print(f"  ✗ {test.__name__} failed")
        except Exception as e:
            failed += 1
            print(f"  ✗ {test.__name__} error: {e}")
    
    print("\n" + "=" * 60)
    print(f"TESTS COMPLETE: {passed} passed, {failed} failed")
    print("=" * 60)
    
    return failed == 0


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
