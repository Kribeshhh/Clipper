"""Apply per-child limits before exec, outside the web/worker process's threads."""

import ctypes
import errno
import os
import resource
import sys


def restrict_linux_child():
    # The filter survives exec. Media tools need local files and encoder threads.
    lib = ctypes.CDLL("libseccomp.so.2")
    lib.seccomp_init.restype = ctypes.c_void_p
    lib.seccomp_init.argtypes = [ctypes.c_uint32]
    lib.seccomp_load.argtypes = [ctypes.c_void_p]
    lib.seccomp_release.argtypes = [ctypes.c_void_p]
    lib.seccomp_rule_add.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_int,
        ctypes.c_uint,
    ]
    lib.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
    allow = 0x7FFF0000
    deny = 0x00050000 | errno.EPERM
    ctx = lib.seccomp_init(allow)
    if not ctx:
        raise RuntimeError("Unable to initialize media process filter")
    try:
        for name in (
            b"socket",
            b"socketpair",
            b"ptrace",
            b"process_vm_readv",
            b"process_vm_writev",
            b"pidfd_getfd",
            b"io_uring_setup",
        ):
            syscall = lib.seccomp_syscall_resolve_name(name)
            if syscall >= 0 and lib.seccomp_rule_add(ctx, deny, syscall, 0):
                raise RuntimeError("Unable to restrict media process")
        if lib.seccomp_load(ctx):
            raise RuntimeError("Unable to apply media process filter")
    finally:
        lib.seccomp_release(ctx)


def main():
    timeout, max_bytes = map(int, sys.argv[1:3])
    for limit, value in [
        (resource.RLIMIT_CPU, timeout),
        (resource.RLIMIT_FSIZE, max_bytes),
        (resource.RLIMIT_NOFILE, 64),
        (resource.RLIMIT_CORE, 0),
    ]:
        resource.setrlimit(limit, (value, value))
    if sys.platform.startswith("linux"):
        restrict_linux_child()
        resource.setrlimit(resource.RLIMIT_AS, (2 * 1024**3, 2 * 1024**3))
    os.umask(0o077)
    os.execv(sys.argv[3], sys.argv[3:])


if __name__ == "__main__":
    main()
