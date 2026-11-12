FROM harveyff/markitdown-mcp:0.1.9-olares.2@sha256:1dd18a75d15be7e87382ba674a6730abfee2859fca7cbb648913da0f7a2c9d80
COPY integration /opt/olares-mcp
ENV MARKITDOWN_FILE_ROOT=/workdir ROUTER_OCR_MODEL=default-ocr OCR_TIMEOUT_SECONDS=180
ENTRYPOINT ["python", "/opt/olares-mcp/server.py"]
CMD ["--http", "--host", "0.0.0.0", "--port", "3001"]
