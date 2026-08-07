# Cross-Platform Workspace Lock Research

## Scope

This note records fixed-Commit references for the v0.3.0 Workspace lock and
local PowerShell gate. The production change must remove unconditional
`fcntl` imports, retain POSIX `flock`, use the Windows standard-library
`msvcrt` primitive, and add no runtime dependency.

GitHub Code Search used:

```text
msvcrt.locking fcntl.flock language:Python
```

## filelock

- Repository: `tox-dev/filelock`
- Commit: `0e0f666c7d5d0a2e8231eac3c463ecd6a05d3f73`
- License: MIT
- Relevant code:
  - `src/filelock/_descriptor.py`
  - `src/filelock/_unix.py`
  - `src/filelock/_windows.py`
- Relevant tests:
  - `tests/test_filelock.py`
  - `tests/test_unix_fallback.py`

`filelock` selects its backend at import time, performs non-blocking native
attempts, and implements blocking behavior as a short polling loop. Its
descriptor API leaves opening, closing, and path ownership to the caller.
Tests cover round trips, contention, blocking without a busy loop, invalid
descriptors, explicit unlock, and compatibility between descriptor and path
locks. Lock files remain in place on POSIX because unlinking can split waiters
across different inodes.

MemoryForge adopts:

- platform-specific imports, so Windows never imports `fcntl`;
- one caller-owned descriptor API;
- a non-blocking primitive plus a bounded-frequency blocking loop;
- explicit unlock before descriptor close;
- stable lock-file identity and no unlink after release;
- contention, round-trip, and blocking-wait tests.

MemoryForge does not adopt:

- timeout, reentrancy, singleton, async, soft-lock fallback, or lock expiry;
- `ctypes`, `NtCreateFile`, `LockFileEx`, or fork ownership management;
- path cleanup or lock-file metadata.

Those features exceed the current single exclusive Workspace-writer contract.

## portalocker

- Repository: `wolph/portalocker`
- Commit: `88972ef4e0c9ac37792ace56ebfa4589ae6769af`
- License: BSD-3-Clause
- Relevant code:
  - `portalocker/portalocker.py`
- Relevant tests:
  - `portalocker_tests/test_core_locking.py`
  - `portalocker_tests/test_windows_locker.py`
  - `portalocker_tests/test_msvcrt_no_pywin32.py`
  - `portalocker_tests/test_posix_locker_dispatch.py`

`portalocker` uses `fcntl.flock` on POSIX and supports dependency-free
exclusive Windows locks through `msvcrt.locking`. Its Windows implementation
normalizes the file position to byte zero before locking and restores the
original position afterward. Tests prove that two descriptors contend on the
same byte range and that the exclusive path works without `pywin32`.

MemoryForge adopts:

- `msvcrt.locking` for exclusive Windows locks only;
- a fixed one-byte range starting at byte zero;
- file-position preservation around Windows lock and unlock calls;
- explicit classification of contention errors.

MemoryForge does not adopt:

- shared locks;
- `pywin32`;
- configurable locker dispatch;
- large byte ranges or file-object wrappers;
- Redis locks, semaphores, temporary locks, or PID locks.

## Local Design

`memoryforge.platform_lock` owns four operations:

```text
try_lock_descriptor(fd) -> bool
lock_descriptor(fd, poll_interval=0.05)
unlock_descriptor(fd)
exclusive_file_lock(path)
```

- POSIX uses `fcntl.flock`.
- Windows uses `msvcrt.locking`.
- Blocking acquisition polls the non-blocking primitive instead of relying on
  the Windows `LK_LOCK` ten-retry limit.
- The Windows byte range always starts at offset zero.
- The caller keeps descriptor ownership.
- `exclusive_file_lock` opens one regular file, verifies path/descriptor
  identity, acquires, yields, unlocks, and closes.
- `Workspace.exclusive_lock` uses the path-level helper.
- `ChangeSetStore` uses the same descriptor API on its already-open staging
  directory, preserving the existing POSIX inode/namespace serialization.
- ChangeSet directory operations remain outside the native Windows claim;
  replacing their `dir_fd` security model is separate work.

The native Windows smoke deliberately covers package import, CLI, Workspace
initialization/open, lock use, and an empty-workspace `unknown` query. It does
not claim full Windows parity for secure local import or ChangeSet directory
operations, which still rely on POSIX directory-descriptor semantics.

## Expected Improvement

- unconditional production imports of `fcntl`: 2 to 0;
- package and CLI import on native Windows: pass;
- POSIX descriptor contention: pass;
- simulated Windows byte-zero contention contract: pass;
- lock-file content mutation: zero;
- blocking busy loop: absent;
- native Windows CLI/Workspace/empty Demo smoke: pass;
- PowerShell local gate artifact contract: present;
- new runtime dependencies: zero;
- GitHub-hosted Actions: remain disabled.
