import os
from flask import Flask, jsonify, request
from flask_cors import CORS
from nba_api.stats.endpoints import playergamelog, teamgamelog, leaguestandings
from nba_api.stats.static import players, teams
from nba_api.live.nba.endpoints import scoreboard
from datetime import datetime
import time
import requests

app = Flask(__name__)
CORS(app)

# Get all NBA teams
NBA_TEAMS = {t['abbreviation']: t for t in teams.get_teams()}

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
                'game_id': g.get('
