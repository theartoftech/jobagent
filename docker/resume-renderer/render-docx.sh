#!/bin/sh
set -eu

if [ "$#" -ne 1 ]; then
  echo "expected exactly one DOCX filename" >&2
  exit 64
fi

document_name=$1
document_path=/input/$document_name
if [ ! -f "$document_path" ]; then
  echo "DOCX input is missing: $document_path" >&2
  exit 66
fi

profile_dir=/tmp/libreoffice-profile
mkdir -p "$profile_dir" /output
soffice --headless "-env:UserInstallation=file://$profile_dir" --convert-to pdf:writer_pdf_Export --outdir /output "$document_path"
pdf_path=/output/${document_name%.docx}.pdf
if [ ! -s "$pdf_path" ]; then
  echo "LibreOffice did not create a PDF: $pdf_path" >&2
  exit 65
fi

pdftoppm -png -r 144 "$pdf_path" /output/page
