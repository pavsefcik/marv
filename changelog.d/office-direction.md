### Added

- **Office/document direction.** `ROADMAP.md` sharpens the coworking track
  around the office-assistant framing (documents and images first, repo editing
  as one artifact type) and gains a near-term "Office capabilities" list with
  sizes and gates: batch image resize/convert with the built-in `sips`,
  PDF rasterise/compress, PDF vector ops (all three gated on the PDF-engine
  decision), plus pointers to the existing multimodal-input (C4.1) and
  artifacts-as-output (C4.3) items.
- **`docs/office.md`.** Office positioning, the recipes that work today with the
  binaries macOS already ships (`sips`, `qlmanage`, `textutil`, `mdls`) through
  the existing `bash` tool, and an honest account of what is missing — `sips`
  and `qlmanage` render **page 1 only**, there is no built-in PDF compressor or
  page count — with the dependency decision the PDF items need.
- **`office-files` skill** (`examples/skills/office-files/SKILL.md`). A
  ready-to-copy playbook for office file work: discover available binaries
  first, batch image resize/convert with `sips`, PDF → images preferring
  `pdftoppm` and stating the single-page `sips` fallback honestly, PDF
  compression via `gs`/`qpdf` or an explicit "install one" message, and safety
  rules (never overwrite inputs, quote paths, verify outputs, report what ran).
  No new runtime tool, dependency, or config key.
