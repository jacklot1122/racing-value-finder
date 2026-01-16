"""
Main Value Finder orchestrator
Integrates with existing downloader/scraper and runs the analysis pipeline
"""

import os
import sys

from . import config
from .model import ProbabilityModel, analyze_races
from .dutching import DutchingCalculator, find_value_dutch_opportunities
from .odds_provider import create_odds_provider
from .report import ReportGenerator, generate_quick_discord_message


class ValueFinder:
    """
    Main orchestrator for the Value Finder + Dutching Engine.
    
    Integrates with existing FormAnalyzer and OddsScraper data.
    """
    
    def __init__(self, download_folder: str, bankroll: float = None):
        """
        Initialize the value finder.
        
        Args:
            download_folder: Path to today's racing_forms folder
            bankroll: Bankroll for dutching calculations
        """
        self.download_folder = download_folder
        self.bankroll = bankroll or config.BANKROLL
        self.model = ProbabilityModel()
        self.dutch_calculator = DutchingCalculator(self.bankroll)
        self.report_generator = ReportGenerator(download_folder)
        
        # Will hold analysis results
        self.analyses = []
    
    def run(self, 
            race_data: list, 
            odds_data: list = None,
            odds_json_path: str = None,
            odds_csv_path: str = None) -> list:
        """
        Run the complete value finding analysis.
        
        Args:
            race_data: List of race dicts from FormAnalyzer.all_races
            odds_data: List of race dicts from OddsScraper.all_odds
            odds_json_path: Path to odds_data.json (alternative to odds_data)
            odds_csv_path: Path to manual odds CSV (fallback)
        
        Returns:
            List of RaceAnalysis objects with all calculations
        """
        print("\n" + "=" * 70)
        print("🎯 VALUE FINDER + DUTCHING ENGINE")
        print("=" * 70)
        print(f"   Bankroll: ${self.bankroll:.0f}")
        print(f"   Field size: {config.FIELD_MIN}-{config.FIELD_MAX} runners")
        print(f"   Min edge: {config.EDGE_MIN*100:.0f}%")
        print(f"   Dud favourite gap: {config.FAV_DUD_GAP*100:.0f}%")
        
        # Create odds provider
        if odds_json_path is None:
            odds_json_path = os.path.join(self.download_folder, "odds_data.json")
        
        odds_provider = create_odds_provider(
            scraped_odds=odds_data,
            json_path=odds_json_path,
            csv_path=odds_csv_path
        )
        
        # Count races with valid odds
        races_with_odds = 0
        for race in race_data:
            venue = race.get('venue', '')
            race_num = race.get('race_number', 0)
            odds = odds_provider.get_odds(venue, '', race_num)
            if odds:
                races_with_odds += 1
        
        print(f"\n→ Found odds for {races_with_odds}/{len(race_data)} races")
        
        # Analyze all races
        print("→ Calculating probabilities and value...")
        self.analyses = analyze_races(race_data, odds_provider, self.model)
        
        print(f"→ Analyzed {len(self.analyses)} races (field {config.FIELD_MIN}-{config.FIELD_MAX})")
        
        # Find dutch opportunities
        print("→ Finding dutching opportunities...")
        opportunities = find_value_dutch_opportunities(self.analyses, self.dutch_calculator)
        
        # Generate reports
        files = self.report_generator.generate_all_reports(self.analyses, self.bankroll)
        
        # Print quick Discord message
        print("\n" + "=" * 70)
        print("📱 DISCORD QUICK MESSAGE (copy below)")
        print("=" * 70)
        print(generate_quick_discord_message(self.analyses, self.bankroll))
        print("=" * 70)
        
        return self.analyses


def run_value_finder_standalone(download_folder: str):
    """
    Run value finder using saved JSON data files.
    
    Call this if you've already downloaded PDFs and scraped odds.
    """
    import json
    import glob
    
    # Try to load odds data
    odds_json = os.path.join(download_folder, "odds_data.json")
    odds_data = None
    if os.path.exists(odds_json):
        with open(odds_json, 'r') as f:
            odds_data = json.load(f)
        print(f"✓ Loaded odds from {odds_json}")
    else:
        print(f"⚠ No odds_data.json found in {download_folder}")
    
    # We need race data from PDFs - this requires FormAnalyzer
    # For standalone, we'll parse the form_analysis.csv if it exists
    form_csv = os.path.join(download_folder, "form_analysis.csv")
    
    if os.path.exists(form_csv):
        import csv
        
        # Reconstruct race_data from CSV
        race_data = {}
        with open(form_csv, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                venue = row.get('Venue', '')
                race_num = int(row.get('Race', 0))
                key = (venue, race_num)
                
                if key not in race_data:
                    race_data[key] = {
                        'venue': venue,
                        'race_number': race_num,
                        'race_name': row.get('Race Name', ''),
                        'horses': []
                    }
                
                race_data[key]['horses'].append({
                    'barrier': int(row.get('Barrier', 0)),
                    'name': row.get('Horse', ''),
                    'form': row.get('Form', ''),
                    'form_score': float(row.get('Form Score', 0))
                })
        
        races = list(race_data.values())
        print(f"✓ Loaded {len(races)} races from form_analysis.csv")
        
        # Run value finder
        finder = ValueFinder(download_folder)
        return finder.run(races, odds_data=odds_data)
    
    else:
        print("✗ No form_analysis.csv found. Run main script first to generate data.")
        return []


if __name__ == "__main__":
    # Example standalone usage
    import sys
    
    if len(sys.argv) > 1:
        folder = sys.argv[1]
    else:
        # Try to find today's folder
        from datetime import datetime
        today = datetime.now().strftime("%Y%m%d")
        script_dir = os.path.dirname(os.path.abspath(__file__))
        folder = os.path.join(os.path.dirname(script_dir), f"racing_forms_{today}")
    
    if os.path.exists(folder):
        run_value_finder_standalone(folder)
    else:
        print(f"Folder not found: {folder}")
        print("Usage: python value_finder.py <path_to_racing_forms_folder>")
