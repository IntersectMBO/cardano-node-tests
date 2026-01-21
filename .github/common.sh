#!/usr/bin/env bash

is_truthy() {
  local val="${1:-}"
  val=${val,,}

  case "$val" in
    1 | true | yes | on | enabled )
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}
