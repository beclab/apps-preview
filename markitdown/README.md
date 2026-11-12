# MarkItDown MCP for Olares

This preview chart runs MarkItDown 0.1.9 with a modified MCP service and optional image/scan text recognition through Olares Router. The app has no web UI. It requires Olares 1.12.7 or newer and currently supports linux/amd64 only.

## Connect a client

Use the app entrance URL with `/mcp` for Streamable HTTP. Legacy SSE is available at `/sse`. The entrance is internal and hidden from the desktop. Connect a trusted MCP client such as Lares; the service is not designed for anonymous public exposure.

Tool: `convert_to_markdown(uri)`.

Place files in Olares Files `Documents/MarkItDown/`; the service mounts this folder read-only as `/workdir`. Example arguments:

```json
{"uri":"file:///workdir/example.pdf"}
```

The tool also accepts data, HTTP and HTTPS URIs. It returns Markdown to the client and does not save a file automatically. Only trusted callers should use it: remote URLs are fetched with the application's network reachability.

## Images and scans

Configure a ready OCR model as Router's `default-ocr` before converting images or image-only PDF pages. The model is installed separately. The tested model was the NVIDIA PaddleOCR Hybrid application, callable as `Olares/paddle-hybrid`.

PDF pages without extractable alphanumeric text use OCR. Pages with a header or page number may not trigger OCR even if the body is scanned. Native pages and OCR pages are labeled in mixed results. Office embedded images and archive members do not receive the extra OCR pass. Model errors and empty results are reported as errors; successful OCR still does not guarantee completeness.

Limits: 32 MiB input, 100 PDF pages inspected, 20 OCR pages per request, 180-second nominal OCR polling deadline. Multi-frame images must be split. Encrypted PDFs must be decrypted first.

## Validation

Verified on Olares 1.12.7 with an amd64 node on 2026-11-12:

- 11 adapter checks passed, covering routing, page mapping, path restrictions, failed/empty OCR and timeout cancellation.
- 10 live MCP checks passed: image, scan-only PDF, mixed PDF, DOCX, XLSX, PPTX, text PDF, encrypted PDF, malformed DOCX and two-page Chinese scan.
- Lares called the tool on a mixed PDF: page 1 used native extraction; page 2 returned the expected image code and quarterly values with an explicit completeness warning.
- Complex Chinese scans can omit table/body regions. Native PDF columns may interleave. Passing these checks does not establish lossless conversion.

## Image and source

Image: `harveyff/markitdown-mcp:0.1.9-olares.6`

Digest: `sha256:d21fbe9c48b7349214bc7519f5fe94b05419b9d45cf8dfd0132d6cb81f25e05c`

The integration source is included in `image/integration/`. `image/Dockerfile` extends the pinned base image with these files. Build from this chart directory with:

```sh
docker build --platform linux/amd64 -t <your-image> image
```

The base uses upstream commit `ed178952a6672abdfd35b5f9e76a80b6cefc086e`; the upstream MCP package is 0.0.1a8, with MCP SDK 2.3.0. Upstream license: `image/LICENSE` (MIT). Olares mesh supplies the application identity to Router; no user API key is included.

The icon is an independently designed mark for this Olares port, not an official Microsoft logo. Promotional images are conceptual product diagrams, not application screenshots. All listing copy and artwork are English.
