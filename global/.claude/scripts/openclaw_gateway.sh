# openclaw_gateway.sh: the shell twin of openclaw_gateway.py. Source it; it holds no logic of its own (the route is chosen in Python).
#
#   . "$here/openclaw_gateway.sh"
#   oc_gateway [-u root] [-t] [--wsl] -- cmd args...    run cmd inside the OpenClaw gateway; stdin and the exit code pass through
#   oc_gateway_workspace                                the front desk's workspace folder, or nothing (return 1) when there is none
#   oc_gateway_home                                     the gateway user's home as the gateway shell sees it
#
# `timeout` cannot run a shell function: wrap the python call instead, `timeout 90 python3 "$oc_gateway_py" exec [-u root] -- cmd ...`.
oc_gateway_py="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/openclaw_gateway.py"
oc_gateway() { python3 "$oc_gateway_py" exec "$@"; }
oc_gateway_workspace() { python3 "$oc_gateway_py" workspace; }
oc_gateway_home() { python3 "$oc_gateway_py" home; }
