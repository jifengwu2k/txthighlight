# `txthighlight`

A tiny local web app for highlighting and commenting on plain text files.

## Installation

```bash
pip install txthighlight
```

## Usage

Two subcommands: `serve` (start the web UI) and `extract` (export highlights).

### `serve` — highlight text in the browser

Serve the current directory:

```bash
txthighlight serve
```

Serve a single file:

```bash
txthighlight serve --host 0.0.0.0 --port 8080 somefile.txt
```

Serve a directory:

```bash
txthighlight serve --host 0.0.0.0 --port 8080 ./docs
```

Then open:

```text
http://0.0.0.0:8080
```

If you serve a directory, `/` shows a simple directory listing and you can click into files or subdirectories. The listing visually marks directories, likely annotatable text files, `.json` sidecars, and other files.

### `extract` — export highlighted text

Once you've highlighted passages in a file, extract them from the command line:

```bash
txthighlight extract somefile.txt
```

By default it prints each highlighted passage on its own line.

Use `--format` to control the output:

```bash
# Plain text (default) — one highlighted span per line
txthighlight extract somefile.txt

# Markdown — highlights as blockquotes with comments
txthighlight extract --format markdown somefile.txt

# JSON — full annotation data including offsets and comments
txthighlight extract --format json somefile.txt
```

The extract command reads the `<file>.txt.json` sidecar that was created while highlighting. It does not require the server to be running.

Highlight metadata is stored next to the source file in:

```text
somefile.txt.json
```

Only UTF-8 text files are supported. Non-UTF-8 files are rejected with an error.

### What it does

- renders a plain text file in the browser
- lets you select text and highlight it
- lets you add comments to highlights
- lets you remove highlights
- stores annotation data locally in a JSON sidecar file
- works with desktop and mobile browsers
- extracts highlighted text via CLI (plain text, markdown, or JSON)

### Why this exists

There are many tools for annotating PDFs, rich text documents, and web pages.
There are very few simple tools for annotating a raw local text file.

Plain text is still a common working format for:

- transcripts
- logs
- OCR output
- legal or policy text
- prompt corpora
- research notes
- interview notes
- exported chat histories

This project fills that narrow gap: plain text in, annotations in a nearby JSON file, no database required.

### The niche

This tool lives in an awkward but useful niche.

Most annotation tools assume one of these:

- HTML pages annotated by a browser extension
- rich text documents like Word or Google Docs
- PDF with built-in annotation support
- note-taking apps with their own storage format

But sometimes you do not want any of that. Sometimes you have a `.txt` file and want to keep working with a `.txt` file.

### Data format

Annotations are stored in `<text-file>.json`.

Example:

```json
{
  "source_file": "/path/to/somefile.txt",
  "annotations": [
    {
      "id": "3d7278a2-6d67-4e1c-a0c8-4a0a7d3b0e40",
      "start": 12,
      "end": 42,
      "comment": "Important passage",
      "created_at": 1713350000,
      "updated_at": 1713350123
    }
  ]
}
```

Offsets are character offsets into the text file as loaded by the app.

## Contributing

Contributions are welcome! Please submit pull requests or open issues on the GitHub repository.

## License

This project is licensed under the [MIT License](LICENSE).
