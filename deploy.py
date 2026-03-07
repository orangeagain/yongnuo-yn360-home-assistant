import os
from pathlib import Path

import paramiko

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('homeassistant.local', port=22, username='root', password='bhwjdmma', timeout=10)
sftp = ssh.open_sftp()

local_base = Path("custom_components/yongnuo_yn360").resolve()
remote_base = "/config/custom_components/yongnuo_yn360"

def ensure_remote_dir(path: str) -> None:
    parts = [part for part in path.strip("/").split("/") if part]
    current = ""
    for part in parts:
        current += f"/{part}"
        try:
            sftp.mkdir(current)
        except Exception:
            pass

ensure_remote_dir(remote_base)

for local_path in sorted(local_base.rglob("*")):
    if not local_path.is_file():
        continue

    relative_path = local_path.relative_to(local_base)
    if "__pycache__" in relative_path.parts:
        continue

    remote_dir = remote_base
    if len(relative_path.parts) > 1:
        remote_dir = remote_base + "/" + "/".join(relative_path.parts[:-1])
        ensure_remote_dir(remote_dir)

    remote_path = remote_base + "/" + relative_path.as_posix()
    sftp.put(os.fspath(local_path), remote_path)
    print(f"Uploaded: {relative_path.as_posix()}")

sftp.close()
ssh.close()
print('All files deployed!')
