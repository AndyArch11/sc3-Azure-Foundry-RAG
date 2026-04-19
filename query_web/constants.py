"""
Application-wide constants for query_web.

These are upload-policy data and version identifiers referenced across
multiple modules (app, corpus, ingestion, status).
"""

QUERY_WEB_VERSION_SIGNATURE = "query-web-meta-safe-v2-20260417"

COMPLIANCE_REPORT_SCHEMA_VERSION = "v1.1"

ALLOWED_EXTENSIONS = {
    ".pdf",
    ".xlsx",
    ".xlsm",
    ".xltx",
    ".xltm",
    ".docx",
    ".doc",
    ".pptx",
    ".ppt",
    ".html",
}

MIME_TYPE_BY_EXTENSION = {
    ".pdf": "application/pdf",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xlsm": "application/vnd.ms-excel.sheet.macroEnabled.12",
    ".xltx": "application/vnd.openxmlformats-officedocument.spreadsheetml.template",
    ".xltm": "application/vnd.ms-excel.template.macroEnabled.12",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".doc": "application/msword",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".ppt": "application/vnd.ms-powerpoint",
    ".html": "text/html",
}
