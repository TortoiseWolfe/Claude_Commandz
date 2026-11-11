"""wsl_reserve.py: the memory reserve values shared by wsl_bridge_watchdog.py and wsl_root_check.py.

It stands alone (standard library only) because the root check runs as root from a root-owned folder (/usr/local/lib/afa-root-check),
beside wsl_root_check.py and nothing else: root never imports code from a folder a person's own processes can write.
"""

# Reserve of free memory the kernel keeps by reclaiming clean cache, so freed pages can merge back into big blocks.
#  min_free_kbytes 1 GiB: 2.5% of the 40 GB VM. WSL returns free pages to Windows (free page reporting), so it costs nothing real.
#  watermark_scale_factor 50: kswapd wakes below min + 0.5% of RAM (~1.2 GB free) and stops at ~1.4 GB; the default 10 left ~50 MB.
#  compaction_proactiveness 30: the kernel default is 20, WSL's build sets 0. Background compaction starts well before order-7 runs out.
DESIRED = {
    "vm.min_free_kbytes": 1048576,
    "vm.watermark_scale_factor": 50,
    "vm.compaction_proactiveness": 30,
}


def sysctl_path(key):
    return "/proc/sys/" + key.replace(".", "/")


def read_file(path):
    with open(path) as f:
        return f.read()


def sysctl_drift(read=read_file):
    """{key: current} for each reserve value that is not what DESIRED says (a missing file counts as drift)."""
    drift = {}
    for key, want in DESIRED.items():
        try:
            cur = int(read(sysctl_path(key)).strip())
        except (OSError, ValueError):
            cur = None
        if cur != want:
            drift[key] = cur
    return drift
