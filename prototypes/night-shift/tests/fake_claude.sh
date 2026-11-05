#!/usr/bin/env bash
# Stand-in for `claude -p`. Behaviour is chosen by words in the prompt.
prompt="$2"
if [[ "$prompt" == *"LIMIT"* ]]; then
  echo '{"type":"result","is_error":true,"result":"Claude usage limit reached. Resets at 3am."}'; exit 1
fi
echo "edited by fake agent" >> agent_output.txt
if [[ "$prompt" == *"BLOCK"* ]]; then st=blocked; dec='["Need an API key for the payment sandbox"]'; else st=done; dec='[]'; fi
python3 -c 'import json,sys; print(json.dumps({"type":"result","is_error":False,"result":"Worked on it.\n```json\n"+json.dumps({"status":sys.argv[1],"summary":"fake summary","decisions":json.loads(sys.argv[2])})+"\n```"}))' "$st" "$dec"
