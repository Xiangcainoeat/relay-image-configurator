import base64
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import struct
import tempfile
import time
import unittest
from unittest.mock import patch, MagicMock
import zlib

SPEC = importlib.util.spec_from_file_location('relay_image', Path(__file__).resolve().parents[1] / 'scripts' / 'relay_image.py')
relay = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(relay)
ENV = {'RELAY_IMAGE_API_KEY': 'unit-test-token-not-a-secret',
       'RELAY_IMAGE_BASE_URL': 'https://relay.example.com/v1'}


def png():
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(b'\x00\xff\x80\x00')) + chunk(b'IEND', b''))


def image_item(**updates):
    item = {'id': 'image-test', 'type': 'image_generation_call', 'status': 'completed',
            'result': base64.b64encode(png()).decode(), 'quality': 'low', 'size': '1x1'}
    item.update(updates)
    return item


def completed(items=None):
    return {'type': 'response.completed', 'response': {
        'id': 'resp-test', 'model': 'reported-driver', 'status': 'completed',
        'usage': {'input_tokens': 12, 'output_tokens': 4},
        'output': [image_item()] if items is None else items}}


class Response(io.BytesIO):
    def __init__(self, events, content_type='text/event-stream'):
        raw = b''.join(b'data: ' + json.dumps(e).encode() + b'\n\n' for e in events)
        super().__init__(raw)
        self.headers = {'Content-Type': content_type, 'x-request-id': 'request-test'}


class ConfigurationTests(unittest.TestCase):
    def test_both_variables_required(self):
        for env in ({}, {'RELAY_IMAGE_API_KEY': 'token'}, {'RELAY_IMAGE_BASE_URL': 'https://example.com'}):
            with self.assertRaises(relay.RelayError):
                relay.configuration(env)

    def test_no_implicit_openai_fallback(self):
        with self.assertRaises(relay.RelayError):
            relay.configuration({'OPENAI_API_KEY': 'other-key', 'OPENAI_BASE_URL': 'https://example.com'})

    def test_valid_base_normalized(self):
        self.assertEqual(relay.configuration(dict(ENV, RELAY_IMAGE_BASE_URL=ENV['RELAY_IMAGE_BASE_URL'] + '/')),
                         (ENV['RELAY_IMAGE_API_KEY'], ENV['RELAY_IMAGE_BASE_URL']))

    def test_unsafe_urls_rejected(self):
        for url in ('http://example.com/v1', 'https://a:b@example.com', 'https://example.com?q=key',
                    'https://example.com/#fragment', 'https://example.com/v1/responses',
                    'https://example.com/v1/images/generations', 'https://example.com:bad',
                    'https:///v1', 'https://ex ample.com', 'https://example.com/v1/models'):
            with self.subTest(url=url), self.assertRaises(relay.RelayError):
                relay.configuration(dict(ENV, RELAY_IMAGE_BASE_URL=url))

    def test_key_whitespace_rejected(self):
        with self.assertRaises(relay.RelayError):
            relay.configuration(dict(ENV, RELAY_IMAGE_API_KEY='token\nheader'))

    def test_doctor_offline_and_secret_hidden(self):
        output = io.StringIO()
        with patch.dict(os.environ, ENV, clear=True), patch.object(relay, 'call') as call, contextlib.redirect_stdout(output):
            self.assertEqual(relay.main(['doctor']), 0)
        call.assert_not_called()
        self.assertNotIn(ENV['RELAY_IMAGE_API_KEY'], output.getvalue())

    def test_missing_env_prevents_generate_network(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(relay, 'call') as call, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(relay.main(['generate', '--confirm-relay', '--prompt', 'cat', '--out', '/unused.png']), 1)
        call.assert_not_called()

    def test_redacts_nested_key_and_bearer(self):
        value = relay.scrub({'error': ['my token abc123', 'Bearer something-else']}, 'abc123')
        self.assertNotIn('abc123', str(value))
        self.assertNotIn('something-else', str(value))

    def test_redirect_is_not_followed(self):
        self.assertIsNone(relay.NoRedirect().redirect_request(None, None, 302, 'redirect', {}, 'https://elsewhere.example'))


class RoutingTests(unittest.TestCase):
    def test_saved_credentials_do_not_authorize_relay_generation(self):
        env = dict(ENV, RELAY_IMAGE_CONFIRM_RELAY='true', RELAY_IMAGE_MODE='relay')
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / 'blocked.png'
            with patch.dict(os.environ, env, clear=True), patch.object(relay, 'call') as call, \
                    contextlib.redirect_stderr(io.StringIO()) as error:
                status = relay.main(['generate', '--prompt', 'cat', '--out', str(out)])
            self.assertEqual(status, 1)
            self.assertIn('not confirmed', error.getvalue())
            call.assert_not_called()
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_dry_run_also_requires_per_request_confirmation(self):
        with patch.dict(os.environ, ENV, clear=True), patch.object(relay, 'call') as call, \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(relay.main(['generate', '--prompt', 'cat', '--out', '/unused.png', '--dry-run']), 1)
        call.assert_not_called()

    def test_png_is_default_request_format(self):
        args = relay.parser().parse_args(['generate', '--confirm-relay', '--prompt', 'cat', '--out', 'cat.png'])
        self.assertEqual(relay.build_payload(args, ENV)['tools'][0]['output_format'], 'png')


class TransportTests(unittest.TestCase):
    def test_single_https_request_has_correct_header_and_payload(self):
        opener = MagicMock()
        with patch.object(relay.urllib.request, 'build_opener', return_value=opener) as build:
            relay.call(ENV['RELAY_IMAGE_BASE_URL'], ENV['RELAY_IMAGE_API_KEY'], '/responses', {'model': 'test-driver'})
        opener.open.assert_called_once()
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, 'https://relay.example.com/v1/responses')
        self.assertEqual(request.get_header('Authorization'), 'Bearer ' + ENV['RELAY_IMAGE_API_KEY'])
        self.assertEqual(json.loads(request.data), {'model': 'test-driver'})
        self.assertIsInstance(build.call_args.args[1], relay.NoRedirect)
        self.assertEqual(build.call_args.args[0].proxies, {})

    def test_http_error_body_redacted_without_retry(self):
        error = relay.urllib.error.HTTPError('https://relay.example.com', 401, 'Unauthorized', {},
                                            io.BytesIO(ENV['RELAY_IMAGE_API_KEY'].encode()))
        opener = MagicMock()
        opener.open.side_effect = error
        with patch.object(relay.urllib.request, 'build_opener', return_value=opener):
            with self.assertRaises(relay.RelayError) as caught:
                relay.call(ENV['RELAY_IMAGE_BASE_URL'], ENV['RELAY_IMAGE_API_KEY'], '/models')
        opener.open.assert_called_once()
        self.assertIn('401', str(caught.exception))
        self.assertNotIn(ENV['RELAY_IMAGE_API_KEY'], str(caught.exception))

    def test_network_error_redacted_without_retry(self):
        opener = MagicMock()
        opener.open.side_effect = relay.urllib.error.URLError(ENV['RELAY_IMAGE_API_KEY'])
        with patch.object(relay.urllib.request, 'build_opener', return_value=opener):
            with self.assertRaises(relay.RelayError) as caught:
                relay.call(ENV['RELAY_IMAGE_BASE_URL'], ENV['RELAY_IMAGE_API_KEY'], '/models')
        opener.open.assert_called_once()
        self.assertNotIn(ENV['RELAY_IMAGE_API_KEY'], str(caught.exception))


class StreamTests(unittest.TestCase):
    def events(self, raw):
        return list(relay.sse_events(io.BytesIO(raw), time.monotonic() + 10))

    def test_comments_multiline_and_eof(self):
        self.assertEqual(self.events(b': keepalive\n\nevent: message\ndata: {"type":\ndata: "ok"}\n'), [{'type': 'ok'}])

    def test_done_terminates(self):
        self.assertEqual(self.events(b'data: [DONE]\n\ndata: invalid\n\n'), [])

    def test_invalid_json_rejected(self):
        with self.assertRaises(relay.RelayError):
            self.events(b'data: invalid\n\n')

    def test_non_object_rejected(self):
        with self.assertRaises(relay.RelayError):
            self.events(b'data: []\n\n')

    def test_event_size_limit(self):
        with patch.object(relay, 'MAX_EVENT_BYTES', 10), self.assertRaises(relay.RelayError):
            self.events(b'data: ' + b'x' * 20 + b'\n\n')

    def test_deadline(self):
        with self.assertRaises(relay.RelayError):
            list(relay.sse_events(io.BytesIO(b''), time.monotonic() - 1))

    def test_png_and_invalid_base64(self):
        self.assertEqual(relay.extract_image(image_item())[1:], (1, 1))
        for raw in ('%%%', base64.b64encode(b'not-png').decode()):
            with self.assertRaises(relay.RelayError):
                relay.extract_image(image_item(result=raw))

    def test_truncated_png_rejected(self):
        raw = base64.b64encode(png()[:-12]).decode()
        with self.assertRaises(relay.RelayError):
            relay.extract_image(image_item(result=raw))

    def test_corrupted_png_checksum_rejected(self):
        raw = bytearray(png())
        raw[29] ^= 1
        with self.assertRaises(relay.RelayError):
            relay.extract_image(image_item(result=base64.b64encode(raw).decode()))


class GenerateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.out = Path(self.temp.name) / 'cat.png'

    def run_gen(self, events, extra=(), content_type='text/event-stream'):
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, ENV, clear=True), \
                patch.object(relay, 'call', return_value=Response(events, content_type)) as call, \
                contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            status = relay.main(['generate', '--confirm-relay', '--prompt', 'cat', '--out', str(self.out)] + list(extra))
        return status, call, stdout.getvalue() + stderr.getvalue()

    def report(self):
        return json.loads(self.out.with_suffix('.json').read_text())

    def test_success_deduplicates_stream_and_terminal_image(self):
        status, call, _ = self.run_gen([{'type': 'response.output_item.done', 'item': image_item()}, completed()])
        self.assertEqual(status, 0)
        call.assert_called_once()
        self.assertEqual(self.out.read_bytes(), png())
        report = self.report()
        self.assertEqual(report['status'], 'completed')
        self.assertEqual(report['actual_size'], '1x1')
        self.assertEqual(report['driver_model_reported'], 'reported-driver')
        self.assertEqual(len(report['warnings']), 2)
        self.assertNotIn('result', report['image_tool'])
        self.assertNotIn(ENV['RELAY_IMAGE_API_KEY'], json.dumps(report))
        self.assertNotIn(ENV['RELAY_IMAGE_BASE_URL'], json.dumps(report))

    def test_cli_overrides_models(self):
        status, call, _ = self.run_gen([completed()], ['--driver-model', 'gpt-5.6-sol', '--image-model', 'custom-image'])
        self.assertEqual(status, 0)
        payload = call.call_args.args[3]
        self.assertEqual(payload['model'], 'gpt-5.6-sol')
        self.assertEqual(payload['tools'][0]['model'], 'custom-image')

    def test_environment_model_default(self):
        args = relay.parser().parse_args(['generate', '--confirm-relay', '--prompt', 'cat', '--out', 'a.png'])
        payload = relay.build_payload(args, {'RELAY_IMAGE_DRIVER_MODEL': 'driver-env', 'RELAY_IMAGE_MODEL': 'image-env'})
        self.assertEqual(payload['model'], 'driver-env')
        self.assertEqual(payload['tools'][0]['model'], 'image-env')

    def test_prompt_file(self):
        prompt = Path(self.temp.name) / 'prompt.txt'
        prompt.write_text('猫\n自然光', encoding='utf-8')
        args = relay.parser().parse_args(['generate', '--prompt-file', str(prompt), '--out', 'a.png'])
        self.assertEqual(relay.build_payload(args)['input'][0]['content'][0]['text'], '猫\n自然光')

    def test_missing_completed_is_failure_and_no_png(self):
        status, call, _ = self.run_gen([{'type': 'response.output_item.done', 'item': image_item()}])
        self.assertEqual(status, 1)
        call.assert_called_once()
        self.assertFalse(self.out.exists())
        self.assertEqual(self.report()['status'], 'failed')

    def test_no_image_is_failure(self):
        status, _, _ = self.run_gen([completed([])])
        self.assertEqual(status, 1)
        self.assertFalse(self.out.exists())

    def test_extra_image_is_failure(self):
        status, _, _ = self.run_gen([completed([image_item(), image_item(id='second')])])
        self.assertEqual(status, 1)
        self.assertFalse(self.out.exists())

    def test_refused_or_failed_stream_no_retry(self):
        status, call, text = self.run_gen([{'type': 'response.failed', 'response': {
            'error': {'message': 'rejected ' + ENV['RELAY_IMAGE_API_KEY']}}}])
        self.assertEqual(status, 1)
        call.assert_called_once()
        self.assertNotIn(ENV['RELAY_IMAGE_API_KEY'], text + json.dumps(self.report()))

    def test_network_failure_records_no_key(self):
        with patch.dict(os.environ, ENV, clear=True), patch.object(relay, 'call', side_effect=relay.RelayError(ENV['RELAY_IMAGE_API_KEY'])) as call, contextlib.redirect_stderr(io.StringIO()):
            status = relay.main(['generate', '--confirm-relay', '--prompt', 'cat', '--out', str(self.out)])
        self.assertEqual(status, 1)
        call.assert_called_once()
        self.assertNotIn(ENV['RELAY_IMAGE_API_KEY'], json.dumps(self.report()))

    def test_wrong_content_type_rejected(self):
        status, _, _ = self.run_gen([completed()], content_type='application/json')
        self.assertEqual(status, 1)
        self.assertFalse(self.out.exists())

    def test_existing_image_prevents_request(self):
        self.out.write_bytes(b'old')
        status, call, _ = self.run_gen([completed()])
        self.assertEqual(status, 1)
        call.assert_not_called()
        self.assertEqual(self.out.read_bytes(), b'old')

    def test_existing_metadata_prevents_request(self):
        self.out.with_suffix('.json').write_text('old')
        status, call, _ = self.run_gen([completed()])
        self.assertEqual(status, 1)
        call.assert_not_called()

    def test_dry_run_no_network_or_output(self):
        status, call, text = self.run_gen([], ['--dry-run'])
        self.assertEqual(status, 0)
        call.assert_not_called()
        self.assertFalse(self.out.exists())
        self.assertFalse(self.out.with_suffix('.json').exists())
        self.assertNotIn(ENV['RELAY_IMAGE_API_KEY'], text)

    def test_negative_timeout_prevents_request(self):
        status, call, _ = self.run_gen([], ['--timeout', '-1'])
        self.assertEqual(status, 1)
        call.assert_not_called()

    def test_wrong_extension_prevents_request(self):
        self.out = self.out.with_suffix('.jpg')
        status, call, _ = self.run_gen([])
        self.assertEqual(status, 1)
        call.assert_not_called()

    def test_nonfinite_timeout_prevents_request(self):
        for value in ('nan', 'inf'):
            status, call, _ = self.run_gen([], ['--timeout', value])
            self.assertEqual(status, 1)
            call.assert_not_called()

    def test_http_read_failure_is_reported_without_retry(self):
        with patch.dict(os.environ, ENV, clear=True), patch.object(relay, 'call', side_effect=relay.http.client.IncompleteRead(b'', 10)) as call, contextlib.redirect_stderr(io.StringIO()):
            status = relay.main(['generate', '--confirm-relay', '--prompt', 'cat', '--out', str(self.out)])
        self.assertEqual(status, 1)
        call.assert_called_once()
        self.assertFalse(self.out.exists())
        self.assertEqual(self.report()['status'], 'failed')


if __name__ == '__main__':
    unittest.main()
