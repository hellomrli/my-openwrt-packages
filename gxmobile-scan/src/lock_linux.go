package main

import (
	"os"
	"path/filepath"
	"syscall"
)

// Kernel locks are released on exit, including a crash or power interruption.
// Never unlink this inode: another process may already have it open.
func acquireLock() func() {
	f, err := os.OpenFile(filepath.Join(outDir, ".scan.lock"), os.O_CREATE|os.O_RDWR, 0600)
	if err != nil {
		return nil
	}
	if syscall.Flock(int(f.Fd()), syscall.LOCK_EX|syscall.LOCK_NB) != nil {
		f.Close()
		return nil
	}
	return func() { syscall.Flock(int(f.Fd()), syscall.LOCK_UN); f.Close() }
}
