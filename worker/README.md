# ICS / CalDAV Storage Worker v3.0

本服務原本位於獨立 `calendar` 倉庫；程式碼、網站和部署配置現已整合到 [`thomaswan564/multi-tool`](https://github.com/thomaswan564/multi-tool)。Cloudflare Worker 仍使用 `calendar` 服務名稱及 `calendar.thomaswan.uk` 網址。Worker 在根路徑和一般網頁路徑透明代理 GitHub Pages 的 `multi-tool` 網站，因此網址列保持 `calendar.thomaswan.uk`；`/api/`、`/caldav/` 和 `.ics` 路徑仍由 Worker 本身處理。舊倉庫已停止維護，所有後續功能以 multi-tool 為準。

## 自行部署

### 1. 準備 GitHub 存儲倉庫

Worker 將帳戶資料寫入 `users.json`，日曆寫入 `ics/`。可用自己的 multi-tool 分支，也可建立專用存儲倉庫（建議 Private），並修改 `wrangler.toml` 的 `GITHUB_OWNER`、`GITHUB_REPO`、`GITHUB_BRANCH`。建立 GitHub fine-grained PAT，只授權存儲倉庫並開啟 **Contents: Read and write**；組織倉庫可能還需管理員批准。切勿把 PAT 寫進檔案、提交或貼到聊天。

> 當存儲倉庫是公開倉庫時，訪客 ICS 和 `users.json` 可被公開讀取。註冊用戶 ICS 是加密內容，但帳戶資料檔及 Git 歷史仍公開。要保持私密，請建立 Private 存儲倉庫並把 PAT 限定到該倉庫。

### 2. 登入 Cloudflare

安裝 Node.js LTS，在本目錄執行：

```bash
npx wrangler login
```

在瀏覽器完成 Cloudflare 授權。編輯 `wrangler.toml` 的 `[vars]`：填入 GitHub 存儲倉庫 owner/repo/branch、Worker 公開網址，以及 Pages 來源。`WEB_ORIGIN` 只填來源，例如 `https://yourname.github.io`，不要帶 `/multi-tool/` 路徑。自訂網域需先在 Cloudflare DNS 配置，再在 Worker 設定 Custom Domain。

### 3. 設置 Worker 密鑰並部署

以下命令會在終端互動提示中輸入密鑰，不要把密鑰放進命令參數或日誌。`ENCRYPTION_KEY` 和 `SESSION_SECRET` 請各自產生至少 32 bytes 的隨機值，安全備份並在重新部署時沿用；更換它們會影響現有帳戶或登入狀態。

```bash
npx wrangler secret put GITHUB_TOKEN
npx wrangler secret put ENCRYPTION_KEY
npx wrangler secret put SESSION_SECRET
# Turnstile widget 的 Secret key
npx wrangler secret put TURNSTILE_SECRET
# 自己記得住的管理員長口令（至少 12 字元）
npx wrangler secret put ADMIN_PASSWORD
npx wrangler deploy
```

`wrangler secret put` 會在終端要求輸入值；Turnstile Secret key 和管理員密碼只輸入提示，不要寫在命令列、檔案或聊天中。`wrangler.toml` 已配置公開 Site key 與允許網域 `calendar.thomaswan.uk`、`thomaswan564.github.io`；若 widget 尚未包含實際訪問網域，請在 Cloudflare Turnstile widget 的 Domains 補上。Siteverify 會核對回傳 action（`register`、`login`、`admin_login`）及 hostname。

部署前可執行測試：

```bash
node --test --test-isolation=none test/worker.test.mjs
```

部署後檢查 `https://你的Worker網域/health`，再用桌面程序註冊測試帳戶並上傳測試 ICS。GitHub Pages 部署方法見 [`web/README.md`](../web/README.md)，課程 ICS / CalDAV 客戶端配置見 [`v3.0/README.md`](../v3.0/README.md)。

### 環境變量

| 變量 | 必填 | 說明 |
| --- | --- | --- |
| `GITHUB_OWNER` / `GITHUB_REPO` | 是 | 存 `users.json` 與 `ics/` 的倉庫 |
| `GITHUB_TOKEN` | 是 | GitHub PAT（Contents read/write） |
| `GITHUB_BRANCH` | 否 | 本項目部署配置使用 `主要的` |
| `GITHUB_PATH` | 否 | 默認 `ics` |
| `ENCRYPTION_KEY` | 建議設置 | `users.json` 中用戶名/API key 的加密密鑰；務必固定 |
| `SESSION_SECRET` | 建議設置 | 驗證碼簽名密鑰 |
| `WEB_ORIGIN` | 否 | GitHub Pages 來源，逗號分隔 |
| `PUBLIC_BASE_URL` | 否 | 對外 Worker 地址 |
| `SCHOOLS` | 否 | 默認 `gzhu,hlu` |
| `TURNSTILE_SECRET` | 是 | Turnstile widget Secret key；註冊、一般登入及管理員登入共用 |
| `TURNSTILE_SITEKEY` | 是 | 公開 widget Site key，已寫入 `wrangler.toml` |
| `TURNSTILE_HOSTNAMES` | 是 | Siteverify 允許的 hostname 清單，已寫入 `wrangler.toml` |
| `ADMIN_PASSWORD` | 管理功能必填 | 管理員登入口令，至少 12 字元；只設為 Worker secret |
| `ADMIN_SESSION_SECRET` | 否 | 管理會話簽名密鑰；未設置時使用既有 `SESSION_SECRET` |
| `LOGIN_RATE_LIMITER` | 是 | Wrangler Rate Limiting binding；註冊、一般登入和管理員登入每分鐘最多 5 次/來源 IP（Cloudflare 區域內近似限速） |

## API 摘要

- `GET /api/captcha`、`POST /api/register`、`POST /api/login`
- `GET /api/me`、`GET /api/files`、`POST /api/upload`、`POST /api/files/delete`
- `POST /api/guest/upload`、`GET /api/guest/files`、`POST /api/guest/delete`
- 管理頁：[`https://calendar.thomaswan.uk/admin`](https://calendar.thomaswan.uk/admin)，使用 `admin` + Worker secret `ADMIN_PASSWORD`，並通過共用 Turnstile 驗證。登入會話有效 8 小時，僅存於頁面記憶體。
- 管理 API：`GET /api/admin/me`、`GET /api/admin/users`、`PATCH /api/admin/users/{username}`、`POST /api/admin/users/{username}/password`、`DELETE /api/admin/users/{username}`、`GET /api/admin/files`、`DELETE /api/admin/files/{id}`
- `GET /{school}/{apiKey}.ics`、`GET /guest/{school}/{studentId}.ics`
- CalDAV：`/caldav/{school}/{apiKey}/`，支援 `PROPFIND / REPORT / GET / PUT / DELETE`

完整約定見 [`../docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md)。

管理員功能包括查看帳戶與 ICS/CalDAV 文件、停用/恢復帳戶、重設帳戶密碼、刪除單一日曆文件，以及連同其 ICS/CalDAV 文件一起刪除帳戶。重設後用戶以新密碼登入；API key 不會重設。管理員帳戶 `admin` 不在一般用戶資料庫中；登入使用 `ADMIN_PASSWORD`、Turnstile 及 8 小時簽名會話。舊的 `ADMIN_API_KEY` 不再用於管理頁；確認新登入正常後可從 Worker secrets 移除舊值。

## 測試、加密與遷移

註冊用戶 ICS 使用 `CAL1.` + base64url(`IV || AES-256-GCM ciphertext || tag`)，密鑰由 API key 派生；CalDAV `PUT` 收到明文 ICS 後也會加密存儲。丟失 API key 就無法解密該日曆。訪客文件明文，路徑為 `ics/guest/{school}/{studentId}.ics`。

從舊 Worker 遷移時，保留舊 `ENCRYPTION_KEY` / `SESSION_SECRET` 和 `users.json`、`ics/` 數據，避免帳戶 key 或日曆失效。舊文件路徑仍有只讀兼容。
