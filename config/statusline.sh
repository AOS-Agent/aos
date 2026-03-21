#!/bin/bash
input=$(cat)

MODEL=$(echo "$input" | jq -r '.model.display_name // "?"' | sed 's/Opus 4.6 (1M context)/O4.6/' | sed 's/Sonnet 4.6/S4.6/' | sed 's/Haiku 4.5/H4.5/')
PCT=$(echo "$input" | jq -r '.context_window.used_percentage // 0' | cut -d. -f1)
COST=$(printf "%.2f" "$(echo "$input" | jq -r '.cost.total_cost_usd // 0')")
DUR_MS=$(echo "$input" | jq -r '.cost.total_duration_ms // 0')
LINES_ADD=$(echo "$input" | jq -r '.cost.total_lines_added // 0')
LINES_DEL=$(echo "$input" | jq -r '.cost.total_lines_removed // 0')

# Context bar
FILLED=$((PCT * 15 / 100))
EMPTY=$((15 - FILLED))
BAR=$(printf "%${FILLED}s" | tr ' ' '▓')$(printf "%${EMPTY}s" | tr ' ' '░')

# Color context percentage based on usage
if [ "$PCT" -ge 80 ]; then
  CLR="\033[31m"  # red
elif [ "$PCT" -ge 50 ]; then
  CLR="\033[33m"  # yellow
else
  CLR="\033[32m"  # green
fi
RST="\033[0m"

# Duration
DUR_SEC=$((DUR_MS / 1000))
MINS=$((DUR_SEC / 60))
SECS=$((DUR_SEC % 60))

GRN="\033[32m"
RED="\033[31m"

printf "${CLR}%s${RST} %s ${CLR}%s%%${RST}  \$%s  %dm%02ds  ${GRN}+%s${RST} ${RED}-%s${RST}" \
  "$MODEL" "$BAR" "$PCT" "$COST" "$MINS" "$SECS" "$LINES_ADD" "$LINES_DEL"
