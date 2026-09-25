import os
import sys
import re
import json
import asyncio
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from playwright.async_api import async_playwright

# 根据系统UI默认字体
SYS_FONT = "PingFang SC" if sys.platform == "darwin" else "Microsoft YaHei"

# 动态获取程序运行根目录 兼容 .exe 打包后运行环境
def get_app_dir():
    if getattr(sys, 'frozen', False):
        # .exe 获取所在的实际目录
        return os.path.dirname(sys.executable)
    else:
        # .py 获取所在目录
        return os.path.dirname(os.path.abspath(__file__))

# 本地课程列表缓存文件路径 保存在 .exe 同目录下
CACHE_FILE = os.path.join(get_app_dir(), "cached_courses.json")


def save_courses_cache(courses):
    """写JSON"""
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(courses, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def load_courses_cache():
    """读JSON"""
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []


def clear_courses_cache():
    """删JSON"""
    if os.path.exists(CACHE_FILE):
        try:
            os.remove(CACHE_FILE)
        except Exception:
            pass


# 检测已安装的网页浏览器

def detect_installed_browsers():
    """
    扫描系统中的网页浏览器 返回一个字典 包含浏览器名称及其相关信息
    """
    browsers = {}

    if sys.platform == "darwin":
        browsers["Safari (WebKit)"] = {"engine": "webkit"}

    known_browsers = [
        {
            "name": "Google Chrome",
            "engine": "chromium",
            "channel": "chrome",
            "paths": [
                "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                os.path.expanduser("~/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
                os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
                os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
                os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
                "/usr/bin/google-chrome",
            ]
        },
        {
            "name": "Microsoft Edge",
            "engine": "chromium",
            "channel": "msedge",
            "paths": [
                "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
                os.path.expanduser("~/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
                os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
                os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
                "/usr/bin/microsoft-edge",
            ]
        },
        {
            "name": "Brave Browser",
            "engine": "chromium",
            "paths": [
                "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
                os.path.expanduser("~/Applications/Brave Browser.app/Contents/MacOS/Brave Browser"),
                os.path.expandvars(r"%ProgramFiles%\BraveSoftware\Brave-Browser\Application\brave.exe"),
                os.path.expandvars(r"%LocalAppData%\BraveSoftware\Brave-Browser\Application\brave.exe"),
            ]
        },
        {
            "name": "Mozilla Firefox",
            "engine": "firefox",
            "paths": [
                "/Applications/Firefox.app/Contents/MacOS/firefox",
                os.path.expanduser("~/Applications/Firefox.app/Contents/MacOS/firefox"),
                os.path.expandvars(r"%ProgramFiles%\Mozilla Firefox\firefox.exe"),
                os.path.expandvars(r"%ProgramFiles(x86)%\Mozilla Firefox\firefox.exe"),
                "/usr/bin/firefox",
            ]
        }
    ]

    for item in known_browsers:
        if item["name"] in browsers:
            continue
        for p in item["paths"]:
            if os.path.exists(p):
                info = {"engine": item["engine"]}
                if "channel" in item:
                    info["channel"] = item["channel"]
                else:
                    info["executable_path"] = p
                browsers[item["name"]] = info
                break

    if sys.platform == "win32":
        try:
            import winreg
            reg_checks = [
                (r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe", "Google Chrome", "chromium", "chrome"),
                (r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe", "Microsoft Edge", "chromium", "msedge"),
                (r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\firefox.exe", "Mozilla Firefox", "firefox", None),
            ]
            for reg_path, name, engine, channel in reg_checks:
                if name not in browsers:
                    for root_key in [winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER]:
                        try:
                            with winreg.OpenKey(root_key, reg_path) as key:
                                exe_path, _ = winreg.QueryValueEx(key, "")
                                if exe_path and os.path.exists(exe_path):
                                    info = {"engine": engine}
                                    if channel:
                                        info["channel"] = channel
                                    else:
                                        info["executable_path"] = exe_path
                                    browsers[name] = info
                                    break
                        except OSError:
                            pass
        except Exception:
            pass

    return browsers


def get_ordered_detected_browsers(detected_browsers):
    """
    按优先级排序已检测到的浏览器：
    优先 Chrome，其次 Edge，再次 Brave / Firefox / Safari 等
    """
    priority_order = ["Google Chrome", "Microsoft Edge", "Brave Browser", "Mozilla Firefox", "Safari (WebKit)"]
    ordered = []
    for name in priority_order:
        if name in detected_browsers:
            ordered.append((name, detected_browsers[name]))
    for name, cfg in detected_browsers.items():
        if name not in priority_order:
            ordered.append((name, cfg))
    return ordered


# 核心网络监听与点击判定逻辑

async def check_xk_phase(page, print_log=None):
    """通用选课阶段判断：进入选课页面后检测是否处于非选课阶段"""
    try:
        await asyncio.sleep(0.5)
        content = await page.content()
        target_msg = "对不起，当前不属于选课阶段，如有需要，请与管理员联系！"
        if "不属于选课阶段" in content or target_msg in content:
            if print_log:
                print_log(f" 暂停运行：{target_msg}")
            raise Exception(target_msg)
    except Exception as e:
        if "不属于选课阶段" in str(e):
            raise e


async def check_schedule_open(page, print_log=None):
    """通用课表开放状态判断：进入课表页面后检测课表是否处于未开放状态"""
    try:
        await asyncio.sleep(0.5)
        content = await page.content()
        target_msg = "该学年学期的课表尚未开放！"
        if target_msg in content:
            if print_log:
                print_log(f" 暂停运行：{target_msg}")
            raise Exception(target_msg)
    except Exception as e:
        if "该学年学期的课表尚未开放！" in str(e):
            raise e


async def ensure_click_with_network(page, locator, print_log=None, action_name="操作", timeout=1200, max_attempts=4):
    if not await locator.is_visible():
        return False

    for attempt in range(1, max_attempts + 1):
        try:
            async with page.expect_response(
                lambda res: res.request.resource_type in ["xhr", "fetch"],
                timeout=timeout
            ):
                await locator.click(force=True)
            return True
        except Exception:
            if print_log and attempt < max_attempts:
                print_log(f"  ↳ [{action_name}] 第 {attempt} 次点击无网络响应，自动重试...")
            await asyncio.sleep(0.1)

    try:
        await locator.click(force=True)
        return True
    except Exception:
        pass
    return False


async def click_search_until_change(page, search_btn, print_log, stop_event=None):
    """
    多次点击“查询”按钮，直到检测到列表数据/内容发生变化
    """
    if not await search_btn.is_visible():
        return

    table_loc = page.locator("table, #xkky_tbody, tbody").first
    try:
        initial_content = await table_loc.inner_html()
    except Exception:
        initial_content = ""

    print_log("  ↳ 查询可选课程...")
    attempt = 0

    while True:
        if stop_event and stop_event.is_set():
            break

        attempt += 1
        try:
            await search_btn.click(force=True)
        except Exception:
            pass

        await asyncio.sleep(0.1)

        try:
            current_content = await table_loc.inner_html()
        except Exception:
            current_content = ""

        # 检测到 DOM / HTML 内容发生变化，或已加载出表格行
        rows_count = await page.locator("tr.body_tr, tr:has(td.jxbmc)").count()
        if (current_content != initial_content and current_content != "") or rows_count > 0:
            print_log(f"  ↳ 抓取成功(共查询 {attempt} 次，获取到 {rows_count} 行数据)")
            break

        if attempt % 10 == 0:
            print_log(f"  ↳ 已抓取 {attempt} 次，持续等待列表更新...")


# ==================== 私立华联大学逻辑 ====================

async def login_hlu(page, username, password, print_log):
    yhm_input = page.locator("#yhm")
    
    if not await yhm_input.is_visible():
        try:
            await yhm_input.wait_for(state="visible", timeout=3000)
        except Exception:
            print_log("  已处于登录状态，继续下一步...")
            return

    await page.fill("#yhm", username)
    await page.fill("#mm", password)
    await page.click("#dl")

    login_task = asyncio.create_task(page.wait_for_url("**/index_initMenu.html**", timeout=15000))
    error_task = asyncio.create_task(page.wait_for_selector("#tips", state="visible", timeout=15000))

    done, pending = await asyncio.wait(
        [login_task, error_task],
        return_when=asyncio.FIRST_COMPLETED
    )

    for t in pending:
        t.cancel()

    tips_loc = page.locator("#tips")

    if error_task in done or await tips_loc.is_visible():
        text = (await tips_loc.text_content()).strip() if await tips_loc.is_visible() else ""
        if "用户名或密码不正确" in text or "不正确" in text:
            raise Exception("用户名或密码不正确，请重新输入！")

    if login_task in done:
        try:
            await login_task
            print_log("  ↳ 登录成功")
            return
        except Exception:
            pass

    if "index_initMenu.html" in page.url:
        print_log("  ↳ 登录成功")
        return

    if await tips_loc.is_visible():
        text = (await tips_loc.text_content()).strip()
        if "用户名或密码不正确" in text or "不正确" in text:
            raise Exception("用户名或密码不正确，请重新输入！")

    raise Exception("登录超时或未知错误，请检查账号密码及网络状态！")


async def fetch_courses_hlu(page, username, password, kwargs, print_log):
    print_log("[1/3] 打开教务系统登录并进入选课页面...")
    await page.goto("http://jwxt.hlu.edu.cn/jwglxt/xtgl/index_initMenu.html", wait_until="domcontentloaded")
    await login_hlu(page, username, password, print_log)

    xk_url = "http://jwxt.hlu.edu.cn/jwglxt/xsxk/zzxkyzb_cxZzxkYzbIndex.html?gnmkdm=N253512&layout=default"
    await page.goto(xk_url, wait_until="domcontentloaded")
    await check_xk_phase(page, print_log)  # 选课阶段检测

    print_log("[2/3] 查找可选课程列表...")
    search_btn = page.locator("#search_go, #search, button:has-text('查詢'), button:has-text('查询'), button:has-text('搜索')").first
    
    try:
        await search_btn.wait_for(state="visible", timeout=8000)
        await click_search_until_change(page, search_btn, print_log)
    except Exception as e:
        print_log(f"  警告：点击“查询”过程出现异常: {e}")

    print_log("[3/3] 整理可选课程列表...")
    rows = page.locator("tr.body_tr, tr:has(td.jxbmc)")
    count = await rows.count()

    course_list = []
    for i in range(count):
        row = rows.nth(i)
        jxbmc_loc = row.locator("td.jxbmc")
        sksj_loc = row.locator("td.sksj")

        if await jxbmc_loc.count() > 0:
            name_keyword = (await jxbmc_loc.text_content()).strip()
            time = (await sksj_loc.text_content()).strip() if await sksj_loc.count() > 0 else ""
            
            if name_keyword:
                full_item = f"{name_keyword} | {time}" if time else name_keyword
                course_list.append(full_item)

    print_log(f"抓取完成，共提取到 {len(course_list)} 门课程选项！")
    return course_list


async def download_hlu(page, username, password, kwargs, print_log):
    save_dir = kwargs.get("save_dir")
    print_log("[1/4] 打开教务系统填写登录凭证...")
    await page.goto("http://jwxt.hlu.edu.cn/jwglxt/xtgl/index_initMenu.html")
    await page.wait_for_load_state("domcontentloaded")

    await login_hlu(page, username, password, print_log)

    print_log("[2/4] 导航至课表查询页面...")
    schedule_url = "http://jwxt.hlu.edu.cn/jwglxt/kbcx/xskbcx_cxXskbcxIndex.html?gnmkdm=N2151&layout=default"
    await page.goto(schedule_url, wait_until="domcontentloaded")
    await check_schedule_open(page, print_log)  # 课表开放检测

    search_btn = page.locator("#search_go")
    if await search_btn.is_visible():
        await ensure_click_with_network(page, search_btn, print_log, "课表查询")

    print_log("[3/4] 触发导出 PDF...")
    async with page.expect_download(timeout=20000) as download_info:
        await page.evaluate("if(typeof shcPdf === 'function'){ shcPdf(); } else { $('#shcPDF').click(); }")

    download = await download_info.value
    save_path = os.path.join(save_dir, f"{username}_Schedule.pdf")
    await download.save_as(save_path)
    print_log(f"课程表已下载并保存于: {save_path}")
    return save_path


async def auto_select_course_hlu(page, username, password, kwargs, print_log):
    target_courses = kwargs.get("target_courses", [])
    retry_interval = kwargs.get("retry_interval", 0.3)
    skip_full = kwargs.get("skip_full", True)
    skip_unopened = kwargs.get("skip_unopened", True)
    stop_event = kwargs.get("stop_event")
    ask_user = kwargs.get("ask_user")

    print_log("[1/3] 打开教务系统填写登录凭证...")
    login_url = "http://jwxt.hlu.edu.cn/jwglxt/xtgl/index_initMenu.html"
    await page.goto(login_url, wait_until="domcontentloaded")

    await login_hlu(page, username, password, print_log)

    print_log("[2/3] 导航至 “自主选课”...")
    xk_url = "http://jwxt.hlu.edu.cn/jwglxt/xsxk/zzxkyzb_cxZzxkYzbIndex.html?gnmkdm=N253512&layout=default"
    await page.goto(xk_url, wait_until="domcontentloaded")
    await check_xk_phase(page, print_log)  # 选课阶段检测

    search_input = page.locator("#kcmc, input[name='kcmc'], #cx_kcmc").first
    search_btn = page.locator("#search_go, #search, button:has-text('查詢'), button:has-text('查询'), button:has-text('搜索')").first

    courses_str = " -> ".join([f"【{c}】" for c in target_courses])
    print_log(f"[3/3] 进程已启动，选择顺序: {courses_str}")
    
    attempt_count = 0
    selected_courses = set()

    while not stop_event.is_set():
        attempt_count += 1
        print_log(f"--- 第 {attempt_count} 次轮询 ---")

        for rank, target_course in enumerate(target_courses, start=1):
            if stop_event.is_set():
                break

            if target_course in selected_courses:
                continue

            course_keyword = target_course
            course_time = ""

            if " | " in target_course:
                parts = target_course.split(" | ", 1)
                course_keyword = parts[0].strip()
                course_time = parts[1].strip()
            elif "-" in target_course:
                match = re.search(r'-(星期|周)', target_course)
                if match:
                    split_pos = match.start()
                    course_keyword = target_course[:split_pos].strip()
                    course_time = target_course[split_pos+1:].strip()
                else:
                    parts = target_course.rsplit("-", 1)
                    course_keyword = parts[0].strip()
                    course_time = parts[1].strip()

            course_keyword = re.sub(r'\{.*$', '', course_keyword).strip()

            print_log(f"  检索目标关键词: 【{course_keyword}】...")

            try:
                if await search_input.is_visible():
                    await search_input.fill(course_keyword)

                if await search_btn.is_visible():
                    await click_search_until_change(page, search_btn, print_log, stop_event)

                rows = page.locator("tr.body_tr, tr:has(td.jxbmc)")
                count = await rows.count()
                target_row = None

                for i in range(count):
                    row = rows.nth(i)
                    jxbmc_loc = row.locator("td.jxbmc")
                    if await jxbmc_loc.count() > 0:
                        td_keyword = (await jxbmc_loc.text_content()).strip()
                        if course_keyword == td_keyword or course_keyword in td_keyword:
                            if course_time:
                                sksj_loc = row.locator("td.sksj")
                                sksj_text = (await sksj_loc.text_content()).strip() if await sksj_loc.count() > 0 else ""
                                clean_time = re.sub(r'\{.*$', '', course_time).strip()
                                if course_time in sksj_text or (clean_time and clean_time in sksj_text):
                                    target_row = row
                                    break
                            else:
                                target_row = row
                                break

                if stop_event.is_set():
                    break

                if target_row is None:
                    print_log(f"  ↳ 未找到关键词【{course_keyword}】相关行，自动继续轮询...")
                    continue

                tx_btn = target_row.locator("button:has-text('退选'), button:has-text('退選'), a:has-text('退选')").first
                xk_btn = target_row.locator("button[id^='btn-xk-'], button:has-text('选课'), button:has-text('選課')").first

                if await tx_btn.is_visible():
                    print_log(f"成功! 检测到【{target_course}】已被选中")
                    selected_courses.add(target_course)
                    if ask_user:
                        cont = ask_user("成功", f"课程 {target_course} 已被选中! \n\n是否继续选择其他课程?")
                        if not cont:
                            return True
                    continue

                if await xk_btn.is_visible():
                    if await xk_btn.is_disabled():
                        print_log(f"  ↳ 课程 {target_course} 选择按钮处于禁用状态")
                        if skip_unopened:
                            print_log(f"  ↳ [配置] 自动跳过未开放/禁用的课程: {target_course}")
                            continue

                    print_log(f"点击符合关键词【{course_keyword}】的选择按钮...")
                    await ensure_click_with_network(page, xk_btn, print_log, f"选课-{target_course}")

                    bootbox_ok = page.locator("#btn_ok, button[data-bb-handler='ok']").first
                    msg_p = page.locator(".alert.alert-modal p, .bootbox-body p, .modal-body p").first

                    try:
                        await page.wait_for_selector("#btn_ok, button:has-text('退选'), .alert", timeout=800)
                    except Exception:
                        pass

                    if await msg_p.is_visible():
                        res_text = (await msg_p.text_content()).strip()
                        print_log(f"  ↳ 提示: {res_text}")

                        if "无余量" in res_text or "已无余量" in res_text:
                            if await bootbox_ok.is_visible():
                                await bootbox_ok.click(force=True)

                            if skip_full:
                                print_log(f"  课程 {target_course} 已满, 自动跳过")
                                continue
                            else:
                                if ask_user:
                                    cont = ask_user("此课程已满", f"提示：\n{res_text}\n\n是否继续?")
                                    if not cont:
                                        return False
                                continue

                        elif any(kw in res_text for kw in ["未开放", "未开始", "不在选课", "不允许", "时间未到"]):
                            if await bootbox_ok.is_visible():
                                await bootbox_ok.click(force=True)

                            if skip_unopened:
                                print_log(f"  课程 {target_course} 未开放选课, 自动跳过")
                                continue
                            else:
                                if ask_user:
                                    cont = ask_user("未开放选课", f"提示：\n{res_text}\n\n是否继续?")
                                    if not cont:
                                        return False
                                continue

                    if await bootbox_ok.is_visible():
                        await bootbox_ok.click(force=True)

                    try:
                        await tx_btn.wait_for(state="visible", timeout=1000)
                        if await tx_btn.is_visible():
                            print_log(f"成功! 检测到【{target_course}】已被选中")
                            selected_courses.add(target_course)
                            if ask_user:
                                cont = ask_user("成功", f"课程 {target_course} 已被选中! \n\n是否继续选择其他课程?")
                                if not cont:
                                    return True
                            continue
                    except Exception:
                        pass

            except Exception as err:
                print_log(f"检索/点击课程 {target_course} 时异常: {str(err)}")

        if len(selected_courses) >= len(target_courses):
            print_log("所有目标课程已全部选中")
            return True

        if retry_interval > 0:
            await asyncio.sleep(retry_interval)

    print_log("用户中止任务，退出...")
    return False


# ==================== 广州大学逻辑 ====================

async def login_gzhu(page, username, password, print_log):
    """https://webvpn.gzhu.edu.cn/ 重复点击登录加速"""
    print_log("  ↳ 登录广州大学融合门户...")
    await page.goto("https://webvpn.gzhu.edu.cn/", wait_until="domcontentloaded")

    un_input = page.locator("#un")
    login_btn = page.locator("#index_login_btn")
    errmsg_loc = page.locator("#errmsg")
    jwxt_app = page.locator("a[title='教務系統'], a[title='教务系统'], a[appid='19255690825746'], a[appurl*='https://jwxt.gzhu.edu.cn/sso/driot4login']").first

    # 1. 首次登录融合门户（重复点击直到画面改变）
    if await un_input.is_visible():
        await page.fill("#un", username)
        await page.fill("#pd", password)
        
        print_log("  ↳ 登录...")
        start_time = asyncio.get_event_loop().time()
        while asyncio.get_event_loop().time() - start_time < 15:
            if await errmsg_loc.is_visible():
                err_text = (await errmsg_loc.text_content()).strip()
                if err_text:
                    raise Exception(f"首次登录失败：{err_text}")

            if not await un_input.is_visible() or await jwxt_app.is_visible():
                break

            try:
                await login_btn.click(force=True)
            except Exception:
                pass
            await asyncio.sleep(0.1)

        print_log("  ↳ 首次登录成功")

    # 2. 查找并点击教务系统入口
    print_log("  ↳ 查找并进入教务系统...")
    try:
        await jwxt_app.wait_for(state="visible", timeout=6000)
    except Exception:
        raise Exception("未能找到教务系统入口按钮，请检查账号权限或网络状态！")

    target_page = None
    try:
        async with page.context.expect_page(timeout=3000) as new_page_info:
            await jwxt_app.click(force=True)
        target_page = await new_page_info.value
    except Exception:
        target_page = page

    # 3. 检测二次登录，重复点击
    print_log("  ↳ 检测教务系统二次身份认证...")
    target_un = target_page.locator("#un")

    try:
        await target_page.wait_for_selector("#un, #xnm, .navbar, body", timeout=4000)
    except Exception:
        pass

    if await target_un.is_visible():
        print_log("  ↳ 登录...")
        await target_page.fill("#un", username)
        await target_page.fill("#pd", password)

        target_btn = target_page.locator("#index_login_btn")
        target_err = target_page.locator("#errmsg")

        start_time = asyncio.get_event_loop().time()
        while asyncio.get_event_loop().time() - start_time < 15:
            if await target_err.is_visible():
                err_text = (await target_err.text_content()).strip()
                if err_text:
                    raise Exception(f"二次登录失败：{err_text}")

            if not await target_un.is_visible() or "jwglxt" in target_page.url:
                break

            try:
                await target_btn.click(force=True)
            except Exception:
                pass
            await asyncio.sleep(0.1)

        print_log("  ↳ 二次登录成功")

    print_log("  ↳ 成功进入教务系统平台")
    return target_page


async def download_gzhu(page, username, password, kwargs, print_log):
    """广州大学课表下载 课表页面后 高频点击 #search_go 查询 点击 #shcPDF 导出 PDF"""
    save_dir = kwargs.get("save_dir")
    print_log("[1/4] 打开广州大学融合门户并填写凭证...")
    target_page = await login_gzhu(page, username, password, print_log)

    print_log("[2/4] 导航至个人课表查询页面...")
    schedule_url = "https://jwxt.gzhu.edu.cn/jwglxt/kbcx/xskbcx_cxXskbcxIndex.html?gnmkdm=N2151&layout=default"
    await target_page.goto(schedule_url, wait_until="domcontentloaded")
    await check_schedule_open(target_page, print_log)  # 课表开放检测

    # 1. 精准定位查询按钮 #search_go
    search_btn = target_page.locator("#search_go")
    try:
        await search_btn.wait_for(state="visible", timeout=8000)
    except Exception:
        print_log("  定位 “查询” 按钮...")

    # 2. 进入页面后立即高频点击 #search_go 按钮
    print_log("  ↳ 查询...")
    table_loc = target_page.locator("#kbtable, table, tbody").first
    
    try:
        initial_content = await table_loc.inner_html()
    except Exception:
        initial_content = ""

    attempt = 0
    # 极短间隔（50ms）高频点击，直至课表 DOM 画面发生更新
    while True:
        attempt += 1
        try:
            await search_btn.click(force=True)
        except Exception:
            pass

        await asyncio.sleep(0.05)  # 50 毫秒高频点击

        try:
            current_content = await table_loc.inner_html()
        except Exception:
            current_content = ""

        # 当课表表格内容发生更新变化，或快速点击已达阈值（例如 60 次）时停止点击
        if (current_content != initial_content and current_content != "") or attempt >= 60:
            print_log(f"  ↳ 查询完成，课表数据已刷出 (共点击查询 {attempt} 次)")
            break

    # 3. 点击 #shcPDF 输出 PDF 并捕获下载流
    print_log("[3/4] 触发 PDF 导出...")
    pdf_btn = target_page.locator("#shcPDF")

    async with target_page.expect_download(timeout=20000) as download_info:
        # 优先通过元素点击，若有覆盖层或事件未绑定则通过 JS 触发
        if await pdf_btn.is_visible():
            await pdf_btn.click(force=True)
        else:
            await target_page.evaluate("document.querySelector('#shcPDF').click()")

    download = await download_info.value
    save_path = os.path.join(save_dir, f"{username}_GZHU_Schedule.pdf")
    await download.save_as(save_path)
    print_log(f"[4/4] 广州大学课程表已成功下载并保存至: {save_path}")
    return save_path

async def fetch_courses_gzhu(page, username, password, kwargs, print_log):
    """广州大学抓取可选课程"""
    print_log("[1/3] 打开广州大学融合门户并登录...")
    target_page = await login_gzhu(page, username, password, print_log)

    xk_url = "https://jwxt.gzhu.edu.cn/jwglxt/xsxk/zzxkyzb_cxZzxkYzbIndex.html?gnmkdm=N253512&layout=default"
    await target_page.goto(xk_url, wait_until="domcontentloaded")
    await check_xk_phase(target_page, print_log)  # 选课阶段检测

    print_log("[2/3] 查找可选课程列表...")
    search_btn = target_page.locator("#search_go, #search, button:has-text('查詢'), button:has-text('查询'), button:has-text('搜索')").first
    try:
        await search_btn.wait_for(state="visible", timeout=8000)
        await click_search_until_change(target_page, search_btn, print_log)
    except Exception as e:
        print_log(f"  警告：点击“查询”过程出现异常: {e}")

    print_log("[3/3] 整理可选课程列表...")
    rows = target_page.locator("tr.body_tr, tr:has(td.jxbmc)")
    count = await rows.count()

    course_list = []
    for i in range(count):
        row = rows.nth(i)
        jxbmc_loc = row.locator("td.jxbmc")
        sksj_loc = row.locator("td.sksj")

        if await jxbmc_loc.count() > 0:
            name_keyword = (await jxbmc_loc.text_content()).strip()
            time = (await sksj_loc.text_content()).strip() if await sksj_loc.count() > 0 else ""
            if name_keyword:
                full_item = f"{name_keyword} | {time}" if time else name_keyword
                course_list.append(full_item)

    print_log(f"广州大学课程抓取完成，共提取到 {len(course_list)} 门课程选项！")
    return course_list


async def auto_select_course_gzhu(page, username, password, kwargs, print_log):
    """广州大学自主选课抢课"""
    target_courses = kwargs.get("target_courses", [])
    retry_interval = kwargs.get("retry_interval", 0.3)
    skip_full = kwargs.get("skip_full", True)
    skip_unopened = kwargs.get("skip_unopened", True)
    stop_event = kwargs.get("stop_event")
    ask_user = kwargs.get("ask_user")

    print_log("[1/3] 打开广州大学融合门户并登录...")
    target_page = await login_gzhu(page, username, password, print_log)

    print_log("[2/3] 导航至 “自主选课”...")
    xk_url = "https://jwxt.gzhu.edu.cn/jwglxt/xsxk/zzxkyzb_cxZzxkYzbIndex.html?gnmkdm=N253512&layout=default"
    await target_page.goto(xk_url, wait_until="domcontentloaded")
    await check_xk_phase(target_page, print_log)  # 选课阶段检测

    search_input = target_page.locator("#kcmc, input[name='kcmc'], #cx_kcmc").first
    search_btn = target_page.locator("#search_go, #search, button:has-text('查詢'), button:has-text('查询'), button:has-text('搜索')").first

    courses_str = " -> ".join([f"【{c}】" for c in target_courses])
    print_log(f"[3/3] 极速选课已启动，选择顺序: {courses_str}")
    
    attempt_count = 0
    selected_courses = set()

    while not stop_event.is_set():
        attempt_count += 1
        print_log(f"--- 第 {attempt_count} 次轮询 ---")

        for rank, target_course in enumerate(target_courses, start=1):
            if stop_event.is_set():
                break

            if target_course in selected_courses:
                continue

            course_keyword = target_course
            course_time = ""

            if " | " in target_course:
                parts = target_course.split(" | ", 1)
                course_keyword = parts[0].strip()
                course_time = parts[1].strip()
            elif "-" in target_course:
                match = re.search(r'-(星期|周)', target_course)
                if match:
                    split_pos = match.start()
                    course_keyword = target_course[:split_pos].strip()
                    course_time = target_course[split_pos+1:].strip()
                else:
                    parts = target_course.rsplit("-", 1)
                    course_keyword = parts[0].strip()
                    course_time = parts[1].strip()

            course_keyword = re.sub(r'\{.*$', '', course_keyword).strip()

            print_log(f"  检索目标关键词: 【{course_keyword}】...")

            try:
                if await search_input.is_visible():
                    await search_input.fill(course_keyword)

                if await search_btn.is_visible():
                    await click_search_until_change(target_page, search_btn, print_log, stop_event)

                rows = target_page.locator("tr.body_tr, tr:has(td.jxbmc)")
                count = await rows.count()
                target_row = None

                for i in range(count):
                    row = rows.nth(i)
                    jxbmc_loc = row.locator("td.jxbmc")
                    if await jxbmc_loc.count() > 0:
                        td_keyword = (await jxbmc_loc.text_content()).strip()
                        if course_keyword == td_keyword or course_keyword in td_keyword:
                            if course_time:
                                sksj_loc = row.locator("td.sksj")
                                sksj_text = (await sksj_loc.text_content()).strip() if await sksj_loc.count() > 0 else ""
                                clean_time = re.sub(r'\{.*$', '', course_time).strip()
                                if course_time in sksj_text or (clean_time and clean_time in sksj_text):
                                    target_row = row
                                    break
                            else:
                                target_row = row
                                break

                if stop_event.is_set():
                    break

                if target_row is None:
                    print_log(f"  ↳ 未找到关键词【{course_keyword}】相关行，自动继续轮询...")
                    continue

                tx_btn = target_row.locator("button:has-text('退选'), button:has-text('退選'), a:has-text('退选')").first
                xk_btn = target_row.locator("button[id^='btn-xk-'], button:has-text('选课'), button:has-text('選課')").first

                if await tx_btn.is_visible():
                    print_log(f"成功! 检测到【{target_course}】已被选中")
                    selected_courses.add(target_course)
                    if ask_user:
                        cont = ask_user("成功", f"课程 {target_course} 已被选中! \n\n是否继续选择其他课程?")
                        if not cont:
                            return True
                    continue

                if await xk_btn.is_visible():
                    if await xk_btn.is_disabled():
                        print_log(f"  ↳ 课程 {target_course} 选择按钮处于禁用状态")
                        if skip_unopened:
                            print_log(f"  ↳ [配置] 自动跳过未开放/禁用的课程: {target_course}")
                            continue

                    print_log(f"点击符合关键词【{course_keyword}】的选择按钮...")
                    await ensure_click_with_network(target_page, xk_btn, print_log, f"选课-{target_course}")

                    bootbox_ok = target_page.locator("#btn_ok, button[data-bb-handler='ok']").first
                    msg_p = target_page.locator(".alert.alert-modal p, .bootbox-body p, .modal-body p").first

                    try:
                        await target_page.wait_for_selector("#btn_ok, button:has-text('退选'), .alert", timeout=800)
                    except Exception:
                        pass

                    if await msg_p.is_visible():
                        res_text = (await msg_p.text_content()).strip()
                        print_log(f"  ↳ 提示: {res_text}")

                        if "无余量" in res_text or "已无余量" in res_text:
                            if await bootbox_ok.is_visible():
                                await bootbox_ok.click(force=True)

                            if skip_full:
                                print_log(f"  课程 {target_course} 已满, 自动跳过")
                                continue
                            else:
                                if ask_user:
                                    cont = ask_user("此课程已满", f"提示：\n{res_text}\n\n是否继续?")
                                    if not cont:
                                        return False
                                continue

                        elif any(kw in res_text for kw in ["未开放", "未开始", "不在选课", "不允许", "时间未到"]):
                            if await bootbox_ok.is_visible():
                                await bootbox_ok.click(force=True)

                            if skip_unopened:
                                print_log(f"  课程 {target_course} 未开放选课, 自动跳过")
                                continue
                            else:
                                if ask_user:
                                    cont = ask_user("未开放选课", f"提示：\n{res_text}\n\n是否继续?")
                                    if not cont:
                                        return False
                                continue

                    if await bootbox_ok.is_visible():
                        await bootbox_ok.click(force=True)

                    try:
                        await tx_btn.wait_for(state="visible", timeout=1000)
                        if await tx_btn.is_visible():
                            print_log(f"成功! 检测到【{target_course}】已被选中")
                            selected_courses.add(target_course)
                            if ask_user:
                                cont = ask_user("成功", f"课程 {target_course} 已被选中! \n\n是否继续选择其他课程?")
                                if not cont:
                                    return True
                            continue
                    except Exception:
                        pass

            except Exception as err:
                print_log(f"检索/点击课程 {target_course} 时异常: {str(err)}")

        if len(selected_courses) >= len(target_courses):
            print_log("所有目标课程已全部选中")
            return True

        if retry_interval > 0:
            await asyncio.sleep(retry_interval)

    print_log("用户中止任务，退出...")
    return False


# 自定义可滚动复选框列表组件（支持全选/全不选操作）

class ScrollableChecklist(tk.Frame):
    def __init__(self, parent, height=130, *args, **kwargs):
        super().__init__(parent, *args, **kwargs)
        self.canvas = tk.Canvas(self, borderwidth=1, relief="sunken", height=height, bg="white")
        self.scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.scrollable_frame = tk.Frame(self.canvas, bg="white")

        self.scrollable_frame.bind(
            "<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        )

        self.canvas_window = self.canvas.create_window((0, 0), window=self.scrollable_frame, anchor="nw")
        self.canvas.configure(yscrollcommand=self.scrollbar.set)

        self.canvas.bind('<Configure>', lambda e: self.canvas.itemconfig(self.canvas_window, width=e.width))

        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")

        self.canvas.bind_all("<MouseWheel>", self._on_mousewheel)
        self.vars = {}

    def _on_mousewheel(self, event):
        if self.winfo_ismapped():
            self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def set_items(self, items):
        for child in self.scrollable_frame.winfo_children():
            child.destroy()
        self.vars.clear()

        for item in items:
            var = tk.BooleanVar(value=False)
            cb = tk.Checkbutton(
                self.scrollable_frame, text=item, variable=var, anchor="w", justify="left", bg="white", font=(SYS_FONT, 9)
            )
            cb.pack(fill="x", anchor="w", padx=3, pady=1)
            self.vars[item] = var

    def get_selected(self):
        return [item for item, var in self.vars.items() if var.get()]

    def get_all_items(self):
        """获取当前列表中的所有项目"""
        return list(self.vars.keys())

    def select_all(self):
        """全选所有项目"""
        for var in self.vars.values():
            var.set(True)

    def deselect_all(self):
        """取消勾选所有项目"""
        for var in self.vars.values():
            var.set(False)

    def clear_items(self):
        """清空当前列表所有项目"""
        for child in self.scrollable_frame.winfo_children():
            child.destroy()
        self.vars.clear()


# 学校 功能 配置映射表

SCHOOL_FEATURES = {
    "请先选择学校": { "请先选择学校": { }, },

    "私立华联大学": {
        "课程表自动获取下载工具": {
            "type": "download",
            "handler": download_hlu
        },
        "全自动选课工具": {
            "type": "course_select",
            "handler": auto_select_course_hlu,
            "fetch_handler": fetch_courses_hlu
        }
    },

    "广州大学": {
        "课程表自动获取下载工具": {
            "type": "download",
            "handler": download_gzhu
        },
        "全自动选课工具": {
            "type": "course_select",
            "handler": auto_select_course_gzhu,
            "fetch_handler": fetch_courses_gzhu
        }
    }
}


# 统一 GUI 主程序

class UnifiedCampusApp:
    def __init__(self, root):
        self.root = root
        self.root.title("教务辅助工具")
        self.root.geometry("600x780")
        self.root.resizable(False, False)

        self.stop_event = threading.Event()
        self.detected_browsers = detect_installed_browsers()
        browser_options = ["自动检测"] + list(self.detected_browsers.keys())
        if len(self.detected_browsers) == 0:
            browser_options = ["未检测到系统安装的可用浏览器"]

        pad_x, pad_y = 12, 5

        # 院校与功能选择区域
        top_frame = tk.LabelFrame(root, text="选择院校与功能", font=(SYS_FONT, 10, "bold"), padx=10, pady=5)
        top_frame.pack(fill="x", padx=pad_x, pady=pad_y)

        tk.Label(top_frame, text="院校:", font=(SYS_FONT, 10)).grid(row=0, column=0, sticky="e", pady=pad_y)
        self.school_cb = ttk.Combobox(
            top_frame, values=list(SCHOOL_FEATURES.keys()), state="readonly", width=36, font=(SYS_FONT, 10)
        )
        self.school_cb.current(0)
        self.school_cb.grid(row=0, column=1, padx=pad_x, pady=pad_y)
        self.school_cb.bind("<<ComboboxSelected>>", self.on_school_changed)

        tk.Label(top_frame, text="功能:", font=(SYS_FONT, 10)).grid(row=1, column=0, sticky="e", pady=pad_y)
        self.feature_cb = ttk.Combobox(
            top_frame, values=[], state="readonly", width=36, font=(SYS_FONT, 10)
        )
        self.feature_cb.grid(row=1, column=1, padx=pad_x, pady=pad_y)
        self.feature_cb.bind("<<ComboboxSelected>>", self.on_feature_changed)

        tk.Label(top_frame, text="浏览器:", font=(SYS_FONT, 10)).grid(row=2, column=0, sticky="e", pady=pad_y)
        self.browser_cb = ttk.Combobox(
            top_frame, values=browser_options, state="readonly", width=36, font=(SYS_FONT, 10)
        )
        self.browser_cb.current(0)
        self.browser_cb.grid(row=2, column=1, padx=pad_x, pady=pad_y)

        # 账号凭证
        cred_frame = tk.LabelFrame(root, text="账号凭证", font=(SYS_FONT, 10, "bold"), padx=10, pady=5)
        cred_frame.pack(fill="x", padx=pad_x, pady=pad_y)

        tk.Label(cred_frame, text="学号:", font=(SYS_FONT, 10)).grid(row=0, column=0, sticky="e", pady=pad_y)
        self.username_entry = tk.Entry(cred_frame, width=38, font=(SYS_FONT, 10))
        self.username_entry.grid(row=0, column=1, padx=pad_x, pady=pad_y)

        tk.Label(cred_frame, text="密码:", font=(SYS_FONT, 10)).grid(row=1, column=0, sticky="e", pady=pad_y)
        self.password_entry = tk.Entry(cred_frame, show="*", width=38, font=(SYS_FONT, 10))
        self.password_entry.grid(row=1, column=1, padx=pad_x, pady=pad_y)

        # 课表下载设置
        self.download_frame = tk.LabelFrame(root, text="课表下载设置", font=(SYS_FONT, 10, "bold"), padx=10, pady=5)
        tk.Label(self.download_frame, text="保存路径:", font=(SYS_FONT, 10)).grid(row=0, column=0, sticky="e", pady=pad_y)
        path_inner = tk.Frame(self.download_frame)
        path_inner.grid(row=0, column=1, sticky="w", padx=pad_x, pady=pad_y)

        self.path_entry = tk.Entry(path_inner, width=25, font=(SYS_FONT, 10))
        self.path_entry.pack(side="left", padx=(0, 5))
        desktop_path = os.path.join(os.path.expanduser("~"), "Desktop")
        self.path_entry.insert(0, desktop_path)

        self.btn_browse = tk.Button(path_inner, text="浏览...", command=self.browse_folder)
        self.btn_browse.pack(side="left")

        # 课程选择设置
        self.course_frame = tk.LabelFrame(root, text="课程选择设置", font=(SYS_FONT, 10, "bold"), padx=10, pady=5)

        btn_fetch_frame = tk.Frame(self.course_frame)
        btn_fetch_frame.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 5))

        self.btn_fetch = tk.Button(
            btn_fetch_frame, text="获取课程列表", bg="#28A745", fg="white", font=(SYS_FONT, 9, "bold"),
            command=self.start_fetch_courses
        )
        self.btn_fetch.pack(side="left", padx=(5, 3))

        # 全选与全不选按钮
        self.btn_select_all = tk.Button(
            btn_fetch_frame, text="全选", bg="#17A2B8", fg="white", font=(SYS_FONT, 9, "bold"),
            command=lambda: self.checklist.select_all()
        )
        self.btn_select_all.pack(side="left", padx=(0, 3))

        self.btn_deselect_all = tk.Button(
            btn_fetch_frame, text="全不选", bg="#6C757D", fg="white", font=(SYS_FONT, 9, "bold"),
            command=lambda: self.checklist.deselect_all()
        )
        self.btn_deselect_all.pack(side="left", padx=(0, 3))

        # 清除缓存按钮
        self.btn_clear_cache = tk.Button(
            btn_fetch_frame, text="清除缓存", bg="#DC3545", fg="white", font=(SYS_FONT, 9, "bold"),
            command=self.clear_cache_action
        )
        self.btn_clear_cache.pack(side="left", padx=(0, 5))

        tk.Label(self.course_frame, text="可选课程列表:\n(勾选要报名的课程)", font=(SYS_FONT, 9, "bold"), justify="right").grid(row=1, column=0, sticky="ne", pady=pad_y)
        
        self.checklist = ScrollableChecklist(self.course_frame, height=130)
        self.checklist.grid(row=1, column=1, padx=pad_x, pady=pad_y, sticky="ew")

        tk.Label(self.course_frame, text="轮询间隔(秒):", font=(SYS_FONT, 10)).grid(row=2, column=0, sticky="e", pady=pad_y)
        self.interval_entry = tk.Entry(self.course_frame, width=38, font=(SYS_FONT, 10))
        self.interval_entry.insert(0, "0")
        self.interval_entry.grid(row=2, column=1, padx=pad_x, pady=pad_y)

        self.skip_full_var = tk.BooleanVar(value=True)
        self.chk_skip_full = tk.Checkbutton(
            self.course_frame, text="自动跳过无余量课程", variable=self.skip_full_var, font=(SYS_FONT, 9)
        )
        self.chk_skip_full.grid(row=3, column=1, sticky="w", padx=pad_x)

        self.skip_unopened_var = tk.BooleanVar(value=True)
        self.chk_skip_unopened = tk.Checkbutton(
            self.course_frame, text="自动跳过未开放选课的课程", variable=self.skip_unopened_var, font=(SYS_FONT, 9)
        )
        self.chk_skip_unopened.grid(row=4, column=1, sticky="w", padx=pad_x)

        # 通用控制选项
        opt_frame = tk.Frame(root)
        opt_frame.pack(fill="x", padx=pad_x, pady=(2, 5))

        self.show_browser_var = tk.BooleanVar(value=False)
        self.chk_browser = tk.Checkbutton(
            opt_frame, text="显示浏览器操作画面", variable=self.show_browser_var, font=(SYS_FONT, 9)
        )
        self.chk_browser.pack(side="left", padx=10)

        btn_frame = tk.Frame(root)
        btn_frame.pack(fill="x", padx=pad_x, pady=5)

        self.btn_start = tk.Button(
            btn_frame, text="开始 Start", bg="#007ACC", fg="white", font=(SYS_FONT, 11, "bold"), width=16,
            command=self.start_task_thread
        )
        self.btn_start.pack(side="left", padx=15)

        self.btn_stop = tk.Button(
            btn_frame, text="停止 Stop", bg="#DC3545", fg="white", font=(SYS_FONT, 11, "bold"), width=12, state="disabled",
            command=self.stop_task
        )
        self.btn_stop.pack(side="left", padx=5)

        # 日志
        tk.Label(root, text="运行日志与状态:", font=(SYS_FONT, 9, "bold")).pack(anchor="w", padx=pad_x)
        self.log_text = tk.Text(root, height=8, width=72, font=("Courier", 10), state="disabled", bg="#F8F9FA")
        self.log_text.pack(padx=pad_x, pady=(0, pad_y))

        self.on_school_changed()
        self.log(f"已自动识别到 {len(self.detected_browsers)} 个本地系统已安装浏览器")

        # 启动时自动加载本地保存的课程列表缓存
        cached_courses = load_courses_cache()
        if cached_courses:
            self.checklist.set_items(cached_courses)
            self.log(f"已自动加载本地保存的 {len(cached_courses)} 门课程列表")

    def on_school_changed(self, event=None):
        selected_school = self.school_cb.get().strip()
        features = list(SCHOOL_FEATURES.get(selected_school, {}).keys())

        self.feature_cb["values"] = features
        if features:
            self.feature_cb.current(0)
        else:
            self.feature_cb.set("")

        self.on_feature_changed()

    def on_feature_changed(self, event=None):
        selected_school = self.school_cb.get().strip()
        selected_feature = self.feature_cb.get().strip()

        feature_info = SCHOOL_FEATURES.get(selected_school, {}).get(selected_feature, {})
        f_type = feature_info.get("type")

        self.download_frame.pack_forget()
        self.course_frame.pack_forget()

        if f_type == "download":
            self.download_frame.pack(fill="x", padx=12, pady=5, before=self.chk_browser.master)
            self.btn_stop.config(state="disabled")
        elif f_type == "course_select":
            self.course_frame.pack(fill="x", padx=12, pady=5, before=self.chk_browser.master)

    def browse_folder(self):
        folder = filedialog.askdirectory(title="选择保存文件夹")
        if folder:
            self.path_entry.delete(0, tk.END)
            self.path_entry.insert(0, folder)

    def log(self, message):
        self.log_text.config(state="normal")
        self.log_text.insert(tk.END, message + "\n")
        self.log_text.see(tk.END)
        self.log_text.config(state="disabled")

    def ask_user_continue(self, title, message):
        result = [False]
        evt = threading.Event()

        def _show():
            res = messagebox.askyesno(title, message, parent=self.root)
            result[0] = res
            evt.set()

        self.root.after(0, _show)
        evt.wait()
        return result[0]

    def stop_task(self):
        self.stop_event.set()
        self.log("停止...")

    def clear_cache_action(self):
        """清除缓存按钮点击逻辑"""
        clear_courses_cache()
        self.checklist.clear_items()
        self.log("已手动清除本地课程列表缓存及界面展示")
        messagebox.showinfo("提示", "已成功清除本地课程列表缓存！")

    # 启动浏览器（调用系统已安装的浏览器）
    async def _launch_browser_instance(self, p, browser_cfg, is_headless):
        engine_type = browser_cfg.get("engine", "chromium")

        if engine_type == "chromium" and "channel" not in browser_cfg and "executable_path" not in browser_cfg:
            raise Exception("请确保系统已安装网页浏览器(Chrome/Edge 等)")

        if engine_type == "firefox":
            launcher = p.firefox
        elif engine_type == "webkit":
            launcher = p.webkit
        else:
            launcher = p.chromium

        kwargs = {"headless": is_headless}
        if "channel" in browser_cfg:
            kwargs["channel"] = browser_cfg["channel"]
        elif "executable_path" in browser_cfg:
            kwargs["executable_path"] = browser_cfg["executable_path"]

        return await launcher.launch(**kwargs)

    # 根据用户选择或优先级加载浏览器
    async def _obtain_browser_instance(self, p, selected_browser, is_headless, print_log):
        if selected_browser == "自动检测":
            ordered = get_ordered_detected_browsers(self.detected_browsers)
            if not ordered:
                raise Exception("未检测到系统安装的任何可用浏览器(Chrome/Edge 等)")

            browser = None
            used_name = ""
            for b_name, cfg in ordered:
                try:
                    browser = await self._launch_browser_instance(p, cfg, is_headless)
                    used_name = b_name
                    break
                except Exception:
                    print_log(f"  ↳ 尝试启动系统浏览器 [{b_name}] 失败，尝试下一个...")
                    continue

            if not browser:
                raise Exception("无法启动任何系统已安装的浏览器！")

            return browser, used_name
        else:
            cfg = self.detected_browsers.get(selected_browser)
            if not cfg:
                raise Exception(f"未找到浏览器 [{selected_browser}] 的配置！")

            browser = await self._launch_browser_instance(p, cfg, is_headless)
            return browser, selected_browser

    # 抓取课程列表入口
    def start_fetch_courses(self):
        selected_school = self.school_cb.get().strip()
        selected_feature = self.feature_cb.get().strip()
        username = self.username_entry.get().strip()
        password = self.password_entry.get().strip()
        selected_browser = self.browser_cb.get().strip()
        show_browser = self.show_browser_var.get()

        if not username or not password:
            messagebox.showwarning("WARNING", "请先填写学号与密码！")
            return

        feature_info = SCHOOL_FEATURES.get(selected_school, {}).get(selected_feature, {})
        fetch_handler = feature_info.get("fetch_handler")
        if not fetch_handler:
            messagebox.showerror("ERROR", "当前院校/功能未配置课程抓取处理器！")
            return

        # 重新从平台抓取时，清空本地旧列表与缓存
        clear_courses_cache()
        self.checklist.clear_items()
        self.log("开始从平台抓取课程列表，已清除原有本地缓存...")

        self.btn_fetch.config(state="disabled")

        def _fetch_thread():
            asyncio.run(self._run_fetch_async(fetch_handler, selected_browser, username, password, show_browser))

        threading.Thread(target=_fetch_thread, daemon=True).start()

    async def _run_fetch_async(self, fetch_handler, selected_browser, username, password, show_browser):
        def print_log(msg):
            self.root.after(0, self.log, msg)

        try:
            async with async_playwright() as p:
                browser, used_browser_name = await self._obtain_browser_instance(p, selected_browser, not show_browser, print_log)
                print_log(f"已调用系统浏览器: [{used_browser_name}]")

                context = await browser.new_context()
                page = await context.new_page()

                courses = await fetch_handler(page, username, password, {}, print_log)
                await browser.close()

                # 自动保存抓取到的课程列表到本地
                save_courses_cache(courses)

                self.root.after(0, lambda: self.checklist.set_items(courses))
                self.root.after(0, lambda: self.log(f"已自动保存 {len(courses)} 门课程到本地列表"))
                self.root.after(0, lambda: messagebox.showinfo(
                    "抓取成功", f"成功抓取到 {len(courses)} 门课程并已自动保存！\n\n请在列表中【勾选】想要报名的课程，完成后点击【开始 Start】按钮发起抢课。"
                ))
        except Exception as e:
            print_log(f"抓取课程列表失败: {str(e)}")
            self.root.after(0, lambda err=str(e): messagebox.showerror("ERROR", f"抓取课程失败：\n{err}"))
        finally:
            self.root.after(0, lambda: self.btn_fetch.config(state="normal"))

    # 开始执行主任务
    def start_task_thread(self):
        selected_school = self.school_cb.get().strip()
        selected_feature = self.feature_cb.get().strip()
        selected_browser = self.browser_cb.get().strip()
        username = self.username_entry.get().strip()
        password = self.password_entry.get().strip()
        show_browser = self.show_browser_var.get()

        if "未检测到" in selected_browser:
            messagebox.showerror("ERROR", "电脑内未安装可用浏览器，请先安装 Chrome/Edge 等浏览器后再尝试")
            return

        if not username or not password:
            messagebox.showwarning("WARNING", "请填写学号与密码")
            return

        feature_info = SCHOOL_FEATURES.get(selected_school, {}).get(selected_feature)
        if not feature_info:
            messagebox.showerror("ERROR", "未找到对应的功能设置")
            return

        f_type = feature_info["type"]
        kwargs = {}

        if f_type == "download":
            save_dir = self.path_entry.get().strip()
            if not os.path.exists(save_dir):
                messagebox.showwarning("WARNING", "指定的路径非法")
                return
            kwargs["save_dir"] = save_dir

        elif f_type == "course_select":
            selected_courses = self.checklist.get_selected()
            if not selected_courses:
                messagebox.showwarning("WARNING", "请先在列表框中勾选至少一个想要报名的课程\n（若列表为空，请先点击【获取课程列表】按钮）")
                return

            try:
                retry_interval = float(self.interval_entry.get().strip())
            except ValueError:
                messagebox.showwarning("WARNING", "间隔时间数值非法")
                return

            kwargs["target_courses"] = selected_courses
            kwargs["retry_interval"] = retry_interval
            kwargs["skip_full"] = self.skip_full_var.get()
            kwargs["skip_unopened"] = self.skip_unopened_var.get()
            kwargs["stop_event"] = self.stop_event
            kwargs["ask_user"] = self.ask_user_continue

        self.stop_event.clear()
        self.btn_start.config(state="disabled")
        if f_type == "course_select":
            self.btn_stop.config(state="normal")

        threading.Thread(
            target=self.run_async_task, 
            args=(feature_info["handler"], selected_browser, username, password, kwargs, show_browser), 
            daemon=True
        ).start()

    def run_async_task(self, handler_func, selected_browser, username, password, kwargs, show_browser):
        asyncio.run(self.execute_playwright(handler_func, selected_browser, username, password, kwargs, show_browser))

    async def execute_playwright(self, handler_func, selected_browser, username, password, kwargs, show_browser):
        def print_log(msg):
            self.root.after(0, self.log, msg)

        is_headless = not show_browser

        try:
            async with async_playwright() as p:
                browser, used_browser_name = await self._obtain_browser_instance(p, selected_browser, is_headless, print_log)
                print_log(f"已调用系统浏览器: [{used_browser_name}]，模式: {'FRONT' if show_browser else 'BACKGROUND'}")

                context = await browser.new_context(accept_downloads=True)
                page = await context.new_page()

                res = await handler_func(page, username, password, kwargs, print_log)

                await browser.close()

                if res:
                    self.root.after(0, lambda: messagebox.showinfo("完成", "已完成操作！"))

        except Exception as e:
            print_log(f"失败: {str(e)}")
            self.root.after(0, lambda err_msg=str(e): messagebox.showerror("ERROR", f"执行失败：\n{err_msg}"))
        finally:
            self.root.after(0, lambda: self.btn_start.config(state="normal"))
            self.root.after(0, lambda: self.btn_stop.config(state="disabled"))


if __name__ == "__main__":
    root = tk.Tk()
    app = UnifiedCampusApp(root)
    root.mainloop()