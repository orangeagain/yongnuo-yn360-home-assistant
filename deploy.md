# Home Assistant 部署指南

## 服务器信息

| 项目 | 值 |
|------|-----|
| 地址 | `homeassistant.local` |
| SSH 端口 | `22` |
| 用户名 | `root` |
| 密码 | `bhwjdmma` |
| HA 版本 | 2026.2.1 |
| 架构 | aarch64 (qemuarm-64) |
| HA Web 端口 | 8123 |

## 部署路径

服务器上的集成目录：`/config/custom_components/yongnuo_yn360/`

## 部署方法

运行项目根目录下的 `deploy.py` 脚本即可一键上传所有文件：

```bash
python deploy.py
```

该脚本通过 `paramiko`（SSH/SFTP）将以下文件上传到服务器：

- `__init__.py`
- `const.py`
- `config_flow.py`
- `light.py`
- `yongnuo_yn360_device.py`
- `manifest.json`
- `translations/en.json`
- `translations/de.json`

## 部署后重启

上传文件后需要重启 Home Assistant 才能生效：

```bash
python -c "
import paramiko
ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('homeassistant.local', port=22, username='root', password='bhwjdmma')
ssh.exec_command('ha core restart')
ssh.close()
"
```

或通过 HA Web UI：设置 → 系统 → 右上角菜单 → 重新启动

## 依赖

- Python 包 `paramiko`：`pip install paramiko`
- 注意：Windows 上原生 `ssh` 命令无法非交互式输入密码（没有 `sshpass`），所以使用 paramiko
