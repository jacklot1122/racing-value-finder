"""
Name matching utilities for horse racing data
Handles normalization and fuzzy matching between PDF names and odds sources
"""

import re
import unicodedata

try:
    from rapidfuzz import fuzz, process
    RAPIDFUZZ_AVAILABLE = True
except ImportError:
    RAPIDFUZZ_AVAILABLE = False
    print("Warning: rapidfuzz not installed. Using basic matching only.")
    print("Install with: pip install rapidfuzz")

from . import config


def normalize_name(name: str) -> str:
    """
    Normalize a horse name for matching.
    - Uppercase
    - Remove punctuation (apostrophes, hyphens, periods)
    - Collapse multiple spaces
    - Remove accents/diacritics
    - Strip whitespace
    """
    if not name:
        return ""
    
    # Convert to uppercase
    name = name.upper()
    
    # Remove accents/diacritics
    name = unicodedata.normalize('NFKD', name)
    name = ''.join(c for c in name if not unicodedata.combining(c))
    
    # Replace hyphens with spaces (for compound names)
    name = name.replace('-', ' ')
    
    # Remove other common punctuation
    name = re.sub(r"['\.\(\)\,\!\?]", "", name)
    
    # Collapse multiple spaces
    name = re.sub(r'\s+', ' ', name)
    
    # Strip
    name = name.strip()
    
    return name


def create_name_variants(name: str) -> list:
    """
    Create common variants of a name for matching.
    Handles cases like:
    - "THE CHOSEN ONE" vs "CHOSEN ONE"
    - "MISTER X" vs "MR X"
    - Numbers written as words
    """
    normalized = normalize_name(name)
    variants = [normalized]
    
    # Remove leading "THE"
    if normalized.startswith("THE "):
        variants.append(normalized[4:])
    
    # Common abbreviations
    replacements = [
        ("MISTER ", "MR "),
        ("MR ", "MISTER "),
        ("MISS ", "MS "),
        ("SAINT ", "ST "),
        ("ST ", "SAINT "),
        ("MOUNT ", "MT "),
        ("MT ", "MOUNT "),
    ]
    
    for old, new in replacements:
        if old in normalized:
            variants.append(normalized.replace(old, new))
    
    return list(set(variants))


def match_name(target_name: str, candidates: list, threshold: int = None) -> tuple:
    """
    Find the best matching name from a list of candidates.
    
    Args:
        target_name: The name to match
        candidates: List of candidate names
        threshold: Minimum match score (0-100), defaults to config.FUZZY_MATCH_THRESHOLD
    
    Returns:
        (matched_name, score) or (None, 0) if no match found
    """
    if threshold is None:
        threshold = config.FUZZY_MATCH_THRESHOLD
    
    if not target_name or not candidates:
        return None, 0
    
    normalized_target = normalize_name(target_name)
    normalized_candidates = {normalize_name(c): c for c in candidates}
    
    # Try exact match first
    if normalized_target in normalized_candidates:
        return normalized_candidates[normalized_target], 100
    
    # Try variant matches
    for variant in create_name_variants(target_name):
        if variant in normalized_candidates:
            return normalized_candidates[variant], 100
    
    # Use fuzzy matching if available
    if RAPIDFUZZ_AVAILABLE:
        # Create normalized lookup
        candidate_list = list(normalized_candidates.keys())
        
        # Try all variants
        best_match = None
        best_score = 0
        
        for variant in create_name_variants(target_name):
            result = process.extractOne(
                variant,
                candidate_list,
                scorer=fuzz.ratio,
                score_cutoff=threshold
            )
            
            if result and result[1] > best_score:
                best_match = normalized_candidates[result[0]]
                best_score = result[1]
        
        if best_match:
            return best_match, best_score
    
    return None, 0


def match_horses_to_odds(pdf_horses: list, odds_horses: dict, log_unmatched: bool = None) -> dict:
    """
    Match PDF horse names to odds data.
    
    Args:
        pdf_horses: List of horse dicts with 'name' key from PDF
        odds_horses: Dict of {normalized_name: odds_value} from odds source
    
    Returns:
        Dict mapping PDF horse name to odds value, or None if unmatched
    """
    if log_unmatched is None:
        log_unmatched = config.LOG_UNMATCHED
    
    matched = {}
    unmatched = []
    
    odds_names = list(odds_horses.keys())
    
    for horse in pdf_horses:
        pdf_name = horse.get('name', '')
        if not pdf_name:
            continue
        
        matched_name, score = match_name(pdf_name, odds_names)
        
        if matched_name:
            matched[pdf_name] = {
                'odds': odds_horses[matched_name],
                'matched_to': matched_name,
                'score': score
            }
        else:
            matched[pdf_name] = None
            unmatched.append(pdf_name)
    
    if log_unmatched and unmatched:
        print(f"    ⚠ Unmatched names ({len(unmatched)}): {', '.join(unmatched[:5])}")
    
    return matched


def build_odds_lookup(odds_data: list) -> dict:
    """
    Build a normalized lookup dict from odds data.
    
    Args:
        odds_data: List of dicts with 'name' and 'best_odds' or 'odds' keys
    
    Returns:
        Dict of {normalized_name: best_odds_value}
    """
    lookup = {}
    
    for horse in odds_data:
        name = horse.get('name', '')
        if not name:
            continue
        
        # Try different keys for odds
        odds = horse.get('best_odds') or horse.get('odds')
        if isinstance(odds, dict):
            # If odds is a dict of bookmakers, get the best one
            valid_odds = [v for v in odds.values() if v and v < 500]
            odds = max(valid_odds) if valid_odds else None
        
        if odds and odds > 1:
            normalized = normalize_name(name)
            lookup[normalized] = odds
    
    return lookup
