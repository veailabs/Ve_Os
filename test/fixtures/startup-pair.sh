best_startup_pair() {
  (( $# > 0 && $# % 2 == 0 )) || return 2
  local answer ready best_answer="" best_ready=""
  while (( $# )); do
    answer=$1 ready=$2
    shift 2
    [[ $answer =~ ^[1-9][0-9]*$ && $ready =~ ^[1-9][0-9]*$ ]] || return 2
    if [[ -z $best_answer ]] || (( ready * best_answer < best_ready * answer )); then
      best_answer=$answer
      best_ready=$ready
    fi
  done
  printf '%s %s\n' "$best_answer" "$best_ready"
}
