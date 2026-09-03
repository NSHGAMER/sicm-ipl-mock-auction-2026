import os
import io
import csv
import uuid
import math
import openpyxl
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


def normalize_role(raw_role):
    if not raw_role:
        return 'Batsman'
    r = str(raw_role).strip().lower().replace('-', ' ').replace('_', ' ')
    if 'wicket' in r or 'keeper' in r or 'wk' in r:
        return 'Wicket Keeper'
    if 'all' in r and 'round' in r:
        return 'All-Rounder'
    if 'spin' in r or 'fast' in r or 'medium' in r or 'bowl' in r or 'arm' in r or 'break' in r or 'orthodox' in r or 'seam' in r or 'pacer' in r:
        return 'Bowler'
    if 'bat' in r or 'batter' in r or 'batsman' in r or 'finisher' in r or 'opener' in r:
        return 'Batsman'
    return 'Batsman'


def parse_base_price(raw_val):
    if raw_val is None or str(raw_val).strip() == '':
        return 20000000
    if isinstance(raw_val, (int, float)):
        val = float(raw_val)
        if val <= 0:
            return 20000000
        if val >= 100000:
            return int(round(val))
        return int(round(Decimal(str(raw_val)) * Decimal(10_000_000)))

    s = str(raw_val).strip().replace('\u20b9', '').replace('₹', '').replace('Rs', '').replace('rs', '').replace(',', '').strip().lower()
    if not s:
        return 20000000

    if 'cr' in s:
        num = s.replace('crores', '').replace('crore', '').replace('cr', '').strip()
        try:
            return int(round(Decimal(num) * Decimal(10_000_000)))
        except Exception:
            return 20000000
    elif 'l' in s or 'lakh' in s or 'lac' in s:
        num = s.replace('lakhs', '').replace('lakh', '').replace('lacs', '').replace('lac', '').replace('l', '').strip()
        try:
            return int(round(Decimal(num) * Decimal(100_000)))
        except Exception:
            return 20000000
    else:
        try:
            val = float(s)
            if val <= 0:
                return 20000000
            if val >= 100000:
                return int(round(val))
            return int(round(Decimal(s) * Decimal(10_000_000)))
        except Exception:
            return 20000000


def parse_reference_file(file_storage):
    filename = file_storage.filename.lower()
    raw_rows = []
    if filename.endswith('.csv'):
        content = file_storage.read().decode('utf-8-sig', errors='replace')
        reader = csv.reader(io.StringIO(content))
        for r in reader:
            if any(cell.strip() for cell in r if isinstance(cell, str)):
                raw_rows.append([str(c).strip() for c in r])
    elif filename.endswith('.xlsx'):
        wb = openpyxl.load_workbook(io.BytesIO(file_storage.read()), data_only=True)
        sheet = wb.active
        for row in sheet.iter_rows(values_only=True):
            if any(c is not None and str(c).strip() != '' for c in row):
                raw_rows.append([str(c).strip() if c is not None else '' for c in row])

    if not raw_rows:
        raise ValueError('Uploaded file is empty.')

    parsed_players = []
    current_col_map = {}

    for row_idx, r in enumerate(raw_rows, start=1):
        if not r or not any(cell for cell in r):
            continue

        first_cell = r[0].strip().lower() if len(r) > 0 else ''
        potential_map = {}
        for idx, cell in enumerate(r):
            h = str(cell).strip().lower().replace('_', ' ').replace('-', ' ')
            if 'name' in h or 'player' in h:
                potential_map['name'] = idx
            elif 'role' in h or 'profile' in h or 'style' in h or 'category' in h or 'pos' in h:
                potential_map['role'] = idx
            elif 'country' in h or 'nationality' in h:
                potential_map['nationality'] = idx
            elif 'team' in h or 'franchise' in h or 'ipl' in h:
                potential_map['team'] = idx
            elif 'price' in h or 'base' in h:
                potential_map['base_price'] = idx

        if 'name' in potential_map and ('role' in potential_map or 'base_price' in potential_map):
            current_col_map = potential_map
            continue

        if first_cell in ('s.no', 's. no', 'sno', 'sl.no', 'sl no', 'player name', 'name', 's #'):
            if 'name' in potential_map:
                current_col_map = potential_map
            continue

        if not current_col_map:
            if len(r) >= 4:
                current_col_map = {'name': 1 if len(r) > 1 and r[0].isdigit() else 0, 'role': 3 if len(r) > 3 and r[0].isdigit() else 1, 'team': 2, 'base_price': len(r) - 1}
            elif len(r) >= 2:
                current_col_map = {'name': 0, 'role': 1, 'team': 2 if len(r) > 2 else None, 'base_price': 3 if len(r) > 3 else None}

        name_idx = current_col_map.get('name')
        if name_idx is None or name_idx >= len(r):
            continue

        name = r[name_idx].strip()
        if not name or name.lower() in ('player name', 'name', 's.no', 'sno', 's. no'):
            continue

        role_idx = current_col_map.get('role')
        raw_role = r[role_idx].strip() if role_idx is not None and role_idx < len(r) else ''
        role = normalize_role(raw_role)

        team = None
        team_idx = current_col_map.get('team')
        if team_idx is not None and team_idx < len(r):
            raw_team = r[team_idx].strip().upper()
            if raw_team in TEAMS:
                team = raw_team

        price_idx = current_col_map.get('base_price')
        raw_price = r[price_idx] if price_idx is not None and price_idx < len(r) else None
        base_price = parse_base_price(raw_price)

        parsed_players.append({
            'player_name': name,
            'role': role,
            'team': team,
            'base_price': base_price,
            'auction_order': len(parsed_players) + 1
        })

    if not parsed_players:
        raise ValueError('Player Name and Role columns are required.')

    return parsed_players


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
        players = supabase.table('auction_reference_players').select('id,player_name,role,team,base_price,auction_order').order('auction_order', desc=False).execute().data or []
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
            else:
                ref_res = supabase.table('auction_reference_players').select('id,player_name,role,base_price').eq('id', state['current_player_id']).limit(1).execute()
                if ref_res.data:
                    live_player = {
                        'id': ref_res.data[0]['id'],
                        'name': ref_res.data[0]['player_name'],
                        'role': ref_res.data[0]['role'],
                        'nationality': None,
                        'base_price': ref_res.data[0]['base_price']
                    }

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
        ref_players = supabase.table('auction_reference_players').select('*').order('auction_order', desc=False).execute().data or []
        purchases = supabase.table('purchases').select('id,player_id,team_id,sold_price,sold_at').order('sold_at', desc=True).execute().data or []

        # Enrich purchases with team code and player name
        teams_map = {t['id']: t for t in teams}
        player_ids = [p['player_id'] for p in purchases if p.get('player_id')]
        players_map = {}
        if player_ids:
            try:
                p_res = supabase.table('players').select('id,name,role').in_('id', player_ids).execute()
                players_map = {pl['id']: pl for pl in (p_res.data or [])}
            except Exception:
                pass

        enriched_purchases = []
        for p in purchases:
            t_info = teams_map.get(p.get('team_id'), {})
            pl_info = players_map.get(p.get('player_id'), {})
            enriched_purchases.append({
                'id': p['id'],
                'player_name': pl_info.get('name', 'Unknown Player'),
                'role': pl_info.get('role', ''),
                'team_code': t_info.get('code', 'Unknown'),
                'sold_price': p.get('sold_price', 0),
                'sold_at': p.get('sold_at')
            })

        participants = supabase.table('users').select('id,username,team_id,role,active,created_at').eq('role', 'participant').execute().data or []
        state_res = supabase.table('auction_state').select('*').eq('id', True).single().execute()
        state = state_res.data if state_res else {'id': True, 'is_live': False, 'current_player_id': None}
    except Exception:
        players, teams, ref_players, enriched_purchases, participants, state = [], [], [], [], [], {'id': True, 'is_live': False, 'current_player_id': None}
    return render_template('admin.html', players=players, teams=teams, ref_players=ref_players, purchases=enriched_purchases, participants=participants, state=state, roles=ROLES, prices=PRICE_OPTIONS)


@app.post('/admin/reference/upload')
@admin_required
def upload_reference_list():
    if 'file' not in request.files:
        flash('No file selected.', 'danger')
        return redirect(url_for('admin_dashboard'))

    file = request.files['file']
    if not file or not file.filename:
        flash('No file selected.', 'danger')
        return redirect(url_for('admin_dashboard'))

    filename = file.filename.lower()
    if not (filename.endswith('.xlsx') or filename.endswith('.csv')):
        flash('Invalid file format. Please upload an Excel (.xlsx) or CSV (.csv) file.', 'danger')
        return redirect(url_for('admin_dashboard'))

    try:
        parsed_players = parse_reference_file(file)

        # Only replace reference list after successful parsing
        supabase.table('auction_reference_players').delete().neq('id', '00000000-0000-0000-0000-000000000000').execute()

        batch_size = 50
        for i in range(0, len(parsed_players), batch_size):
            supabase.table('auction_reference_players').insert(parsed_players[i:i+batch_size]).execute()

        flash(f'Reference list updated with {len(parsed_players)} players.', 'success')
    except Exception as e:
        flash(f'Reference upload failed: {str(e)}', 'danger')
    return redirect(url_for('admin_dashboard'))


@app.post('/admin/sale/manual')
@admin_required
def manual_sale():
    name = request.form.get('name', '').strip()
    role = request.form.get('role', '').strip()
    team = request.form.get('team', '').strip().upper()
    raw_sold_price = (request.form.get('sold_price') or '').strip()

    try:
        if not name:
            raise ValueError('Player name is required.')
        if role not in ROLES:
            raise ValueError('Invalid player role.')
        if team not in TEAMS:
            raise ValueError('Please select a valid purchasing team.')
        if not raw_sold_price:
            raise ValueError('Sold price is required.')

        try:
            crore_val = float(raw_sold_price)
        except (ValueError, TypeError):
            raise ValueError('Sold price must be a valid number.')

        if math.isnan(crore_val) or math.isinf(crore_val) or crore_val <= 0:
            raise ValueError('Sold price must be greater than 0 Cr.')
        if crore_val > 100:
            raise ValueError('Sold price cannot exceed 100 Cr.')

        try:
            sold_price_rupees = int(round(Decimal(raw_sold_price) * Decimal(10_000_000)))
        except (InvalidOperation, ValueError):
            sold_price_rupees = int(round(crore_val * 10_000_000))

        if sold_price_rupees <= 0:
            raise ValueError('Sold price must be greater than 0.')

        # 1. Create the actual player record in public.players
        order_res = supabase.table('players').select('auction_order').order('auction_order', desc=True).limit(1).execute()
        if order_res.data and len(order_res.data) > 0 and order_res.data[0].get('auction_order') is not None:
            next_order = int(order_res.data[0]['auction_order']) + 1
        else:
            next_order = 1

        player_payload = {
            'name': name,
            'role': role,
            'nationality': None,
            'base_price': sold_price_rupees,
            'photo_url': None,
            'notes': None,
            'auction_order': next_order,
            'is_available': True
        }
        player_res = supabase.table('players').insert(player_payload).execute()
        if not player_res.data:
            raise Exception('Failed to create player record.')
        player_id = player_res.data[0]['id']

        # 2. Call existing mark_player_sold RPC
        admin_id = session.get('user_id')
        try:
            uuid.UUID(str(admin_id))
            u_chk = supabase.table('users').select('id').eq('id', str(admin_id)).execute()
            if not u_chk.data:
                admin_id = get_admin_user_id()
                session['user_id'] = admin_id
        except Exception:
            admin_id = get_admin_user_id()
            session['user_id'] = admin_id

        request_id = str(uuid.uuid4())

        try:
            supabase.rpc('mark_player_sold', {
                'p_player_id': player_id,
                'p_team': team,
                'p_sold_price': sold_price_rupees,
                'p_recorded_by': admin_id,
                'p_request_id': request_id
            }).execute()
        except Exception as rpc_err:
            # Clean up the un-sold player record on failure
            try:
                supabase.table('players').delete().eq('id', player_id).execute()
            except Exception:
                pass
            raise rpc_err

        # Clear live player if needed
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

        flash(f"SOLD — {name} to {team} for {fmt_money(sold_price_rupees)}", 'success')
    except Exception as e:
        err = safe_error(e)
        if err == 'LOW_BALANCE':
            flash(f'LOW BALANCE — {team} does not have enough wallet funds for {fmt_money(sold_price_rupees)}.', 'danger')
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
        flash('Player added for live auction.', 'success')
    except Exception as e:
        flash(safe_error(e), 'danger')
    return redirect(url_for('admin_dashboard'))


@app.post('/admin/player/<player_id>/set-live')
@admin_required
def set_live(player_id):
    try:
        p_res = supabase.table('players').select('id,name,is_available').eq('id', player_id).execute()
        target_id = player_id
        pl_name = 'Player'
        if p_res.data:
            pl_name = p_res.data[0].get('name', 'Player')
            if not p_res.data[0].get('is_available', False):
                flash('Cannot set sold player as live.', 'warning')
                return redirect(url_for('admin_dashboard'))
        else:
            ref_res = supabase.table('auction_reference_players').select('*').eq('id', player_id).execute()
            if ref_res.data:
                ref_p = ref_res.data[0]
                pl_name = ref_p.get('player_name', 'Player')
                existing_pl = supabase.table('players').select('id,is_available').eq('name', pl_name).execute()
                if existing_pl.data and existing_pl.data[0].get('is_available'):
                    target_id = existing_pl.data[0]['id']
                else:
                    new_pl = supabase.table('players').insert({
                        'name': pl_name,
                        'role': ref_p.get('role', 'Batsman'),
                        'base_price': ref_p.get('base_price', 20000000),
                        'is_available': True
                    }).execute()
                    if new_pl.data:
                        target_id = new_pl.data[0]['id']
            else:
                flash('Player not found.', 'danger')
                return redirect(url_for('admin_dashboard'))

        now_ts = datetime.now(timezone.utc).isoformat()
        supabase.table('auction_state').update({
            'current_player_id': target_id,
            'is_live': True,
            'updated_at': now_ts
        }).eq('id', True).execute()
        flash(f"{pl_name} is now LIVE.", 'success')
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
            u_chk = supabase.table('users').select('id').eq('id', str(admin_id)).execute()
            if not u_chk.data:
                admin_id = get_admin_user_id()
                session['user_id'] = admin_id
        except Exception:
            admin_id = get_admin_user_id()
            session['user_id'] = admin_id

        request_id = str(uuid.uuid4())

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
