import subprocess
from flask import Blueprint, render_template, jsonify
from routes.auth import login_required
from wireguard import WG_INTERFACE

logs_bp = Blueprint('logs', __name__)

_LOG_CMDS = {
    'traverse':  ['journalctl', '-u', 'traverse', '-n', '100', '--no-pager', '--output=short'],
    'wireguard': ['journalctl', '-u', f'wg-quick@{WG_INTERFACE}', '-n', '100', '--no-pager', '--output=short'],
}


def _fetch_log(key):
    cmd = _LOG_CMDS.get(key)
    if not cmd:
        return 'Unknown log source.'
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return r.stdout or r.stderr or '(no output)'
    except Exception as e:
        return f'Error: {e}'


@logs_bp.route('/logs')
@login_required
def logs_view():
    return render_template('logs.html')


@logs_bp.route('/api/logs/traverse')
@login_required
def logs_traverse():
    return jsonify({'lines': _fetch_log('traverse')})


@logs_bp.route('/api/logs/wireguard')
@login_required
def logs_wireguard():
    return jsonify({'lines': _fetch_log('wireguard')})
