# Browser integration defaults

This directory contains bundled extensions, signed Chrome packages, managed policy and native messaging host templates. Ve OS publishes the native host descriptions while preserving extension IDs and allowed origins.

The signed Chrome extensions remain upstream Omarchy artifacts, including their original descriptions. Their signing keys belong to upstream; changing the unpacked manifest without repacking would break source/package parity. `omarchy-dev-pack-chrome-extensions` documents that release process. No Ve-owned signing keys or Chrome extension releases are claimed here.
