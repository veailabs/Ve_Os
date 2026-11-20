#!/bin/bash

set -euo pipefail
source "$(dirname "$0")/base-test.sh"
source "$ROOT/test/fixtures/startup-pair.sh"

read -r answer ready <<<"$(best_startup_pair 478 1242 291 1204 464 1279)"
[[ $answer == 478 && $ready == 1242 ]] || fail "recorded cold starts retain the best normalized real pair" "Selected $answer/$ready instead of 478/1242"
(( ready * 100 <= answer * 400 )) || fail "recorded passing cold start satisfies the unchanged 400 percent bound"
(( 1204 * 100 > 291 * 400 )) || fail "recorded independent minima reproduce the false failure"
pass "recorded startup timings avoid the independent-minima false failure"

read -r answer ready <<<"$(best_startup_pair 478 2000 291 1300 464 1900)"
(( ready * 100 > answer * 400 )) || fail "all slow cold starts still fail the unchanged bound"
pass "all slow cold starts remain outside the startup budget"

read -r answer ready <<<"$(best_startup_pair 100 250 200 500 300 750)"
[[ $answer == 100 && $ready == 250 ]] || fail "equal ratios preserve a complete first pair"
pass "equal normalized times retain one observed pair"

for invalid in '0 1204' '291 0' '-1 100' 'x 100' '100'; do
  read -r -a values <<<"$invalid"
  if best_startup_pair "${values[@]}" >/dev/null; then fail "invalid startup sample is rejected: $invalid"; fi
done
if best_startup_pair >/dev/null; then fail "empty startup samples are rejected"; fi
if best_startup_pair 478 1242 0 100 >/dev/null; then fail "a later invalid sample is rejected"; fi
pass "empty, incomplete, zero, negative and nonnumeric samples are rejected"
