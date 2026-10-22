/**
 * Calendar Worker v3.0
 *
 * Backend/API and transparent GitHub Pages proxy:
 *   - register / login / view API key
 *   - guest & registered file upload/list/delete
 *   - GitHub storage
 *   - ICS feed
 *   - CalDAV (PROPFIND / REPORT / GET / PUT / DELETE)
 *
 * Storage:
 *   users.json
 *   ics/guest/{school}/{studentId}.ics        (plaintext)
 *   ics/{school}/{apiKey}.ics                (AES-GCM ciphertext, "CAL1." + base64url)
 *
 * Crypto:
 *   key = SHA-256(apiKey), AES-256-GCM, 12-byte IV
 *   payload = "CAL1." + base64url(IV || ciphertext || tag)
 */

const USERS_PATH = "users.json";
const CAL_PREFIX = "CAL1.";
const MAX_UPLOAD_BYTES = 5 * 1024 * 1024;
const UNSAFE_FILENAME = /[\\/:*?"<>|]/;

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const method = request.method.toUpperCase();
    const cors = buildCors(request, env);

    if (url.pathname === "/health") {
      return json({ ok: true }, 200, cors);
    }

    if (method === "OPTIONS") {
      return new Response(null, { status: 204, headers: cors });
    }

    const isBackendPath = url.pathname === "/.well-known/caldav"
      || url.pathname === "/caldav"
      || url.pathname.startsWith("/caldav/")
      || url.pathname.startsWith("/api/")
      || ((method === "GET" || method === "HEAD") && url.pathname.toLowerCase().endsWith(".ics"));

    if (isBackendPath && (!env.GITHUB_OWNER || !env.GITHUB_REPO || !env.GITHUB_TOKEN)) {
      return json({ success: false, error: "Worker 配置不完整（GITHUB_*）" }, 500, cors);
    }

    try {
      if (url.pathname === "/.well-known/caldav") {
        return new Response(null, {
          status: 301,
          headers: { ...cors, Location: "/caldav/" }
        });
      }

      if (url.pathname === "/caldav" || url.pathname.startsWith("/caldav/")) {
        return await handleCalDav(request, env, url, cors);
      }

      if (url.pathname.startsWith("/api/")) {
        return await handleApi(request, env, url, cors);
      }

      if ((method === "GET" || method === "HEAD") && url.pathname.toLowerCase().endsWith(".ics")) {
        return await serveCalendar(request, env, url, cors);
      }

      if (method === "GET" || method === "HEAD") {
        return await proxyGitHubPages(request, url);
      }

      return json({ success: false, error: "Not Found" }, 404, cors);
    } catch (error) {
      return json(
        { success: false, error: error && error.message ? error.message : String(error) },
        500,
        cors
      );
    }
  }
};

async function proxyGitHubPages(request, url) {
  const upstreamPath = url.pathname === "/"
    ? "/multi-tool/"
    : url.pathname === "/admin"
      ? "/multi-tool/admin.html"
      : `/multi-tool${url.pathname}`;
  const pagesUrl = new URL(
    upstreamPath,
    "https://thomaswan564.github.io"
  );
  pagesUrl.search = url.search;

  const headers = new Headers(request.headers);
  headers.delete("authorization");
  headers.delete("cookie");
  headers.delete("host");
  headers.delete("origin");

  return fetch(new Request(pagesUrl, {
    method: request.method,
    headers,
    redirect: "manual"
  }));
}

/* ========================================================================== */
/* API                                                                        */
/* ========================================================================== */

async function handleApi(request, env, url, cors) {
  const path = url.pathname;
  const method = request.method.toUpperCase();
  const config = getConfig(env, url);

  if (path === "/api/admin" || path.startsWith("/api/admin/")) {
    return handleAdminApi(request, env, url, config, cors);
  }

  if (path === "/api/captcha" && method === "GET") {
    if (!env.TURNSTILE_SECRET || !env.TURNSTILE_SITEKEY || !env.TURNSTILE_HOSTNAMES) {
      return json({ success: false, error: "人机验证尚未配置" }, 503, cors);
    }
    return json({ success: true, turnstileSiteKey: env.TURNSTILE_SITEKEY }, 200, cors);
  }

  if (path === "/api/register" && method === "POST") {
    return registerUser(request, env, config, cors);
  }

  if (path === "/api/login" && method === "POST") {
    return loginUser(request, env, config, cors);
  }

  if (path === "/api/guest/upload" && method === "POST") {
    return guestUpload(request, env, config, cors);
  }

  if (path === "/api/guest/files" && method === "GET") {
    return guestList(env, url, config, cors);
  }

  if (path === "/api/guest/delete" && method === "POST") {
    return guestDelete(request, env, config, cors);
  }

  /* From here on an API key is required. */
  const auth = await authenticate(request, env);
  if (!auth) {
    return json({ success: false, error: "未登录或 API key 无效" }, 401, cors);
  }

  const { users, user, apiKey } = auth;
  const school = sanitizeSchool(user.school);
  if (!school) {
    return json({ success: false, error: "账户未绑定学校" }, 400, cors);
  }

  if (path === "/api/me" && method === "GET") {
    return json(await userPayload(user, apiKey, config), 200, cors);
  }

  if (path === "/api/files" && method === "GET") {
    return listRegistered(env, user, apiKey, school, config, cors);
  }

  if (path === "/api/upload" && method === "POST") {
    return uploadRegistered(request, env, user, apiKey, school, config, cors);
  }

  if (path === "/api/files/delete" && method === "POST") {
    const body = await safeJson(request);
    return deleteRegistered(env, apiKey, school, body.filename, config, cors);
  }

  if (path.startsWith("/api/files/") && method === "DELETE") {
    const filename = decodeURIComponent(path.slice("/api/files/".length));
    return deleteRegistered(env, apiKey, school, filename, config, cors);
  }

  if (path === "/api/account/password" && method === "POST") {
    const body = await safeJson(request);
    const oldPassword = String(body.oldPassword || "");
    const newPassword = String(body.newPassword || "");
    if (user.passwordHash !== (await sha256Hex(oldPassword))) {
      return json({ success: false, error: "原密码不正确" }, 400, cors);
    }
    if (newPassword.length < 4) {
      return json({ success: false, error: "新密码至少 4 位" }, 400, cors);
    }
    user.passwordHash = await sha256Hex(newPassword);
    await saveUsers(env, users);
    return json({ success: true }, 200, cors);
  }

  if (path === "/api/account/delete" && method === "POST") {
    const filePath = registeredPath(config, school, apiKey);
    await deleteFile(env, filePath);
    const nextUsers = users.filter((item) => item.apiKeyHash !== user.apiKeyHash);
    await saveUsers(env, nextUsers);
    return json({ success: true }, 200, cors);
  }

  return json({ success: false, error: "Not Found" }, 404, cors);
}

/* ========================================================================== */
/* Admin API: password + Turnstile, protected by a short-lived signed session. */
/* ========================================================================== */

async function handleAdminApi(request, env, url, config, cors) {
  const method = request.method.toUpperCase();
  if (method === "POST" && url.pathname === "/api/admin/login") {
    return adminLogin(request, env, cors);
  }
  if (!env.ADMIN_PASSWORD || String(env.ADMIN_PASSWORD).length < 12 || !adminSessionSecret(env)) {
    return json({ success: false, error: "管理員密碼服務尚未配置" }, 503, cors);
  }
  const supplied = (request.headers.get("Authorization") || "").match(/^Bearer\s+(.+)$/i)?.[1]?.trim() || "";
  const session = await verifyAdminSession(supplied, adminSessionSecret(env));
  if (!session) return json({ success: false, error: "管理員登入已過期，請重新登入" }, 401, cors);
  if (method === "GET" && url.pathname === "/api/admin/me") {
    return json({ success: true, username: "admin", role: "admin", permissions: ["users.read", "users.update", "users.delete", "files.read", "files.delete"] }, 200, cors);
  }
  if (method === "GET" && url.pathname === "/api/admin/users") {
    const users = await loadUsers(env);
    const result = await Promise.all(users.map(async (user) => ({
      username: await decryptSecret(user.usernameEncrypted || "", config.encryptionKey).catch(() => "(無法解密)"),
      school: user.school || "", disabled: Boolean(user.disabled), createdAt: user.createdAt || ""
    })));
    return json({ success: true, users: result }, 200, cors);
  }
  if (method === "GET" && url.pathname === "/api/admin/files") {
    const files = await listAdminFiles(env, config);
    return json({ success: true, files: files.map(({ path, ownerId, ...file }) => file) }, 200, cors);
  }
  const userMatch = url.pathname.match(/^\/api\/admin\/users\/([^/]+)(?:\/(password))?$/);
  if (userMatch) {
    const username = decodeURIComponent(userMatch[1]);
    const users = await loadUsers(env);
    const target = await findAdminUser(users, username, config);
    if (!target) return json({ success: false, error: "找不到用戶" }, 404, cors);
    if (userMatch[2] === "password" && method === "POST") {
      const body = await safeJson(request);
      const password = String(body.newPassword || "");
      if (password.length < 8) return json({ success: false, error: "新密碼至少 8 位" }, 400, cors);
      target.passwordHash = await sha256Hex(password);
      await saveUsers(env, users);
      return json({ success: true }, 200, cors);
    }
    if (method === "PATCH") {
      const body = await safeJson(request);
      if (typeof body.disabled !== "boolean") return json({ success: false, error: "disabled 必須是布林值" }, 400, cors);
      target.disabled = body.disabled;
      await saveUsers(env, users);
      return json({ success: true, disabled: target.disabled }, 200, cors);
    }
    if (method === "DELETE") {
      const files = await listAdminFiles(env, config);
      for (const file of files.filter((item) => item.ownerId === target.apiKeyHash)) await deleteFile(env, file.path);
      await saveUsers(env, users.filter((item) => item !== target));
      return json({ success: true, deletedFiles: files.filter((item) => item.ownerId === target.apiKeyHash).length }, 200, cors);
    }
  }
  const fileMatch = url.pathname.match(/^\/api\/admin\/files\/([a-f0-9]{64})$/i);
  if (fileMatch && method === "DELETE") {
    const files = await listAdminFiles(env, config);
    const target = files.find((file) => file.id === fileMatch[1].toLowerCase());
    if (!target) return json({ success: false, error: "找不到文件" }, 404, cors);
    await deleteFile(env, target.path);
    return json({ success: true }, 200, cors);
  }
  return json({ success: false, error: "Not Found" }, 404, cors);
}

async function adminLogin(request, env, cors) {
  if (!env.ADMIN_PASSWORD || String(env.ADMIN_PASSWORD).length < 12 || !adminSessionSecret(env)
    || !env.TURNSTILE_SECRET || !env.TURNSTILE_SITEKEY || !env.TURNSTILE_HOSTNAMES) {
    return json({ success: false, error: "管理員登入尚未配置" }, 503, cors);
  }
  const ip = request.headers.get("CF-Connecting-IP") || "unknown";
  const rate = await enforceLoginRateLimit(env, ip, "admin");
  if (!rate) return json({ success: false, error: "嘗試次數過多，請稍後再試" }, 429, cors);
  const body = await safeJson(request);
  const captchaOk = await verifyTurnstile(
    String(body["cf-turnstile-response"] || ""), env.TURNSTILE_SECRET, ip, "admin_login", request.url, env
  );
  if (!captchaOk) return json({ success: false, error: "人機驗證失敗，請重試" }, 400, cors);
  const password = String(body.password || "");
  const sessionSecret = adminSessionSecret(env);
  const [expected, supplied] = await Promise.all([sha256Bytes(env.ADMIN_PASSWORD), sha256Bytes(password)]);
  if (password.length !== String(env.ADMIN_PASSWORD).length || !timingSafeEqualBytes(expected, supplied)) {
    return json({ success: false, error: "管理員密碼錯誤" }, 401, cors);
  }
  const expiresAt = Date.now() + 8 * 60 * 60 * 1000;
  const data = bytesToBase64Url(new TextEncoder().encode(JSON.stringify({ sub: "admin", exp: expiresAt })));
  const token = `${data}.${await hmacSign(`admin-session:${data}`, sessionSecret)}`;
  return json({ success: true, username: "admin", token, expiresAt }, 200, cors);
}

async function verifyAdminSession(token, secret) {
  const parts = String(token || "").split(".");
  if (parts.length !== 2 || !timingSafeEqual(parts[1], await hmacSign(`admin-session:${parts[0]}`, secret))) return false;
  try {
    const session = JSON.parse(new TextDecoder().decode(base64UrlToBytes(parts[0])));
    return session.sub === "admin" && Number.isFinite(session.exp) && Date.now() < session.exp;
  } catch { return false; }
}

function adminSessionSecret(env) {
  const secret = String(env.ADMIN_SESSION_SECRET || env.SESSION_SECRET || "");
  return secret.length >= 32 ? secret : "";
}

async function enforceLoginRateLimit(env, ip, scope) {
  if (!env.LOGIN_RATE_LIMITER) return false;
  const { success } = await env.LOGIN_RATE_LIMITER.limit({ key: `${scope}:${ip}` });
  return success;
}

function timingSafeEqualBytes(left, right) {
  if (left.length !== right.length) return false;
  let diff = 0;
  for (let i = 0; i < left.length; i += 1) diff |= left[i] ^ right[i];
  return diff === 0;
}

async function sha256Bytes(value) {
  return new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(String(value))));
}

async function findAdminUser(users, username, config) {
  const clean = sanitizeUsername(username);
  if (!clean) return null;
  for (const user of users) {
    const name = await decryptSecret(user.usernameEncrypted || "", config.encryptionKey).catch(() => "");
    if (name.toLowerCase() === clean.toLowerCase()) return user;
  }
  return null;
}

async function listAdminFiles(env, config) {
  const users = await loadUsers(env);
  const usersByKey = new Map(users.map((user) => [user.apiKeyHash, user]));
  const usernameByKey = new Map();
  for (const user of users) usernameByKey.set(user.apiKeyHash, await decryptSecret(user.usernameEncrypted || "", config.encryptionKey).catch(() => "(無法解密)"));
  const output = [];
  const add = async (entry, school, kind, apiKey = "", owner = null) => {
    if (entry.type !== "file" || !entry.name.toLowerCase().endsWith(".ics")) return;
    const id = await sha256Hex(entry.path);
    output.push({ id, path: entry.path, ownerId: owner?.apiKeyHash || "", filename: entry.name, school, kind,
      owner: owner ? (usernameByKey.get(owner.apiKeyHash) || "user") : "訪客", size: entry.size || 0 });
  };
  for (const school of config.schools) {
    const schoolPath = `${config.basePath}/${school}`;
    for (const entry of await readDir(env, schoolPath)) {
      if (entry.type === "file") {
        const key = entry.name.replace(/\.ics$/i, "");
        const owner = usersByKey.get(await sha256Hex(key));
        await add(entry, school, "registered", key, owner || null);
      } else if (entry.type === "dir") {
        const owner = usersByKey.get(await sha256Hex(entry.name));
        for (const resource of await readDir(env, entry.path)) await add(resource, school, "caldav", entry.name, owner || null);
      }
    }
  }
  for (const school of config.schools) {
    for (const entry of await readDir(env, `${config.basePath}/guest/${school}`)) await add(entry, school, "guest");
  }
  return output;
}

/* ========================================================================== */
/* Register / login                                                           */
/* ========================================================================== */

async function registerUser(request, env, config, cors) {
  if (!env.TURNSTILE_SECRET || !env.TURNSTILE_SITEKEY || !env.TURNSTILE_HOSTNAMES) {
    return json({ success: false, error: "人机验证尚未配置" }, 503, cors);
  }
  const ip = request.headers.get("CF-Connecting-IP") || "unknown";
  if (!await enforceLoginRateLimit(env, ip, "register")) {
    return json({ success: false, error: "操作过于频繁，请稍后再试" }, 429, cors);
  }
  const body = await safeJson(request);
  const username = sanitizeUsername(body.username);
  const password = String(body.password || "");
  const school = sanitizeSchool(body.school);

  if (!username) {
    return json(
      { success: false, error: "用户名只能包含字母、数字、下划线和连字符（1-40 位）" },
      400,
      cors
    );
  }
  if (username.toLowerCase() === "admin") {
    return json({ success: false, error: "admin 是保留的管理員帳戶名稱" }, 400, cors);
  }
  if (password.length < 4) {
    return json({ success: false, error: "密码至少 4 位" }, 400, cors);
  }
  if (!school || (config.schools.length && !config.schools.includes(school))) {
    return json({ success: false, error: "请选择有效的高校" }, 400, cors);
  }

  const captchaOk = await verifyTurnstile(
    String(body["cf-turnstile-response"] || ""), env.TURNSTILE_SECRET, ip, "register", request.url, env, request.headers.get("Origin")
  );

  if (!captchaOk) {
    return json({ success: false, error: "人机验证失败，请重试" }, 400, cors);
  }

  const users = await loadUsers(env);
  const usernameHash = await sha256Hex(username);

  if (users.some((item) => item.usernameHash === usernameHash)) {
    return json({ success: false, error: "该用户名已存在" }, 400, cors);
  }

  const apiKey = generateApiKey();
  const user = {
    usernameHash,
    usernameEncrypted: await encryptSecret(username, config.encryptionKey),
    passwordHash: await sha256Hex(password),
    apiKeyHash: await sha256Hex(apiKey),
    apiKeyEncrypted: await encryptSecret(apiKey, config.encryptionKey),
    school,
    role: "user",
    disabled: false,
    createdAt: new Date().toISOString()
  };

  users.push(user);
  await saveUsers(env, users);

  return json(await userPayload(user, apiKey, config), 200, cors);
}

async function loginUser(request, env, config, cors) {
  if (!env.TURNSTILE_SECRET || !env.TURNSTILE_SITEKEY || !env.TURNSTILE_HOSTNAMES) {
    return json({ success: false, error: "人机验证尚未配置" }, 503, cors);
  }
  const ip = request.headers.get("CF-Connecting-IP") || "unknown";
  if (!await enforceLoginRateLimit(env, ip, "login")) {
    return json({ success: false, error: "登录尝试过于频繁，请稍后再试" }, 429, cors);
  }
  const body = await safeJson(request);
  const username = String(body.username || "").trim();
  const password = String(body.password || "");
  if (!username || !password) {
    return json({ success: false, error: "请输入用户名和密码" }, 400, cors);
  }
  const captchaOk = await verifyTurnstile(
    String(body["cf-turnstile-response"] || ""), env.TURNSTILE_SECRET, ip, "login", request.url, env, request.headers.get("Origin")
  );
  if (!captchaOk) return json({ success: false, error: "人机验证失败，请重试" }, 400, cors);

  const users = await loadUsers(env);
  const usernameHash = await sha256Hex(username);
  const passwordHash = await sha256Hex(password);
  const user = users.find(
    (item) => item.usernameHash === usernameHash && item.passwordHash === passwordHash && !item.disabled
  );

  if (!user) {
    return json({ success: false, error: "用户名或密码错误" }, 401, cors);
  }

  let apiKey = "";
  if (user.apiKeyEncrypted) {
    try {
      apiKey = await decryptSecret(user.apiKeyEncrypted, config.encryptionKey);
    } catch {
      apiKey = "";
    }
  }

  if (!apiKey) {
    apiKey = generateApiKey();
    user.apiKeyHash = await sha256Hex(apiKey);
    user.apiKeyEncrypted = await encryptSecret(apiKey, config.encryptionKey);
    await saveUsers(env, users);
  }

  return json(await userPayload(user, apiKey, config), 200, cors);
}

async function userPayload(user, apiKey, config) {
  const school = sanitizeSchool(user.school);
  let username = "";
  if (user.usernameEncrypted) {
    try {
      username = await decryptSecret(user.usernameEncrypted, config.encryptionKey);
    } catch {
      username = "";
    }
  }

  const subscriptionUrl = `${config.baseUrl}/${school}/${apiKey}.ics`;
  const caldavUrl = `${config.baseUrl}/caldav/${school}/${apiKey}/`;

  return {
    success: true,
    username: username || "user",
    school,
    apiKey,
    subscriptionUrl,
    webcalUrl: toWebcal(subscriptionUrl),
    caldavUrl
  };
}

/* ========================================================================== */
/* Registered files                                                           */
/* ========================================================================== */

async function listRegistered(env, user, apiKey, school, config, cors) {
  const path = registeredPath(config, school, apiKey);
  const file = await readFile(env, path);
  const subscriptionUrl = `${config.baseUrl}/${school}/${apiKey}.ics`;
  const caldavUrl = `${config.baseUrl}/caldav/${school}/${apiKey}/`;

  if (!file) {
    return json({ success: true, files: [], subscriptionUrl, webcalUrl: toWebcal(subscriptionUrl), caldavUrl }, 200, cors);
  }

  return json(
    {
      success: true,
      files: [
        {
          filename: `${apiKey}.ics`,
          kind: "registered",
          size: file.size,
          sha: file.sha,
          url: subscriptionUrl,
          webcal: toWebcal(subscriptionUrl),
          caldav: caldavUrl,
          encrypted: file.text.startsWith(CAL_PREFIX)
        }
      ],
      subscriptionUrl,
      webcalUrl: toWebcal(subscriptionUrl),
      caldavUrl
    },
    200,
    cors
  );
}

async function uploadRegistered(request, env, user, apiKey, school, config, cors) {
  const form = await request.formData();
  const file = form.get("file");

  if (!file || typeof file.arrayBuffer !== "function") {
    return json({ success: false, error: "没有上传文件" }, 400, cors);
  }
  if (file.size > MAX_UPLOAD_BYTES) {
    return json({ success: false, error: "文件不能超过 5 MB" }, 400, cors);
  }

  const raw = new TextDecoder("utf-8").decode(new Uint8Array(await file.arrayBuffer())).trim();
  if (!raw) {
    return json({ success: false, error: "文件为空" }, 400, cors);
  }

  let stored;
  if (raw.startsWith(CAL_PREFIX)) {
    const plain = await decryptIcs(raw, apiKey).catch(() => "");
    if (!isIcs(plain)) {
      return json({ success: false, error: "密文无法解密或内容不是有效 ICS" }, 400, cors);
    }
    stored = raw;
  } else if (isIcs(raw)) {
    stored = await encryptIcs(raw, apiKey);
  } else {
    return json({ success: false, error: "只允许上传 .ics 文件" }, 400, cors);
  }

  const path = registeredPath(config, school, apiKey);
  await writeFile(env, path, stored, `Update calendar ${school}/${apiKey}.ics`);

  const subscriptionUrl = `${config.baseUrl}/${school}/${apiKey}.ics`;
  const caldavUrl = `${config.baseUrl}/caldav/${school}/${apiKey}/`;

  return json(
    {
      success: true,
      filename: `${apiKey}.ics`,
      kind: "registered",
      size: new TextEncoder().encode(stored).length,
      url: subscriptionUrl,
      webcal: toWebcal(subscriptionUrl),
      caldav: caldavUrl,
      encrypted: true
    },
    200,
    cors
  );
}

async function deleteRegistered(env, apiKey, school, filename, config, cors) {
  const clean = sanitizeFilename(filename);
  const expected = `${apiKey}.ics`;

  if (!clean || clean !== expected) {
    return json({ success: false, error: "只能删除自己的日历文件" }, 403, cors);
  }

  const path = registeredPath(config, school, apiKey);
  const removed = await deleteFile(env, path);
  if (!removed) {
    return json({ success: false, error: "文件不存在" }, 404, cors);
  }
  return json({ success: true, filename: expected }, 200, cors);
}

/* ========================================================================== */
/* Guest files                                                                */
/* ========================================================================== */

async function guestUpload(request, env, config, cors) {
  const form = await request.formData();
  const school = sanitizeSchool(form.get("school"));
  const studentId = sanitizeStudentId(form.get("studentId"));
  const file = form.get("file");

  if (!school || (config.schools.length && !config.schools.includes(school))) {
    return json({ success: false, error: "请选择有效的高校" }, 400, cors);
  }
  if (!studentId) {
    return json({ success: false, error: "学号格式无效" }, 400, cors);
  }
  if (!file || typeof file.arrayBuffer !== "function") {
    return json({ success: false, error: "没有上传文件" }, 400, cors);
  }
  if (file.size > MAX_UPLOAD_BYTES) {
    return json({ success: false, error: "文件不能超过 5 MB" }, 400, cors);
  }

  const text = new TextDecoder("utf-8").decode(new Uint8Array(await file.arrayBuffer())).trim();
  if (!isIcs(text)) {
    return json({ success: false, error: "只允许上传有效的 ICS 日历" }, 400, cors);
  }

  const path = guestPath(config, school, studentId);
  await writeFile(env, path, text, `Update guest calendar ${school}/${studentId}.ics`);

  const subscriptionUrl = `${config.baseUrl}/guest/${school}/${studentId}.ics`;
  return json(
    {
      success: true,
      filename: `${studentId}.ics`,
      kind: "guest",
      size: new TextEncoder().encode(text).length,
      url: subscriptionUrl,
      webcal: toWebcal(subscriptionUrl),
      encrypted: false
    },
    200,
    cors
  );
}

async function guestList(env, url, config, cors) {
  const school = sanitizeSchool(url.searchParams.get("school"));
  const studentId = sanitizeStudentId(url.searchParams.get("studentId"));

  if (!school || !studentId) {
    return json({ success: false, error: "参数不完整" }, 400, cors);
  }

  const path = guestPath(config, school, studentId);
  const file = await readFile(env, path);
  const subscriptionUrl = `${config.baseUrl}/guest/${school}/${studentId}.ics`;

  if (!file) {
    return json({ success: true, files: [], subscriptionUrl, webcalUrl: toWebcal(subscriptionUrl) }, 200, cors);
  }

  return json(
    {
      success: true,
      files: [
        {
          filename: `${studentId}.ics`,
          kind: "guest",
          size: file.size,
          sha: file.sha,
          url: subscriptionUrl,
          webcal: toWebcal(subscriptionUrl),
          encrypted: false
        }
      ],
      subscriptionUrl,
      webcalUrl: toWebcal(subscriptionUrl)
    },
    200,
    cors
  );
}

async function guestDelete(request, env, config, cors) {
  const body = await safeJson(request);
  const school = sanitizeSchool(body.school);
  const studentId = sanitizeStudentId(body.studentId);

  if (!school || !studentId) {
    return json({ success: false, error: "参数不完整" }, 400, cors);
  }

  const removed = await deleteFile(env, guestPath(config, school, studentId));
  if (!removed) {
    return json({ success: false, error: "文件不存在" }, 404, cors);
  }
  return json({ success: true, filename: `${studentId}.ics` }, 200, cors);
}

/* ========================================================================== */
/* ICS feed                                                                   */
/* ========================================================================== */

async function serveCalendar(request, env, url, cors) {
  const method = request.method.toUpperCase();
  const parts = url.pathname.split("/").filter(Boolean).map((part) => decodeURIComponent(part));
  const config = getConfig(env, url);

  if (parts.length === 0) {
    return new Response("Invalid calendar URL.", { status: 400, headers: cors });
  }

  let path = "";
  let filename = parts[parts.length - 1];
  let resources = null;

  if (parts[0] === "guest") {
    if (parts.length !== 3) {
      return new Response("Invalid guest calendar URL.", { status: 400, headers: cors });
    }
    const school = sanitizeSchool(parts[1]);
    const studentId = sanitizeStudentId(filename.replace(/\.ics$/i, ""));
    if (!school || !studentId || !filename.toLowerCase().endsWith(".ics")) {
      return new Response("Invalid guest calendar URL.", { status: 400, headers: cors });
    }
    filename = `${studentId}.ics`;
    path = guestPath(config, school, studentId);
  } else if (parts.length === 2 && filename.toLowerCase().endsWith(".ics")) {
    const school = sanitizeSchool(parts[0]);
    const apiKey = sanitizeApiKey(filename.replace(/\.ics$/i, ""));
    if (!school || !apiKey) {
      return new Response("Invalid calendar URL.", { status: 400, headers: cors });
    }
    const feedUsers = await loadUsers(env);
    const feedKeyHash = await sha256Hex(apiKey);
    const feedOwner = feedUsers.find((item) => item.school === school && item.apiKeyHash === feedKeyHash);
    if (!feedOwner || feedOwner.disabled) {
      return new Response("Calendar not found.", { status: 404, headers: cors });
    }
    filename = `${apiKey}.ics`;
    resources = await loadCalDavResources(env, config, school, apiKey);
  } else {
    // Legacy paths (ics/{school}/{username}/{file}.ics) - read-only plaintext.
    path = `${config.basePath}/${parts.map((part) => sanitizeFolder(part)).join("/")}`;
    if (parts.some((part) => !sanitizeFolder(part))) {
      return new Response("Invalid calendar URL.", { status: 400, headers: cors });
    }
  }

  let calendarText = "";
  if (resources) {
    const apiKey = filename.replace(/\.ics$/i, "");
    const texts = [];
    for (const resource of resources) {
      let plain = await decryptIcs(resource.text, apiKey).catch(() => "");
      if (!isIcs(plain) && isIcs(resource.text)) {
        plain = resource.text;
      }
      if (isIcs(plain)) texts.push(plain);
    }
    if (texts.length === 0) {
      return new Response("Calendar is encrypted or invalid.", { status: 404, headers: cors });
    }
    calendarText = mergeIcsCalendars(texts);
  } else {
    const file = await readFile(env, path);
    if (!file) {
      return new Response("Calendar not found.", { status: 404, headers: cors });
    }
    if (!isIcs(file.text)) {
      return new Response("Calendar is not a valid ICS.", { status: 404, headers: cors });
    }
    calendarText = file.text;
  }

  if (method === "HEAD") {
    return new Response(null, { status: 200, headers: calendarHeaders(cors, filename) });
  }
  return calendarResponse(calendarText, filename, cors);
}

function mergeIcsCalendars(texts) {
  const clean = texts.filter(isIcs);
  if (clean.length === 1) {
    return clean[0];
  }

  let header = "";
  let footer = "\r\nEND:VCALENDAR\r\n";
  const events = [];

  clean.forEach((text, index) => {
    const normalized = String(text).replace(/\r\n|\n|\r/g, "\r\n");
    const blocks = normalized.match(/BEGIN:VEVENT[\s\S]*?END:VEVENT/g) || [];
    events.push(...blocks);

    if (index === 0) {
      const firstEvent = normalized.indexOf("BEGIN:VEVENT");
      header = firstEvent >= 0 ? normalized.slice(0, firstEvent) : normalized.replace(/END:VCALENDAR\s*$/, "");
      const lastEventEnd = normalized.lastIndexOf("END:VEVENT");
      if (lastEventEnd >= 0) {
        footer = normalized.slice(lastEventEnd + "END:VEVENT".length);
      }
    }
  });

  return header + events.join("\r\n") + footer;
}

/* ========================================================================== */
/* CalDAV                                                                     */
/* ========================================================================== */

async function handleCalDav(request, env, url, cors) {
  const method = request.method.toUpperCase();
  const segments = url.pathname
    .replace(/^\/caldav\/?/, "")
    .split("/")
    .filter(Boolean)
    .map((part) => decodeURIComponent(part));

  if (method === "OPTIONS") {
    return new Response(null, {
      status: 200,
      headers: {
        ...cors,
        DAV: "1, 2, calendar-access",
        Allow: "OPTIONS, GET, HEAD, PUT, DELETE, PROPFIND, REPORT, MKCOL, MKCALENDAR, PROPPATCH",
        "MS-Author-Via": "DAV"
      }
    });
  }

  if (segments.length === 0) {
    return caldavRoot(request, env, cors);
  }

  const school = sanitizeSchool(segments[0]);
  const apiKey = sanitizeApiKey(segments[1]);
  if (!school || !apiKey) {
    return new Response("Not Found", { status: 404, headers: cors });
  }

  const creds = parseCalDavCredentials(request);
  if (!caldavAuthorized(creds, apiKey)) {
    return caldavUnauthorized(cors);
  }

  const users = await loadUsers(env);
  const apiHash = await sha256Hex(apiKey);
  const owner = users.find((item) => item.school === school && item.apiKeyHash === apiHash && !item.disabled);
  if (!owner) {
    return caldavUnauthorized(cors);
  }

  const config = getConfig(env, url);
  const resources = await loadCalDavResources(env, config, school, apiKey);
  const collectionHref = `/caldav/${encodeURIComponent(school)}/${encodeURIComponent(apiKey)}/`;
  const principalHref = `${collectionHref}principal/`;
  const rest = segments.slice(2);

  if (method === "PROPFIND") {
    if (rest[0] === "principal" && rest.length === 1) {
      return caldavPrincipalResponse(principalHref, collectionHref, cors);
    }
    if (rest.length === 0) {
      return caldavCollectionResponse(request, collectionHref, principalHref, resources, cors);
    }
    if (rest.length === 1) {
      const resource = resources.find((item) => item.filename === rest[0]);
      if (!resource) {
        return new Response("Not Found", { status: 404, headers: cors });
      }
      return caldavResourceResponse(resource, cors);
    }
    return new Response("Not Found", { status: 404, headers: cors });
  }

  if (method === "REPORT") {
    if (rest.length !== 0) {
      return new Response("Not Found", { status: 404, headers: cors });
    }
    return caldavReport(request, resources, apiKey, cors);
  }

  if (method === "GET" || method === "HEAD") {
    if (rest.length !== 1) {
      return new Response("Not Found", { status: 404, headers: cors });
    }
    const resource = resources.find((item) => item.filename === rest[0]);
    if (!resource) {
      return new Response("Not Found", { status: 404, headers: cors });
    }
    const plain = await decryptIcs(resource.text, apiKey).catch(() => "");
    if (!isIcs(plain)) {
      return new Response("Calendar is encrypted or invalid.", { status: 500, headers: cors });
    }
    if (method === "HEAD") {
      return new Response(null, {
        status: 200,
        headers: { ...calendarHeaders(cors, resource.filename), ETag: resource.etag }
      });
    }
    return calendarResponse(plain, resource.filename, cors, { ETag: resource.etag });
  }

  if (method === "PUT") {
    if (rest.length !== 1) {
      return new Response("Invalid calendar resource.", { status: 400, headers: cors });
    }

    const filename = sanitizeFilename(rest[0]);
    if (!filename) {
      return new Response("Invalid calendar filename.", { status: 400, headers: cors });
    }

    const bodyText = await request.text();
    if (!isIcs(bodyText)) {
      return new Response("Invalid ICS.", { status: 400, headers: cors });
    }

    const existing = resources.find((item) => item.filename === filename);
    const ifMatch = request.headers.get("If-Match");
    const ifNoneMatch = request.headers.get("If-None-Match");
    if (ifNoneMatch === "*" && existing) {
      return new Response("Precondition Failed", { status: 412, headers: cors });
    }
    if (ifMatch && ifMatch !== "*") {
      if (!existing || ifMatch !== existing.etag) {
        return new Response("Precondition Failed", { status: 412, headers: cors });
      }
    }

    const encrypted = await encryptIcs(bodyText, apiKey);
    const path = caldavResourcePath(config, school, apiKey, filename);
    await writeFile(env, path, encrypted, `Update calendar via CalDAV ${school}/${apiKey}/${filename}`);

    return new Response(null, {
      status: existing ? 204 : 201,
      headers: {
        ...cors,
        ETag: `"${await sha256Hex(encrypted)}"`,
        Location: `${collectionHref}${encodeURIComponent(filename)}`
      }
    });
  }

  if (method === "DELETE") {
    if (rest.length !== 1) {
      return new Response("Not Found", { status: 404, headers: cors });
    }
    const resource = resources.find((item) => item.filename === rest[0]);
    if (!resource) {
      return new Response("Not Found", { status: 404, headers: cors });
    }
    await deleteFile(env, resource.path);
    return new Response(null, { status: 204, headers: cors });
  }

  if (method === "MKCOL" || method === "MKCALENDAR") {
    return new Response(null, { status: 201, headers: cors });
  }

  if (method === "PROPPATCH") {
    return caldavPropPatch(collectionHref, cors);
  }

  return new Response("Method Not Allowed", {
    status: 405,
    headers: { ...cors, Allow: "OPTIONS, GET, HEAD, PUT, DELETE, PROPFIND, REPORT, MKCOL, MKCALENDAR, PROPPATCH" }
  });
}

async function loadCalDavResources(env, config, school, apiKey) {
  const mainName = `${apiKey}.ics`;
  const resources = [];

  const mainPath = registeredPath(config, school, apiKey);
  const main = await readFile(env, mainPath);
  if (main) {
    resources.push(await makeCalDavResource(mainName, mainPath, main, school, apiKey));
  }

  const directory = caldavDirectoryPath(config, school, apiKey);
  const entries = await readDir(env, directory);
  for (const entry of entries) {
    if (entry.name === mainName) continue;
    if (entry.type && entry.type !== "file") continue;
    const filename = sanitizeFilename(entry.name);
    if (!filename) continue;
    const path = `${directory}/${filename}`;
    const file = await readFile(env, path);
    if (!file) continue;
    resources.push(await makeCalDavResource(filename, path, file, school, apiKey));
  }

  return resources;
}

async function makeCalDavResource(filename, path, file, school, apiKey) {
  return {
    filename,
    path,
    text: file.text,
    size: file.size,
    etag: `"${await sha256Hex(file.text)}"`,
    href: `/caldav/${encodeURIComponent(school)}/${encodeURIComponent(apiKey)}/${encodeURIComponent(filename)}`
  };
}

function caldavDirectoryPath(config, school, apiKey) {
  return `${config.basePath}/${school}/${apiKey}`;
}

function caldavResourcePath(config, school, apiKey, filename) {
  if (filename === `${apiKey}.ics`) {
    return registeredPath(config, school, apiKey);
  }
  return `${caldavDirectoryPath(config, school, apiKey)}/${filename}`;
}

async function caldavRoot(request, env, cors) {
  if (request.method.toUpperCase() !== "PROPFIND") {
    return new Response("Method Not Allowed", { status: 405, headers: cors });
  }

  const creds = parseCalDavCredentials(request);
  let apiKey = "";
  if (creds) {
    if (creds.token) {
      apiKey = sanitizeApiKey(creds.token);
    } else {
      apiKey = sanitizeApiKey(creds.password) || sanitizeApiKey(creds.username);
    }
  }

  if (!apiKey) {
    return caldavUnauthorized(cors);
  }

  const users = await loadUsers(env);
  const apiHash = await sha256Hex(apiKey);
  const user = users.find((item) => item.apiKeyHash === apiHash);
  if (!user) {
    return caldavUnauthorized(cors);
  }

  const school = sanitizeSchool(user.school);
  const collectionHref = `/caldav/${encodeURIComponent(school)}/${encodeURIComponent(apiKey)}/`;
  const principalHref = `${collectionHref}principal/`;

  const xml = `<?xml version="1.0" encoding="utf-8"?>
<d:multistatus xmlns:d="DAV:" xmlns:cal="urn:ietf:params:xml:ns:caldav" xmlns:cs="http://calendarserver.org/ns/">
  <d:response>
    <d:href>/caldav/</d:href>
    <d:propstat>
      <d:prop>
        <d:current-user-principal><d:href>${xmlEscape(principalHref)}</d:href></d:current-user-principal>
        <cal:calendar-home-set><d:href>${xmlEscape(collectionHref)}</d:href></cal:calendar-home-set>
      </d:prop>
      <d:status>HTTP/1.1 200 OK</d:status>
    </d:propstat>
  </d:response>
</d:multistatus>`;

  return xmlResponse(xml, cors);
}

async function caldavCollectionResponse(request, collectionHref, principalHref, resources, cors) {
  const depth = (request.headers.get("Depth") || "0").trim();
  const ctag = await sha256Hex(
    resources
      .map((resource) => `${resource.filename}:${resource.etag}`)
      .sort()
      .join("|")
  );

  const collection = `
  <d:response>
    <d:href>${xmlEscape(collectionHref)}</d:href>
    <d:propstat>
      <d:prop>
        <d:resourcetype><d:collection/><cal:calendar/></d:resourcetype>
        <d:displayname>课程表</d:displayname>
        <cs:getctag>${xmlEscape(ctag)}</cs:getctag>
        <d:current-user-principal><d:href>${xmlEscape(principalHref)}</d:href></d:current-user-principal>
        <cal:calendar-home-set><d:href>${xmlEscape(collectionHref)}</d:href></cal:calendar-home-set>
        <cal:supported-calendar-component-set><cal:comp name="VEVENT"/></cal:supported-calendar-component-set>
        <cal:calendar-description>课程表订阅</cal:calendar-description>
        <d:current-user-privilege-set>
          <d:privilege><d:read/></d:privilege>
          <d:privilege><d:write/></d:privilege>
          <d:privilege><d:write-content/></d:privilege>
        </d:current-user-privilege-set>
      </d:prop>
      <d:status>HTTP/1.1 200 OK</d:status>
    </d:propstat>
  </d:response>`;

  const resourceXml = depth !== "0" ? resources.map(resourceResponseXml).join("") : "";

  return xmlResponse(
    `<?xml version="1.0" encoding="utf-8"?>
<d:multistatus xmlns:d="DAV:" xmlns:cal="urn:ietf:params:xml:ns:caldav" xmlns:cs="http://calendarserver.org/ns/">
${collection}${resourceXml}
</d:multistatus>`,
    cors,
    "1, 2, 3, calendar-access"
  );
}

async function caldavPrincipalResponse(principalHref, collectionHref, cors) {
  const xml = `<?xml version="1.0" encoding="utf-8"?>
<d:multistatus xmlns:d="DAV:" xmlns:cal="urn:ietf:params:xml:ns:caldav" xmlns:cs="http://calendarserver.org/ns/">
  <d:response>
    <d:href>${xmlEscape(principalHref)}</d:href>
    <d:propstat>
      <d:prop>
        <d:resourcetype><d:principal/></d:resourcetype>
        <d:displayname>Calendar</d:displayname>
        <d:current-user-principal><d:href>${xmlEscape(principalHref)}</d:href></d:current-user-principal>
        <cal:calendar-home-set><d:href>${xmlEscape(collectionHref)}</d:href></cal:calendar-home-set>
      </d:prop>
      <d:status>HTTP/1.1 200 OK</d:status>
    </d:propstat>
  </d:response>
</d:multistatus>`;

  return xmlResponse(xml, cors, "1, 2, 3, calendar-access");
}

function resourceResponseXml(resource) {
  return `
  <d:response>
    <d:href>${xmlEscape(resource.href)}</d:href>
    <d:propstat>
      <d:prop>
        <d:resourcetype/>
        <d:getetag>${xmlEscape(resource.etag)}</d:getetag>
        <d:getcontenttype>text/calendar; component=VEVENT</d:getcontenttype>
        <d:getcontentlength>${resource.size}</d:getcontentlength>
        <d:getlastmodified>${new Date().toUTCString()}</d:getlastmodified>
      </d:prop>
      <d:status>HTTP/1.1 200 OK</d:status>
    </d:propstat>
  </d:response>`;
}

async function caldavResourceResponse(resource, cors) {
  return xmlResponse(
    `<?xml version="1.0" encoding="utf-8"?>
<d:multistatus xmlns:d="DAV:" xmlns:cal="urn:ietf:params:xml:ns:caldav" xmlns:cs="http://calendarserver.org/ns/">
${resourceResponseXml(resource)}
</d:multistatus>`,
    cors,
    "1, 2, 3, calendar-access"
  );
}

async function caldavReport(request, resources, apiKey, cors) {
  const body = await request.text();
  const requestedHrefs = [...body.matchAll(/<(?:\w+:)?href[^>]*>([\s\S]*?)<\/(?:\w+:)?href>/gi)].map(
    (match) => decodeXml(match[1].trim())
  );
  const isMultiget = /calendar-multiget/i.test(body);
  const responses = [];

  for (const resource of resources) {
    if (isMultiget && requestedHrefs.length > 0) {
      const wanted = requestedHrefs.some(
        (href) => href.endsWith(resource.href) || href.endsWith(resource.filename)
      );
      if (!wanted) continue;
    }

    const plain = await decryptIcs(resource.text, apiKey).catch(() => "");
    if (!isIcs(plain)) continue;

    responses.push(`
  <d:response>
    <d:href>${xmlEscape(resource.href)}</d:href>
    <d:propstat>
      <d:prop>
        <d:getetag>${xmlEscape(resource.etag)}</d:getetag>
        <cal:calendar-data>${xmlEscape(plain)}</cal:calendar-data>
      </d:prop>
      <d:status>HTTP/1.1 200 OK</d:status>
    </d:propstat>
  </d:response>`);
  }

  return xmlResponse(
    `<?xml version="1.0" encoding="utf-8"?>
<d:multistatus xmlns:d="DAV:" xmlns:cal="urn:ietf:params:xml:ns:caldav" xmlns:cs="http://calendarserver.org/ns/">
${responses.join("")}
</d:multistatus>`,
    cors,
    "1, 2, 3, calendar-access"
  );
}

function caldavPropPatch(collectionHref, cors) {
  const xml = `<?xml version="1.0" encoding="utf-8"?>
<d:multistatus xmlns:d="DAV:">
  <d:response>
    <d:href>${xmlEscape(collectionHref)}</d:href>
    <d:propstat>
      <d:prop/>
      <d:status>HTTP/1.1 200 OK</d:status>
    </d:propstat>
  </d:response>
</d:multistatus>`;
  return xmlResponse(xml, cors, "1, 2, 3, calendar-access");
}

function parseCalDavCredentials(request) {
  const header = request.headers.get("Authorization") || "";
  if (/^Basic\s+/i.test(header)) {
    try {
      const raw = atob(header.replace(/^Basic\s+/i, "").trim());
      const index = raw.indexOf(":");
      return {
        username: index >= 0 ? raw.slice(0, index) : raw,
        password: index >= 0 ? raw.slice(index + 1) : ""
      };
    } catch {
      return null;
    }
  }
  if (/^Bearer\s+/i.test(header)) {
    return { token: header.replace(/^Bearer\s+/i, "").trim() };
  }
  return null;
}

function caldavAuthorized(creds, apiKey) {
  if (!creds) return false;
  if (creds.token) return creds.token === apiKey;
  return creds.username === apiKey || creds.password === apiKey;
}

function caldavUnauthorized(cors) {
  return new Response("Unauthorized", {
    status: 401,
    headers: {
      ...cors,
      "WWW-Authenticate": 'Basic realm="Calendar", charset="UTF-8"'
    }
  });
}

/* ========================================================================== */
/* Users                                                                      */
/* ========================================================================== */

async function loadUsers(env) {
  const config = getConfig(env, new URL("https://placeholder.invalid"));
  const existing = await readFile(env, USERS_PATH);
  let rawUsers = [];

  if (existing) {
    const parsed = JSON.parse(existing.text);
    rawUsers = Array.isArray(parsed) ? parsed : Array.isArray(parsed.users) ? parsed.users : [];
  } else if (env.USERS_JSON) {
    const parsed = JSON.parse(env.USERS_JSON);
    rawUsers = Array.isArray(parsed) ? parsed : Array.isArray(parsed.users) ? parsed.users : [];
  }

  const users = [];
  let changed = false;

  for (const raw of rawUsers) {
    const user = { ...raw };
    if (user.role === "admin") {
      changed = true;
      continue;
    }

    if (user.username && !user.usernameHash) {
      user.usernameHash = await sha256Hex(String(user.username));
      user.usernameEncrypted = await encryptSecret(String(user.username), config.encryptionKey);
      delete user.username;
      changed = true;
    }
    if (user.password && !user.passwordHash) {
      user.passwordHash = await sha256Hex(String(user.password));
      delete user.password;
      changed = true;
    }
    if (user.apiKey && !user.apiKeyHash) {
      user.apiKeyHash = await sha256Hex(String(user.apiKey));
      user.apiKeyEncrypted = await encryptSecret(String(user.apiKey), config.encryptionKey);
      delete user.apiKey;
      changed = true;
    }
    if (!user.usernameHash) {
      if (user.usernameEncrypted) {
        const name = await decryptSecret(user.usernameEncrypted, config.encryptionKey).catch(() => "");
        if (name) {
          user.usernameHash = await sha256Hex(name);
          changed = true;
        }
      }
    }
    if (!user.apiKeyHash) {
      const fresh = generateApiKey();
      user.apiKeyHash = await sha256Hex(fresh);
      user.apiKeyEncrypted = await encryptSecret(fresh, config.encryptionKey);
      changed = true;
    }
    if (!user.school) {
      const first = String(user.folder || "").split("/")[0];
      user.school = ["hlu", "gzhu"].includes(first) ? first : "";
      changed = true;
    }
    user.role = user.role || "user";
    delete user.folder;
    users.push(user);
  }

  if (changed) {
    await saveUsers(env, users);
  }
  return users;
}

async function saveUsers(env, users) {
  const clean = users
    .filter((user) => user.role !== "admin")
    .map((user) => ({
      usernameHash: user.usernameHash,
      usernameEncrypted: user.usernameEncrypted,
      passwordHash: user.passwordHash,
      apiKeyHash: user.apiKeyHash,
      apiKeyEncrypted: user.apiKeyEncrypted,
      school: user.school || "",
      role: "user",
      disabled: Boolean(user.disabled),
      createdAt: user.createdAt || ""
    }));

  await writeFile(env, USERS_PATH, JSON.stringify(clean, null, 2), "Update users.json");
}

async function authenticate(request, env) {
  const header = request.headers.get("Authorization") || "";
  const match = header.match(/^Bearer\s+(.+)$/i);
  if (!match) return null;

  const apiKey = sanitizeApiKey(match[1].trim());
  if (!apiKey) return null;

  const users = await loadUsers(env);
  const hash = await sha256Hex(apiKey);
  const user = users.find((item) => item.apiKeyHash === hash && !item.disabled);
  if (!user) return null;

  return { users, user, apiKey };
}

/* ========================================================================== */
/* Crypto                                                                     */
/* ========================================================================== */

async function sha256Hex(value) {
  const bytes = value instanceof Uint8Array ? value : new TextEncoder().encode(String(value));
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

async function deriveSecretKey(secret) {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(String(secret)));
  return crypto.subtle.importKey("raw", digest, { name: "AES-GCM" }, false, ["encrypt", "decrypt"]);
}

async function encryptSecret(plaintext, secret) {
  const key = await deriveSecretKey(secret);
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const encrypted = await crypto.subtle.encrypt(
    { name: "AES-GCM", iv },
    key,
    new TextEncoder().encode(String(plaintext))
  );
  const combined = new Uint8Array(iv.length + encrypted.byteLength);
  combined.set(iv);
  combined.set(new Uint8Array(encrypted), iv.length);
  return bytesToBase64Url(combined);
}

async function decryptSecret(ciphertext, secret) {
  const key = await deriveSecretKey(secret);
  const combined = base64UrlToBytes(String(ciphertext));
  const iv = combined.slice(0, 12);
  const data = combined.slice(12);
  const decrypted = await crypto.subtle.decrypt({ name: "AES-GCM", iv }, key, data);
  return new TextDecoder().decode(decrypted);
}

async function encryptIcs(plaintext, apiKey) {
  const key = await deriveSecretKey(apiKey);
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const encrypted = await crypto.subtle.encrypt(
    { name: "AES-GCM", iv },
    key,
    new TextEncoder().encode(plaintext)
  );
  const combined = new Uint8Array(iv.length + encrypted.byteLength);
  combined.set(iv);
  combined.set(new Uint8Array(encrypted), iv.length);
  return CAL_PREFIX + bytesToBase64Url(combined);
}

async function decryptIcs(payload, apiKey) {
  const raw = String(payload || "").trim();
  if (!raw.startsWith(CAL_PREFIX)) {
    return raw;
  }
  const key = await deriveSecretKey(apiKey);
  const combined = base64UrlToBytes(raw.slice(CAL_PREFIX.length));
  const iv = combined.slice(0, 12);
  const data = combined.slice(12);
  const decrypted = await crypto.subtle.decrypt({ name: "AES-GCM", iv }, key, data);
  return new TextDecoder().decode(decrypted);
}

function generateApiKey() {
  return [...crypto.getRandomValues(new Uint8Array(24))]
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
}

/* ========================================================================== */
/* Captcha                                                                    */
/* ========================================================================== */

async function generateCaptcha(secret) {
  const a = Math.floor(Math.random() * 9) + 1;
  const b = Math.floor(Math.random() * 9) + 1;
  const data = bytesToBase64Url(
    new TextEncoder().encode(JSON.stringify({ a, b, exp: Date.now() + 10 * 60 * 1000 }))
  );
  const signature = await hmacSign(data, secret);
  return { question: `${a} + ${b}`, token: `${data}.${signature}` };
}

async function verifyCaptcha(token, answer, secret) {
  const parts = String(token || "").split(".");
  if (parts.length !== 2) return false;

  const expected = await hmacSign(parts[0], secret);
  if (!timingSafeEqual(parts[1], expected)) return false;

  let payload;
  try {
    payload = JSON.parse(new TextDecoder().decode(base64UrlToBytes(parts[0])));
  } catch {
    return false;
  }
  if (!payload.exp || Date.now() > payload.exp) return false;
  return String(answer).trim() === String(payload.a + payload.b);
}

async function hmacSign(data, secret) {
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"]
  );
  const signature = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(data));
  return bytesToBase64Url(new Uint8Array(signature));
}

function timingSafeEqual(a, b) {
  const left = String(a || "");
  const right = String(b || "");
  if (left.length !== right.length) return false;
  let result = 0;
  for (let index = 0; index < left.length; index += 1) {
    result |= left.charCodeAt(index) ^ right.charCodeAt(index);
  }
  return result === 0;
}

async function verifyTurnstile(token, secret, remoteIp, expectedAction, requestUrl, env, origin = "") {
  if (!token || token.length > 2048 || !secret || !env.TURNSTILE_HOSTNAMES) return false;
  let expectedHostname;
  try { expectedHostname = new URL(origin || requestUrl).hostname.toLowerCase(); } catch { return false; }
  const allowedHostnames = String(env.TURNSTILE_HOSTNAMES).split(",").map((item) => item.trim().toLowerCase()).filter(Boolean);
  if (!allowedHostnames.includes(expectedHostname)) return false;
  const body = new URLSearchParams({ secret, response: token, remoteip: remoteIp });
  const response = await fetch("https://challenges.cloudflare.com/turnstile/v0/siteverify", {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body,
    signal: AbortSignal.timeout(10_000)
  });
  if (!response.ok) return false;
  const result = await response.json();
  return result.success === true
    && result.action === expectedAction
    && String(result.hostname || "").toLowerCase() === expectedHostname;
}

/* ========================================================================== */
/* GitHub                                                                     */
/* ========================================================================== */

async function readFile(env, path) {
  const response = await githubFetch(env, githubContentsUrl(env, path));
  if (response.status === 404) return null;
  if (!response.ok) {
    throw new Error(`GitHub 读取失败：${response.status} ${await response.text()}`);
  }
  const data = await response.json();
  if (Array.isArray(data)) {
    throw new Error(`${path} 是目录，不是文件`);
  }
  const text = decodeUtf8(base64ToBytes(data.content || ""));
  return { text, sha: data.sha, size: data.size ?? new TextEncoder().encode(text).length };
}

async function readDir(env, path) {
  const response = await githubFetch(env, githubContentsUrl(env, path));
  if (response.status === 404) return [];
  if (!response.ok) {
    throw new Error(`GitHub 列表失败：${response.status} ${await response.text()}`);
  }
  const data = await response.json();
  if (!Array.isArray(data)) return [];
  return data.map((item) => ({
    name: item.name,
    path: item.path,
    sha: item.sha,
    size: item.size,
    type: item.type
  }));
}

async function writeFile(env, path, text, message) {
  const existing = await readFile(env, path);
  const body = {
    message: message || `Update ${path}`,
    content: bytesToBase64(new TextEncoder().encode(String(text))),
    branch: env.GITHUB_BRANCH || "main"
  };
  if (existing) body.sha = existing.sha;

  const response = await githubFetch(env, githubContentsUrl(env, path, false), {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body)
  });

  if (!response.ok) {
    throw new Error(`GitHub 写入失败：${response.status} ${await response.text()}`);
  }
}

async function deleteFile(env, path) {
  const existing = await readFile(env, path);
  if (!existing) return false;

  const response = await githubFetch(env, githubContentsUrl(env, path, false), {
    method: "DELETE",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      message: `Delete ${path}`,
      sha: existing.sha,
      branch: env.GITHUB_BRANCH || "main"
    })
  });

  if (!response.ok) {
    throw new Error(`GitHub 删除失败：${response.status} ${await response.text()}`);
  }
  return true;
}

function githubContentsUrl(env, path, withRef = true) {
  const base = `https://api.github.com/repos/${encodeURIComponent(env.GITHUB_OWNER)}/${encodeURIComponent(
    env.GITHUB_REPO
  )}/contents/${String(path)
    .split("/")
    .filter(Boolean)
    .map((part) => encodeURIComponent(part))
    .join("/")}`;
  if (!withRef) return base;
  return `${base}?ref=${encodeURIComponent(env.GITHUB_BRANCH || "main")}`;
}

async function githubFetch(env, url, init = {}) {
  const headers = new Headers(init.headers || {});
  headers.set("Authorization", `Bearer ${env.GITHUB_TOKEN}`);
  headers.set("Accept", headers.get("Accept") || "application/vnd.github+json");
  headers.set("X-GitHub-Api-Version", "2022-11-28");
  headers.set("User-Agent", "calendar-worker-v3");
  return fetch(url, { ...init, headers });
}

/* ========================================================================== */
/* Paths / config                                                             */
/* ========================================================================== */

function getConfig(env, url) {
  return {
    baseUrl: String(env.PUBLIC_BASE_URL || url.origin).replace(/\/+$/, ""),
    basePath: String(env.GITHUB_PATH || "ics").replace(/^\/+|\/+$/g, "") || "ics",
    encryptionKey: env.ENCRYPTION_KEY || env.SESSION_SECRET || env.GITHUB_TOKEN,
    sessionSecret: env.SESSION_SECRET || env.ENCRYPTION_KEY || env.GITHUB_TOKEN,
    schools: String(env.SCHOOLS || "gzhu,hlu")
      .split(",")
      .map((item) => item.trim().toLowerCase())
      .filter(Boolean)
  };
}

function registeredPath(config, school, apiKey) {
  return `${config.basePath}/${school}/${apiKey}.ics`;
}

function guestPath(config, school, studentId) {
  return `${config.basePath}/guest/${school}/${studentId}.ics`;
}

/* ========================================================================== */
/* Validation                                                                 */
/* ========================================================================== */

function sanitizeUsername(value) {
  const clean = String(value || "").trim();
  return /^[a-zA-Z0-9_-]{1,40}$/.test(clean) ? clean : "";
}

function sanitizeSchool(value) {
  const clean = String(value || "").trim().toLowerCase();
  return /^[a-z0-9_-]{1,20}$/.test(clean) ? clean : "";
}

function sanitizeStudentId(value) {
  const clean = String(value || "").trim();
  return /^[a-zA-Z0-9_-]{1,40}$/.test(clean) ? clean : "";
}

function sanitizeApiKey(value) {
  const clean = String(value || "").trim();
  return /^[a-fA-F0-9]{16,128}$/.test(clean) ? clean.toLowerCase() : "";
}

function sanitizeFilename(value) {
  const name = String(value || "").split(/[\\/]/).pop().trim();
  if (!name || name === "." || name === ".." || UNSAFE_FILENAME.test(name)) return "";
  if (!name.toLowerCase().endsWith(".ics")) return "";
  return name;
}

function sanitizeFolder(value) {
  const clean = String(value || "").trim();
  return /^[a-zA-Z0-9_.-]{1,100}$/.test(clean) && clean !== "." && clean !== ".." ? clean : "";
}

/* ========================================================================== */
/* HTTP / encoding helpers                                                    */
/* ========================================================================== */

function json(data, status, cors) {
  return new Response(JSON.stringify(data), {
    status,
    headers: {
      ...cors,
      "Content-Type": "application/json; charset=utf-8",
      "Cache-Control": "no-store"
    }
  });
}

function xmlResponse(xml, cors, dav = "1, 2, calendar-access") {
  return new Response(xml, {
    status: 207,
    headers: {
      ...cors,
      "Content-Type": "application/xml; charset=utf-8",
      "Cache-Control": "no-store",
      DAV: dav
    }
  });
}

function calendarHeaders(cors, filename) {
  return {
    ...cors,
    "Content-Type": "text/calendar; charset=utf-8",
    "Cache-Control": "no-cache, no-store, must-revalidate, max-age=0",
    Pragma: "no-cache",
    Expires: "0",
    "Content-Disposition": `inline; filename="calendar.ics"; filename*=UTF-8''${encodeURIComponent(filename)}`
  };
}

function calendarResponse(text, filename, cors, extra = {}) {
  const normalized = String(text).replace(/\r\n|\n|\r/g, "\r\n");
  return new Response(normalized, {
    status: 200,
    headers: { ...calendarHeaders(cors, filename), ...extra }
  });
}

function buildCors(request, env) {
  const origin = request.headers.get("Origin") || "";
  const allowed = String(env.WEB_ORIGIN || "*")
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
  const allow = allowed.includes("*") ? "*" : allowed.includes(origin) ? origin : "";

  const headers = {
    Vary: "Origin",
    "Access-Control-Allow-Methods":
      "GET,POST,PATCH,DELETE,OPTIONS,PROPFIND,REPORT,PUT,MKCOL,MKCALENDAR,PROPPATCH",
    "Access-Control-Allow-Headers":
      "Authorization,Content-Type,Depth,If-Match,If-None-Match,DAV,CF-Turnstile-Response",
    "Access-Control-Expose-Headers": "ETag,DAV,Allow,Location",
    "Access-Control-Max-Age": "86400"
  };
  if (allow) headers["Access-Control-Allow-Origin"] = allow;
  return headers;
}

function toWebcal(url) {
  return String(url).replace(/^https?:\/\//i, "webcal://");
}

function isIcs(text) {
  const value = String(text || "");
  return value.includes("BEGIN:VCALENDAR") && value.includes("END:VCALENDAR");
}

function decodeUtf8(bytes) {
  return new TextDecoder("utf-8").decode(bytes);
}

function bytesToBase64(bytes) {
  let binary = "";
  const chunk = 0x8000;
  for (let index = 0; index < bytes.length; index += chunk) {
    binary += String.fromCharCode(...bytes.subarray(index, index + chunk));
  }
  return btoa(binary);
}

function base64ToBytes(value) {
  const binary = atob(String(value).replace(/\s+/g, ""));
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index);
  }
  return bytes;
}

function bytesToBase64Url(bytes) {
  return bytesToBase64(bytes).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function base64UrlToBytes(value) {
  const clean = String(value || "").replace(/-/g, "+").replace(/_/g, "/");
  const padded = clean + "=".repeat((4 - (clean.length % 4)) % 4);
  return base64ToBytes(padded);
}

function xmlEscape(value) {
  return String(value || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&apos;");
}

function decodeXml(value) {
  return String(value || "")
    .replaceAll("&amp;", "&")
    .replaceAll("&lt;", "<")
    .replaceAll("&gt;", ">")
    .replaceAll("&quot;", '"')
    .replaceAll("&apos;", "'");
}

async function safeJson(request) {
  try {
    return await request.json();
  } catch {
    return {};
  }
}
