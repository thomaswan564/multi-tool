# ICS / CalDAV Storage Worker v3.0

本服務原本位於獨立 `calendar` 倉庫；程式碼、網站和部署配置現已整合到 [`thomaswan564/multi-tool`](https://github.com/thomaswan564/multi-tool)。Cloudflare Worker 仍使用 `calendar` 服務名稱及 `calendar.thomaswan.uk` 網址。舊倉庫已停止維護，所有後續功能以 multi-tool 為準。

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
# 管理員金鑰：使用密碼管理器產生至少 32 字元的唯一隨機值
npx wrangler secret put ADMIN_API_KEY
# 如有配置 Turnstile，另外設置 TURNSTILE_SECRET
npx wrangler deploy
```

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
| `TURNSTILE_SECRET` | 否 | 配置後註冊使用 Turnstile |
| `ADMIN_API_KEY` | 管理功能必填 | 管理員專用高熵憑證，至少 32 字元；不等同 GitHub PAT，勿放進程式碼 |

## API 摘要

- `GET /api/captcha`、`POST /api/register`、`POST /api/login`
- `GET /api/me`、`GET /api/files`、`POST /api/upload`、`POST /api/files/delete`
- `POST /api/guest/upload`、`GET /api/guest/files`、`POST /api/guest/delete`
- 管理頁：[`https://thomaswan564.github.io/multi-tool/admin.html`](https://thomaswan564.github.io/multi-tool/admin.html)，需在 Worker 設定 `ADMIN_API_KEY`
- 管理 API：`GET /api/admin/me`、`GET /api/admin/users`、`PATCH /api/admin/users/{username}`、`POST /api/admin/users/{username}/password`、`DELETE /api/admin/users/{username}`、`GET /api/admin/files`、`DELETE /api/admin/files/{id}`
- `GET /{school}/{apiKey}.ics`、`GET /guest/{school}/{studentId}.ics`
- CalDAV：`/caldav/{school}/{apiKey}/`，支援 `PROPFIND / REPORT / GET / PUT / DELETE`

完整約定見 [`../docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md)。

管理員功能包括查看帳戶與 ICS/CalDAV 文件、停用/恢復帳戶、重設帳戶密碼、刪除單一日曆文件，以及連同其 ICS/CalDAV 文件一起刪除帳戶。重設後用戶以新密碼登入；API key 不會重設。管理員身份由 Worker secret `ADMIN_API_KEY` 提供，並非公開 `users.json` 內可註冊的帳戶。登入管理頁後金鑰只留在當前頁面的記憶體，退出或關頁即清除。需更換管理金鑰時再次執行 `npx wrangler secret put ADMIN_API_KEY` 並重新輸入新值。

## 測試、加密與遷移

註冊用戶 ICS 使用 `CAL1.` + base64url(`IV || AES-256-GCM ciphertext || tag`)，密鑰由 API key 派生；CalDAV `PUT` 收到明文 ICS 後也會加密存儲。丟失 API key 就無法解密該日曆。訪客文件明文，路徑為 `ics/guest/{school}/{studentId}.ics`。

從舊 Worker 遷移時，保留舊 `ENCRYPTION_KEY` / `SESSION_SECRET` 和 `users.json`、`ics/` 數據，避免帳戶 key 或日曆失效。舊文件路徑仍有只讀兼容。
