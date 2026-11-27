package main

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func setup(t *testing.T) *Baseline {
	t.Helper()
	jobMu.Lock()
	jobMu.Unlock()
	outDir = t.TempDir()
	publishDir = t.TempDir()
	baselinePath = filepath.Join(outDir, "iptv_baseline.json")
	workers, measureWorkers = 2, 1
	state = scanState{}
	defaultLo, defaultHi = 1, 2
	return &Baseline{Gateway: gateway, Channels: []Channel{{Name: "测试频道", Group: "测试", Sources: []Source{{Cid: 1, H: 1080, Kbps: 3}}}}}
}

func TestRangeLimits(t *testing.T) {
	for _, value := range []string{"", "1", "2-1", "0-1", "1-5001", "1-2 trailing", "1-4294967296"} {
		if _, _, err := parseRange(value); err == nil {
			t.Errorf("accepted %q", value)
		}
	}
	if lo, hi, err := parseRange("3221225600-3221226800"); err != nil || lo != 3221225600 || hi != 3221226800 {
		t.Fatalf("64-bit CID: %v %v %v", lo, hi, err)
	}
}

func TestFailedScanPreservesFiles(t *testing.T) {
	for _, status := range []int{404, 500, 200} {
		t.Run(fmt.Sprint(status), func(t *testing.T) {
			base := setup(t)
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { w.WriteHeader(status); fmt.Fprint(w, "not a playlist") }))
			defer server.Close()
			gateway = server.URL
			os.WriteFile(baselinePath, []byte("unchanged baseline"), 0644)
			playlist := filepath.Join(outDir, "广西移动IPTV_去重版.m3u")
			os.WriteFile(playlist, []byte("unchanged playlist"), 0644)
			if _, err := runScan(base, baselinePath, 1, 2, true); err == nil {
				t.Fatal("failed gateway was accepted")
			}
			data, _ := os.ReadFile(playlist)
			if string(data) != "unchanged playlist" {
				t.Fatal("playlist overwritten on failure")
			}
			data, _ = os.ReadFile(baselinePath)
			if string(data) != "unchanged baseline" || base.Channels[0].Sources[0].Gone {
				t.Fatal("baseline modified on failure")
			}
		})
	}
}

func TestSegmentDuration(t *testing.T) {
	setup(t)
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if strings.HasSuffix(r.URL.Path, ".ts") {
			fmt.Fprint(w, strings.Repeat("x", 1000))
			return
		}
		fmt.Fprint(w, "#EXTM3U\n#EXTINF:2.0,\nsegment.ts\n")
	}))
	defer server.Close()
	gateway = server.URL
	_, rate := measureSegment(1)
	if rate != 0.004 {
		t.Fatalf("expected 0.004 Mbps from a 2s segment, got %v", rate)
	}
}

func TestReportPublishedAfterCurrentScan(t *testing.T) {
	base := setup(t)
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { fmt.Fprint(w, "#EXTM3U\n#EXTINF:10,\nx.ts\n") }))
	defer server.Close()
	gateway = server.URL
	if _, err := runScan(base, baselinePath, 1, 1, true); err != nil {
		t.Fatal(err)
	}
	report, err := os.ReadFile(filepath.Join(publishDir, "扫描报告.txt"))
	if err != nil || !strings.Contains(string(report), "扫描范围: 1-1") {
		t.Fatalf("report missing or stale: %s %v", report, err)
	}
	var saved Baseline
	data, _ := os.ReadFile(baselinePath)
	if json.Unmarshal(data, &saved) != nil || saved.Gateway != gateway {
		t.Fatal("gateway was not persisted")
	}
}

func TestRefreshWebPlaylistUsesCurrentGateway(t *testing.T) {
	base := setup(t)
	gateway = "http://old.invalid:8080"
	base.Gateway = gateway
	base.Updated = "2026-01-02T03:04:05Z"
	winner := 1
	base.Channels[0].LastWinner = &winner
	base.Channels[0].Sources = append(base.Channels[0].Sources, Source{Cid: 2, H: 1080, Kbps: 3.1})
	if err := refreshWebPlaylist(base); err != nil {
		t.Fatal(err)
	}
	before, err := json.Marshal(base)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(baselinePath, before, 0644); err != nil {
		t.Fatal(err)
	}
	report := []byte("previous scan report")
	if err := os.WriteFile(filepath.Join(outDir, "扫描报告.txt"), report, 0644); err != nil {
		t.Fatal(err)
	}
	// No server is listening here: startup must not probe or scan the gateway.
	gateway = "http://new.invalid:8080"
	if err := refreshWebPlaylist(base); err != nil {
		t.Fatal(err)
	}
	for _, directory := range []string{outDir, publishDir} {
		data, err := os.ReadFile(filepath.Join(directory, "广西移动IPTV_去重版.m3u"))
		if err != nil || strings.Contains(string(data), "old.invalid") ||
			!strings.Contains(string(data), "\n"+gateway+"/1/index.m3u8?servicetype=1\n") {
			t.Fatalf("stale gateway or winner in %s: %s (%v)", directory, data, err)
		}
		data, err = os.ReadFile(filepath.Join(directory, "扫描报告.txt"))
		if err != nil || string(data) != string(report) {
			t.Fatalf("scan report changed: %s (%v)", data, err)
		}
	}
	after, err := os.ReadFile(baselinePath)
	if err != nil || string(after) != string(before) {
		t.Fatalf("persisted baseline changed during startup: %s (%v)", after, err)
	}
	if base.Updated != "2026-01-02T03:04:05Z" || *base.Channels[0].LastWinner != 1 || !state.LastRun.IsZero() {
		t.Fatal("startup changed scan metadata or the stable winner")
	}
}

func TestRefreshWebPlaylistWithoutPublishing(t *testing.T) {
	base := setup(t)
	publishDir = ""
	if err := refreshWebPlaylist(base); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(filepath.Join(outDir, "广西移动IPTV_去重版.m3u")); err != nil {
		t.Fatal(err)
	}
}

func TestRefreshWebPlaylistReportsWriteAndPublishFailures(t *testing.T) {
	for _, failPublish := range []bool{false, true} {
		t.Run(fmt.Sprint(failPublish), func(t *testing.T) {
			base := setup(t)
			if failPublish {
				publishDir = filepath.Join(outDir, "not-a-directory")
				if err := os.WriteFile(publishDir, []byte("keep"), 0644); err != nil {
					t.Fatal(err)
				}
			} else {
				outDir = filepath.Join(outDir, "missing")
			}
			if err := refreshWebPlaylist(base); err == nil {
				t.Fatal("startup write failure was ignored")
			}
		})
	}
}

func TestKernelLockCanBeReacquired(t *testing.T) {
	setup(t)
	unlock := acquireLock()
	if unlock == nil {
		t.Fatal("first lock failed")
	}
	if other := acquireLock(); other != nil {
		other()
		t.Fatal("concurrent lock succeeded")
	}
	unlock()
	unlock = acquireLock()
	if unlock == nil {
		t.Fatal("leftover lock file blocked next run")
	}
	unlock()
}

func TestAtomicWriteFailureLeavesOldFile(t *testing.T) {
	setup(t)
	destination := filepath.Join(outDir, "directory")
	os.Mkdir(destination, 0755)
	os.WriteFile(filepath.Join(destination, "sentinel"), []byte("keep"), 0644)
	if err := atomicWrite(destination, []byte("replace")); err == nil {
		t.Fatal("expected rename failure")
	}
	if data, _ := os.ReadFile(filepath.Join(destination, "sentinel")); string(data) != "keep" {
		t.Fatal("old content lost")
	}
	matches, _ := filepath.Glob(filepath.Join(outDir, ".gxmobile-*"))
	if len(matches) != 0 {
		t.Fatal("temporary files leaked")
	}
}

func TestConcurrentAPIJobsAndStatus(t *testing.T) {
	base := setup(t)
	gate := make(chan struct{})
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { <-gate; fmt.Fprint(w, "#EXTM3U\n") }))
	defer upstream.Close()
	gateway = upstream.URL
	h := newWebHandler("", base)
	request := func(path, body string) *httptest.ResponseRecorder {
		w := httptest.NewRecorder()
		h.ServeHTTP(w, httptest.NewRequest("POST", path, strings.NewReader(body)))
		return w
	}
	if w := request("/api/scan", `{"mode":"fast","range":"1-1"}`); w.Code != 200 {
		t.Fatal(w.Body.String())
	}
	if w := request("/api/scan", `{"mode":"fast"}`); w.Code != 409 {
		t.Errorf("second job: %d", w.Code)
	}
	if w := request("/api/rename", `{"name":"测试频道","newName":"修改","group":"测试"}`); w.Code != 409 {
		t.Errorf("edit during scan: %d", w.Code)
	}
	for i := 0; i < 20; i++ {
		w := httptest.NewRecorder()
		h.ServeHTTP(w, httptest.NewRequest("GET", "/api/status", nil))
		if w.Code != 200 {
			t.Error("status blocked during scan")
		}
	}
	close(gate)
	finished := make(chan struct{})
	go func() { jobMu.Lock(); jobMu.Unlock(); close(finished) }()
	select {
	case <-finished:
	case <-time.After(5 * time.Second):
		t.Fatal("scan did not finish")
	}
	if state.Err != "" {
		t.Fatal(state.Err)
	}
}

func TestAPIValidationAndOrigin(t *testing.T) {
	base := setup(t)
	h := newWebHandler("", base)
	for _, body := range []string{`{`, `{"mode":"oops"}`, `{"mode":"fast","range":"1-9999999"}`, `{"mode":"fast"} {}`} {
		w := httptest.NewRecorder()
		h.ServeHTTP(w, httptest.NewRequest("POST", "/api/scan", strings.NewReader(body)))
		if w.Code != 400 {
			t.Errorf("invalid body %s: %d", body, w.Code)
		}
	}
	w := httptest.NewRecorder()
	r := httptest.NewRequest("POST", "/api/scan", strings.NewReader(`{"mode":"fast"}`))
	r.Header.Set("Origin", "https://example.com")
	h.ServeHTTP(w, r)
	if w.Code != 403 {
		t.Fatal("cross-origin browser bypass accepted")
	}
}
