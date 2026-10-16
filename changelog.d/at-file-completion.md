### Added

- **`@path` completion in the prompt.** A trailing `@partial` (or `@"partial`)
  opens the dropdown the `/` commands and `$` skills already use, listing
  matching entries from the working directory: directories first with a trailing
  `/`, names holding whitespace quoted as `@"my shot.png"`, and dotfiles only
  once the partial starts with a dot. Each choice writes the whole prompt back
  (the text before the `@`, the path, a trailing space) so a mention in the
  middle of a sentence stays intact. The list is paths, not attachments — only an
  existing image or PDF is attached on submit.
- **Tab follows the option id.** `Tab` now rewrites the prompt from `Option.id`
  when an option carries one, as `Enter` and a click already did, so a model
  choice or a completed path behaves the same on either key. `/` and `$`
  options carry no id, so their behaviour is unchanged.
