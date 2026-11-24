"""Set up Chromium's FD 3/4 transport, without a shell or a listening port."""

import fcntl
import os
import sys


def main():
    # Duplicate both ends above FD 4 before replacing either destination.
    reader = fcntl.fcntl(int(sys.argv[1]), fcntl.F_DUPFD_CLOEXEC, 10)
    writer = fcntl.fcntl(int(sys.argv[2]), fcntl.F_DUPFD_CLOEXEC, 10)
    os.dup2(reader, 3, inheritable=True)
    os.dup2(writer, 4, inheritable=True)
    for fd in {int(sys.argv[1]), int(sys.argv[2]), reader, writer} - {3, 4}:
        os.close(fd)
    os.execv(sys.argv[3], sys.argv[3:])


if __name__ == "__main__":
    main()
