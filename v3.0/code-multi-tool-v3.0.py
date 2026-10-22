import os
import sys
import re
import io
import json
import asyncio
import tempfile
import threading
import webbrowser
import tkinter as tk
from datetime import datetime, timedelta, timezone
from tkinter import ttk, filedialog, messagebox, simpledialog

import pdfplumber
from playwright.async_api import async_playwright

try:
    from PIL import Image, ImageTk
except Exception:  # Pillow 未安装时二维码窗口给出提示
    Image = None
    ImageTk = None

# 程序目錄與可選的 vendored 依賴（例如 qrcode）
APP_DIR = (
    os.path.dirname(sys.executable)
    if getattr(sys, "frozen", False)
    else os.path.dirname(os.path.abspath(__file__))
)
for _path in (APP_DIR, os.path.join(APP_DIR, "_vendor")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from cloud_client import (  # noqa: E402
    CloudAPI,
    CloudError,
    SCHOOL_CODES as CLOUD_SCHOOL_CODES,
    caldav_url,
    fetch_latest_release,
    is_newer_version,
    load_cloud_config,
    make_qr_png,
    normalize_base_url,
    resolve_update_source,
    save_cloud_config,
    subscription_url,
    webcal_url,
)

APP_VERSION = "3.0.0"

# 根据系统UI默认字体
SYS_FONT = "PingFang SC" if sys.platform == "darwin" else "Microsoft YaHei"


# ==================== 课表 PDF 解析与 ICS 生成（内嵌自 pdf.py） ====================

WEEKDAY_OFFSETS = {
    "星期一": 0,
    "星期二": 1,
    "星期三": 2,
    "星期四": 3,
    "星期五": 4,
    "星期六": 5,
    "星期日": 6,
}

PERIOD_TIMES = {
    1: ("08:20", "09:00"),
    2: ("09:05", "09:45"),
    3: ("10:00", "10:40"),
    4: ("10:45", "11:25"),
    5: ("12:10", "12:50"),
    6: ("12:55", "13:35"),
    7: ("14:00", "14:40"),
    8: ("14:45", "15:25"),
    9: ("15:35", "16:15"),
    10: ("16:20", "17:00"),
    11: ("18:15", "18:55"),
    12: ("19:00", "19:40"),
}

# 广州大学课表节次时间（与私立华联不同）
GZHU_PERIOD_TIMES = {
    1: ("08:30", "09:15"),
    2: ("09:20", "10:05"),
    3: ("10:25", "11:10"),
    4: ("11:15", "12:00"),
    5: ("13:50", "14:35"),
    6: ("14:40", "15:25"),
    7: ("15:45", "16:30"),
    8: ("16:35", "17:20"),
    9: ("18:20", "19:05"),
    10: ("19:10", "19:55"),
    11: ("20:00", "20:45"),
}


def _escape_ics_text(value):
    text = str(value or "")
    text = text.replace("\\", "\\\\").replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\n", "\\n").replace(";", "\\;").replace(",", "\\,")
    return text


def _fold_ics_line(line, limit=75):
    """按 iCalendar 的 UTF-8 字节长度折行。"""
    chunks = []
    current = ""
    current_length = 0
    for character in line:
        byte_length = len(character.encode("utf-8"))
        if current and current_length + byte_length > limit:
            chunks.append(current)
            current = " "
            current_length = 1
        current += character
        current_length += byte_length
    chunks.append(current)
    return "\r\n".join(chunks)


def _text_bytes(value, byte_string_type, text_string_type):
    if isinstance(value, text_string_type):
        return value.original_bytes
    if isinstance(value, byte_string_type):
        return bytes(value)
    return None


def _make_unicode_repaired_copy(pdf_path):
    """修复部分 UniGB PDF 缺少 ToUnicode 映射导致的中文乱码。"""
    try:
        from pypdf import PdfReader, PdfWriter
        from pypdf.generic import (
            ByteStringObject,
            ContentStream,
            DecodedStreamObject,
            NameObject,
            TextStringObject,
        )
        from pdfminer.cmapdb import CMapDB
    except ImportError:
        return None

    reader = PdfReader(pdf_path, strict=False)
    font_info_by_page = []
    encoding_maps = {}
    for page in reader.pages:
        resources = page.get("/Resources")
        resources = resources.get_object() if resources else {}
        fonts = resources.get("/Font")
        fonts = fonts.get_object() if fonts else {}
        page_font_info = {}
        for resource_name, font_ref in fonts.items():
            font = font_ref.get_object()
            base_font = str(font.get("/BaseFont", ""))
            encoding = str(font.get("/Encoding", "")).lstrip("/")
            if (
                encoding == "UniGB-UCS2-H"
                and "ToUnicode" not in font
                and "STSong-Light" in base_font
            ):
                page_font_info[str(resource_name)] = (base_font, encoding)
                encoding_maps.setdefault(encoding, CMapDB.get_cmap(encoding).code2cid)
        font_info_by_page.append(page_font_info)

    if not any(font_info_by_page):
        return None

    unicode_by_font = {}
    for page_index, page in enumerate(reader.pages):
        page_font_info = font_info_by_page[page_index]
        if not page_font_info:
            continue
        current_font = None
        content = ContentStream(page.get_contents(), reader)
        for operands, operator in content.operations:
            if operator == b"Tf" and operands:
                current_font = str(operands[0])
                continue
            font_info = page_font_info.get(current_font)
            if not font_info or operator not in (b"Tj", b"TJ"):
                continue

            values = operands if isinstance(operands, list) else [operands]
            for value in values:
                raw = _text_bytes(value, ByteStringObject, TextStringObject)
                if not raw or len(raw) % 2:
                    continue
                encoding = font_info[1]
                code_to_cid = encoding_maps[encoding]
                mapping = unicode_by_font.setdefault(font_info, {})
                for offset in range(0, len(raw), 2):
                    source_code = raw[offset] * 256 + raw[offset + 1]
                    cid = code_to_cid.get(raw[offset], {}).get(raw[offset + 1])
                    if cid is None or cid > 0xFFFF or 0xD800 <= source_code <= 0xDFFF:
                        continue
                    previous = mapping.get(cid)
                    if previous is not None and previous != source_code:
                        mapping[cid] = None
                    elif cid not in mapping:
                        mapping[cid] = source_code

    cmaps_by_font = {}
    for font_info, mapping in unicode_by_font.items():
        entries = [(cid, code) for cid, code in mapping.items() if code is not None]
        if not entries:
            continue
        chunks = [
            b"/CIDInit /ProcSet findresource begin\n12 dict begin\nbegincmap\n",
            b"/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def\n",
            b"/CMapName /SchedulePDFUnicode def\n/CMapType 2 def\n",
            b"1 begincodespacerange\n<0000> <FFFF>\nendcodespacerange\n",
        ]
        entries.sort()
        for offset in range(0, len(entries), 100):
            chunk = entries[offset : offset + 100]
            chunks.append(f"{len(chunk)} beginbfchar\n".encode("ascii"))
            chunks.extend(
                f"<{cid:04X}> <{unicode_code:04X}>\n".encode("ascii")
                for cid, unicode_code in chunk
            )
            chunks.append(b"endbfchar\n")
        chunks.append(b"endcmap\nCMapName currentdict /CMap defineresource pop\nend\nend\n")
        cmaps_by_font[font_info] = b"".join(chunks)

    if not cmaps_by_font:
        return None

    writer = PdfWriter()
    writer.clone_document_from_reader(reader)
    for page in writer.pages:
        resources = page.get("/Resources")
        resources = resources.get_object() if resources else {}
        fonts = resources.get("/Font")
        fonts = fonts.get_object() if fonts else {}
        for font_ref in fonts.values():
            font = font_ref.get_object()
            font_info = (
                str(font.get("/BaseFont", "")),
                str(font.get("/Encoding", "")).lstrip("/"),
            )
            cmap_data = cmaps_by_font.get(font_info)
            if cmap_data and "ToUnicode" not in font:
                cmap_stream = DecodedStreamObject()
                cmap_stream.set_data(cmap_data)
                font[NameObject("/ToUnicode")] = writer._add_object(cmap_stream)

    file_descriptor, repaired_path = tempfile.mkstemp(prefix="schedule_pdf_", suffix=".pdf")
    os.close(file_descriptor)
    try:
        with open(repaired_path, "wb") as output_file:
            writer.write(output_file)
    except Exception:
        os.unlink(repaired_path)
        raise
    return repaired_path


def _read_pdf_tables(pdf_path):
    repaired_path = _make_unicode_repaired_copy(pdf_path)
    read_path = repaired_path or pdf_path
    try:
        page_tables = []
        with pdfplumber.open(read_path) as pdf:
            for page_num, page in enumerate(pdf.pages, start=1):
                for table_num, table in enumerate(page.extract_tables() or [], start=1):
                    if table:
                        cleaned_table = [
                            ["" if cell is None else str(cell).strip() for cell in row]
                            for row in table
                        ]
                        page_tables.append((page_num, table_num, cleaned_table))
        return page_tables
    finally:
        if repaired_path and os.path.exists(repaired_path):
            os.unlink(repaired_path)


def _find_schedule_header(table):
    """找到包含节次和星期列的课表表头。"""
    weekday_names = {
        "星期一": "星期一", "周一": "星期一",
        "星期二": "星期二", "周二": "星期二",
        "星期三": "星期三", "周三": "星期三",
        "星期四": "星期四", "周四": "星期四",
        "星期五": "星期五", "周五": "星期五",
        "星期六": "星期六", "周六": "星期六",
        "星期日": "星期日", "星期天": "星期日", "周日": "星期日",
    }
    for row_index, row in enumerate(table):
        day_columns = {}
        period_column = None
        time_column = None
        for column_index, cell in enumerate(row):
            text = re.sub(r"\s+", "", str(cell or ""))
            if text in weekday_names:
                day_columns[column_index] = weekday_names[text]
            elif text in ("节次", "课次"):
                period_column = column_index
            elif text in ("时间段", "时段"):
                time_column = column_index
        if day_columns and period_column is not None:
            return row_index, period_column, time_column, day_columns
    return None


def _split_course_blocks(cell_text):
    """按课程标题及其节次标记拆分同一格中的多门课程。"""
    marker_pattern = re.compile(
        r"\((\d{1,2})\s*[-~～至]\s*(\d{1,2})\s*节\)"
    )
    markers = list(marker_pattern.finditer(str(cell_text or "")))
    blocks = []
    for marker_index, marker in enumerate(markers):
        prefix_lines = [
            line.strip()
            for line in str(cell_text[: marker.start()]).splitlines()
            if line.strip()
        ]
        if not prefix_lines:
            continue
        course_name = re.sub(r"\s+", " ", prefix_lines[-1]).strip(" ：:/")
        next_start = (
            markers[marker_index + 1].start()
            if marker_index + 1 < len(markers)
            else len(cell_text)
        )
        block_text = str(cell_text[marker.end() : next_start]).strip()
        blocks.append(
            (course_name, block_text, (int(marker.group(1)), int(marker.group(2))))
        )
    return blocks


def _extract_schedule(page_tables):
    """解析跨页课表，并整理为一张按课程排列的表。"""
    headers = ["星期", "时段", "节次", "课程", "周次", "地点", "教师", "教学班", "PDF 页"]
    weekday_order = {day: index for index, day in enumerate(WEEKDAY_OFFSETS)}
    active_day_columns = None
    active_period_column = None
    active_time_column = None
    active_time_slot = ""
    schedule_found = False
    events = []
    event_order = 0
    weeks_pattern = re.compile(
        r"(?<!\d)(\d{1,2}\s*(?:[-~～至]\s*\d{1,2})?\s*周"
        r"\s*(?:[（(]?\s*[单双]\s*周?\s*[）)]?)?)"
    )

    for page_num, _table_num, table in page_tables:
        found_header = _find_schedule_header(table)
        if found_header:
            header_index, active_period_column, active_time_column, active_day_columns = found_header
            schedule_found = True
            first_data_row = header_index + 1
        elif active_day_columns is not None:
            first_data_row = 0
        else:
            continue

        width = max(
            max(active_day_columns) + 1,
            active_period_column + 1,
            (active_time_column + 1) if active_time_column is not None else 0,
            max((len(row) for row in table), default=0),
        )
        for row in table[first_data_row:]:
            values = list(row) + [""] * (width - len(row))
            if active_time_column is not None:
                time_slot = str(values[active_time_column] or "").strip()
                if time_slot:
                    active_time_slot = time_slot

            period_text = str(values[active_period_column] or "").strip()
            period_match = re.fullmatch(r"(?:第\s*)?(\d{1,2})(?:\s*节)?", period_text)
            if not period_match:
                continue
            row_period = int(period_match.group(1))

            for day_column, weekday in active_day_columns.items():
                cell_text = str(values[day_column] or "").strip()
                if not cell_text:
                    continue
                for course, block_text, block_periods in _split_course_blocks(cell_text):
                    if not course:
                        continue
                    first_period, last_period = block_periods or (row_period, row_period)
                    flattened = re.sub(r"\s+", " ", block_text).strip()
                    weeks_match = weeks_pattern.search(flattened)

                    def capture_field(label_pattern):
                        match = re.search(
                            rf"{label_pattern}\s*[:：]\s*([^/]+)", flattened
                        )
                        return re.sub(r"\s+", " ", match.group(1)).strip() if match else ""

                    events.append((
                        weekday_order[weekday],
                        first_period,
                        event_order,
                        [
                            weekday,
                            active_time_slot,
                            f"{first_period}-{last_period}节",
                            course,
                            weeks_match.group(1).replace(" ", "") if weeks_match else "",
                            capture_field("场地(?:地点)?"),
                            capture_field("教师"),
                            capture_field("教学班(?:组成)?"),
                            str(page_num),
                        ],
                    ))
                    event_order += 1

    if not schedule_found:
        return [], [], False
    events.sort(key=lambda event: event[:3])
    return headers, [event[3] for event in events], True


def extract_schedule_data(pdf_path):
    """读取课表 PDF，返回整理后的表头和课程安排行。"""
    headers, rows, schedule_found = _extract_schedule(_read_pdf_tables(pdf_path))
    if not schedule_found:
        raise ValueError(
            "未识别到包含星期和节次的课程表。若 PDF 页面能显示中文但识别不到表头，请安装 pypdf 后重试。"
        )
    if not rows:
        raise ValueError("识别到了课表，但没有提取到课程安排。")
    return headers, rows


def _week_series(weeks_text):
    """将周次文字压缩成 DTSTART 周、循环间隔和循环次数。"""
    token_pattern = re.compile(
        r"(?<!\d)(\d{1,2})(?:\s*[-~～至]\s*(\d{1,2}))?\s*周"
        r"(?:\s*[（(]?\s*([单双])\s*周?\s*[）)]?)?"
    )
    explicit_series = []
    ordinary_weeks = set()
    for match in token_pattern.finditer(str(weeks_text or "")):
        first = int(match.group(1))
        last = int(match.group(2) or first)
        parity = match.group(3)
        if first < 1 or last < first:
            raise ValueError(f"周次范围不正确：{weeks_text}")
        weeks = list(range(first, last + 1))
        if parity == "单":
            weeks = [week for week in weeks if week % 2 == 1]
        elif parity == "双":
            weeks = [week for week in weeks if week % 2 == 0]
        if not weeks:
            continue
        if parity:
            explicit_series.append((weeks[0], 2, len(weeks)))
        else:
            ordinary_weeks.update(weeks)

    while ordinary_weeks:
        start = min(ordinary_weeks)
        step = 1 if start + 1 in ordinary_weeks else 2 if start + 2 in ordinary_weeks else 1
        series_weeks = [start]
        ordinary_weeks.remove(start)
        next_week = start + step
        while next_week in ordinary_weeks:
            series_weeks.append(next_week)
            ordinary_weeks.remove(next_week)
            next_week += step
        explicit_series.append((start, step, len(series_weeks)))

    return sorted(set(explicit_series))


def _earliest_start_week(headers, rows):
    """返回课表中最小的起始周次（最早开课周）；无法识别时返回 None。"""
    column_index = {header: index for index, header in enumerate(headers)}
    if "周次" not in column_index:
        return None
    week_index = column_index["周次"]
    earliest = None
    for row in rows:
        if week_index >= len(row):
            continue
        try:
            series = _week_series(str(row[week_index]).strip())
        except ValueError:
            continue
        for start_week, _interval, _count in series:
            if earliest is None or start_week < earliest:
                earliest = start_week
    return earliest


def build_ics_content(headers, rows, first_lesson_start, period_times=PERIOD_TIMES):
    """按课表周次和星期生成带每周循环规则的 iCalendar 内容。"""
    if first_lesson_start.weekday() != 0:
        raise ValueError("第一周第 1 节时间应填写第一周星期一的日期。")
    first_start = period_times[1][0]
    if first_lesson_start.strftime("%H:%M") != first_start:
        raise ValueError(
            f"第一周第 1 节的开始时间应为 {first_start}，与提供的上课时间表一致。"
        )

    column_index = {header: index for index, header in enumerate(headers)}
    required_columns = {"星期", "节次", "课程", "周次"}
    if not required_columns.issubset(column_index):
        raise ValueError("课表缺少生成日历所需的星期、节次、课程或周次列。")

    period_pattern = re.compile(r"(\d{1,2})\s*(?:-\s*(\d{1,2}))?\s*节?")
    max_period = max(period_times)
    events = []
    for row_number, row in enumerate(rows, start=1):
        def value(name):
            index = column_index[name]
            return str(row[index]).strip() if index < len(row) else ""

        course = value("课程")
        if not course:
            continue
        weekday = value("星期")
        if weekday not in WEEKDAY_OFFSETS:
            raise ValueError(f"第 {row_number} 行的星期无法识别：{weekday or '空白'}")

        period_match = period_pattern.search(value("节次"))
        if not period_match:
            raise ValueError(f"第 {row_number} 行的节次无法识别：{value('节次') or '空白'}")
        first_period = int(period_match.group(1))
        last_period = int(period_match.group(2) or first_period)
        if first_period < 1 or last_period < first_period:
            raise ValueError(f"第 {row_number} 行的节次范围不正确：{value('节次')}")
        if first_period not in period_times or last_period not in period_times:
            raise ValueError(
                f"第 {row_number} 行包含暂未配置时间的节次（当前仅配置第 1–{max_period} 节）：{course}"
            )

        series_list = _week_series(value("周次"))
        if not series_list:
            raise ValueError(f"第 {row_number} 行缺少可识别的周次：{course}")

        location = re.sub(r"\s+", "", value("地点"))
        if location in {"未排地点", "未安排地点", "无地点", "暂无地点", "待定"}:
            location = ""
        summary = f"{course} - {location}" if location else course
        period_start = datetime.strptime(period_times[first_period][0], "%H:%M").time()
        period_end = datetime.strptime(period_times[last_period][1], "%H:%M").time()

        for start_week, interval, count in series_list:
            event_date = first_lesson_start.date() + timedelta(
                days=(start_week - 1) * 7 + WEEKDAY_OFFSETS[weekday]
            )
            event_start = datetime.combine(event_date, period_start)
            event_end = datetime.combine(event_date, period_end)
            events.append((event_start, event_end, summary, start_week, interval, count))

    if not events:
        raise ValueError("没有可生成日历事件的课程。")

    year = first_lesson_start.year
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Class Schedule//CN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:课程表",
        "X-WR-TIMEZONE:Asia/Shanghai",
    ]
    dtstamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    for index, (start, end, summary, start_week, interval, count) in enumerate(events, start=1):
        rrule = "FREQ=WEEKLY;"
        if interval > 1:
            rrule += f"INTERVAL={interval};"
        rrule += f"COUNT={count}"
        uid = f"course-{year}-week{start_week}-{index}@schedule"
        lines.extend([
            "BEGIN:VEVENT",
            f"UID:{uid}",
            f"DTSTAMP:{dtstamp}",
            f"SUMMARY:{_escape_ics_text(summary)}",
            "LOCATION:",
            f"DTSTART;TZID=Asia/Shanghai:{start.strftime('%Y%m%dT%H%M%S')}",
            f"DTEND;TZID=Asia/Shanghai:{end.strftime('%Y%m%dT%H%M%S')}",
            f"RRULE:{rrule}",
            "END:VEVENT",
        ])
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold_ics_line(line) for line in lines) + "\r\n"


def build_ics_content_gzhu(headers, rows, first_lesson_start):
    """广州大学课表生成 iCalendar（节次时间与私立华联不同）。"""
    return build_ics_content(headers, rows, first_lesson_start, period_times=GZHU_PERIOD_TIMES)


# ==================== 广州大学课表 PDF 解析（表格结构与私立华联不同） ====================

GZHU_WEEKDAY_NAMES = ["星期日", "星期一", "星期二", "星期三", "星期四", "星期五", "星期六"]

_GZHU_MARKER = re.compile(r"^\((\d{1,2})-(\d{1,2})节\)")


def _is_gzhu_name_line(line):
    """判断一行是否为课程名（而非字段续行）。"""
    text = line.strip()
    if not text:
        return False
    if ":" in text or "/" in text:
        return False
    return True


def _gzhu_capture_field(text, label):
    # Each timetable course starts with a name line before its next period
    # marker. Keep a field on its own line so that the following course name
    # cannot be appended to the previous teacher/location value.
    match = re.search(rf"/{label}:([^/\r\n]*)", text)
    return match.group(1).strip() if match else ""


def _parse_gzhu_column(lines):
    """解析某星期列（多行）里的课程。

    返回 [(课程名, 起始节, 结束节, 周次文本, 地点, 教师), ...]
    """
    courses = []
    index = 0
    total = len(lines)
    while index < total:
        marker = _GZHU_MARKER.match(lines[index].strip())
        if not marker:
            index += 1
            continue
        first_period = int(marker.group(1))
        last_period = int(marker.group(2))

        # 课程名：标记行上方连续的“非字段”行（课程名可能跨 1~2 行）
        name_parts = []
        cursor = index - 1
        while cursor >= 0 and _is_gzhu_name_line(lines[cursor]):
            name_parts.append(lines[cursor].strip())
            cursor -= 1
        name_parts.reverse()
        course_name = "".join(name_parts).rstrip("*&#@").strip()

        # 字段：从标记之后直到下一个标记行
        fields = lines[index][marker.end():]
        next_index = index + 1
        while next_index < total and not _GZHU_MARKER.match(lines[next_index].strip()):
            fields += "\n" + lines[next_index]
            next_index += 1

        weeks_text = fields.split("/", 1)[0].strip()
        location = _gzhu_capture_field(fields, "场地")
        teacher = _gzhu_capture_field(fields, "教师")

        if course_name and weeks_text:
            courses.append((course_name, first_period, last_period, weeks_text, location, teacher))
        index = next_index
    return courses


def _detect_gzhu_weekday_columns(page):
    """根据表头文字位置确定七个星期列的水平裁剪范围，以及表头下方起始 y。"""
    centers = {}
    header_bottom = None
    for word in page.extract_words():
        text = word["text"].strip()
        if text in GZHU_WEEKDAY_NAMES:
            if text not in centers:
                centers[text] = (word["x0"] + word["x1"]) / 2
            header_bottom = max(header_bottom or 0, word["bottom"])
    if len(centers) != len(GZHU_WEEKDAY_NAMES):
        return None, None
    first_center = centers[GZHU_WEEKDAY_NAMES[0]]
    last_center = centers[GZHU_WEEKDAY_NAMES[-1]]
    spacing = (last_center - first_center) / (len(GZHU_WEEKDAY_NAMES) - 1)
    columns = []
    for name in GZHU_WEEKDAY_NAMES:
        center = centers[name]
        columns.append((name, center - spacing / 2, center + spacing / 2))
    return columns, header_bottom


def extract_schedule_data_gzhu(pdf_path):
    """读取广州大学课表 PDF，返回整理后的表头和课程安排行。"""
    headers = ["星期", "节次", "课程", "周次", "地点", "教师"]
    rows = []
    with pdfplumber.open(pdf_path) as pdf:
        if not pdf.pages:
            raise ValueError("PDF 没有任何页面。")
        columns, header_bottom = _detect_gzhu_weekday_columns(pdf.pages[0])
        if not columns:
            raise ValueError("未识别到广州大学课表的星期表头（星期日~星期六）。")
        for weekday, x0, x1 in columns:
            column_text = ""
            for page_index, page in enumerate(pdf.pages):
                # 首页从表头下方开始裁剪，避开标题行与表头；后续页为续接内容，从顶部开始。
                top = header_bottom if page_index == 0 else 0
                cropped = page.crop((x0, top, x1, page.height))
                text = (cropped.extract_text() or "").strip()
                if text:
                    column_text += text + "\n"
            lines = [line for line in column_text.splitlines() if line.strip()]
            if lines and lines[0].strip() in GZHU_WEEKDAY_NAMES:
                lines = lines[1:]
            for course_name, first_period, last_period, weeks_text, location, teacher in _parse_gzhu_column(lines):
                rows.append([
                    weekday,
                    f"{first_period}-{last_period}节",
                    course_name,
                    weeks_text,
                    location,
                    teacher,
                ])
    if not rows:
        raise ValueError("识别到了课表，但没有提取到课程安排。")
    return headers, rows

# 动态获取程序运行根目录 兼容 .exe 打包后运行环境
def get_app_dir():
    return APP_DIR

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


# 本地登录信息缓存（学号/密码/API key），与课程列表缓存一样存在程序目录
LOGIN_CACHE_FILE = os.path.join(get_app_dir(), "saved_login.json")


def save_login_cache(username, password, api_key):
    """保存学号/密码/API key 到本地 JSON。"""
    try:
        with open(LOGIN_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(
                {"username": username or "", "password": password or "", "api_key": api_key or ""},
                f, ensure_ascii=False, indent=2,
            )
    except Exception:
        pass


def load_login_cache():
    """读取本地保存的学号/密码/API key。"""
    if os.path.exists(LOGIN_CACHE_FILE):
        try:
            with open(LOGIN_CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception:
            return {}
    return {}


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


async def login_xgxt_hlu(page, username, password, print_log):
    """学工系统自动登录逻辑（适配最新 Element UI 登录页）"""
    print_log("  ↳ 正在访问学工系统登录界面...")
    await page.goto("http://xgxt.hlu.edu.cn/", wait_until="domcontentloaded")

    # 针对登录页面 DOM 结构的准确定位
    un_input = page.locator("input[placeholder*='账号'], input[placeholder*='学号'], input[placeholder*='用户名'], input[type='text']").first
    pwd_input = page.locator("input[placeholder*='密码'], input[type='password']").first
    login_btn = page.locator("button.el-button--primary, button:has-text('登 录'), button:has-text('登录')").first
    agree_cb = page.locator(".xy label.el-checkbox, .xy .el-checkbox__inner, label.el-checkbox").first

    try:
        # 等待账号输入框加载
        await un_input.wait_for(state="visible", timeout=6000)

        # 填充账号与密码
        await un_input.fill(username)
        await pwd_input.fill(password)

        # 自动勾选“我已阅读并同意”协议框
        if await agree_cb.is_visible():
            cb_input = page.locator(".xy .el-checkbox__input, .el-checkbox__input").first
            is_checked = await cb_input.evaluate("el => el.classList.contains('is-checked')")
            if not is_checked:
                await agree_cb.click(force=True)
                print_log("  ↳ 已自动勾选《隐私条款》和《服务协议》")

        # 点击登录按钮
        await login_btn.click()

        # 等待页面跳转或加载完成
        await page.wait_for_load_state("networkidle", timeout=10000)
        print_log("  ↳ 学工系统登录指令已发送")

        # 检测是否登录成功（URL 是否改变或不再处于 /login）
        if "/login" not in page.url:
            print_log("  ↳ 学工系统登录成功")
        else:
            # 检查是否有 Element UI 的错误消息提示 (如密码错误或未勾选协议)
            msg_notice = page.locator(".el-message--error, .el-form-item__error").first
            if await msg_notice.is_visible():
                err_text = (await msg_notice.text_content()).strip()
                raise Exception(f"登录失败: {err_text}")

    except Exception as e:
        if "登录失败" in str(e):
            raise e
        print_log(f"  ↳ 登录处理提示/警告: {str(e)}，尝试继续执行后续步骤...")


async def leave_application_hlu(page, username, password, kwargs, print_log):
    """私立华联大学请假申请提交"""
    leave_type = kwargs.get("leave_type", "病假")
    start_time = kwargs.get("start_time", "")
    end_time = kwargs.get("end_time", "")
    reason = kwargs.get("reason", "")
    attachment = kwargs.get("attachment", "")
    ask_user = kwargs.get("ask_user")

    print_log("[1/4] 打开学工系统并登录...")
    await login_xgxt_hlu(page, username, password, print_log)

    print_log("[2/4] 导航至请假申请页面...")
    await page.goto("http://xgxt.hlu.edu.cn/apply/qjsq", wait_until="domcontentloaded")
    await page.wait_for_timeout(1000)

    print_log("[3/4] 填写入参信息...")

    # 1. 选择请假类型 (Element UI Element 下拉框选择)
    qjlx_form_item = page.locator(".el-form-item:has(label:has-text('请假类型'))").first
    if await qjlx_form_item.is_visible():
        input_box = qjlx_form_item.locator(".el-input__inner").first
        await input_box.click()
        await page.wait_for_timeout(300)

        # 匹配下拉列表选项
        item_option = page.locator(f".el-select-dropdown__item:has-text('{leave_type}')").first
        if await item_option.is_visible():
            await item_option.click()
        else:
            # 直接通过输入框输入自定义类型
            await input_box.fill(leave_type)
    else:
        print_log("  警告：未找到【请假类型】输入项")

    # 2. 填写请假时间范围 (datetimerange 双输入框)
    if start_time or end_time:
        range_inputs = page.locator(".picker-warp .el-range-input, .el-date-editor--datetimerange .el-range-input")
        if await range_inputs.count() >= 2:
            if start_time:
                await range_inputs.nth(0).fill(start_time)
                await range_inputs.nth(0).dispatch_event("change")
            if end_time:
                await range_inputs.nth(1).fill(end_time)
                await range_inputs.nth(1).dispatch_event("change")

    # 3. 填写请假事由
    if reason:
        reason_input = page.locator("textarea.el-textarea__inner").first
        if await reason_input.is_visible():
            await reason_input.fill(reason)

    # 4. 上传附件
    if attachment and os.path.exists(attachment):
        print_log(f"  ↳ 正在上传附件文件: {os.path.basename(attachment)}...")
        file_input = page.locator("input[type='file'].el-upload__input").first
        await file_input.set_input_files(attachment)
        await page.wait_for_timeout(1000)

    print_log("[4/4] 请假申请内容已填入完毕！")

    # ================= 禁用页面提交按钮逻辑 =================
    try:
        await page.evaluate("""() => {
            const btns = Array.from(document.querySelectorAll('button'));
            const targetBtn = btns.find(btn => btn.innerText.includes('提交申请'));
            if (targetBtn) {
                targetBtn.disabled = true;
                targetBtn.classList.add('is-disabled');
                targetBtn.ostyle.pacity = '0.5';
                targetBtn.style.cursor = 'not-allowed';
            }
        }""")
        print_log("  ↳ 已成功把网页上的“提交申请”按钮禁用置灰")
    except Exception as e:
        print_log(f"  ↳ 禁用页面按钮时出现提示: {e}")

    print_log("  ↳ 已暂停任务，浏览器页面保持打开，请在页面中核对并按需修改信息。")

    # 将最终确认交给主线程弹出 Tkinter 弹窗。
    # 弹窗出现期间 Playwright 任务会等待，浏览器页面不会关闭。
    if ask_user:
        confirm_message = (
            "请假申请信息已经填写完成，请先在浏览器页面中核对。\n\n"
            "如有错误，可以直接在浏览器页面中修改。\n"
            "修改完成后，请点击下面的“是”，程序将自动为您提交申请。\n\n"
            "点击“否”将退出本次任务，不提交申请。"
        )

        confirmed = ask_user("请核对请假申请信息", confirm_message)

        if not confirmed:
            print_log("用户选择“否”，已取消提交，请假任务退出。")
            return False

    # 用户确认后，先恢复“提交申请”按钮可用状态，再由程序自动点击提交
    print_log("  ↳ 用户确认继续，正在解禁并自动点击“提交申请”...")

    # 使用 Locator 准确抓取提交按钮
    submit_btn = page.locator(
        "button.el-button.el-button--primary.el-button--medium"
    ).filter(has_text="提交申请").first

    try:
        await submit_btn.wait_for(state="visible", timeout=5000)
    except Exception:
        # 兼容 Element UI 类名发生变化的情况，退化到文本定位。
        submit_btn = page.locator(
            "button:has-text('提交申请')"
        ).first
        await submit_btn.wait_for(state="visible", timeout=5000)

    # 恢复按钮状态，解除 disabled 锁定
    await page.evaluate("""() => {
        const btns = Array.from(document.querySelectorAll('button'));
        const targetBtn = btns.find(btn => btn.innerText.includes('提交申请'));
        if (targetBtn) {
            targetBtn.disabled = false;
            targetBtn.classList.remove('is-disabled');
            targetBtn.style.opacity = '1';
            targetBtn.style.cursor = 'pointer';
        }
    }""")

    await submit_btn.click(force=True)
    print_log("  ↳ 已点击“提交申请”，等待页面处理...")

    # 给前端留出时间发送提交请求并显示结果。
    await page.wait_for_timeout(1000)

    return True


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


# 学校英文代码（用于云端订阅网址路径，如 /gzhu/{api}.ics）
SCHOOL_CODES = dict(CLOUD_SCHOOL_CODES)


# 学校 功能 配置映射表

SCHOOL_FEATURES = {
    "请先选择学校": {
        "请先选择学校": {},
    },

    "私立华联大学": {
        "课程表获取与 ICS 生成工具": {
            "type": "schedule_ics",
            "handler": download_hlu,
            "extractor": extract_schedule_data,
            "builder": build_ics_content,
            "period_times": PERIOD_TIMES,
        },
        "全自动选课工具": {
            "type": "course_select",
            "handler": auto_select_course_hlu,
            "fetch_handler": fetch_courses_hlu
        },
        "自动请假申请工具": {
            "type": "leave_apply",
            "handler": leave_application_hlu
        }
    },

    "广州大学": {
        "课程表获取与 ICS 生成工具": {
            "type": "schedule_ics",
            "handler": download_gzhu,
            "extractor": extract_schedule_data_gzhu,
            "builder": build_ics_content_gzhu,
            "period_times": GZHU_PERIOD_TIMES,
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
        self.root.title(f"教务辅助工具 v{APP_VERSION}")
        self.root.geometry("600x1010")
        self.root.resizable(False, False)

        self.stop_event = threading.Event()
        self.detected_browsers = detect_installed_browsers()
        browser_options = ["自动检测"] + list(self.detected_browsers.keys())
        if len(self.detected_browsers) == 0:
            browser_options = ["未检测到系统安装的可用浏览器"]

        self.app_config = load_cloud_config(APP_DIR)
        self.cloud_base_url = self.app_config["cloud_base_url"]
        self.web_portal_url = self.app_config["web_portal_url"]
        self.cloud_school = ""
        self._qr_photo = None
        self._cloud_files_window = None
        self._update_check_running = False

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
        self.cred_frame = tk.LabelFrame(root, text="账号凭证", font=(SYS_FONT, 10, "bold"), padx=10, pady=5)
        self.cred_frame.pack(fill="x", padx=pad_x, pady=pad_y)

        tk.Label(self.cred_frame, text="学号:", font=(SYS_FONT, 10)).grid(row=0, column=0, sticky="e", pady=pad_y)
        self.username_entry = tk.Entry(self.cred_frame, width=38, font=(SYS_FONT, 10))
        self.username_entry.grid(row=0, column=1, padx=pad_x, pady=pad_y)

        tk.Label(self.cred_frame, text="密码:", font=(SYS_FONT, 10)).grid(row=1, column=0, sticky="e", pady=pad_y)
        self.password_entry = tk.Entry(self.cred_frame, show="*", width=38, font=(SYS_FONT, 10))
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

        # 课表 PDF 转 ICS 设置（与获取课程表功能集成）
        self.ics_frame = tk.LabelFrame(root, text="课表 PDF 转 ICS 设置", font=(SYS_FONT, 10, "bold"), padx=10, pady=5)

        self.use_local_pdf_var = tk.BooleanVar(value=False)
        self.chk_use_local_pdf = tk.Checkbutton(
            self.ics_frame, text="从本地 PDF 文件生成（跳过下载）", variable=self.use_local_pdf_var,
            font=(SYS_FONT, 9), command=self.on_local_mode_changed
        )
        self.chk_use_local_pdf.grid(row=0, column=0, columnspan=3, sticky="w", padx=pad_x, pady=(0, pad_y))

        self.ics_pdf_label = tk.Label(self.ics_frame, text="课表 PDF:", font=(SYS_FONT, 9))
        self.ics_pdf_label.grid(row=1, column=0, sticky="e", pady=pad_y)
        ics_pdf_inner = tk.Frame(self.ics_frame)
        ics_pdf_inner.grid(row=1, column=1, sticky="w", padx=pad_x, pady=pad_y)
        self.ics_pdf_inner = ics_pdf_inner
        self.ics_pdf_path_entry = tk.Entry(ics_pdf_inner, width=30, font=(SYS_FONT, 9))
        self.ics_pdf_path_entry.pack(side="left", padx=(0, 5))
        self.btn_browse_ics_pdf = tk.Button(
            ics_pdf_inner, text="选择 PDF...", command=self.browse_schedule_pdf
        )
        self.btn_browse_ics_pdf.pack(side="left")

        tk.Label(self.ics_frame, text="最早开课周的周一第 1 节开始:", font=(SYS_FONT, 9)).grid(
            row=2, column=0, sticky="e", pady=pad_y
        )
        ics_anchor_inner = tk.Frame(self.ics_frame)
        ics_anchor_inner.grid(row=2, column=1, sticky="w", padx=pad_x, pady=pad_y)
        self.ics_anchor_entry = tk.Entry(
            ics_anchor_inner, width=24, font=(SYS_FONT, 9), state="readonly"
        )
        self.ics_anchor_entry.pack(side="left", padx=(0, 5))
        self.btn_ics_anchor = tk.Button(
            ics_anchor_inner,
            text="选择时间...",
            command=lambda: self.choose_datetime(
                self.ics_anchor_entry, "选择最早开课周的周一第 1 节时间", default_time=self._ics_default_time()
            ),
        )
        self.btn_ics_anchor.pack(side="left")

        tk.Label(
            self.ics_frame,
            text="填课表最早开课那一周的周一日期（如 2026-09-28）；程序会自动换算到对应周次。日期需为星期一。",
            font=(SYS_FONT, 8),
            fg="gray",
        ).grid(row=3, column=1, columnspan=3, sticky="w", padx=pad_x, pady=(0, pad_y))

        # 云端订阅设置区域（Worker + GitHub 存儲）
        self.cloud_frame = tk.LabelFrame(root, text="云端订阅设置", font=(SYS_FONT, 10, "bold"), padx=10, pady=5)

        tk.Label(self.cloud_frame, text="云端地址:", font=(SYS_FONT, 9)).grid(row=0, column=0, sticky="e", pady=pad_y)
        self.cloud_url_entry = tk.Entry(self.cloud_frame, width=32, font=(SYS_FONT, 9))
        self.cloud_url_entry.insert(0, self.cloud_base_url)
        self.cloud_url_entry.grid(row=0, column=1, padx=pad_x, pady=pad_y, sticky="w")
        self.btn_open_web = tk.Button(
            self.cloud_frame, text="网页", font=(SYS_FONT, 9), command=self.open_web_portal
        )
        self.btn_open_web.grid(row=0, column=2, padx=(0, 4), pady=pad_y)

        self.registered_var = tk.BooleanVar(value=False)
        self.chk_registered = tk.Checkbutton(
            self.cloud_frame,
            text="注册用户（使用 API key）",
            variable=self.registered_var,
            font=(SYS_FONT, 9),
            command=self.on_registered_changed,
        )
        self.chk_registered.grid(row=1, column=1, sticky="w", padx=pad_x, pady=(0, 2))
        self.guest_hint_label = tk.Label(
            self.cloud_frame, text="不勾选 = 访客（学校 + 学号，明文）", font=(SYS_FONT, 8), fg="gray"
        )
        self.guest_hint_label.grid(row=1, column=2, sticky="w", padx=(0, 4))

        tk.Label(self.cloud_frame, text="API key:", font=(SYS_FONT, 9)).grid(row=2, column=0, sticky="e", pady=pad_y)
        self.cloud_api_key_entry = tk.Entry(self.cloud_frame, width=32, font=(SYS_FONT, 9), state="disabled")
        self.cloud_api_key_entry.grid(row=2, column=1, padx=pad_x, pady=pad_y, sticky="w")
        self.btn_validate_api = tk.Button(
            self.cloud_frame, text="验证", font=(SYS_FONT, 9), state="disabled",
            command=self.validate_api_key,
        )
        self.btn_validate_api.grid(row=2, column=2, padx=(0, 4), pady=pad_y)

        cloud_actions = tk.Frame(self.cloud_frame)
        cloud_actions.grid(row=3, column=1, columnspan=2, sticky="w", padx=pad_x, pady=(0, pad_y))
        self.btn_web_account = tk.Button(
            cloud_actions, text="注册 / 登录 / 查看 API", font=(SYS_FONT, 9),
            command=self.open_web_portal,
        )
        self.btn_web_account.pack(side="left", padx=(0, 6))
        self.btn_manage_files = tk.Button(
            cloud_actions, text="云端文件列表...", font=(SYS_FONT, 9),
            command=self.open_cloud_files,
        )
        self.btn_manage_files.pack(side="left", padx=(0, 6))
        self.btn_upload_local = tk.Button(
            cloud_actions, text="上传本地 ICS...", font=(SYS_FONT, 9),
            command=self.upload_local_ics,
        )
        self.btn_upload_local.pack(side="left")

        flags = tk.Frame(self.cloud_frame)
        flags.grid(row=4, column=1, columnspan=2, sticky="w", padx=pad_x, pady=(0, pad_y))
        self.upload_to_cloud_var = tk.BooleanVar(value=False)
        self.chk_upload_cloud = tk.Checkbutton(
            flags, text="生成后自动上传", variable=self.upload_to_cloud_var, font=(SYS_FONT, 9)
        )
        self.chk_upload_cloud.pack(side="left", padx=(0, 8))
        self.keep_files_var = tk.BooleanVar(value=True)
        self.chk_keep_files = tk.Checkbutton(
            flags, text="保留本地 PDF / ICS", variable=self.keep_files_var, font=(SYS_FONT, 9)
        )
        self.chk_keep_files.pack(side="left", padx=(0, 8))
        self.save_login_var = tk.BooleanVar(value=False)
        self.chk_save_login = tk.Checkbutton(
            flags, text="保存登录信息", variable=self.save_login_var, font=(SYS_FONT, 9)
        )
        self.chk_save_login.pack(side="left")

        tk.Label(self.cloud_frame, text="订阅网址:", font=(SYS_FONT, 9)).grid(row=5, column=0, sticky="e", pady=pad_y)
        url_inner = tk.Frame(self.cloud_frame)
        url_inner.grid(row=5, column=1, columnspan=2, sticky="w", padx=pad_x, pady=pad_y)
        self.subscription_url_entry = tk.Entry(url_inner, width=31, font=(SYS_FONT, 9), state="readonly")
        self.subscription_url_entry.pack(side="left", padx=(0, 5))
        self.btn_copy_url = tk.Button(url_inner, text="复制", font=(SYS_FONT, 9), command=self.copy_subscription_url)
        self.btn_copy_url.pack(side="left", padx=(0, 4))
        self.btn_qr = tk.Button(url_inner, text="二维码", font=(SYS_FONT, 9), command=self.show_subscription_qr)
        self.btn_qr.pack(side="left")

        tk.Label(self.cloud_frame, text="CalDAV:", font=(SYS_FONT, 9)).grid(row=6, column=0, sticky="e", pady=pad_y)
        caldav_inner = tk.Frame(self.cloud_frame)
        caldav_inner.grid(row=6, column=1, columnspan=2, sticky="w", padx=pad_x, pady=pad_y)
        self.caldav_url_entry = tk.Entry(caldav_inner, width=38, font=(SYS_FONT, 9), state="readonly")
        self.caldav_url_entry.pack(side="left", padx=(0, 5))
        self.btn_copy_caldav = tk.Button(
            caldav_inner, text="复制", font=(SYS_FONT, 9), command=self.copy_caldav_url
        )
        self.btn_copy_caldav.pack(side="left")
        self.btn_setup_caldav = tk.Button(
            caldav_inner, text="配置 CalDAV…", font=(SYS_FONT, 9), command=self.show_caldav_setup
        )
        self.btn_setup_caldav.pack(side="left", padx=(5, 0))

        tk.Label(
            self.cloud_frame,
            text="二维码内容为 webcal:// 订阅地址；CalDAV 用户名与密码均填 API key。访客无需 API，文件以“学校+学号”明文保存。",
            font=(SYS_FONT, 8),
            fg="gray",
            wraplength=520,
            justify="left",
        ).grid(row=7, column=0, columnspan=3, sticky="w", padx=pad_x, pady=(0, pad_y))

        self.on_registered_changed()
        self.cloud_api_key_entry.bind("<KeyRelease>", lambda event: self.refresh_subscription_ui())
        self.username_entry.bind("<KeyRelease>", lambda event: self.refresh_subscription_ui())


        # 请假申请设置区域
        self.leave_frame = tk.LabelFrame(root, text="请假申请设置", font=(SYS_FONT, 10, "bold"), padx=10, pady=5)

        tk.Label(self.leave_frame, text="请假类型:", font=(SYS_FONT, 10)).grid(row=0, column=0, sticky="e", pady=pad_y)
        self.leave_type_cb = ttk.Combobox(
            self.leave_frame, values=["病假", "事假", "公假"], font=(SYS_FONT, 10), width=36
        )
        self.leave_type_cb.current(0)
        self.leave_type_cb.grid(row=0, column=1, padx=pad_x, pady=pad_y)

        tk.Label(self.leave_frame, text="开始时间:", font=(SYS_FONT, 10)).grid(row=1, column=0, sticky="e", pady=pad_y)
        start_time_inner = tk.Frame(self.leave_frame)
        start_time_inner.grid(row=1, column=1, padx=pad_x, pady=pad_y, sticky="w")
        self.start_time_entry = tk.Entry(start_time_inner, width=28, font=(SYS_FONT, 10), state="readonly")
        self.start_time_entry.pack(side="left", padx=(0, 5))
        self.btn_start_time = tk.Button(
            start_time_inner, text="选择时间...", font=(SYS_FONT, 9),
            command=lambda: self.choose_datetime(self.start_time_entry, "选择开始时间")
        )
        self.btn_start_time.pack(side="left")

        tk.Label(self.leave_frame, text="结束时间:", font=(SYS_FONT, 10)).grid(row=2, column=0, sticky="e", pady=pad_y)
        end_time_inner = tk.Frame(self.leave_frame)
        end_time_inner.grid(row=2, column=1, padx=pad_x, pady=pad_y, sticky="w")
        self.end_time_entry = tk.Entry(end_time_inner, width=28, font=(SYS_FONT, 10), state="readonly")
        self.end_time_entry.pack(side="left", padx=(0, 5))
        self.btn_end_time = tk.Button(
            end_time_inner, text="选择时间...", font=(SYS_FONT, 9),
            command=lambda: self.choose_datetime(self.end_time_entry, "选择结束时间")
        )
        self.btn_end_time.pack(side="left")

        # 默认使用当天的常用时间范围
        from datetime import datetime
        today = datetime.now().strftime("%Y-%m-%d")
        self._set_readonly_entry(self.start_time_entry, f"{today} 08:00:00")
        self._set_readonly_entry(self.end_time_entry, f"{today} 18:00:00")

        tk.Label(self.leave_frame, text="请假事由:", font=(SYS_FONT, 10)).grid(row=3, column=0, sticky="ne", pady=pad_y)
        self.reason_text = tk.Text(self.leave_frame, width=38, height=3, font=(SYS_FONT, 9))
        self.reason_text.grid(row=3, column=1, padx=pad_x, pady=pad_y)

        tk.Label(self.leave_frame, text="附件文件:", font=(SYS_FONT, 10)).grid(row=4, column=0, sticky="e", pady=pad_y)
        file_inner = tk.Frame(self.leave_frame)
        file_inner.grid(row=4, column=1, sticky="w", padx=pad_x, pady=pad_y)

        self.file_entry = tk.Entry(file_inner, width=25, font=(SYS_FONT, 10))
        self.file_entry.pack(side="left", padx=(0, 5))

        self.btn_browse_file = tk.Button(file_inner, text="选择文件...", command=self.browse_attachment)
        self.btn_browse_file.pack(side="left")

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
        self.btn_check_update = tk.Button(
            opt_frame, text="检查更新", font=(SYS_FONT, 9),
            command=lambda: self.check_for_updates(manual=True),
        )
        self.btn_check_update.pack(side="right", padx=10)

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
        self.log_text = tk.Text(root, height=6, width=72, font=("Courier", 10), state="disabled", bg="#F8F9FA")
        self.log_text.pack(padx=pad_x, pady=(0, pad_y))

        self.on_school_changed()
        self.log(f"已自动识别到 {len(self.detected_browsers)} 个本地系统已安装浏览器")

        # 启动时自动加载本地保存的课程列表缓存
        cached_courses = load_courses_cache()
        if cached_courses:
            self.checklist.set_items(cached_courses)
            self.log(f"已自动加载本地保存的 {len(cached_courses)} 门课程列表")

        # 启动时自动加载本地保存的登录信息（学号/密码/API key）
        saved_login = load_login_cache()
        if saved_login:
            if saved_login.get("username"):
                self.username_entry.delete(0, tk.END)
                self.username_entry.insert(0, saved_login["username"])
            if saved_login.get("password"):
                self.password_entry.delete(0, tk.END)
                self.password_entry.insert(0, saved_login["password"])
            if saved_login.get("api_key"):
                self.cloud_api_key_entry.delete(0, tk.END)
                self.cloud_api_key_entry.insert(0, saved_login["api_key"])
                self.registered_var.set(True)
                self.on_registered_changed()
            self.save_login_var.set(True)
            self.log("已自动加载本地保存的登录信息（学号/密码/API key）")
            self.refresh_subscription_ui()

        self.log(f"当前版本 v{APP_VERSION}")
        self.root.after(1000, self.check_for_updates)

    def on_school_changed(self, event=None):
        selected_school = self.school_cb.get().strip()
        features = list(SCHOOL_FEATURES.get(selected_school, {}).keys())

        self.feature_cb["values"] = features
        if features:
            self.feature_cb.current(0)
        else:
            self.feature_cb.set("")

        self.on_feature_changed()
        if hasattr(self, "subscription_url_entry"):
            self.refresh_subscription_ui()

    def on_feature_changed(self, event=None):
        selected_school = self.school_cb.get().strip()
        selected_feature = self.feature_cb.get().strip()

        feature_info = SCHOOL_FEATURES.get(selected_school, {}).get(selected_feature, {})
        f_type = feature_info.get("type")

        self.download_frame.pack_forget()
        self.ics_frame.pack_forget()
        self.cloud_frame.pack_forget()
        self.course_frame.pack_forget()
        self.leave_frame.pack_forget()

        # 账号凭证（学号/密码）：现在所有功能都需要学号（下载或作为订阅文件名）
        if not self.cred_frame.winfo_manager():
            self.cred_frame.pack(fill="x", padx=12, pady=5, after=self.school_cb.master)

        if f_type == "schedule_ics":
            self.ics_frame.pack(fill="x", padx=12, pady=5, before=self.chk_browser.master)
            self.cloud_frame.pack(fill="x", padx=12, pady=5, before=self.chk_browser.master)
            self.btn_stop.config(state="disabled")
            self.on_local_mode_changed()
        elif f_type == "course_select":
            self.course_frame.pack(fill="x", padx=12, pady=5, before=self.chk_browser.master)
        elif f_type == "leave_apply":
            self.leave_frame.pack(fill="x", padx=12, pady=5, before=self.chk_browser.master)
            self.btn_stop.config(state="disabled")

    def on_local_mode_changed(self):
        """根据“从本地 PDF 文件生成”勾选，切换下载保存路径与本地 PDF 选择的显示。"""
        use_local = self.use_local_pdf_var.get()
        if use_local:
            if self.download_frame.winfo_manager():
                self.download_frame.pack_forget()
            self.ics_pdf_label.grid()
            self.ics_pdf_inner.grid()
        else:
            self.ics_pdf_label.grid_remove()
            self.ics_pdf_inner.grid_remove()
            if self.ics_frame.winfo_manager() and not self.download_frame.winfo_manager():
                self.download_frame.pack(fill="x", padx=12, pady=5, before=self.chk_browser.master)

    def copy_subscription_url(self):
        """把订阅网址复制到剪贴板。"""
        url = self.subscription_url_entry.get().strip()
        if not url:
            messagebox.showwarning("提示", "暂无订阅网址可复制。")
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(url)
        self.log(f"订阅网址已复制到剪贴板：{url}")

    # ------------------------------------------------------------------
    # 云端账户 / 文件 / 二维码
    # ------------------------------------------------------------------

    def on_registered_changed(self):
        """切换访客 / 注册用户模式。"""
        registered = bool(self.registered_var.get())
        state = "normal" if registered else "disabled"
        self.cloud_api_key_entry.config(state=state)
        self.btn_validate_api.config(state=state)
        self.guest_hint_label.config(
            text="已启用：文件加密存 ics/{school}/{api}.ics" if registered
            else "不勾选 = 访客（学校 + 学号，明文）",
            fg="#18794e" if registered else "gray",
        )
        if not registered:
            self._set_readonly_entry(self.caldav_url_entry, "")
        self.refresh_subscription_ui()

    def get_cloud_context(self):
        """返回本次上传需要的模式与参数。"""
        base = normalize_base_url(self.cloud_url_entry.get())
        registered = bool(self.registered_var.get())
        school = SCHOOL_CODES.get(self.school_cb.get().strip(), "").strip()
        student_id = self.username_entry.get().strip()

        if registered:
            api_key = self.cloud_api_key_entry.get().strip()
            if not api_key:
                raise CloudError("已勾选“注册用户”，但未填写 API key")
            school = school or self.cloud_school
            if not school:
                raise CloudError("请先选择学校")
            return {
                "mode": "registered",
                "base_url": base,
                "api_key": api_key,
                "school": school,
                "student_id": student_id,
                "filename": f"{api_key}.ics",
            }

        if not school:
            raise CloudError("请先选择学校")
        if not student_id:
            raise CloudError("访客模式需要填写学号")
        return {
            "mode": "guest",
            "base_url": base,
            "api_key": "",
            "school": school,
            "student_id": student_id,
            "filename": f"{student_id}.ics",
        }

    def refresh_subscription_ui(self):
        """根据当前输入预览订阅地址，不访问网络。"""
        try:
            context = self.get_cloud_context()
        except CloudError:
            self._set_readonly_entry(self.subscription_url_entry, "")
            self._set_readonly_entry(self.caldav_url_entry, "")
            return

        if context["mode"] == "guest":
            url = subscription_url(
                context["base_url"], context["school"], student_id=context["student_id"]
            )
            self._set_readonly_entry(self.subscription_url_entry, url)
            self._set_readonly_entry(self.caldav_url_entry, "")
        else:
            url = subscription_url(
                context["base_url"], context["school"], api_key=context["api_key"]
            )
            self._set_readonly_entry(self.subscription_url_entry, url)
            self._set_readonly_entry(
                self.caldav_url_entry,
                caldav_url(context["base_url"], context["school"], context["api_key"]),
            )

    def open_web_portal(self):
        url = str(self.web_portal_url or "").strip()
        if not url or "YOURNAME" in url:
            url = simpledialog.askstring(
                "设置网页地址",
                "请输入 GitHub Pages 地址，例如：\nhttps://<用户名>.github.io/<仓库名>/",
                parent=self.root,
            )
            if not url:
                return
            self.web_portal_url = url.strip()
            self.app_config["web_portal_url"] = self.web_portal_url
            try:
                save_cloud_config(
                    APP_DIR,
                    self.cloud_url_entry.get().strip(),
                    self.web_portal_url,
                )
                self.log(f"网页地址已保存：{self.web_portal_url}")
            except Exception as exc:
                self.log(f"网页地址保存失败：{exc}")
        webbrowser.open(self.web_portal_url)

    def check_for_updates(self, manual=False):
        """背景查詢 GitHub Releases 最新版本，不阻塞 UI。"""
        if not self.app_config.get("update_check", True) and not manual:
            self.log("已关闭版本检测（cloud_config.json: update_check=false）")
            return

        owner, repo = resolve_update_source(self.app_config)
        if not owner or not repo:
            self.log(
                "未配置 GitHub 仓库，跳过版本检测；可在 cloud_config.json 设置 "
                "github_owner/github_repo，或把 web_portal_url 设为 "
                "https://<用户名>.github.io/<仓库名>/"
            )
            if manual:
                if not messagebox.askyesno(
                    "版本检测未配置",
                    "尚未设置 GitHub 仓库。\n\n是否现在填写 owner/repo 以启用版本检测？",
                    parent=self.root,
                ):
                    return
                owner = simpledialog.askstring(
                    "GitHub 用户/组织", "例如：thomaswan", parent=self.root
                )
                if not owner:
                    return
                repo = simpledialog.askstring(
                    "GitHub 仓库", "例如：multi-tool", parent=self.root
                )
                if not repo:
                    return
                self.app_config["github_owner"] = owner.strip()
                self.app_config["github_repo"] = repo.strip()
                try:
                    save_cloud_config(
                        APP_DIR,
                        self.cloud_url_entry.get().strip(),
                        self.web_portal_url,
                        github_owner=self.app_config["github_owner"],
                        github_repo=self.app_config["github_repo"],
                    )
                    self.log(f"GitHub 仓库已保存：{owner}/{repo}")
                except Exception as exc:
                    self.log(f"GitHub 仓库保存失败：{exc}")
                    return
                self.check_for_updates(manual=True)
            return

        if self._update_check_running:
            return

        self._update_check_running = True
        self.btn_check_update.config(state="disabled")
        self.log(f"正在检查更新：{owner}/{repo} ...")

        def worker():
            try:
                release = fetch_latest_release(owner, repo)
            except Exception as exc:
                try:
                    self.root.after(0, lambda msg=str(exc): self._on_update_error(msg))
                except Exception:
                    pass
            else:
                try:
                    self.root.after(0, lambda data=release: self._on_update_result(data))
                except Exception:
                    pass

        threading.Thread(target=worker, daemon=True).start()

    def _on_update_error(self, message):
        self._update_check_running = False
        try:
            self.btn_check_update.config(state="normal")
        except Exception:
            pass
        self.log(f"版本检测失败：{message}")

    def _on_update_result(self, release):
        self._update_check_running = False
        try:
            self.btn_check_update.config(state="normal")
        except Exception:
            pass

        latest = str(release.get("tag") or "")
        if not latest:
            self.log("版本检测完成，但 Release 没有 tag_name")
            return

        if is_newer_version(latest, APP_VERSION):
            self.log(f"发现新版本 {latest}（当前 {APP_VERSION}）")
            download_url = str(
                release.get("download_url") or release.get("html_url") or ""
            )
            message = (
                f"当前版本：v{APP_VERSION}\n"
                f"最新版本：{latest}\n\n"
                "是否前往 GitHub Releases 下载最新版？"
            )
            if messagebox.askyesno("发现新版本", message, parent=self.root):
                if download_url:
                    webbrowser.open(download_url)
        else:
            self.log(f"当前已是最新版本（本地 {APP_VERSION}，Release {latest}）")

    def validate_api_key(self):
        try:
            context = self.get_cloud_context()
        except CloudError as exc:
            messagebox.showwarning("提示", str(exc), parent=self.root)
            return

        if context["mode"] != "registered":
            messagebox.showinfo("提示", "请先勾选“注册用户”。", parent=self.root)
            return

        self.btn_validate_api.config(state="disabled")
        self.log("正在验证 API key...")

        def worker():
            try:
                info = CloudAPI(context["base_url"]).me(context["api_key"])
            except Exception as exc:
                self.root.after(0, lambda msg=str(exc): self._on_validate_failed(msg))
            else:
                self.root.after(0, lambda data=info: self._on_validate_ok(data))

        threading.Thread(target=worker, daemon=True).start()

    def _on_validate_failed(self, message):
        self.btn_validate_api.config(state="normal")
        self.log(f"API key 验证失败：{message}")
        messagebox.showerror("验证失败", message, parent=self.root)

    def _on_validate_ok(self, info):
        self.btn_validate_api.config(state="normal")
        self.cloud_school = str(info.get("school") or self.cloud_school or "")
        subscription = str(info.get("subscriptionUrl") or "")
        caldav = str(info.get("caldavUrl") or "")
        if subscription:
            self._set_readonly_entry(self.subscription_url_entry, subscription)
        if caldav:
            self._set_readonly_entry(self.caldav_url_entry, caldav)
        username = str(info.get("username") or "")
        self.log(f"API key 验证成功：用户 {username}，学校 {self.cloud_school}")
        messagebox.showinfo(
            "验证成功",
            f"用户名：{username}\n学校：{self.cloud_school}\n\n订阅地址与 CalDAV 地址已填入。",
            parent=self.root,
        )

    def open_cloud_files(self):
        """打开云端文件列表窗口（在程序内完成列表/上传/删除）。"""
        if self._cloud_files_window is not None and self._cloud_files_window.winfo_exists():
            self._cloud_files_window.lift()
            self._cloud_files_window.refresh()
            return

        try:
            context = self.get_cloud_context()
        except CloudError as exc:
            messagebox.showwarning("提示", str(exc), parent=self.root)
            return

        self._cloud_files_window = CloudFilesWindow(self, context)
        self._cloud_files_window.protocol(
            "WM_DELETE_WINDOW", self._on_cloud_files_closed
        )

    def _on_cloud_files_closed(self):
        if self._cloud_files_window is not None and self._cloud_files_window.winfo_exists():
            self._cloud_files_window.destroy()
        self._cloud_files_window = None

    def upload_local_ics(self):
        """从本地选择 .ics 并按当前模式上传。"""
        path = filedialog.askopenfilename(
            title="选择要上传的 ICS 文件", filetypes=[("ICS 文件", "*.ics")]
        )
        if not path:
            return

        try:
            with open(path, "r", encoding="utf-8") as handle:
                ics_text = handle.read()
            context = self.get_cloud_context()
        except (OSError, UnicodeError, CloudError) as exc:
            messagebox.showwarning("提示", f"无法读取或上传：{exc}", parent=self.root)
            return

        self.log(f"开始上传本地 ICS：{path}（{'注册用户' if context['mode'] == 'registered' else '访客'}）")
        self.btn_upload_local.config(state="disabled")

        def worker():
            try:
                result = self._upload_ics_via_cloud(ics_text, context)
            except Exception as exc:
                self.root.after(0, lambda msg=str(exc): self._on_upload_failed(msg))
            else:
                self.root.after(0, lambda data=result: self._on_upload_ok(data))

        threading.Thread(target=worker, daemon=True).start()

    def _on_upload_failed(self, message):
        self.btn_upload_local.config(state="normal")
        self.log(f"上传失败：{message}")
        messagebox.showerror("上传失败", message, parent=self.root)

    def _on_upload_ok(self, result):
        self.btn_upload_local.config(state="normal")
        url = str(result.get("url") or "")
        caldav = str(result.get("caldav") or "")
        if url:
            self._set_readonly_entry(self.subscription_url_entry, url)
        if caldav:
            self._set_readonly_entry(self.caldav_url_entry, caldav)
        self.log(f"上传成功：{url or result}")
        if self._cloud_files_window is not None and self._cloud_files_window.winfo_exists():
            self._cloud_files_window.refresh()
        messagebox.showinfo(
            "上传成功",
            f"文件已上传。\n\n订阅地址：\n{url or '（服务端未返回）'}",
            parent=self.root,
        )

    def _upload_ics_via_cloud(self, ics_text, context):
        api = CloudAPI(context["base_url"])
        if context["mode"] == "registered":
            return api.upload_registered(context["api_key"], ics_text)
        return api.upload_guest(context["school"], context["student_id"], ics_text)

    def show_subscription_qr(self):
        url = self.subscription_url_entry.get().strip()
        if not url:
            self.refresh_subscription_ui()
            url = self.subscription_url_entry.get().strip()
        if not url:
            messagebox.showwarning("提示", "暂无订阅网址；请先填写学号并选择学校。", parent=self.root)
            return
        self.show_qr_window(webcal_url(url), url)

    def copy_caldav_url(self):
        url = self.caldav_url_entry.get().strip()
        if not url:
            messagebox.showwarning("提示", "仅注册用户有 CalDAV 地址。", parent=self.root)
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(url)
        self.log(f"CalDAV 地址已复制：{url}")

    def show_caldav_setup(self):
        """Show client-specific setup for the registered user's CalDAV calendar."""
        url = self.caldav_url_entry.get().strip()
        if not url:
            messagebox.showwarning("提示", "CalDAV 仅供已验证的注册用户使用。请先注册/登录并验证 API key。", parent=self.root)
            return
        instructions = (
            f"CalDAV 服务器地址：\n{url}\n\n"
            "帳戶類型：手動 / Advanced / CalDAV\n"
            "用戶名：你的 API key\n"
            "密碼：同一個 API key\n\n"
            "Apple 日曆（macOS）：日曆 → 添加帳戶 → 其他 CalDAV 帳戶 → 手動；"
            "填入以上伺服器地址、用戶名和密碼。\n\n"
            "iPhone / iPad：設定 → 日曆 → 帳戶 → 添加帳戶 → 其他 → 添加 CalDAV 帳戶，"
            "選手動並填入以上資料。\n\n"
            "Thunderbird：新建日曆 → 網絡上 → CalDAV，填入伺服器地址，"
            "再按提示填用戶名和 API key。Android 可使用 DAVx⁵，添加 CalDAV URL 後填入相同憑據。\n\n"
            "CalDAV 可同步日曆更改；webcal/ICS 訂閱是唯讀。API key 等同密碼，請勿分享。"
        )
        messagebox.showinfo("CalDAV 配置", instructions, parent=self.root)

    def show_qr_window(self, webcal, subscription, caldav=""):
        """显示 webcal:// 订阅二维码。"""
        try:
            png = make_qr_png(webcal, box_size=6)
        except Exception as exc:
            messagebox.showerror("二维码生成失败", str(exc), parent=self.root)
            return

        if Image is None or ImageTk is None:
            messagebox.showerror(
                "缺少 Pillow",
                "显示二维码需要 Pillow，请执行：pip install pillow",
                parent=self.root,
            )
            return

        window = tk.Toplevel(self.root)
        window.title("订阅二维码（webcal://）")
        window.transient(self.root)
        window.resizable(False, False)

        image = Image.open(io.BytesIO(png))
        photo = ImageTk.PhotoImage(image)
        window._qr_photo = photo
        self._qr_photo = photo

        tk.Label(window, image=photo).pack(padx=14, pady=12)

        url_frame = tk.Frame(window)
        url_frame.pack(fill="x", padx=14)
        url_entry = tk.Entry(url_frame, width=44, font=(SYS_FONT, 9))
        url_entry.insert(0, webcal)
        url_entry.config(state="readonly")
        url_entry.pack(side="left", padx=(0, 6))

        def copy_webcal():
            self.root.clipboard_clear()
            self.root.clipboard_append(webcal)
            self.log(f"webcal 地址已复制：{webcal}")

        tk.Button(url_frame, text="复制", command=copy_webcal).pack(side="left")

        hint = "用手机相机/日历扫描二维码即可订阅。"
        if caldav:
            hint += f"\nCalDAV：{caldav}\n用户名/密码均填 API key。"
        tk.Label(window, text=hint, font=(SYS_FONT, 9), fg="#444444", justify="left").pack(
            anchor="w", padx=14, pady=(8, 0)
        )

        def save_qr():
            path = filedialog.asksaveasfilename(
                title="保存二维码", defaultextension=".png",
                filetypes=[("PNG 图片", "*.png")],
            )
            if path:
                with open(path, "wb") as handle:
                    handle.write(png)
                self.log(f"二维码已保存：{path}")

        buttons = tk.Frame(window)
        buttons.pack(pady=10)
        tk.Button(buttons, text="保存二维码...", command=save_qr).pack(side="left", padx=4)
        tk.Button(buttons, text="关闭", command=window.destroy).pack(side="left", padx=4)

    def _set_readonly_entry(self, entry, value):
        """给只读输入框设置内容"""
        entry.config(state="normal")
        entry.delete(0, tk.END)
        entry.insert(0, value or "")
        entry.config(state="readonly")

    def _ics_default_time(self):
        """根据当前所选院校/功能返回第 1 节的开始时间（时, 分）。"""
        selected_school = self.school_cb.get().strip()
        selected_feature = self.feature_cb.get().strip()
        feature_info = SCHOOL_FEATURES.get(selected_school, {}).get(selected_feature, {})
        period_times = feature_info.get("period_times", PERIOD_TIMES)
        hour, minute = period_times[1][0].split(":")
        return int(hour), int(minute)

    def choose_datetime(self, target_entry, title, default_time=None):
        """弹出日期+时间选择框，最终写入 YYYY-MM-DD HH:MM:SS 格式"""
        from datetime import datetime

        current_value = target_entry.get().strip()
        try:
            current_dt = datetime.strptime(current_value, "%Y-%m-%d %H:%M:%S")
        except Exception:
            current_dt = datetime.now().replace(second=0, microsecond=0)
            if default_time is not None:
                current_dt = current_dt.replace(
                    hour=default_time[0], minute=default_time[1]
                )

        dialog = tk.Toplevel(self.root)
        dialog.title(title)
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.resizable(False, False)

        frame = tk.Frame(dialog, padx=15, pady=12)
        frame.pack(fill="both", expand=True)

        tk.Label(frame, text="日期:", font=(SYS_FONT, 10)).grid(row=0, column=0, sticky="e", padx=(0, 8), pady=6)

        year_var = tk.StringVar(value=str(current_dt.year))
        month_var = tk.StringVar(value=f"{current_dt.month:02d}")
        day_var = tk.StringVar(value=f"{current_dt.day:02d}")
        hour_var = tk.StringVar(value=f"{current_dt.hour:02d}")
        minute_var = tk.StringVar(value=f"{current_dt.minute:02d}")
        second_var = tk.StringVar(value=f"{current_dt.second:02d}")

        year_cb = ttk.Combobox(frame, textvariable=year_var, values=[str(y) for y in range(current_dt.year - 2, current_dt.year + 6)], state="readonly", width=6)
        month_cb = ttk.Combobox(frame, textvariable=month_var, values=[f"{m:02d}" for m in range(1, 13)], state="readonly", width=4)
        day_cb = ttk.Combobox(frame, textvariable=day_var, values=[f"{d:02d}" for d in range(1, 32)], state="readonly", width=4)

        year_cb.grid(row=0, column=1, padx=2)
        tk.Label(frame, text="年").grid(row=0, column=2)
        month_cb.grid(row=0, column=3, padx=2)
        tk.Label(frame, text="月").grid(row=0, column=4)
        day_cb.grid(row=0, column=5, padx=2)
        tk.Label(frame, text="日").grid(row=0, column=6)

        tk.Label(frame, text="时间:", font=(SYS_FONT, 10)).grid(row=1, column=0, sticky="e", padx=(0, 8), pady=6)

        hour_cb = ttk.Combobox(frame, textvariable=hour_var, values=[f"{h:02d}" for h in range(24)], state="readonly", width=4)
        minute_cb = ttk.Combobox(frame, textvariable=minute_var, values=[f"{m:02d}" for m in range(60)], state="readonly", width=4)
        second_cb = ttk.Combobox(frame, textvariable=second_var, values=[f"{s:02d}" for s in range(60)], state="readonly", width=4)

        hour_cb.grid(row=1, column=1, padx=2)
        tk.Label(frame, text="时").grid(row=1, column=2)
        minute_cb.grid(row=1, column=3, padx=2)
        tk.Label(frame, text="分").grid(row=1, column=4)
        second_cb.grid(row=1, column=5, padx=2)
        tk.Label(frame, text="秒").grid(row=1, column=6)

        button_frame = tk.Frame(frame)
        button_frame.grid(row=2, column=0, columnspan=7, pady=(12, 0))

        def confirm():
            try:
                selected_dt = datetime(
                    int(year_var.get()),
                    int(month_var.get()),
                    int(day_var.get()),
                    int(hour_var.get()),
                    int(minute_var.get()),
                    int(second_var.get())
                )
            except ValueError:
                messagebox.showerror("时间错误", "请选择有效的日期和时间。", parent=dialog)
                return

            self._set_readonly_entry(target_entry, selected_dt.strftime("%Y-%m-%d %H:%M:%S"))
            dialog.destroy()

        def cancel():
            dialog.destroy()

        tk.Button(button_frame, text="确定", width=10, command=confirm).pack(side="left", padx=5)
        tk.Button(button_frame, text="取消", width=10, command=cancel).pack(side="left", padx=5)

        dialog.bind("<Return>", lambda event: confirm())
        dialog.bind("<Escape>", lambda event: cancel())

        # 让弹窗居中于主窗口
        dialog.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - dialog.winfo_width()) // 2
        y = self.root.winfo_rooty() + (self.root.winfo_height() - dialog.winfo_height()) // 2
        dialog.geometry(f"+{max(x, 0)}+{max(y, 0)}")

        self.root.wait_window(dialog)

    def browse_folder(self):
        folder = filedialog.askdirectory(title="选择保存文件夹")
        if folder:
            self.path_entry.delete(0, tk.END)
            self.path_entry.insert(0, folder)

    def browse_attachment(self):
        file_path = filedialog.askopenfilename(title="选择请假附件文件")
        if file_path:
            self.file_entry.delete(0, tk.END)
            self.file_entry.insert(0, file_path)

    def browse_schedule_pdf(self):
        file_path = filedialog.askopenfilename(
            title="选择课程表 PDF", filetypes=[("PDF 文件", "*.pdf")]
        )
        if file_path:
            self.ics_pdf_path_entry.delete(0, tk.END)
            self.ics_pdf_path_entry.insert(0, file_path)

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
            err_str = str(e)
            print_log(f"抓取课程列表失败: {err_str}")

            if "ERR_HTTP_RESPONSE_CODE_FAILURE" in err_str or "xgxt.hlu.edu.cn" in err_str or "webvpn.gzhu.edu.cn" in err_str:
                print_log("网络访问异常，可能无法访问教务/学工系统，请检查网络代理或网络状态")
                network_err_msg = (
                    "当前网络环境无法访问教务/学工系统，电脑可能开启了网络代理或已离线，"
                    "请检查并关闭系统网络代理或请检查网络状态后重试。"
                )
                self.root.after(0, lambda: messagebox.showerror("网络访问异常", network_err_msg))
            else:
                self.root.after(0, lambda err_msg=err_str: messagebox.showerror("ERROR", f"抓取课程失败：\n{err_msg}"))
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

        feature_info = SCHOOL_FEATURES.get(selected_school, {}).get(selected_feature)
        if not feature_info:
            messagebox.showerror("ERROR", "未找到对应的功能设置")
            return

        f_type = feature_info["type"]
        if f_type == "schedule_ics":
            self.start_schedule_ics()
            return

        if "未检测到" in selected_browser:
            messagebox.showerror("ERROR", "电脑内未安装可用浏览器，请先安装 Chrome/Edge 等浏览器后再尝试")
            return

        if not username or not password:
            messagebox.showwarning("WARNING", "请填写学号与密码")
            return

        kwargs = {}

        if f_type == "leave_apply":
            start_time = self.start_time_entry.get().strip()
            end_time = self.end_time_entry.get().strip()

            # 增加判断：结束时间不得早于开始时间
            if start_time and end_time:
                from datetime import datetime
                try:
                    st = datetime.strptime(start_time, "%Y-%m-%d %H:%M:%S")
                    et = datetime.strptime(end_time, "%Y-%m-%d %H:%M:%S")
                    if et < st:
                        messagebox.showwarning("WARNING", "结束时间不得早于开始时间！")
                        return
                except ValueError:
                    messagebox.showwarning("WARNING", "时间格式不正确！")
                    return

            kwargs["leave_type"] = self.leave_type_cb.get().strip()
            kwargs["start_time"] = start_time
            kwargs["end_time"] = end_time
            kwargs["reason"] = self.reason_text.get("1.0", tk.END).strip()
            kwargs["attachment"] = self.file_entry.get().strip()
            kwargs["ask_user"] = self.ask_user_continue

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

    def start_schedule_ics(self):
        selected_school = self.school_cb.get().strip()
        selected_feature = self.feature_cb.get().strip()
        feature_info = SCHOOL_FEATURES.get(selected_school, {}).get(selected_feature, {})
        extractor = feature_info.get("extractor", extract_schedule_data)
        builder = feature_info.get("builder", build_ics_content)
        download_handler = feature_info.get("handler")
        selected_browser = self.browser_cb.get().strip()
        username = self.username_entry.get().strip()
        password = self.password_entry.get().strip()
        show_browser = self.show_browser_var.get()

        # 最早开课周的周一锚点
        anchor_text = self.ics_anchor_entry.get().strip()
        try:
            first_lesson_start = datetime.strptime(anchor_text, "%Y-%m-%d %H:%M:%S")
        except ValueError as e:
            messagebox.showwarning(
                "设置有误",
                f"请设置最早开课周的周一第 1 节的开始时间。\n{e}",
            )
            return
        if first_lesson_start.weekday() != 0:
            messagebox.showwarning("设置有误", "请选择星期一（周一）的日期。")
            return

        save_dir = self.path_entry.get().strip()
        if not os.path.exists(save_dir):
            messagebox.showwarning("WARNING", "指定的路径非法")
            return

        use_local = self.use_local_pdf_var.get()
        pdf_path = None
        if use_local:
            pdf_path = self.ics_pdf_path_entry.get().strip()
            if not pdf_path or not os.path.isfile(pdf_path):
                messagebox.showwarning("提示", "请选择有效的课表 PDF 文件。")
                return
        else:
            if not download_handler:
                messagebox.showerror("ERROR", "当前院校未配置课程表下载处理器！")
                return
            if "未检测到" in selected_browser:
                messagebox.showerror("ERROR", "电脑内未安装可用浏览器，请先安装 Chrome/Edge 等浏览器后再尝试")
                return
            if not username or not password:
                messagebox.showwarning("WARNING", "请填写学号与密码")
                return

        # 云端订阅设置（访客明文 / 注册用户密文）
        cloud_config = None
        if self.upload_to_cloud_var.get():
            try:
                cloud_config = self.get_cloud_context()
            except CloudError as exc:
                messagebox.showwarning(
                    "云端设置不完整",
                    f"{exc}\n\n本次将跳过上传，只生成 ICS 文件。",
                    parent=self.root,
                )

        keep_files = self.keep_files_var.get()

        # 保存登录信息（学号/密码/API key）
        if self.save_login_var.get():
            saved_api_key = self.cloud_api_key_entry.get().strip() if self.registered_var.get() else ""
            save_login_cache(username, password, saved_api_key)

        self.btn_start.config(state="disabled")
        self.btn_stop.config(state="disabled")
        self.log("开始获取课表并生成 ICS...")

        threading.Thread(
            target=self.run_schedule_ics,
            args=(
                extractor, builder, download_handler, selected_browser, username, password,
                show_browser, use_local, pdf_path, save_dir, first_lesson_start,
                cloud_config, keep_files,
            ),
            daemon=True,
        ).start()

    def run_schedule_ics(
        self, extractor, builder, download_handler, selected_browser, username, password,
        show_browser, use_local, pdf_path, save_dir, first_lesson_start, cloud_config, keep_files,
    ):
        downloaded_pdf = None
        ics_save_path = None
        try:
            # 1. 获取课表 PDF（本地或下载）
            if use_local:
                self.root.after(0, self.log, f"使用本地课表 PDF：{pdf_path}")
            else:
                kwargs = {"save_dir": save_dir}
                downloaded_pdf = asyncio.run(
                    self._run_download_async(download_handler, selected_browser, username, password, kwargs, show_browser)
                )
                pdf_path = downloaded_pdf
                self.root.after(0, self.log, f"课程表已下载：{pdf_path}")

            # 2. 生成 ICS
            headers, rows = extractor(pdf_path)
            earliest_week = _earliest_start_week(headers, rows)
            if earliest_week is None:
                earliest_week = 1
            # 用户填的是最早开课周的周一，反推学期第 1 周。
            first_lesson_start = first_lesson_start - timedelta(weeks=(earliest_week - 1))
            ics_text = builder(headers, rows, first_lesson_start)

            student_id = username or "schedule"
            ics_save_path = os.path.join(save_dir, f"{student_id}.ics")
            with open(ics_save_path, "w", encoding="utf-8", newline="") as file:
                file.write(ics_text)
            self.root.after(0, self.log, f"ICS 已生成：{ics_save_path}（{len(rows)} 条课程安排）")

            # 3. 上传到云端订阅（访客：明文；注册用户：程序端 AES-GCM 密文）
            subscription_url = ""
            caldav_result = ""
            if cloud_config:
                try:
                    result = self._upload_ics_via_cloud(ics_text, cloud_config)
                    subscription_url = str(result.get("url") or result.get("subscriptionUrl") or "")
                    caldav_result = str(result.get("caldav") or result.get("caldavUrl") or "")
                    self.root.after(0, self.log, f"已上传到云端订阅：{subscription_url}")
                except Exception as e:
                    self.root.after(0, self.log, f"云端上传失败：{e}")

            # 4. 把订阅网址 / CalDAV 地址放到文本框，方便一键复制、显示二维码
            self.root.after(0, self._set_readonly_entry, self.subscription_url_entry, subscription_url)
            if caldav_result:
                self.root.after(0, self._set_readonly_entry, self.caldav_url_entry, caldav_result)

            # 5. 若不保留本地文件且已上传成功，删除本地 PDF/ICS
            if cloud_config and subscription_url and not keep_files:
                for path in (downloaded_pdf, ics_save_path):
                    if path and os.path.isfile(path):
                        try:
                            os.remove(path)
                            self.root.after(0, self.log, f"已删除本地文件：{path}")
                        except Exception:
                            pass

            message = f"课程日历已生成：\n{ics_save_path}"
            if subscription_url:
                message += f"\n\n云端订阅地址：\n{subscription_url}"
            if caldav_result:
                message += f"\n\nCalDAV：\n{caldav_result}\n用户名/密码均填 API key。"
            if cloud_config and not subscription_url:
                message += "\n\n云端上传未成功，请查看运行日志。"
            self.root.after(
                0,
                lambda m=message: messagebox.showinfo("完成", m, parent=self.root),
            )
        except Exception as e:
            err_msg = str(e)
            self.root.after(0, self.log, f"生成 ICS 失败：{err_msg}")
            self.root.after(
                0,
                lambda message=err_msg: messagebox.showerror(
                    "ICS 生成失败", message, parent=self.root
                ),
            )
        finally:
            self.root.after(0, lambda: self.btn_start.config(state="normal"))

    async def _run_download_async(self, handler_func, selected_browser, username, password, kwargs, show_browser):
        """执行下载处理器并返回课表 PDF 路径。"""
        def print_log(msg):
            self.root.after(0, self.log, msg)

        is_headless = not show_browser
        async with async_playwright() as p:
            browser, used_browser_name = await self._obtain_browser_instance(p, selected_browser, is_headless, print_log)
            print_log(f"已调用系统浏览器: [{used_browser_name}]")
            context = await browser.new_context(accept_downloads=True)
            page = await context.new_page()
            pdf_path = await handler_func(page, username, password, kwargs, print_log)
            await browser.close()
            return pdf_path

    def run_async_task(self, handler_func, selected_browser, username, password, kwargs, show_browser):
        asyncio.run(self.execute_playwright(handler_func, selected_browser, username, password, kwargs, show_browser))

    async def execute_playwright(self, handler_func, selected_browser, username, password, kwargs, show_browser):
        def print_log(msg):
            self.root.after(0, self.log, msg)

        # 请假申请在最终提交前需要用户在浏览器页面中人工核对/修改，
        # 因此无论“显示浏览器操作画面”是否勾选，都必须使用可见浏览器。
        is_leave_apply = handler_func == leave_application_hlu
        is_headless = False if is_leave_apply else not show_browser

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
            err_str = str(e)
            print_log(f"失败: {err_str}")

            if "ERR_HTTP_RESPONSE_CODE_FAILURE" in err_str or "xgxt.hlu.edu.cn" in err_str or "webvpn.gzhu.edu.cn" in err_str:
                print_log("网络访问异常，可能无法访问教务/学工系统，请检查网络代理或网络状态")
                network_err_msg = (
                    "当前网络环境无法访问教务/学工系统，电脑可能开启了网络代理或已离线，"
                    "请检查并关闭系统网络代理或网络状态后重试。"
                )
                self.root.after(0, lambda: messagebox.showerror("网络访问异常", network_err_msg))
            else:
                self.root.after(0, lambda err_msg=err_str: messagebox.showerror("ERROR", f"执行失败：\n{err_msg}"))
        finally:
            self.root.after(0, lambda: self.btn_start.config(state="normal"))
            self.root.after(0, lambda: self.btn_stop.config(state="disabled"))


class CloudFilesWindow(tk.Toplevel):
    """在程序內管理雲端已上傳文件（列表 / 上傳 / 刪除 / 複製 / 二維碼）。"""

    def __init__(self, app, context):
        super().__init__(app.root)
        self.app = app
        self.context = context
        self.rows = {}
        self.title("云端文件列表")
        self.geometry("860x430")
        self.transient(app.root)
        self.resizable(True, True)

        top = tk.Frame(self)
        top.pack(fill="x", padx=10, pady=(8, 4))
        self.mode_label = tk.Label(top, text="", font=(SYS_FONT, 9, "bold"))
        self.mode_label.pack(side="left")
        tk.Button(top, text="刷新", font=(SYS_FONT, 9), command=self.refresh).pack(side="right", padx=3)
        tk.Button(top, text="上传本地 ICS...", font=(SYS_FONT, 9), command=self.upload_local).pack(side="right", padx=3)

        columns = ("filename", "kind", "size", "url")
        tree_frame = tk.Frame(self)
        tree_frame.pack(fill="both", expand=True, padx=10, pady=4)
        self.tree = ttk.Treeview(tree_frame, columns=columns, show="headings", height=11)
        self.tree.heading("filename", text="文件名")
        self.tree.heading("kind", text="类型")
        self.tree.heading("size", text="大小")
        self.tree.heading("url", text="订阅地址")
        self.tree.column("filename", width=220, anchor="w")
        self.tree.column("kind", width=80, anchor="center")
        self.tree.column("size", width=80, anchor="e")
        self.tree.column("url", width=440, anchor="w")
        self.tree.pack(side="left", fill="both", expand=True)

        scrollbar = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        scrollbar.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.bind("<Double-1>", lambda event: self.copy_url())

        buttons = tk.Frame(self)
        buttons.pack(fill="x", padx=10, pady=(2, 4))
        tk.Button(buttons, text="复制选中地址", font=(SYS_FONT, 9), command=self.copy_url).pack(side="left", padx=(0, 6))
        tk.Button(buttons, text="显示二维码", font=(SYS_FONT, 9), command=self.show_qr).pack(side="left", padx=(0, 6))
        tk.Button(buttons, text="删除选中", font=(SYS_FONT, 9), command=self.delete_selected).pack(side="left", padx=(0, 6))
        tk.Button(buttons, text="关闭", font=(SYS_FONT, 9), command=self.destroy).pack(side="right")

        self.status = tk.Label(self, text="", font=(SYS_FONT, 9), fg="#555555", anchor="w")
        self.status.pack(fill="x", padx=10, pady=(0, 8))

        self.refresh()

    def _apply_context(self):
        try:
            context = self.app.get_cloud_context()
        except CloudError:
            return None
        self.context = context
        if context["mode"] == "registered":
            self.mode_label.config(
                text=f"注册用户 · {context['school']} · 密文 ics/{context['school']}/{context['api_key']}.ics"
            )
        else:
            self.mode_label.config(
                text=f"访客 · {context['school']} · 明文 ics/guest/{context['school']}/{context['student_id']}.ics"
            )
        return context

    def refresh(self):
        if not self.winfo_exists():
            return
        context = self._apply_context()
        if context is None:
            self.status.config(text="云端参数不完整；请填写学校、学号或 API key。")
            return

        self.status.config(text="正在读取云端文件列表...")

        def worker():
            try:
                api = CloudAPI(context["base_url"])
                if context["mode"] == "registered":
                    data = api.list_registered(context["api_key"])
                else:
                    data = api.list_guest(context["school"], context["student_id"])
            except Exception as exc:
                self._safe_after(lambda msg=str(exc): self._on_error(msg))
            else:
                self._safe_after(lambda payload=data: self._on_loaded(payload))

        threading.Thread(target=worker, daemon=True).start()

    def _safe_after(self, callback):
        try:
            if self.winfo_exists():
                self.after(0, callback)
        except Exception:
            pass

    def _on_loaded(self, data):
        if not self.winfo_exists():
            return
        for item in self.tree.get_children():
            self.tree.delete(item)
        self.rows.clear()

        files = data.get("files") if isinstance(data, dict) else []
        for index, entry in enumerate(files or []):
            if not isinstance(entry, dict):
                continue
            kind = "注册" if entry.get("kind") == "registered" else "访客"
            size = self._format_size(entry.get("size"))
            url = str(entry.get("url") or "")
            item_id = f"row-{index}"
            self.tree.insert(
                "", "end", iid=item_id,
                values=(entry.get("filename", ""), kind, size, url),
            )
            self.rows[item_id] = entry

        if not self.rows:
            self.status.config(text="暂无已上传文件。可点击“上传本地 ICS...”或在主程序生成后自动上传。")
        else:
            self.status.config(text=f"共 {len(self.rows)} 个文件。双击一行可复制订阅地址。")

    def _on_error(self, message):
        if not self.winfo_exists():
            return
        self.status.config(text=f"读取失败：{message}")

    @staticmethod
    def _format_size(value):
        try:
            size = int(value or 0)
        except Exception:
            return "-"
        if size < 1024:
            return f"{size} B"
        if size < 1024 * 1024:
            return f"{size / 1024:.1f} KB"
        return f"{size / (1024 * 1024):.1f} MB"

    def _selected(self):
        selection = self.tree.selection()
        if not selection:
            messagebox.showinfo("提示", "请先在列表中选择一个文件。", parent=self)
            return None
        return self.rows.get(selection[0])

    def upload_local(self):
        self.app.upload_local_ics()

    def copy_url(self):
        entry = self._selected()
        if not entry:
            return
        url = str(entry.get("url") or "")
        if not url:
            return
        self.clipboard_clear()
        self.clipboard_append(url)
        self.status.config(text=f"已复制：{url}")

    def show_qr(self):
        entry = self._selected()
        if not entry:
            return
        url = str(entry.get("url") or "")
        if not url:
            return
        self.app.show_qr_window(webcal_url(url), url, str(entry.get("caldav") or ""))

    def delete_selected(self):
        entry = self._selected()
        if not entry:
            return
        if not messagebox.askyesno(
            "确认删除", f"确定删除云端文件 {entry.get('filename')}？", parent=self
        ):
            return

        context = self._apply_context()
        if context is None:
            return

        def worker():
            try:
                api = CloudAPI(context["base_url"])
                if context["mode"] == "registered":
                    api.delete_registered(context["api_key"], str(entry.get("filename") or ""))
                else:
                    api.delete_guest(context["school"], context["student_id"])
            except Exception as exc:
                self._safe_after(lambda msg=str(exc): self._on_error(f"删除失败：{msg}"))
            else:
                self._safe_after(lambda: self._on_deleted(entry))

        threading.Thread(target=worker, daemon=True).start()

    def _on_deleted(self, entry):
        if not self.winfo_exists():
            return
        self.status.config(text=f"已删除：{entry.get('filename')}")
        self.refresh()


if __name__ == "__main__":
    root = tk.Tk()
    app = UnifiedCampusApp(root)
    root.mainloop()
