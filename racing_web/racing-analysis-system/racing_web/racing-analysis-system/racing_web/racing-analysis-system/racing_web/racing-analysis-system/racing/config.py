"""
Configuration settings for Value Finder + Dutching Engine
"""

# =============================================================================
# RACE FILTERS
# =============================================================================
FIELD_MAX = 10          # Only analyze races with this many runners or fewer
FIELD_MIN = 4           # Minimum runners (too few = unreliable)

# =============================================================================
# MODEL PARAMETERS
# =============================================================================
TEMP = 15.0             # Softmax temperature (higher = more spread, lower = sharper)
STRENGTH_FLOOR = 5.0    # Minimum strength to add to form scores
FAV_BIAS_CORRECTION = 0.02  # Reduce favourite probability by this amount (optional)

# =============================================================================
# VALUE DETECTION
# =============================================================================
EDGE_MIN = 0.03         # Minimum edge (model_prob - implied_prob) to flag as value
VALUE_ROI_MIN = 0.05    # Minimum expected ROI to consider
ODDS_MIN = 1.5          # Ignore horses with odds below this
ODDS_MAX = 30.0         # Ignore horses with odds above this

# =============================================================================
# DUTCHING PARAMETERS
# =============================================================================
BANKROLL = 100.0        # Default bankroll for dutch calculations
DUTCH_MIN_COMBINED_PROB = 0.55  # Minimum combined model probability for dutch set
DUTCH_MAX_RUNNERS = 4   # Maximum runners in a dutch set
DUTCH_MIN_RUNNERS = 2   # Minimum runners in a dutch set

# =============================================================================
# DUD FAVOURITE DETECTION
# =============================================================================
FAV_DUD_GAP = 0.08      # If favourite implied_prob - model_prob >= this, flag as dud
FAV_ODDS_MAX = 5.0      # Only consider favourites shorter than this

# =============================================================================
# NAME MATCHING
# =============================================================================
FUZZY_MATCH_THRESHOLD = 85  # Minimum score for fuzzy match (0-100)
LOG_UNMATCHED = True        # Log names that couldn't be matched

# =============================================================================
# OUTPUT
# =============================================================================
DISCORD_MAX_RACES = 15      # Max races to include in Discord report
SHOW_TOP_VALUE_BACKS = 3    # How many value backs to show per race
CSV_DECIMAL_PLACES = 4      # Decimal precision in CSV output

# =============================================================================
# ODDS SOURCES
# =============================================================================
ODDS_CSV_FALLBACK = True    # Use CSV if scraping fails
ODDS_CSV_PATH = None        # Path to manual odds CSV (set at runtime)
PREFERRED_BOOKMAKER = "tab"  # Which bookmaker odds to use if multiple available
