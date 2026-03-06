# Yongnuo BLE Protocol Reference

永诺 YN360 / YN150 系列灯具 BLE 控制协议文档。

## 适用型号

| BLE 广播名 | 型号 | 支持功能 |
|------------|------|----------|
| `YUNM...` / `Yongnuo LED` | YN360 系列 | RGB + 色温 (channel=0x01) |
| `YN150Ultra RGB` | YN150 Ultra RGB | RGB (无色温 LED) |
| `YN150WY` | YN150 WY | 色温 (channel=0x00，无 RGB LED) |

## GATT 服务与特征

所有型号共用同一组 GATT 服务。

### 主服务 `f000aa60-0451-4000-b000-000000000000`

| UUID | 属性 | 用途 |
|------|------|------|
| `f000aa61-0451-4000-b000-000000000000` | write | **命令通道**（发送控制指令） |
| `f000aa63-0451-4000-b000-000000000000` | notify | 状态反馈 |
| `0000fff3-0000-1000-8000-00805f9b34fb` | write | 附加写入通道（用途未知） |
| `0000fff4-0000-1000-8000-00805f9b34fb` | notify, write | 双向通道（用途未知） |
| `0000fff5-0000-1000-8000-00805f9b34fb` | read, write | 返回 `CHAR5_VALUE`（占位符） |

## 数据包格式

所有命令均为 **6 字节定长包**，大端序。

```
字节:  [0]    [1]    [2]    [3]    [4]    [5]
含义:  头部   命令   参数1  参数2  参数3  尾部
固定:  0xAE                              0x56
```

## 命令列表

### 1. RGB 颜色控制 — `0xA1`

设置 RGB 颜色，同时开灯。

```
AE A1 RR GG BB 56
```

| 字节 | 含义 | 范围 |
|------|------|------|
| RR | 红色 | 0x00 - 0xFF (0-255) |
| GG | 绿色 | 0x00 - 0xFF (0-255) |
| BB | 蓝色 | 0x00 - 0xFF (0-255) |

**示例：**

| 指令 | 颜色 | 说明 |
|------|------|------|
| `AE A1 FF 00 00 56` | 红色 最大亮度 | R=255 |
| `AE A1 00 FF 00 56` | 绿色 最大亮度 | G=255 |
| `AE A1 00 00 FF 56` | 蓝色 最大亮度 | B=255 |
| `AE A1 FF FF FF 56` | 白色 最大亮度 | R=G=B=255 |
| `AE A1 FF 80 00 56` | 橙色 | R=255, G=128 |
| `AE A1 FF FF 00 56` | 黄色 | R=255, G=255 |
| `AE A1 00 FF FF 56` | 青色 | G=255, B=255 |
| `AE A1 FF 00 FF 56` | 品红 | R=255, B=255 |
| `AE A1 80 00 FF 56` | 紫色 | R=128, B=255 |
| `AE A1 11 11 11 56` | 暗白色 | 低亮度 |

**适用型号：** YN360、YN150Ultra RGB

> **注意：** YN150WY 会响应 A1 指令（开灯），但 RGB 值被忽略，因为硬件没有 RGB LED。
> **注意：** YN150Ultra RGB 不支持色温指令 (`AE AA`)，该指令无响应。色温控制仅限 YN150WY 和 YN360。

### 2. 色温控制 — `0xAA`

设置冷白 / 暖白亮度，控制色温。

```
AE AA CH CW WW 56
```

| 字节 | 含义 | 范围 |
|------|------|------|
| CH | 通道字节 | 0x01 (YN360) 或 0x00 (YN150WY) |
| CW | 冷白亮度 (约 5500K) | 0 - 99 (0x00 - 0x63) |
| WW | 暖白亮度 (约 3200K) | 0 - 99 (0x00 - 0x63) |

> **重要：** CW/WW 范围是 0-99，不是 0-255。超过 99 的值会被设备忽略。

**通道字节 (CH)：**

| 值 | 适用型号 | 说明 |
|----|----------|------|
| `0x01` | YN360 | YN360 系列使用 |
| `0x00` | YN150WY | YN150WY 使用；发送 0x01 无响应 |

**示例（YN150WY，channel=0x00）：**

| 指令 | 效果 | 说明 |
|------|------|------|
| `AE AA 00 63 00 56` | 冷白 最大亮度 | CW=99, WW=0 |
| `AE AA 00 00 63 56` | 暖白 最大亮度 | CW=0, WW=99 |
| `AE AA 00 32 32 56` | 混合 中等亮度 | CW=50, WW=50 |
| `AE AA 00 63 63 56` | 混合 最大亮度 | CW=99, WW=99 |
| `AE AA 00 00 0A 56` | 暖白 低亮度 | WW=10 |
| `AE AA 00 0A 00 56` | 冷白 低亮度 | CW=10 |

**示例（YN360，channel=0x01）：**

| 指令 | 效果 | 说明 |
|------|------|------|
| `AE AA 01 63 00 56` | 冷白 最大亮度 | CW=99, WW=0 |
| `AE AA 01 00 63 56` | 暖白 最大亮度 | CW=0, WW=99 |
| `AE AA 01 32 32 56` | 混合 中等亮度 | CW=50, WW=50 |

### 3. 关灯 — `0xA3`

关闭灯具。

```
AE A3 00 00 00 56
```

**适用型号：** 全部

## 完整指令速查表

| 功能 | 指令 (hex) | 备注 |
|------|-----------|------|
| 红色 | `AE A1 FF 00 00 56` | |
| 绿色 | `AE A1 00 FF 00 56` | |
| 蓝色 | `AE A1 00 00 FF 56` | |
| 白色（RGB 最大） | `AE A1 FF FF FF 56` | |
| 黄色 | `AE A1 FF FF 00 56` | |
| 青色 | `AE A1 00 FF FF 56` | |
| 品红 | `AE A1 FF 00 FF 56` | |
| 橙色 | `AE A1 FF 80 00 56` | |
| 紫色 | `AE A1 80 00 FF 56` | |
| 色温 冷白100% (YN150WY) | `AE AA 00 63 00 56` | channel=0x00 |
| 色温 暖白100% (YN150WY) | `AE AA 00 00 63 56` | channel=0x00 |
| 色温 混合50/50 (YN150WY) | `AE AA 00 32 32 56` | channel=0x00 |
| 色温 冷白100% (YN360) | `AE AA 01 63 00 56` | channel=0x01 |
| 色温 暖白100% (YN360) | `AE AA 01 00 63 56` | channel=0x01 |
| 色温 混合50/50 (YN360) | `AE AA 01 32 32 56` | channel=0x01 |
| 关灯 | `AE A3 00 00 00 56` | 全型号通用 |

## 调试工具

项目附带 `debug_ble.py`，可独立运行（不依赖 Home Assistant）：

```bash
# 扫描附近 BLE 设备
python debug_ble.py scan

# 查看 GATT 服务
python debug_ble.py services <地址>

# 监听通知
python debug_ble.py sniff <地址>

# 手写指令
python debug_ble.py write <地址> f000aa61-0451-4000-b000-000000000000 AEA1FF000056

# 自动 RGB 测试（红/绿/蓝渐变、色相环、白色亮度）
python debug_ble.py auto-rgb <地址>

# 自动色温测试（冷白/暖白渐变、双通道同步、交叉渐变，测试 channel 0x00 和 0x01）
python debug_ble.py auto-ct <地址>
```

## 参考资料

- [Samuel Pinches — Hacking YN360 Light Wand](https://samuelpinches.com.au/hacking/hacking-yn360-light-wand/)
- [kenkeiter/lantern](https://github.com/kenkeiter/lantern)
- [pinchies/YN360_webbtle](https://github.com/pinchies/YN360_webbtle)
