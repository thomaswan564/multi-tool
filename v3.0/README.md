# 教務輔助工具 v3.0.2

v3.0 把主要功能放回桌面程序：生成 ICS、加密上傳、雲端文件列表/刪除、webcal 二維碼、CalDAV 顯示。網頁只保留註冊、登錄、查看 API。

## 主要變化

- **桌面程序**：`code-multi-tool-v3.0.py`
- **訪客模式**：不勾選「註冊用戶」，用學校 + 學號上傳明文 `ics/guest/{school}/{studentId}.ics`。
- **註冊模式**：勾選後填 API key，程序用 AES-256-GCM 加密，Worker 存 `ics/{school}/{api}.ics`。
- **雲端文件列表**：程序內「雲端文件列表...」可刷新、上傳本地 ICS、刪除、複製地址、顯示二維碼。
- **webcal 二維碼**：主界面「二維碼」或文件列表「顯示二維碼」生成 `webcal://` 訂閱碼。
- **CalDAV**：註冊用戶可複製 `/caldav/{school}/{api}/`。CalDAV 可供非 Apple 原生日曆客戶端（如 Thunderbird、Android DAVx⁵）使用；Apple 用戶可改用第三方 CalDAV App，或以 webcal/ICS 唯讀訂閱。
- **Apple 用戶提示**：程序會在 CalDAV 配置、訂閱二維碼及生成完成提示中說明原生 Apple 日曆不支援此服務的 CalDAV 連線，並提供替代方式。

## 安裝

```bash
cd v3.0
pip install -r requirements.txt
python -m playwright install
```

`qrcode` 已 vendored 到 `v3.0/_vendor`；若使用其他 Python 環境，建議 `pip install qrcode[pil]`。

## Windows EXE

到 [GitHub Releases](https://github.com/thomaswan564/multi-tool/releases/latest) 下載 `Multi-Tool-v3.0.2.exe`，無需另外安裝 Python。程序會使用電腦已安裝的 Chrome、Edge、Firefox 或 Brave 執行教務系統自動化；電腦需至少安裝其中一種瀏覽器。首次啟動若 Windows SmartScreen 顯示提示，請先確認檔案來自本倉庫的 Releases。

維護者可在 Windows 開發環境安裝 `requirements.txt` 與 PyInstaller，再執行 `./build_windows.ps1` 重建。打包後檔案位於 `dist/Multi-Tool-v3.0.2.exe`。

## v3.0.2

- 說明 Apple 原生日曆無法使用本服務的 CalDAV；Apple 用戶可選第三方 CalDAV App，或使用唯讀 webcal 訂閱。
- 在桌面程序的配置指引、二維碼說明和生成完成提示中加入兼容性提醒。

## 配置

複製 `cloud_config.example.json` 為 `cloud_config.json`（與程序同目錄）：

```json
{
  "cloud_base_url": "https://calendar.thomaswan.uk",
  "web_portal_url": "https://thomaswan564.github.io/multi-tool/",
  "github_owner": "thomaswan564",
  "github_repo": "multi-tool",
  "update_check": true
}
```

- `cloud_base_url`：Cloudflare Worker 地址。
- `web_portal_url`：GitHub Pages 網頁地址，供「網頁」按鈕打開註冊/登錄/查看 API。
- `github_owner` / `github_repo`：版本檢測用的 GitHub 倉庫；留空時會嘗試從 `web_portal_url` 的 `*.github.io/<repo>/` 推斷。
- `update_check`：設為 `false` 可關閉啟動時版本檢測。

## 版本檢測

程序啟動時（以及點擊「檢查更新」時）會查詢：

```
GET https://api.github.com/repos/{github_owner}/{github_repo}/releases/latest
```

- 比較 `tag_name` 與程式內 `APP_VERSION`（例如 `v3.0.2` > `3.0.1`）。
- 若遠端較新，彈窗提示「前往下載」，打開 Release 頁面或 `.exe` 資產連結。
- 未配置倉庫、沒有 Release、離線或 API 限流時只寫入日誌，不阻塞使用。
- GitHub Release tag 建議使用 `v3.0.0`、`v3.0.1` 這種格式；公開倉庫即可，無需 Token。
- 每次發佈新 exe 前，請同步修改程式頂部的 `APP_VERSION`（例如 `3.0.2`），否則新版本仍會被判定為舊版/同版。

## 使用流程

1. 選擇院校與「課程表獲取與 ICS 生成工具」。
2. 填學號/密碼，選擇課表 PDF 保存路徑與最早開課周的週一日期。
3. 雲端設定：
   - **訪客**：直接填學校 + 學號，無需 API key；文件明文。
   - **註冊用戶**：勾選「註冊用戶」，填入網頁註冊得到的 API key，點「驗證」。
4. 勾選「生成後自動上傳」，點「開始 Start」。
5. 生成後：
   - 點「二維碼」顯示 `webcal://` 訂閱碼。
   - 點「雲端文件列表...」查看/刪除/上傳。
   - 註冊用戶點「配置 CalDAV…」查看說明。非 Apple 原生日曆客戶端可用 CalDAV；Apple 用戶請使用第三方 CalDAV App，或選 webcal 唯讀訂閱。

### 手動配置 ICS / CalDAV

在桌面程序完成註冊並驗證 API key 後：

- **ICS 訂閱（唯讀）**：複製「訂閱網址」，或掃描「二維碼」；若客戶端要求訂閱地址，可將 `https://.../{school}/{apiKey}.ics` 的協議改為 `webcal://`。訪客模式使用 `.../guest/{school}/{studentId}.ics`，不需帳密，但連結持有人可讀取日曆。
- **CalDAV（可同步修改）**：此服務的 CalDAV 不適用於 Apple 原生日曆及 iOS 系統 CalDAV 帳戶。非 Apple 用戶可在 Thunderbird「新建日曆 → 網絡上 → CalDAV」或 Android DAVx⁵ 中手動配置；Apple 用戶可使用支援 CalDAV 的第三方 App。服務器地址填程序顯示的 `/caldav/{school}/{apiKey}/`，**用戶名和密碼均填 API key**。
- **Apple 日曆 webcal 訂閱（唯讀）**：使用程序的「訂閱網址」或掃描 webcal 二維碼添加訂閱。訂閱只能讀取課程，無法由日曆端修改課程。
- API key 等同密碼，請勿分享；註冊用戶日曆加密密鑰由 API key 派生，遺失後無法解密既有資料。

## 文件說明

| 文件 | 用途 |
| --- | --- |
| `code-multi-tool-v3.0.py` | 桌面主程序（Tkinter + Playwright） |
| `cloud_client.py` | 加密、Worker API、URL、二維碼，可獨立測試 |
| `tests/test_app_functions.py` | 課表解析、週次與 ICS 功能單元測試 |
| `tests/test_cloud_client.py` | 加密/URL/API 客戶端單元測試 |
| `_vendor/qrcode` | vendored 二維碼庫 |
| `cloud_config.example.json` | 雲端/網頁地址配置模板 |

## 測試

```bash
python -m unittest discover -s tests -v
```

## 注意

- API key 同時是上傳憑據、CalDAV 用戶名/密碼和日曆解密密鑰；丟失後無法解密舊文件。
- 訪客文件是明文，任何知道 URL 的人都能讀取；敏感課表請註冊。
- 加密上傳依賴 `cryptography`；二維碼顯示依賴 `Pillow`。
