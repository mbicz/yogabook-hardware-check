#!/bin/sh
# ybcheck: {"id":"rtc.time_advances","component":"rtc","layer":"headless","timeout":20}
# Standalone: sh test_rtc.sh. No RTC alarm, clock write, wake, or suspend operation.
set -u
LC_ALL=C
export LC_ALL
for program in cat timeout sleep readlink; do
    command -v "$program" >/dev/null 2>&1 || { echo "blocked: missing program $program"; exit 78; }
done
found=0
for rtc in /sys/class/rtc/rtc*; do
    [ -d "$rtc" ] || continue
    found=1
    [ -r "$rtc/name" ] || { echo "blocked: cannot read $rtc/name"; exit 78; }
    name=$(timeout 3 cat "$rtc/name") || { echo "FAIL: RTC name read failed: $rtc"; exit 1; }
    [ -n "$name" ] || { echo "FAIL: empty RTC driver name: $rtc"; exit 1; }
    echo "rtc=$rtc name=$name resolved=$(readlink -f "$rtc")"
    parent=$(readlink -f "$rtc")
    bound=0
    while [ "$parent" != / ] && [ -n "$parent" ]; do
        if [ -L "$parent/driver" ]; then
            echo "driver=$(readlink -f "$parent/driver") ancestor=$parent"
            bound=1
            break
        fi
        parent=${parent%/*}
    done
    [ "$bound" -eq 1 ] || { echo "FAIL: RTC has no bound driver ancestor"; exit 1; }
    # since_epoch is obtained from the RTC driver, NOT the system wall clock.
    if [ -e "$rtc/since_epoch" ]; then
        [ -r "$rtc/since_epoch" ] || { echo "blocked: permission to read $rtc/since_epoch"; exit 78; }
        before=$(timeout 3 cat "$rtc/since_epoch") || { echo "FAIL: RTC read failed: $rtc"; exit 1; }
        sleep 2
        after=$(timeout 3 cat "$rtc/since_epoch") || { echo "FAIL: RTC second read failed: $rtc"; exit 1; }
    else
        # GNU date only parses RTC-provided date/time; it is not sampled as proof.
        command -v date >/dev/null 2>&1 || { echo "blocked: missing date for RTC date/time parsing"; exit 78; }
        [ -r "$rtc/date" ] && [ -r "$rtc/time" ] || { echo "blocked: RTC date/time not readable: $rtc"; exit 78; }
        date1=$(timeout 3 cat "$rtc/date") || exit 1
        time1=$(timeout 3 cat "$rtc/time") || exit 1
        before=$(date -u -d "$date1 $time1" +%s) || { echo "blocked: date lacks GNU RTC parsing support"; exit 78; }
        sleep 2
        date2=$(timeout 3 cat "$rtc/date") || exit 1
        time2=$(timeout 3 cat "$rtc/time") || exit 1
        after=$(date -u -d "$date2 $time2" +%s) || { echo "blocked: cannot parse RTC date/time"; exit 78; }
    fi
    case "$before:$after" in
        *[!0-9:]*|:*|*:) echo "FAIL: invalid RTC samples $before $after"; exit 1 ;;
    esac
    echo "rtc=$rtc before_epoch=$before after_epoch=$after"
    [ "$after" -gt "$before" ] || { echo "FAIL: RTC time did not advance"; exit 1; }
    [ $((after - before)) -le 10 ] || { echo "FAIL: RTC jumped unexpectedly"; exit 1; }
done
[ "$found" -eq 1 ] || { echo "FAIL: required onboard RTC missing"; exit 1; }
echo "PASS: RTC driver time advanced; absolute accuracy, battery retention, alarms and wake are untested"
exit 0
