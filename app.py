import os, uuid
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from supabase import create_client
from dotenv import load_dotenv

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

TEAMS = ['RCB','CSK','MI','KKR','SRH','RR','DC','PBKS','GT','LSG']
ROLES = ['Batsman','Bowler','All-Rounder','Wicket Keeper']
PRICE_OPTIONS = [
    (3000000,'30 L'),(4000000,'40 L'),(5000000,'50 L'),(6000000,'60 L'),(7000000,'70 L'),(8000000,'80 L'),(9000000,'90 L'),
    (10000000,'1 Cr'),(12500000,'1.25 Cr'),(15000000,'1.5 Cr'),(17500000,'1.75 Cr'),
]
for crore in [2,2.25,2.5,2.75,3,3.25,3.5,3.75,4,4.25,4.5,4.75,5,5.5,6,6.5,7,7.5,8,8.5,9,9.5,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,30,35,40,45,50,55,60,65,70,75,80,85,90,95,100]:
    PRICE_OPTIONS.append((int(crore*10000000), f'{crore:g} Cr'))


def fmt_money(v):
    v = int(v or 0)
    if v >= 10000000:
        return f'₹{v/10000000:g} Cr'
    return f'₹{v/100000:.2f} L'
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
    return msg[:220]

@app.get('/')
def home():
    try:
        players = supabase.table('players').select('id,name,role,nationality,base_price,photo_url,notes,is_available,auction_order').order('auction_order', desc=False).execute().data or []
        state = supabase.table('auction_state').select('is_live,current_player_id,updated_at').eq('id', True).single().execute().data
    except Exception:
        players, state = [], {'is_live': False, 'current_player_id': None}
    return render_template('index.html', players=players, state=state, teams=TEAMS, roles=ROLES)

@app.route('/register', methods=['GET','POST'])
def register():
    if request.method == 'POST':
        team = request.form.get('team','').strip().upper()
        name = request.form.get('name','').strip()
        college = request.form.get('college','').strip()
        password = request.form.get('password','')
        if team not in TEAMS or len(name) < 2 or len(college) < 2 or len(password) < 6:
            flash('Enter valid details. Password must be at least 6 characters.', 'danger')
            return render_template('register.html', teams=TEAMS)
        try:
            result = supabase.rpc('register_participant', {'p_team_code': team, 'p_username': team, 'p_password': password, 'p_name': name, 'p_college': college}).execute()
            if not result.data:
                raise Exception('Registration failed')
            flash(f'{team} registered successfully. You can now login.', 'success')
            return redirect(url_for('login'))
        except Exception as e:
            msg = safe_error(e)
            if 'already registered' in msg.lower() or 'duplicate' in msg.lower():
                msg = f'Team {team} is already registered.'
            flash(msg, 'danger')
    return render_template('register.html', teams=TEAMS)

@app.route('/login', methods=['GET','POST'])
def login():
    if request.method == 'POST':
        team = request.form.get('team','').strip().upper()
        password = request.form.get('password','')
        try:
            result = supabase.rpc('login_participant', {'p_username': team, 'p_password': password}).execute()
            data = result.data[0] if result.data else None
            if not data:
                raise Exception('Invalid team or password.')
            session.clear(); session['role']='participant'; session['user_id']=data['user_id']; session['team_id']=data['team_id']; session['team']=team
            return redirect(url_for('dashboard'))
        except Exception:
            flash('Invalid team or password.', 'danger')
    return render_template('login.html', teams=TEAMS)

@app.get('/logout')
def logout():
    session.clear(); return redirect(url_for('home'))

@app.get('/dashboard')
@participant_required
def dashboard():
    try:
        data = supabase.rpc('get_team_dashboard', {'p_team_id': session['team_id']}).execute().data
        if isinstance(data, list): data = data[0] if data else {}
        return render_template('dashboard.html', data=data, team=session['team'])
    except Exception:
        flash('Dashboard is temporarily unavailable.', 'danger')
        return render_template('error.html', code=500, message='Unable to load your dashboard.'), 500

@app.post('/admin/login')
def admin_login():
    username = request.form.get('username','')
    password = request.form.get('password','')
    if username == os.environ.get('ADMIN_USERNAME','admin') and password == os.environ.get('ADMIN_PASSWORD',''):
        session.clear(); session['role']='admin'; session['user_id']='admin'; return redirect(url_for('admin_dashboard'))
    flash('Invalid admin credentials.', 'danger'); return redirect(url_for('home'))

@app.get('/admin/dashboard')
@admin_required
def admin_dashboard():
    players = supabase.table('players').select('*').order('auction_order', desc=False).execute().data or []
    teams = supabase.table('teams').select('*').order('code').execute().data or []
    participants = supabase.table('users').select('id,username,team_id,role,active,created_at').eq('role','participant').execute().data or []
    state = supabase.table('auction_state').select('*').eq('id',True).single().execute().data
    return render_template('admin.html', players=players, teams=teams, participants=participants, state=state, roles=ROLES, prices=PRICE_OPTIONS)

@app.post('/admin/player/add')
@admin_required
def add_player():
    try:
        payload = {
            'name': request.form.get('name','').strip(), 'role': request.form.get('role',''),
            'nationality': request.form.get('nationality','').strip() or None,
            'base_price': int(request.form.get('base_price','0') or 0),
            'photo_url': request.form.get('photo_url','').strip() or None,
            'notes': request.form.get('notes','').strip() or None,
            'auction_order': int(request.form.get('auction_order','0') or 0), 'is_available': True
        }
        if not payload['name'] or payload['role'] not in ROLES: raise ValueError('Invalid player details')
        supabase.table('players').insert(payload).execute(); flash('Player added.', 'success')
    except Exception as e: flash(safe_error(e), 'danger')
    return redirect(url_for('admin_dashboard'))

@app.post('/admin/player/<player_id>/set-live')
@admin_required
def set_live(player_id):
    try:
        supabase.rpc('set_live_player', {'p_player_id': player_id}).execute(); flash('Player is now LIVE.', 'success')
    except Exception as e: flash(safe_error(e), 'danger')
    return redirect(url_for('admin_dashboard'))

@app.post('/admin/player/<player_id>/sold')
@admin_required
def sold(player_id):
    team = request.form.get('team','').strip().upper(); price = request.form.get('price','')
    custom = request.form.get('custom_price','').strip()
    try:
        if team not in TEAMS: raise ValueError('Select a valid team.')
        amount = int(custom) if custom else int(price)
        if amount <= 0 or amount > 10000000000: raise ValueError('Invalid sale price.')
        request_id = str(uuid.uuid4())
        result = supabase.rpc('mark_player_sold', {'p_player_id': player_id, 'p_team_code': team, 'p_sold_price': amount, 'p_request_id': request_id}).execute()
        row = result.data[0] if result.data else {}
        flash(f"SOLD — {row.get('player_name','Player')} to {team} for {fmt_money(amount)}", 'success')
    except Exception as e:
        msg = safe_error(e)
        if 'LOW_BALANCE' in msg: msg = 'LOW BALANCE — sale blocked.'
        elif 'SQUAD_FULL' in msg: msg = 'SQUAD FULL — this team already has 12 players.'
        elif 'ALREADY_SOLD' in msg: msg = 'This player has already been sold.'
        flash(msg, 'danger')
    return redirect(url_for('admin_dashboard'))

@app.post('/admin/player/<player_id>/delete')
@admin_required
def delete_player(player_id):
    try:
        supabase.table('players').delete().eq('id', player_id).eq('is_available', True).execute(); flash('Player deleted.', 'success')
    except Exception as e: flash(safe_error(e), 'danger')
    return redirect(url_for('admin_dashboard'))

@app.post('/admin/live/stop')
@admin_required
def stop_live():
    try: supabase.rpc('stop_live_player', {}).execute(); flash('Live display stopped.', 'success')
    except Exception as e: flash(safe_error(e), 'danger')
    return redirect(url_for('admin_dashboard'))

@app.get('/health')
def health():
    try:
        supabase.table('teams').select('id').limit(1).execute()
        return jsonify(status='ok'), 200
    except Exception: return jsonify(status='error'), 503

@app.errorhandler(403)
def forbidden(e): return render_template('error.html', code=403, message='You are not authorized to access this page.'), 403
@app.errorhandler(404)
def not_found(e): return render_template('error.html', code=404, message='The page you requested was not found.'), 404
@app.errorhandler(500)
def server_error(e): return render_template('error.html', code=500, message='Something went wrong. Please try again.'), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT',5000)), debug=False)
