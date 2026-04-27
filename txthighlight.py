#!/usr/bin/env python
# coding=utf-8
from __future__ import print_function

import argparse
import codecs
import json
import os
import sys
import threading
import time
import uuid

if sys.version_info >= (3,):
    from html import escape as html_escape
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from socketserver import ThreadingMixIn
    from urllib.parse import parse_qs, quote, unquote, urlparse
    text_type = str
else:
    from BaseHTTPServer import BaseHTTPRequestHandler, HTTPServer
    from SocketServer import ThreadingMixIn
    from cgi import escape as html_escape
    from urlparse import parse_qs, urlparse
    from urllib import quote, unquote
    text_type = unicode


class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


HTTP_OK = 200
HTTP_BAD_REQUEST = 400
HTTP_NOT_FOUND = 404
HTTP_INTERNAL_SERVER_ERROR = 500


HTML_PAGE = """<!DOCTYPE html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>Text Highlighter</title>
  <style>
    #topMenu {
      position: fixed;
      left: 0;
      right: 0;
      bottom: 0;
      z-index: 10;
      background: #fff;
      padding: 8px 8px calc(8px + env(safe-area-inset-bottom));
      display: flex;
      gap: 8px;
      user-select: none;
    }
    .highlight {
      background: yellow;
    }
    #document,
    #commentSnippet {
      white-space: pre-wrap;
      overflow-wrap: break-word;
    }
  </style>
</head>
<body>
  <div id=\"topMenu\">
    <button id=\"highlightBtn\" disabled>Highlight</button>
    <button id=\"commentBtn\" disabled>Comment</button>
    <button id=\"removeBtn\" disabled>Remove</button>
  </div>

  <div id=\"document\"></div>

  <dialog id=\"commentModal\">
    <form method=\"dialog\">
      <p><strong>Add comment</strong></p>
      <div id=\"commentSnippet\"></div>
      <textarea id=\"commentBox\" placeholder=\"Add a note for this highlight…\"></textarea>
      <div>
        <button id=\"cancelComment\" value=\"cancel\">Cancel</button>
        <button id=\"saveComment\" value=\"default\">Save comment</button>
      </div>
    </form>
  </dialog>

  <script>
    const Mode = {
      IDLE: 'idle',
      SELECTION_ACTIVE: 'selection_active',
      SELECTION_MENU_ACTIVE: 'selection_menu_active',
      HIGHLIGHT_ACTIVE: 'highlight_active',
      COMMENT_DIALOG: 'comment_dialog',
    };

    const state = {
      mode: Mode.IDLE,
      text: '',
      annotations: [],
      fileName: '',
      filePath: '',
      metadataPath: '',
      selection: null,
      highlightId: null,
      commentTargetId: null,
    };

    const currentPath = window.location.pathname;
    const documentApiPath = `/api/document?path=${encodeURIComponent(currentPath)}`;
    const highlightsApiPath = `/api/highlights?path=${encodeURIComponent(currentPath)}`;

    const docEl = document.getElementById('document');
    const topMenuEl = document.getElementById('topMenu');
    const highlightBtnEl = document.getElementById('highlightBtn');
    const commentBtnEl = document.getElementById('commentBtn');
    const removeBtnEl = document.getElementById('removeBtn');
    const commentModalEl = document.getElementById('commentModal');
    const commentSnippetEl = document.getElementById('commentSnippet');
    const commentBoxEl = document.getElementById('commentBox');
    const saveCommentEl = document.getElementById('saveComment');
    const cancelCommentEl = document.getElementById('cancelComment');

    function showError(message) {
      if (message) {
        window.alert(message);
      }
    }

    function syncTopMenuOffset() {
      document.body.style.paddingTop = '0px';
      document.body.style.paddingBottom = `${topMenuEl.offsetHeight}px`;
    }

    function escapeHtml(text) {
      return text
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#39;');
    }

    function findAnnotationById(id) {
      return state.annotations.find((item) => item.id === id) || null;
    }

    function renderDocument() {
      const annotations = [...state.annotations].sort((a, b) => a.start - b.start || a.end - b.end);
      let cursor = 0;
      let html = '';
      for (const ann of annotations) {
        if (ann.start > cursor) {
          html += escapeHtml(state.text.slice(cursor, ann.start));
        }
        const text = escapeHtml(state.text.slice(ann.start, ann.end));
        const title = ann.comment ? escapeHtml(ann.comment) : 'Highlight';
        html += `<span class="highlight" data-id="${ann.id}" title="${title}">${text}</span>`;
        cursor = ann.end;
      }
      if (cursor < state.text.length) {
        html += escapeHtml(state.text.slice(cursor));
      }
      docEl.innerHTML = html || '(empty file)';
    }

    function getTextOffset(container, targetNode, targetOffset) {
      let total = 0;
      const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT);
      let node;
      while ((node = walker.nextNode())) {
        if (node === targetNode) {
          return total + targetOffset;
        }
        total += node.textContent.length;
      }
      return null;
    }

    function getSelectionOffsets() {
      const selection = window.getSelection();
      if (!selection || selection.rangeCount === 0) {
        return null;
      }
      const range = selection.getRangeAt(0);
      if (range.collapsed) {
        return null;
      }
      if (!docEl.contains(range.startContainer) || !docEl.contains(range.endContainer)) {
        return null;
      }
      const start = getTextOffset(docEl, range.startContainer, range.startOffset);
      const end = getTextOffset(docEl, range.endContainer, range.endOffset);
      if (start == null || end == null || start === end) {
        return null;
      }
      return start < end ? { start, end } : { start: end, end: start };
    }

    function openCommentDialog(id) {
      const ann = findAnnotationById(id);
      if (!ann) {
        return;
      }
      if (commentModalEl.open && commentModalEl.dataset.targetId === id) {
        return;
      }
      commentModalEl.dataset.targetId = id;
      commentSnippetEl.textContent = state.text.slice(ann.start, ann.end);
      commentBoxEl.value = ann.comment || '';
      if (!commentModalEl.open) {
        commentModalEl.showModal();
      }
      window.setTimeout(() => commentBoxEl.focus(), 0);
    }

    function renderUI() {
      const hasSelection = Boolean(state.selection);
      const hasHighlight = Boolean(state.highlightId && findAnnotationById(state.highlightId));

      highlightBtnEl.disabled = !hasSelection;
      commentBtnEl.disabled = !hasSelection && !hasHighlight;
      removeBtnEl.disabled = !hasHighlight;

      if (state.mode === Mode.COMMENT_DIALOG && state.commentTargetId) {
        openCommentDialog(state.commentTargetId);
      } else if (commentModalEl.open) {
        commentModalEl.close();
      }
    }

    function transition(event, payload = {}) {
      switch (event) {
        case 'DOCUMENT_LOADED':
          state.text = payload.text || '';
          state.annotations = payload.annotations || [];
          state.fileName = payload.file_name || '';
          state.filePath = payload.file_path || '';
          state.metadataPath = payload.metadata_path || '';
          state.mode = Mode.IDLE;
          state.selection = null;
          state.highlightId = null;
          state.commentTargetId = null;
          renderDocument();
          renderUI();
          return;

        case 'SELECTION_CHANGED':
          if (payload.selection) {
            state.mode = Mode.SELECTION_ACTIVE;
            state.selection = payload.selection;
            state.highlightId = null;
            state.commentTargetId = null;
          } else if (state.mode === Mode.SELECTION_ACTIVE) {
            state.mode = Mode.IDLE;
            state.selection = null;
          }
          renderUI();
          return;

        case 'MENU_INTERACTION_STARTED':
          if (state.selection || payload.selection) {
            state.mode = Mode.SELECTION_MENU_ACTIVE;
            state.selection = state.selection || payload.selection;
            state.highlightId = null;
            state.commentTargetId = null;
          }
          renderUI();
          return;

        case 'MENU_INTERACTION_ENDED':
          if (state.mode === Mode.SELECTION_MENU_ACTIVE) {
            state.mode = state.selection ? Mode.SELECTION_ACTIVE : Mode.IDLE;
            renderUI();
          }
          return;

        case 'HIGHLIGHT_SELECTED':
          if (!payload.id) {
            return;
          }
          state.mode = Mode.HIGHLIGHT_ACTIVE;
          state.highlightId = payload.id;
          state.selection = null;
          state.commentTargetId = null;
          window.getSelection()?.removeAllRanges();
          renderUI();
          return;

        case 'OPEN_COMMENT_DIALOG':
          if (!payload.id) {
            return;
          }
          state.mode = Mode.COMMENT_DIALOG;
          state.highlightId = payload.id;
          state.selection = null;
          state.commentTargetId = payload.id;
          renderUI();
          return;

        case 'COMMENT_DIALOG_CLOSED':
          if (state.highlightId && findAnnotationById(state.highlightId)) {
            state.mode = Mode.HIGHLIGHT_ACTIVE;
          } else {
            state.mode = Mode.IDLE;
            state.highlightId = null;
          }
          state.commentTargetId = null;
          renderUI();
          return;

        case 'CLEAR_ACTIVE':
          state.mode = Mode.IDLE;
          state.selection = null;
          state.highlightId = null;
          state.commentTargetId = null;
          renderUI();
          return;

        case 'ANNOTATIONS_UPDATED':
          state.annotations = payload.annotations || [];
          renderDocument();
          if (state.highlightId && !findAnnotationById(state.highlightId)) {
            state.highlightId = null;
            if (state.mode !== Mode.SELECTION_ACTIVE && state.mode !== Mode.SELECTION_MENU_ACTIVE) {
              state.mode = Mode.IDLE;
            }
          }
          if (state.commentTargetId && !findAnnotationById(state.commentTargetId)) {
            state.commentTargetId = null;
            if (state.mode === Mode.COMMENT_DIALOG) {
              state.mode = state.highlightId ? Mode.HIGHLIGHT_ACTIVE : Mode.IDLE;
            }
          }
          renderUI();
          return;
      }
    }

    async function api(path, payload) {
      const response = await fetch(path, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.error || `HTTP ${response.status}`);
      }
      return data;
    }

    async function mutate(payload) {
      try {
        const data = await api(highlightsApiPath, payload);
        transition('ANNOTATIONS_UPDATED', { annotations: data.annotations });
        return data.annotations;
      } catch (error) {
        console.error(error);
        showError(error.message);
        return null;
      }
    }

    async function loadDocument() {
      const response = await fetch(documentApiPath);
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.error || `HTTP ${response.status}`);
      }
      transition('DOCUMENT_LOADED', data);
    }

    function findHighlightForSelection(selection, annotations) {
      const matches = annotations
        .filter((ann) => ann.start <= selection.start && ann.end >= selection.end)
        .sort((a, b) => (a.end - a.start) - (b.end - b.start));
      return matches[0] || null;
    }

    topMenuEl.addEventListener('pointerdown', () => {
      transition('MENU_INTERACTION_STARTED', { selection: getSelectionOffsets() });
    });

    topMenuEl.addEventListener('pointerup', () => {
      window.setTimeout(() => {
        transition('MENU_INTERACTION_ENDED');
      }, 0);
    });

    topMenuEl.addEventListener('pointercancel', () => {
      transition('MENU_INTERACTION_ENDED');
    });

    saveCommentEl.addEventListener('click', async () => {
      const id = state.commentTargetId || state.highlightId;
      if (!id) return;
      await mutate({ action: 'update_comment', id, comment: commentBoxEl.value });
      transition('COMMENT_DIALOG_CLOSED');
    });

    cancelCommentEl.addEventListener('click', () => {
      transition('COMMENT_DIALOG_CLOSED');
    });

    commentModalEl.addEventListener('close', () => {
      if (state.mode === Mode.COMMENT_DIALOG) {
        transition('COMMENT_DIALOG_CLOSED');
      }
    });

    highlightBtnEl.addEventListener('click', async () => {
      const selection = state.selection;
      if (!selection) return;
      window.getSelection()?.removeAllRanges();
      transition('CLEAR_ACTIVE');
      await mutate({ action: 'add', start: selection.start, end: selection.end });
    });

    commentBtnEl.addEventListener('click', async () => {
      const selection = state.selection;
      if (selection) {
        window.getSelection()?.removeAllRanges();
        transition('CLEAR_ACTIVE');
        const annotations = await mutate({ action: 'add', start: selection.start, end: selection.end });
        if (!annotations) return;
        const ann = findHighlightForSelection(selection, annotations);
        if (!ann) return;
        transition('OPEN_COMMENT_DIALOG', { id: ann.id });
        return;
      }

      const id = state.highlightId;
      if (!id) return;
      transition('OPEN_COMMENT_DIALOG', { id });
    });

    removeBtnEl.addEventListener('click', async () => {
      const id = state.highlightId;
      if (!id) return;
      transition('CLEAR_ACTIVE');
      await mutate({ action: 'remove_id', id });
    });

    docEl.addEventListener('click', (event) => {
      const highlight = event.target.closest('.highlight');
      if (highlight) {
        transition('HIGHLIGHT_SELECTED', { id: highlight.dataset.id });
        return;
      }
      if (!getSelectionOffsets()) {
        transition('CLEAR_ACTIVE');
      }
    });

    document.addEventListener('selectionchange', () => {
      if (state.mode === Mode.COMMENT_DIALOG || document.activeElement === commentBoxEl) {
        return;
      }
      const selection = getSelectionOffsets();
      if (selection) {
        transition('SELECTION_CHANGED', { selection });
        window.requestAnimationFrame(renderUI);
      } else if (state.mode !== Mode.SELECTION_MENU_ACTIVE) {
        transition('SELECTION_CHANGED', { selection: null });
      }
    });

    document.addEventListener('pointerup', () => {
      if (state.mode === Mode.COMMENT_DIALOG || document.activeElement === commentBoxEl) {
        return;
      }
      window.setTimeout(() => {
        const selection = getSelectionOffsets();
        if (selection) {
          transition('SELECTION_CHANGED', { selection });
        }
      }, 0);
    });

    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') {
        window.getSelection()?.removeAllRanges();
        transition('CLEAR_ACTIVE');
      }
    });

    document.addEventListener('click', (event) => {
      if (event.target.closest('#topMenu') || event.target.closest('.highlight')) {
        return;
      }
      if (!getSelectionOffsets() && (state.mode === Mode.HIGHLIGHT_ACTIVE || state.mode === Mode.SELECTION_ACTIVE || state.mode === Mode.SELECTION_MENU_ACTIVE)) {
        transition('CLEAR_ACTIVE');
      }
    });

    window.addEventListener('resize', syncTopMenuOffset);
    window.requestAnimationFrame(syncTopMenuOffset);

    loadDocument().catch((error) => {
      console.error(error);
      showError(error.message);
      docEl.textContent = 'Failed to load file.';
    });
  </script>
</body>
</html>
"""


class RequestPathError(Exception):
    pass


class RequestNotFoundError(RequestPathError):
    pass


class RequestIsDirectoryError(RequestPathError):
    pass


class InvalidTextFileError(ValueError):
    pass


def now_timestamp():
    # type: () -> int
    return int(time.time())


def clamp_index(value, maximum):
    # type: (object, int) -> int
    number = int(value)
    return max(0, min(maximum, number))


def uri_component_to_text(component):
    # type: (text_type) -> text_type
    decoded = unquote(component)
    if not isinstance(decoded, text_type):
        decoded = decoded.decode("utf-8")
    return decoded


def text_to_uri_component(value):
    # type: (text_type) -> text_type
    if sys.version_info >= (3,):
        return quote(value, safe="")
    return quote(value.encode("utf-8"), safe="")


def uri_path_to_unicode_path_components(uri_path):
    # type: (text_type) -> list
    parsed_path = urlparse(uri_path).path
    return [uri_component_to_text(component) for component in parsed_path.split("/") if component]


def unicode_path_components_to_uri_path(unicode_path_components, force_directory=False):
    # type: (list, bool) -> text_type
    if not unicode_path_components:
        return "/"
    encoded = [text_to_uri_component(component) for component in unicode_path_components]
    uri_path = "/" + "/".join(encoded)
    if force_directory and not uri_path.endswith("/"):
        uri_path += "/"
    return uri_path


def unicode_path_components_to_filesystem_path(root_directory_path, unicode_path_components):
    # type: (text_type, list) -> text_type
    absolute_root_directory_path = os.path.realpath(root_directory_path)
    absolute_file_path = os.path.realpath(
        os.path.join(absolute_root_directory_path, *unicode_path_components)  # type: ignore
    )
    if (
        absolute_file_path == absolute_root_directory_path
        or absolute_file_path.startswith(absolute_root_directory_path + os.sep)
    ):
        return absolute_file_path
    return None


def ensure_metadata_file(text_path, metadata_path):
    # type: (text_type, text_type) -> None
    if os.path.exists(metadata_path):
        return
    payload = {
        "source_file": text_path,
        "annotations": [],
    }
    with codecs.open(metadata_path, "w", encoding="utf-8") as f:
        f.write(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")


def load_text(text_path):
    # type: (text_type) -> text_type
    try:
        with codecs.open(text_path, "r", encoding="utf-8") as f:
            return f.read()
    except UnicodeDecodeError:
        raise InvalidTextFileError("Only UTF-8 text files are supported: %s" % text_path)


def load_annotations(metadata_path, text_path, text):
    # type: (text_type, text_type, text_type) -> list
    try:
        with codecs.open(metadata_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except ValueError:
        raise ValueError("Invalid JSON in metadata file: %s" % metadata_path)
    annotations = data.get("annotations", [])
    if not isinstance(annotations, list):
        raise ValueError("Metadata JSON must contain an 'annotations' array")
    normalized = []
    for item in annotations:
        try:
            start = int(item["start"])
            end = int(item["end"])
        except Exception as exc:
            raise ValueError("Every annotation must have integer start/end offsets: %s" % exc)
        if not (0 <= start < end <= len(text)):
            continue
        normalized.append(
            {
                "id": str(item.get("id") or uuid.uuid4()),
                "start": start,
                "end": end,
                "comment": str(item.get("comment") or ""),
                "created_at": int(item.get("created_at") or now_timestamp()),
                "updated_at": int(item.get("updated_at") or now_timestamp()),
            }
        )
    normalized.sort(key=lambda ann: (ann["start"], ann["end"], ann["id"]))
    return normalized


def save_annotations(metadata_path, text_path, annotations):
    # type: (text_type, text_type, list) -> None
    payload = {
        "source_file": text_path,
        "annotations": annotations,
    }
    with codecs.open(metadata_path, "w", encoding="utf-8") as f:
        f.write(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")


def add_annotation(annotations, start, end, comment):
    # type: (list, int, int, text_type) -> list
    if start > end:
        start, end = end, start
    if start == end:
        return annotations

    merged_comment_parts = []
    if comment.strip():
        merged_comment_parts.append(comment.strip())

    overlapping = []
    remaining = []
    for ann in annotations:
        if ann["end"] < start or ann["start"] > end:
            remaining.append(ann)
        else:
            overlapping.append(ann)

    if overlapping:
        start = min([start] + [ann["start"] for ann in overlapping])
        end = max([end] + [ann["end"] for ann in overlapping])
        overlapping_comments = [ann["comment"].strip() for ann in overlapping if ann.get("comment", "").strip()]
        for item in overlapping_comments:
            if item not in merged_comment_parts:
                merged_comment_parts.append(item)
        created_at = min(int(ann.get("created_at") or now_timestamp()) for ann in overlapping)
    else:
        created_at = now_timestamp()

    remaining.append(
        {
            "id": overlapping[0]["id"] if overlapping else str(uuid.uuid4()),
            "start": start,
            "end": end,
            "comment": "\n\n".join(merged_comment_parts),
            "created_at": created_at,
            "updated_at": now_timestamp(),
        }
    )
    remaining.sort(key=lambda ann: (ann["start"], ann["end"], ann["id"]))
    return remaining


def remove_range(annotations, start, end):
    # type: (list, int, int) -> list
    if start > end:
        start, end = end, start
    if start == end:
        return annotations

    updated = []
    for ann in annotations:
        ann_start = ann["start"]
        ann_end = ann["end"]
        if ann_end <= start or ann_start >= end:
            updated.append(ann)
            continue
        if ann_start < start:
            left_ann = dict(ann)
            left_ann["id"] = str(uuid.uuid4())
            left_ann["start"] = ann_start
            left_ann["end"] = start
            left_ann["updated_at"] = now_timestamp()
            updated.append(left_ann)
        if ann_end > end:
            right_ann = dict(ann)
            right_ann["id"] = str(uuid.uuid4())
            right_ann["start"] = end
            right_ann["end"] = ann_end
            right_ann["updated_at"] = now_timestamp()
            updated.append(right_ann)
    updated.sort(key=lambda ann: (ann["start"], ann["end"], ann["id"]))
    return updated


def update_comment(annotations, ann_id, comment):
    # type: (list, text_type, text_type) -> list
    found = False
    updated = []
    for ann in annotations:
        if ann["id"] == ann_id:
            found = True
            updated_ann = dict(ann)
            updated_ann["comment"] = comment
            updated_ann["updated_at"] = now_timestamp()
            updated.append(updated_ann)
        else:
            updated.append(ann)
    if not found:
        raise ValueError("Unknown highlight id: %s" % ann_id)
    return updated


class AppState(object):
    def __init__(self, root_path, lock):
        # type: (text_type, threading.Lock) -> None
        self.root_path = os.path.realpath(root_path)
        self.lock = lock

    def filesystem_path_from_uri_path(self, uri_path):
        # type: (text_type) -> text_type
        components = uri_path_to_unicode_path_components(uri_path)
        return unicode_path_components_to_filesystem_path(self.root_path, components)

    def _require_existing_file(self, uri_path):
        # type: (text_type) -> text_type
        filesystem_path = self.filesystem_path_from_uri_path(uri_path)
        if filesystem_path is None or not os.path.exists(filesystem_path):
            raise RequestNotFoundError("File not found: %s" % uri_path)
        if os.path.isdir(filesystem_path):
            raise RequestIsDirectoryError("Path is a directory: %s" % uri_path)
        return filesystem_path

    def document_payload(self, uri_path):
        # type: (text_type) -> dict
        text_path = self._require_existing_file(uri_path)
        metadata_path = text_path + ".json"
        text = load_text(text_path)
        with self.lock:
            ensure_metadata_file(text_path, metadata_path)
            annotations = load_annotations(metadata_path, text_path, text)
        return {
            "file_name": os.path.basename(text_path),
            "file_path": text_path,
            "metadata_path": metadata_path,
            "text": text,
            "annotations": annotations,
        }

    def mutate(self, uri_path, payload):
        # type: (text_type, dict) -> list
        text_path = self._require_existing_file(uri_path)
        metadata_path = text_path + ".json"
        text = load_text(text_path)
        action = payload.get("action")

        with self.lock:
            ensure_metadata_file(text_path, metadata_path)
            annotations = load_annotations(metadata_path, text_path, text)
            if action == "add":
                start = clamp_index(payload.get("start"), len(text))
                end = clamp_index(payload.get("end"), len(text))
                comment = str(payload.get("comment") or "")
                annotations = add_annotation(annotations, start, end, comment)
            elif action == "remove_range":
                start = clamp_index(payload.get("start"), len(text))
                end = clamp_index(payload.get("end"), len(text))
                annotations = remove_range(annotations, start, end)
            elif action == "update_comment":
                ann_id = str(payload.get("id") or "")
                comment = str(payload.get("comment") or "")
                annotations = update_comment(annotations, ann_id, comment)
            elif action == "remove_id":
                ann_id = str(payload.get("id") or "")
                annotations = [ann for ann in annotations if ann["id"] != ann_id]
            else:
                raise ValueError("Unsupported action: %s" % action)
            annotations.sort(key=lambda ann: (ann["start"], ann["end"], ann["id"]))
            save_annotations(metadata_path, text_path, annotations)
            return annotations


class HighlighterHandler(BaseHTTPRequestHandler):
    server_version = "TextHighlighter/1.1"

    @property
    def app_state(self):
        # type: () -> AppState
        return self.server.app_state

    def do_GET(self):
        # type: () -> None
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/document":
            target_path = first_query_value(parse_qs(parsed.query), "path", "/")
            self._handle_document_request(target_path)
            return

        filesystem_path = self.app_state.filesystem_path_from_uri_path(path)
        if filesystem_path is None or not os.path.exists(filesystem_path):
            self._send_json({"error": "Not found"}, status=HTTP_NOT_FOUND)
            return
        if os.path.isdir(filesystem_path):
            html = render_directory_listing(self.app_state.root_path, path, filesystem_path)
            self._send_html(html)
            return
        try:
            load_text(filesystem_path)
        except InvalidTextFileError as exc:
            self._send_html(render_error_page(str(exc)), status=HTTP_BAD_REQUEST)
            return
        self._send_html(HTML_PAGE)

    def do_POST(self):
        # type: () -> None
        parsed = urlparse(self.path)
        path = parsed.path
        if path != "/api/highlights":
            self._send_json({"error": "Not found"}, status=HTTP_NOT_FOUND)
            return

        target_path = first_query_value(parse_qs(parsed.query), "path", "/")

        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._send_json({"error": "Invalid Content-Length"}, status=HTTP_BAD_REQUEST)
            return

        try:
            if length:
                body = self.rfile.read(length)
                if not isinstance(body, text_type):
                    body = body.decode("utf-8")
            else:
                body = "{}"
            payload = json.loads(body)
            annotations = self.app_state.mutate(target_path, payload)
        except RequestNotFoundError as exc:
            self._send_json({"error": str(exc)}, status=HTTP_NOT_FOUND)
            return
        except RequestIsDirectoryError as exc:
            self._send_json({"error": str(exc)}, status=HTTP_BAD_REQUEST)
            return
        except ValueError as exc:
            self._send_json({"error": str(exc)}, status=HTTP_BAD_REQUEST)
            return
        except Exception as exc:
            self._send_json({"error": str(exc)}, status=HTTP_INTERNAL_SERVER_ERROR)
            return

        self._send_json({"ok": True, "annotations": annotations})

    def _handle_document_request(self, target_path):
        # type: (text_type) -> None
        try:
            payload = self.app_state.document_payload(target_path)
        except RequestNotFoundError as exc:
            self._send_json({"error": str(exc)}, status=HTTP_NOT_FOUND)
            return
        except RequestIsDirectoryError as exc:
            self._send_json({"error": str(exc)}, status=HTTP_BAD_REQUEST)
            return
        except ValueError as exc:
            self._send_json({"error": str(exc)}, status=HTTP_BAD_REQUEST)
            return
        except Exception as exc:
            self._send_json({"error": str(exc)}, status=HTTP_INTERNAL_SERVER_ERROR)
            return
        self._send_json(payload)

    def log_message(self, fmt, *args):
        # type: (text_type, *object) -> None
        print("[%s] %s - %s" % (self.log_date_time_string(), self.address_string(), fmt % args))

    def _send_html(self, html, status=HTTP_OK):
        # type: (text_type, int) -> None
        data = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_json(self, payload, status=HTTP_OK):
        # type: (dict, int) -> None
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def first_query_value(query, name, default=""):
    # type: (dict, text_type, text_type) -> text_type
    values = query.get(name)
    if not values:
        return default
    return values[0]


def render_error_page(message):
    # type: (text_type) -> text_type
    return u"\n".join(
        [
            u"<!DOCTYPE html>",
            u"<html>",
            u"<head>",
            u"<meta charset='utf-8'>",
            u"<meta name='viewport' content='width=device-width, initial-scale=1'>",
            u"<title>Text Highlighter Error</title>",
            u"</head>",
            u"<body>",
            u"<h1>Cannot open file</h1>",
            u"<p class='error'>%s</p>" % html_escape(message, True),
            u"</body>",
            u"</html>",
        ]
    )


def is_sidecar_metadata_file(filesystem_entry_path):
    # type: (text_type) -> bool
    if not os.path.isfile(filesystem_entry_path):
        return False
    if not filesystem_entry_path.endswith(".json"):
        return False
    source_path = filesystem_entry_path[:-5]
    return os.path.isfile(source_path)


LIKELY_TEXT_EXTENSIONS = set(
    [
        "txt", "text", "md", "rst", "log", "csv", "tsv", "jsonl",
        "ini", "cfg", "conf", "toml", "yaml", "yml", "xml", "html", "css",
        "py", "js", "ts", "tsx", "jsx", "c", "cc", "cpp", "h", "hpp",
        "java", "go", "rs", "sh", "bat", "ps1", "sql",
    ]
)


def is_likely_annotatable_text_file(filesystem_entry, filesystem_entry_path):
    # type: (text_type, text_type) -> bool
    if not os.path.isfile(filesystem_entry_path):
        return False
    if is_sidecar_metadata_file(filesystem_entry_path):
        return False
    if u"." not in filesystem_entry:
        return True
    extension = filesystem_entry.rsplit(u".", 1)[-1].lower()
    return extension in LIKELY_TEXT_EXTENSIONS


def render_directory_listing(root_path, uri_path, filesystem_path):
    # type: (text_type, text_type, text_type) -> text_type
    path_components = uri_path_to_unicode_path_components(uri_path)
    display_path = urlparse(uri_path).path or "/"

    html_lines = [
        u"<!DOCTYPE html>",
        u"<html>",
        u"<head>",
        u"<meta charset='utf-8'>",
        u"<meta name='viewport' content='width=device-width, initial-scale=1'>",
        u"<title>Directory listing for %s</title>" % html_escape(display_path, True),
        u"<style>",
        u".badge-dir { background: #eef5ff; border-color: #b8d3ff; }",
        u".badge-text { background: #eefbf0; border-color: #b8e0bd; }",
        u".badge-sidecar { background: #f7f0ff; border-color: #d7c2ff; }",
        u".badge-file { background: #f6f6f6; border-color: #ddd; }",
        u"</style>",
        u"</head>",
        u"<body>",
        u"<h1>Directory listing for %s</h1>" % html_escape(display_path, True),
        u"<div class='meta'>Serving from %s</div>" % html_escape(root_path, True),
        u"<div class='legend'>",
        u"<span class='badge badge-dir'>directory</span>",
        u"<span class='badge badge-text'>likely annotatable text</span>",
        u"<span class='badge badge-sidecar'>.json sidecar</span>",
        u"<span class='badge badge-file'>other file</span>",
        u"</div>",
        u"<hr>",
        u"<ul>",
    ]

    if path_components:
        parent_directory_uri_path = unicode_path_components_to_uri_path(path_components[:-1], True)
        html_lines.append(u"<li><div class='entry'><a href='%s' class='name'>../</a><span class='badge badge-dir'>up</span></div></li>" % parent_directory_uri_path)

    filesystem_entries = sorted(os.listdir(filesystem_path), key=lambda item: (not os.path.isdir(os.path.join(filesystem_path, item)), item.lower()))  # type: ignore
    for filesystem_entry in filesystem_entries:
        filesystem_entry_path = os.path.join(filesystem_path, filesystem_entry)  # type: ignore
        is_directory = os.path.isdir(filesystem_entry_path)
        display_name = filesystem_entry + (u"/" if is_directory else u"")
        entry_uri_path = unicode_path_components_to_uri_path(path_components + [filesystem_entry], is_directory)
        if is_directory:
            badge_class = u"badge-dir"
            badge_text = u"directory"
        elif is_sidecar_metadata_file(filesystem_entry_path):
            badge_class = u"badge-sidecar"
            badge_text = u"sidecar"
        elif is_likely_annotatable_text_file(filesystem_entry, filesystem_entry_path):
            badge_class = u"badge-text"
            badge_text = u"annotate"
        else:
            badge_class = u"badge-file"
            badge_text = u"file"
        html_lines.append(
            u"<li><div class='entry'><a href='%s' class='name'>%s</a> <span class='badge %s'>%s</span></div></li>"
            % (
                entry_uri_path,
                html_escape(display_name, True),
                badge_class,
                html_escape(badge_text, True),
            )
        )

    html_lines += [
        u"</ul>",
        u"<hr>",
        u"<p>Open a file to annotate it. Open a directory to keep browsing.</p>",
        u"</body>",
        u"</html>",
    ]
    return u"\n".join(html_lines)


def parse_args():
    # type: () -> argparse.Namespace
    parser = argparse.ArgumentParser(
        description="Serve a plain text file browser with local highlight/comment storage."
    )
    parser.add_argument(
        "path",
        nargs="?",
        default=".",
        help="Path to a plain text file or a directory containing plain text files (default: current directory)",
    )
    parser.add_argument("--host", default="localhost", help="Host interface to bind to")
    parser.add_argument("--port", default=8000, type=int, help="Port to listen on")
    return parser.parse_args()


def main():
    # type: () -> None
    args = parse_args()
    target_path = os.path.abspath(os.path.expanduser(args.path))
    if not os.path.exists(target_path):
        raise SystemExit("Path not found: %s" % target_path)

    if os.path.isdir(target_path):
        root_path = target_path
        initial_uri_path = "/"
        print("Serving directory %s" % root_path)
    else:
        load_text(target_path)
        root_path = os.path.dirname(target_path)
        initial_uri_path = unicode_path_components_to_uri_path([os.path.basename(target_path)])
        print("Serving file %s" % target_path)

    app_state = AppState(root_path=root_path, lock=threading.Lock())

    httpd = ThreadingHTTPServer((args.host, args.port), HighlighterHandler)
    httpd.app_state = app_state  # type: ignore[attr-defined]

    print("Root directory: %s" % root_path)
    print("Directory view: http://%s:%s/" % (args.host, args.port))
    if initial_uri_path != "/":
        print("Initial file: http://%s:%s%s" % (args.host, args.port, initial_uri_path))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down…")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
