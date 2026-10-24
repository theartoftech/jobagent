from __future__ import annotations

import os
import zipfile
from pathlib import Path

from lxml import etree as LET


WORD_NAMESPACE = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
WORD_SETTINGS_PATH = "word/settings.xml"
MODERN_WORD_COMPATIBILITY_MODE = "15"
WORD_COMPATIBILITY_URI = "http://schemas.microsoft.com/office/word"
WORD_NAMESPACES = {"w": WORD_NAMESPACE}


def modernize_word_settings_xml(settings_xml: bytes) -> bytes:
    root = LET.fromstring(
        settings_xml,
        parser=LET.XMLParser(remove_blank_text=False, resolve_entities=False),
    )
    settings = root.xpath(
        ".//w:compatSetting[@w:name='compatibilityMode']",
        namespaces=WORD_NAMESPACES,
    )
    if len(settings) > 1:
        raise ValueError("Word settings contain multiple compatibilityMode declarations")
    if settings:
        compatibility = settings[0]
    else:
        compat = root.find(f"{{{WORD_NAMESPACE}}}compat")
        if compat is None:
            compat = LET.SubElement(root, f"{{{WORD_NAMESPACE}}}compat")
        compatibility = LET.SubElement(compat, f"{{{WORD_NAMESPACE}}}compatSetting")
        compatibility.set(f"{{{WORD_NAMESPACE}}}name", "compatibilityMode")
        compatibility.set(f"{{{WORD_NAMESPACE}}}uri", WORD_COMPATIBILITY_URI)
    compatibility.set(f"{{{WORD_NAMESPACE}}}val", MODERN_WORD_COMPATIBILITY_MODE)
    return LET.tostring(root, encoding="UTF-8", xml_declaration=True, standalone=True)


def require_modern_word_compatibility(settings_xml: bytes, *, document_path: Path) -> None:
    root = LET.fromstring(
        settings_xml,
        parser=LET.XMLParser(remove_blank_text=False, resolve_entities=False),
    )
    settings = root.xpath(
        ".//w:compatSetting[@w:name='compatibilityMode']",
        namespaces=WORD_NAMESPACES,
    )
    if len(settings) != 1:
        raise ValueError(
            f"DOCX must declare exactly one Word compatibility mode: {document_path}"
        )
    mode = settings[0].get(f"{{{WORD_NAMESPACE}}}val", "")
    if mode != MODERN_WORD_COMPATIBILITY_MODE:
        raise ValueError(
            f"DOCX must use Word compatibility level 15, found {mode or 'missing'}: "
            f"{document_path}"
        )


def modernize_docx_compatibility(path: Path) -> None:
    if not path.is_file() or not zipfile.is_zipfile(path):
        raise ValueError(f"Cannot modernize invalid DOCX package: {path}")
    temporary_path = path.with_name(f".{path.name}.modernizing")
    if temporary_path.exists():
        raise FileExistsError(f"DOCX modernization staging path already exists: {temporary_path}")
    try:
        with zipfile.ZipFile(path, "r") as source:
            if WORD_SETTINGS_PATH not in source.namelist():
                raise ValueError(f"DOCX package is missing {WORD_SETTINGS_PATH}: {path}")
            with zipfile.ZipFile(temporary_path, "w") as destination:
                for entry in source.infolist():
                    content = source.read(entry.filename)
                    if entry.filename == WORD_SETTINGS_PATH:
                        content = modernize_word_settings_xml(content)
                    destination.writestr(entry, content)
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)
