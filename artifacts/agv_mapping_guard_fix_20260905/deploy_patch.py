"""Session-local AGV hotfix helper; credentials come only from the environment."""
import hashlib
import io
import json
import os
import posixpath
import shlex
import stat
from datetime import datetime, timezone
from pathlib import Path

import paramiko

ROOT = Path(__file__).resolve().parents[2]
WORK = ROOT / '.codex-worktrees/fix-agv-mapping-session-guard'
REMOTE = '/home/bitcq/ccs_edge_ws'
PACKAGE = 'EPGeneral_map_stream'
FILES = [
    'scripts/ground_air_stage_client.py', 'package.xml',
    'src/epgeneral_map_stream/__init__.py', 'src/epgeneral_map_stream/config.py',
    'README.md', 'CHANGELOG.md', 'test/test_ground_air_stage_client.py',
    'test/test_version_and_entrypoint.py', 'test/test_config.py',
]


def digest(data):
    return hashlib.sha256(data).hexdigest()


client = paramiko.SSHClient()
client.load_system_host_keys()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect('192.168.50.130', username='bitcq',
               password=os.environ['CCS_AGV_PASSWORD'], timeout=12)


def command(cmd):
    _, out, err = client.exec_command(cmd, timeout=30)
    stdout, stderr = out.read().decode(), err.read().decode()
    code = out.channel.recv_exit_status()
    if code:
        raise RuntimeError('remote exit %d: %s\n%s' % (code, stdout, stderr))
    return stdout


setup = 'source /opt/ros/noetic/setup.bash; source ' + REMOTE + '/devel/setup.bash; '
state = command(setup + "python3 -c 'import rospy; from std_msgs.msg import UInt8; "
                "rospy.init_node(\"ccs_guard_deploy_check\", anonymous=True); "
                "assert rospy.wait_for_message(\"/ground_air/system/stage\", UInt8, timeout=5).data == 0'")
enabled = command('systemctl --user is-enabled ccs-edge-dev.service || test $? = 1').strip()
assert enabled == 'disabled', enabled
before_nodes = command(setup + 'rosnode list')
assert not any(n in before_nodes.splitlines() for n in
               ['/fast_lio_node', '/ground_air_map_recorder', '/ground_air_global_relocalizer'])
tag = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '_mapping_guard_v2'
backup = REMOTE + '/.deployment_backups/' + tag
command('mkdir -p ' + shlex.quote(backup + '/files'))
sftp = client.open_sftp()
manifest = []
try:
    for relative in FILES:
        source = WORK / 'edge_side_pkg' / PACKAGE / relative
        target = REMOTE + '/src/' + PACKAGE + '/' + relative
        data = source.read_bytes().replace(b'\r\n', b'\n')
        with sftp.open(target, 'rb') as stream:
            old = stream.read()
        mode = stat.S_IMODE(sftp.stat(target).st_mode)
        saved = backup + '/files/' + relative
        command('mkdir -p ' + shlex.quote(posixpath.dirname(saved)) +
                ' && cp -p ' + shlex.quote(target) + ' ' + shlex.quote(saved))
        temp = target + '.' + tag + '.tmp'
        sftp.putfo(io.BytesIO(data), temp)
        sftp.chmod(temp, mode)
        with sftp.open(temp, 'rb') as stream:
            assert digest(stream.read()) == digest(data)
        sftp.posix_rename(temp, target)
        manifest.append({'path': target, 'backup': saved, 'mode': oct(mode),
                         'before_sha256': digest(old), 'after_sha256': digest(data)})
    checks = command(setup + 'python3 ' + REMOTE + '/src/' + PACKAGE +
                     '/scripts/check_version.py && rosrun epgeneral_map_stream ground_air_stage_client.py --check')
    after_enabled = command('systemctl --user is-enabled ccs-edge-dev.service || test $? = 1').strip()
    assert after_enabled == enabled
    report = {'backup': backup, 'manifest': manifest, 'enabled_before': enabled,
              'enabled_after': after_enabled, 'checks': checks}
    encoded = json.dumps(report, indent=2).encode()
    sftp.putfo(io.BytesIO(encoded), backup + '/manifest.json')
    Path(__file__).with_name('deployment.json').write_bytes(encoded)
    print(json.dumps(report, indent=2))
except Exception:
    for item in reversed(manifest):
        command('cp -p ' + shlex.quote(item['backup']) + ' ' + shlex.quote(item['path']))
    raise
finally:
    sftp.close()
    client.close()
