# GitHub Pages 網頁

只包含三個功能：**註冊、登錄、查看 API**。沒有文件上傳/列表/刪除，那些都在桌面程序內。

## 文件

| 文件 | 用途 |
| --- | --- |
| `index.html` | 登錄 |
| `register.html` | 註冊（數學題或 Turnstile） |
| `account.html` | 查看/複製 API key、webcal 訂閱、CalDAV 地址 |
| `config.js` | `apiBase` 指向 Cloudflare Worker |
| `style.css` / `app.js` | 樣式與共享邏輯 |

## 部署

### 方式 A：GitHub Actions（推薦）

倉庫已包含 `.github/workflows/pages.yml`。在 GitHub：

1. Settings → Pages → Build and deployment → Source 選 **GitHub Actions**。
2. push 到 `multi-tool` 倉庫的 `主要的` 分支後，workflow 會把 `web/` 發佈為 Pages。

正式頁面：`https://thomaswan564.github.io/multi-tool/`。

### 方式 B：倉庫根目錄 / docs

若不用 Actions，把 `web/` 內所有文件複製到 Pages 發佈目錄（倉庫根目錄或 `/docs`）。

## 配置

1. 編輯 `web/config.js`：

```js
window.CALENDAR_CONFIG = {
  apiBase: "https://calendar.thomaswan.uk",
  siteName: "课程表日历"
};
```

2. 在 Worker 的 `WEB_ORIGIN` 填入 Pages 來源，例如：

```
WEB_ORIGIN = "https://yourname.github.io"
```

若使用自訂域名，填自訂域名；多個來源用逗號分隔。

## 說明

- 登錄後把 API key 存在瀏覽器 `localStorage`，請求以 `Authorization: Bearer` 發送，不依賴 Cookie。
- 註冊成功後立即顯示 API key；登錄也會返回同一個 key，方便「查看 API」。
- 換瀏覽器或清除站點數據後，用用戶名/密碼重新登錄即可再次看到 API key。
