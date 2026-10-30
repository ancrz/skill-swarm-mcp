"""Linux Landlock LSM In-Process Filesystem Sandboxing Engine (SPEC-4)."""

import ctypes
import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger("skill-swarm.sandbox")

SYS_LANDLOCK_CREATE_RULESET = 444
SYS_LANDLOCK_ADD_RULE = 445
SYS_LANDLOCK_RESTRICT_SELF = 446
PR_SET_NO_NEW_PRIVS = 38

LANDLOCK_CREATE_RULESET_VERSION = 1 << 0
LANDLOCK_RULE_PATH_BENEATH = 1

ACCESS_FS_EXECUTE = 1 << 0
ACCESS_FS_WRITE_FILE = 1 << 1
ACCESS_FS_READ_FILE = 1 << 2
ACCESS_FS_READ_DIR = 1 << 3
ACCESS_FS_REMOVE_DIR = 1 << 4
ACCESS_FS_REMOVE_FILE = 1 << 5
ACCESS_FS_MAKE_CHAR = 1 << 6
ACCESS_FS_MAKE_DIR = 1 << 7
ACCESS_FS_MAKE_REG = 1 << 8
ACCESS_FS_MAKE_SOCK = 1 << 9
ACCESS_FS_MAKE_FIFO = 1 << 10
ACCESS_FS_MAKE_BLOCK = 1 << 11
ACCESS_FS_MAKE_SYM = 1 << 12
ACCESS_FS_REFER = 1 << 13
ACCESS_FS_TRUNCATE = 1 << 14

READ_ACCESS_MASK = ACCESS_FS_EXECUTE | ACCESS_FS_READ_FILE | ACCESS_FS_READ_DIR
WRITE_ACCESS_MASK = (
    ACCESS_FS_WRITE_FILE
    | ACCESS_FS_REMOVE_DIR
    | ACCESS_FS_REMOVE_FILE
    | ACCESS_FS_MAKE_REG
    | ACCESS_FS_MAKE_DIR
    | ACCESS_FS_TRUNCATE
)
FULL_FS_ACCESS_V1 = (1 << 13) - 1
FULL_FS_ACCESS_V3 = (1 << 15) - 1


class LandlockRulesetAttr(ctypes.Structure):
    _fields_ = [("handled_access_fs", ctypes.c_uint64)]


class LandlockPathBeneathAttr(ctypes.Structure):
    _fields_ = [
        ("allowed_access", ctypes.c_uint64),
        ("parent_fd", ctypes.c_int32),
    ]


def _get_libc() -> ctypes.CDLL | None:
    try:
        return ctypes.CDLL("libc.so.6", use_errno=True)
    except Exception as e:
        logger.debug("libc.so.6 unavailable: %s", e)
        return None


def get_landlock_status() -> dict[str, Any]:
    """Check kernel Landlock LSM support fail-soft without raising exceptions."""
    try:
        libc = _get_libc()
        if libc is None:
            return {"status": "UNSUPPORTED", "supported": False, "abi_version": 0, "message": "libc unavailable"}

        res = libc.syscall(SYS_LANDLOCK_CREATE_RULESET, 0, 0, LANDLOCK_CREATE_RULESET_VERSION)
        if res > 0:
            return {
                "status": "SUPPORTED",
                "supported": True,
                "abi_version": int(res),
                "features": {
                    "v1_basic": res >= 1,
                    "v2_refer": res >= 2,
                    "v3_truncate": res >= 3,
                },
            }
        errno = ctypes.get_errno()
        return {
            "status": "UNSUPPORTED",
            "supported": False,
            "abi_version": 0,
            "errno": errno,
            "message": f"Landlock unsupported on active kernel (errno {errno}).",
        }
    except Exception as e:
        logger.debug("Landlock status probe failed: %s", e)
        return {"status": "UNSUPPORTED", "supported": False, "abi_version": 0, "error": str(e)}


class LandlockSkillSandbox:
    """Constructor and runner for Landlock filesystem restriction rulesets."""

    def __init__(
        self,
        allowed_read_paths: list[str | Path] | None = None,
        allowed_write_paths: list[str | Path] | None = None,
    ):
        self.allowed_read_paths = [Path(p).resolve() for p in (allowed_read_paths or ["/"])]
        self.allowed_write_paths = [Path(p).resolve() for p in (allowed_write_paths or [])]

    def apply(self) -> dict[str, Any]:
        """Apply Landlock ruleset to the calling process."""
        status = get_landlock_status()
        if not status.get("supported"):
            return {"status": "UNSUPPORTED", "applied": False, "message": status.get("message")}

        abi = status.get("abi_version", 1)
        handled = FULL_FS_ACCESS_V3 if abi >= 3 else FULL_FS_ACCESS_V1
        read_mask = READ_ACCESS_MASK
        write_mask = (READ_ACCESS_MASK | WRITE_ACCESS_MASK) if abi < 3 else (READ_ACCESS_MASK | WRITE_ACCESS_MASK | ACCESS_FS_TRUNCATE)

        libc = _get_libc()
        if libc is None:
            return {"status": "UNSUPPORTED", "applied": False, "message": "libc unavailable"}

        attr = LandlockRulesetAttr(handled_access_fs=handled)
        ruleset_fd = libc.syscall(SYS_LANDLOCK_CREATE_RULESET, ctypes.byref(attr), ctypes.sizeof(attr), 0)
        if ruleset_fd < 0:
            errno = ctypes.get_errno()
            return {"status": "ERROR", "applied": False, "error": f"ruleset creation failed: errno {errno}"}

        rules_added = 0
        try:
            for rpath in self.allowed_read_paths:
                if not rpath.exists():
                    continue
                try:
                    fd = os.open(str(rpath), os.O_PATH | os.O_CLOEXEC)
                    try:
                        beneath = LandlockPathBeneathAttr(allowed_access=read_mask, parent_fd=fd)
                        if libc.syscall(SYS_LANDLOCK_ADD_RULE, ruleset_fd, LANDLOCK_RULE_PATH_BENEATH, ctypes.byref(beneath), 0) == 0:
                            rules_added += 1
                    finally:
                        os.close(fd)
                except OSError:
                    continue

            for wpath in self.allowed_write_paths:
                if not wpath.exists():
                    continue
                try:
                    fd = os.open(str(wpath), os.O_PATH | os.O_CLOEXEC)
                    try:
                        beneath = LandlockPathBeneathAttr(allowed_access=write_mask, parent_fd=fd)
                        if libc.syscall(SYS_LANDLOCK_ADD_RULE, ruleset_fd, LANDLOCK_RULE_PATH_BENEATH, ctypes.byref(beneath), 0) == 0:
                            rules_added += 1
                    finally:
                        os.close(fd)
                except OSError:
                    continue

            if libc.prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
                errno = ctypes.get_errno()
                return {"status": "ERROR", "applied": False, "error": f"prctl failed: errno {errno}"}

            if libc.syscall(SYS_LANDLOCK_RESTRICT_SELF, ruleset_fd, 0) != 0:
                errno = ctypes.get_errno()
                return {"status": "ERROR", "applied": False, "error": f"restrict_self failed: errno {errno}"}

            return {"status": "APPLIED", "applied": True, "abi_version": abi, "rules_added": rules_added}
        finally:
            os.close(ruleset_fd)

    def run_isolated_probe(self, probe_write_target: str | Path) -> dict[str, Any]:
        """Execute sandbox test in a fork without restricting parent process."""
        target_path = Path(probe_write_target).resolve()
        r_pipe, w_pipe = os.pipe()

        pid = os.fork()
        if pid == 0:
            os.close(r_pipe)
            apply_res = self.apply()
            probe_result = {
                "supported": apply_res.get("applied", False),
                "enforced": apply_res.get("applied", False),
                "write_attempted": str(target_path),
                "write_blocked": False,
                "error": None,
            }

            try:
                with open(target_path, "w", encoding="utf-8") as f:
                    f.write("probe")
                probe_result["write_blocked"] = False
            except PermissionError as pe:
                probe_result["write_blocked"] = True
                probe_result["error"] = str(pe)
            except Exception as e:
                probe_result["write_blocked"] = False
                probe_result["error"] = str(e)

            payload = json.dumps(probe_result).encode("utf-8")
            os.write(w_pipe, payload)
            os.close(w_pipe)
            os._exit(0)

        os.close(w_pipe)
        data = b""
        while chunk := os.read(r_pipe, 4096):
            data += chunk
        os.close(r_pipe)
        os.waitpid(pid, 0)

        try:
            return json.loads(data.decode("utf-8"))
        except Exception as e:
            return {"status": "ERROR", "error": f"Failed decoding probe output: {e}"}
