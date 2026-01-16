"""
Racing Value Finder Web Application
Flask-based web interface for racing analysis, value picks, and arbitrage detection
"""

import os
import sys
import json
import time
import re
import shutil
import threading
from datetime import datetime
import pytz
from flask import Flask, render_template, jsonify, request
from flask_socketio import SocketIO, emit
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

# Add parent directory for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from playwright.sync_api import sync_playwright

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'racing-value-finder-2026')
socketio = SocketIO(app, cors_allowed_origins="*")

# Sydney timezone
SYDNEY_TZ = pytz.timezone('Australia/Sydney')

# Global data storage
race_data = {
    'races': [],
    'odds': [],
    'value_picks': [],
    'arb_opportunities': [],
    'dud_favourites': [],
    'last_updated': None,
    'loading': False
}

# Scraping status
scrape_status = {
    'is_scraping': False,
    'started_at': None,
    'current_step': '',
    'progress': 0,
    'total_meetings': 0,
    'meetings_done': 0,
    'total_races': 0,
    'races_done': 0,
    'estimated_time_remaining': None,
    'error': None
}

# Active arb monitoring threads
arb_monitors = {}


def get_sydney_time():
    """Get current time in Sydney"""
    return datetime.now(SYDNEY_TZ)


def get_data_folder(date=None):
    """Get racing data folder for a specific date
    Uses /data volume on Railway for persistent storage
    Falls back to local directory for development
    """
    if date is None:
        date = get_sydney_time()
    date_str = date.strftime("%Y%m%d")
    
    # Check for Railway persistent volume
    if os.path.exists('/data'):
        base_dir = '/data'
    else:
        base_dir = os.path.dirname(os.path.abspath(__file__))
    
    return os.path.join(base_dir, f"racing_forms_{date_str}")


def cleanup_old_data():
    """Delete old racing form folders (older than today)"""
    # Check for Railway persistent volume
    if os.path.exists('/data'):
        base_dir = '/data'
    else:
        base_dir = os.path.dirname(os.path.abspath(__file__))
    
    today_str = get_sydney_time().strftime("%Y%m%d")
    
    for folder_name in os.listdir(base_dir):
        if folder_name.startswith("racing_forms_") and folder_name != f"racing_forms_{today_str}":
            folder_path = os.path.join(base_dir, folder_name)
            if os.path.isdir(folder_path):
                try:
                    shutil.rmtree(folder_path)
                    print(f"Deleted old data folder: {folder_name}")
                except Exception as e:
                    print(f"Error deleting {folder_name}: {e}")


def daily_refresh():
    """Daily task to refresh form data - runs at 5 AM Sydney time"""
    global scrape_status
    
    print(f"[{get_sydney_time()}] Starting daily data refresh...")
    
    # Update scrape status
    scrape_status['is_scraping'] = True
    scrape_status['started_at'] = get_sydney_time().isoformat()
    scrape_status['current_step'] = 'Cleaning up old data...'
    scrape_status['progress'] = 5
    scrape_status['error'] = None
    
    try:
        # Clean up old data folders
        cleanup_old_data()
        
        scrape_status['current_step'] = 'Scraping race meetings...'
        scrape_status['progress'] = 10
        socketio.emit('scrape_progress', scrape_status)
        
        # Scrape new data for today
        scrape_todays_races()
        
        scrape_status['current_step'] = 'Analyzing data...'
        scrape_status['progress'] = 90
        socketio.emit('scrape_progress', scrape_status)
        
        # Reload data into memory
        load_existing_data()
        
        scrape_status['current_step'] = 'Complete!'
        scrape_status['progress'] = 100
        scrape_status['is_scraping'] = False
        socketio.emit('scrape_progress', scrape_status)
        
        # Notify connected clients
        socketio.emit('data_refreshed', {'time': get_sydney_time().strftime("%H:%M:%S")})
        
        print(f"[{get_sydney_time()}] Daily refresh complete!")
        
    except Exception as e:
        scrape_status['error'] = str(e)
        scrape_status['is_scraping'] = False
        socketio.emit('scrape_progress', scrape_status)
        print(f"[{get_sydney_time()}] Error during refresh: {e}")
    
    finally:
        scrape_status['is_scraping'] = False


def is_australian_track(venue):
    """Check if the venue is an Australian track"""
    venue_lower = venue.lower().replace(' ', '_').replace('-', '_')
    
    # Reject any venue with international country suffixes
    international_suffixes = [
        '_nz', '_us', '_uk', '_za', '_fr', '_jp', '_tr', '_hk', '_sg',
        '_ie', '_ae', '_kr', '_in', '_my', '_ph', '_cl', '_ar', '_br'
    ]
    
    for suffix in international_suffixes:
        if venue_lower.endswith(suffix):
            return False
    
    # Known international venues to exclude
    international = [
        # New Zealand
        'te_rapa', 'trentham', 'ellerslie', 'riccarton', 'otaki', 'awapuni',
        'hastings', 'matamata', 'pukekohe', 'ruakaka', 'wanganui', 'woodville',
        'ashburton', 'wingatui', 'riverton', 'oamaru', 'timaru', 'waimate',
        'cromwell', 'kurow', 'omakau', 'roxburgh', 'tapanui', 'waikouaiti',
        'avondale', 'rotorua', 'new_plymouth', 'waikato', 'taranaki',
        # USA
        'aqueduct', 'belmont_park', 'santa_anita', 'gulfstream', 'del_mar',
        'churchill', 'keeneland', 'saratoga', 'pimlico', 'laurel', 'parx',
        'oaklawn', 'tampa_bay', 'fair_grounds', 'turfway', 'golden_gate',
        'los_alamitos', 'penn_national', 'charles_town', 'mountaineer',
        'presque_isle', 'finger_lakes', 'monmouth', 'woodbine',
        # Hong Kong
        'hong_kong', 'sha_tin', 'happy_valley',
        # Singapore
        'kranji',
        # Japan
        'tokyo', 'nakayama', 'kyoto', 'hanshin', 'chukyo',
        # UK
        'newmarket', 'epsom', 'cheltenham', 'goodwood', 'kempton', 'lingfield', 
        'wolverhampton', 'doncaster', 'haydock', 'aintree',
        # Ireland
        'curragh', 'leopardstown', 'fairyhouse', 'punchestown', 'galway',
        # France
        'longchamp', 'chantilly', 'deauville', 'saint_cloud',
        # Dubai/UAE
        'meydan', 'abu_dhabi',
        # South Africa
        'turffontein', 'kenilworth', 'greyville', 'scottsville', 'fairview', 'vaal',
    ]
    
    for intl in international:
        if intl in venue_lower:
            return False
    
    return True


def scrape_todays_races():
    """Scrape today's race meetings and odds"""
    global scrape_status
    
    folder = get_data_folder()
    os.makedirs(folder, exist_ok=True)
    
    print(f"Scraping today's races to {folder}...")
    
    try:
        with sync_playwright() as p:
            browser = p.firefox.launch(headless=True)
            context = browser.new_context(
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0'
            )
            page = context.new_page()
            
            scrape_status['current_step'] = 'Loading racing page...'
            
            # Go to punters.com.au thoroughbred racing page
            page.goto("https://www.punters.com.au/form-guide/", timeout=30000)
            time.sleep(3)
            
            # Wait for race cards to load
            try:
                page.wait_for_selector('a[href*="/form-guide/horses/"]', timeout=15000)
                print("→ Found race links")
            except:
                print("→ Waiting for content...")
                time.sleep(5)
            
            # Get all race card links - use the same selector as racingwebsite.py
            race_cards = page.query_selector_all('a.event-card[href*="/form-guide/"]')
            if not race_cards:
                race_cards = page.query_selector_all('a[href*="/form-guide/horses/"]')
            
            # Extract unique meetings (group by venue)
            meetings = {}
            abandoned_meetings = set()
            all_race_urls = []
            
            for card in race_cards:
                href = card.get_attribute('href')
                if href and '/form-guide/horses/' in href:
                    full_url = f"https://www.punters.com.au{href}" if not href.startswith('http') else href
                    full_url = full_url.split('#')[0]
                    
                    # Extract venue and date using pattern from racingwebsite.py
                    # Example: /form-guide/horses/canterbury-20260116/race-1/
                    pattern = r'/form-guide/horses/([^/]+)/([^/]+)/'
                    match = re.search(pattern, href)
                    
                    if match:
                        venue_date = match.group(1)
                        race_part = match.group(2)
                        
                        # Extract date from venue string (last 8 digits)
                        date_match = re.search(r'(\d{8})$', venue_date)
                        if date_match:
                            date = date_match.group(1)
                            venue = venue_date.replace(f'-{date}', '').replace('-', ' ').title()
                        else:
                            date = get_sydney_time().strftime("%Y%m%d")
                            venue = venue_date.replace('-', ' ').title()
                        
                        # Extract race number
                        race_match = re.search(r'race-(\d+)', race_part)
                        race_num = int(race_match.group(1)) if race_match else 0
                        
                        # Only include Australian tracks
                        if not is_australian_track(venue):
                            continue
                        
                        meeting_key = f"{date}_{venue}"
                        
                        # Check for abandoned - look at card text and parent elements
                        try:
                            card_text = card.inner_text().upper()
                            # Also check parent container for abandoned status
                            parent = card.evaluate('el => el.closest(".event-card-container, .meeting-card, [class*=meeting]")?.innerText?.toUpperCase() || ""')
                            
                            if 'ABANDONED' in card_text or 'ABANDONED' in parent:
                                abandoned_meetings.add(meeting_key)
                                print(f"  → Skipping {venue} (ABANDONED)")
                                continue
                        except:
                            pass
                        
                        # Skip if this meeting is already marked abandoned
                        if meeting_key in abandoned_meetings:
                            continue
                        
                        # Store the race URL
                        all_race_urls.append({
                            'url': full_url,
                            'venue': venue,
                            'race_number': race_num,
                            'date': date,
                            'meeting_key': meeting_key
                        })
                        
                        # Track unique meetings
                        if meeting_key not in meetings:
                            meetings[meeting_key] = venue
            
            print(f"Found {len(meetings)} meetings with {len(all_race_urls)} races")
            scrape_status['total_meetings'] = len(meetings)
            scrape_status['total_races'] = len(all_race_urls)
            scrape_status['meetings_done'] = 0
            
            all_odds = []
            
            # Process each unique meeting
            meeting_list = list(meetings.items())
            for idx, (meeting_key, venue) in enumerate(meeting_list):
                try:
                    scrape_status['meetings_done'] = idx + 1
                    scrape_status['progress'] = 10 + int(((idx + 1) / len(meeting_list)) * 70)
                    
                    # Estimate time remaining
                    remaining = len(meeting_list) - idx
                    scrape_status['estimated_time_remaining'] = f"~{remaining * 20} seconds"
                    scrape_status['current_step'] = f'Scraping {venue} ({idx + 1}/{len(meeting_list)})...'
                    
                    # Emit update (with error handling)
                    try:
                        socketio.emit('scrape_progress', scrape_status)
                    except:
                        pass
                    print(f"[{idx + 1}/{len(meeting_list)}] Scraping {venue}...")
                    
                    # Get races for this meeting
                    meeting_races = [r for r in all_race_urls if r['meeting_key'] == meeting_key]
                    
                    # Check first race page for abandoned status
                    if meeting_races:
                        first_race = meeting_races[0]
                        try:
                            page.goto(first_race['url'], timeout=30000)
                            time.sleep(1)
                            page_text = page.inner_text('body').upper()
                            
                            # Check for abandoned indicators
                            if 'ABANDONED' in page_text or 'MEETING ABANDONED' in page_text:
                                abandoned_meetings.add(meeting_key)
                                print(f"  → Meeting ABANDONED - skipping all races")
                                continue
                        except Exception as e:
                            print(f"  → Error checking meeting status: {e}")
                    
                    for race_info in meeting_races:
                        try:
                            odds = scrape_race_odds_page(page, race_info['url'])
                            if odds:
                                all_odds.append({
                                    'venue': race_info['venue'],
                                    'race_number': race_info['race_number'],
                                    'url': race_info['url'],
                                    'horses': odds
                                })
                                print(f"    → Race {race_info['race_number']}: {len(odds)} horses")
                            else:
                                print(f"    → Race {race_info['race_number']}: No odds found")
                        except Exception as e:
                            print(f"  Error scraping race {race_info['race_number']}: {e}")
                    
                except Exception as e:
                    print(f"Error scraping {venue}: {e}")
                    continue
            
            browser.close()
            
            # Save odds data
            if all_odds:
                odds_file = os.path.join(folder, "odds_data.json")
                with open(odds_file, 'w', encoding='utf-8') as f:
                    json.dump(all_odds, f, indent=2)
                print(f"✓ Saved {len(all_odds)} races to {odds_file}")
                
                # Verify file was saved
                if os.path.exists(odds_file):
                    file_size = os.path.getsize(odds_file)
                    print(f"✓ File verified: {file_size} bytes")
                else:
                    print("✗ File not found after save!")
            else:
                print("✗ No odds data collected to save")
            
            # Log abandoned meetings
            if abandoned_meetings:
                print(f"Skipped {len(abandoned_meetings)} abandoned meetings: {list(abandoned_meetings)}")
            
    except Exception as e:
        print(f"Error in scrape_todays_races: {e}")
        import traceback
        traceback.print_exc()


def scrape_race_odds_page(page, race_url):
    """Scrape odds from a specific race page"""
    try:
        if not race_url.startswith('http'):
            race_url = f"https://www.punters.com.au{race_url}"
        
        page.goto(race_url + "#OddsComparison", timeout=30000)
        time.sleep(2)
        
        try:
            page.wait_for_selector('table.compare-odds__table', timeout=10000)
        except:
            return []
        
        # Extract bookmaker names
        bookmaker_headers = page.query_selector_all('table.compare-odds__table thead th img')
        bookmakers = []
        for img in bookmaker_headers:
            alt = img.get_attribute('alt')
            if alt:
                bookmakers.append(alt)
        
        # Extract odds
        horses = []
        rows = page.query_selector_all('table.compare-odds__table tbody tr.compare-odds-selection')
        
        for row in rows:
            try:
                competitor = row.query_selector('.selection-runner__competitor')
                if not competitor:
                    continue
                
                text = competitor.inner_text().strip()
                match = re.match(r'(\d+)\.\s*(.+?)\s*\((\d+)\)', text)
                if not match:
                    continue
                
                horse_num = match.group(1)
                horse_name = match.group(2).strip()
                barrier = match.group(3)
                
                odds_cells = row.query_selector_all('.compare-odds-selection__cell')
                horse_odds = {}
                
                for i, cell in enumerate(odds_cells[1:]):
                    odds_link = cell.query_selector('a.compare-odds-selection__cell--link')
                    if odds_link:
                        odds_text = odds_link.inner_text().strip().replace('$', '')
                        try:
                            odds_float = float(odds_text)
                            if i < len(bookmakers):
                                horse_odds[bookmakers[i]] = odds_float
                        except:
                            pass
                
                if horse_odds:
                    valid_odds = {k: v for k, v in horse_odds.items() if v and v < 500}
                    if valid_odds:
                        best_bookie = max(valid_odds, key=valid_odds.get)
                        horses.append({
                            'number': int(horse_num),
                            'name': horse_name,
                            'barrier': int(barrier),
                            'odds': horse_odds,
                            'best_odds': valid_odds[best_bookie],
                            'best_bookmaker': best_bookie,
                            'avg_odds': sum(valid_odds.values()) / len(valid_odds)
                        })
            except:
                continue
        
        return horses
        
    except Exception as e:
        print(f"Error scraping race odds: {e}")
        return []


# Initialize scheduler
scheduler = BackgroundScheduler(timezone=SYDNEY_TZ)

# Schedule daily refresh at 5:00 AM Sydney time
scheduler.add_job(
    daily_refresh,
    CronTrigger(hour=5, minute=0, timezone=SYDNEY_TZ),
    id='daily_refresh',
    replace_existing=True
)


def load_existing_data():
    """Load data from existing JSON/CSV files"""
    global race_data
    
    folder = get_data_folder()
    
    # Load odds data
    odds_file = os.path.join(folder, "odds_data.json")
    if os.path.exists(odds_file):
        with open(odds_file, 'r', encoding='utf-8') as f:
            race_data['odds'] = json.load(f)
    
    # Load form analysis
    form_file = os.path.join(folder, "form_analysis.csv")
    if os.path.exists(form_file):
        import csv
        races_dict = {}
        with open(form_file, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                key = (row['Venue'], int(row['Race']))
                if key not in races_dict:
                    races_dict[key] = {
                        'venue': row['Venue'],
                        'race_number': int(row['Race']),
                        'race_name': row.get('Race Name', ''),
                        'horses': []
                    }
                races_dict[key]['horses'].append({
                    'barrier': int(row.get('Barrier', 0)),
                    'name': row['Horse'],
                    'form': row.get('Form', ''),
                    'form_score': float(row.get('Form Score', 0))
                })
        race_data['races'] = list(races_dict.values())
    
    race_data['last_updated'] = datetime.now().strftime("%H:%M:%S")
    
    # Calculate value picks and arb opportunities
    analyze_all_data()


def calculate_form_strength(horses):
    """Calculate relative strength from form scores"""
    if not horses:
        return []
    
    # Get form scores, use 0 for missing
    scores = [max(h.get('form_score', 0), 1) for h in horses]
    
    # Normalize to probabilities using softmax
    import math
    temp = 15.0  # Temperature parameter
    scaled = [s / temp for s in scores]
    max_scaled = max(scaled)
    exp_values = [math.exp(s - max_scaled) for s in scaled]
    total = sum(exp_values)
    
    probabilities = [e / total for e in exp_values]
    return probabilities


def analyze_all_data():
    """Analyze odds and form data to find value picks and arb opportunities"""
    global race_data
    
    race_data['value_picks'] = []
    race_data['arb_opportunities'] = []
    race_data['dud_favourites'] = []
    
    # Match races with odds
    for odds_race in race_data['odds']:
        venue = odds_race['venue']
        race_num = odds_race['race_number']
        horses = odds_race['horses']
        
        if not horses:
            continue
        
        # Find matching form data
        form_race = None
        for r in race_data['races']:
            if r['venue'].lower() == venue.lower() and r['race_number'] == race_num:
                form_race = r
                break
        
        # Get best odds for each horse
        horse_odds = []
        for h in horses:
            best_odds = h.get('best_odds')
            if best_odds and best_odds < 500:
                # Find form score for this horse
                form_score = 0
                if form_race:
                    for fh in form_race['horses']:
                        if normalize_name(fh['name']) == normalize_name(h['name']):
                            form_score = fh.get('form_score', 0)
                            break
                
                horse_odds.append({
                    'name': h['name'],
                    'number': h.get('number', 0),
                    'barrier': h.get('barrier', 0),
                    'best_odds': best_odds,
                    'best_bookmaker': h.get('best_bookmaker', ''),
                    'avg_odds': h.get('avg_odds', best_odds),
                    'form_score': form_score,
                    'jockey': h.get('jockey', ''),
                    'trainer': h.get('trainer', '')
                })
        
        if len(horse_odds) < 2:
            continue
        
        # Calculate dutch book (sum of implied probabilities)
        dutch_book = sum(1.0 / h['best_odds'] for h in horse_odds)
        
        # Calculate model probabilities from form
        form_scores = [h['form_score'] for h in horse_odds]
        if max(form_scores) > 0:
            model_probs = calculate_form_strength([{'form_score': s} for s in form_scores])
        else:
            # Use market implied if no form data
            model_probs = [(1.0 / h['best_odds']) / dutch_book for h in horse_odds]
        
        # Add model probability to each horse
        for i, h in enumerate(horse_odds):
            h['model_prob'] = model_probs[i]
            h['implied_prob'] = 1.0 / h['best_odds']
            h['fair_odds'] = 1.0 / model_probs[i] if model_probs[i] > 0 else 999
            h['edge'] = model_probs[i] - h['implied_prob']
        
        # Sort by model probability
        horse_odds.sort(key=lambda x: x['model_prob'], reverse=True)
        
        # Find favourite (lowest best odds)
        favourite = min(horse_odds, key=lambda x: x['best_odds'])
        
        # Check for dud favourite (model thinks it's overrated)
        # This is a "lay the favourite" or "dutch the field" opportunity
        if favourite['edge'] < -0.05:  # 5% negative edge (favourite is overrated)
            # Calculate the dutch book for the rest of the field (excluding favourite)
            other_horses = [h for h in horse_odds if h['name'] != favourite['name']]
            
            if len(other_horses) >= 2:
                # Dutch book for non-favourites
                field_dutch_book = sum(1.0 / h['best_odds'] for h in other_horses)
                
                # Model's probability that NON-favourite wins
                field_model_prob = sum(h['model_prob'] for h in other_horses)
                
                # Market's implied probability that NON-favourite wins
                field_implied_prob = 1.0 - favourite['implied_prob']
                
                # If field dutch book < 1, dutching the field is profitable
                # Even if > 1, if model says field is more likely, it's still value
                field_edge = field_model_prob - field_implied_prob
                
                # Calculate potential profit from dutching the field
                # If you bet to win $100 on any non-favourite winning:
                # Total stake = 100 * field_dutch_book
                # Profit if any non-fav wins = 100 - stake = 100 * (1 - field_dutch_book)
                dutch_profit_pct = (1.0 - field_dutch_book) * 100 if field_dutch_book < 1 else 0
                
                # Calculate stakes for each horse to dutch (equal return of $100)
                dutch_stakes = []
                for h in other_horses:
                    stake_pct = (1.0 / h['best_odds']) / field_dutch_book * 100
                    dutch_stakes.append({
                        'name': h['name'],
                        'number': h.get('number', 0),
                        'odds': h['best_odds'],
                        'bookmaker': h.get('best_bookmaker', ''),
                        'stake_pct': round(stake_pct, 1),
                        'model_prob': h['model_prob'],
                        'form_score': h.get('form_score', 0)
                    })
                
                race_data['dud_favourites'].append({
                    'venue': venue,
                    'race_number': race_num,
                    'favourite': favourite['name'],
                    'favourite_number': favourite.get('number', 0),
                    'odds': favourite['best_odds'],
                    'model_prob': favourite['model_prob'],
                    'implied_prob': favourite['implied_prob'],
                    'edge': favourite['edge'],
                    'overrated_by': round(abs(favourite['edge']) * 100, 1),  # % overrated
                    'better_picks': [h['name'] for h in horse_odds[:3] if h['name'] != favourite['name']][:2],
                    # Dutch the field data
                    'field_dutch_book': round(field_dutch_book, 4),
                    'field_model_prob': round(field_model_prob * 100, 1),
                    'field_implied_prob': round(field_implied_prob * 100, 1),
                    'field_edge': round(field_edge * 100, 1),
                    'dutch_profit_pct': round(dutch_profit_pct, 2),
                    'is_dutch_arb': field_dutch_book < 1.0,
                    'dutch_stakes': dutch_stakes,
                    'field_size': len(other_horses),
                    'url': odds_race.get('url', '')
                })
        
        # Find value picks (model prob > implied prob by threshold)
        for h in horse_odds:
            if h['edge'] >= 0.03 and h['model_prob'] >= 0.10:  # 3% edge, min 10% win chance
                race_data['value_picks'].append({
                    'venue': venue,
                    'race_number': race_num,
                    'horse': h['name'],
                    'number': h['number'],
                    'best_odds': h['best_odds'],
                    'best_at': h['best_bookmaker'],
                    'fair_odds': round(h['fair_odds'], 2),
                    'model_prob': h['model_prob'],
                    'implied_prob': h['implied_prob'],
                    'edge': h['edge'],
                    'form_score': h['form_score'],
                    'value_rating': min(5, int(h['edge'] * 50) + 1)  # 1-5 star rating
                })
        
        # Check for market edge (dutch book < 1 means potentially profitable)
        # Only flag if profit is at least 2% AND we have odds from multiple bookmakers
        if dutch_book < 0.98:  # 2%+ profit threshold
            guaranteed_profit = (1.0 / dutch_book - 1) * 100  # As percentage
            
            # Count how many horses have odds from multiple bookmakers
            multi_bookie_count = sum(1 for h in horse_odds if 'avg_odds' in h and h.get('avg_odds') != h.get('best_odds'))
            
            # Only include if we have meaningful multi-bookie data
            if multi_bookie_count >= 3 or guaranteed_profit >= 3.0:
                race_data['arb_opportunities'].append({
                    'venue': venue,
                    'race_number': race_num,
                    'dutch_book': dutch_book,
                    'guaranteed_profit_pct': guaranteed_profit,
                    'horses': horse_odds,
                    'field_size': len(horse_odds),
                    'url': odds_race.get('url', ''),
                    'last_checked': datetime.now().strftime("%H:%M:%S"),
                    'status': 'active',
                    'multi_bookie_count': multi_bookie_count
                })
    
    # Sort value picks by edge
    race_data['value_picks'].sort(key=lambda x: x['edge'], reverse=True)


def normalize_name(name):
    """Normalize horse name for matching"""
    import unicodedata
    name = unicodedata.normalize('NFKD', name).encode('ASCII', 'ignore').decode('ASCII')
    name = name.upper().strip()
    name = name.replace('-', ' ')
    name = re.sub(r"['\.\(\)\,\!\?]", "", name)
    name = ' '.join(name.split())
    return name


def scrape_race_odds(venue, race_number, url):
    """Scrape current odds for a specific race"""
    try:
        with sync_playwright() as p:
            browser = p.firefox.launch(headless=True)
            context = browser.new_context(
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0'
            )
            page = context.new_page()
            
            page.goto(url + "#OddsComparison", timeout=30000)
            time.sleep(2)
            
            try:
                page.wait_for_selector('table.compare-odds__table', timeout=10000)
            except:
                browser.close()
                return None
            
            # Extract bookmaker names
            bookmaker_headers = page.query_selector_all('table.compare-odds__table thead th img')
            bookmakers = []
            for img in bookmaker_headers:
                alt = img.get_attribute('alt')
                if alt:
                    bookmakers.append(alt)
            
            # Extract odds
            horses = []
            rows = page.query_selector_all('table.compare-odds__table tbody tr.compare-odds-selection')
            
            for row in rows:
                try:
                    competitor = row.query_selector('.selection-runner__competitor')
                    if not competitor:
                        continue
                    
                    text = competitor.inner_text().strip()
                    match = re.match(r'(\d+)\.\s*(.+?)\s*\((\d+)\)', text)
                    if not match:
                        continue
                    
                    horse_num = match.group(1)
                    horse_name = match.group(2).strip()
                    barrier = match.group(3)
                    
                    odds_cells = row.query_selector_all('.compare-odds-selection__cell')
                    horse_odds = {}
                    
                    for i, cell in enumerate(odds_cells[1:]):
                        odds_link = cell.query_selector('a.compare-odds-selection__cell--link')
                        if odds_link:
                            odds_text = odds_link.inner_text().strip().replace('$', '')
                            try:
                                odds_float = float(odds_text)
                                if i < len(bookmakers):
                                    horse_odds[bookmakers[i]] = odds_float
                            except:
                                pass
                    
                    if horse_odds:
                        valid_odds = {k: v for k, v in horse_odds.items() if v and v < 500}
                        if valid_odds:
                            best_bookie = max(valid_odds, key=valid_odds.get)
                            horses.append({
                                'number': int(horse_num),
                                'name': horse_name,
                                'barrier': int(barrier),
                                'odds': horse_odds,
                                'best_odds': valid_odds[best_bookie],
                                'best_bookmaker': best_bookie,
                                'avg_odds': sum(valid_odds.values()) / len(valid_odds)
                            })
                except:
                    continue
            
            browser.close()
            return horses
            
    except Exception as e:
        print(f"Error scraping odds: {e}")
        return None


def monitor_arb_opportunity(arb_id, venue, race_number, url):
    """Background thread to monitor an arb opportunity"""
    global arb_monitors, race_data
    
    while arb_id in arb_monitors and arb_monitors[arb_id]['active']:
        time.sleep(120)  # Wait 2 minutes
        
        if arb_id not in arb_monitors or not arb_monitors[arb_id]['active']:
            break
        
        # Scrape fresh odds
        horses = scrape_race_odds(venue, race_number, url)
        
        if horses:
            # Calculate new dutch book
            dutch_book = sum(1.0 / h['best_odds'] for h in horses if h.get('best_odds'))
            
            # Find the arb in our data
            for arb in race_data['arb_opportunities']:
                if arb['venue'] == venue and arb['race_number'] == race_number:
                    arb['dutch_book'] = dutch_book
                    arb['horses'] = horses
                    arb['last_checked'] = datetime.now().strftime("%H:%M:%S")
                    
                    if dutch_book >= 1.0:
                        arb['status'] = 'expired'
                        arb['guaranteed_profit_pct'] = 0
                    else:
                        arb['status'] = 'active'
                        arb['guaranteed_profit_pct'] = (1.0 / dutch_book - 1) * 100
                    
                    # Emit update to clients
                    socketio.emit('arb_update', arb)
                    break


@app.route('/')
def index():
    """Main dashboard"""
    return render_template('index.html')


@app.route('/api/data')
def get_data():
    """Get all current data"""
    return jsonify({
        'races': race_data['races'],
        'odds': race_data['odds'],
        'value_picks': race_data['value_picks'],
        'arb_opportunities': race_data['arb_opportunities'],
        'dud_favourites': race_data['dud_favourites'],
        'last_updated': race_data['last_updated'],
        'total_races': len(race_data['odds'])
    })


@app.route('/api/refresh')
def refresh_data():
    """Refresh data from files"""
    load_existing_data()
    return jsonify({'status': 'ok', 'last_updated': race_data['last_updated']})


@app.route('/api/calculate_dutch', methods=['POST'])
def calculate_dutch():
    """Calculate dutching stakes for a race"""
    data = request.json
    bankroll = float(data.get('bankroll', 100))
    venue = data.get('venue')
    race_number = int(data.get('race_number'))
    selected_horses = data.get('horses', [])  # List of horse names to dutch
    
    # Find the race odds
    race_odds = None
    for r in race_data['odds']:
        if r['venue'].lower() == venue.lower() and r['race_number'] == race_number:
            race_odds = r
            break
    
    if not race_odds:
        return jsonify({'error': 'Race not found'}), 404
    
    # Get horses to dutch
    horses_to_dutch = []
    for h in race_odds['horses']:
        if not selected_horses or h['name'] in selected_horses:
            if h.get('best_odds') and h['best_odds'] < 500:
                horses_to_dutch.append(h)
    
    if len(horses_to_dutch) < 2:
        return jsonify({'error': 'Need at least 2 horses to dutch'}), 400
    
    # Calculate dutch stakes (equal profit method)
    dutch_book = sum(1.0 / h['best_odds'] for h in horses_to_dutch)
    
    stakes = []
    for h in horses_to_dutch:
        implied = 1.0 / h['best_odds']
        stake = bankroll * implied / dutch_book
        profit_if_wins = (stake * h['best_odds']) - bankroll
        
        stakes.append({
            'name': h['name'],
            'number': h.get('number', 0),
            'odds': h['best_odds'],
            'bookmaker': h.get('best_bookmaker', ''),
            'stake': round(stake, 2),
            'profit_if_wins': round(profit_if_wins, 2),
            'win_prob': round((1.0 / h['best_odds']) * 100, 1)
        })
    
    # Calculate expected value
    is_arb = dutch_book < 1.0
    if is_arb:
        guaranteed_profit = bankroll * (1.0 / dutch_book - 1)
        roi = (guaranteed_profit / bankroll) * 100
    else:
        # Calculate EV from model probabilities
        expected_return = 0
        for s in stakes:
            # Use implied prob as estimate
            prob = s['win_prob'] / 100
            expected_return += prob * (s['stake'] * s['odds'] / s['stake'])
        roi = (expected_return - 1) * 100
        guaranteed_profit = 0
    
    return jsonify({
        'venue': venue,
        'race_number': race_number,
        'bankroll': bankroll,
        'dutch_book': round(dutch_book, 4),
        'is_arb': is_arb,
        'guaranteed_profit': round(guaranteed_profit, 2),
        'roi': round(roi, 2),
        'stakes': stakes,
        'total_stake': bankroll,
        'overround_pct': round((dutch_book - 1) * 100, 2)
    })


@app.route('/api/start_arb_monitor', methods=['POST'])
def start_arb_monitor():
    """Start monitoring an arb opportunity"""
    data = request.json
    venue = data.get('venue')
    race_number = int(data.get('race_number'))
    url = data.get('url', '')
    
    arb_id = f"{venue}_{race_number}"
    
    if arb_id in arb_monitors and arb_monitors[arb_id]['active']:
        return jsonify({'status': 'already_monitoring'})
    
    arb_monitors[arb_id] = {'active': True}
    
    # Start background thread
    thread = threading.Thread(
        target=monitor_arb_opportunity,
        args=(arb_id, venue, race_number, url),
        daemon=True
    )
    thread.start()
    
    return jsonify({'status': 'started', 'arb_id': arb_id})


@app.route('/api/stop_arb_monitor', methods=['POST'])
def stop_arb_monitor():
    """Stop monitoring an arb opportunity"""
    data = request.json
    arb_id = data.get('arb_id')
    
    if arb_id in arb_monitors:
        arb_monitors[arb_id]['active'] = False
        del arb_monitors[arb_id]
    
    return jsonify({'status': 'stopped'})


@app.route('/api/race/<venue>/<int:race_number>')
def get_race_detail(venue, race_number):
    """Get detailed data for a specific race"""
    # Find odds
    odds_data = None
    for r in race_data['odds']:
        if r['venue'].lower() == venue.lower() and r['race_number'] == race_number:
            odds_data = r
            break
    
    # Find form data
    form_data = None
    for r in race_data['races']:
        if r['venue'].lower() == venue.lower() and r['race_number'] == race_number:
            form_data = r
            break
    
    if not odds_data:
        return jsonify({'error': 'Race not found'}), 404
    
    # Merge form scores with odds data
    horses = []
    for h in odds_data['horses']:
        horse_data = {
            'number': h.get('number', 0),
            'name': h['name'],
            'barrier': h.get('barrier', 0),
            'jockey': h.get('jockey', ''),
            'trainer': h.get('trainer', ''),
            'best_odds': h.get('best_odds'),
            'best_bookmaker': h.get('best_bookmaker', ''),
            'avg_odds': h.get('avg_odds'),
            'all_odds': h.get('odds', {}),
            'form_score': 0,
            'form': ''
        }
        
        # Find matching form data
        if form_data:
            for fh in form_data['horses']:
                if normalize_name(fh['name']) == normalize_name(h['name']):
                    horse_data['form_score'] = fh.get('form_score', 0)
                    horse_data['form'] = fh.get('form', '')
                    break
        
        horses.append(horse_data)
    
    # Calculate probabilities
    form_scores = [h['form_score'] for h in horses]
    if max(form_scores) > 0:
        model_probs = calculate_form_strength([{'form_score': s} for s in form_scores])
        for i, h in enumerate(horses):
            h['model_prob'] = model_probs[i]
    else:
        dutch_book = sum(1.0 / h['best_odds'] for h in horses if h.get('best_odds') and h['best_odds'] < 500)
        for h in horses:
            if h.get('best_odds') and h['best_odds'] < 500:
                h['model_prob'] = (1.0 / h['best_odds']) / dutch_book
            else:
                h['model_prob'] = 0
    
    # Sort by model probability
    horses.sort(key=lambda x: x.get('model_prob', 0), reverse=True)
    
    # Calculate race stats
    valid_horses = [h for h in horses if h.get('best_odds') and h['best_odds'] < 500]
    dutch_book = sum(1.0 / h['best_odds'] for h in valid_horses) if valid_horses else 0
    
    return jsonify({
        'venue': venue,
        'race_number': race_number,
        'race_name': form_data.get('race_name', '') if form_data else '',
        'horses': horses,
        'field_size': len(valid_horses),
        'dutch_book': round(dutch_book, 4),
        'overround_pct': round((dutch_book - 1) * 100, 2) if dutch_book > 0 else 0,
        'is_arb': dutch_book < 1.0
    })


@app.route('/api/scrape_now', methods=['POST'])
def trigger_scrape():
    """Manually trigger a data refresh"""
    def run_scrape():
        daily_refresh()
    
    thread = threading.Thread(target=run_scrape, daemon=True)
    thread.start()
    
    return jsonify({
        'status': 'started',
        'message': 'Scraping started in background. Data will refresh shortly.'
    })


@app.route('/api/status')
def get_status():
    """Get current system status"""
    sydney_now = get_sydney_time()
    folder = get_data_folder()
    
    return jsonify({
        'sydney_time': sydney_now.strftime("%Y-%m-%d %H:%M:%S"),
        'data_folder': folder,
        'folder_exists': os.path.exists(folder),
        'races_loaded': len(race_data['odds']),
        'value_picks': len(race_data['value_picks']),
        'market_edges': len(race_data['arb_opportunities']),
        'dud_favourites': len(race_data['dud_favourites']),
        'last_updated': race_data['last_updated'],
        'scheduler_running': scheduler.running
    })


@app.route('/api/scrape_status')
def get_scrape_status():
    """Get current scraping status"""
    return jsonify(scrape_status)


@socketio.on('connect')
def handle_connect():
    """Handle client connection"""
    emit('connected', {'status': 'ok'})


@socketio.on('subscribe_arb')
def handle_subscribe_arb(data):
    """Subscribe to arb updates"""
    # Client wants to receive arb updates
    pass


# Start the scheduler
scheduler.start()

# Load data on module import for production
load_existing_data()

# If no data exists, trigger initial scrape
if not race_data['odds']:
    print("No data found - triggering initial scrape...")
    threading.Thread(target=daily_refresh, daemon=True).start()


if __name__ == '__main__':
    print("=" * 60)
    print("Racing Value Finder Web Application")
    print("=" * 60)
    
    print(f"\nSydney Time: {get_sydney_time().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Daily refresh scheduled for 5:00 AM Sydney time")
    
    print(f"\nLoaded {len(race_data['races'])} races with form data")
    print(f"Loaded {len(race_data['odds'])} races with odds data")
    print(f"Found {len(race_data['value_picks'])} value picks")
    print(f"Found {len(race_data['arb_opportunities'])} market edge opportunities")
    print(f"Found {len(race_data['dud_favourites'])} dud favourite alerts")
    
    # Get port from environment variable for Railway/production
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_DEBUG', 'true').lower() == 'true'
    
    print("\n" + "=" * 60)
    print(f"Starting web server on port {port}...")
    if debug:
        print("Open http://localhost:5000 in your browser")
    print("=" * 60)
    
    socketio.run(app, host='0.0.0.0', port=port, debug=debug)
