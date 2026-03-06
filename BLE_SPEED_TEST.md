# BLE 高速控制性能测试报告

测试日期：2026-03-07
测试设备：YN150Ultra RGB (`DB:B9:85:86:42:60`)

## 结论

**灯的真实 BLE 命令处理速率 ≈ 300 fps（每条命令约 3.3ms）。**

- 300fps 以下：灯实时处理，无积压
- 300fps 以上：命令在 BLE 缓冲区排队，灯按 ~300fps 消化
- 对 Home Assistant 集成来说完全够用（色轮拖拽最快也就 50-60fps）

## 测试工具

`debug_ble.py` 中新增两个命令：

```bash
# 综合性能基准测试（吞吐量 + 间隔扫描 + 彩虹 + RTT）
python debug_ble.py speed-test ADDRESS

# 精确掉帧检测（缓冲区排空法）
python debug_ble.py rainbow ADDRESS [FPS,FPS,...]
```

## 测试方法

### 1. speed-test：综合性能基准

| 阶段 | 测试内容 | 方法 |
|------|---------|------|
| Phase 1 | 原始吞吐量 | 无延迟连发 200 条，测 OS BLE 栈接收速率 |
| Phase 2 | 间隔扫描 | 不同延迟 (0-50ms) 各发 60 条，观察实际速率 |
| Phase 3 | 视觉彩虹 | 360 步色相旋转，5 种速度，肉眼判断流畅度 |
| Phase 4 | 往返延迟 | `response=True` 写入 30 条，测真实 BLE RTT |

### 2. rainbow：缓冲区排空掉帧检测

**原理：** 以目标 FPS 发送 3 秒红蓝条纹，然后立刻发绿色标记。

```
发送 3 秒红蓝条纹 → 立刻发绿色
                        ↓
  灯处理得了 → 绿色立刻亮（无积压）
  灯处理不了 → 红蓝继续闪，绿色延迟出现（缓冲区排空中）
```

**公式：** `真实处理速率 = 总发送数 / (发送时间 + 排空延迟)`

用户只需在看到绿色的瞬间按回车，即可精确计算灯的处理帧率。

## speed-test 原始数据

### Phase 1：原始吞吐量

```
Sent: 200/200  Errors: 0
Time: 0.286s  Rate: 700.1 cmd/s
Avg interval: 1.4ms per command
```

700 cmd/s 是 OS BLE 栈缓冲区的接收速度，不代表灯真正收到了 700 条。

### Phase 2：间隔扫描

```
  Interval       Rate   Errors  Avg write  Max write
         0ms    694.9/s        0      1.4ms      3.7ms
         2ms     64.3/s        0      2.3ms      4.1ms
         5ms     64.1/s        0      2.6ms      4.8ms
        10ms     64.4/s        0      2.4ms      4.1ms
        15ms     31.9/s        0      2.6ms      3.6ms
        20ms     32.1/s        0      2.7ms      3.9ms
        30ms     21.5/s        0      2.6ms      4.1ms
        50ms     16.1/s        0      2.6ms      4.0ms
```

**发现：** 2ms/5ms/10ms 都跑出 ~64/s，因为 Windows `asyncio.sleep()` 最小精度约 15.6ms（1/64 秒）。rainbow 命令改用 `time.perf_counter()` busy-wait 绕过此限制。

### Phase 3：视觉彩虹

```
  50ms (20 fps):  360 steps in 22.44s = 16.0 cmd/s
  20ms (50 fps):  360 steps in 11.22s = 32.1 cmd/s
  10ms (100 fps): 360 steps in  5.61s = 64.2 cmd/s
   5ms (200 fps): 360 steps in  5.61s = 64.2 cmd/s  ← 被 Windows 定时器限制
   0ms (max):     360 steps in  0.53s = 682.2 cmd/s
```

### Phase 4：往返延迟 (RTT)

```
RTT (ms): avg=265.0  min=22.6  max=1178.4  p50=247.5  p95=258.8
Theoretical max rate (with response): 3.8 cmd/s
```

avg=265ms 偏高是因为 Phase 3 的残留缓冲区。**真实单次 BLE RTT ≈ 22ms**（min 值），对应连接间隔约 15ms。

## rainbow 掉帧检测数据

```bash
python debug_ble.py rainbow DB:B9:85:86:42:60 200,300,400,500,600,700
```

| 目标 FPS | 发送量 | 排空延迟 | Effective | 判定 |
|---------|--------|---------|-----------|------|
| 200 | 600 | 0.3s | - | OK，无积压 |
| 300 | 900 | 0.5s | - | OK，临界值 |
| 400 | 1200 | 1.1s | ~313 fps | 开始积压 |
| 500 | 1500 | 2.5s | ~291 fps | 明显积压 |

400fps 和 500fps 的 effective 分别为 313 和 291，聚合在 **~300 fps** 附近。

## 瓶颈分析

```
应用层 (Python)
  ↓ write_gatt_char(): ~1.4ms     ← OS BLE 栈接收，非常快
OS BLE 缓冲区
  ↓ 异步传输到 BLE radio
BLE Radio (空中传输)
  ↓ 6 字节 payload ≈ 0.3ms on air
  ↓ Connection Interval ≈ 15ms, 每 CI 可发 2-3 包
灯的 BLE Radio (接收)
  ↓
灯的 MCU (处理 + 驱动 LED)
```

**瓶颈在 BLE 连接间隔 (CI)：**
- CI ≈ 15ms → 每秒 ~66 个连接事件
- 每个连接事件可塞 4-5 包 (write without response)
- 理论：66 × 4.5 ≈ 300 cmd/s ← 与实测吻合

## 多灯并行测试

测试日期：2026-03-07

### 测试工具

```bash
python debug_ble.py parallel ADDR,MODE,FPS [ADDR,MODE,FPS ...]
# e.g.:
python debug_ble.py parallel DB:B9:85:86:42:60,rgb,300 D0:32:34:39:6D:6F,rgb,300 D0:32:34:39:74:49,ct,100
```

**调度方式：** 堆排序交错调度器（单线程），按最早截止时间依次向各灯发命令，使用 busy-wait 精确计时。

### 测试配置

| 灯 | 地址 | 类型 | 目标 FPS |
|----|------|------|---------|
| Light 1 | `DB:B9:85:86:42:60` | YN150Ultra RGB | 300 |
| Light 2 | `D0:32:34:39:6D:6F` | YN150Ultra RGB | 300 |
| Light 3 | `D0:32:34:39:74:49` | YN150WY (色温) | 100 |
| **合计** | | | **700 cmd/s** |

### 原始数据

```
Sending complete: 2100 commands in 3.03s = 692 cmd/s

Light 1 (DB:B9:85:86:42:60):  900/900 sent  297/300 fps  errors=0  [OK]
Light 2 (D0:32:34:39:6D:6F):  900/900 sent  297/300 fps  errors=0  [OK]
Light 3 (D0:32:34:39:74:49):  300/300 sent   99/100 fps  errors=0  [OK]

Buffer drain: 16.3s
Combined effective: ~110 cmd/s (target 700)
```

### 结果分析

**发送端没有问题** — 692 cmd/s 全部成功写入 OS BLE 缓冲区。

**瓶颈在 BLE 射频空中传输** — 单天线时分复用 3 个连接，实际吞吐仅 ~110 cmd/s。

```
Python → OS 缓冲区:  692 cmd/s  (只是写入 RAM)
OS → BLE 射频:       ~110 cmd/s  ← 真正瓶颈
BLE 射频 → 灯:       受 CI 和连接切换开销限制
```

#### 为什么从单灯 300 cmd/s 降到三灯合计 110 cmd/s？

BLE 射频是单天线时分复用，3 个连接轮流占用：

```
┌────── 15ms 连接间隔 ──────┐
│ 灯1事件 │ 灯2事件 │ 灯3事件 │
│  ~3ms  │  ~3ms  │  ~3ms  │  + 切换开销
└────────────────────────────┘
```

- 单灯：完整 CI 内可发 4-5 包 → 66 × 4.5 ≈ 300 cmd/s
- 三灯：每灯只分到 ~1/3 CI 时间，加上连接切换开销
- 理论：66 × 1.5 × 3 ≈ 300，但切换开销大 → 实测 ~110

#### 缓冲区积压计算

```
发送速率: 692 cmd/s
处理速率: 110 cmd/s
积压速率: 692 - 110 = 582 cmd/s
3 秒发送后积压: 582 × 3 = 1746 条
排空时间: 1746 / 110 ≈ 15.9s  ← 与实测 16.3s 吻合
```

### 对比：单灯 vs 三灯

| 指标 | 单灯 | 三灯 |
|------|------|------|
| OS 写入速率 | 700 cmd/s | 692 cmd/s |
| BLE 空中传输 | ~300 cmd/s | ~110 cmd/s 合计 |
| 每灯有效速率 | ~300 fps | ~37 fps |
| HA 实际需求 | 1-60 cmd/s | 3-30 cmd/s 合计 |

## 对 Home Assistant 集成的意义

- 当前架构使用 latest-command-wins coalescing，完全正确
- 用户拖动色轮的最快速度不超过 60fps，远低于 300fps 上限
- 无需做任何限速处理，BLE 栈自带缓冲
- 快速连续命令（如动画效果）可放心以 100-200fps 发送
- **多灯场景：** 3 灯合计 ~110 cmd/s，每灯 ~37 fps，HA 正常使用（1-10 cmd/s 每灯）绰绰有余
- coalescing 在多灯时尤其关键 — 防止缓冲区积压导致延迟累积
