import paramiko
import os

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('homeassistant.local', port=22, username='root', password='bhwjdmma', timeout=10)
sftp = ssh.open_sftp()

local_base = 'C:/Users/orang/Documents/GitHub/yongnuo-yn360-home-assistant/custom_components/yongnuo_yn360'
remote_base = '/config/custom_components/yongnuo_yn360'

# Ensure translations dir exists
try:
    sftp.mkdir(remote_base + '/translations')
except Exception:
    pass

files = [
    '__init__.py',
    'const.py',
    'config_flow.py',
    'light.py',
    'yongnuo_yn360_device.py',
    'manifest.json',
    'translations/en.json',
    'translations/de.json',
]

for f in files:
    local_path = os.path.join(local_base, f)
    remote_path = remote_base + '/' + f
    sftp.put(local_path, remote_path)
    print(f'Uploaded: {f}')

sftp.close()
ssh.close()
print('All files deployed!')
