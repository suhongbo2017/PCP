# phonectl

Windows 上控制 Android 手机的 Python 工具：镜像、操控、剪贴板，以及脚本自动化。
底层复用 scrcpy 的服务端 `scrcpy-server` 与其视频/控制协议，版本锁定为 **5.0.1**。

## 目录

```
vendor/adb/      adb.exe + AdbWinApi.dll + AdbWinUsbApi.dll（platform-tools 官方文件）
vendor/server/   scrcpy-server-v5.0.1.jar（scrcpy v5.0.1 release）
src/phonectl/
  core/          adb、会话、协议、解码、控制通道、Controller（不依赖 Qt）
  ui/            PySide6 镜像窗口（含无线连接、脚本与宏面板）
  automation/    Device 脚本 API、OpenCV 找图、脚本运行器、宏录制/回放
  __main__.py    命令行入口
tests/           unit/（协议、控制、UI 纯逻辑）、integration/（假 adb 模拟设备端）
tools/           真机检查脚本（GUI 冒烟、性能）
```

## 安装

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev,gui]"
```

## 真机准备

1. 手机开启「开发者选项」→「USB 调试」，用数据线连接电脑并确认授权。
2. 小米等机型如果输入不生效，开启「USB 调试（安全设置）」。
3. 手机需要处于解锁状态：锁屏会拦截注入的输入（`isKeyguardShowing=true` 时无效）。

## 无线连接（Wi-Fi）

电脑和手机需要在同一个局域网内（能互相 ping 通）。连接后 `adb devices` 中的无线设备地址形如 `192.168.1.23:5555`，所有命令都可以用 `-s` 指定它。

**方式一：用 USB 线开启一次（所有 Android 版本）**

```powershell
python -m phonectl wifi enable          # 读取手机 Wi-Fi IP，切换到 TCP/IP 并连接
```
完成后可以拔掉 USB 线。下次只需 `python -m phonectl wifi connect 192.168.1.23`。
注意：手机重启后 adb 会回到 USB 模式，需要重新用 USB 执行一次 `wifi enable`。

**方式二：无线调试配对（Android 11 及以上，无需 USB）**

1. 手机：开发者选项 → 无线调试 → 使用配对码配对，记下"配对地址"和 6 位配对码。
2. `python -m phonectl wifi pair 192.168.1.23:37123 123456`
3. 在"无线调试"页面找到 `IP:端口`（端口与配对端口不同），然后 `python -m phonectl wifi connect 192.168.1.23:41234`。

**其他**

```powershell
python -m phonectl wifi ip              # 查看手机 Wi-Fi IP（USB 或无线都可以）
python -m phonectl wifi disconnect      # 断开全部无线设备
```

GUI 顶部的"无线连接"按钮提供同样的三种方式。无线画质受带宽影响，可以在"清晰度"里选 720p 或 1080p。

## 多设备性能模式

GUI 连接前提供三种视频模式：

- `全部显示`：所有设备传输、解码并显示视频，适合少量设备观察。
- `主设备显示`：只有第一台设备传输视频，其余设备关闭视频流但保留控制能力。
- `仅控制`：所有设备关闭视频流，只保留控制、脚本、宏和批量操作，适合连接更多设备。

同时可以设置最大帧率（5–60 FPS）和视频码率（2–8 Mbps）。默认是 30 FPS、4 Mbps。降低分辨率、帧率和码率可以减少网络、CPU 和内存占用；无视频设备的触控坐标通过 ADB 获取屏幕尺寸。

## 多台设备批量操作

GUI 顶部设备列表支持勾选多台已授权设备，点击“连接选中”后会为每台设备建立独立会话。返回、主页、点击、滑动、键盘、剪贴板、工具栏操作、脚本和宏回放会广播到所有已连接设备；镜像区域会按网格显示每台已连接设备的实时画面。默认关闭同步操作：点击某个设备画面后，鼠标、键盘、工具栏和剪贴板只作用于当前设备；打开“同步操作”后才会广播到全部设备，并按各设备分辨率映射触控坐标。点击“断开全部”关闭当前批次会话。

多台手机建议使用相同方向和相近分辨率，以保证同一坐标或宏在各设备上的效果一致。多个画面会自动排列为两列，窗口会在多设备连接时扩大。连接时任一设备失败会终止本批次并清理已建立的会话。

## 视频翻页

GUI 控制工具栏提供“上一条”和“下一条”手动翻页，以及“自动翻页”开关。宏录制会记录镜像触控、键盘、工具栏与翻页动作；宏回放目标遵循当前设备和“同步操作”设置。自动模式默认每 5 秒翻到下一条，间隔可设置为 1–300 秒；关闭开关或断开设备时停止自动翻页。翻页操作遵循“同步操作”模式：默认只操作当前设备，开启同步时操作全部已连接设备。翻页通过模拟短视频应用常用的上下滑动实现。

## 图形界面（镜像窗口）

```powershell
.\.venv\Scripts\python.exe -m phonectl gui
```

- 顶部选择设备、清晰度（720p … 原始分辨率），点"连接"。
- 画面按比例居中显示，黑边区域的点击会被忽略。

| 鼠标 | 作用 |
|---|---|
| 左键拖动 | 触摸 / 滑动（移动事件限制在约 60Hz） |
| 右键 | 返回 |
| 中键 | 主页 |
| 滚轮 | 滚动 |

| 键盘 | 作用 |
|---|---|
| 可打印字符 | 输入文字（ASCII） |
| Enter / Backspace / Delete / Tab / 方向键 / Home / End | 对应按键 |
| Esc | 返回 |
| Ctrl+字母 | 发送带 Ctrl 的按键（如 Ctrl+A 全选） |
| Ctrl+V | 把电脑剪贴板发送到手机剪贴板 |

左侧工具栏：返回、主页、多任务、音量±、电源键、亮屏、通知栏、收起面板、旋转、截图（保存到 `screenshots/`）、剪贴板双向同步。

## 命令行

```powershell
python -m phonectl devices
python -m phonectl info                         # 设备名、屏幕尺寸
python -m phonectl screenshot out.png [--max-size 1024]
python -m phonectl tap X Y
python -m phonectl swipe X1 Y1 X2 Y2 [--duration 0.3]
python -m phonectl key home|back|recents|enter|delete|...
python -m phonectl text "hello"
python -m phonectl wake
python -m phonectl clip-set "文本" [--paste]
python -m phonectl clip-get
python -m phonectl notify
python -m phonectl run scripts/example.py -s SERIAL
python -m phonectl replay macros/example.json -s SERIAL [--speed 2]
python -m phonectl find templates/icon.png -s SERIAL [--threshold 0.85]
python -m phonectl crop templates/icon.png X Y W H -s SERIAL
python -m phonectl wifi enable|connect|pair|disconnect|ip
```

坐标单位是屏幕像素（与 `info` 输出的尺寸一致）。`-v` 打开调试日志。

## 脚本自动化

脚本是普通 Python 文件，定义 `run(dev)`，通过 `Device` API 控制当前设备。可直接运行只读示例 `examples/foreground.py`：

```powershell
python -m phonectl run examples/foreground.py -s SERIAL
```

基本脚本结构：

```python
from pathlib import Path

def run(dev):
    dev.home()
    dev.sleep(1)
    dev.tap_rel(0.5, 0.5)
    match = dev.wait_for(Path("templates/ok.png"), timeout=5)
    dev.print("found", match.center, match.score)
```

常用方法包括 `tap`、`tap_rel`、`swipe`、`key`、`text`、`screenshot`、`find`、`wait_for`、`tap_image`、`start_app` 和 `current_app`。`dev.sleep()` 和每个设备操作都会检查停止请求。中文 `dev.text()` 会写入手机剪贴板并返回 `False`，需要在手机输入框中手动粘贴。

GUI 连接设备后，底部“自动化”面板可以运行脚本、停止脚本、录制鼠标/工具栏操作并回放宏。录制的 JSON 文件保存到 `macros/`。宏只记录输入控制，不记录剪贴板读写。

`crop` 从当前画面截取模板，`find` 用 OpenCV 模板匹配并打印坐标、尺寸、置信度和缩放比例。模板匹配支持多尺度，脚本中默认尝试 0.8、0.9、1.0、1.1、1.2 倍。

## 已知限制

- **中文输入**：服务端的文字注入不能输入汉字。GUI 会把中文放进手机剪贴板，然后需要你在输入框里**长按 → 粘贴**。`key paste` 和 Ctrl+V 在测试的搜索框里无效。
- **锁屏**：锁屏时输入被忽略，需要先手动解锁。
- **剪贴板手机→电脑**：`clip-get` 与"手机→电脑剪贴板"按钮使用 `clipboard_autosync=false` 请求当前内容。默认的 autosync 模式下，手机端的复制是否会自动推送到电脑，尚未在真机上验证。
- **会话期间 `dumpsys window` 的焦点为 null**：检查手机状态时请用 `dumpsys activity activities`（`topResumedActivity`）。

## 双击启动 GUI 工具

面向普通使用者的单文件 GUI 工具为 `dist\\phonectl-gui.exe`，双击直接打开 GUI，不需要命令行或 Python 环境。首次启动会从单文件解包依赖，启动时间会稍长；当前文件约 130 MB。只有一台已就绪设备时会自动连接，多台设备时可以在窗口里选择。

可直接复制该 exe 到常用位置；也可以运行桌面快捷方式安装脚本：

```powershell
powershell -ExecutionPolicy Bypass -File .\\install-shortcut.ps1
```

要重新构建 GUI 单文件包：

```powershell
.\\.venv\\Scripts\\python.exe -m PyInstaller --noconfirm --clean phonectl-gui.spec
```

发布时双击工具只需 `phonectl-gui.exe`。命令行开发/排障版仍在 `dist\\phonectl\\phonectl.exe`，整个 `dist\\phonectl` 目录需要一起保留。

## Windows exe 打包（CLI onedir）

项目使用 PyInstaller 生成 `onedir` 发布包，发布目录为 `dist/phonectl/`。该目录必须整体复制，不能只复制 `phonectl.exe`，因为 adb、Windows DLL、scrcpy server 和运行时依赖都在 `_internal/` 中。

构建：

```powershell
powershell -ExecutionPolicy Bypass -File .\build.ps1
```

启动 GUI：

```powershell
.\dist\phonectl\phonectl.exe gui -s 192.168.102.22:40463
```

CLI 示例：

```powershell
.\dist\phonectl\phonectl.exe devices
.\dist\phonectl\phonectl.exe info -s SERIAL
.\dist\phonectl\phonectl.exe run _internal\examples\foreground.py -s SERIAL
```

发布时复制整个 `dist\phonectl` 文件夹。当前已验证 exe 可以独立执行 `--help`、`devices`、`info`、`run`，并能启动 GUI。

## 测试

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

`tests/integration` 用 `fake_adb.py` 模拟设备端（视频、控制两个连接），无需手机。

真机检查（需要手机解锁并停在桌面）：

```powershell
.\.venv\Scripts\python.exe tools\gui_device_check.py     # 通过 QTest 驱动窗口，验证点击/滑动/按键到达手机
.\.venv\Scripts\python.exe tools\gui_perf_check.py --quality 2 --seconds 6   # 解码帧率与 GUI 缩放耗时
```

## 注意

- scrcpy 协议是内部协议，**客户端与 server jar 版本必须一致**。升级 jar 时要同步检查 `core/protocol/`。
- 环境变量 `PHONECTL_ADB`、`PHONECTL_SERVER` 可覆盖 adb 与 server 路径。
- scrcpy 为 Apache-2.0 许可，分发 server jar 时需保留其 LICENSE 声明。
