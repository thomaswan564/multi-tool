# -*- coding: utf-8 -*-
"""Regression tests for pure schedule and utility functions in the desktop app."""
import importlib.util
import os
import sys
import unittest
from datetime import datetime
from unittest.mock import patch

HERE = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.dirname(HERE)
sys.path.insert(0, APP_DIR)
sys.path.insert(0, os.path.join(APP_DIR, "_vendor"))
SPEC = importlib.util.spec_from_file_location(
    "multi_tool_app", os.path.join(APP_DIR, "code-multi-tool-v3.0.py")
)
app = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(app)

MON = "\u661f\u671f\u4e00"
TUE = "\u661f\u671f\u4e8c"
SUN = "\u661f\u671f\u65e5"
COURSE = "\u4e2d\u6587\u8bfe\u7a0b"
ROOM = "\u6559\u5ba4 A"


class WeekSeriesTests(unittest.TestCase):
    def test_contiguous_range_and_disjoint_weeks(self):
        self.assertEqual(app._week_series("1-4\u5468、7\u5468"), [(1, 1, 4), (7, 1, 1)])

    def test_odd_and_even_weeks(self):
        self.assertEqual(app._week_series("1-8\u5468\uff08\u5355\u5468\uff09"), [(1, 2, 4)])
        self.assertEqual(app._week_series("2-8\u5468(\u53cc)"), [(2, 2, 4)])

    def test_bad_range_raises(self):
        with self.assertRaises(ValueError):
            app._week_series("5-2\u5468")

    def test_earliest_week_skips_invalid_rows(self):
        self.assertEqual(app._earliest_start_week(["\u8ab2\u7a0b", "\u5468\u6b21"], [["A", "bad"], ["B", "3-8\u5468"]]), 3)


class CaldavSetupTests(unittest.TestCase):
    def test_setup_dialog_shows_credentials_and_clients(self):
        instance = object.__new__(app.UnifiedCampusApp)
        instance.caldav_url_entry = type("Entry", (), {"get": lambda self: "https://calendar.example/caldav/gzhu/key/"})()
        instance.root = object()
        with patch.object(app.messagebox, "showinfo") as showinfo:
            instance.show_caldav_setup()
        text = showinfo.call_args.args[1]
        self.assertIn("https://calendar.example/caldav/gzhu/key/", text)
        self.assertIn("用戶名：你的 API key", text)
        self.assertIn("Apple 日曆", text)
        self.assertIn("Thunderbird", text)
        self.assertIn("唯讀", text)

    def test_guest_without_caldav_gets_actionable_warning(self):
        instance = object.__new__(app.UnifiedCampusApp)
        instance.caldav_url_entry = type("Entry", (), {"get": lambda self: ""})()
        instance.root = object()
        with patch.object(app.messagebox, "showwarning") as showwarning:
            instance.show_caldav_setup()
        self.assertIn("API key", showwarning.call_args.args[1])


class IcsGenerationTests(unittest.TestCase):
    HEADERS = ["\u661f\u671f", "\u8282\u6b21", "\u8bfe\u7a0b", "\u5468\u6b21", "\u5730\u70b9"]
    ANCHOR = datetime(2026, 9, 7, 8, 20)

    def test_weekly_event_and_blank_location_property(self):
        ics = app.build_ics_content(self.HEADERS, [[MON, "1-2\u8282", COURSE, "1-4\u5468", ROOM]], self.ANCHOR)
        self.assertIn(f"SUMMARY:{COURSE} - {ROOM.replace(' ', '')}\r\n", ics)
        self.assertIn("LOCATION:\r\n", ics)
        self.assertIn("DTSTART;TZID=Asia/Shanghai:20260907T082000", ics)
        self.assertIn("DTEND;TZID=Asia/Shanghai:20260907T094500", ics)
        self.assertIn("RRULE:FREQ=WEEKLY;COUNT=4", ics)
        self.assertRegex(ics, r"DTSTAMP:\d{8}T\d{6}Z")

    def test_alternate_week_missing_location_and_sunday(self):
        ics = app.build_ics_content(self.HEADERS, [[SUN, "3\u8282", "Lab", "2-8\u5468\uff08\u53cc\u5468\uff09", "\u672a\u6392\u5730\u70b9"]], self.ANCHOR)
        self.assertIn("SUMMARY:Lab\r\n", ics)
        self.assertIn("LOCATION:\r\n", ics)
        self.assertIn("DTSTART;TZID=Asia/Shanghai:20260920T100000", ics)
        self.assertIn("RRULE:FREQ=WEEKLY;INTERVAL=2;COUNT=4", ics)

    def test_gzhu_period_times(self):
        gzhu_anchor = datetime(2026, 9, 7, 8, 30)
        ics = app.build_ics_content_gzhu(self.HEADERS, [[TUE, "1-2\u8282", "GZHU", "1\u5468", ""]], gzhu_anchor)
        self.assertIn("DTSTART;TZID=Asia/Shanghai:20260908T083000", ics)
        self.assertIn("DTEND;TZID=Asia/Shanghai:20260908T100500", ics)

    def test_invalid_inputs_raise(self):
        with self.assertRaisesRegex(ValueError, "\u661f\u671f\u4e00"):
            app.build_ics_content(self.HEADERS, [[MON, "1\u8282", "A", "1\u5468", ""]], datetime(2026, 9, 8, 8, 20))
        with self.assertRaisesRegex(ValueError, "\u7f3a\u5c11\u751f\u6210\u65e5\u5386"):
            app.build_ics_content(["\u8ab2\u7a0b"], [["A"]], self.ANCHOR)
        with self.assertRaises(ValueError):
            app.build_ics_content(self.HEADERS, [["???", "1\u8282", "A", "1\u5468", ""]], self.ANCHOR)
        with self.assertRaises(ValueError):
            app.build_ics_content(self.HEADERS, [[MON, "off", "A", "1\u5468", ""]], self.ANCHOR)
        with self.assertRaises(ValueError):
            app.build_ics_content(self.HEADERS, [[MON, "1\u8282", "", "1\u5468", ""]], self.ANCHOR)

    def test_utf8_ics_lines_fold_at_75_bytes(self):
        folded = app._fold_ics_line("SUMMARY:" + "\u8ab2\u7a0b" * 50)
        for line in folded.split("\r\n"):
            self.assertLessEqual(len(line.encode("utf-8")), 75)


class PdfScheduleParserTests(unittest.TestCase):
    def test_hlu_pdf_extractor_success_and_missing_schedule(self):
        table = [["\u8282\u6b21", MON], ["1", "\u8bfe\u7a0b\u7532\n(1-1\u8282)\n1-2\u5468"]]
        with patch.object(app, "_read_pdf_tables", return_value=[(1, 1, table)]):
            headers, rows = app.extract_schedule_data("fixture.pdf")
        self.assertEqual(rows[0][3], "\u8bfe\u7a0b\u7532")
        with patch.object(app, "_read_pdf_tables", return_value=[]):
            with self.assertRaises(ValueError):
                app.extract_schedule_data("empty.pdf")

    def test_hlu_style_table_metadata_and_weekday_order(self):
        table = [
            ["\u8282\u6b21", MON, TUE],
            ["1", "\u8bfe\u7a0b\u7532\n(1-2\u8282)\n1-16\u5468 / \u573a\u5730: A101 / \u6559\u5e08: \u738b\u8001\u5e08", ""],
            ["2", "", "\u8bfe\u7a0b\u4e59\n(2-2\u8282)\n2-8\u5468(\u53cc\u5468) / \u573a\u5730: B202"],
        ]
        headers, rows, found = app._extract_schedule([(1, 1, table)])
        self.assertTrue(found)
        self.assertEqual(headers[:4], ["\u661f\u671f", "\u65f6\u6bb5", "\u8282\u6b21", "\u8bfe\u7a0b"])
        self.assertEqual([row[0] for row in rows], [MON, TUE])
        self.assertEqual(rows[0][3:7], ["\u8bfe\u7a0b\u7532", "1-16\u5468", "A101", "\u738b\u8001\u5e08"])
        self.assertEqual(rows[1][4], "2-8\u5468(\u53cc\u5468)")

    def test_missing_header(self):
        self.assertEqual(app._extract_schedule([(1, 1, [["title"], ["text"]])]), ([], [], False))

    def test_gzhu_parser_does_not_leak_next_course_into_teacher(self):
        parsed = app._parse_gzhu_column([
            "\u9ad8\u7b49\u6570\u5b66*",
            "(1-2\u8282)1-16\u5468/\u573a\u5730:\u6559\u5b66\u697c 101/\u6559\u5e08:\u674e\u8001\u5e08",
            "\u5927\u5b66\u82f1\u8bed",
            "(3-4\u8282)2-16\u5468(\u53cc)/\u573a\u5730:\u5916\u8bed\u697c/\u6559\u5e08:\u5f20\u8001\u5e08",
        ])
        self.assertEqual(parsed, [
            ("\u9ad8\u7b49\u6570\u5b66", 1, 2, "1-16\u5468", "\u6559\u5b66\u697c 101", "\u674e\u8001\u5e08"),
            ("\u5927\u5b66\u82f1\u8bed", 3, 4, "2-16\u5468(\u53cc)", "\u5916\u8bed\u697c", "\u5f20\u8001\u5e08"),
        ])

    def test_gzhu_pdf_extractor_maps_weekday_columns(self):
        class Cropped:
            def __init__(self, text):
                self.text = text

            def extract_text(self):
                return self.text

        class Page:
            width = 700
            height = 800

            def extract_words(self):
                return [
                    {"text": name, "x0": index * 100 - 10, "x1": index * 100 + 10, "bottom": 20}
                    for index, name in enumerate(app.GZHU_WEEKDAY_NAMES)
                ]

            def crop(self, bbox):
                x0, _top, _x1, _bottom = bbox
                if 150 <= x0 < 250:  # Tuesday column
                    return Cropped("\u5927\u5b66\u82f1\u8bed\n(1-2\u8282)1-16\u5468/\u573a\u5730:\u6559\u5b66\u697c/\u6559\u5e08:\u674e\u8001\u5e08")
                return Cropped("")

        class Pdf:
            pages = [Page()]

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        with patch.object(app.pdfplumber, "open", return_value=Pdf()):
            headers, rows = app.extract_schedule_data_gzhu("fixture.pdf")
        self.assertEqual(headers[0:3], ["\u661f\u671f", "\u8282\u6b21", "\u8bfe\u7a0b"])
        self.assertEqual(rows, [["\u661f\u671f\u4e8c", "1-2\u8282", "\u5927\u5b66\u82f1\u8bed", "1-16\u5468", "\u6559\u5b66\u697c", "\u674e\u8001\u5e08"]])


class BrowserAndCacheTests(unittest.TestCase):
    def test_browser_priority_and_unknown_names(self):
        values = {"Firefox": {}, "Microsoft Edge": {}, "Google Chrome": {}, "Custom": {}}
        self.assertEqual([name for name, _ in app.get_ordered_detected_browsers(values)], ["Google Chrome", "Microsoft Edge", "Firefox", "Custom"])

    def test_cache_roundtrip_and_corrupt_fallback(self):
        with patch.object(app, "CACHE_FILE", os.path.join(HERE, "_courses_test.json")), patch.object(app, "LOGIN_CACHE_FILE", os.path.join(HERE, "_login_test.json")):
            try:
                self.assertEqual(app.load_courses_cache(), [])
                app.save_courses_cache(["Course A"])
                self.assertEqual(app.load_courses_cache(), ["Course A"])
                app.save_login_cache("user", "secret", "key")
                self.assertEqual(app.load_login_cache(), {"username": "user", "password": "secret", "api_key": "key"})
                with open(app.CACHE_FILE, "w", encoding="utf-8") as file:
                    file.write("{")
                self.assertEqual(app.load_courses_cache(), [])
                app.clear_courses_cache()
                self.assertFalse(os.path.exists(app.CACHE_FILE))
            finally:
                for path in (app.CACHE_FILE, app.LOGIN_CACHE_FILE):
                    if os.path.exists(path):
                        os.remove(path)


if __name__ == "__main__":
    unittest.main()
