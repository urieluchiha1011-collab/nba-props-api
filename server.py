import os
from flask import Flask, jsonify, request
from flask_cors import CORS
from nba_api.stats.endpoints import playergamelog, teamgamelog
from nba_api.stats.static import players, teams
from nba_api.live.nba.endpoints import scoreboard
from datetime import datetime
import time
import requests

app = Flask(__name__)
CORS(app)

def find_player(name):
    for p in players.get_players():
        if name.lower() in p['full_name'].lower():
            return p['id']
    return None

def find_team(abbr):
    abbr = abbr.upper()
    for t in teams.get_teams():
        if t['abbreviation'] == abbr:
            return t
    return None

@app.route('/api/health')
def health():
    return jsonify({'status': 'ok', 'time': datetime.now().isoformat()})

@app.route('/api/injuries')
def get_injuries():
    try:
        url = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/injuries"
        resp = requests.get(url, timeout=10)
        data = resp.json()
        
        teams_data = {}
        injured_players = []
        
        for team in data.get('injuries', []):
            team_abbr = team.get('team', {}).get('abbreviation', 'UNK')
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
        
        return jsonify({
            'updated': datetime.now().isoformat(),
            'source': 'ESPN NBA Injuries (Live)',
            'teams': teams_data,
            'injured_players': injured_players,
            'total': len(injured_players)
        })
    except Exception as e:
        return jsonify({'error': str(e), 'teams': {}, 'injured_players': []}), 500

@app.route('/api/games/today')
def get_games():
    try:
        board = scoreboard.ScoreBoard()
        data = board.get_dict()
        games = []
        for g in data.get('scoreboard', {}).get('games', []):
            games.append({
                'game_id': g.get('gameId'),
                'home_team': g.get('homeTeam', {}).get('teamTricode'),
                'away_team': g.get('awayTeam', {}).get('teamTricode'),
                'home_score': g.get('homeTeam', {}).get('score'),
                'away_score': g.get('awayTeam', {}).get('score'),
                'status': g.get('gameStatusText'),
                'start_time': g.get('gameTimeUTC')
            })
        return jsonify({'date': datetime.now().strftime('%Y-%m-%d'), 'games': games, 'count': len(games)})
    except Exception as e:
        return jsonify({'error': str(e), 'games': []}), 500

@app.route('/api/teams')
def get_teams():
    all_teams = teams.get_teams()
    return jsonify({'teams': [{'id': t['id'], 'abbreviation': t['abbreviation'], 'full_name': t['full_name'], 'city': t['city']} for t in all_teams]})

@app.route('/api/team/<abbr>')
def get_team_stats(abbr):
    try:
        team = find_team(abbr)
        if not team:
            return jsonify({'error': 'Team not found'}), 404
        
        time.sleep(0.6)
        log = teamgamelog.TeamGameLog(team_id=team['id'], season='2024-25')
        df = log.get_data_frames()[0]
        
        if df.empty:
            return jsonify({'error': 'No games found'}), 404
        
        wins = len(df[df['WL'] == 'W'])
        losses = len(df[df['WL'] == 'L'])
        
        last10 = df.head(10)
        l10_wins = len(last10[last10['WL'] == 'W'])
        
        ppg = df['PTS'].mean()
        opp_ppg = df['PTS'].mean() - df['PLUS_MINUS'].mean()
        
        recent = []
        for _, row in df.head(10).iterrows():
            recent.append({
                'date': row['GAME_DATE'],
                'matchup': row['MATCHUP'],
                'result': row['WL'],
                'pts': int(row['PTS']),
                'plus_minus': int(row['PLUS_MINUS'])
            })
        
        return jsonify({
            'team': team,
            'record': {'wins': wins, 'losses': losses},
            'last10': str(l10_wins) + '-' + str(10-l10_wins),
            'ppg': round(ppg, 1),
            'opp_ppg': round(opp_ppg, 1),
            'diff': round(ppg - opp_ppg, 1),
            'games': len(df),
            'recent': recent
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/player/<name>')
def get_player(name):
    try:
        pid = find_player(name)
        if not pid:
            return jsonify({'error': 'Player not found'}), 404
        time.sleep(0.6)
        log = playergamelog.PlayerGameLog(player_id=pid, season='2024-25')
        df = log.get_data_frames()[0]
        if df.empty:
            return jsonify({'error': 'No games found'}), 404
        return jsonify({
            'name': name,
            'games': len(df),
            'averages': {
                'pts': round(df['PTS'].mean(), 1),
                'reb': round(df['REB'].mean(), 1),
                'ast': round(df['AST'].mean(), 1),
                'fg3m': round(df['FG3M'].mean(), 1),
                'stl': round(df['STL'].mean(), 1),
                'blk': round(df['BLK'].mean(), 1)
            }
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/analyze', methods=['POST'])
def analyze():
    try:
        props = request.get_json().get('props', [])
        results = []
        locks = []
        
        try:
            inj_resp = get_injuries()
            inj_data = inj_resp.get_json()
            injured = inj_data.get('injured_players', [])
        except:
            injured = []
        
        for p in props:
            name = p.get('name', '')
            line = float(p.get('line', 0))
            stat = p.get('stat', 'pts')
            
            if name.lower() in injured:
                results.append({'name': name, 'verdict': 'SKIP', 'reason': 'INJURED'})
                continue
            
            pid = find_player(name)
            if not pid:
                results.append({'name': name, 'verdict': 'SKIP', 'reason': 'Not found'})
                continue
            
            time.sleep(0.6)
            df = playergamelog.PlayerGameLog(player_id=pid, season='2024-25').get_data_frames()[0]
            
            if df.empty:
                results.append({'name': name, 'verdict': 'SKIP', 'reason': 'No games'})
                continue
            
            stat_map = {
                'pts': 'PTS', 'points': 'PTS',
                'reb': 'REB', 'rebounds': 'REB',
                'ast': 'AST', 'assists': 'AST',
                'fg3m': 'FG3M', '3pm': 'FG3M',
                'stl': 'STL', 'steals': 'STL',
                'blk': 'BLK', 'blocks': 'BLK'
            }
            col = stat_map.get(stat.lower(), 'PTS')
            avg = df[col].mean()
            edge = avg - line
            games = len(df)
            
            if abs(edge) >= 6 and games >= 18:
                direction = 'OVER' if edge > 0 else 'UNDER'
                locks.append({
                    'name': name,
                    'line': line,
                    'stat': stat,
                    'direction': direction,
                    'edge': round(edge, 1),
                    'avg': round(avg, 1),
                    'games': games
                })
                results.append({
                    'name': name,
                    'avg': round(avg, 1),
                    'edge': round(edge, 1),
                    'games': games,
                    'verdict': '🔒 LOCK ' + direction
                })
            else:
                results.append({
                    'name': name,
                    'avg': round(avg, 1),
                    'edge': round(edge, 1),
                    'games': games,
                    'verdict': 'SKIP'
                })
        
        return jsonify({
            'results': results,
            'locks': locks,
            'lock_count': len(locks),
            'injuries_checked': len(injured)
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
