import test, { beforeEach } from "node:test";
import assert from "node:assert/strict";

import worker from "../src/index.js";

/* ------------------------------------------------------------------ */
/* In-memory GitHub mock                                               */
/* ------------------------------------------------------------------ */

const REPO = "https://api.github.com/repos/testowner/testrepo/contents/";
const files = new Map();

function shaOf(text) {
  return Buffer.from(text).toString("hex").slice(0, 40);
}

function base64(text) {
  return Buffer.from(text, "utf8").toString("base64");
}

function setMockFetch() {
  globalThis.fetch = async (input, init = {}) => {
    const url = typeof input === "string" ? input : input.url;

    if (url.startsWith("https://challenges.cloudflare.com/")) {
      return new Response(JSON.stringify({ success: true }), { status: 200 });
    }

    if (!url.startsWith(REPO)) {
      throw new Error(`Unexpected fetch: ${url}`);
    }

    const parsed = new URL(url);
    const path = decodeURIComponent(parsed.pathname.slice(new URL(REPO).pathname.length)).replace(/^\/+/, "");
    const method = (init.method || "GET").toUpperCase();

    if (method === "GET") {
      if (files.has(path)) {
        const text = files.get(path).text;
        return new Response(
          JSON.stringify({
            name: path.split("/").pop(),
            path,
            sha: files.get(path).sha,
            size: Buffer.byteLength(text),
            content: base64(text),
            encoding: "base64"
          }),
          { status: 200, headers: { "Content-Type": "application/json" } }
        );
      }

      const children = [...files.entries()]
        .filter(([key]) => key.startsWith(`${path}/`))
        .map(([key, value]) => ({
          name: key.split("/").pop(),
          path: key,
          sha: value.sha,
          size: Buffer.byteLength(value.text),
          type: "file"
        }));

      if (children.length > 0) {
        return new Response(JSON.stringify(children), {
          status: 200,
          headers: { "Content-Type": "application/json" }
        });
      }

      return new Response("Not Found", { status: 404 });
    }

    if (method === "PUT") {
      const body = JSON.parse(init.body);
      const text = Buffer.from(body.content, "base64").toString("utf8");
      files.set(path, { text, sha: shaOf(text) });
      return new Response(JSON.stringify({ content: { sha: shaOf(text) } }), { status: 200 });
    }

    if (method === "DELETE") {
      files.delete(path);
      return new Response(null, { status: 200 });
    }

    return new Response("Unsupported", { status: 500 });
  };
}

/* ------------------------------------------------------------------ */
/* Helpers                                                             */
/* ------------------------------------------------------------------ */

const env = {
  GITHUB_OWNER: "testowner",
  GITHUB_REPO: "testrepo",
  GITHUB_BRANCH: "main",
  GITHUB_PATH: "ics",
  GITHUB_TOKEN: "ghp_test",
  ENCRYPTION_KEY: "test-encryption-key",
  SESSION_SECRET: "test-session-secret",
  PUBLIC_BASE_URL: "https://calendar.test",
  WEB_ORIGIN: "*",
  SCHOOLS: "gzhu,hlu"
};

const ICS = [
  "BEGIN:VCALENDAR",
  "VERSION:2.0",
  "PRODID:-//test//EN",
  "BEGIN:VEVENT",
  "UID:test-1",
  "DTSTAMP:20260101T000000Z",
  "DTSTART:20260102T080000Z",
  "DTEND:20260102T090000Z",
  "SUMMARY:Test Course",
  "END:VEVENT",
  "END:VCALENDAR",
  ""
].join("\r\n");

async function api(path, options = {}) {
  const request = new Request(`https://calendar.test${path}`, options);
  return worker.fetch(request, env);
}

async function readJson(response) {
  return JSON.parse(await response.text());
}

async function register(username = "testuser", password = "pass1234", school = "gzhu") {
  const captchaResponse = await api("/api/captcha");
  const captcha = await readJson(captchaResponse);
  const match = String(captcha.question).match(/(\d+)\s*\+\s*(\d+)/);
  assert.ok(match, "captcha question should be a + b");

  const response = await api("/api/register", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      username,
      password,
      school,
      captchaToken: captcha.token,
      captchaAnswer: String(Number(match[1]) + Number(match[2]))
    })
  });

  return { response, body: await readJson(response) };
}

function bearer(apiKey) {
  return { Authorization: `Bearer ${apiKey}` };
}

function basic(apiKey) {
  return { Authorization: `Basic ${Buffer.from(`${apiKey}:${apiKey}`).toString("base64")}` };
}

async function encryptForTest(plaintext, apiKey) {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(apiKey));
  const key = await crypto.subtle.importKey("raw", digest, { name: "AES-GCM" }, false, ["encrypt"]);
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const encrypted = await crypto.subtle.encrypt(
    { name: "AES-GCM", iv },
    key,
    new TextEncoder().encode(plaintext)
  );
  const combined = new Uint8Array(12 + encrypted.byteLength);
  combined.set(iv);
  combined.set(new Uint8Array(encrypted), 12);
  return `CAL1.${Buffer.from(combined).toString("base64url")}`;
}

async function multipart(path, fields, filename, content, headers = {}) {
  const form = new FormData();
  for (const [key, value] of Object.entries(fields)) form.append(key, value);
  form.append("file", new File([content], filename, { type: "text/calendar" }));

  return api(path, { method: "POST", headers, body: form });
}

/* ------------------------------------------------------------------ */
/* Tests                                                               */
/* ------------------------------------------------------------------ */

beforeEach(() => {
  files.clear();
  setMockFetch();
});

test("health & captcha", async () => {
  const health = await api("/health");
  assert.equal(health.status, 200);
  assert.deepEqual(await readJson(health), { ok: true });

  const captchaResponse = await api("/api/captcha");
  const captcha = await readJson(captchaResponse);
  assert.equal(captchaResponse.status, 200);
  assert.match(captcha.question, /^\d+ \+ \d+$/);
  assert.ok(captcha.token.includes("."));
});

test("register returns API key and writes users.json", async () => {
  const { response, body } = await register("alice");
  assert.equal(response.status, 200);
  assert.equal(body.success, true);
  assert.equal(body.username, "alice");
  assert.equal(body.school, "gzhu");
  assert.match(body.apiKey, /^[a-f0-9]{48}$/);
  assert.equal(body.subscriptionUrl, `https://calendar.test/gzhu/${body.apiKey}.ics`);
  assert.equal(body.webcalUrl, `webcal://calendar.test/gzhu/${body.apiKey}.ics`);
  assert.equal(body.caldavUrl, `https://calendar.test/caldav/gzhu/${body.apiKey}/`);

  const users = JSON.parse(files.get("users.json").text);
  assert.equal(users.length, 1);
  assert.equal(users[0].school, "gzhu");
  assert.ok(users[0].apiKeyEncrypted);
  assert.equal(users[0].apiKey, undefined);
  assert.equal(users[0].password, undefined);
});

test("login and /api/me return the same API key", async () => {
  const created = await register("bob", "secret123", "hlu");
  const login = await api("/api/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username: "bob", password: "secret123" })
  });
  const loginBody = await readJson(login);
  assert.equal(login.status, 200);
  assert.equal(loginBody.apiKey, created.body.apiKey);

  const me = await api("/api/me", { headers: bearer(created.body.apiKey) });
  const meBody = await readJson(me);
  assert.equal(me.status, 200);
  assert.equal(meBody.username, "bob");
  assert.equal(meBody.school, "hlu");
  assert.equal(meBody.apiKey, created.body.apiKey);
});

test("guest upload, list, feed and delete", async () => {
  const uploadResponse = await multipart(
    "/api/guest/upload",
    { school: "gzhu", studentId: "2023001" },
    "2023001.ics",
    ICS
  );
  const uploaded = await readJson(uploadResponse);
  assert.equal(uploadResponse.status, 200);
  assert.equal(uploaded.success, true);
  assert.equal(files.get("ics/guest/gzhu/2023001.ics").text, ICS.trim());

  const feed = await api("/guest/gzhu/2023001.ics");
  assert.equal(feed.status, 200);
  assert.match(await feed.text(), /BEGIN:VCALENDAR/);

  const list = await api("/api/guest/files?school=gzhu&studentId=2023001");
  const listBody = await readJson(list);
  assert.equal(listBody.files.length, 1);
  assert.equal(listBody.files[0].kind, "guest");

  const deleted = await api("/api/guest/delete", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ school: "gzhu", studentId: "2023001" })
  });
  assert.equal(deleted.status, 200);
  assert.equal(files.has("ics/guest/gzhu/2023001.ics"), false);
});

test("registered upload stores ciphertext and feed decrypts", async () => {
  const { body } = await register("carol", "pass1234", "gzhu");
  const apiKey = body.apiKey;
  const ciphertext = await encryptForTest(ICS, apiKey);

  const uploadResponse = await multipart(
    "/api/upload",
    {},
    `${apiKey}.ics`,
    ciphertext,
    bearer(apiKey)
  );
  const uploaded = await readJson(uploadResponse);
  assert.equal(uploadResponse.status, 200);
  assert.equal(uploaded.success, true);
  assert.equal(uploaded.encrypted, true);

  const stored = files.get(`ics/gzhu/${apiKey}.ics`).text;
  assert.ok(stored.startsWith("CAL1."));
  assert.equal(stored.includes("BEGIN:VCALENDAR"), false);

  const feed = await api(`/gzhu/${apiKey}.ics`);
  assert.equal(feed.status, 200);
  assert.match(await feed.text(), /SUMMARY:Test Course/);

  const list = await api("/api/files", { headers: bearer(apiKey) });
  const listBody = await readJson(list);
  assert.equal(listBody.files.length, 1);
  assert.equal(listBody.files[0].filename, `${apiKey}.ics`);
  assert.equal(listBody.files[0].encrypted, true);
});

test("registered upload accepts plaintext ICS and encrypts it", async () => {
  const { body } = await register("dave", "pass1234", "gzhu");
  const apiKey = body.apiKey;

  const uploadResponse = await multipart("/api/upload", {}, "schedule.ics", ICS, bearer(apiKey));
  assert.equal(uploadResponse.status, 200);
  const stored = files.get(`ics/gzhu/${apiKey}.ics`).text;
  assert.ok(stored.startsWith("CAL1."));

  const feed = await api(`/gzhu/${apiKey}.ics`);
  assert.match(await feed.text(), /BEGIN:VCALENDAR/);
});

test("CalDAV discovery, PROPFIND, REPORT, PUT, GET, DELETE", async () => {
  const { body } = await register("erin", "pass1234", "gzhu");
  const apiKey = body.apiKey;
  await multipart("/api/upload", {}, "x.ics", ICS, bearer(apiKey));

  const root = await api("/caldav/", {
    method: "PROPFIND",
    headers: { ...basic(apiKey), Depth: "0" }
  });
  assert.equal(root.status, 207);
  assert.match(await root.text(), new RegExp(`/caldav/gzhu/${apiKey}/principal/`));

  const propfind = await api(`/caldav/gzhu/${apiKey}/`, {
    method: "PROPFIND",
    headers: { ...basic(apiKey), Depth: "1" }
  });
  const propfindText = await propfind.text();
  assert.equal(propfind.status, 207);
  assert.match(propfindText, /<cal:calendar\/>/);
  assert.match(propfindText, new RegExp(`${apiKey}\\.ics`));

  const report = await api(`/caldav/gzhu/${apiKey}/`, {
    method: "REPORT",
    headers: { ...basic(apiKey), "Content-Type": "application/xml", Depth: "1" },
    body: `<?xml version="1.0"?>
      <c:calendar-query xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
        <d:prop><d:getetag/><c:calendar-data/></d:prop>
      </c:calendar-query>`
  });
  const reportText = await report.text();
  assert.equal(report.status, 207);
  assert.match(reportText, /calendar-data/);
  assert.match(reportText, /SUMMARY:Test Course/);

  const updatedIcs = ICS.replace("Test Course", "Updated Course");
  const put = await api(`/caldav/gzhu/${apiKey}/${apiKey}.ics`, {
    method: "PUT",
    headers: { ...basic(apiKey), "Content-Type": "text/calendar" },
    body: updatedIcs
  });
  assert.ok([201, 204].includes(put.status));

  const get = await api(`/caldav/gzhu/${apiKey}/${apiKey}.ics`, {
    headers: basic(apiKey)
  });
  assert.equal(get.status, 200);
  assert.match(await get.text(), /Updated Course/);

  const deleted = await api(`/caldav/gzhu/${apiKey}/${apiKey}.ics`, {
    method: "DELETE",
    headers: basic(apiKey)
  });
  assert.equal(deleted.status, 204);

  const gone = await api(`/caldav/gzhu/${apiKey}/${apiKey}.ics`, {
    headers: basic(apiKey)
  });
  assert.equal(gone.status, 404);
});

test("duplicate registration and wrong login are rejected", async () => {
  const first = await register("dupe", "pass1234", "gzhu");
  assert.equal(first.response.status, 200);

  const second = await register("dupe", "pass1234", "gzhu");
  assert.equal(second.response.status, 400);
  assert.match(second.body.error, /已存在/);

  const wrong = await api("/api/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username: "dupe", password: "wrong" })
  });
  assert.equal(wrong.status, 401);
});

test("guest upload rejects non-ICS", async () => {
  const response = await multipart(
    "/api/guest/upload",
    { school: "gzhu", studentId: "2023003" },
    "bad.ics",
    "not a calendar"
  );
  assert.equal(response.status, 400);
  assert.equal(files.has("ics/guest/gzhu/2023003.ics"), false);
});

test("CalDAV can create and delete an additional resource", async () => {
  const { body } = await register("grace", "pass1234", "gzhu");
  const apiKey = body.apiKey;
  await multipart("/api/upload", {}, "x.ics", ICS, bearer(apiKey));

  const extraName = "new-event.ics";
  const extraIcs = ICS.replace("test-1", "new-event-1").replace("Test Course", "New Event");
  const put = await api(`/caldav/gzhu/${apiKey}/${extraName}`, {
    method: "PUT",
    headers: { ...basic(apiKey), "Content-Type": "text/calendar" },
    body: extraIcs
  });
  assert.equal(put.status, 201);
  assert.equal(files.get(`ics/gzhu/${apiKey}/${extraName}`).text.startsWith("CAL1."), true);

  const propfind = await api(`/caldav/gzhu/${apiKey}/`, {
    method: "PROPFIND",
    headers: { ...basic(apiKey), Depth: "1" }
  });
  const xml = await propfind.text();
  assert.match(xml, new RegExp(`${apiKey}\\.ics`));
  assert.match(xml, /new-event\.ics/);

  const getExtra = await api(`/caldav/gzhu/${apiKey}/${extraName}`, {
    headers: basic(apiKey)
  });
  assert.equal(getExtra.status, 200);
  assert.match(await getExtra.text(), /New Event/);

  const feed = await api(`/gzhu/${apiKey}.ics`);
  const feedText = await feed.text();
  assert.match(feedText, /Test Course/);
  assert.match(feedText, /New Event/);

  const deleted = await api(`/caldav/gzhu/${apiKey}/${extraName}`, {
    method: "DELETE",
    headers: basic(apiKey)
  });
  assert.equal(deleted.status, 204);
  assert.equal(files.has(`ics/gzhu/${apiKey}/${extraName}`), false);

  const main = await api(`/caldav/gzhu/${apiKey}/${apiKey}.ics`, {
    headers: basic(apiKey)
  });
  assert.equal(main.status, 200);
});

test("CalDAV rejects a wrong API key", async () => {
  await register("frank", "pass1234", "gzhu");
  const response = await api("/caldav/gzhu/000000000000000000000000000000000000000000000000/", {
    method: "PROPFIND",
    headers: { ...basic("000000000000000000000000000000000000000000000000"), Depth: "0" }
  });
  assert.equal(response.status, 401);
});

test("CORS allows the configured origin", async () => {
  const response = await api("/health", {
    headers: { Origin: "https://user.github.io" }
  });
  assert.equal(response.headers.get("Access-Control-Allow-Origin"), "*");
});
