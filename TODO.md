# To do

Work that is decided but not started, and ideas that are not decided. Done items move to
the changelog.

## Next

- Decide whether the container should run as a non-root user. Today root keeps only
  `DAC_READ_SEARCH` and `DAC_OVERRIDE`; a fixed user would need to read every mounted folder.

## Ideas

- Let each top card end at its own height instead of stretching to the tallest one.
  Declined for now: the aligned row looks tidier.
- Browser tests of the UI with Playwright, in the test suite and in CI.
