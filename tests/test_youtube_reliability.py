"""YouTube links: yt-dlp update at start, Chrome-cookie retry on sign-in/bot checks, clear blocked message."""
import os
import subprocess
import unittest
from unittest.mock import patch
import url_media
from url_media import MediaError, YOUTUBE_BLOCKED, download

BOT = "ERROR: [youtube] abc: Sign in to confirm you're not a bot. Use --cookies-from-browser or --cookies"


class YouTubeRetryTests(unittest.TestCase):
    def fake(self, *replies):
        """yt-dlp fails with each reply in turn; records the commands."""
        self.commands = []
        replies = list(replies)

        def run(command, **kwargs):
            self.commands.append(command)
            return subprocess.CompletedProcess(command, 1, '', replies.pop(0))
        return patch('url_media.subprocess.run', side_effect=run)

    def test_bot_check_retries_with_chrome_cookies_then_says_youtube_blocked_it(self):
        with self.fake(BOT, BOT), patch.dict(os.environ, {}, clear=False):
            os.environ.pop('CLIPFORGE_YTDLP_COOKIES_BROWSER', None)
            with self.assertRaises(MediaError) as caught:
                download('https://www.youtube.com/watch?v=abc')
        self.assertEqual(str(caught.exception), YOUTUBE_BLOCKED)
        self.assertEqual(len(self.commands), 2)
        self.assertNotIn('--cookies-from-browser', self.commands[0])
        self.assertEqual(self.commands[1][self.commands[1].index('--cookies-from-browser') + 1], 'chrome')

    def test_cookie_retry_can_succeed(self):
        def run(command, **kwargs):
            if '--cookies-from-browser' not in command:
                return subprocess.CompletedProcess(command, 1, '', BOT)
            raise RuntimeError('reached the cookie retry')
        with patch('url_media.subprocess.run', side_effect=run), self.assertRaisesRegex(RuntimeError, 'cookie retry'):
            download('https://youtu.be/abc')

    def test_other_youtube_failures_do_not_load_cookies(self):
        with self.fake('ERROR: HTTP Error 403: Forbidden'), self.assertRaises(MediaError) as caught:
            download('https://youtu.be/abc')
        self.assertEqual(str(caught.exception), YOUTUBE_BLOCKED)
        self.assertEqual(len(self.commands), 1)

    def test_cookie_retry_can_be_turned_off(self):
        with self.fake(BOT), patch.dict(os.environ, {'CLIPFORGE_YTDLP_COOKIES_BROWSER': ''}), self.assertRaises(MediaError):
            download('https://youtu.be/abc')
        self.assertEqual(len(self.commands), 1)

    def test_tiktok_never_loads_cookies_and_keeps_its_message(self):
        with self.fake(BOT), self.assertRaises(MediaError) as caught:
            download('https://www.tiktok.com/@a/video/1')
        self.assertEqual(len(self.commands), 1)
        self.assertNotEqual(str(caught.exception), YOUTUBE_BLOCKED)


class UpdateTests(unittest.TestCase):
    def test_update_installs_the_newest_yt_dlp(self):
        with patch('url_media.subprocess.run', return_value=subprocess.CompletedProcess([], 0, '', '')) as run, \
                patch.dict(os.environ, {'CLIPFORGE_YTDLP_AUTO_UPDATE': '1'}):
            self.assertTrue(url_media.update_downloader())
        command = run.call_args[0][0]
        self.assertEqual(command[1:6], ['-m', 'pip', 'install', '--upgrade', '--quiet'])
        self.assertEqual(command[-1], 'yt-dlp')

    def test_update_can_be_turned_off_and_failure_is_harmless(self):
        with patch('url_media.subprocess.run') as run, patch.dict(os.environ, {'CLIPFORGE_YTDLP_AUTO_UPDATE': '0'}):
            self.assertFalse(url_media.update_downloader())
        run.assert_not_called()
        with patch('url_media.subprocess.run', side_effect=OSError), patch.dict(os.environ, {'CLIPFORGE_YTDLP_AUTO_UPDATE': '1'}):
            self.assertFalse(url_media.update_downloader())

    def test_backend_updates_yt_dlp_when_it_starts(self):
        import app
        self.assertIn(app.update_video_downloader, app.app.router.on_startup)


if __name__ == '__main__':
    unittest.main()
