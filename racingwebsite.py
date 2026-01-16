"""
Racing Form Guide Downloader & Analyzer
Downloads all race form guides from punters.com.au and analyzes horse form
Includes odds comparison scraping from all bookmakers
"""

import os
import sys
import time
import re
import glob
import shutil
import json
from datetime import datetime
import requests
from playwright.sync_api import sync_playwright
import pdfplumber
import pandas as pd

# Fix encoding issues on Windows
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# Add racing module to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Import Value Finder components (optional - gracefully handle if not installed)
try:
    from racing import (
        ValueFinder,
        config as vf_config,
        generate_quick_discord_message
    )
    VALUE_FINDER_AVAILABLE = True
except ImportError:
    VALUE_FINDER_AVAILABLE = False
    print("Note: Value Finder module not found. Basic analysis only.")


class OddsScraper:
    """Scrapes odds comparison data from race pages"""
    
    def __init__(self, download_folder):
        self.download_folder = download_folder
        self.all_odds = []
        self.bookmakers = []
        
    def scrape_odds_from_page(self, page, race_url, venue, race_number):
        """Extract odds comparison table from a race page"""
        try:
            page.goto(race_url, timeout=30000)
            time.sleep(2)
            
            # Wait for odds table to load
            try:
                page.wait_for_selector('table.compare-odds__table', timeout=10000)
            except:
                print(f"    ⚠ No odds table found")
                return None
            
            race_odds = {
                'venue': venue,
                'race_number': race_number,
                'url': race_url,
                'horses': []
            }
            
            # Extract bookmaker names from header
            bookmaker_headers = page.query_selector_all('table.compare-odds__table thead th img')
            bookmakers = []
            for img in bookmaker_headers:
                alt = img.get_attribute('alt')
                if alt:
                    bookmakers.append(alt)
            
            if not self.bookmakers:
                self.bookmakers = bookmakers
            
            # Extract odds for each horse
            rows = page.query_selector_all('table.compare-odds__table tbody tr.compare-odds-selection')
            
            for row in rows:
                try:
                    # Get horse info from first cell
                    runner_cell = row.query_selector('.compare-odds-selection__runner')
                    if not runner_cell:
                        continue
                    
                    # Extract horse number and name
                    competitor = row.query_selector('.selection-runner__competitor')
                    if competitor:
                        text = competitor.inner_text().strip()
                        # Parse "2. Flying Wahine (2)" format
                        match = re.match(r'(\d+)\.\s*(.+?)\s*\((\d+)\)', text)
                        if match:
                            horse_num = match.group(1)
                            horse_name = match.group(2).strip()
                            barrier = match.group(3)
                        else:
                            continue
                    else:
                        continue
                    
                    # Extract jockey and trainer
                    jockey_elem = row.query_selector('.selection-runner__jockey a')
                    trainer_elem = row.query_selector('.selection-runner__trainer a')
                    jockey = jockey_elem.inner_text().strip() if jockey_elem else ""
                    trainer = trainer_elem.inner_text().strip() if trainer_elem else ""
                    
                    # Extract odds from each bookmaker cell
                    odds_cells = row.query_selector_all('.compare-odds-selection__cell')
                    horse_odds = {}
                    
                    # Skip first cell (runner info), get odds from remaining cells
                    for i, cell in enumerate(odds_cells[1:]):  # Skip first cell
                        odds_link = cell.query_selector('a.compare-odds-selection__cell--link')
                        if odds_link:
                            odds_text = odds_link.inner_text().strip()
                            # Clean odds value (remove $)
                            odds_value = odds_text.replace('$', '')
                            try:
                                odds_float = float(odds_value)
                            except:
                                odds_float = None
                            
                            if i < len(bookmakers):
                                horse_odds[bookmakers[i]] = odds_float
                    
                    # Check if this horse has best odds marker
                    best_marker = row.query_selector('.compare-odds-selection__cell--best-pill')
                    has_best = best_marker is not None
                    
                    horse_data = {
                        'number': int(horse_num),
                        'name': horse_name,
                        'barrier': int(barrier),
                        'jockey': jockey,
                        'trainer': trainer,
                        'odds': horse_odds,
                        'has_best_odds': has_best
                    }
                    
                    # Calculate best odds and where
                    if horse_odds:
                        valid_odds = {k: v for k, v in horse_odds.items() if v is not None and v < 500}
                        if valid_odds:
                            best_bookie = max(valid_odds, key=valid_odds.get)
                            horse_data['best_odds'] = valid_odds[best_bookie]
                            horse_data['best_bookmaker'] = best_bookie
                            horse_data['avg_odds'] = sum(valid_odds.values()) / len(valid_odds)
                    
                    race_odds['horses'].append(horse_data)
                    
                except Exception as e:
                    continue
            
            if race_odds['horses']:
                self.all_odds.append(race_odds)
                return race_odds
            
        except Exception as e:
            print(f"    ✗ Error scraping odds: {e}")
        
        return None
    
    def get_value_bets(self, min_odds_diff=0.5):
        """Find horses where one bookmaker has significantly better odds"""
        value_bets = []
        
        for race in self.all_odds:
            for horse in race['horses']:
                if 'best_odds' in horse and 'avg_odds' in horse:
                    diff = horse['best_odds'] - horse['avg_odds']
                    if diff >= min_odds_diff:
                        value_bets.append({
                            'venue': race['venue'],
                            'race': race['race_number'],
                            'horse': horse['name'],
                            'best_odds': horse['best_odds'],
                            'best_at': horse.get('best_bookmaker', 'Unknown'),
                            'avg_odds': round(horse['avg_odds'], 2),
                            'value_diff': round(diff, 2)
                        })
        
        return sorted(value_bets, key=lambda x: x['value_diff'], reverse=True)
    
    def save_odds_report(self):
        """Save odds data to CSV and JSON"""
        if not self.all_odds:
            return
        
        # Flatten for CSV
        all_rows = []
        for race in self.all_odds:
            for horse in race['horses']:
                row = {
                    'Venue': race['venue'],
                    'Race': race['race_number'],
                    'Number': horse['number'],
                    'Horse': horse['name'],
                    'Barrier': horse['barrier'],
                    'Jockey': horse['jockey'],
                    'Trainer': horse['trainer'],
                    'Best Odds': horse.get('best_odds', ''),
                    'Best At': horse.get('best_bookmaker', ''),
                    'Avg Odds': round(horse.get('avg_odds', 0), 2) if horse.get('avg_odds') else ''
                }
                # Add individual bookmaker odds
                for bookie in self.bookmakers:
                    row[bookie] = horse['odds'].get(bookie, '')
                all_rows.append(row)
        
        if all_rows:
            df = pd.DataFrame(all_rows)
            csv_path = os.path.join(self.download_folder, "odds_comparison.csv")
            df.to_csv(csv_path, index=False)
            print(f"\n📊 Odds comparison saved to: odds_comparison.csv")
            
            # Save JSON for detailed data
            json_path = os.path.join(self.download_folder, "odds_data.json")
            with open(json_path, 'w') as f:
                json.dump(self.all_odds, f, indent=2)
            print(f"📊 Detailed odds data saved to: odds_data.json")
        
        # Save value bets report
        value_bets = self.get_value_bets()
        if value_bets:
            vb_df = pd.DataFrame(value_bets)
            vb_path = os.path.join(self.download_folder, "value_bets.csv")
            vb_df.to_csv(vb_path, index=False)
            print(f"📊 Value bets saved to: value_bets.csv")
    
    def print_odds_summary(self):
        """Print summary of odds data"""
        if not self.all_odds:
            print("\nNo odds data collected.")
            return
        
        print("\n" + "=" * 70)
        print("ODDS COMPARISON SUMMARY")
        print("=" * 70)
        
        for race in self.all_odds:
            print(f"\n🏇 {race['venue']} - Race {race['race_number']}")
            print("-" * 60)
            
            # Sort by best odds (favorites first)
            sorted_horses = sorted(race['horses'], 
                                   key=lambda x: x.get('best_odds', 999))
            
            print(f"  {'#':<3} {'Horse':<22} {'Best Odds':<10} {'Best At':<15} {'Avg':<8}")
            print(f"  {'-'*3} {'-'*22} {'-'*10} {'-'*15} {'-'*8}")
            
            for horse in sorted_horses[:8]:  # Top 8
                num = horse['number']
                name = horse['name'][:20]
                best = f"${horse.get('best_odds', '-')}"
                best_at = horse.get('best_bookmaker', '-')[:13]
                avg = f"${horse.get('avg_odds', 0):.2f}" if horse.get('avg_odds') else '-'
                print(f"  {num:<3} {name:<22} {best:<10} {best_at:<15} {avg:<8}")
        
        # Print value bets
        value_bets = self.get_value_bets(0.3)
        if value_bets:
            print("\n" + "=" * 70)
            print("💰 VALUE BETS (Odds significantly above average)")
            print("=" * 70)
            for vb in value_bets[:10]:
                print(f"  {vb['venue']} R{vb['race']}: {vb['horse']}")
                print(f"    → ${vb['best_odds']} at {vb['best_at']} (avg ${vb['avg_odds']}, +${vb['value_diff']})")
            print()

class FormAnalyzer:
    """Analyzes horse racing form from PDF data"""
    
    def __init__(self, download_folder):
        self.download_folder = download_folder
        self.all_races = []
    
    def is_australian_venue(self, venue_folder):
        """Check if folder is for an Australian venue"""
        venue_lower = venue_folder.lower()
        
        # Reject any with international suffixes (must END with the suffix)
        international_suffixes = [
            '_nz', '_us', '_uk', '_za', '_fr', '_jp', '_tr', '_hk', '_sg',
            '_ie', '_ae', '_kr', '_in', '_my', '_ph', '_cl', '_ar', '_br',
            '_de', '_it', '_es', '_se', '_no', '_dk', '_be', '_nl', '_at',
            '_ch', '_cz', '_pl', '_hu', '_ro', '_bg', '_hr', '_sk', '_si',
            '_ca', '_mx', '_pe', '_uy', '_qa', '_bh', '_sa', '_om', '_kw'
        ]
        
        for suffix in international_suffixes:
            if venue_lower.endswith(suffix):
                return False
        
        # Known international venue names (exact match or unique identifiers)
        international_names = [
            'cagnessurmer', 'pau_fr', 'nagoya_jp', 'ohi_jp', 'fairview_za', 
            'vaal_za', 'antalya_tr', 'izmir_tr', 'sha_tin', 'happy_valley', 
            'meydan', 'kranji', 'longchamp', 'chantilly', 'deauville', 
            'newmarket_uk', 'ascot_uk', 'te_rapa_nz', 'trentham', 'ellerslie', 
            'aqueduct_us', 'gulfstream'
        ]
        
        for name in international_names:
            if name in venue_lower:
                return False
        
        return True
        
    def extract_text_from_pdf(self, pdf_path):
        """Extract all text from a PDF file"""
        text = ""
        try:
            with pdfplumber.open(pdf_path) as pdf:
                for page in pdf.pages:
                    text += page.extract_text() or ""
                    text += "\n\n"
        except Exception as e:
            print(f"  Error reading PDF: {e}")
        return text
    
    def parse_race_data(self, text, venue):
        """Parse race and horse data from extracted text"""
        races = []
        
        # Split by race markers - look for "Race X" patterns
        race_sections = re.split(r'(?=Race\s+\d+\s)', text, flags=re.IGNORECASE)
        
        for section in race_sections:
            if not section.strip():
                continue
                
            # Try to extract race number and name
            race_match = re.match(r'Race\s+(\d+)\s*[-–]?\s*(.+?)(?:\n|$)', section, re.IGNORECASE)
            if not race_match:
                continue
                
            race_num = race_match.group(1)
            race_name = race_match.group(2).strip()[:50]
            
            race_data = {
                'venue': venue,
                'race_number': int(race_num),
                'race_name': race_name,
                'horses': []
            }
            
            # Extract horse entries - look for numbered entries
            # Pattern: number followed by horse name, then form figures
            horse_pattern = r'(\d{1,2})\s+([A-Z][A-Za-z\'\s]+?)(?:\s+\([\d\w]+\))?\s*([1-9x0]\s*){0,10}'
            
            lines = section.split('\n')
            for line in lines:
                # Look for barrier/horse number at start of line
                entry_match = re.match(r'^(\d{1,2})\s+([A-Z][A-Za-z\'\-\s]{2,25})', line)
                if entry_match:
                    barrier = entry_match.group(1)
                    horse_name = entry_match.group(2).strip()
                    
                    # Extract form figures (last starts: 1,2,3,4,5,6,7,8,9,0,x)
                    form_match = re.search(r'([1-9x0]{1,10})\s*$', line)
                    form = form_match.group(1) if form_match else ""
                    
                    # Try to find weight
                    weight_match = re.search(r'(\d{2,3}\.?\d?)\s*kg', line, re.IGNORECASE)
                    weight = weight_match.group(1) if weight_match else ""
                    
                    horse_data = {
                        'barrier': int(barrier),
                        'name': horse_name,
                        'form': form,
                        'weight': weight,
                        'form_score': self.calculate_form_score(form)
                    }
                    race_data['horses'].append(horse_data)
            
            if race_data['horses']:
                races.append(race_data)
        
        return races
    
    def calculate_form_score(self, form):
        """Calculate a score based on recent form figures"""
        if not form:
            return 0
        
        score = 0
        weights = [5, 4, 3, 2, 1]  # Most recent runs weighted higher
        
        for i, char in enumerate(form[:5]):  # Last 5 starts
            weight = weights[i] if i < len(weights) else 1
            if char == '1':
                score += 10 * weight
            elif char == '2':
                score += 7 * weight
            elif char == '3':
                score += 5 * weight
            elif char == '4':
                score += 3 * weight
            elif char == '5':
                score += 2 * weight
            elif char in '6789':
                score += 1 * weight
            elif char in 'x0':
                score -= 2 * weight
        
        return score
    
    def analyze_all_pdfs(self):
        """Analyze all PDFs in the download folder"""
        print("\n" + "=" * 60)
        print("FORM ANALYSIS")
        print("=" * 60)
        
        # Find all PDF files
        pdf_files = glob.glob(os.path.join(self.download_folder, "**", "*.pdf"), recursive=True)
        
        if not pdf_files:
            print("No PDF files found to analyze.")
            return
        
        # Filter to only AU venues
        au_pdfs = []
        skipped_intl = 0
        for pdf_path in pdf_files:
            venue_folder = os.path.basename(os.path.dirname(pdf_path))
            if self.is_australian_venue(venue_folder):
                au_pdfs.append(pdf_path)
            else:
                skipped_intl += 1
        
        if skipped_intl > 0:
            print(f"\n→ Skipping {skipped_intl} international form guides")
        
        if not au_pdfs:
            print("No Australian PDF files found to analyze.")
            return
        
        print(f"→ Analyzing {len(au_pdfs)} Australian form guides...\n")
        
        for pdf_path in au_pdfs:
            venue_folder = os.path.basename(os.path.dirname(pdf_path))
            venue = venue_folder.split('_', 1)[1] if '_' in venue_folder else venue_folder
            venue = venue.replace('_', ' ').title()
            
            print(f"📋 {venue}")
            
            text = self.extract_text_from_pdf(pdf_path)
            races = self.parse_race_data(text, venue)
            
            if races:
                self.all_races.extend(races)
                print(f"   Found {len(races)} races")
            else:
                print(f"   Could not parse race data")
        
        # Generate analysis report
        self.generate_report()
    
    def generate_report(self):
        """Generate form analysis report"""
        if not self.all_races:
            print("\nNo race data to analyze.")
            return
        
        print("\n" + "=" * 60)
        print("TOP PICKS BY FORM")
        print("=" * 60)
        
        for race in self.all_races:
            if not race['horses']:
                continue
            
            # Sort horses by form score
            sorted_horses = sorted(race['horses'], key=lambda x: x['form_score'], reverse=True)
            
            print(f"\n🏇 {race['venue']} - Race {race['race_number']}: {race['race_name']}")
            print("-" * 50)
            
            # Show top 3 and bottom 2
            print("  TOP PICKS:")
            for i, horse in enumerate(sorted_horses[:3], 1):
                rating = self.get_rating(horse['form_score'])
                form_display = horse['form'] if horse['form'] else "N/A"
                print(f"    {i}. {horse['name']:<20} Form: {form_display:<10} Score: {horse['form_score']:>3} {rating}")
            
            if len(sorted_horses) > 5:
                print("  AVOID:")
                for horse in sorted_horses[-2:]:
                    rating = self.get_rating(horse['form_score'])
                    form_display = horse['form'] if horse['form'] else "N/A"
                    print(f"    ⚠ {horse['name']:<20} Form: {form_display:<10} Score: {horse['form_score']:>3} {rating}")
        
        # Save detailed report to CSV
        self.save_detailed_report()
    
    def get_rating(self, score):
        """Convert score to star rating"""
        if score >= 80:
            return "⭐⭐⭐⭐⭐"
        elif score >= 60:
            return "⭐⭐⭐⭐"
        elif score >= 40:
            return "⭐⭐⭐"
        elif score >= 20:
            return "⭐⭐"
        elif score >= 0:
            return "⭐"
        else:
            return "❌"
    
    def save_detailed_report(self):
        """Save detailed analysis to CSV file"""
        all_horses = []
        for race in self.all_races:
            for horse in race['horses']:
                all_horses.append({
                    'Venue': race['venue'],
                    'Race': race['race_number'],
                    'Race Name': race['race_name'],
                    'Barrier': horse['barrier'],
                    'Horse': horse['name'],
                    'Form': horse['form'],
                    'Weight': horse['weight'],
                    'Form Score': horse['form_score'],
                    'Rating': self.get_rating(horse['form_score'])
                })
        
        if all_horses:
            df = pd.DataFrame(all_horses)
            csv_path = os.path.join(self.download_folder, "form_analysis.csv")
            df.to_csv(csv_path, index=False)
            print(f"\n📊 Detailed analysis saved to: form_analysis.csv")


class RacingFormDownloader:
    def __init__(self):
        self.base_url = "https://www.punters.com.au/form-guide/"
        self.script_dir = os.path.dirname(os.path.abspath(__file__))
        self.existing_pdfs = {}  # Track existing PDFs by their URL hash
        self.download_folder = self.setup_download_folder()
        self.race_urls = []  # Store individual race URLs for odds scraping
        self.abandoned_venues = set()  # Track abandoned meetings
        
    def setup_download_folder(self):
        """Create today's folder and check for existing downloads"""
        today = datetime.now().strftime("%Y%m%d")
        folder_name = f"racing_forms_{today}"
        download_path = os.path.join(self.script_dir, folder_name)
        
        # Find and consolidate any existing downloads from today only
        self.consolidate_existing_downloads(today, download_path)
        
        os.makedirs(download_path, exist_ok=True)
        print(f"✓ Download folder: {download_path}")
        print(f"  Date: {datetime.now().strftime('%A, %d %B %Y')}")
        return download_path
    
    def consolidate_existing_downloads(self, today, target_folder):
        """Find existing PDFs and move them to correct structure, delete duplicates"""
        print("→ Checking for existing downloads...")
        
        # Find all racing_forms folders from today
        pattern = os.path.join(self.script_dir, f"racing_forms_{today}*")
        existing_folders = glob.glob(pattern)
        
        # Also check for any PDFs in timestamped folders
        all_pdfs = []
        for folder in existing_folders:
            if os.path.isdir(folder):
                for root, dirs, files in os.walk(folder):
                    for f in files:
                        if f.endswith('.pdf'):
                            all_pdfs.append(os.path.join(root, f))
        
        if all_pdfs:
            print(f"  Found {len(all_pdfs)} existing PDFs")
            
            # Create target folder
            os.makedirs(target_folder, exist_ok=True)
            
            # Track unique PDFs by their puntcdn ID
            unique_pdfs = {}
            for pdf_path in all_pdfs:
                # Extract the PDF ID from filename or path
                filename = os.path.basename(pdf_path)
                parent_folder = os.path.basename(os.path.dirname(pdf_path))
                
                # Use parent folder (date_venue) as key
                if parent_folder.startswith(today):
                    key = parent_folder
                    if key not in unique_pdfs:
                        unique_pdfs[key] = pdf_path
                        self.existing_pdfs[key] = pdf_path
            
            # Move unique PDFs to proper structure
            for key, pdf_path in unique_pdfs.items():
                target_subfolder = os.path.join(target_folder, key)
                os.makedirs(target_subfolder, exist_ok=True)
                target_file = os.path.join(target_subfolder, os.path.basename(pdf_path))
                
                if pdf_path != target_file and not os.path.exists(target_file):
                    shutil.copy2(pdf_path, target_file)
                    self.existing_pdfs[key] = target_file
            
            # Clean up old timestamped folders (but not the main one)
            for folder in existing_folders:
                if folder != target_folder and os.path.isdir(folder):
                    try:
                        shutil.rmtree(folder)
                        print(f"  Cleaned up: {os.path.basename(folder)}")
                    except:
                        pass
            
            print(f"  ✓ {len(self.existing_pdfs)} unique forms ready")
        else:
            print("  No existing downloads found")

    def extract_race_info(self, href):
        """Extract venue, date and race name from URL"""
        # Example: /form-guide/horses/canterbury-20260116/the-agency-real-estate-handicap-race-1/
        pattern = r'/form-guide/horses/([^/]+)/([^/]+)/'
        match = re.search(pattern, href)
        
        if match:
            venue_date = match.group(1)
            race_name = match.group(2)
            
            # Extract date from venue string (last 8 digits)
            date_match = re.search(r'(\d{8})$', venue_date)
            if date_match:
                date = date_match.group(1)
                venue = venue_date.replace(f'-{date}', '').replace('-', '_')
            else:
                date = datetime.now().strftime("%Y%m%d")
                venue = venue_date.replace('-', '_')
            
            return venue, date, race_name.replace('-', '_')
        
        return "unknown", datetime.now().strftime("%Y%m%d"), "unknown"

    def is_australian_track(self, venue):
        """Check if the venue is an Australian track"""
        venue_lower = venue.lower()
        
        # Reject any venue with international country suffixes
        international_suffixes = [
            '_nz', '_us', '_uk', '_za', '_fr', '_jp', '_tr', '_hk', '_sg',
            '_ie', '_ae', '_kr', '_in', '_my', '_ph', '_cl', '_ar', '_br',
            '_de', '_it', '_es', '_se', '_no', '_dk', '_be', '_nl', '_at',
            '_ch', '_cz', '_pl', '_hu', '_ro', '_bg', '_hr', '_sk', '_si',
            '_ca', '_mx', '_pe', '_uy', '_qa', '_bh', '_sa', '_om', '_kw'
        ]
        
        for suffix in international_suffixes:
            if venue_lower.endswith(suffix):
                return False
        
        # Known international venues to exclude (NZ, US, UK, etc.)
        international = [
            # New Zealand
            'te_rapa', 'trentham', 'ellerslie', 'riccarton', 'otaki', 'awapuni',
            'hastings', 'matamata', 'pukekohe', 'ruakaka', 'wanganui', 'woodville',
            'ashburton', 'wingatui', 'riverton', 'oamaru', 'timaru', 'waimate',
            'cromwell', 'kurow', 'omakau', 'roxburgh', 'tapanui', 'waikouaiti',
            # USA
            'aqueduct', 'belmont_park', 'santa_anita', 'gulfstream', 'del_mar',
            'churchill', 'keeneland', 'saratoga', 'pimlico', 'laurel', 'parx',
            'oaklawn', 'tampa_bay', 'fair_grounds', 'turfway', 'golden_gate',
            'los_alamitos', 'penn_national', 'charles_town', 'mountaineer',
            'presque_isle', 'finger_lakes', 'monmouth', 'woodbine',
            # Hong Kong
            'hong_kong', 'sha_tin', 'happy_valley',
            # Singapore
            'singapore', 'kranji',
            # Japan
            'japan', 'tokyo', 'nakayama', 'kyoto', 'hanshin', 'chukyo',
            'nagoya', 'ohi', 'kawasaki', 'funabashi', 'urawa', 'oi',
            # UK (be specific - ascot/newcastle/sandown are also AU tracks)
            'ascot_uk', 'newmarket_uk', 'epsom_uk', 'cheltenham', 'york_uk', 'goodwood',
            'sandown_uk', 'kempton', 'lingfield', 'wolverhampton', 'newcastle_uk',
            # Ireland
            'curragh', 'leopardstown', 'fairyhouse', 'punchestown', 'galway',
            # France
            'longchamp', 'chantilly', 'deauville', 'saint_cloud', 'maisons',
            'cagnes', 'cagnessurmer', 'pau', 'lyon', 'marseille', 'bordeaux',
            # Dubai/UAE
            'dubai', 'meydan', 'abu_dhabi',
            # South Africa
            'turffontein', 'kenilworth', 'greyville', 'scottsville', 'fairview', 'vaal',
            # Turkey
            'antalya', 'izmir', 'istanbul', 'ankara', 'bursa',
            # Other international
            'sha_tin', 'happy_valley', 'seoul', 'busan',
        ]
        
        for intl in international:
            if intl in venue_lower:
                return False
        return True

    def pdf_already_exists(self, meeting_key):
        """Check if we already have the FULL form PDF downloaded"""
        # Check in existing_pdfs dict
        if meeting_key in self.existing_pdfs:
            # Verify it's actually a full form PDF
            pdf_path = self.existing_pdfs[meeting_key]
            if os.path.exists(pdf_path) and 'full_form' in os.path.basename(pdf_path).lower():
                return True
        
        # Also check if folder exists with a full form PDF
        venue_folder = os.path.join(self.download_folder, meeting_key)
        if os.path.exists(venue_folder):
            pdfs = glob.glob(os.path.join(venue_folder, "*full_form*.pdf"))
            if pdfs:
                return True
            
            # Check for any PDF but warn if it's not full form
            all_pdfs = glob.glob(os.path.join(venue_folder, "*.pdf"))
            if all_pdfs and not any('full_form' in p.lower() for p in all_pdfs):
                # Has old single-race PDFs, delete them and re-download
                for pdf in all_pdfs:
                    try:
                        os.remove(pdf)
                    except:
                        pass
                return False
        return False

    def download_pdf(self, pdf_url, venue, date, race_name):
        """Download PDF file with proper naming"""
        # Create subfolder for venue and date
        venue_folder = os.path.join(self.download_folder, f"{date}_{venue}")
        os.makedirs(venue_folder, exist_ok=True)
        
        # Create filename
        filename = f"{race_name}.pdf"
        filepath = os.path.join(venue_folder, filename)
        
        try:
            response = requests.get(pdf_url, timeout=30)
            response.raise_for_status()
            
            with open(filepath, 'wb') as f:
                f.write(response.content)
            
            print(f"    ✓ Downloaded: {filename}")
            return True
            
        except Exception as e:
            print(f"    ✗ Failed to download {filename}: {e}")
            return False

    def run(self):
        """Main execution function"""
        print("=" * 60)
        print("Racing Form Guide Downloader (AU Only)")
        print("=" * 60)
        
        successful = 0
        failed = 0
        skipped = 0
        
        with sync_playwright() as p:
            # Try Firefox first - often better at bypassing bot detection in headless
            print("\n→ Starting headless browser...")
            try:
                browser = p.firefox.launch(headless=True)
            except:
                # Fallback to chromium
                browser = p.chromium.launch(
                    headless=True,
                    args=['--disable-blink-features=AutomationControlled']
                )
            
            context = browser.new_context(
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0',
                viewport={'width': 1920, 'height': 1080}
            )
            
            page = context.new_page()
            
            try:
                # Go to form guide page
                print(f"→ Loading {self.base_url}")
                page.goto(self.base_url, timeout=60000, wait_until='domcontentloaded')
                
                # Wait for page to fully load
                print("→ Waiting for page to load...")
                time.sleep(3)
                
                # Check if we got blocked or need to wait more
                page_content = page.content()
                if 'checking your browser' in page_content.lower() or len(page_content) < 5000:
                    print("→ Cloudflare check detected, waiting...")
                    time.sleep(5)
                    page_content = page.content()
                
                # Try to wait for race cards
                try:
                    page.wait_for_selector('a.event-card', timeout=15000)
                    print("→ Found race cards")
                except:
                    print("→ Looking for alternative selectors...")
                    try:
                        page.wait_for_selector('a[href*="/form-guide/horses/"]', timeout=10000)
                        print("→ Found race links")
                    except:
                        print("→ Still waiting for content...")
                        time.sleep(5)
                
                # Get all race card links
                race_cards = page.query_selector_all('a.event-card[href*="/form-guide/"]')
                
                if not race_cards:
                    race_cards = page.query_selector_all('a[href*="/form-guide/horses/"]')
                
                # First, detect abandoned meetings by looking at the page structure
                # Look for meeting sections/headers that contain ABANDONED
                abandoned_meetings = set()
                
                # Check for abandoned indicators in different places on the page
                page_html = page.content().upper()
                
                # Find all meeting sections - look for venue names with ABANDONED nearby
                meeting_sections = page.query_selector_all('[class*="meeting"], [class*="event-group"], section')
                for section in meeting_sections:
                    try:
                        section_text = section.inner_text().upper()
                        if 'ABANDONED' in section_text:
                            # Try to extract venue from this section
                            links = section.query_selector_all('a[href*="/form-guide/horses/"]')
                            for link in links:
                                href = link.get_attribute('href')
                                if href:
                                    venue, date, _ = self.extract_race_info(href)
                                    meeting_key = f"{date}_{venue}"
                                    abandoned_meetings.add(meeting_key)
                    except:
                        pass
                
                # Also check by looking at status badges directly
                status_badges = page.query_selector_all('[class*="status"], [class*="badge"], .event-status')
                for badge in status_badges:
                    try:
                        if 'ABANDONED' in badge.inner_text().upper():
                            # Find nearest race link
                            parent = badge
                            for _ in range(5):  # Walk up 5 levels
                                parent = parent.query_selector('xpath=..')
                                if parent:
                                    link = parent.query_selector('a[href*="/form-guide/horses/"]')
                                    if link:
                                        href = link.get_attribute('href')
                                        venue, date, _ = self.extract_race_info(href)
                                        meeting_key = f"{date}_{venue}"
                                        abandoned_meetings.add(meeting_key)
                                        break
                    except:
                        pass
                
                # Extract unique MEETINGS (not races) - one PDF per venue, AU only
                meetings = {}
                international_skipped = 0
                
                for card in race_cards:
                    href = card.get_attribute('href')
                    if href and '/form-guide/horses/' in href:
                        full_url = f"https://www.punters.com.au{href}" if not href.startswith('http') else href
                        full_url = full_url.split('#')[0]
                        
                        # Extract venue and date to group by meeting
                        venue, date, race_name = self.extract_race_info(full_url)
                        meeting_key = f"{date}_{venue}"
                        
                        # Skip non-Australian tracks
                        if not self.is_australian_track(venue):
                            international_skipped += 1
                            continue
                        
                        # Skip if already marked as abandoned
                        if meeting_key in abandoned_meetings:
                            continue
                        
                        # Check if this specific card shows abandoned
                        try:
                            card_text = card.inner_text().upper()
                            # Check the card and its immediate container
                            parent = card.query_selector('xpath=..')
                            if parent:
                                parent_text = parent.inner_text().upper()
                                if 'ABANDONED' in parent_text:
                                    abandoned_meetings.add(meeting_key)
                                    continue
                            if 'ABANDONED' in card_text:
                                abandoned_meetings.add(meeting_key)
                                continue
                        except:
                            pass
                        
                        # Store race URL for odds scraping
                        race_match = re.search(r'race-(\d+)', race_name)
                        if race_match:
                            race_num = int(race_match.group(1))
                            self.race_urls.append({
                                'url': full_url,
                                'venue': venue.replace('_', ' ').title(),
                                'race_number': race_num,
                                'date': date
                            })
                        
                        # Skip if already marked as abandoned (double check)
                        if meeting_key in abandoned_meetings:
                            continue
                        
                        if meeting_key not in meetings:
                            meetings[meeting_key] = {
                                'url': full_url,
                                'venue': venue,
                                'date': date,
                                'key': meeting_key
                            }
                
                # Store abandoned venues for later use
                for meeting_key in abandoned_meetings:
                    venue_name = meeting_key.split('_', 1)[1] if '_' in meeting_key else meeting_key
                    self.abandoned_venues.add(venue_name)
                
                # Delete any abandoned meeting folders
                if abandoned_meetings:
                    print(f"\n⚠ Found {len(abandoned_meetings)} ABANDONED meetings:")
                    for meeting_key in abandoned_meetings:
                        venue_name = meeting_key.split('_', 1)[1] if '_' in meeting_key else meeting_key
                        print(f"  → {venue_name.replace('_', ' ').title()} - ABANDONED")
                        
                        # Delete folder if it exists
                        folder_path = os.path.join(self.download_folder, meeting_key)
                        if os.path.exists(folder_path):
                            try:
                                shutil.rmtree(folder_path)
                                print(f"    ✓ Deleted existing download")
                            except Exception as e:
                                print(f"    ✗ Could not delete: {e}")
                    print()
                
                print(f"✓ Found {len(meetings)} AU meetings")
                if international_skipped > 0:
                    print(f"  (Skipped {international_skipped} international races)")
                print()
                
                if not meetings:
                    print("✗ No meetings found!")
                    print("→ Saving debug screenshot...")
                    page.screenshot(path=os.path.join(self.download_folder, "debug_screenshot.png"))
                    # Also save page HTML for debugging
                    with open(os.path.join(self.download_folder, "debug_page.html"), 'w', encoding='utf-8') as f:
                        f.write(page.content())
                    print(f"→ Debug files saved to {self.download_folder}")
                    return
                
                # Process each meeting (one PDF per venue)
                meeting_list = list(meetings.values())
                for i, meeting in enumerate(meeting_list, 1):
                    venue = meeting['venue']
                    date = meeting['date']
                    meeting_key = meeting['key']
                    
                    # Check if already downloaded
                    if self.pdf_already_exists(meeting_key):
                        print(f"[{i}/{len(meeting_list)}] {venue} - Already downloaded ✓")
                        skipped += 1
                        continue
                    
                    print(f"[{i}/{len(meeting_list)}] {venue}")
                    
                    try:
                        # Navigate to any race page for this meeting
                        page.goto(meeting['url'], timeout=30000)
                        time.sleep(2)
                        
                        # Check if this meeting is abandoned (check the page content)
                        page_text = page.inner_text('body').upper()
                        if 'ABANDONED' in page_text or 'MEETING ABANDONED' in page_text:
                            print(f"    ⚠ ABANDONED - Skipping")
                            self.abandoned_venues.add(venue)  # Track for later
                            # Delete folder if it exists
                            venue_folder = os.path.join(self.download_folder, meeting_key)
                            if os.path.exists(venue_folder):
                                try:
                                    shutil.rmtree(venue_folder)
                                    print(f"    ✓ Deleted existing folder")
                                except:
                                    pass
                            skipped += 1
                            continue
                        
                        # Look for the "Download Form" button and click it
                        download_btn = page.query_selector('button[data-analytics="Form Guide : Form : Download Form"]')
                        if download_btn:
                            download_btn.click()
                            time.sleep(1)
                        
                        # Find the Full Page A4 PDF link
                        pdf_link = page.query_selector('a[href*="puntcdn.com/form-guides/"][href$=".pdf"]')
                        
                        if pdf_link:
                            pdf_url = pdf_link.get_attribute('href')
                            print(f"    → PDF: {pdf_url}")
                            
                            if self.download_pdf(pdf_url, venue, date, f"{venue}_full_form"):
                                successful += 1
                            else:
                                failed += 1
                        else:
                            print("    ✗ No PDF link found")
                            failed += 1
                            
                    except Exception as e:
                        print(f"    ✗ Error: {e}")
                        failed += 1
                    
                    time.sleep(0.5)
                
            except Exception as e:
                print(f"\n✗ Critical error: {e}")
                try:
                    page.screenshot(path=os.path.join(self.download_folder, "error_screenshot.png"))
                except:
                    pass
            
            finally:
                browser.close()
                print("\n✓ Browser closed")
        
        # Summary
        print("\n" + "=" * 60)
        print("DOWNLOAD COMPLETE")
        print("=" * 60)
        print(f"New downloads: {successful}")
        print(f"Already had: {skipped}")
        print(f"Failed: {failed}")
        print(f"Files saved to: {self.download_folder}")
        print("=" * 60)
        
        return self.download_folder


def main():
    """Main entry point - download forms, scrape odds, and analyze"""
    # Download forms
    downloader = RacingFormDownloader()
    download_folder = downloader.run()
    
    if not download_folder:
        return
    
    # Clean up any international folders that shouldn't be there
    cleanup_international_folders(download_folder)
    
    # Get abandoned venues from downloader (if any detected during download)
    abandoned_venues = getattr(downloader, 'abandoned_venues', set())
    
    # Scrape odds from race pages - always do this, collecting URLs fresh
    print("\n")
    print("=" * 60)
    print("SCRAPING ODDS COMPARISON DATA")
    print("=" * 60)
    
    odds_scraper = OddsScraper(download_folder)
    
    # Collect race URLs from the form guide page (also detects abandoned)
    race_urls, abandoned_venues = collect_race_urls_for_odds(download_folder, abandoned_venues)
    
    # Delete any abandoned venue folders
    if abandoned_venues:
        today = datetime.now().strftime("%Y%m%d")
        for venue in abandoned_venues:
            folder_path = os.path.join(download_folder, f"{today}_{venue}")
            if os.path.exists(folder_path):
                try:
                    shutil.rmtree(folder_path)
                    print(f"  → Deleted abandoned: {venue.replace('_', ' ').title()}")
                except:
                    pass
    
    if race_urls:
        # Sort race URLs by venue and race number
        sorted_races = sorted(race_urls, key=lambda x: (x['venue'], x['race_number']))
        
        print(f"\n→ Found {len(sorted_races)} races to scrape odds from\n")
        
        with sync_playwright() as p:
            try:
                browser = p.firefox.launch(headless=True)
            except:
                browser = p.chromium.launch(headless=True)
            
            context = browser.new_context(
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0',
                viewport={'width': 1920, 'height': 1080}
            )
            page = context.new_page()
            
            current_venue = None
            for i, race in enumerate(sorted_races, 1):
                if race['venue'] != current_venue:
                    current_venue = race['venue']
                    print(f"\n📍 {current_venue}")
                
                print(f"  Race {race['race_number']}...", end=" ", flush=True)
                
                result = odds_scraper.scrape_odds_from_page(
                    page, 
                    race['url'] + "#OddsComparison",
                    race['venue'],
                    race['race_number']
                )
                
                if result:
                    print(f"✓ {len(result['horses'])} runners")
                else:
                    print("✗")
                
                time.sleep(0.5)  # Be nice to the server
            
            browser.close()
        
        # Print and save odds summary
        odds_scraper.print_odds_summary()
        odds_scraper.save_odds_report()
    else:
        print("\n→ No race URLs found for odds scraping")
    
    # Analyze the downloaded forms (basic analysis)
    print("\n")
    analyzer = FormAnalyzer(download_folder)
    analyzer.analyze_all_pdfs()
    
    # Run Value Finder + Dutching Engine if available
    if VALUE_FINDER_AVAILABLE and analyzer.all_races:
        print("\n")
        try:
            value_finder = ValueFinder(download_folder)
            analyses = value_finder.run(
                race_data=analyzer.all_races,
                odds_data=odds_scraper.all_odds if odds_scraper.all_odds else None
            )
        except Exception as e:
            print(f"⚠ Value Finder error: {e}")
            import traceback
            traceback.print_exc()
    elif not VALUE_FINDER_AVAILABLE:
        print("\n💡 Tip: Install the racing module for Value Finder + Dutching analysis")
        print("   This provides probability models, value detection, and dutch recommendations")


def cleanup_international_folders(download_folder):
    """Remove any international venue folders that shouldn't be there"""
    international_markers = [
        '_nz', '_us', '_uk', '_za', '_fr', '_jp', '_tr', '_hk', '_sg',
        '_ie', '_ae', '_kr', '_in', '_my', '_ph', 'cagnes', 'pau', 
        'nagoya', 'ohi', 'fairview', 'vaal', 'antalya', 'izmir',
        'aqueduct', 'te_rapa', 'trentham', 'meydan'
    ]
    
    removed = 0
    for folder in os.listdir(download_folder):
        folder_path = os.path.join(download_folder, folder)
        if os.path.isdir(folder_path):
            folder_lower = folder.lower()
            for marker in international_markers:
                if marker in folder_lower:
                    try:
                        shutil.rmtree(folder_path)
                        removed += 1
                    except:
                        pass
                    break
    
    if removed > 0:
        print(f"→ Cleaned up {removed} international folders")


def collect_race_urls_for_odds(download_folder, abandoned_venues=None):
    """Collect all race URLs for odds scraping from the form guide page"""
    race_urls = []
    today = datetime.now().strftime("%Y%m%d")
    if abandoned_venues is None:
        abandoned_venues = set()
    
    # Get list of AU venues we have PDFs for
    au_venues = set()
    for folder in os.listdir(download_folder):
        folder_path = os.path.join(download_folder, folder)
        if os.path.isdir(folder_path) and folder.startswith(today):
            # Check if it's an AU venue (no international markers)
            folder_lower = folder.lower()
            is_intl = False
            intl_markers = ['_nz', '_us', '_uk', '_za', '_fr', '_jp', '_tr', '_hk', '_sg', '_ie', '_ae']
            for marker in intl_markers:
                if folder_lower.endswith(marker):
                    is_intl = True
                    break
            if not is_intl:
                # Extract venue name
                venue = folder.replace(f"{today}_", "")
                # Skip if this venue was abandoned
                if venue not in abandoned_venues:
                    au_venues.add(venue)
    
    if not au_venues:
        return [], abandoned_venues
    
    print(f"\n→ Collecting race URLs for {len(au_venues)} AU venues...")
    
    with sync_playwright() as p:
        try:
            browser = p.firefox.launch(headless=True)
        except:
            browser = p.chromium.launch(headless=True)
        
        context = browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0',
            viewport={'width': 1920, 'height': 1080}
        )
        page = context.new_page()
        
        try:
            page.goto("https://www.punters.com.au/form-guide/", timeout=60000)
            time.sleep(3)
            
            # First check for abandoned meetings on the page
            page_text = page.inner_text('body').upper()
            
            # Look for abandoned status badges/text near venue names
            meeting_sections = page.query_selector_all('[class*="meeting"], [class*="event-group"], section, div[class*="card"]')
            for section in meeting_sections:
                try:
                    section_text = section.inner_text().upper()
                    if 'ABANDONED' in section_text:
                        links = section.query_selector_all('a[href*="/form-guide/horses/"]')
                        for link in links:
                            href = link.get_attribute('href')
                            if href:
                                match = re.search(r'/form-guide/horses/([^/]+)-\d{8}/', href)
                                if match:
                                    venue = match.group(1).replace('-', '_')
                                    if venue in au_venues:
                                        abandoned_venues.add(venue)
                                        au_venues.discard(venue)
                                        print(f"  ⚠ {venue.replace('_', ' ').title()} - ABANDONED (skipping)")
                except:
                    pass
            
            # Get all race links
            race_cards = page.query_selector_all('a[href*="/form-guide/horses/"]')
            
            for card in race_cards:
                href = card.get_attribute('href')
                if href and '/form-guide/horses/' in href:
                    # Check if card or parent shows ABANDONED
                    try:
                        parent = card.query_selector('xpath=..')
                        if parent:
                            parent_text = parent.inner_text().upper()
                            if 'ABANDONED' in parent_text:
                                match = re.search(r'/form-guide/horses/([^/]+)-\d{8}/', href)
                                if match:
                                    venue = match.group(1).replace('-', '_')
                                    if venue in au_venues:
                                        abandoned_venues.add(venue)
                                        au_venues.discard(venue)
                                        print(f"  ⚠ {venue.replace('_', ' ').title()} - ABANDONED (skipping)")
                                continue
                    except:
                        pass
                    
                    # Extract venue from URL
                    match = re.search(r'/form-guide/horses/([^/]+)-\d{8}/([^/]+)/', href)
                    if match:
                        venue = match.group(1).replace('-', '_')
                        race_name = match.group(2)
                        
                        # Only include if it's one of our AU venues (not abandoned)
                        if venue in au_venues:
                            race_match = re.search(r'race-(\d+)', race_name)
                            if race_match:
                                full_url = f"https://www.punters.com.au{href}" if not href.startswith('http') else href
                                race_urls.append({
                                    'url': full_url.split('#')[0],
                                    'venue': venue.replace('_', ' ').title(),
                                    'race_number': int(race_match.group(1)),
                                    'date': today
                                })
        except Exception as e:
            print(f"  Error collecting race URLs: {e}")
        finally:
            browser.close()
    
    # Remove duplicates
    seen = set()
    unique_urls = []
    for race in race_urls:
        key = (race['venue'], race['race_number'])
        if key not in seen:
            seen.add(key)
            unique_urls.append(race)
    
    return unique_urls, abandoned_venues


if __name__ == "__main__":
    main()
