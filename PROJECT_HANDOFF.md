# phonectl 项目交接文档

> 更新日期：2026-10-26
> 当前阶段：M0-M3 已完成，CLI onedir 包和双击 GUI 单文件包均已构建
> 目标平台：Windows
> 主要语言：Python 3.11+

## 1. 项目概览

`phonectl` 是一个基于 scrcpy 5.0.1 server 的 Windows Android 手机控制工具，当前包含：

- 实时手机画面镜像
- 鼠标、键盘和工具栏控制
- USB 与 Wi-Fi adb 连接
- Android 11+ 无线调试配对
- 手机剪贴板与电脑剪贴板同步
- Python 自动化脚本
- OpenCV 模板匹配和找图点击
- GUI 宏录制与回放
- CLI 和 PySide6 GUI 两套入口

核心约束：客户端协议和 `scrcpy-server-v5.0.1.jar` 必须保持同一版本。

## 2. 当前目录结构

```text
phone-control/
├── src/phonectl/
│   ├── __main__.py             CLI 入口
│   ├── config.py               adb、server、资源路径配置
│   ├── core/
│   │   ├── adb.py              adb 封装、设备解析、Wi-Fi 操作
│   │   ├── wifi.py             无线连接流程和重试
│   │   ├── session.py          scrcpy 会话、视频/控制 socket
│   │   ├── controller.py       点击、滑动、按键、剪贴板等控制 API
│   │   ├── control_channel.py  控制通道和响应处理
│   │   └── protocol/           scrcpy 控制、视频、设备协议
│   ├── automation/
│   │   ├── device.py           脚本 Device API
│   │   ├── vision.py           OpenCV 模板匹配
│   │   ├── runner.py           后台脚本运行器
│   │   └── macro.py             宏录制、JSON、回放
│   └── ui/
│       ├── main_window.py      主窗口、镜像、工具栏、自动化面板
│       ├── mirror_view.py      镜像交互和坐标映射
│       └── wifi_dialog.py      Wi-Fi 连接和配对对话框
├── vendor/
│   ├── adb/                    adb.exe 和 Windows DLL
│   └── server/                 scrcpy-server-v5.0.1.jar
├── examples/foreground.py      只读自动化示例
├── tests/                      单元测试和集成测试
├── tools/                      真机 GUI 检查和性能工具
├── README.md                   使用说明
├── PROJECT_HANDOFF.md          本交接文档
└── pyproject.toml              项目和依赖配置
```

## 3. 环境和安装

要求：

- Windows
- Python 3.11 或更高
- 手机开启 USB 调试，或已配置无线调试
- 小米设备输入不生效时，需要打开“USB 调试（安全设置）”
- 手机必须解锁才能接收注入输入

开发环境安装：

```powershell
cd D:\PI\phone-control
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev,gui]"
```

当前主要依赖：

- PySide6
- PyAV
- NumPy
- OpenCV
- pytest

项目自带 `vendor/adb` 和 scrcpy server，不依赖系统 PATH 中的 adb。

## 4. 常用运行命令

列出设备：

```powershell
.\.venv\Scripts\python.exe -m phonectl devices
```

打开 GUI：

```powershell
.\.venv\Scripts\python.exe -m phonectl gui -s SERIAL
```

CLI 基础操作：

```powershell
.\.venv\Scripts\python.exe -m phonectl info -s SERIAL
.\.venv\Scripts\python.exe -m phonectl screenshot out.png -s SERIAL
.\.venv\Scripts\python.exe -m phonectl tap 500 800 -s SERIAL
.\.venv\Scripts\python.exe -m phonectl swipe 500 1400 500 500 -s SERIAL
.\.venv\Scripts\python.exe -m phonectl key home -s SERIAL
.\.venv\Scripts\python.exe -m phonectl text "hello" -s SERIAL
```

无线连接：

```powershell
.\.venv\Scripts\python.exe -m phonectl wifi enable -s USB_SERIAL
.\.venv\Scripts\python.exe -m phonectl wifi pair IP:PAIR_PORT 123456
.\.venv\Scripts\python.exe -m phonectl wifi connect IP:CONNECT_PORT
.\.venv\Scripts\python.exe -m phonectl wifi disconnect IP:PORT
```

无线调试配对流程：

1. 手机进入“开发者选项 → 无线调试 → 使用配对码配对设备”。
2. 使用手机显示的配对地址和 6 位配对码执行 `wifi pair`。
3. 返回无线调试主页面，读取另一个用于连接的 `IP:端口`。
4. 执行 `wifi connect IP:端口`。
5. 配对端口和连接端口通常不同。

当前已验证过的设备地址：

```text
192.168.102.22:40463  配对后的无线调试连接
192.168.102.22:5555   旧的 adb tcpip 连接
```

## 5. GUI 功能

主窗口包含：

- 设备选择、刷新、连接和断开
- 画质选择：720p、1080p、1280、1600、原始分辨率
- 实时镜像
- 鼠标点击、拖动、滚轮
- 键盘输入
- 左侧工具栏：返回、主页、多任务、音量、电源键、息屏、亮屏、通知栏、收起面板、旋转、截图、剪贴板
- Wi-Fi 连接对话框
- 底部自动化面板

“息屏”调用 `set_display_power(False)`，只关闭手机屏幕，不关闭 adb 或 scrcpy 会话。电脑端连接和控制仍然保持。

自动化面板支持：

- 选择并运行 `.py` 脚本
- 停止运行中的脚本
- 录制鼠标和工具栏控制操作
- 保存 JSON 宏到 `macros/`
- 选择并回放宏
- 查看运行日志

## 6. 自动化脚本

脚本定义 `run(dev)`：

```python
from pathlib import Path


def run(dev):
    dev.home()
    dev.sleep(1)
    match = dev.wait_for(Path("templates/ok.png"), timeout=5)
    dev.tap(*match.center)
```

常用 API：

- `dev.tap(x, y)`
- `dev.tap_rel(fx, fy)`
- `dev.swipe(...)`
- `dev.swipe_up()`、`swipe_down()`、`swipe_left()`、`swipe_right()`
- `dev.key("home")`
- `dev.text("hello")`
- `dev.screenshot()`
- `dev.find(template)`
- `dev.wait_for(template)`
- `dev.tap_image(template)`
- `dev.start_app(package)`
- `dev.current_app()`
- `dev.wait_app(package)`
- `dev.sleep(seconds)`
- `dev.print(...)`

脚本运行器在后台线程运行，`dev.sleep()` 和设备动作会检查停止请求。中文 `dev.text()` 不能直接注入，会尝试写入手机剪贴板并返回 `False`。

CLI 自动化：

```powershell
.\.venv\Scripts\python.exe -m phonectl run examples/foreground.py -s SERIAL
.\.venv\Scripts\python.exe -m phonectl replay macros/example.json -s SERIAL
.\.venv\Scripts\python.exe -m phonectl find templates/icon.png -s SERIAL
.\.venv\Scripts\python.exe -m phonectl crop templates/icon.png X Y W H -s SERIAL
```

## 7. 测试状态

最近一次完整测试：

```text
116 passed
```

测试覆盖：

- scrcpy 控制协议序列化
- adb 输出解析和 Wi-Fi 连接重试
- 视频协议和帧转换
- GUI 坐标映射、按键映射、工具栏息屏按钮
- OpenCV 模板匹配、区域、多尺度和中文路径
- Device API
- 脚本运行、停止、异常和日志
- 宏录制、JSON 往返、时间回放和停止
- fake adb 集成测试

已完成的真机验证：

- USB 和 Wi-Fi 连接
- 无线调试配对
- 实时镜像和控制
- 截图和模板匹配
- 自动化脚本读取设备屏幕、尺寸和前台 Activity
- GUI 宏录制与回放路径
- “息屏 → 唤醒”，连接保持正常

执行测试：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

## 8. 打包状态和下一步

CLI onedir 包位于 `dist/phonectl/`；面向普通用户的单文件 GUI 位于 `dist/phonectl-gui.exe`。推荐双击 `phonectl-gui.exe` 使用。打包时必须包含以下资源：

```text
vendor/adb/adb.exe
vendor/adb/AdbWinApi.dll
vendor/adb/AdbWinUsbApi.dll
vendor/server/scrcpy-server-v5.0.1.jar
```

`config.py` 已支持源码模式和 PyInstaller 冻结模式：用户数据写入 exe 所在目录，adb 和 server 从 PyInstaller 资源目录读取。

CLI 排障版本采用 `onedir`；用户 GUI 另提供 `onefile`，双击直接进入 GUI、不显示控制台，若只检测到一台设备会自动连接。单文件约 130 MB，首次启动需要解包，启动时间会比 onedir 长。

```text
dist/phonectl/
├── phonectl.exe
├── vendor/adb/...
├── vendor/server/...
└── 运行依赖 DLL
```

CLI onedir 更容易诊断资源缺失问题，也避免每次启动解压。

重新构建：

```powershell
cd D:\PI\phone-control
powershell -ExecutionPolicy Bypass -File .\build.ps1
```

`build.ps1` 构建 CLI onedir 包。GUI 单文件包构建命令：

```powershell
.\.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean phonectl-gui.spec
```

安装桌面快捷方式（复制到用户本地 Programs 目录，不需要管理员权限）：

```powershell
powershell -ExecutionPolicy Bypass -File .\install-shortcut.ps1
```

GUI 用户文件：`dist/phonectl-gui.exe`。CLI 发布目录：

```text
dist/phonectl/
├── phonectl.exe
├── _internal/vendor/adb/...
├── _internal/vendor/server/scrcpy-server-v5.0.1.jar
└── _internal/examples/foreground.py
```

已完成的独立 exe 验证：

```text
phonectl.exe --help       通过
phonectl.exe devices      通过，发现 Wi-Fi 设备
phonectl.exe info         通过，建立 scrcpy 会话并返回 1080x2400
phonectl.exe run          通过，执行发布包内 foreground.py
phonectl.exe gui          能启动并保持常驻
phonectl-gui.exe           无控制台，启动后稳定运行 18 秒
```

打包完成后必须验证：

1. 在没有激活虚拟环境的情况下启动 exe。
2. `phonectl.exe devices` 能发现 adb 设备。
3. GUI 能启动并显示设备列表。
4. GUI 能推送 scrcpy server 并建立镜像。
5. USB 和 Wi-Fi 设备均能连接。
6. “息屏”按钮仍能工作。
7. `run examples/foreground.py` 或发布包中的示例脚本能运行。
8. 目标机器没有 Python 时仍能启动。

## 9. 已知限制

- 中文输入不能通过 scrcpy `INJECT_TEXT` 可靠注入；需要剪贴板和手机端粘贴。
- 手机锁屏时输入注入可能无效。
- `adb tcpip 5555` 模式通常在手机重启后失效，需要重新启用；无线调试配对方式更适合长期使用。
- Wi-Fi 连接性能受局域网带宽和延迟影响。
- `wifi pair` 配对端口和无线调试主页面的连接端口不同。
- GUI 脚本文件由用户提供并在本机进程中执行，不能运行不可信脚本。
- OpenCV 模板匹配对模板质量、画面缩放和 UI 变化敏感。

## 10. 维护注意事项

- 不要单独升级 scrcpy server；客户端协议代码和 server jar 必须同步。
- 修改 `vendor` 资源后重新验证 adb、Wi-Fi 和镜像流程。
- 任何控制协议修改都应同时更新协议单测和真机检查。
- 打包前先执行完整 pytest，再执行发布目录冒烟测试。
- 当前目录不是 Git 仓库，交接时需由接手人自行建立版本控制或接入现有仓库。
