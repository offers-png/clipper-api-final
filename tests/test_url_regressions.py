"""Regression coverage for URL transport and production log findings; offline."""
import asyncio
import ast
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
import requests
from fastapi import HTTPException
from billing_identity import checkout_reference
from url_media import download, MediaError


class TransportTests(unittest.TestCase):
    def test_slow_url_download_does_not_block_async_request_loop(self):
        from fastapi import UploadFile, File, Form
        from fastapi.responses import JSONResponse
        source=Path(__file__).resolve().parents[1]/'app.py'
        node=next(n for n in ast.parse(source.read_text(encoding='utf-8')).body if isinstance(n,ast.AsyncFunctionDef) and n.name=='transcribe_audio')
        node.decorator_list=[]
        started=threading.Event();release=threading.Event()
        def slow_download(url):
            started.set();release.wait(.5)
            raise MediaError('Simulated host deadline; no external request made')
        scope=dict(UploadFile=UploadFile,File=File,Form=Form,JSONResponse=JSONResponse,
                   asyncio=asyncio,os=os,MediaError=MediaError,download_to_tmp=slow_download,
                   Request=object,ai_actor=lambda request:{'email':'fixture'})
        exec(compile(ast.Module(body=[node],type_ignores=[]),str(source),'exec'),scope)
        async def check():
            task=asyncio.create_task(scope['transcribe_audio'](file=None,url='https://example.invalid/source.mp4',request=None))
            try:
                await asyncio.sleep(.02)
                self.assertTrue(started.is_set());self.assertFalse(task.done())
            finally:release.set()
            self.assertEqual((await task).status_code,422)
        asyncio.run(check())

    def test_direct_read_timeout_is_bounded_and_does_not_echo_url(self):
        with patch('url_media.requests.get',side_effect=requests.ReadTimeout('private-url-token')):
            with self.assertRaises(MediaError) as caught:
                download('https://example.invalid/source.mp4')
        self.assertIn('timed out',str(caught.exception));self.assertNotIn('private-url-token',str(caught.exception))

    def test_redirect_loop_has_safe_failure(self):
        with patch('url_media.requests.get',side_effect=requests.TooManyRedirects('private-url-token')):
            with self.assertRaises(MediaError) as caught:download('https://example.invalid/source.mp4')
        self.assertNotIn('private-url-token',str(caught.exception))

    def test_platform_timeout_is_bounded(self):
        with patch('url_media.subprocess.run',side_effect=subprocess.TimeoutExpired('yt_dlp',900)):
            with self.assertRaisesRegex(MediaError,'timed out'):download('https://www.tiktok.com/@fixture/video/1')

    def test_ip_block_is_reported_without_raw_provider_diagnostics(self):
        result=subprocess.CompletedProcess([],1,'','Your IP address is blocked from accessing this post private-token')
        with patch('url_media.subprocess.run',return_value=result):
            with self.assertRaises(MediaError) as caught:download('https://www.tiktok.com/@fixture/video/1')
        self.assertIn('blocked',str(caught.exception));self.assertNotIn('private-token',str(caught.exception))

    def test_redirect_to_platform_page_routes_to_extractor(self):
        response=Mock(status_code=200,headers={'Content-Type':'text/html'},url='https://www.tiktok.com/@fixture/video/1')
        response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
        with patch('url_media.requests.get',return_value=response),patch('url_media.platform_download',side_effect=MediaError('extractor reached')) as extract:
            with self.assertRaisesRegex(MediaError,'extractor reached'):download('https://example.invalid/share')
        extract.assert_called_once();self.assertEqual(extract.call_args.args[0],response.url)
        response.iter_content.assert_not_called()

    def test_deceptive_hostname_does_not_route_to_tiktok(self):
        with patch('url_media.requests.get',side_effect=requests.ConnectionError()),patch('url_media.platform_download') as extract:
            with self.assertRaises(MediaError):download('https://tiktok.com.example.invalid/source.mp4')
        extract.assert_not_called()


class BillingTransportTests(unittest.TestCase):
    def request(self,body,content_type):
        request=Mock(headers={'content-type':content_type})
        async def read():return body
        async def form():raise AssertionError('legacy multipart parser must not run for empty forms')
        request.body=read;request.form=form
        return request

    def test_old_empty_browser_multipart_skips_broken_parser(self):
        boundary='----WebKitFormBoundaryfixture'
        request=self.request(('--'+boundary+'--\r\n').encode(),'multipart/form-data; boundary='+boundary)
        self.assertIsNone(asyncio.run(checkout_reference(request)))

    def test_new_empty_encoded_body_needs_no_parser(self):
        self.assertIsNone(asyncio.run(checkout_reference(self.request(b'','application/x-www-form-urlencoded'))))

    def test_malformed_request_returns_400_not_500(self):
        request=self.request(b'broken-boundary','multipart/form-data; boundary=fixture')
        with self.assertRaises(HTTPException) as caught:asyncio.run(checkout_reference(request))
        self.assertEqual(caught.exception.status_code,400)

    def test_oversized_verification_body_rejected(self):
        with self.assertRaises(HTTPException) as caught:asyncio.run(checkout_reference(self.request(b'x'*16385,'text/plain')))
        self.assertEqual(caught.exception.status_code,413)
