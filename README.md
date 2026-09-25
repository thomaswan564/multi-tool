# 教务辅助工具

> 🚀 **懒人直达：** [点击这里直接跳转到使用方法](https://github.com/thomaswan564/multi-tool#-%E4%BD%BF%E7%94%A8%E6%96%B9%E6%B3%95)

一个基于 **Python + Tkinter + Playwright** 开发的桌面端教务辅助工具，提供课程表自动获取、PDF 下载以及自主选课自动化等功能。

> **项目版本：v1.1**
>
> 本项目主要用于学习 Python GUI、Playwright 浏览器自动化、网页交互及教务系统自动化相关技术。

## ✨ 功能

### 🏫 支持的学校

目前代码内置以下学校：

- 私立华联大学
- 广州大学

不同学校使用独立的网页操作逻辑，后续可以继续扩展其他学校。

### 📅 课程表自动获取

支持自动登录教务系统并进入课表查询页面：

- 自动填写学号和密码
- 自动进入课表查询页面
- 检测课表是否已经开放
- 自动执行查询操作
- 自动导出课表 PDF
- 用户可以选择 PDF 保存目录

### 🎯 全自动选课

支持自主选课页面的自动化操作：

- 自动登录教务系统
- 自动进入自主选课页面
- 自动检测当前是否处于选课阶段
- 自动获取可选课程列表
- GUI 勾选需要报名的课程
- 支持设置轮询间隔
- 按照选择顺序进行选课
- 支持自动跳过暂未开放的课程
- 支持停止正在运行的自动选课任务

### 🌐 浏览器自动检测

程序会自动检测系统中已经安装的浏览器，目前包含：

- Google Chrome
- Microsoft Edge
- Brave Browser
- Mozilla Firefox
- Safari（macOS）

用户可以选择“自动检测”，也可以手动选择检测到的浏览器。

### 💾 课程列表缓存

程序会将已经获取到的可选课程列表保存为 JSON 文件，下次启动程序时可以直接加载之前的课程列表，减少重复获取。

当前代码默认缓存文件名：

```text
cached_courses.json
```

缓存位置取决于当前代码版本的 `CACHE_FILE` 配置。

如果需要将缓存统一保存到 Windows 的 `%APPDATA%`，可以将缓存目录修改为类似：

```text
%APPDATA%\教务辅助工具\cached_courses.json
```

程序界面也提供 **“清除缓存”** 功能，可以删除已经保存的课程列表。

---

## 🖥️ 运行环境

建议使用：

- Windows 10 / Windows 11
- Python 3.10+
- Chromium 系浏览器（推荐 Chrome 或 Edge）
- Playwright

理论上也支持 macOS，但具体教务系统页面和浏览器环境需要根据实际情况测试。

---

## 📦 安装

### 1. 安装 Python

前往 Python 官网下载安装 Python：

https://www.python.org/

安装时建议勾选：

```text
Add Python to PATH
```

检查 Python：

```powershell
py --version
```

或者：

```powershell
python --version
```

### 2. 安装 Playwright

在 PowerShell / CMD 中执行：

```powershell
py -m pip install playwright
```

然后安装 Playwright 浏览器组件：

```powershell
py -m playwright install
```

如果只使用 Chromium，可以安装：

```powershell
py -m playwright install chromium
```

### 3. 检查 Playwright

```powershell
py -m playwright --version
```

---

## ▶️ 运行程序

下载或克隆项目后，在项目目录执行：

```powershell
py code-multi-tool-v1.1.py
```

如果你的 Python 命令是 `python`，也可以：

```powershell
python code-multi-tool-v1.1.py
```

程序启动后会出现 Tkinter 图形界面。

---

## 🧭 使用方法

### 课程表下载

1. 启动程序
2. 选择学校
3. 选择 `课程表自动获取下载工具`
4. 选择浏览器
5. 输入学号
6. 输入密码
7. 选择 PDF 保存路径
8. 点击开始
9. 等待程序完成课表查询和 PDF 下载

### 自动选课

1. 启动程序
2. 选择学校
3. 选择 `全自动选课工具`
4. 选择浏览器
5. 输入学号和密码
6. 获取可选课程列表
7. 勾选需要报名的课程
8. 设置轮询间隔
9. 根据需要开启“自动跳过未开放选课的课程”
10. 点击开始
11. 程序会按照选择顺序自动执行选课操作

运行过程中可以通过 **停止** 按钮终止自动选课任务。

---

## 🔐 账号安全

程序需要使用教务系统账号登录，因此请注意：

- 不要把自己的学号、密码提交到 GitHub
- 不要在代码中硬编码个人账号密码
- 不要将包含个人账号信息的截图上传到公开仓库
- 不要提交 `cached_courses.json` 等可能包含个人数据的文件

建议在 `.gitignore` 中加入：

```gitignore
__pycache__/
*.pyc
cached_courses.json
.env
.vscode/
.idea/
```

---

## 🧩 项目结构

```text
.
├── code-multi-tool-v1.1.py    # 主程序
├── README.md                   # 项目说明
└── cached_courses.json         # 本地课程缓存（运行后生成）
```

如果后续将项目拆分为多个模块，可以进一步整理为：

```text
.
├── main.py
├── schools/
│   ├── hlu.py
│   └── gzhu.py
├── requirements.txt
├── README.md
└── .gitignore
```

---

## 🛠️ 技术栈

| 技术 | 用途 |
|---|---|
| Python | 程序主体 |
| Tkinter | GUI 图形界面 |
| Playwright | 浏览器自动化 |
| asyncio | 异步网页操作 |
| threading | GUI 与自动化任务并行 |
| JSON | 本地课程列表缓存 |

---

## 🔧 开发说明

程序采用 Tkinter 作为 GUI 主线程，同时使用 `asyncio` + `threading` 执行 Playwright 自动化任务，以避免浏览器自动化过程中阻塞 GUI。

学校相关功能通过配置映射统一管理，例如：

```python
SCHOOL_FEATURES = {
    "私立华联大学": {
        "课程表自动获取下载工具": {...},
        "全自动选课工具": {...}
    },
    "广州大学": {
        "课程表自动获取下载工具": {...},
        "全自动选课工具": {...}
    }
}
```

因此新增学校时，可以按照现有学校的结构增加对应的登录、课表查询、课程抓取和自动选课函数。

---

## ⚠️ 注意事项

1. 本项目依赖具体学校教务系统的网页结构。
2. 如果学校升级教务系统、修改页面元素或接口，原有自动化逻辑可能失效。
3. “全自动选课”功能需要学校当前处于开放选课阶段。
4. 课表下载功能需要学校已经开放对应学年学期的课表查询。
5. 不同学校的登录流程可能不同，例如部分系统可能存在二次身份认证。
6. 请合理设置轮询间隔，避免对学校教务系统造成过高请求压力。
7. 使用本程序产生的选课结果、账号问题及学校系统限制等，应以学校官方系统实际结果为准。

---

## 📄 License

本项目暂未指定具体开源许可证。

如果计划公开发布并允许其他人修改、分发，建议根据自己的需求选择合适的开源许可证，例如 MIT License。

---

## 👤 作者

**Thomas Wan**

个人兴趣项目，主要用于学习：

- Python GUI 开发
- Playwright 浏览器自动化
- Web 自动化
- 教务系统自动化
- 桌面工具开发

欢迎提交 Issue 或 Pull Request，共同改进项目。
