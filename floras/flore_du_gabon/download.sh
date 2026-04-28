#!/bin/bash
# Download all 61 Flore du Gabon PDFs from Zenodo

RECORD_IDS=(
    14910050 14900487 14900391
    11079343 11079277 11079224 11077824 11077792 11077761
    11074384 11074363 11073959 11073761 11073479 11072829
    11072743 11072660 11072531 11072140 11072103 11072024
    11068388 11068345 11068267 11066135 11066062 11065995
    11065903 11064952 11064786 11061591 11061516 11061444
    11061402 11061373 11059544 11059484 11059446 11059383
    11059307 11039844 11039572 11039294 11038976 11038686
    11032174 11032134 11032099 11005131 11005077 11004874
    11004851 11004527 11004506 11004467 11002410 11002365
    11002335 11002291 11002006 11001797
)

SUCCESS=0
FAILED=0
SKIPPED=0

for REC_ID in "${RECORD_IDS[@]}"; do
    echo "--- Record $REC_ID ---"

    # Get file info from API
    API_RESP=$(curl -s --max-time 30 "https://zenodo.org/api/records/$REC_ID")
    if [ $? -ne 0 ]; then
        echo "  ERROR: Failed to query API for $REC_ID"
        FAILED=$((FAILED+1))
        continue
    fi

    # Extract filename and download URL
    FILENAME=$(echo "$API_RESP" | python3 -c "
import json, sys, urllib.parse
data = json.load(sys.stdin)
files = data.get('files', [])
if files:
    key = files[0]['key']
    print(key)
else:
    print('')
" 2>/dev/null)

    if [ -z "$FILENAME" ]; then
        echo "  ERROR: No files found for record $REC_ID"
        FAILED=$((FAILED+1))
        continue
    fi

    # Skip if already downloaded
    if [ -f "$FILENAME" ]; then
        echo "  SKIPPED: '$FILENAME' already exists"
        SKIPPED=$((SKIPPED+1))
        continue
    fi

    # Build download URL (URL-encode the filename)
    ENCODED=$(python3 -c "import urllib.parse; print(urllib.parse.quote('$FILENAME'))")
    URL="https://zenodo.org/api/records/$REC_ID/files/$ENCODED/content"

    echo "  Downloading: $FILENAME"
    curl -L --max-time 300 --retry 3 --retry-delay 5 \
         -o "$FILENAME" "$URL"
    if [ $? -eq 0 ]; then
        echo "  OK: $FILENAME"
        SUCCESS=$((SUCCESS+1))
    else
        echo "  ERROR: Download failed for $FILENAME"
        rm -f "$FILENAME"
        FAILED=$((FAILED+1))
    fi

    # Small delay to be polite to the server
    sleep 1
done

echo ""
echo "============================="
echo "Done: $SUCCESS downloaded, $SKIPPED skipped, $FAILED failed"
