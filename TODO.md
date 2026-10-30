# To do

Work that is decided but not started, and ideas that are not decided. Done items move to
the changelog.

## Next

- Keep the "Jump to bottom" pill from covering the last log lines, for example with some
  bottom padding on the log while the pill is visible.
- Document `FILE__TRUENAS_API_KEY` with a secret file as the recommended way to pass the
  TrueNAS key, since a plain environment variable is readable through `docker inspect`.
- Complete the GitHub wiki.

## Ideas

- A way to preview a deletion from the UI: Dry run with `--delete`. The API already
  accepts `dry_run` together with `delete`.
- Let each top card end at its own height instead of stretching to the tallest one.
  Declined for now: the aligned row looks tidier.
- Run the CI tests on pushes to `wip` without publishing an image.
- Browser tests of the UI with Playwright, in the test suite and in CI.
