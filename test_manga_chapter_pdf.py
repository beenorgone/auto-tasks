"""Focused parser and PDF checks for manga-chapter-pdf.py."""

import importlib.util
import contextlib
import io
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from PIL import Image


SPEC = importlib.util.spec_from_file_location("manga_chapter_pdf", Path(__file__).with_name("manga-chapter-pdf.py"))
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class MangaChapterPdfTests(unittest.TestCase):
    def test_parse_only_reader_images_in_order(self):
        html = ('<h1 id="chapter-heading">H2 - Tập 22</h1>'
                '<img src="https://truyentuoitho.com/cover.jpg">'
                '<div class="reading-content"><div id="comic-wrap">'
                '<div class="page-box"><img src="https://img.resourcehub.shop/1.webp"></div>'
                '<div class="page-box"><img data-src="https://img.resourcehub.shop/2.webp"></div>'
                '</div></div>')
        chapter = MODULE.parse_chapter(html, "https://truyentuoitho.com/manga/h2/tap-22/")
        self.assertEqual(chapter.title, "H2 - Tập 22")
        self.assertEqual(chapter.image_urls, ["https://img.resourcehub.shop/1.webp",
                                              "https://img.resourcehub.shop/2.webp"])

    def test_rejects_missing_or_untrusted_pages(self):
        with self.assertRaises(ValueError):
            MODULE.parse_chapter('<h1 id="chapter-heading">Locked</h1>',
                                 "https://truyentuoitho.com/manga/h2/tap-22/")
        html = ('<h1 id="chapter-heading">H2</h1><div class="reading-content">'
                '<div id="comic-wrap"><div class="page-box">'
                '<img src="http://127.0.0.1/private"></div></div></div>')
        with self.assertRaises(ValueError):
            MODULE.parse_chapter(html, "https://truyentuoitho.com/manga/h2/tap-22/")

    def test_bulk_list_deduplicates_and_ignores_comments(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "links.txt"
            source.write_text("# chapters\nhttps://truyentuoitho.com/manga/h2/tap-23/\n\n"
                              "https://truyentuoitho.com/manga/h2/tap-24/\n", encoding="utf-8")
            urls = MODULE.read_urls(["https://truyentuoitho.com/manga/h2/tap-23/"], source)
            self.assertEqual(len(urls), 2)
            self.assertTrue(urls[-1].endswith("tap-24/"))

    def test_discover_series_uses_chapter_list_order(self):
        class Response:
            status_code = 200
            is_redirect = False
            text = ('<li class="wp-manga-chapter"><a href="/manga/ninja-loan-thi/tap-2/">Tập 2</a></li>'
                    '<li class="wp-manga-chapter"><a href="/manga/ninja-loan-thi/tap-1/">Tập 1</a></li>')

            def raise_for_status(self):
                pass

        session = mock.Mock()
        session.post.return_value = Response()
        urls = MODULE.discover_series(session, "https://truyentuoitho.com/manga/ninja-loan-thi/")
        self.assertEqual([url.rsplit("/", 2)[-2] for url in urls], ["tap-1", "tap-2"])

    def test_rate_limit_stops_batch(self):
        class Response:
            status_code = 429
            headers = {"Retry-After": "120"}

            def close(self):
                pass

        class Session:
            def get(self, *args, **kwargs):
                return Response()

        with self.assertRaisesRegex(MODULE.AccessLimitedError, "Retry-After: 120"):
            MODULE.get(Session(), "https://img.resourcehub.shop/1.webp",
                       referer="https://truyentuoitho.com/")

    def test_status_shows_next_step_without_network(self):
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as temporary, contextlib.redirect_stdout(output):
            MODULE.show_status(["https://truyentuoitho.com/manga/h2/tap-23/"], Path(temporary))
        self.assertIn("Chưa bắt đầu", output.getvalue())
        self.assertIn("tiếp theo: tải", output.getvalue())

    def test_queue_remembers_inaccessible_chapter(self):
        urls = ["https://truyentuoitho.com/manga/ninja-loan-thi/tap-56/",
                "https://truyentuoitho.com/manga/ninja-loan-thi/tap-57/"]
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            with mock.patch.object(MODULE, "process_chapter",
                                   side_effect=MODULE.PagesUnavailableError("Cần quyền truy cập")) as process:
                MODULE.run_queue(urls, folder, 1, False, object(), 0)
                self.assertEqual(process.call_count, 1)
                MODULE.run_queue(urls, folder, 1, False, object(), 0)
                self.assertEqual(process.call_count, 2)
            state = MODULE.read_queue_state(MODULE.queue_state_path(urls, folder))
            self.assertEqual(set(state["blocked"]), set(urls))
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                MODULE.show_status(urls, folder)
            self.assertEqual(output.getvalue().count("Cần xem lại"), 2)

    def test_pdf_requires_all_pages_and_preserves_page_count(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            paths = [folder / "1.webp", folder / "2.webp"]
            Image.new("RGB", (200, 300), "white").save(paths[0], "WEBP")
            Image.new("RGB", (300, 200), "black").save(paths[1], "WEBP")
            chapter = MODULE.Chapter("https://truyentuoitho.com/manga/h2/tap-22/", "H2", ["a", "b"])
            output = folder / "book.pdf"
            with self.assertRaises(ValueError):
                MODULE.build_pdf(chapter, paths[:1], output)
            self.assertFalse(output.exists())
            MODULE.build_pdf(chapter, paths, output)
            info = subprocess.check_output(["pdfinfo", str(output)], text=True)
            self.assertIn("Pages:           2", info)
            self.assertTrue(MODULE.complete_pdf(output, 2))
            self.assertFalse(MODULE.complete_pdf(output, 3))


if __name__ == "__main__":
    unittest.main()
