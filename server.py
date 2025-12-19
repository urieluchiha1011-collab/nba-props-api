import os
import gc
from flask import Flask, jsonify, request
from flask_cors import CORS
from datetime import datetime
import time
import threading
from threading import Thread, Lock
import requests

# ═══════════════════════════════════════════════════════════════
# GITHUB PACKAGES ONLY - NO EXTERNAL APIS (except ESPN for injuries)
# ═══════════════════════════════════════════════════════════════

# GitHub: swar/nba_api - Official NBA Stats
from nba_api.stats.endpoints import playergamelog, teamgamelog
from nba_api.stats.static import players, teams
from nba_api.live.nba.endpoints import scoreboard

# ESPN Injury API (FREE, no auth, no Java!)
ESPN_INJURY_URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/injuries"

app = Flask(__name__)
CORS(app)

# ═══════════════════════════════════════════════════════════════
# AUTO-UPDATE SYSTEM - REFRESHES EVERY FEW SECONDS
# ═══════════════════════════════════════════════════════════════

# Update intervals (in seconds)
INJURY_UPDATE_INTERVAL = 30    # Injuries every 30 seconds
GAMES_UPDATE_INTERVAL = 10     # Live games every 10 seconds
PLAYER_CACHE_TTL = 300         # Player stats cache for 5 minutes

# Thread-safe cache
CACHE = {
    'injuries': {
        'data': {'teams': {}, 'injured_players': [], 'total': 0},
        'updated': None,
        'source': 'loading...'
    },
    'games': {
        'data': {'games': [], 'count': 0, 'date': ''},
        'updated': None
    },
    'players': {}  # player_id -> {data, timestamp}
}
CACHE_LOCK = Lock()

# Cache players/teams list once at startup
ALL_PLAYERS = players.get_players()
ALL_TEAMS = teams.get_teams()
print(f"✅ Loaded {len(ALL_PLAYERS)} players and {len(ALL_TEAMS)} teams")

def find_player(name):
    name_lower = name.lower()
    for p in ALL_PLAYERS:
        if name_lower in p['full_name'].lower():
            return p['id'], p['full_name']
    return None, None

def find_team(abbr):
    abbr = abbr.upper()
    for t in ALL_TEAMS:
        if t['abbreviation'] == abbr:
            return t
    return None

# ═══════════════════════════════════════════════════════════════
# BACKGROUND UPDATE THREADS
# ═══════════════════════════════════════════════════════════════

def update_injuries_loop():
    """Background thread: Update injuries every 30 seconds using ESPN API"""
    global CACHE
    print(f"🔄 Injuries updater started (every {INJURY_UPDATE_INTERVAL}s) - Using ESPN API")
    
    # ESPN team abbreviation mapping
    ESPN_TO_NBA = {
        'GS': 'GSW', 'SA': 'SAS', 'NY': 'NYK', 'NO': 'NOP', 
        'UTAH': 'UTA', 'WSH': 'WAS', 'PHX': 'PHO'
    }
    
    while True:
        try:
            response = requests.get(ESPN_INJURY_URL, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                teams_data = {}
                injured_players = []
                
                for team in data.get('injuries', []):
                    espn_abbr = team.get('team', {}).get('abbreviation', 'UNK')
                    team_abbr = ESPN_TO_NBA.get(espn_abbr, espn_abbr)
                    
                    if team_abbr not in teams_data:
                        teams_data[team_abbr] = []
                    
                    for player in team.get('injuries', []):
                        name = player.get('athlete', {}).get('displayName', '')
                        status = player.get('status', '')
                        reason = player.get('details', {}).get('detail', '') or player.get('longComment', '')
                        
                        teams_data[team_abbr].append({
                            'name': name,
                            'status': status,
                            'reason': reason
                        })
                        
                        if status in ['Out', 'Questionable', 'Doubtful', 'Day-To-Day']:
                            injured_players.append(name.lower())
                
                with CACHE_LOCK:
                    CACHE['injuries'] = {
                        'data': {
                            'teams': teams_data,
                            'injured_players': injured_players,
                            'total': len(injured_players)
                        },
                        'updated': datetime.now().isoformat(),
                        'source': 'ESPN NBA Injuries (Live)'
                    }
                print(f"✅ [{datetime.now().strftime('%H:%M:%S')}] Injuries updated: {len(injured_players)} players")
            else:
                print(f"⚠️ ESPN API returned status {response.status_code}")
                
        except Exception as e:
            print(f"⚠️ Injury update error: {e}")
            with CACHE_LOCK:
                if CACHE['injuries']['updated'] is None:
                    CACHE['injuries'] = {
                        'data': {'teams': {}, 'injured_players': [], 'total': 0},
                        'updated': datetime.now().isoformat(),
                        'source': f'ESPN API error: {str(e)[:30]}'
                    }
        
        time.sleep(INJURY_UPDATE_INTERVAL)

def update_games_loop():
    """Background thread: Update live games every 10 seconds"""
    global CACHE
    print(f"🔄 Games updater started (every {GAMES_UPDATE_INTERVAL}s)")
    
    while True:
        try:
            board = scoreboard.ScoreBoard()
            data = board.get_dict()
            games = []
            
            for g in data.get('scoreboard', {}).get('games', []):
                home = g.get('homeTeam', {})
                away = g.get('awayTeam', {})
                
                games.append({
                    'game_id': g.get('gameId'),
                    'home_team': home.get('teamTricode'),
                    'away_team': away.get('teamTricode'),
                    'home_score': home.get('score', 0),
                    'away_score': away.get('score', 0),
                    'status': g.get('gameStatusText'),
                    'start_time': g.get('gameTimeUTC'),
                    'period': g.get('period', 0),
                    'game_clock': g.get('gameClock', ''),
                    'home_record': f"{home.get('wins', 0)}-{home.get('losses', 0)}",
                    'away_record': f"{away.get('wins', 0)}-{away.get('losses', 0)}"
                })
            
            with CACHE_LOCK:
                CACHE['games'] = {
                    'data': {
                        'games': games,
                        'count': len(games),
                        'date': datetime.now().strftime('%Y-%m-%d')
                    },
                    'updated': datetime.now().isoformat()
                }
            print(f"✅ [{datetime.now().strftime('%H:%M:%S')}] Games updated: {len(games)} games")
        except Exception as e:
            print(f"⚠️ Games update error: {e}")
        
        time.sleep(GAMES_UPDATE_INTERVAL)

def start_auto_updates():
    """Start all background update threads"""
    print("\n" + "═" * 60)
    print("🚀 STARTING AUTO-UPDATE SYSTEM")
    print("═" * 60)
    
    # Start injury updater
    t1 = Thread(target=update_injuries_loop, daemon=True)
    t1.start()
    
    # Start games updater
    t2 = Thread(target=update_games_loop, daemon=True)
    t2.start()
    
    print("═" * 60)
    print("✅ Auto-updates running!")
    print(f"   • Injuries: Every {INJURY_UPDATE_INTERVAL} seconds")
    print(f"   • Games: Every {GAMES_UPDATE_INTERVAL} seconds")
    print("═" * 60 + "\n")

# ═══════════════════════════════════════════════════════════════
# API ENDPOINTS
# ═══════════════════════════════════════════════════════════════

@app.route('/api/health')
def health():
    with CACHE_LOCK:
        injuries_updated = CACHE['injuries']['updated']
        games_updated = CACHE['games']['updated']
    
    return jsonify({
        'status': 'ok',
        'time': datetime.now().isoformat(),
        'auto_update': {
            'injuries': {
                'interval': f'{INJURY_UPDATE_INTERVAL}s',
                'last_update': injuries_updated
            },
            'games': {
                'interval': f'{GAMES_UPDATE_INTERVAL}s', 
                'last_update': games_updated
            }
        },
        'sources': {
            'stats': 'nba_api (GitHub: swar/nba_api)',
            'injuries': 'ESPN NBA Injuries API (Live)'
        }
    })

@app.route('/api/injuries')
def get_injuries():
    """Get cached injuries (auto-updated every 30s)"""
    with CACHE_LOCK:
        cached = CACHE['injuries'].copy()
    
    return jsonify({
        'updated': cached['updated'],
        'source': cached['source'],
        'teams': cached['data']['teams'],
        'injured_players': cached['data']['injured_players'],
        'total': cached['data']['total'],
        'auto_refresh': f'Every {INJURY_UPDATE_INTERVAL} seconds',
        'next_update_in': f'{INJURY_UPDATE_INTERVAL}s'
    })

@app.route('/api/games/today')
def get_games():
    """Get cached live games (auto-updated every 10s)"""
    with CACHE_LOCK:
        cached = CACHE['games'].copy()
    
    return jsonify({
        'date': cached['data'].get('date', datetime.now().strftime('%Y-%m-%d')),
        'games': cached['data'].get('games', []),
        'count': cached['data'].get('count', 0),
        'updated': cached['updated'],
        'auto_refresh': f'Every {GAMES_UPDATE_INTERVAL} seconds',
        'source': 'nba_api (GitHub: swar/nba_api)'
    })

@app.route('/api/games/live')
def get_live_scores():
    """Get live scores with real-time data"""
    with CACHE_LOCK:
        cached = CACHE['games'].copy()
    
    live_games = [g for g in cached['data'].get('games', []) 
                  if g.get('period', 0) > 0 and 'Final' not in str(g.get('status', ''))]
    
    return jsonify({
        'live_games': live_games,
        'count': len(live_games),
        'updated': cached['updated'],
        'auto_refresh': f'Every {GAMES_UPDATE_INTERVAL} seconds'
    })

@app.route('/api/teams')
def get_teams():
    return jsonify({
        'teams': [{'id': t['id'], 'abbreviation': t['abbreviation'], 'full_name': t['full_name'], 'city': t['city']} for t in ALL_TEAMS],
        'source': 'nba_api (GitHub: swar/nba_api)'
    })

@app.route('/api/team/<abbr>')
def get_team_stats(abbr):
    try:
        team = find_team(abbr)
        if not team:
            return jsonify({'error': 'Team not found'}), 404
        
        time.sleep(0.6)
        log = teamgamelog.TeamGameLog(team_id=team['id'], season='2025-26')
        df = log.get_data_frames()[0]
        
        if df.empty:
            return jsonify({'error': 'No games found'}), 404
        
        wins = len(df[df['WL'] == 'W'])
        losses = len(df[df['WL'] == 'L'])
        
        home_games = df[df['MATCHUP'].str.contains('vs.')]
        away_games = df[df['MATCHUP'].str.contains('@')]
        home_wins = len(home_games[home_games['WL'] == 'W'])
        home_losses = len(home_games[home_games['WL'] == 'L'])
        away_wins = len(away_games[away_games['WL'] == 'W'])
        away_losses = len(away_games[away_games['WL'] == 'L'])
        
        last10 = df.head(10)
        l10_wins = len(last10[last10['WL'] == 'W'])
        
        streak = 0
        streak_type = df.iloc[0]['WL'] if len(df) > 0 else 'W'
        for _, row in df.iterrows():
            if row['WL'] == streak_type:
                streak += 1
            else:
                break
        
        ppg = df['PTS'].mean()
        opp_ppg = ppg - df['PLUS_MINUS'].mean() if 'PLUS_MINUS' in df.columns else ppg
        
        recent = []
        for _, row in df.head(5).iterrows():
            recent.append({
                'date': row['GAME_DATE'],
                'matchup': row['MATCHUP'],
                'result': row['WL'],
                'pts': int(row['PTS'])
            })
        
        del df, log
        gc.collect()
        
        return jsonify({
            'team': team,
            'record': {'wins': wins, 'losses': losses},
            'home': {'wins': home_wins, 'losses': home_losses},
            'away': {'wins': away_wins, 'losses': away_losses},
            'last10': str(l10_wins) + '-' + str(10-l10_wins),
            'streak': streak_type + str(streak),
            'ppg': round(ppg, 1),
            'opp_ppg': round(opp_ppg, 1),
            'diff': round(ppg - opp_ppg, 1),
            'recent': recent,
            'source': 'nba_api (GitHub: swar/nba_api)'
        })
    except Exception as e:
        gc.collect()
        return jsonify({'error': str(e)}), 500

@app.route('/api/player/<n>')
def get_player(name):
    try:
        pid, full_name = find_player(name)
        if not pid:
            return jsonify({'error': 'Player not found'}), 404
        
        # Check cache first
        now = time.time()
        with CACHE_LOCK:
            if pid in CACHE['players']:
                cached = CACHE['players'][pid]
                if now - cached['timestamp'] < PLAYER_CACHE_TTL:
                    return jsonify(cached['data'])
        
        time.sleep(0.6)
        log = playergamelog.PlayerGameLog(player_id=pid, season='2025-26')
        df = log.get_data_frames()[0]
        
        if df.empty:
            del df, log
            gc.collect()
            return jsonify({'error': 'No games found'}), 404
        
        result = {
            'name': full_name,
            'games': len(df),
            'averages': {
                'pts': round(df['PTS'].mean(), 1),
                'reb': round(df['REB'].mean(), 1),
                'ast': round(df['AST'].mean(), 1),
                'fg3m': round(df['FG3M'].mean(), 1),
                'stl': round(df['STL'].mean(), 1),
                'blk': round(df['BLK'].mean(), 1)
            },
            'source': 'nba_api (GitHub: swar/nba_api)',
            'cached_until': datetime.fromtimestamp(now + PLAYER_CACHE_TTL).isoformat()
        }
        
        # Cache the result
        with CACHE_LOCK:
            CACHE['players'][pid] = {'data': result, 'timestamp': now}
        
        del df, log
        gc.collect()
        return jsonify(result)
    except Exception as e:
        gc.collect()
        return jsonify({'error': str(e)}), 500

# ═══════════════════════════════════════════════════════════════
# PROP ANALYSIS ENDPOINT
# ═══════════════════════════════════════════════════════════════

@app.route('/api/analyze', methods=['POST'])
def analyze():
    try:
        props = request.get_json().get('props', [])
        results = []
        locks = []
        
        # Get injured players from cache (auto-updated)
        with CACHE_LOCK:
            injured = CACHE['injuries']['data']['injured_players'].copy()
            injury_source = CACHE['injuries']['source']
        
        stat_map = {
            'pts': 'PTS', 'points': 'PTS',
            'reb': 'REB', 'rebounds': 'REB',
            'ast': 'AST', 'assists': 'AST',
            'fg3m': 'FG3M', '3pm': 'FG3M', 'threes': 'FG3M', '3pt': 'FG3M',
            'stl': 'STL', 'steals': 'STL',
            'blk': 'BLK', 'blocks': 'BLK',
            'tov': 'TOV', 'turnovers': 'TOV', 'turnover': 'TOV'
        }
        
        for p in props[:15]:
            name = p.get('name', '')
            line = float(p.get('line', 0))
            stat = p.get('stat', 'pts')
            
            # Check if injured (from auto-updated cache)
            if any(name.lower() in inj or inj in name.lower() for inj in injured):
                results.append({'name': name, 'verdict': 'SKIP', 'reason': 'INJURED', 'injured': True})
                continue
            
            pid, full_name = find_player(name)
            if not pid:
                results.append({'name': name, 'verdict': 'SKIP', 'reason': 'Not found'})
                continue
            
            try:
                time.sleep(0.5)
                log = playergamelog.PlayerGameLog(player_id=pid, season='2025-26')
                df = log.get_data_frames()[0]
                
                if df.empty:
                    results.append({'name': full_name, 'verdict': 'SKIP', 'reason': 'No games'})
                    del df, log
                    gc.collect()
                    continue
                
                col = stat_map.get(stat.lower(), 'PTS')
                values = df[col].tolist()
                avg = float(df[col].mean())
                median = float(df[col].median())
                std_dev = float(df[col].std()) if len(df) > 1 else avg * 0.18
                edge = avg - line
                games = len(df)
                
                # Hit rates
                over_hits = len([v for v in values if v > line])
                hit_rate = (over_hits / games * 100) if games > 0 else 0
                
                # Last 5 games
                last5 = values[:5] if len(values) >= 5 else values
                last5_avg = sum(last5) / len(last5) if last5 else avg
                last5_hits = len([v for v in last5 if v > line])
                
                # Home/Away split
                home_games = df[df['MATCHUP'].str.contains('vs.')]
                away_games = df[df['MATCHUP'].str.contains('@')]
                home_avg = float(home_games[col].mean()) if len(home_games) > 0 else avg
                away_avg = float(away_games[col].mean()) if len(away_games) > 0 else avg
                
                # Confidence calculation
                confidence = 50
                confidence += min(25, max(-25, edge * 4))
                if games >= 20: confidence += 10
                elif games < 15: confidence -= 5
                if abs(edge) >= 5: confidence += 8
                if abs(edge) >= 7: confidence += 7
                if hit_rate >= 70: confidence += 5
                elif hit_rate <= 30: confidence -= 5
                if last5_hits >= 4: confidence += 3
                elif last5_hits <= 1: confidence -= 3
                confidence = max(5, min(95, confidence))
                
                del df, log
                gc.collect()
                
                direction = 'OVER' if edge > 0 else 'UNDER'
                
                result_data = {
                    'name': full_name,
                    'avg': round(avg, 1),
                    'median': round(median, 1),
                    'edge': round(edge, 1),
                    'games': games,
                    'hit_rate': round(hit_rate, 1),
                    'last5_avg': round(last5_avg, 1),
                    'last5_hits': last5_hits,
                    'home_avg': round(home_avg, 1),
                    'away_avg': round(away_avg, 1),
                    'std_dev': round(std_dev, 2),
                    'confidence': round(confidence)
                }
                
                if confidence >= 85 and games >= 15:
                    locks.append({**result_data, 'line': line, 'stat': stat, 'direction': direction})
                    result_data['verdict'] = '🔒 LOCK ' + direction
                elif confidence >= 75:
                    result_data['verdict'] = '✅ GOOD ' + direction
                else:
                    result_data['verdict'] = 'SKIP'
                
                results.append(result_data)
                
            except Exception as e:
                results.append({'name': name, 'verdict': 'SKIP', 'reason': str(e)[:50]})
                gc.collect()
        
        gc.collect()
        return jsonify({
            'results': results,
            'locks': locks,
            'lock_count': len(locks),
            'injuries_checked': len(injured),
            'injury_source': injury_source,
            'source': 'nba_api (GitHub: swar/nba_api)',
            'injury_auto_refresh': f'Every {INJURY_UPDATE_INTERVAL}s'
        })
    except Exception as e:
        gc.collect()
        return jsonify({'error': str(e)}), 500

# ═══════════════════════════════════════════════════════════════
# START SERVER WITH AUTO-UPDATES
# ═══════════════════════════════════════════════════════════════

# Start auto-updates when module is loaded (works with gunicorn)
start_auto_updates()

if __name__ == '__main__':
    print("\n" + "═" * 60)
    print("🏀 NBA PROPS API - GITHUB APIS ONLY")
    print("═" * 60)
    print("Stats: nba_api (GitHub: swar/nba_api)")
    print("Injuries: ESPN API (Live, No Java Required)")
    print("═" * 60)
    
    # Run Flask server (for local dev)
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)), threaded=True)
