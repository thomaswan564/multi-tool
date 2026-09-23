教務系統輔助工具 (Campus Educational Administration Assistant)
一個基於 Python + Playwright + Tkinter 開發的桌面圖形化教務系統輔助工具。目前支持自動化登錄教務系統、自動下載個人課程表 PDF 以及全自動自主選課（搶課）與課程本地緩存功能。

🌟 核心功能
智能瀏覽器檢測 (System Browser Auto-Detection)

自動掃描系統內已安裝的瀏覽器（Google Chrome、Microsoft Edge、Brave、Firefox、Safari）。

優先調用本地已安裝的瀏覽器運行，避免下載與調用 Playwright 額外內置的 Chromium。

課程表自動獲取與下載 (PDF Schedule Downloader)

自動登錄教務系統並導航至課表查詢頁面。

一鍵觸發導出並將課程表 PDF 自動保存至指定目錄（默認桌面）。

全自動自主選課 / 搶課 (Auto Course Selection)

一鍵抓取與緩存：快速獲取當前可選課程列表，並自動保存至本地 (cached_courses.json)，下次啟動無需重新抓取。

靈活配置：支持自定義輪詢時間間隔（0秒起高頻輪詢）。

智能過濾：可勾選自動跳過無餘量課程、自動跳過未開放課程。

多目標選擇：支持勾選多門目標課程按順序輪詢搶課。

友好的 GUI 界面 (Tkinter Desktop UI)

提供直觀的操作界面、日誌實時顯示、滾動複選框列表以及前台/後台瀏覽器顯示切換。

🛠️ 技術棧
GUI 框架：tkinter / ttk

網絡與頁面自動化：playwright (async_playwright)

異步處理：asyncio + threading

數據緩存：json

📦 安裝與準備工作
1. 克隆倉庫
Bash
git clone https://github.com/thomaswan564/multi-tool.git
cd multi-tool
2. 安裝依賴環境
請確保您的電腦已安裝 Python 3.8+。使用 pip 安裝必要的 Python 模組：

Bash
pip install playwright
註：本工具會直接優先調用您電腦中已安裝的 Chrome 或 Edge 瀏覽器，因此無需強制執行 playwright install chromium。

🚀 使用說明
執行程序
在命令行中運行主腳本：

Bash
python multi-tool.py
操作流程
A. 課表下載功能
在界面上方選擇院校與【課程表自動獲取下載工具】。

輸入您的學號與密碼。

選擇 PDF 保存路徑（默認桌面）。

點擊 【開始 Start】，等待系統自動導出下載。

B. 全自動選課 / 搶課功能
選擇【全自動選課工具】。

輸入學號與密碼。

點擊 【獲取課程列表】（初次使用時），系統會自動登錄並下載當前可報名的課程清單，並自動保存至本地。

在可選課程列表中勾選您想要搶購/報名的目標課程。

設定輪詢間隔（如 0 或 0.5 秒）以及過濾選項。

點擊 【開始 Start】 開始自動化輪詢搶課。

📁 項目結構
Plaintext
.
├── multi-tool.py       # 主程序源碼 (GUI 與自動化邏輯)
├── cached_courses.json    # 運行後自動生成的本地課程緩存檔案
└── README.md              # 項目說明文件
⚠️ 注意事項與免責聲明
個人用途：本項目僅供自動化技術學習與個人學術研究使用，請勿用於商業用途或大規模高頻刷新打擾教務系統服務器。

網絡環境：搶課效果取決於個人網絡狀態與教務系統服務器響應速度。

瀏覽器依賴：請確保您的電腦中已安裝 Google Chrome 或 Microsoft Edge 瀏覽器。

歡迎點Star！⭐
