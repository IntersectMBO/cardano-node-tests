#!/usr/bin/env bash

cardano_cli_log() {
  local out retval _
  echo cardano-cli "$@" >> "$START_CLUSTER_LOG"

  for _ in {1..3}; do
    retval=0
    { out="$(cardano-cli "$@" 2>&1)"; } || retval="$?"

    case "$out" in
      *"resource vanished"*)
        printf "Retrying \`cardano-cli %s\`. Failure:\n%s\n" "$*" "$out" >&2
        sleep 1
        ;;
      *)
        if [ -n "$out" ]; then echo "$out"; fi
        break
        ;;
    esac
  done

  return "$retval"
}

check_spend_success() {
  for _ in {1..10}; do
    if ! cardano_cli_log latest query utxo "$@" \
      --testnet-magic "$NETWORK_MAGIC" --output-text | grep -q lovelace; then
      return 0
    fi
    sleep 3
  done
  return 1
}

get_txins() {
  local txin_addr stop_txin_amount txhash txix amount _

  txin_addr="${1:?"Missing TxIn address"}"
  stop_txin_amount="${2:?"Missing stop TxIn amount"}"

  stop_txin_amount="$((stop_txin_amount + 2000000))"

  # Repeat in case `query utxo` fails
  for _ in {1..3}; do
    TXINS=()
    TXIN_AMOUNT=0
    while read -r txhash txix amount _; do
      if [ -z "$txhash" ] || [ -z "$txix" ] || [ "$amount" -lt 1000000 ]; then
        continue
      fi
      TXIN_AMOUNT="$((TXIN_AMOUNT + amount))"
      TXINS+=("--tx-in" "${txhash}#${txix}")
      if [ "$TXIN_AMOUNT" -ge "$stop_txin_amount" ]; then
        break
      fi
    done <<< "$(cardano_cli_log latest query utxo \
                --testnet-magic "$NETWORK_MAGIC" \
                --output-text \
                --address "$txin_addr" |
                grep -E "lovelace$|[0-9]$|lovelace \+ TxOutDatumNone$")"

    if [ "$TXIN_AMOUNT" -ge "$stop_txin_amount" ]; then
      break
    fi
  done
}

get_address_balance() {
  local txhash txix amount total_amount _

  # Repeat in case `query utxo` fails
  for _ in {1..3}; do
    total_amount=0
    while read -r txhash txix amount _; do
      if [ -z "$txhash" ] || [ -z "$txix" ]; then
        continue
      fi
      total_amount="$((total_amount + amount))"
    done <<< "$(cardano-cli latest query utxo "$@" --output-text | grep " lovelace")"

    if [ "$total_amount" -gt 0 ]; then
      break
    fi
  done

  echo "$total_amount"
}

get_epoch() {
  cardano_cli_log latest query tip --testnet-magic "$NETWORK_MAGIC" | jq -r '.epoch'
}

get_slot() {
  local future_offset="${1:-0}"
  cardano_cli_log latest query tip --testnet-magic "$NETWORK_MAGIC" | jq -r ".slot + $future_offset"
}

get_era() {
  cardano_cli_log latest query tip --testnet-magic "$NETWORK_MAGIC" | jq -r ".era"
}

get_sec_to_epoch_end() {
  cardano_cli_log latest query tip --testnet-magic "$NETWORK_MAGIC" |
    jq -r "$SLOT_LENGTH * .slotsToEpochEnd | ceil"
}

wait_for_era() {
  local era

  for _ in {1..10}; do
    era="$(get_era)"
    if [ "$era" = "$1" ]; then
      return
    fi
    sleep 3
  done

  echo "Unexpected era '$era' instead of '$1'" >&2
  exit 1
}

wait_for_epoch() {
  local start_epoch
  local target_epoch="$1"
  local epochs_to_go=1
  local sec_to_epoch_end
  local sec_to_sleep
  local curr_epoch
  local _

  start_epoch="$(get_epoch)"

  if [ "$start_epoch" -ge "$target_epoch" ]; then
    return
  else
    epochs_to_go="$((target_epoch - start_epoch))"
  fi

  sec_to_epoch_end="$(get_sec_to_epoch_end)"
  sec_to_sleep="$(( sec_to_epoch_end + ((epochs_to_go - 1) * EPOCH_SEC) ))"
  sleep "$sec_to_sleep"

  for _ in {1..10}; do
    curr_epoch="$(get_epoch)"
    if [ "$curr_epoch" -ge "$target_epoch" ]; then
      return
    fi
    sleep 3
  done

  echo "Unexpected epoch '$curr_epoch' instead of '$target_epoch'" >&2
  exit 1
}
