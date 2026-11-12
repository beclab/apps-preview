"""Local-document conversion with bounded, explicit Router OCR fallback."""
import io
import mimetypes
import os
import re
import time
import zipfile
from pathlib import Path
from urllib.parse import urlparse, unquote, quote

import requests
from markitdown import MarkItDown, StreamInfo
from markitdown._uri_utils import parse_data_uri, file_uri_to_path
from pdfminer.high_level import extract_pages
from pdfminer.layout import LTTextContainer
from pdfminer.pdfdocument import PDFPasswordIncorrect
from PIL import Image


class ConversionError(Exception):
    """An actionable error safe to expose through MCP."""


MAX_BYTES = 32 * 1024 * 1024
MAX_PDF_PAGES = 100
MAX_OCR_PAGES = 20
OCR_NOTICE = (
    '> Conversion: Router OCR fallback (model: {model}; reason: {reason}).\n'
    '> WARNING: OCR can omit text, tables, or layout regions even when the job succeeds. '
    'This result has NOT been verified as complete; check important numbers against the source.\n\n'
)


def has_text(text):
    return any(c.isalnum() for c in text)


def load_resource(uri):
    """Read once, preserving URL metadata and bounding OCR upload size."""
    uri = uri.strip()
    parsed = urlparse(uri)
    filename = Path(unquote(parsed.path)).name or 'document'
    mime = mimetypes.guess_type(filename)[0]
    charset = None
    source_url = None
    if parsed.scheme == 'file':
        host, name = file_uri_to_path(uri)
        if host not in (None, '', 'localhost'):
            raise ConversionError('File URI host must be empty or localhost.')
        path = Path(name).resolve()
        root = Path(os.getenv('MARKITDOWN_FILE_ROOT', '/workdir')).resolve()
        if not path.is_relative_to(root):
            raise ConversionError('Local files must be inside the configured documents directory (/workdir).')
        with path.open('rb') as f:
            data = f.read(MAX_BYTES + 1)
    elif parsed.scheme == 'data':
        if len(uri) > MAX_BYTES * 4 + 1024:
            raise ConversionError('Resource exceeds the 32 MiB input limit.')
        mime, attributes, data = parse_data_uri(uri)
        charset = attributes.get('charset')
        filename = 'document' + (mimetypes.guess_extension(mime or '') or '')
    elif parsed.scheme in ('http', 'https'):
        chunks = []
        size = 0
        with requests.get(uri, stream=True, timeout=(10, 30)) as response:
            response.raise_for_status()
            mime = response.headers.get('Content-Type', '').split(';')[0] or mime
            source_url = response.url
            filename = Path(unquote(urlparse(source_url).path)).name or filename
            for chunk in response.iter_content(65536):
                size += len(chunk)
                if size > MAX_BYTES:
                    raise ConversionError('Resource exceeds the 32 MiB input limit.')
                chunks.append(chunk)
        data = b''.join(chunks)
    else:
        raise ConversionError('Supported URI schemes: file:, data:, http:, https:.')
    if len(data) > MAX_BYTES:
        raise ConversionError('Resource exceeds the 32 MiB input limit.')
    if not data:
        raise ConversionError('The input file is empty.')
    return data, StreamInfo(filename=filename, extension=Path(filename).suffix or None,
                            mimetype=mime, charset=charset, url=source_url)


class RouterOCR:
    def __init__(self):
        self.base = os.getenv('ROUTER_BASE_URL', '').rstrip('/')
        self.model = os.getenv('ROUTER_OCR_MODEL', 'default-ocr')
        self.timeout = float(os.getenv('OCR_TIMEOUT_SECONDS', '180'))
        self.session = requests.Session()
        # In Olares the mesh supplies the app identity; no user key is embedded.

    def _request(self, method, path, **kwargs):
        try:
            response = self.session.request(method, self.base + path,
                                            timeout=(10, 30), allow_redirects=False, **kwargs)
        except requests.RequestException as exc:
            raise ConversionError('Router OCR is unreachable or timed out. Check Router and its OCR model.') from exc
        if not 200 <= response.status_code < 300:
            raise ConversionError(f'Router OCR returned HTTP {response.status_code}. Check OCR readiness and app access.')
        try:
            return response.json()
        except ValueError as exc:
            raise ConversionError('Router returned a non-JSON OCR response.') from exc

    def convert(self, data, filename, mime, pages=None):
        if not self.base:
            raise ConversionError('OCR is required, but ROUTER_BASE_URL is not configured.')
        fields = {'model': self.model, 'format': 'text', 'pdf_strategy': 'vision'}
        if pages:
            fields['pages'] = ','.join(str(n) for n in pages)
        deadline = time.monotonic() + self.timeout
        task_id = None
        try:
            task = self._request('POST', '/ocr', data=fields,
                                 files={'file': (filename, data, mime)})
            task_id = task.get('id') or task.get('job_id')
            if not task_id or not re.fullmatch(r'[A-Za-z0-9_-]+', task_id):
                raise ConversionError('Router returned no valid OCR task identifier.')
            while task.get('status') not in ('succeeded', 'failed', 'cancelled', 'canceled'):
                if time.monotonic() >= deadline:
                    raise ConversionError(f'OCR timed out after {self.timeout:g} seconds; conversion is incomplete.')
                time.sleep(0.5)
                task = self._request('GET', '/ocr/tasks/' + quote(task_id, safe=''), params={'model': self.model})
            if task.get('status') != 'succeeded':
                raise ConversionError('The Router OCR task failed or was cancelled; no complete conversion is available.')
            result = task.get('result')
            if result is None:
                result = self._request('GET', '/ocr/tasks/' + quote(task_id, safe='') + '/result', params={'model': self.model})
            if not isinstance(result, (str, dict)):
                raise ConversionError('Router OCR returned an unsupported result format.')
            return result
        except Exception:
            if task_id:
                try:
                    self.session.delete(self.base + '/ocr/tasks/' + quote(task_id, safe=''), params={'model': self.model}, timeout=5)
                except requests.RequestException:
                    pass
            raise
        finally:
            self.session.close()


def _page_text(layout):
    if isinstance(layout, LTTextContainer):
        return layout.get_text()
    if hasattr(layout, '__iter__'):
        return ''.join(_page_text(item) for item in layout)
    return ''


def convert(uri, ocr_factory=RouterOCR):
    data, info = load_resource(uri)
    converter = MarkItDown(enable_plugins=False)
    pdf = data.lstrip().startswith(b'%PDF-') or info.extension == '.pdf' or info.mimetype == 'application/pdf'
    if pdf:
        pages = []
        try:
            for layout in extract_pages(io.BytesIO(data)):
                pages.append(_page_text(layout).strip())
                if len(pages) > MAX_PDF_PAGES:
                    raise ConversionError('PDF exceeds the 100-page inspection limit. Split it into smaller documents.')
        except PDFPasswordIncorrect as exc:
            raise ConversionError('PDF is password-protected. Decrypt it before conversion; OCR was not attempted.') from exc
        except ConversionError:
            raise
        except Exception as exc:
            raise ConversionError('PDF could not be parsed. It may be encrypted or damaged; OCR was not attempted.') from exc
        if not pages:
            raise ConversionError('PDF contains no readable pages.')
        missing = [n for n, text in enumerate(pages, 1) if not has_text(text)]
        if not missing:
            return converter.convert_stream(io.BytesIO(data), stream_info=info).markdown
        if len(missing) > MAX_OCR_PAGES:
            raise ConversionError('More than 20 PDF pages need OCR. Split the PDF; no pages were silently omitted.')
        ocr = ocr_factory()
        result = ocr.convert(data, 'document.pdf', 'application/pdf', pages=missing)
        if not isinstance(result, dict):
            raise ConversionError('OCR did not return page-numbered PDF results; conversion is incomplete.')
        failed = []
        for number in missing:
            text = result.get(str(number))
            if not isinstance(text, str) or not has_text(text) or text.lstrip().lower().startswith(('error:', '[error', 'ocr error')):
                failed.append(number)
            else:
                pages[number - 1] = text.strip()
        if failed:
            raise ConversionError('OCR returned empty or failed pages: ' + ', '.join(map(str, failed)) + '. Conversion is incomplete.')
        notice = OCR_NOTICE.format(model=ocr.model, reason='PDF pages without extractable text: ' + ', '.join(map(str, missing)))
        return notice + '\n\n'.join(f'## Page {n} ({"OCR" if n in missing else "native text"})\n\n{text}' for n, text in enumerate(pages, 1))

    is_image = bool(info.mimetype and info.mimetype.startswith('image/'))
    try:
        with Image.open(io.BytesIO(data)) as image:
            is_image = True
            mime = Image.MIME.get(image.format, info.mimetype or 'application/octet-stream')
            if getattr(image, 'n_frames', 1) > 1:
                raise ConversionError('Multi-frame images must be split into individual pages before OCR.')
            image.verify()
    except ConversionError:
        raise
    except Exception as exc:
        if is_image:
            raise ConversionError('Image is damaged or unsupported; OCR was not attempted.') from exc
    if is_image:
        ocr = ocr_factory()
        result = ocr.convert(data, info.filename or 'image', mime)
        if not isinstance(result, str) or not has_text(result):
            raise ConversionError('OCR returned no text for the image. Conversion is incomplete.')
        return OCR_NOTICE.format(model=ocr.model, reason='image requires visual text recognition') + result.strip()
    if info.extension in ('.docx', '.xlsx', '.pptx') and not zipfile.is_zipfile(io.BytesIO(data)):
        raise ConversionError('Office document is not a valid ZIP package. It may be damaged; raw bytes were not returned as a successful conversion.')
    result = converter.convert_stream(io.BytesIO(data), stream_info=info).markdown
    if not has_text(result):
        raise ConversionError('No extractable text was found. Automatic OCR currently supports images and PDF pages only.')
    return result
