# 教務輔助工具 | Academic Affairs Assistant

A desktop **Academic Affairs Assistant** built with **Python + Tkinter + Playwright**, providing automatic timetable retrieval, PDF export, and automated course selection.

> **Current Version: v1.2**
>
> This project is mainly developed as a personal learning project for Python GUI development, Playwright browser automation, web interaction, and academic affairs system automation.

本項目是一個基於 **Python + Tkinter + Playwright** 開發的桌面端**教務輔助工具**，提供課程表自動獲取、PDF 下載以及自主選課自動化等功能。

> **當前版本：v1.2**
>
> 本項目主要用於學習 Python GUI、Playwright 瀏覽器自動化、網頁交互及教務系統自動化相關技術。

---

## 🚀 Quick Start | 快速開始

> **Lazy? Go straight to the usage guide:**
>
> [👉 Click here to jump directly to usage guide](https://github.com/thomaswan564/multi-tool#-%E4%BD%BF%E7%94%A8%E6%96%B9%E6%B3%95--usage)

> **懶人直達：**
>
> [👉 點擊這裡直接跳轉至「使用方法」](https://github.com/thomaswan564/multi-tool#-%E4%BD%BF%E7%94%A8%E6%96%B9%E6%B3%95--usage)

---

## ✨ Features | 功能

### 🏫 Supported Universities | 支持的學校

Currently supported:

- **Guangzhou (Canton) Private Hualian University (HLU)**
- **Guangzhou (Canton) University (GZHU)**

目前支持：

- **廣州私立華聯大學（HLU）**
- **廣州大學（GZHU）**

Each university uses its own web automation logic, allowing additional universities to be added in the future.

不同學校使用獨立的網頁操作邏輯，後續可以繼續擴展其他學校。

### 📅 Automatic Timetable Retrieval | 課程表自動獲取

The program can automatically log in to the academic affairs system and access the timetable query page:

- Automatically enter student ID and password
- Automatically navigate to the timetable page
- Detect whether the timetable is available
- Automatically perform the query
- Export the timetable as a PDF
- Allow the user to choose the PDF save location

程序可以自動登入教務系統並進入課表查詢頁面：

- 自動填寫學號和密碼
- 自動進入課表查詢頁面
- 檢測課表是否已開放
- 自動執行查詢操作
- 自動導出課表 PDF
- 用戶可以選擇 PDF 保存位置

### 🎯 Automated Course Selection | 全自動選課

The automated course selection feature supports:

- Automatic login to the academic affairs system
- Automatic navigation to the course selection page
- Detection of the current course selection status
- Automatic retrieval of available courses
- GUI-based course selection
- Configurable polling interval
- Course selection in the order selected by the user
- Automatic skipping of courses that are not yet open
- Ability to stop an active course selection task

全自動選課功能支持：

- 自動登入教務系統
- 自動進入自主選課頁面
- 自動檢測當前是否處於選課階段
- 自動獲取可選課程列表
- 通過 GUI 勾選需要報名的課程
- 支持設置輪詢間隔
- 按照用戶選擇的順序進行選課
- 支持自動跳過暫未開放的課程
- 支持停止正在運行的自動選課任務

### 🌐 Browser Detection | 瀏覽器自動檢測

The program automatically detects supported browsers installed on the system, including:

- Google Chrome
- Microsoft Edge
- Brave Browser
- Mozilla Firefox
- Safari (macOS)

程序會自動檢測系統中已安裝的瀏覽器，目前包括：

- Google Chrome
- Microsoft Edge
- Brave Browser
- Mozilla Firefox
- Safari（macOS）

You can select **Automatic Detection (自动检测)** or manually choose a detected browser.

用戶可以選擇「自动检测」，也可以手動選擇已檢測到的瀏覽器。

### 💾 Course List Cache | 課程列表緩存

The program saves retrieved course lists as a JSON cache file so that previously retrieved courses can be loaded when the program starts again.

程序會將已獲取的可選課程列表保存為 JSON 緩存文件，下次啟動程序時可以直接加載之前的課程列表，減少重複獲取。

The cache file is:

```text
cached_courses.json
```

在 Windows 下，配置數據保存在當前 Windows 用戶的 `%APPDATA%` 目錄中：

```text
%APPDATA%\教務輔助工具\cached_courses.json
```

The application also provides a **Clear Cache** function.

程序界面同時提供 **「清除缓存」** 功能，可以刪除已保存的課程列表。

---

## 🖥️ Requirements | 運行環境

Recommended environment:

- Microsoft Windows 10 / Windows 11
- Python 3.10+
- Chromium-based browser or Mozilla Firefox
- Playwright

建議運行環境：

- Microsoft Windows 10 / Windows 11
- Python 3.10+
- Chromium 系瀏覽器或 Mozilla Firefox
- Playwright

The program may also work on macOS, but the supported university systems and browser environments should be tested separately.

理論上也可以在 macOS 上運行，但具體教務系統頁面及瀏覽器環境需要根據實際情況測試。

---

## 📦 Installation | 安裝

### 1. Install Python | 安裝 Python

Download and install Python from the official website:

[Python](https://www.python.org/)

Download and install Python from the official website:

[Python 官方網站](https://www.python.org/)

During installation, it is recommended to enable:

```text
Add Python to PATH
```

Check the installation:

```powershell
py --version
```

or:

```powershell
python --version
```

### 2. Install Playwright | 安裝 Playwright

Run the following in PowerShell or CMD:

```powershell
py -m pip install playwright
```

Then install the Playwright browser components:

```powershell
py -m playwright install
```

If you only need Chromium:

```powershell
py -m playwright install chromium
```

### 3. Check Playwright | 檢查 Playwright

```powershell
py -m playwright --version
```

---

## ▶️ Run | 運行程序

Clone or download the repository, then open PowerShell / CMD in the project directory.

克隆或下載本項目後，在項目目錄打開 PowerShell / CMD。

Run:

```powershell
py code-multi-tool-v1.1.py
```

If your Python command is `python`, you can also use:

```powershell
python code-multi-tool-v1.1.py
```

The program will open a Tkinter graphical interface.

程序啟動後會出現 Tkinter 圖形界面。

---

## 🧭 使用方法 | Usage

### 📅 Timetable Download | 課程表下載

1. Start the program.
2. Select a university.
3. Select `课程表自动获取下载工具`.
4. Select a browser.
5. Enter your student ID.
6. Enter your password.
7. Select a PDF save location.
8. Click **开始 Start**.
9. Wait for the timetable query and PDF download to finish.

1. 啟動程序。
2. 選擇學校。
3. 選擇 `课程表自动获取下载工具`。
4. 選擇瀏覽器。
5. 輸入學號。
6. 輸入密碼。
7. 選擇 PDF 保存路徑。
8. 點擊 **开始 Start**。
9. 等待程序完成課表查詢及 PDF 下載。

### 🎯 Automated Course Selection | 全自動選課

1. Start the program.
2. Select a university.
3. Select `全自动选课工具`.
4. Select a browser.
5. Enter your student ID and password.
6. Retrieve the available course list.
7. Select the courses you want to register for.
8. Set the polling interval.
9. Enable **Automatically Skip Unopened Courses** if needed.
10. Click **开始 Start**.
11. The program will automatically attempt course selection in the order you selected.

1. 啟動程序。
2. 選擇學校。
3. 選擇 `全自动选课工具`。
4. 選擇瀏覽器。
5. 輸入學號及密碼。
6. 獲取可選課程列表。
7. 勾選需要報名的課程。
8. 設置輪詢間隔。
9. 根據需要開啟「自動跳過未開放選課的課程」。
10. 點擊 **开始 Start**。
11. 程序會按照選擇順序自動執行選課操作。

You can click **停止 Stop** during execution to terminate the automated course selection task.

運行過程中可以通過 **停止 Stop** 按鈕終止自動選課任務。

---

## 🔐 Account Security | 賬號安全

The program requires your academic affairs system credentials. Please keep them secure:

- Do not upload your student ID or password to GitHub.
- Do not hard-code personal credentials in the source code.
- Do not upload screenshots containing personal account information to a public repository.
- Do not commit local cache files that may contain personal information.

程序需要使用教務系統賬號登錄，請注意：

- 不要將自己的學號、密碼提交到 GitHub。
- 不要在代碼中硬編碼個人賬號密碼。
- 不要將包含個人賬號信息的截圖上傳到公開倉庫。
- 不要提交可能包含個人數據的本地緩存文件。

Recommended `.gitignore` entries:

建議在 `.gitignore` 中加入：

```gitignore
__pycache__/
*.pyc
cached_courses.json
.env
.vscode/
.idea/
```

---

## 🧩 Project Structure | 項目結構

```text
.
├── code-multi-tool-v1.1.py    # Main program / 主程序
├── README.md                   # Documentation / 項目說明
└── cached_courses.json         # Local cache / 本地課程緩存（運行後生成）
```

If the project is later split into multiple modules, it can be organized as:

如果後續將項目拆分為多個模塊，可以進一步整理為：

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

## 🛠️ Technology Stack | 技術棧

| Technology | Usage | 技術 | 用途 |
|---|---|---|---|
| Python | Main application | Python | 程序主體 |
| Tkinter | GUI | Tkinter | 圖形界面 |
| Playwright | Browser automation | Playwright | 瀏覽器自動化 |
| asyncio | Asynchronous web operations | asyncio | 異步網頁操作 |
| threading | GUI and automation task concurrency | threading | GUI 與自動化任務並行 |
| JSON | Local course cache | JSON | 本地課程列表緩存 |

---

## 🔧 Development | 開發說明

The program uses Tkinter as the main GUI thread and runs Playwright automation through `asyncio` + `threading` to avoid blocking the GUI during browser automation.

程序採用 Tkinter 作為 GUI 主線程，同時使用 `asyncio` + `threading` 執行 Playwright 自動化任務，以避免瀏覽器自動化過程中阻塞 GUI。

University-specific features are managed through a unified configuration mapping:

學校相關功能通過統一的配置映射進行管理：

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

Additional universities can be added by implementing the corresponding login, timetable query, course retrieval, and automated course selection functions.

因此，新增學校時，可以按照現有學校的結構增加對應的登錄、課表查詢、課程抓取及自動選課函數。

---

## ⚠️ Notes | 注意事項

1. This project depends on the specific web structure of each university's academic affairs system.
2. If a university changes its system, page elements, or APIs, existing automation logic may stop working.
3. The automated course selection feature requires the university to be in an active course selection period.
4. Timetable retrieval requires the relevant timetable to be available in the university system.
5. Different universities may use different login procedures, including additional authentication.
6. Please use a reasonable polling interval to avoid placing excessive request load on university systems.
7. Course selection results, account issues, and system restrictions are subject to the actual results and rules of the university's official system.

1. 本項目依賴各學校教務系統的具體網頁結構。
2. 如果學校升級教務系統、修改頁面元素或接口，原有自動化邏輯可能失效。
3. 「全自动选课工具」功能需要學校當前處於開放選課階段。
4. 課表下載功能需要學校已經開放對應學年學期的課表查詢。
5. 不同學校的登錄流程可能不同，例如部分系統可能存在二次身份認證。
6. 請合理設置輪詢間隔，避免對學校教務系統造成過高請求壓力。
7. 使用本程序產生的選課結果、賬號問題及學校系統限制等，應以學校官方系統實際結果為準。

---

# 👨‍💻 Author

**Thomas Wan**

Personal Website:

https://thomaswan.uk

---

Personal interest project, mainly developed for learning:

個人興趣項目，主要用於學習：

- Python GUI development / Python GUI 開發
- Playwright browser automation / Playwright 瀏覽器自動化
- Web automation / Web 自動化
- Academic affairs system automation / 教務系統自動化
- Desktop application development / 桌面工具開發

Issues and Pull Requests are welcome.

⭐ 如果這個項目對你有幫助，歡迎 Star。

歡迎提交 Issues 或 Pull Request，共同改進項目。

---

## 📋 Release Notes | 版本更新

### v1.2

**What's New:**
- Store *Configuration Data* in the Windows user's *AppData directory* instead of the program directory.
- Fixed known issues and improved stability.

**版本 v1.2：**
- 將 *用戶配置文件（Configuration Data）* 存儲至 *Windows 用戶的 AppData 目錄*，而非程序目錄。
- 修復已知問題並提升程序穩定性。

### v1.1

**What's New:**
- Added support for *Guangzhou (Canton) University (GZHU)*.
- Fixed known issues and improved stability.

**版本 v1.1：**
- 新增支持 *广州大学（GZHU）*。
- 修復已知問題並提升程序穩定性。

### v1.0

**Initial Release:**
- Supports *Guangzhou (Canton) Private Hualian University*.
- Supports *Microsoft Windows*.
- Supports *Chromium-based browsers and Firefox*.

**版本 v1.0：**
- 僅支持 *广州私立华联大学（HLU）*。
- 僅支持 *Microsoft Windows*。
- 僅支持 *Chromium 系瀏覽器及 Firefox 瀏覽器*。

***Thank you for using!***  
***感謝您的使用！***
