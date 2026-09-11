#!/usr/bin/env python3
"""Environment-gated, no-retry Responses image client. Python 3.9+, stdlib only."""
import argparse
import base64
import binascii
import json
import http.client
import math
import os
from pathlib import Path
import re
import struct
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib

MAX_EVENT_BYTES = 64 * 1024 * 1024
MAX_IMAGE_BYTES = 32 * 1024 * 1024


class RelayError(Exception):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward a credential to a redirect target.


def scrub(value, secret):
    if isinstance(value, str):
        value = value.replace(secret, '[REDACTED]') if secret else value
        return re.sub(r'(?i)Bearer\s+[^\s"\x27]+', 'Bearer [REDACTED]', value)
    if isinstance(value, dict):
        return {k: scrub(v, secret) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub(v, secret) for v in value]
    return value


def configuration(env=None):
    env = os.environ if env is None else env
    missing = [k for k in ('RELAY_IMAGE_API_KEY', 'RELAY_IMAGE_BASE_URL')
               if not env.get(k, '').strip()]
    if missing:
        raise RelayError('Missing terminal environment variables: ' + ', '.join(missing)
                         + '. Run: source scripts/configure.sh (see README).')
    key = env['RELAY_IMAGE_API_KEY'].strip()
    if any(c.isspace() for c in key) or not key.isascii():
        raise RelayError('API key must be an ASCII token without whitespace.')
    url = env['RELAY_IMAGE_BASE_URL'].strip().rstrip('/')
    try:
        p = urllib.parse.urlsplit(url)
        _ = p.port
    except ValueError:
        raise RelayError('Invalid RELAY_IMAGE_BASE_URL.') from None
    if (p.scheme != 'https' or not p.hostname or p.username is not None
            or p.password is not None or p.query or p.fragment
            or any(c.isspace() for c in url) or not url.isascii()):
        raise RelayError('BASE_URL must be an HTTPS API base, without credentials, query or fragment.')
    if p.path.endswith(('/responses', '/images/generations', '/models')):
        raise RelayError('Use the API base URL, not a /responses, /models or /images/generations endpoint.')
    return key, url


def call(base, key, path, payload=None, timeout=180):
    headers = {'Authorization': 'Bearer ' + key,
               'Accept': 'text/event-stream' if payload else 'application/json',
               'User-Agent': 'relay-image-configurator/1.0'}
    data = None
    if payload is not None:
        data = json.dumps(payload).encode('utf-8')
        headers['Content-Type'] = 'application/json'
    request = urllib.request.Request(base + path, data=data, headers=headers)
    # Ignore implicit proxy environment settings. HTTPS validation remains enabled.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        return opener.open(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        request_id = exc.headers.get('x-request-id', '')
        detail = exc.read(8192).decode('utf-8', errors='replace')
        raise RelayError(scrub('HTTP {} (request_id={}): {}'.format(
            exc.code, request_id, detail), key)) from None
    except (urllib.error.URLError, OSError) as exc:
        raise RelayError(scrub('Network/TLS error: ' + str(exc), key)) from None


def sse_events(response, deadline):
    """Read SSE data fields, including multi-line data and events without a final blank line."""
    parts = []
    size = 0
    while True:
        if time.monotonic() > deadline:
            raise RelayError('Total response time limit exceeded. No automatic retry.')
        line = response.readline(MAX_EVENT_BYTES + 1)
        if len(line) > MAX_EVENT_BYTES:
            raise RelayError('SSE line exceeds safety limit.')
        if not line or line in (b'\n', b'\r\n'):
            if parts:
                data = b'\n'.join(parts)
                if data.strip() == b'[DONE]':
                    return
                try:
                    event = json.loads(data)
                except (ValueError, UnicodeDecodeError):
                    raise RelayError('Malformed SSE JSON from relay.') from None
                if not isinstance(event, dict):
                    raise RelayError('Expected an SSE JSON object.')
                yield event
                parts, size = [], 0
            if not line:
                return
        elif line.startswith(b'data:'):
            part = line[5:].lstrip(b' ').rstrip(b'\r\n')
            size += len(part)
            if size > MAX_EVENT_BYTES:
                raise RelayError('SSE event exceeds safety limit.')
            parts.append(part)


def build_payload(args, env=None):
    env = os.environ if env is None else env
    prompt = args.prompt
    if args.prompt_file:
        prompt = Path(args.prompt_file).read_text(encoding='utf-8')
    if not prompt or not prompt.strip():
        raise RelayError('Prompt cannot be empty.')
    return {
        'model': args.driver_model or env.get('RELAY_IMAGE_DRIVER_MODEL') or 'gpt-5.5',
        'instructions': 'Use the image generation tool to fulfill the user request. Generate exactly one image.',
        'input': [{'role': 'user', 'content': [{'type': 'input_text', 'text': prompt}]}],
        'tools': [{'type': 'image_generation',
                   'model': args.image_model or env.get('RELAY_IMAGE_MODEL') or 'gpt-image-2',
                   'size': args.size, 'quality': args.quality, 'output_format': 'png'}],
        'tool_choice': {'type': 'image_generation'}, 'store': False, 'stream': True,
    }


def extract_image(item):
    raw = item.get('result')
    if not isinstance(raw, str) or not raw:
        return None
    if len(raw) > (MAX_IMAGE_BYTES * 4 // 3 + 4):
        raise RelayError('Image exceeds safety limit.')
    try:
        data = base64.b64decode(raw, validate=True)
    except (ValueError, binascii.Error):
        raise RelayError('Relay returned invalid image base64.') from None
    if (len(data) < 33 or data[:8] != b'\x89PNG\r\n\x1a\n'
            or data[12:16] != b'IHDR' or data[8:12] != b'\x00\x00\x00\r'):
        raise RelayError('Expected a PNG with a valid IHDR header.')
    width, height = struct.unpack('>II', data[16:24])
    if width == 0 or height == 0:
        raise RelayError('Invalid PNG dimensions.')
    offset = 8
    has_data = False
    ended = False
    while offset + 12 <= len(data):
        length = struct.unpack('>I', data[offset:offset + 4])[0]
        end = offset + 12 + length
        if end > len(data):
            raise RelayError('Truncated PNG chunk.')
        kind = data[offset + 4:offset + 8]
        chunk = data[offset + 4:end - 4]
        expected_crc = struct.unpack('>I', data[end - 4:end])[0]
        if (zlib.crc32(chunk) & 0xffffffff) != expected_crc:
            raise RelayError('PNG chunk checksum mismatch.')
        has_data = has_data or kind == b'IDAT'
        offset = end
        if kind == b'IEND':
            ended = length == 0 and end == len(data)
            break
    if not has_data or not ended:
        raise RelayError('Incomplete PNG: missing image data or final IEND chunk.')
    return data, width, height


def generate(args, key, base):
    payload = build_payload(args)
    if args.dry_run:
        print(json.dumps({'endpoint': '/responses', 'request': scrub(payload, key),
                          'network_request_sent': False}, ensure_ascii=False, indent=2))
        return
    output = Path(args.out).expanduser().absolute()
    if output.suffix.lower() != '.png':
        raise RelayError('--out must end with .png.')
    meta_path = output.with_suffix('.json')
    if output.exists() or meta_path.exists():
        raise RelayError('Output or metadata already exists; choose a new --out. No request sent.')
    output.parent.mkdir(parents=True, exist_ok=True)
    # Reserve the destination BEFORE a billable request; fail before paying on races/permissions.
    image_file = output.open('xb')
    try:
        meta_file = meta_path.open('x', encoding='utf-8')
    except Exception:
        image_file.close()
        output.unlink()
        raise
    report = {'endpoint': '/responses', 'driver_model_requested': payload['model'],
              'image_model_requested': payload['tools'][0]['model'],
              'size_requested': args.size, 'quality_requested': args.quality,
              'prompt': payload['input'][0]['content'][0]['text'],
              'status': 'failed', 'images': [], 'max_retries': 0, 'warnings': []}
    started = time.monotonic()
    candidates = {}
    failure = None
    try:
        with call(base, key, '/responses', payload, args.timeout) as response:
            report['request_id'] = response.headers.get('x-request-id')
            if 'text/event-stream' not in response.headers.get('Content-Type', '').lower():
                raise RelayError('Expected an SSE response; relay did not return text/event-stream.')
            completed = False
            for event in sse_events(response, started + args.max_seconds):
                kind = event.get('type')
                if kind == 'response.output_item.done':
                    item = event.get('item', {})
                    if item.get('type') == 'image_generation_call' and item.get('result'):
                        candidates[item.get('id', 'image')] = item
                elif kind in ('response.failed', 'response.incomplete', 'error'):
                    error = event.get('response', {}).get('error') or event.get('error') or event.get('message') or kind
                    raise RelayError('Relay did not complete: ' + json.dumps(scrub(error, key), ensure_ascii=False))
                elif kind == 'response.completed':
                    result = event.get('response', {})
                    if result.get('status') != 'completed':
                        raise RelayError('Unexpected terminal response status.')
                    report['response_id'] = result.get('id')
                    report['driver_model_reported'] = result.get('model')
                    report['usage'] = result.get('usage')
                    for item in result.get('output', []):
                        if item.get('type') == 'image_generation_call' and item.get('result'):
                            candidates[item.get('id', 'image')] = item
                    completed = True
                    break  # Some proxies keep SSE connections alive after the terminal event.
            if not completed:
                raise RelayError('Stream ended without response.completed; no automatic retry.')
        if len(candidates) != 1:
            raise RelayError('Expected exactly one image, received {}.'.format(len(candidates)))
        item = next(iter(candidates.values()))
        extracted = extract_image(item)
        if extracted is None:
            raise RelayError('Completed response contains no image bytes.')
        data, width, height = extracted
        image_file.write(data)
        report['images'] = [str(output)]
        report['actual_size'] = '{}x{}'.format(width, height)
        allowed = ('id', 'status', 'action', 'model', 'size', 'quality', 'background',
                   'output_format', 'revised_prompt', 'usage')
        report['image_tool'] = {k: item[k] for k in allowed if k in item}
        if args.size != 'auto' and report['actual_size'] != args.size:
            report['warnings'].append('Actual PNG size differs from requested size.')
        if args.quality != 'auto' and item.get('quality') not in (None, args.quality):
            report['warnings'].append('Returned quality differs from requested quality.')
        report['status'] = 'completed'
    except (RelayError, ValueError, OSError, http.client.HTTPException, KeyboardInterrupt) as exc:
        failure = scrub(str(exc) or type(exc).__name__, key)
        report['error'] = failure
    finally:
        image_file.close()
        report['elapsed_seconds'] = round(time.monotonic() - started, 2)
        with meta_file:
            json.dump(scrub(report, key), meta_file, ensure_ascii=False, indent=2)
            meta_file.write('\n')
        if report['status'] != 'completed':
            output.unlink(missing_ok=True)
    if failure:
        raise RelayError(failure + '\nDiagnostic report: ' + str(meta_path))
    print(json.dumps(scrub(report, key), ensure_ascii=False, indent=2))
    print('Metadata: ' + str(meta_path))


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    doctor = sub.add_parser('doctor', help='Validate local environment; no network by default')
    doctor.add_argument('--list-models', action='store_true', help='Explicitly GET /models; never generate an image')
    gen = sub.add_parser('generate', help='Send one potentially billable Responses image request, without retries')
    prompt = gen.add_mutually_exclusive_group(required=True)
    prompt.add_argument('--prompt')
    prompt.add_argument('--prompt-file')
    gen.add_argument('--out', required=True)
    gen.add_argument('--driver-model')
    gen.add_argument('--image-model')
    gen.add_argument('--size', default='1024x1024')
    gen.add_argument('--quality', choices=['low', 'medium', 'high', 'auto'], default='medium')
    gen.add_argument('--timeout', type=float, default=180, help='Socket I/O timeout in seconds')
    gen.add_argument('--max-seconds', type=float, default=300, help='Stream elapsed-time budget (checked between reads)')
    gen.add_argument('--dry-run', action='store_true', help='Print a credential-free request; no network')
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    secret = os.environ.get('RELAY_IMAGE_API_KEY', '').strip()
    try:
        key, base = configuration()
        if args.command == 'doctor':
            print('Environment OK: API key is set (hidden); HTTPS API base is valid.')
            print('Offline checks do NOT prove authentication, model access, generation support or billing.')
            if args.list_models:
                with call(base, key, '/models', timeout=30) as response:
                    raw = response.read(2 * 1024 * 1024 + 1)
                    if len(raw) > 2 * 1024 * 1024:
                        raise RelayError('Model list exceeds safety limit.')
                    data = json.loads(raw)
                print(json.dumps(scrub({'models': [m.get('id') for m in data.get('data', [])]}, key), indent=2))
                print('Listed models may still be unavailable to this upstream account.')
        else:
            if not all(math.isfinite(v) and v > 0 for v in (args.timeout, args.max_seconds)):
                raise RelayError('Timeout values must be finite and positive.')
            if args.size != 'auto' and not re.fullmatch(r'[1-9]\d{1,4}x[1-9]\d{1,4}', args.size):
                raise RelayError('Size must be auto or WIDTHxHEIGHT; see relay/model limits.')
            generate(args, key, base)
    except (RelayError, OSError, ValueError) as exc:
        print('Error: ' + scrub(str(exc), secret), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
