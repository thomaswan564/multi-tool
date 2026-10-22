/* Shared helpers for the static GitHub Pages UI. */
(function () {
  const CONFIG = window.CALENDAR_CONFIG || {};
  const API_BASE = String(CONFIG.apiBase || window.location.origin).replace(/\/+$/, "");
  const KEY_STORAGE = "calendar_api_key";
  const USER_STORAGE = "calendar_username";
  const SCHOOL_STORAGE = "calendar_school";

  function getApiKey() {
    try {
      return localStorage.getItem(KEY_STORAGE) || "";
    } catch {
      return "";
    }
  }

  function getUsername() {
    try {
      return localStorage.getItem(USER_STORAGE) || "";
    } catch {
      return "";
    }
  }

  function getSchool() {
    try {
      return localStorage.getItem(SCHOOL_STORAGE) || "";
    } catch {
      return "";
    }
  }

  function saveSession(data) {
    try {
      localStorage.setItem(KEY_STORAGE, data.apiKey || "");
      localStorage.setItem(USER_STORAGE, data.username || "");
      localStorage.setItem(SCHOOL_STORAGE, data.school || "");
    } catch {
      /* localStorage can be disabled; the page still works for this session. */
    }
  }

  function clearSession() {
    try {
      localStorage.removeItem(KEY_STORAGE);
      localStorage.removeItem(USER_STORAGE);
      localStorage.removeItem(SCHOOL_STORAGE);
    } catch {
      /* ignore */
    }
  }

  async function apiRequest(path, options) {
    const opts = options || {};
    const headers = Object.assign({}, opts.headers || {});
    let body = opts.body;

    if (body && typeof body === "object" && !(body instanceof FormData)) {
      headers["Content-Type"] = "application/json";
      body = JSON.stringify(body);
    }
    if (opts.auth) {
      const key = getApiKey();
      if (!key) {
        throw new Error("尚未登录，请先登录。");
      }
      headers.Authorization = "Bearer " + key;
    }

    let response;
    try {
      response = await fetch(API_BASE + path, {
        method: opts.method || "GET",
        headers,
        body
      });
    } catch (error) {
      throw new Error("无法连接服务器：" + error.message);
    }

    let data;
    try {
      data = await response.json();
    } catch {
      throw new Error("服务器返回异常（HTTP " + response.status + "）");
    }

    if (!response.ok || data.success === false) {
      throw new Error(data.error || "请求失败（HTTP " + response.status + "）");
    }
    return data;
  }

  function showMessage(element, message, isError) {
    if (!element) return;
    element.textContent = message || "";
    element.style.display = message ? "block" : "none";
    element.className = isError ? "error" : "ok";
  }

  async function copyText(text) {
    const value = String(text || "");
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(value);
        return true;
      }
    } catch {
      /* fall through */
    }

    const textarea = document.createElement("textarea");
    textarea.value = value;
    textarea.style.position = "fixed";
    textarea.style.left = "-9999px";
    document.body.appendChild(textarea);
    textarea.select();
    try {
      document.execCommand("copy");
      return true;
    } catch {
      return false;
    } finally {
      document.body.removeChild(textarea);
    }
  }

  function bindCopy(button, valueGetter) {
    if (!button) return;
    button.addEventListener("click", async function () {
      const ok = await copyText(valueGetter());
      const original = button.textContent;
      button.textContent = ok ? "已复制 ✓" : "复制失败";
      setTimeout(function () {
        button.textContent = original;
      }, 1200);
    });
  }

  function requireLogin() {
    if (!getApiKey()) {
      window.location.replace("index.html");
      return "";
    }
    return getApiKey();
  }

  function redirectIfLoggedIn() {
    if (getApiKey()) {
      window.location.replace("account.html");
    }
  }

  window.CalendarWeb = {
    API_BASE,
    getApiKey,
    getUsername,
    getSchool,
    saveSession,
    clearSession,
    apiRequest,
    showMessage,
    copyText,
    bindCopy,
    requireLogin,
    redirectIfLoggedIn
  };
})();
