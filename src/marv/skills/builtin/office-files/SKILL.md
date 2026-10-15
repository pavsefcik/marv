---
name: office-files
description: Batch document and image work with the binaries macOS already ships (sips, qlmanage, textutil, mdls)
---

# Office Files

You are doing office file work in a folder of documents and images: resizing a
batch of photos, turning PDF pages into images, compressing a PDF, converting a
Word file to text. Work with what is on the machine — `sips`, `qlmanage`,
`textutil` and `mdls` ship with macOS — and use the existing `bash` tool for it.

## Step 0 — discover what is installed

Run this first, every session, before promising any output:

```sh
for b in sips qlmanage textutil mdls file stat pdftoppm pdfinfo pdftotext gs qpdf magick; do
  if command -v "$b" >/dev/null 2>&1; then printf '%-9s %s\n' "$b" "$(command -v "$b")"; else printf '%-9s MISSING\n' "$b"; fi
done
```

`MISSING` means you must not claim that tool's work. Report what you used and
what you could not do.

## Safety rules — read before running anything

1. **Never overwrite an input.** Write every result into a separate directory
   (`out/`, or `outputs/` when the user asked for deliverables).
2. **Quote every path**: `"$f"`, `--out "$out"`. Filenames with spaces are the
   normal case in office folders, and an unquoted path is a data-loss bug.
3. **Never build a command by pasting a filename into a string.** Loop over
   globs (`for f in "$in_dir"/*.jpg`) or `find … -print0` and always reference
   the variable quoted. Do not `eval`, do not `sh -c "$user_string"`, do not
   drive a loop from `ls` output.
4. **Check the output exists** before reporting success: `[ -f "$out" ]`.
5. **Report what actually happened** — a per-file table plus explicit notes for
   skipped, failed, or partially done files. If a tool only handled page one of
   a PDF, say exactly that.
6. **Do not silently degrade quality.** If the tool for a lossy step
   (PDF compression) is missing, stop and say how to install it rather than
   re-encoding with something weaker.

## Batch image resize / convert (`sips`, built in)

Set the four variables, then run the loop. `-Z` is the max dimension
(aspect-ratio preserved); `-z height width` is an exact size.

```sh
in_dir="in"; out_dir="out"; max=1600; format=jpeg
[ -d "$in_dir" ] || { echo "no such directory: $in_dir"; exit 1; }
mkdir -p "$out_dir"
printf '%-34s %-11s %-11s %8s\n' FILE BEFORE AFTER OUT
for f in "$in_dir"/*.jpg "$in_dir"/*.jpeg "$in_dir"/*.png "$in_dir"/*.heic "$in_dir"/*.tif "$in_dir"/*.tiff; do
  [ -f "$f" ] || continue
  base=$(basename "$f"); stem=${base%.*}
  case "$format" in
    jpeg|jpg) ext=jpg ;;
    png) ext=png ;;
    heic) ext=heic ;;
    tiff) ext=tif ;;
    *) ext="$format" ;;
  esac
  out="$out_dir/$stem.$ext"
  if [ "$out" = "$f" ]; then echo "SKIP (input == output): $f"; continue; fi
  before=$(sips -g pixelWidth -g pixelHeight "$f" | awk '/pixelWidth/{w=$2}/pixelHeight/{h=$2}END{print w"x"h}')
  if ! sips -Z "$max" -s format "$format" --out "$out" "$f" >/dev/null 2>&1 || [ ! -f "$out" ]; then
    printf '%-34s %-11s %-11s %8s\n' "$base" "$before" "-" "FAILED"
    continue
  fi
  after=$(sips -g pixelWidth -g pixelHeight "$out" | awk '/pixelWidth/{w=$2}/pixelHeight/{h=$2}END{print w"x"h}')
  printf '%-34s %-11s %-11s %8s\n' "$base" "$before" "$after" "$(( $(stat -f%z "$out") / 1024 ))K"
done
```

Variations:

- Exact size instead of max dimension: replace `-Z "$max"` with
  `-z 600 800` (**height first**, then width).
- JPEG quality: add `-s formatOptions 60` (10–100). It matters on photographic
  input; a file that is already a small JPEG barely moves.
- One-liner batch into a directory (`sips` keeps the basename and warns when the
  format changes the suffix):

  ```sh
  sips -Z 1600 -s format jpeg -o "out" "in"/*.jpg
  ```

- Inspect a file without opening it:
  `sips -g pixelWidth -g pixelHeight -g format -g space "file.heic"`.

## PDF → images

Prefer `pdftoppm` (poppler) — it does every page at a chosen DPI:

```sh
in_pdf="in/report.pdf"; out_dir="out"; dpi=150
[ -f "$in_pdf" ] || { echo "no such file: $in_pdf"; exit 1; }
mkdir -p "$out_dir"
if command -v pdfinfo >/dev/null 2>&1; then
  echo "pages: $(pdfinfo "$in_pdf" | awk '/^Pages:/{print $2}')"
fi
if command -v pdftoppm >/dev/null 2>&1; then
  pdftoppm -r "$dpi" -png "$in_pdf" "$out_dir/page"
  echo "rendered every page with pdftoppm at ${dpi}dpi"
else
  stem=$(basename "${in_pdf%.*}")
  out="$out_dir/$stem-page1.png"
  sips -s format png --out "$out" "$in_pdf" >/dev/null 2>&1
  echo "NOTE: pdftoppm is not installed, so this is page 1 only (sips renders one page)."
  echo "      For every page: brew install poppler"
fi
ls -la "$out_dir"
```

Honesty rules for this step:

- `sips` on a PDF renders **the first page only** and ignores the rest.
  `qlmanage -t -s 1200 -o "$out_dir" "$in_pdf"` produces a single thumbnail
  image — also one page.
- There is no built-in page count. `mdls -name kMDItemNumberOfPages "$pdf"`
  answers only for Spotlight-indexed files and prints `(null)` otherwise; treat
  `(null)` as "unknown", not as one page.
- Never print "all pages rendered" after a `sips`/`qlmanage` run.

## PDF compress, merge/split/rotate/text-extract

macOS ships **no** PDF compressor, merger, or splitter. These need an installed
tool, so check first and say what you used:

```sh
in_pdf="in/report.pdf"; out_dir="out"; mkdir -p "$out_dir"
if command -v gs >/dev/null 2>&1; then
  gs -sDEVICE=pdfwrite -dCompatibilityLevel=1.4 -dPDFSETTINGS=/ebook \
     -dNOPAUSE -dQUIET -dBATCH \
     -sOutputFile="$out_dir/report-ebook.pdf" "$in_pdf"
  echo "compressed with ghostscript (/ebook re-encodes page images; it is lossy)"
  echo "before: $(stat -f%z "$in_pdf") bytes  after: $(stat -f%z "$out_dir/report-ebook.pdf") bytes"
elif command -v qpdf >/dev/null 2>&1; then
  qpdf --linearize --object-streams=generate "$in_pdf" "$out_dir/report-linearized.pdf"
  echo "rewrote with qpdf (lossless restructuring; often a small gain)"
else
  echo "No PDF engine (gs/qpdf) installed - not compressing, and not pretending to."
  echo "Install one: brew install ghostscript qpdf   (poppler adds pdftoppm/pdftotext)"
fi
```

- Ghostscript settings, smallest to largest: `/screen`, `/ebook`, `/printer`.
  All re-encode images; verify the before/after sizes and say which setting ran.
- `qpdf` operations are lossless (structure only):

  ```sh
  qpdf --empty --pages a.pdf b.pdf -- "$out_dir/merged.pdf"   # merge
  qpdf "$in_pdf" --pages . 1-3 -- "$out_dir/first-3-pages.pdf" # split
  qpdf --rotate=90 "$in_pdf" "$out_dir/rotated.pdf"            # rotate
  ```

- Text extraction: `pdftotext -layout "$in_pdf" -` (poppler), or
  `gs -sDEVICE=txtwrite -o "$out_dir/report.txt" "$in_pdf"`.

## Documents and metadata (`textutil`, `qlmanage`, `mdls`)

```sh
textutil -info "in/notes.rtf"                            # type, size, character count
textutil -convert txt -stdout "in/notes.rtf"             # read a document as text
textutil -convert txt -output "out/notes.txt" "in/notes.docx"
qlmanage -t -s 1200 -o "$out_dir" "in/deck.key"          # preview thumbnail
mdls -name kMDItemContentType -name kMDItemNumberOfPages "in/report.pdf"
```

`textutil` reads/writes txt, rtf, rtfd, html, doc, docx, odt, wordml and
webarchive. `mdls` reads Spotlight metadata, which is `(null)` for files that
are not indexed.

## Report format

End with a short summary the user can check:

- **What ran** — the tools and the exact settings (max dimension, DPI, quality,
  PDF preset).
- **Table** — input file, before → after dimensions or sizes, output path.
- **Not done** — missing binaries, single-page-only renders, skipped files, and
  the install command that would fix it.
