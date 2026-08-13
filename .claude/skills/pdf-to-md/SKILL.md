---
name: pdf-to-md
description: Convert a PDF to markdown before reading it, and cache the result so the next drill-down on the same document skips the conversion. Use whenever docs mode is about to open a PDF, or when asked to read a PDF's content, argument, or citations rather than just its metadata.
---

Read the markdown, not the PDF, whenever a cached copy exists. Deep PDF parsing is
slow and expensive to repeat, and docs mode revisits the same handful of files
across many drill-downs — this is what stops every one of those turns from
redoing it.

## Cache path

The cache lives at the directory named in this turn's system prompt (look for
"check `<path>` for a cached markdown copy"). A PDF's cache entry is named from
its own resolved absolute path, deterministically, with no run-to-run
randomness:

1. Take the PDF's absolute path.
2. Drop the single leading `/`.
3. Replace every remaining `/` with `__`.
4. Append `.md`.

`/Users/anshu/stuff/Papers/attention.pdf` becomes
`Users__anshu__stuff__Papers__attention.pdf.md`, written inside the cache
directory. Two different PDFs never collide because the whole path is encoded,
not just the filename; the same PDF reached via two different trails always
lands on the same cache entry.

## Steps

1. Before opening a PDF, compute its cache path as above and check whether it
   already exists.
2. If it exists, read that instead of the PDF. Done — do not open the PDF at all.
3. If it does not exist, read the PDF and produce a faithful markdown transcript:
   headings, section structure, body text, and enough of any tables or figure
   captions to reason about the document. This is a transcript, not a summary —
   summarizing here would throw away material a later, more specific drill-down
   might need.
4. Write the transcript to the cache path before doing anything else with the
   content. Writing it late risks skipping it if the turn runs out of room.
5. Read your own cached copy back, and reason from that.

Only ever write inside the cache directory. Nothing else about this turn's
write permissions changes.
