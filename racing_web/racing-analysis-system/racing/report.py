"""
Report generation for Value Finder + Dutching Engine
Outputs Discord-ready markdown, console summary, and CSV files
"""

import os
import csv
from datetime import datetime
from typing import List

from . import config
from .model import RaceAnalysis, HorseAnalysis
from .dutching import DutchResult


class ReportGenerator:
    """Generates various report formats from race analysis"""
    
    def __init__(self, output_folder: str):
        self.output_folder = output_folder
        self.timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    def generate_all_reports(self, 
                             analyses: List[RaceAnalysis],
                             bankroll: float = None) -> dict:
        """
        Generate all report formats.
        
        Returns:
            Dict with paths to generated files
        """
        if bankroll is None:
            bankroll = config.BANKROLL
        
        files = {}
        
        # Console summary (always)
        self.print_console_summary(analyses, bankroll)
        
        # Discord markdown
        discord_path = self.save_discord_report(analyses, bankroll)
        if discord_path:
            files['discord'] = discord_path
        
        # CSV reports
        csv_path = self.save_value_csv(analyses)
        if csv_path:
            files['value_csv'] = csv_path
        
        dutch_path = self.save_dutch_csv(analyses)
        if dutch_path:
            files['dutch_csv'] = dutch_path
        
        return files
    
    def print_console_summary(self, analyses: List[RaceAnalysis], bankroll: float):
        """Print comprehensive console summary"""
        print("\n" + "=" * 70)
        print("🎯 VALUE FINDER + DUTCHING ENGINE RESULTS")
        print("=" * 70)
        
        # Stats
        races_with_value = [r for r in analyses if r.value_backs]
        races_with_dud = [r for r in analyses if r.has_dud_favourite]
        races_with_dutch = [r for r in analyses if r.dutch_recommendation]
        
        print(f"\n📊 SUMMARY")
        print(f"   Races analyzed: {len(analyses)}")
        print(f"   Races with value backs: {len(races_with_value)}")
        print(f"   Dud favourite alerts: {len(races_with_dud)}")
        print(f"   Dutch recommendations: {len(races_with_dutch)}")
        
        # Top value backs
        all_value = []
        for race in analyses:
            for horse in race.value_backs:
                all_value.append((race, horse))
        
        if all_value:
            # Sort by edge
            all_value.sort(key=lambda x: x[1].edge or 0, reverse=True)
            
            print(f"\n🔥 TOP VALUE BACKS (Edge >= {config.EDGE_MIN*100:.0f}%)")
            print("-" * 70)
            
            for race, horse in all_value[:10]:
                print(f"   {race.venue} R{race.race_number}: {horse.name}")
                print(f"      Odds: ${horse.market_odds:.2f} | "
                      f"Model: {horse.model_prob*100:.1f}% | "
                      f"Fair: ${horse.model_fair_odds:.2f} | "
                      f"Edge: +{horse.edge*100:.1f}%")
        
        # Dud favourites
        if races_with_dud:
            print(f"\n⚠️ DUD FAVOURITE ALERTS")
            print("-" * 70)
            
            for race in races_with_dud:
                fav = race.favourite
                gap = fav.implied_prob - fav.model_prob
                print(f"   {race.venue} R{race.race_number}: {fav.name}")
                print(f"      Odds: ${fav.market_odds:.2f} (Implied: {fav.implied_prob*100:.1f}%) | "
                      f"Model: {fav.model_prob*100:.1f}% | Gap: +{gap*100:.1f}%")
        
        # Dutch recommendations
        if races_with_dutch:
            print(f"\n🎲 DUTCH RECOMMENDATIONS (Bankroll: ${bankroll:.0f})")
            print("-" * 70)
            
            for race in races_with_dutch:
                dutch = race.dutch_recommendation
                result = dutch['result']
                dtype = dutch['type'].upper().replace('_', ' ')
                
                print(f"\n   {race.venue} R{race.race_number} [{dtype}]")
                print(f"   Book: {result.dutch_book:.3f} {'(ARB!)' if result.is_arb else ''}")
                
                for stake in result.stakes:
                    print(f"      • {stake.horse_name}: ${stake.stake:.2f} "
                          f"@ ${stake.odds:.2f} → +${stake.profit_if_wins:.2f} if wins")
                
                print(f"   Combined prob: {result.combined_model_prob*100:.1f}% | "
                      f"EV: ${result.expected_value:.2f} ({result.roi_percent:.1f}% ROI)")
        
        print("\n" + "=" * 70)
    
    def format_discord_race(self, race: RaceAnalysis, bankroll: float) -> str:
        """Format a single race for Discord"""
        lines = []
        
        # Header
        lines.append(f"**{race.venue} R{race.race_number} — Field: {race.field_size}**")
        
        # Favourite info
        if race.favourite:
            fav = race.favourite
            dud_status = "🚨 YES" if race.has_dud_favourite else "No"
            gap = (fav.implied_prob - fav.model_prob) * 100 if fav.implied_prob else 0
            
            lines.append(f"Favourite: **{fav.name}** @ ${fav.market_odds:.2f} "
                        f"(Model: {fav.model_prob*100:.0f}%, Implied: {fav.implied_prob*100:.0f}%)")
            lines.append(f"Dud favourite: {dud_status} (Gap: {gap:+.0f}%)")
        
        lines.append("")
        
        # Value backs
        if race.value_backs:
            lines.append("**Top Value Backs:**")
            for horse in race.value_backs[:config.SHOW_TOP_VALUE_BACKS]:
                lines.append(f"• {horse.name} — ${horse.market_odds:.2f} — "
                           f"Model {horse.model_prob*100:.0f}% — "
                           f"Fair ${horse.model_fair_odds:.2f} — "
                           f"Edge +{horse.edge*100:.0f}%")
            lines.append("")
        
        # Dutch recommendation
        if race.dutch_recommendation:
            result = race.dutch_recommendation['result']
            dtype = race.dutch_recommendation['type']
            
            if dtype == 'dud_favourite':
                lines.append(f"**Dutch Recommendation (Oppose Favourite, ${bankroll:.0f}):**")
            else:
                lines.append(f"**Dutch Recommendation (${bankroll:.0f}):**")
            
            for stake in result.stakes:
                lines.append(f"• {stake.horse_name} stake ${stake.stake:.2f} "
                           f"→ profit if wins +${stake.profit_if_wins:.2f}")
            
            arb_note = " **(ARB!)**" if result.is_arb else ""
            lines.append(f"Dutch book: {result.dutch_book:.3f}{arb_note}")
            lines.append(f"Model EV: ${result.expected_value:+.2f} ({result.roi_percent:.1f}% ROI)")
        
        return "\n".join(lines)
    
    def save_discord_report(self, analyses: List[RaceAnalysis], bankroll: float) -> str:
        """Save Discord-ready markdown report"""
        lines = []
        
        # Header
        lines.append("# 🎯 Racing Value Report")
        lines.append(f"*Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}*")
        lines.append("")
        
        # Quick stats
        value_count = sum(len(r.value_backs) for r in analyses)
        dud_count = sum(1 for r in analyses if r.has_dud_favourite)
        dutch_count = sum(1 for r in analyses if r.dutch_recommendation)
        
        lines.append(f"📊 **{len(analyses)}** races | "
                    f"**{value_count}** value backs | "
                    f"**{dud_count}** dud favourites | "
                    f"**{dutch_count}** dutch opps")
        lines.append("")
        lines.append("---")
        lines.append("")
        
        # Races with opportunities
        interesting_races = [
            r for r in analyses 
            if r.value_backs or r.has_dud_favourite or r.dutch_recommendation
        ]
        
        # Sort by value
        interesting_races.sort(
            key=lambda r: (
                r.dutch_recommendation['result'].expected_value 
                if r.dutch_recommendation else 0
            ),
            reverse=True
        )
        
        # Limit for Discord
        for race in interesting_races[:config.DISCORD_MAX_RACES]:
            lines.append(self.format_discord_race(race, bankroll))
            lines.append("")
            lines.append("---")
            lines.append("")
        
        # Save
        content = "\n".join(lines)
        filepath = os.path.join(self.output_folder, f"discord_report_{self.timestamp}.md")
        
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        
        print(f"\n📝 Discord report saved: {os.path.basename(filepath)}")
        return filepath
    
    def save_value_csv(self, analyses: List[RaceAnalysis]) -> str:
        """Save detailed value analysis CSV"""
        rows = []
        
        for race in analyses:
            for horse in race.horses:
                rows.append({
                    'Venue': race.venue,
                    'Date': datetime.now().strftime('%Y-%m-%d'),
                    'Race': race.race_number,
                    'FieldSize': race.field_size,
                    'Horse': horse.name,
                    'Barrier': horse.barrier,
                    'Form': horse.form,
                    'FormScore': horse.form_score,
                    'Strength': round(horse.strength, 2),
                    'ModelProb': round(horse.model_prob, config.CSV_DECIMAL_PLACES),
                    'FairOdds': round(horse.model_fair_odds, 2),
                    'MarketOdds': horse.market_odds or '',
                    'ImpliedProb': round(horse.implied_prob, config.CSV_DECIMAL_PLACES) if horse.implied_prob else '',
                    'Edge': round(horse.edge, config.CSV_DECIMAL_PLACES) if horse.edge else '',
                    'ValueROI': round(horse.value_roi, config.CSV_DECIMAL_PLACES) if horse.value_roi else '',
                    'IsValue': horse.is_value,
                    'IsFavourite': horse.is_favourite,
                    'IsDudFavourite': horse.is_dud_favourite
                })
        
        if not rows:
            return None
        
        filepath = os.path.join(self.output_folder, f"value_analysis_{self.timestamp}.csv")
        
        fieldnames = list(rows[0].keys())
        with open(filepath, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        
        print(f"📊 Value CSV saved: {os.path.basename(filepath)}")
        return filepath
    
    def save_dutch_csv(self, analyses: List[RaceAnalysis]) -> str:
        """Save dutch recommendations CSV"""
        rows = []
        
        for race in analyses:
            if not race.dutch_recommendation:
                continue
            
            result = race.dutch_recommendation['result']
            dtype = race.dutch_recommendation['type']
            
            for stake in result.stakes:
                rows.append({
                    'Venue': race.venue,
                    'Race': race.race_number,
                    'Type': dtype,
                    'Horse': stake.horse_name,
                    'Odds': stake.odds,
                    'Stake': stake.stake,
                    'ProfitIfWins': stake.profit_if_wins,
                    'ModelProb': round(stake.model_prob, config.CSV_DECIMAL_PLACES),
                    'DutchBook': result.dutch_book,
                    'IsArb': result.is_arb,
                    'CombinedProb': result.combined_model_prob,
                    'ExpectedValue': result.expected_value,
                    'ROI_Percent': result.roi_percent
                })
        
        if not rows:
            return None
        
        filepath = os.path.join(self.output_folder, f"dutch_recommendations_{self.timestamp}.csv")
        
        fieldnames = list(rows[0].keys())
        with open(filepath, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        
        print(f"📊 Dutch CSV saved: {os.path.basename(filepath)}")
        return filepath


def generate_quick_discord_message(analyses: List[RaceAnalysis], 
                                   bankroll: float = None) -> str:
    """
    Generate a compact Discord message for quick sharing.
    Focuses on the best opportunities only.
    """
    if bankroll is None:
        bankroll = config.BANKROLL
    
    lines = []
    lines.append("🎯 **Quick Racing Alerts**")
    lines.append("")
    
    # Best value backs
    all_value = []
    for race in analyses:
        for horse in race.value_backs:
            all_value.append((race, horse))
    
    all_value.sort(key=lambda x: x[1].edge or 0, reverse=True)
    
    if all_value:
        lines.append("**🔥 Best Value:**")
        for race, horse in all_value[:5]:
            lines.append(f"• {race.venue} R{race.race_number}: "
                        f"{horse.name} ${horse.market_odds:.2f} "
                        f"(+{horse.edge*100:.0f}% edge)")
        lines.append("")
    
    # Best dutch
    dutch_races = [r for r in analyses if r.dutch_recommendation]
    dutch_races.sort(
        key=lambda r: r.dutch_recommendation['result'].expected_value,
        reverse=True
    )
    
    if dutch_races:
        lines.append("**🎲 Best Dutch:**")
        for race in dutch_races[:3]:
            result = race.dutch_recommendation['result']
            arb = " (ARB!)" if result.is_arb else ""
            lines.append(f"• {race.venue} R{race.race_number}: "
                        f"EV ${result.expected_value:+.2f}{arb}")
        lines.append("")
    
    # Dud favourites
    dud_races = [r for r in analyses if r.has_dud_favourite]
    if dud_races:
        lines.append("**⚠️ Dud Favourites:**")
        for race in dud_races[:5]:
            fav = race.favourite
            gap = (fav.implied_prob - fav.model_prob) * 100
            lines.append(f"• {race.venue} R{race.race_number}: "
                        f"{fav.name} (+{gap:.0f}% overbet)")
    
    return "\n".join(lines)
