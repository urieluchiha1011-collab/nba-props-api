import os
from flask import Flask, jsonify, request
from flask_cors import CORS
from nba_api.stats.endpoints import playergamelog, commonplayerinfo
from nba_api.stats.static import players
from nba_api.live.nba.endpoints import scoreboard
from datetime import datetime
import time

app = Flask(__name__)
CORS(app)

def find_player(name):
    for p in players.get_players():
        if name.lower() in p['full_name'].lower():
            return p['id']
    return None

@app.route('/api/health')
def health():
    return jsonify({'status': 'ok', 'time': datetime.now().isoformat()})

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
                'fg3m': round(df['FG3M'].mean(), 1)
            }
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/analyze', methods=['POST'])
def analyze():
    try:
        props = request.get_json().get('props', [])
        results, locks = [], []
        for p in props:
            name, line, stat = p.get('name',''), float(p.get('line',0)), p.get('stat','pts')
            pid = find_player(name)
            if not pid:
                results.append({'name': name, 'verdict': 'SKIP', 'reason': 'Not found'})
                continue
            time.sleep(0.6)
            df = playergamelog.PlayerGameLog(player_id=pid, season='2024-25').get_data_frames()[0]
            if df.empty:
                results.append({'name': name, 'verdict': 'SKIP', 'reason': 'No games'})
                continue
            stat_map = {'pts':'PTS','points':'PTS','reb':'REB','rebounds':'REB','ast':'AST','assists':'AST','fg3m':'FG3M','3pm':'FG3M'}
            col = stat_map.get(stat.lower(), 'PTS')
            avg = df[col].mean()
            edge = avg - line
            games = len(df)
            if abs(edge) >= 6 and games >= 18:
                direction = 'OVER' if edge > 0 else 'UNDER'
                locks.append({'name': name, 'line': line, 'stat': stat, 'direction': direction, 'edge': round(edge,1), 'avg': round(avg,1), 'games': games})
                results.append({'name': name, 'avg': round(avg,1), 'edge': round(edge,1), 'games': games, 'verdict': f'🔒 LOCK {direction}'})
            else:
                results.append({'name': name, 'avg': round(avg,1), 'edge': round(edge,1), 'games': games, 'verdict': 'SKIP'})
        return jsonify({'results': results, 'locks': locks, 'lock_count': len(locks)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
@app.route('/api/games/today')
def get_games():
    try:
        from nba_api.live.nba.endpoints import scoreboard
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
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
