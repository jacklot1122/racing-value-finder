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

# Active arb monitoring threads
arb_monitors = {}


def get_sydney_time():
    """Get current time in Sydney"""
    return datetime.now(SYDNEY_TZ)


def get_data_folder(date=None):
    """Get racing data folder for a specific date"""
    if date is None:
        date = get_sydney_time()
    date_str = date.strftime("%Y%m%d")
    script_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(script_dir, f"racing_forms_{date_str}")


def cleanup_old_data():
    """Delete old racing form folders (older than today)"""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    today_str = get_sydney_time().strftime("%Y%m%d")
    
    for folder_name in os.listdir(script_dir):
        if folder_name.startswith("racing_forms_") and folder_name != f"racing_forms_{today_str}":
            folder_path = os.path.join(script_dir, folder_name)
            if os.path.isdir(folder_path):
                try:
                    shutil.rmtree(folder_path)
                    print(f"Deleted old data folder: {folder_name}")
                except Exception as e:
                    print(f"Error deleting {folder_name}: {e}")


def daily_refresh():
    """Daily task to refresh form data - runs at 5 AM Sydney time"""
    print(f"[{get_sydney_time()}] Starting daily data refresh...")
    
    # Clean up old data folders
    cleanup_old_data()
    
    # Scrape new data for today
    scrape_todays_races()
    
    # Reload data into memory
    load_existing_data()
    
    # Notify connected clients
    socketio.emit('data_refreshed', {'time': get_sydney_time().strftime("%H:%M:%S")})
    
    print(f"[{get_sydney_time()}] Daily refresh complete!")


def scrape_todays_races():
    """Scrape today's race meetings and odds"""
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
            
            # Go to punters.com.au to get today's meetings
            page.goto("https://www.punters.com.au/racing/", timeout=30000)
            time.sleep(3)
            
            # Get all meeting links
            meetings = []
            meeting_links = page.query_selector_all('a[href*="/form-guide/"]')
            
            for link in meeting_links:
                href = link.get_attribute('href')
                if href and '/form-guide/' in href:
                    meetings.append(href)
            
            all_odds = []
            
            for meeting_url in meetings[:10]:  # Limit to first 10 meetings
                try:
                    if not meeting_url.startswith('http'):
                        meeting_url = f"https://www.punters.com.au{meeting_url}"
                    
                    page.goto(meeting_url, timeout=30000)
                    time.sleep(2)
                    
                    # Extract venue name from URL
                    venue_match = re.search(r'/form-guide/([^/]+)/', meeting_url)
                    venue = venue_match.group(1).replace('-', ' ').title() if venue_match else 'Unknown'
                    
                    # Find race links
                    race_links = page.query_selector_all('a[href*="/race-"]')
                    
                    for race_link in race_links:
                        race_href = race_link.get_attribute('href')
                        race_match = re.search(r'/race-(\d+)', race_href)
                        if race_match:
                            race_num = int(race_match.group(1))
                            
                            # Scrape odds for this race
                            horses = scrape_race_odds_page(page, race_href)
                            
                            if horses:
                                all_odds.append({
                                    'venue': venue,
                                    'race_number': race_num,
                                    'horses': horses,
                                    'url': race_href
                                })
                                
                except Exception as e:
                    print(f"Error scraping meeting: {e}")
                    continue
            
            browser.close()
            
            # Save odds data
            if all_odds:
                odds_file = os.path.join(folder, "odds_data.json")
                with open(odds_file, 'w', encoding='utf-8') as f:
                    json.dump(all_odds, f, indent=2)
                print(f"Saved {len(all_odds)} races to {odds_file}")
            
    except Exception as e:
        print(f"Error in scrape_todays_races: {e}")


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
        if favourite['edge'] < -0.05:  # 5% negative edge
            race_data['dud_favourites'].append({
                'venue': venue,
                'race_number': race_num,
                'favourite': favourite['name'],
                'odds': favourite['best_odds'],
                'model_prob': favourite['model_prob'],
                'implied_prob': favourite['implied_prob'],
                'edge': favourite['edge'],
                'better_picks': [h['name'] for h in horse_odds[:3] if h['name'] != favourite['name']][:2]
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
        
        # Check for arbitrage (dutch book < 1)
        if dutch_book < 1.0:
            guaranteed_profit = (1.0 / dutch_book - 1) * 100  # As percentage
            
            race_data['arb_opportunities'].append({
                'venue': venue,
                'race_number': race_num,
                'dutch_book': dutch_book,
                'guaranteed_profit_pct': guaranteed_profit,
                'horses': horse_odds,
                'field_size': len(horse_odds),
                'url': odds_race.get('url', ''),
                'last_checked': datetime.now().strftime("%H:%M:%S"),
                'status': 'active'
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
