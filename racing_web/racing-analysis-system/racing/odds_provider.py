"""
Odds provider module - fetches odds from various sources
Supports punters.com.au scraping and CSV fallback
"""

import os
import re
import csv
import json
from abc import ABC, abstractmethod
from datetime import datetime

from . import config
from .name_matcher import normalize_name


class OddsProvider(ABC):
    """Abstract base class for odds providers"""
    
    @abstractmethod
    def get_odds(self, venue: str, date: str, race_number: int) -> dict:
        """
        Get odds for a specific race.
        
        Args:
            venue: Venue name (e.g., "Canterbury")
            date: Date string (YYYYMMDD)
            race_number: Race number (1-based)
        
        Returns:
            Dict mapping horse_name -> decimal_odds
            Returns empty dict if odds unavailable
        """
        pass
    
    @abstractmethod
    def get_all_odds_for_meeting(self, venue: str, date: str) -> dict:
        """
        Get odds for all races at a meeting.
        
        Returns:
            Dict mapping race_number -> {horse_name: odds}
        """
        pass


class ScrapedOddsProvider(OddsProvider):
    """Provider that uses already-scraped odds from OddsScraper"""
    
    def __init__(self, odds_data: list = None, json_path: str = None):
        """
        Initialize with scraped odds data.
        
        Args:
            odds_data: List of race dicts from OddsScraper.all_odds
            json_path: Path to odds_data.json file
        """
        self.odds_by_race = {}
        
        if odds_data:
            self._load_from_list(odds_data)
        elif json_path and os.path.exists(json_path):
            self._load_from_json(json_path)
    
    def _load_from_list(self, odds_data: list):
        """Load odds from a list of race dicts"""
        for race in odds_data:
            venue = normalize_name(race.get('venue', ''))
            race_num = race.get('race_number', 0)
            
            if not venue or not race_num:
                continue
            
            key = (venue, race_num)
            self.odds_by_race[key] = {}
            
            for horse in race.get('horses', []):
                name = horse.get('name', '')
                odds = horse.get('best_odds')
                
                if name and odds and odds > 1:
                    self.odds_by_race[key][normalize_name(name)] = odds
    
    def _load_from_json(self, json_path: str):
        """Load odds from JSON file"""
        try:
            with open(json_path, 'r') as f:
                odds_data = json.load(f)
            self._load_from_list(odds_data)
        except Exception as e:
            print(f"Error loading odds JSON: {e}")
    
    def get_odds(self, venue: str, date: str, race_number: int) -> dict:
        key = (normalize_name(venue), race_number)
        return self.odds_by_race.get(key, {})
    
    def get_all_odds_for_meeting(self, venue: str, date: str) -> dict:
        venue_norm = normalize_name(venue)
        result = {}
        
        for (v, race_num), odds in self.odds_by_race.items():
            if v == venue_norm:
                result[race_num] = odds
        
        return result


class CSVOddsProvider(OddsProvider):
    """Provider that reads odds from a CSV file"""
    
    def __init__(self, csv_path: str):
        """
        Initialize with a CSV file.
        
        Expected CSV format:
        Venue,Race,Horse,Odds
        Canterbury,1,Flying Wahine,3.50
        ...
        """
        self.odds_by_race = {}
        self._load_csv(csv_path)
    
    def _load_csv(self, csv_path: str):
        """Load odds from CSV file"""
        if not os.path.exists(csv_path):
            print(f"Odds CSV not found: {csv_path}")
            return
        
        try:
            with open(csv_path, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                
                for row in reader:
                    venue = normalize_name(row.get('Venue', ''))
                    try:
                        race_num = int(row.get('Race', 0))
                        odds = float(row.get('Odds', 0))
                    except:
                        continue
                    
                    horse = row.get('Horse', '')
                    
                    if venue and race_num and horse and odds > 1:
                        key = (venue, race_num)
                        if key not in self.odds_by_race:
                            self.odds_by_race[key] = {}
                        self.odds_by_race[key][normalize_name(horse)] = odds
        
        except Exception as e:
            print(f"Error loading odds CSV: {e}")
    
    def get_odds(self, venue: str, date: str, race_number: int) -> dict:
        key = (normalize_name(venue), race_number)
        return self.odds_by_race.get(key, {})
    
    def get_all_odds_for_meeting(self, venue: str, date: str) -> dict:
        venue_norm = normalize_name(venue)
        result = {}
        
        for (v, race_num), odds in self.odds_by_race.items():
            if v == venue_norm:
                result[race_num] = odds
        
        return result


class CompositeOddsProvider(OddsProvider):
    """Provider that tries multiple sources in order"""
    
    def __init__(self, providers: list):
        """
        Initialize with list of providers to try in order.
        
        Args:
            providers: List of OddsProvider instances
        """
        self.providers = providers
    
    def get_odds(self, venue: str, date: str, race_number: int) -> dict:
        for provider in self.providers:
            odds = provider.get_odds(venue, date, race_number)
            if odds:
                return odds
        return {}
    
    def get_all_odds_for_meeting(self, venue: str, date: str) -> dict:
        for provider in self.providers:
            odds = provider.get_all_odds_for_meeting(venue, date)
            if odds:
                return odds
        return {}


def create_odds_provider(scraped_odds: list = None, 
                         json_path: str = None,
                         csv_path: str = None) -> OddsProvider:
    """
    Factory function to create the appropriate odds provider.
    
    Args:
        scraped_odds: Already scraped odds data (list of race dicts)
        json_path: Path to odds_data.json
        csv_path: Path to fallback CSV
    
    Returns:
        Configured OddsProvider instance
    """
    providers = []
    
    # Primary: scraped odds
    if scraped_odds:
        providers.append(ScrapedOddsProvider(odds_data=scraped_odds))
    elif json_path and os.path.exists(json_path):
        providers.append(ScrapedOddsProvider(json_path=json_path))
    
    # Fallback: CSV
    if csv_path and os.path.exists(csv_path):
        providers.append(CSVOddsProvider(csv_path))
    
    if not providers:
        # Return empty provider
        return ScrapedOddsProvider(odds_data=[])
    
    if len(providers) == 1:
        return providers[0]
    
    return CompositeOddsProvider(providers)
