#!/bin/sh
# Builds the standard-library-only identity echo Lambda. PYTHON selects the interpreter.
set -e
mkdir -p "${1:?output dir}"
"${PYTHON:-python3}" -c 'import sys,zipfile; z=zipfile.ZipFile(sys.argv[2],"w",zipfile.ZIP_DEFLATED); z.write(sys.argv[1],"handler.py"); z.close()' \
  "$(dirname "$0")/handler.py" "$1/resource-api.zip"
