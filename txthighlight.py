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
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from socketserver import ThreadingMixIn
    from urllib.parse import urlparse
    text_type = str
else:
    from BaseHTTPServer import BaseHTTPRequestHandler, HTTPServer
    from SocketServer import ThreadingMixIn
    from urlparse import urlparse
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
  <div id=\"document\"></div>

  <div id=\"selectionToolbar\" hidden>
    <button id=\"highlightBtn\">Highlight</button>
    <button id=\"selectionCommentBtn\">Comment</button>
  </div>

  <div id=\"highlightToolbar\" hidden>
    <button id=\"highlightCommentBtn\">Comment</button>
    <button id=\"highlightRemoveBtn\">Remove</button>
  </div>

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
      HIGHLIGHT_ACTIVE: 'highlight_active',
      COMMENT_DIALOG: 'comment_dialog',
    };

    const state = {
      mode: Mode.IDLE,
      text: '',
      annotations: [],
      fileName: '',
      metadataPath: '',
      selection: null,
      highlightId: null,
      commentTargetId: null,
    };

    const docEl = document.getElementById('document');
    const selectionToolbarEl = document.getElementById('selectionToolbar');
    const highlightToolbarEl = document.getElementById('highlightToolbar');
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

    function findHighlightElById(id) {
      if (!id) {
        return null;
      }
      return docEl.querySelector(`.highlight[data-id="${id}"]`);
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

    function getSelectionRect() {
      const selection = window.getSelection();
      if (!selection || selection.rangeCount === 0 || !state.selection) {
        return null;
      }
      const rect = selection.getRangeAt(0).getBoundingClientRect();
      if (!rect || (!rect.width && !rect.height)) {
        return null;
      }
      return rect;
    }

    function placeToolbar(toolbarEl, rect) {
      toolbarEl.hidden = false;
      toolbarEl.style.position = 'fixed';
      toolbarEl.style.zIndex = '50';

      const toolbarWidth = toolbarEl.offsetWidth;
      const top = rect.bottom + 8;
      const maxLeft = Math.max(10, window.innerWidth - toolbarWidth - 10);
      const left = Math.min(
        maxLeft,
        Math.max(10, rect.left + rect.width / 2 - toolbarWidth / 2)
      );

      toolbarEl.style.top = `${top}px`;
      toolbarEl.style.left = `${left}px`;
    }

    function hideSelectionToolbar() {
      selectionToolbarEl.hidden = true;
    }

    function hideHighlightToolbar() {
      highlightToolbarEl.hidden = true;
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

    function closeCommentDialog() {
      commentModalEl.dataset.targetId = '';
      if (commentModalEl.open) {
        commentModalEl.close();
      }
    }

    function renderUI() {
      hideSelectionToolbar();
      hideHighlightToolbar();

      if (state.mode === Mode.SELECTION_ACTIVE && state.selection) {
        const rect = getSelectionRect();
        if (rect) {
          placeToolbar(selectionToolbarEl, rect);
        }
      }

      if (state.mode === Mode.HIGHLIGHT_ACTIVE && state.highlightId) {
        const highlightEl = findHighlightElById(state.highlightId);
        if (highlightEl) {
          placeToolbar(highlightToolbarEl, highlightEl.getBoundingClientRect());
        }
      }

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
            if (state.mode !== Mode.SELECTION_ACTIVE) {
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
        const data = await api('/api/highlights', payload);
        transition('ANNOTATIONS_UPDATED', { annotations: data.annotations });
        return data.annotations;
      } catch (error) {
        console.error(error);
        showError(error.message);
        return null;
      }
    }

    async function loadDocument() {
      const response = await fetch('/api/document');
      const data = await response.json();
      transition('DOCUMENT_LOADED', data);
    }

    function findHighlightForSelection(selection, annotations) {
      const matches = annotations
        .filter((ann) => ann.start <= selection.start && ann.end >= selection.end)
        .sort((a, b) => (a.end - a.start) - (b.end - b.start));
      return matches[0] || null;
    }

    selectionToolbarEl.addEventListener('pointerdown', (event) => {
      event.preventDefault();
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

    document.getElementById('highlightBtn').addEventListener('click', async () => {
      const selection = state.selection;
      if (!selection) return;
      window.getSelection()?.removeAllRanges();
      transition('CLEAR_ACTIVE');
      await mutate({ action: 'add', start: selection.start, end: selection.end });
    });

    document.getElementById('selectionCommentBtn').addEventListener('click', async () => {
      const selection = state.selection;
      if (!selection) return;
      window.getSelection()?.removeAllRanges();
      transition('CLEAR_ACTIVE');
      const annotations = await mutate({ action: 'add', start: selection.start, end: selection.end });
      if (!annotations) return;
      const ann = findHighlightForSelection(selection, annotations);
      if (!ann) return;
      transition('OPEN_COMMENT_DIALOG', { id: ann.id });
    });

    document.getElementById('highlightCommentBtn').addEventListener('click', async () => {
      const id = state.highlightId;
      if (!id) return;
      transition('OPEN_COMMENT_DIALOG', { id });
    });

    document.getElementById('highlightRemoveBtn').addEventListener('click', async () => {
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
      } else {
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
      if (!event.target.closest('#highlightToolbar') && !event.target.closest('.highlight')) {
        if (state.mode === Mode.HIGHLIGHT_ACTIVE && !getSelectionOffsets()) {
          transition('CLEAR_ACTIVE');
        }
      }
      if (!event.target.closest('#selectionToolbar') && !getSelectionOffsets() && state.mode === Mode.SELECTION_ACTIVE) {
        transition('CLEAR_ACTIVE');
      }
    });

    loadDocument().catch((error) => {
      console.error(error);
      showError(error.message);
      docEl.textContent = 'Failed to load file.';
    });
  </script>
</body>
</html>
"""


class AppState(object):
    def __init__(self, text_path, metadata_path, text, lock):
        # type: (text_type, text_type, text_type, threading.Lock) -> None
        self.text_path = text_path
        self.metadata_path = metadata_path
        self.text = text
        self.lock = lock
        self._ensure_metadata_file()

    def _ensure_metadata_file(self):
        # type: () -> None
        if os.path.exists(self.metadata_path):
            return
        payload = {
            "source_file": self.text_path,
            "annotations": [],
        }
        with codecs.open(self.metadata_path, "w", encoding="utf-8") as f:
            f.write(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")

    def _load_annotations(self):
        # type: () -> list
        try:
            with codecs.open(self.metadata_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except ValueError:
            raise ValueError("Invalid JSON in metadata file: %s" % self.metadata_path)
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
            if not (0 <= start < end <= len(self.text)):
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

    def _save_annotations(self, annotations):
        # type: (list) -> None
        payload = {
            "source_file": self.text_path,
            "annotations": annotations,
        }
        with codecs.open(self.metadata_path, "w", encoding="utf-8") as f:
            f.write(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")

    def document_payload(self):
        # type: () -> dict
        with self.lock:
            annotations = self._load_annotations()
        return {
            "file_name": os.path.basename(self.text_path),
            "file_path": self.text_path,
            "metadata_path": self.metadata_path,
            "text": self.text,
            "annotations": annotations,
        }

    def mutate(self, payload):
        # type: (dict) -> list
        action = payload.get("action")
        with self.lock:
            annotations = self._load_annotations()
            if action == "add":
                start = clamp_index(payload.get("start"), len(self.text))
                end = clamp_index(payload.get("end"), len(self.text))
                comment = str(payload.get("comment") or "")
                annotations = add_annotation(annotations, start, end, comment)
            elif action == "remove_range":
                start = clamp_index(payload.get("start"), len(self.text))
                end = clamp_index(payload.get("end"), len(self.text))
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
            self._save_annotations(annotations)
            return annotations


def now_timestamp():
    # type: () -> int
    return int(time.time())


def clamp_index(value, maximum):
    # type: (object, int) -> int
    number = int(value)
    return max(0, min(maximum, number))


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


class HighlighterHandler(BaseHTTPRequestHandler):
    server_version = "TextHighlighter/1.0"

    @property
    def app_state(self):
        # type: () -> AppState
        return self.server.app_state

    def do_GET(self):
        # type: () -> None
        path = urlparse(self.path).path
        if path == "/":
            self._send_html(HTML_PAGE)
            return
        if path == "/api/document":
            self._send_json(self.app_state.document_payload())
            return
        self._send_json({"error": "Not found"}, status=HTTP_NOT_FOUND)

    def do_POST(self):
        # type: () -> None
        path = urlparse(self.path).path
        if path != "/api/highlights":
            self._send_json({"error": "Not found"}, status=HTTP_NOT_FOUND)
            return

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
            annotations = self.app_state.mutate(payload)
        except ValueError as exc:
            self._send_json({"error": str(exc)}, status=HTTP_BAD_REQUEST)
            return
        except Exception as exc:
            self._send_json({"error": str(exc)}, status=HTTP_INTERNAL_SERVER_ERROR)
            return

        self._send_json({"ok": True, "annotations": annotations})

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


def parse_args():
    # type: () -> argparse.Namespace
    parser = argparse.ArgumentParser(
        description="Serve a plain text file in a browser with local highlight/comment storage."
    )
    parser.add_argument("text_file", help="Path to the plain text file to annotate")
    parser.add_argument("--host", default="localhost", help="Host interface to bind to")
    parser.add_argument("--port", default=8000, type=int, help="Port to listen on")
    return parser.parse_args()


def main():
    # type: () -> None
    args = parse_args()
    text_path = os.path.abspath(os.path.expanduser(args.text_file))
    if not os.path.isfile(text_path):
        raise SystemExit("Text file not found: %s" % text_path)

    metadata_path = text_path + ".json"
    with codecs.open(text_path, "r", encoding="utf-8", errors="replace") as f:
        text = f.read()
    app_state = AppState(text_path=text_path, metadata_path=metadata_path, text=text, lock=threading.Lock())

    httpd = ThreadingHTTPServer((args.host, args.port), HighlighterHandler)
    httpd.app_state = app_state  # type: ignore[attr-defined]

    print("Serving %s" % text_path)
    print("Metadata file: %s" % metadata_path)
    print("Open http://%s:%s" % (args.host, args.port))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down…")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
