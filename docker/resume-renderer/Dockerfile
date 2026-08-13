FROM debian:12.11-slim

RUN apt-get update \
    && apt-get install --yes --no-install-recommends libreoffice-writer poppler-utils \
    && rm -rf /var/lib/apt/lists/*

COPY render-docx.sh /usr/local/bin/render-docx
RUN chmod 0555 /usr/local/bin/render-docx

ENTRYPOINT ["/usr/local/bin/render-docx"]
