import os
import uuid
import math
from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from supabase import create_client
from dotenv import load_dotenv
from werkzeug.security import generate_password_hash, check_password_hash

load_dotenv()

app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.environ.get('SECRET_KEY', 'change-me-in-production'),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    SESSION_COOKIE_SECURE=os.environ.get('SESSION_COOKIE_SECURE', '0') == '1',
)

SUPABASE_URL = os.environ.get('SUPABASE_URL', '')
SUPABASE_KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY', os.environ.get('SUPABASE_KEY', ''))
if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError('SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required')
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

TEAMS = ['RCB', 'CSK', 'MI', 'KKR', 'SRH', 'RR', 'DC', 'PBKS', 'GT', 'LSG']
ROLES = ['Batsman', 'Bowler', 'All-Rounder', 'Wicket Keeper']
PRICE_OPTIONS = [
    (3000000, '30 L'), (4000000, '40 L'), (5000000, '50 L'), (6000000, '60 L'),
    (7000000, '70 L'), (8000000, '80 L'), (9000000, '90 L'),
    (10000000, '1 Cr'), (12500000, '1.25 Cr'), (15000000, '1.5 Cr'), (17500000, '1.75 Cr'),
]
for crore in [2, 2.25, 2.5, 2.75, 3, 3.25, 3.5, 3.75, 4, 4.25, 4.5, 4.75, 5, 5.5, 6, 6.5, 7, 7.5, 8, 8.5, 9, 9.5, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 30, 35, 40, 45, 50, 55, 60, 65, 70, 75, 80, 85, 90, 95, 100]:
    PRICE_OPTIONS.append((int(crore * 10000000), f'{crore:g} Cr'))


def fmt_money(v):
    v = int(v or 0)
    if v >= 10000000:
        cr = v / 10000000
        return f'₹{cr:g} Cr'
    lakh = v / 100000
    return f'₹{lakh:.2f} L'
app.jinja_env.filters['money'] = fmt_money


def participant_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if session.get('role') != 'participant' or not session.get('team_id'):
            flash('Please login as a participant.', 'warning')
            return redirect(url_for('login'))
        return fn(*args, **kwargs)
    return wrapper


def admin_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if session.get('role') != 'admin':
            flash('Admin access required.', 'danger')
            return redirect(url_for('home'))
        return fn(*args, **kwargs)
    return wrapper


def safe_error(e):
    msg = str(e)
    if 'TEAM_OR_USERNAME_ALREADY_REGISTERED' in msg or 'already registered' in msg.lower() or 'duplicate' in msg.lower():
        return 'TEAM_ALREADY_REGISTERED'
    if 'LOW_BALANCE' in msg:
        return 'LOW_BALANCE'
    if 'SQUAD_FULL' in msg:
        return 'SQUAD_FULL'
    if 'ALREADY_SOLD' in msg:
        return 'ALREADY_SOLD'
    if 'PLAYER_NOT_FOUND' in msg:
        return 'PLAYER_NOT_FOUND'
    if 'INVALID_PRICE' in msg:
        return 'INVALID_PRICE'
    if 'INVALID_REGISTRATION' in msg:
        return 'INVALID_REGISTRATION'
    return msg[:220]


def get_admin_user_id():
    admin_username = os.environ.get('ADMIN_USERNAME', 'admin')
    try:
        res = supabase.table('users').select('id').eq('username', admin_username).eq('role', 'admin').limit(1).execute()
        if res.data:
            return res.data[0]['id']
        admin_pw = os.environ.get('ADMIN_PASSWORD', '')
        pw_hash = generate_password_hash(admin_pw) if admin_pw else generate_password_hash('admin')
        admin_id = str(uuid.uuid4())
        insert_res = supabase.table('users').insert({
            'id': admin_id,
            'username': admin_username,
            'password_hash': pw_hash,
            'role': 'admin',
            'active': True
        }).execute()
        if insert_res.data:
            return insert_res.data[0]['id']
        return admin_id
    except Exception:
        try:
            res = supabase.table('users').select('id').eq('role', 'admin').limit(1).execute()
            if res.data:
                return res.data[0]['id']
        except Exception:
            pass
        return str(uuid.uuid4())


@app.get('/')
def home():
    try:
        players = supabase.table('players').select('id,name,role,nationality,base_price,photo_url,notes,is_available,auction_order').order('auction_order', desc=False).execute().data or []
        state_res = supabase.table('auction_state').select('is_live,current_player_id,updated_at').eq('id', True).single().execute()
        state = state_res.data if state_res else {'is_live': False, 'current_player_id': None}
    except Exception:
        players, state = [], {'is_live': False, 'current_player_id': None}
    return render_template('index.html', players=players, state=state, teams=TEAMS, roles=ROLES)


@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        team = request.form.get('team', '').strip().upper()
        name = request.form.get('name', '').strip()
        college = request.form.get('college', '').strip()
        password = request.form.get('password', '')

        if team not in TEAMS:
            flash('Please select a valid IPL team.', 'danger')
            return render_template('register.html', teams=TEAMS)
        if len(name) < 2:
            flash('Participant name must be at least 2 characters.', 'danger')
            return render_template('register.html', teams=TEAMS)
        if len(college) < 2:
            flash('College / institution must be at least 2 characters.', 'danger')
            return render_template('register.html', teams=TEAMS)
        if len(password) < 6:
            flash('Password must be at least 6 characters.', 'danger')
            return render_template('register.html', teams=TEAMS)

        # Hash password server-side using werkzeug.security
        password_hash = generate_password_hash(password)
        try:
            # PostgreSQL function: register_participant(p_team team_code, p_username text, p_password_hash text)
            result = supabase.rpc('register_participant', {
                'p_team': team,
                'p_username': team,
                'p_password_hash': password_hash
            }).execute()

            data = result.data
            if not data:
                raise Exception('Registration failed')

            user_id = data.get('user_id') if isinstance(data, dict) else None
            # Update name and college if columns exist in users table
            if user_id and (name or college):
                try:
                    supabase.table('users').update({
                        'name': name,
                        'college': college
                    }).eq('id', user_id).execute()
                except Exception:
                    pass

            flash(f'{team} registered successfully. You can now login.', 'success')
            return redirect(url_for('login'))
        except Exception as e:
            err = safe_error(e)
            if err == 'TEAM_ALREADY_REGISTERED':
                flash(f'Team {team} is already registered.', 'danger')
            elif err == 'INVALID_REGISTRATION':
                flash('Invalid registration details provided.', 'danger')
            else:
                flash(f'Registration error: {err}', 'danger')
    return render_template('register.html', teams=TEAMS)


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        team = request.form.get('team', '').strip().upper()
        password = request.form.get('password', '')

        if team not in TEAMS or not password:
            flash('Invalid team or password.', 'danger')
            return render_template('login.html', teams=TEAMS)

        try:
            team_res = supabase.table('teams').select('id,code,name').eq('code', team).limit(1).execute()
            if not team_res.data:
                flash('Invalid team or password.', 'danger')
                return render_template('login.html', teams=TEAMS)
            team_data = team_res.data[0]
            team_id = team_data['id']

            user_res = supabase.table('users').select('id,team_id,username,password_hash,active,role').eq('team_id', team_id).eq('role', 'participant').limit(1).execute()
            if not user_res.data:
                flash('Invalid team or password.', 'danger')
                return render_template('login.html', teams=TEAMS)
            user_data = user_res.data[0]

            if not user_data.get('active', True):
                flash('This team account is currently deactivated.', 'danger')
                return render_template('login.html', teams=TEAMS)

            stored_hash = user_data.get('password_hash', '')
            if not check_password_hash(stored_hash, password):
                flash('Invalid team or password.', 'danger')
                return render_template('login.html', teams=TEAMS)

            session.clear()
            session['role'] = 'participant'
            session['user_id'] = user_data['id']
            session['team_id'] = team_id
            session['team'] = team
            return redirect(url_for('dashboard'))
        except Exception:
            flash('Invalid team or password.', 'danger')
    return render_template('login.html', teams=TEAMS)


@app.get('/logout')
def logout():
    session.clear()
    return redirect(url_for('home'))


@app.get('/dashboard')
@participant_required
def dashboard():
    team_id = session.get('team_id')
    team_code = session.get('team')
    try:
        # 1. Fetch own team details (only their own wallet & squad size)
        team_res = supabase.table('teams').select('id,code,name,wallet_balance,max_squad_size').eq('id', team_id).single().execute()
        team_data = team_res.data
        if not team_data:
            flash('Team not found.', 'danger')
            return render_template('error.html', code=404, message='Team not found.'), 404

        # 2. Fetch purchases for this team only
        purchases_res = supabase.table('purchases').select('id,player_id,sold_price,sold_at').eq('team_id', team_id).order('sold_at', desc=False).execute()
        purchases = purchases_res.data or []

        # Enrich purchased players with player details
        purchased_players = []
        if purchases:
            player_ids = [p['player_id'] for p in purchases if p.get('player_id')]
            if player_ids:
                players_map = {}
                try:
                    players_res = supabase.table('players').select('id,name,role,nationality').in_('id', player_ids).execute()
                    players_map = {p['id']: p for p in (players_res.data or [])}
                except Exception:
                    pass
                for p in purchases:
                    pl = players_map.get(p['player_id'], {})
                    purchased_players.append({
                        'player_id': p['player_id'],
                        'player_name': pl.get('name', 'Unknown Player'),
                        'role': pl.get('role', ''),
                        'nationality': pl.get('nationality', ''),
                        'sold_price': p['sold_price'],
                        'sold_at': p['sold_at']
                    })

        # 3. Fetch public auction state and live player info
        state_res = supabase.table('auction_state').select('id,is_live,current_player_id,updated_at').eq('id', True).single().execute()
        state = state_res.data or {'is_live': False, 'current_player_id': None}

        live_player = None
        if state.get('is_live') and state.get('current_player_id'):
            pl_res = supabase.table('players').select('id,name,role,nationality,base_price,photo_url,notes').eq('id', state['current_player_id']).limit(1).execute()
            if pl_res.data:
                live_player = pl_res.data[0]

        dashboard_data = {
            'team': team_code,
            'wallet_balance': team_data['wallet_balance'],
            'squad_count': len(purchased_players),
            'max_squad_size': team_data.get('max_squad_size', 12),
            'players': purchased_players,
            'live_player': live_player,
            'is_live': state.get('is_live', False)
        }
        return render_template('dashboard.html', data=dashboard_data, team=team_code)
    except Exception:
        flash('Dashboard is temporarily unavailable.', 'danger')
        return render_template('error.html', code=500, message='Unable to load your dashboard.'), 500


@app.post('/admin/login')
def admin_login():
    username = request.form.get('username', '')
    password = request.form.get('password', '')
    expected_username = os.environ.get('ADMIN_USERNAME', 'admin')
    expected_password = os.environ.get('ADMIN_PASSWORD', '')
    if username == expected_username and password == expected_password:
        admin_id = get_admin_user_id()
        session.clear()
        session['role'] = 'admin'
        session['user_id'] = admin_id
        session['admin_username'] = username
        return redirect(url_for('admin_dashboard'))
    flash('Invalid admin credentials.', 'danger')
    return redirect(url_for('home'))


@app.get('/admin/dashboard')
@admin_required
def admin_dashboard():
    try:
        players = supabase.table('players').select('*').order('auction_order', desc=False).execute().data or []
        teams = supabase.table('teams').select('*').order('code').execute().data or []
        participants = supabase.table('users').select('id,username,team_id,role,active,created_at').eq('role', 'participant').execute().data or []
        state_res = supabase.table('auction_state').select('*').eq('id', True).single().execute()
        state = state_res.data if state_res else {'id': True, 'is_live': False, 'current_player_id': None}
    except Exception:
        players, teams, participants, state = [], [], [], {'id': True, 'is_live': False, 'current_player_id': None}
    return render_template('admin.html', players=players, teams=teams, participants=participants, state=state, roles=ROLES, prices=PRICE_OPTIONS)


@app.post('/admin/player/add')
@admin_required
def add_player():
    try:
        name = request.form.get('name', '').strip()
        role = request.form.get('role', '').strip()
        nationality = request.form.get('nationality', '').strip() or None

        if not name:
            raise ValueError('Player name is required.')
        if role not in ROLES:
            raise ValueError('Invalid player role.')

        raw_price = (request.form.get('base_price') or request.form.get('base_price_crore') or '').strip()
        if not raw_price:
            raise ValueError('Base price is required.')

        try:
            crore_val = float(raw_price)
        except (ValueError, TypeError):
            raise ValueError('Base price must be a valid number.')

        if math.isnan(crore_val) or math.isinf(crore_val):
            raise ValueError('Invalid base price value.')
        if crore_val <= 0:
            raise ValueError('Base price must be greater than 0 Cr.')
        if crore_val > 100:
            raise ValueError('Base price cannot exceed 100 Cr.')

        try:
            base_price_rupees = int(round(Decimal(raw_price) * Decimal(10_000_000)))
        except (InvalidOperation, ValueError):
            base_price_rupees = int(round(crore_val * 10_000_000))

        if base_price_rupees <= 0:
            raise ValueError('Base price in rupees must be greater than 0.')

        # Automatically assign MAX(auction_order) + 1, or 1 if no players
        order_res = supabase.table('players').select('auction_order').order('auction_order', desc=True).limit(1).execute()
        if order_res.data and len(order_res.data) > 0 and order_res.data[0].get('auction_order') is not None:
            next_order = int(order_res.data[0]['auction_order']) + 1
        else:
            next_order = 1

        payload = {
            'name': name,
            'role': role,
            'nationality': nationality,
            'base_price': base_price_rupees,
            'photo_url': None,
            'notes': None,
            'auction_order': next_order,
            'is_available': True
        }
        supabase.table('players').insert(payload).execute()
        flash('Player added.', 'success')
    except Exception as e:
        flash(safe_error(e), 'danger')
    return redirect(url_for('admin_dashboard'))


@app.post('/admin/player/<player_id>/set-live')
@admin_required
def set_live(player_id):
    try:
        p_res = supabase.table('players').select('id,name,is_available').eq('id', player_id).single().execute()
        if not p_res.data:
            flash('Player not found.', 'danger')
            return redirect(url_for('admin_dashboard'))
        if not p_res.data.get('is_available', False):
            flash('Cannot set sold player as live.', 'warning')
            return redirect(url_for('admin_dashboard'))

        now_ts = datetime.now(timezone.utc).isoformat()
        supabase.table('auction_state').update({
            'current_player_id': player_id,
            'is_live': True,
            'updated_at': now_ts
        }).eq('id', True).execute()
        flash(f"{p_res.data.get('name', 'Player')} is now LIVE.", 'success')
    except Exception as e:
        flash(f'Failed to set live player: {safe_error(e)}', 'danger')
    return redirect(url_for('admin_dashboard'))


@app.post('/admin/player/<player_id>/sold')
@admin_required
def sold(player_id):
    team = request.form.get('team', '').strip().upper()
    price = request.form.get('price', '')
    custom = request.form.get('custom_price', '').strip()
    try:
        if team not in TEAMS:
            raise ValueError('Select a valid team.')
        amount = int(custom) if custom else int(price)
        if amount <= 0 or amount > 1000000000:
            raise ValueError('Invalid sale price.')

        admin_id = session.get('user_id')
        try:
            uuid.UUID(str(admin_id))
        except (ValueError, TypeError):
            admin_id = get_admin_user_id()
            session['user_id'] = admin_id

        request_id = str(uuid.uuid4())

        # PostgreSQL function:
        # mark_player_sold(p_player_id uuid, p_team team_code, p_sold_price bigint, p_recorded_by uuid, p_request_id uuid)
        result = supabase.rpc('mark_player_sold', {
            'p_player_id': player_id,
            'p_team': team,
            'p_sold_price': amount,
            'p_recorded_by': admin_id,
            'p_request_id': request_id
        }).execute()

        # Clear live player if this player was live
        try:
            state_res = supabase.table('auction_state').select('current_player_id').eq('id', True).single().execute()
            if state_res.data and state_res.data.get('current_player_id') == player_id:
                now_ts = datetime.now(timezone.utc).isoformat()
                supabase.table('auction_state').update({
                    'current_player_id': None,
                    'is_live': False,
                    'updated_at': now_ts
                }).eq('id', True).execute()
        except Exception:
            pass

        pl_name = 'Player'
        try:
            p_info = supabase.table('players').select('name').eq('id', player_id).single().execute()
            if p_info.data:
                pl_name = p_info.data.get('name', 'Player')
        except Exception:
            pass

        flash(f"SOLD — {pl_name} to {team} for {fmt_money(amount)}", 'success')
    except Exception as e:
        err = safe_error(e)
        if err == 'LOW_BALANCE':
            flash(f'LOW BALANCE — {team} does not have enough wallet funds for {fmt_money(amount)}.', 'danger')
        elif err == 'SQUAD_FULL':
            flash(f'SQUAD FULL — {team} already has the maximum 12 players.', 'danger')
        elif err == 'ALREADY_SOLD':
            flash('This player has already been sold.', 'danger')
        elif err == 'PLAYER_NOT_FOUND':
            flash('Player not found in database.', 'danger')
        elif err == 'INVALID_PRICE':
            flash('Invalid sale price.', 'danger')
        else:
            flash(f'Error recording sale: {err}', 'danger')
    return redirect(url_for('admin_dashboard'))


@app.post('/admin/player/<player_id>/delete')
@admin_required
def delete_player(player_id):
    try:
        supabase.table('players').delete().eq('id', player_id).eq('is_available', True).execute()
        flash('Player deleted.', 'success')
    except Exception as e:
        flash(safe_error(e), 'danger')
    return redirect(url_for('admin_dashboard'))


@app.post('/admin/live/stop')
@admin_required
def stop_live():
    try:
        now_ts = datetime.now(timezone.utc).isoformat()
        supabase.table('auction_state').update({
            'current_player_id': None,
            'is_live': False,
            'updated_at': now_ts
        }).eq('id', True).execute()
        flash('Live display stopped.', 'success')
    except Exception as e:
        flash(f'Failed to stop live display: {safe_error(e)}', 'danger')
    return redirect(url_for('admin_dashboard'))


@app.get('/health')
def health():
    try:
        supabase.table('teams').select('id').limit(1).execute()
        return jsonify(status='ok'), 200
    except Exception:
        return jsonify(status='error'), 503


@app.errorhandler(403)
def forbidden(e):
    return render_template('error.html', code=403, message='You are not authorized to access this page.'), 403


@app.errorhandler(404)
def not_found(e):
    return render_template('error.html', code=404, message='The page you requested was not found.'), 404


@app.errorhandler(500)
def server_error(e):
    return render_template('error.html', code=500, message='Something went wrong. Please try again.'), 500


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)), debug=False)
