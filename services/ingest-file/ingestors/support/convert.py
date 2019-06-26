import logging
import requests
from pathlib import Path
from pantomime.types import DEFAULT
from requests import RequestException
from servicelayer.util import backoff, service_retries

from ingestors.settings import UNOSERVICE_URL
from ingestors.support.cache import CacheSupport
from ingestors.support.temp import TempFileSupport
from ingestors.exc import ProcessingException

log = logging.getLogger(__name__)


class DocumentConvertSupport(CacheSupport, TempFileSupport):
    """Provides helpers for UNO document conversion via HTTP."""

    def document_to_pdf(self, file_path, entity):
        key = self.cache_key('pdf', entity.first('contentHash'))
        pdf_hash = self.get_cache_value(key)
        if pdf_hash is not None:
            log.info("Using [%s] PDF from cache", entity.first('fileName'))
            entity.set('pdfHash', pdf_hash)
            work_path = self.manager.work_path
            path = self.manager.archive.load_file(pdf_hash,
                                                  temp_path=work_path)
            if path is not None:
                return Path(path).resolve()

        pdf_file = self._document_to_pdf(file_path, entity)
        if pdf_file is not None:
            content_hash = self.manager.archive.archive_file(pdf_file)
            entity.set('pdfHash', content_hash)
            self.set_cache_value(key, content_hash)
            return Path(pdf_file).resolve()

    def _document_to_pdf(self, file_path, entity):
        """Converts an office document to PDF."""
        if UNOSERVICE_URL is None:
            raise RuntimeError("No UNOSERVICE_URL for document conversion.")
        log.info('Converting [%s] to PDF...', entity.first('fileName'))
        file_name = entity.first('fileName') or 'data'
        mime_type = entity.first('mimeType') or DEFAULT
        attempt = 1
        for attempt in service_retries():
            fh = open(file_path, 'rb')
            try:
                files = {'file': (file_name, fh, mime_type)}
                res = requests.post(UNOSERVICE_URL,
                                    files=files,
                                    timeout=(5, 305),
                                    stream=True)
                if res.status_code > 399:
                    raise ProcessingException(res.text)
                out_path = self.make_work_file('out.pdf')
                with open(out_path, 'wb') as fh:
                    bytes_written = 0
                    for chunk in res.iter_content(chunk_size=None):
                        bytes_written += len(chunk)
                        fh.write(chunk)
                    if bytes_written > 50:
                        return out_path
            except RequestException as exc:
                log.error("Conversion failed: %s", exc)
                backoff(failures=attempt)
            finally:
                fh.close()
        raise ProcessingException("Document could not be converted to PDF.")
