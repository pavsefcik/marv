# Office

marv is a local-first assistant for **office work**: documents, images, and the
questions around them, on the machine you already use. This page is the
practical entry point for that direction — what works today using software that
already ships with macOS, and what still needs a decision.

## Positioning

The unit of work is **a conversation plus artifacts**, not a repository. The
long-range track is described in [`../ROADMAP.md`](../ROADMAP.md) ("Direction —
local-LLM coworking"); the short version is:

- The everyday jobs are a folder of JPEGs to resize, a PDF to rasterise or
  compress, a Word file to read, a receipt to look at.
- Those jobs are read-mostly and interruption-heavy, and the deliverable is
  usually **a file**, not a wall of chat.
- Code editing is one artifact type among several, not the default frame.

This does not need a rewrite, a new tool, or a new dependency to start. `sips`,
`qlmanage`, `textutil` and `mdls` are on every macOS install, and the existing
[`bash`](tools.md) tool can drive them. The playbook ships with marv at
[`../src/marv/skills/builtin/office-files/SKILL.md`](../src/marv/skills/builtin/office-files/SKILL.md)
and is loaded automatically: invoke it with `$office-files` in any session, no
copy needed.

## Check what is installed

Recipes below are only as good as the binaries present, so discover first:

```sh
for b in sips qlmanage textutil mdls file stat pdftoppm pdfinfo pdftotext gs qpdf magick; do
  if command -v "$b" >/dev/null 2>&1; then printf '%-9s %s\n' "$b" "$(command -v "$b")"; else printf '%-9s MISSING\n' "$b"; fi
done
```

`sips`, `qlmanage`, `textutil`, `mdls`, `file` and `stat` are the built-in set.
`pdftoppm`/`pdfinfo`/`pdftotext` come from poppler, `gs` from Ghostscript, `qpdf`
from qpdf (all `brew install …`), `magick` from ImageMagick. Treat a `MISSING`
line as "this recipe is unavailable", never as "the command probably works".

## Images — `sips` (built in)

```sh
sips -Z 1600 --out "out/photo.jpg" "in/photo.heic"           # max dimension, aspect kept
sips -z 600 800 --out "out/card.png" "in/card.png"           # exact size: height then width
sips -s format jpeg -s formatOptions 60 --out "out/q60.jpg" "in/photo.png"
sips -Z 1600 -s format jpeg -o "out" "in"/*.jpg              # batch into a directory
sips -g pixelWidth -g pixelHeight -g format "in/photo.heic"  # metadata, no Pillow needed
```

Verified behaviour on a macOS 27 install:

- `-Z` scales the longest side and preserves the aspect ratio; `-z` is an exact
  `height width` and does not.
- JPEG, PNG, HEIC and TIFF read and write; conversions between them work.
- `-s formatOptions <10–100>` sets JPEG quality. It changes size materially on
  photographic input; re-encoding a file that is already a small JPEG barely
  moves it.
- `-o <dir>` writes the same basename into `<dir>` and warns when the format
  changes the suffix (`img.png` → `img.jpg`). Inputs are never modified when
  `--out`/`-o` is given.

## PDFs — the honest state

macOS gives you a **first page and a thumbnail**, not a PDF toolkit:

```sh
sips -s format png --out "out/page1.png" "in/report.pdf"      # first page only
qlmanage -t -s 1200 -o "out" "in/report.pdf"                  # one thumbnail (report.pdf.png)
mdls -name kMDItemNumberOfPages "in/report.pdf"               # Spotlight-only; (null) if unindexed
```

Verified behaviour:

- `sips` on a 14 MB multi-page PDF produced **exactly one** PNG — page 1. There
  is no page-range or all-pages mode, and the call still exits 0, so a script
  that does not check will silently claim success.
- `qlmanage -t` produced one thumbnail at the requested max size, same single
  page.
- `mdls` answered `kMDItemNumberOfPages = 1` for an indexed file and `(null)`
  for the same file copied to an unindexed location, so it cannot be used as a
  reliable page count.

Not available built-in: multi-page rasterising, compression, merge, split,
rotate, and text extraction. Those need poppler, Ghostscript, or qpdf — see the
decision below. Until then, state the single-page limit in the answer rather
than implying the whole document was converted.

## Documents and metadata

```sh
textutil -info "in/notes.rtf"                             # type, size, character count
textutil -convert txt -stdout "in/notes.rtf"              # read a document as text
textutil -convert txt -output "out/notes.txt" "in/notes.docx"
qlmanage -t -s 1200 -o "out" "in/deck.key"                # thumbnail of anything Quick Look shows
mdls -name kMDItemContentType -name kMDItemFSName "in/report.pdf"
```

`textutil` reads and writes `txt`, `rtf`, `rtfd`, `html`, `doc`, `docx`, `odt`,
`wordml` and `webarchive` (no PDF). `mdls` reads Spotlight metadata, so anything
not indexed reads as `(null)`.

## Not wired yet — and the decision it needs

| Capability | Today | Decision needed |
| --- | --- | --- |
| Multi-page PDF → images | page 1 only, via `sips`/`qlmanage` | adopt poppler (`pdftoppm`) as the supported rasteriser |
| PDF compress | nothing built-in | Ghostscript (`gs -sDEVICE=pdfwrite -dPDFSETTINGS=/ebook`) and/or `qpdf` |
| PDF merge/split/rotate/text-extract | nothing built-in | `qpdf` (lossless, structure) and `pdftotext` (poppler) |
| Image work beyond `sips` (masking, compositing, colour work) | `sips` covers resize/convert only | a Python image library (Pillow) — a runtime dependency |
| Multimodal input (an image or PDF page → the vision model) | text-only `Message.content` | the existing C4.1 item: content parts through `to_api_dict` and the provider payload |
| Artifacts as output (`outputs/` workspace, rendered panel) | files land wherever `bash` writes them | the existing C4.3 item |

**The one decision that unblocks most of this:** whether the PDF engine is an
*external tool the user installs* (detected with `command -v`, with a clear
"install it like this" message and no silent degradation — the route the
`office-files` skill takes) or a *Python dependency inside marv* (`pypdf`,
`pdf2image` — which still needs poppler for rasterising). The external-tool route
keeps the no-new-dependency and local-first install story intact; the dependency
route makes the capability unconditional at packaging cost. This lane deliberately
did not decide it.

## No new dependency in this lane

The skill and these recipes use the `bash` tool that already ships and the
binaries already on the machine. No runtime tool, no config key, no dependency
was added. Where a step needs an installable tool, the recipes detect it,
refuse to substitute something lossier, and print the install command.

## Where this is tracked

- [`../ROADMAP.md`](../ROADMAP.md) — "Direction — local-LLM coworking" and its
  "Office capabilities (near-term)" list with sizes and gates.
- [`../src/marv/skills/builtin/office-files/SKILL.md`](../src/marv/skills/builtin/office-files/SKILL.md)
  — the runnable playbook (`$office-files`), discovered automatically.
- [`tools.md`](tools.md) — the `bash` tool these recipes run through.
