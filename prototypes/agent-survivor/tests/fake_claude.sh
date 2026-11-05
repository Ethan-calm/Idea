#!/usr/bin/env bash
# Stand-in for `claude -p`: crashes if the persona is a brutalist, otherwise writes an index.html.
if [[ "$2" == *"brutalist"* ]]; then echo '{"is_error":true,"result":"boom"}'; exit 1; fi
echo "<h1>entry</h1>" > index.html
echo '{"is_error":false,"result":"done"}'
