# v3.0 架構與接口約定

本文件是 v3.0 重構的「單一事實來源」。桌面程序、Worker、靜態網頁都必須遵守這裡的路徑、加密與接口約定。

## 1. 職責邊界

| 組件 | 職責 |
| --- | --- |
| `v3.0/code-multi-tool-v3.0.py` | 桌面主程序：登入教務系統、下載 PDF、生成 ICS、加密上傳、雲端文件列表/刪除、webcal 二維碼、CalDAV 資訊 |
| `worker/src/index.js` | 僅後端：註冊/登入、API key 校驗、GitHub 讀寫、ICS 分發、CalDAV（PROPFIND/REPORT/GET/PUT/DELETE） |
| `web/`（GitHub Pages） | 僅三個頁面：註冊、登入、查看 API |
| GitHub 倉庫 | 保存 `users.json` 與 `ics/` 下的日曆文件 |

Worker **不再返回任何 HTML**。網頁 HTML 全部放在 `web/`，由 GitHub Pages 托管。桌面程序不再依賴網頁完成上傳、列表、刪除。

## 2. GitHub 存儲佈局

```
users.json                                  # 帳戶（加密後的用戶名/API key + 哈希）
ics/
  guest/
    {school}/
      {studentId}.ics                       # 訪客：明文
  {school}/
    {apiKey}.ics                            # 註冊用戶主文件：AES-GCM 密文
    {apiKey}/                               # CalDAV 額外資源（可選）
      {uuid}.ics                            # 客戶端新增事件：AES-GCM 密文
```

- `{school}`：`gzhu`、`hlu` 等小寫代碼。
- `{studentId}`：學號，僅允許 `[A-Za-z0-9_-]{1,40}`。
- `{apiKey}`：24 字節隨機數的 48 位小寫十六進制。
- 訪客文件 URL：`https://<worker>/guest/{school}/{studentId}.ics`。
- 註冊文件 URL：`https://<worker>/{school}/{apiKey}.ics`。
- CalDAV 集合：`https://<worker>/caldav/{school}/{apiKey}/`。
- 主文件 `{apiKey}.ics` 由桌面程序上傳；CalDAV 客戶端新增事件時，Worker 會把額外資源存放在 `{apiKey}/` 目錄，主文件保持不變。
- 訂閱 feed `/{school}/{apiKey}.ics` 會合併主文件與 `{apiKey}/` 下的額外 VEVENT，讓 webcal 訂閱也看得到 CalDAV 新增事件。

## 3. 加密約定（註冊用戶）

- 算法：AES-256-GCM。
- 密鑰：`SHA-256(apiKey UTF-8)` 的 32 字節原始摘要。
- IV：12 字節隨機數。
- 密文：WebCrypto/Python `AESGCM` 的 `ciphertext || tag`。
- 文件文本格式：`CAL1.` + base64url(`IV || ciphertext || tag`)，base64url 無 padding。
- 上傳：桌面程序先加密再上傳；Worker 會解密驗證內容確實是 ICS。若收到明文 ICS，Worker 也會加密後再寫入（兼容 CalDAV PUT 與舊客戶端）。
- 分發：Worker 收到 `GET /{school}/{apiKey}.ics` 時，用路徑中的 API key 派生密鑰並解密，返回 `text/calendar`。
- CalDAV：讀取時解密；`PUT` 時加密後寫入。

> API key 同時是 URL 路徑、CalDAV 用戶名/密碼和加密密鑰。丟失 API key 等於丟失該日曆的訪問與解密能力，請讓用戶妥善保存。

## 4. HTTP API（JSON）

所有響應均為 JSON（除 `.ics` 與 CalDAV XML）。CORS 由 `WEB_ORIGIN` 控制；使用 `Authorization: Bearer <apiKey>`，不依賴 Cookie。

### 公共

| 方法 | 路徑 | 說明 |
| --- | --- | --- |
| GET | `/health` | `{ok:true}` |
| GET | `/api/captcha` | 返回 `{question, token}`；若配置 Turnstile，返回 `{turnstileSiteKey}` |
| POST | `/api/register` | `{username,password,school,captchaToken,captchaAnswer,cf-turnstile-response}` → `{success, username, school, apiKey, subscriptionUrl, webcalUrl, caldavUrl}` |
| POST | `/api/login` | `{username,password}` → 同上（含 API key，供「查看 API」） |

### 註冊用戶（Bearer）

| 方法 | 路徑 | 說明 |
| --- | --- | --- |
| GET | `/api/me` | 帳戶資訊與三個 URL |
| GET | `/api/files` | `{success, files:[{filename,kind,size,sha,url,webcal,caldav}]}` |
| POST | `/api/upload` | multipart `file`；接受密文（`CAL1.`）或明文 ICS；寫入 `ics/{school}/{apiKey}.ics` |
| POST | `/api/files/delete` | `{filename}`；只允許刪除自己的 `{apiKey}.ics` |
| DELETE | `/api/files/{filename}` | 同上 |
| POST | `/api/account/password` | `{oldPassword,newPassword}` |
| POST | `/api/account/delete` | 刪除帳戶與自己的 ICS |

### 訪客（無 API key）

| 方法 | 路徑 | 說明 |
| --- | --- | --- |
| POST | `/api/guest/upload` | multipart `file` + `school` + `studentId`；寫入 `ics/guest/{school}/{studentId}.ics`（明文） |
| GET | `/api/guest/files?school=&studentId=` | 返回該訪客的文件（0 或 1 個） |
| POST | `/api/guest/delete` | `{school,studentId}`；刪除該訪客的文件 |

### 訂閱分發

| 方法 | 路徑 | 說明 |
| --- | --- | --- |
| GET | `/guest/{school}/{studentId}.ics` | 返回訪客明文 ICS |
| GET | `/{school}/{apiKey}.ics` | 解密後返回註冊用戶 ICS（合併主文件與 CalDAV 額外資源） |
| GET | 其他 `/**/*.ics` | 兼容舊路徑：若內容本身是明文 ICS 則直接返回 |

## 5. CalDAV（完整讀寫）

- 發現：`GET /.well-known/caldav` → `301 /caldav/`。
- 認證：HTTP Basic，用戶名或密碼任一等於 API key；也接受 `Authorization: Bearer <apiKey>`。
- 集合：`/caldav/{school}/{apiKey}/`。
- Principal：`/caldav/{school}/{apiKey}/principal/`。
- 資源：`/caldav/{school}/{apiKey}/{apiKey}.ics`。
- 方法：`OPTIONS`、`PROPFIND`（Depth 0/1）、`REPORT`（calendar-query / calendar-multiget）、`GET`、`HEAD`、`PUT`、`DELETE`、`MKCOL`、`MKCALENDAR`、`PROPPATCH`。
- 主資源為 `{apiKey}.ics`；客戶端新增其他 `.ics` 資源時，存放在 `ics/{school}/{apiKey}/{filename}.ics`，同樣加密。
- `PUT` 可更新主資源或建立/更新額外資源；`DELETE` 刪除對應資源。
- Apple 日曆設定：帳戶類型「其他 CalDAV」，伺服器地址填集合 URL，用戶名與密碼都填 API key。

## 6. 環境變量（Worker）

| 變量 | 必填 | 說明 |
| --- | --- | --- |
| `GITHUB_OWNER` | 是 | GitHub 用戶/組織 |
| `GITHUB_REPO` | 是 | 倉庫名 |
| `GITHUB_TOKEN` | 是 | 具備 contents:read/write 的 PAT |
| `GITHUB_BRANCH` | 否 | 已整合到 `multi-tool` 時為 `主要的` |
| `GITHUB_PATH` | 否 | 默認 `ics` |
| `ENCRYPTION_KEY` | 否 | `users.json` 中用戶名/API key 的加密密鑰；默認回退 `SESSION_SECRET`/`GITHUB_TOKEN` |
| `SESSION_SECRET` | 否 | 驗證碼簽名；默認回退 `ENCRYPTION_KEY` |
| `WEB_ORIGIN` | 否 | 允許的網頁來源，逗號分隔；默認 `*` |
| `PUBLIC_BASE_URL` | 否 | 對外 Worker 位址；默認請求 `origin` |
| `SCHOOLS` | 否 | 允許的學校代碼，默認 `gzhu,hlu` |
| `TURNSTILE_SECRET` / `TURNSTILE_SITEKEY` | 否 | 配置後註冊使用 Cloudflare Turnstile |
| `USERS_JSON` | 否 | 首次部署時 `users.json` 的種子 |

## 7. 兼容性

- 繼續讀取舊 `users.json`（明文 `username/password/apiKey` 會自動遷移為哈希+密文）。
- 舊的 `ics/{school}/{username}/...ics` 只讀：`GET` 若內容為明文 ICS 仍可訂閱；新程序不再寫入該佈局。

## 8. 客戶端版本檢測

桌面程序內 `APP_VERSION` 是本地版本（例如 `3.0.0`）。啟動時與手動點擊「檢查更新」時：

1. 從 `cloud_config.json` 讀取 `github_owner` / `github_repo`；未填時嘗試從 `web_portal_url` 的 `https://<owner>.github.io/<repo>/` 推斷。
2. `GET https://api.github.com/repos/{owner}/{repo}/releases/latest`。
3. 解析 `tag_name`（如 `v3.0.1`）並與 `APP_VERSION` 按數字段比較。
4. 若遠端較新，提示使用者並打開 `html_url` 或 `.exe` 資產下載地址。
5. 未配置倉庫、404（沒有 Release）、網絡失敗或限流時僅記錄日誌，不阻塞程式。

Release 應使用 `v3.0.0` / `v3.0.1` 格式；公開倉庫不需要 Token。`update_check=false` 可關閉啟動檢測。
