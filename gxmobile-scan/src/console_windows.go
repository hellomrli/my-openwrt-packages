//go:build windows

package main

import "syscall"

// 切换控制台到 UTF-8 代码页, 避免中文乱码
func fixConsole() {
	kernel32 := syscall.NewLazyDLL("kernel32.dll")
	proc := kernel32.NewProc("SetConsoleOutputCP")
	proc.Call(65001)
}
