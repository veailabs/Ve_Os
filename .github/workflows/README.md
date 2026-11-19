# Ve OS workflows

`test.yml` runs translation checks and the full Linux VM suite for ready pull requests into `master` and manual runs. Drafts skip every job. Its final `ci-ok` job is the intended required check after a successful run has been verified.

`visual-evidence.yml` asks for screenshots on visible changes; it remains advisory and skips drafts. `i18n-sync.yml` is restricted to the upstream repository: the Ve fork does not activate upstream translation credentials or automatic merges.
