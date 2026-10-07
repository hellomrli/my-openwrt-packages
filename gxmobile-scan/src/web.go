package main

import (
	"crypto/subtle"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"
)

type chanView struct {
	Name   string `json:"name"`
	Group  string `json:"group"`
	Winner int    `json:"winner"`
	Tag    string `json:"tag"`
	Live   int    `json:"live"`
	Gone   int    `json:"gone"`
	URL    string `json:"url"`
}

func jsonResponse(w http.ResponseWriter, value any) {
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "no-store")
	json.NewEncoder(w).Encode(value)
}

func decodeRequest(w http.ResponseWriter, r *http.Request, value any) bool {
	if r.Method != http.MethodPost {
		http.Error(w, "POST only", 405)
		return false
	}
	d := json.NewDecoder(http.MaxBytesReader(w, r.Body, 4096))
	d.DisallowUnknownFields()
	if err := d.Decode(value); err != nil {
		http.Error(w, "无效 JSON 请求", 400)
		return false
	}
	if d.Decode(new(any)) != io.EOF {
		http.Error(w, "请求包含多余数据", 400)
		return false
	}
	return true
}

func channelViews(base *Baseline) []chanView {
	out := []chanView{}
	for _, ch := range base.Channels {
		v := chanView{Name: ch.Name, Group: ch.Group}
		var best *Source
		for i := range ch.Sources {
			s := &ch.Sources[i]
			if s.Gone {
				v.Gone++
				continue
			}
			v.Live++
			if best == nil || s.H > best.H || (s.H == best.H && s.Kbps > best.Kbps) {
				best = s
			}
		}
		if ch.LastWinner != nil {
			for i := range ch.Sources {
				s := &ch.Sources[i]
				if s.Cid == *ch.LastWinner && !s.Gone {
					best = s
					break
				}
			}
		}
		if best != nil {
			v.Winner, v.Tag = best.Cid, strings.Trim(resTag(best.H, best.Kbps), "()")
			v.URL = fmt.Sprintf("%s/%d/index.m3u8?servicetype=1", gateway, best.Cid)
		}
		out = append(out, v)
	}
	return out
}

func validName(s string) bool {
	return strings.TrimSpace(s) != "" && len(s) <= 200 && !strings.ContainsAny(s, "\r\n\x00\"")
}

func newWebHandler(auth string, base *Baseline) http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("/api/status", func(w http.ResponseWriter, r *http.Request) {
		state.mu.Lock()
		st := map[string]any{"running": state.Running, "step": state.Step, "startedAt": state.StartedAt,
			"lastRun": state.LastRun, "exists": state.Exists, "lastDiff": state.LastDiff, "err": state.Err}
		state.mu.Unlock()
		baseMu.RLock()
		st["updated"], st["channels"], st["sources"] = base.Updated, len(base.Channels), activeSrc(base)
		baseMu.RUnlock()
		st["gateway"], st["range"], st["workers"] = gateway, fmt.Sprintf("%d-%d", defaultLo, defaultHi), workers
		jsonResponse(w, st)
	})
	mux.HandleFunc("/api/channels", func(w http.ResponseWriter, r *http.Request) {
		baseMu.RLock()
		out := channelViews(base)
		baseMu.RUnlock()
		jsonResponse(w, out)
	})
	mux.HandleFunc("/api/scan", func(w http.ResponseWriter, r *http.Request) {
		var req struct {
			Mode  string `json:"mode"`
			Range string `json:"range"`
		}
		if !decodeRequest(w, r, &req) {
			return
		}
		if req.Mode != "fast" && req.Mode != "full" {
			http.Error(w, "无效扫描模式", 400)
			return
		}
		lo, hi := defaultLo, defaultHi
		if req.Range != "" {
			var err error
			lo, hi, err = parseRange(req.Range)
			if err != nil {
				http.Error(w, err.Error(), 400)
				return
			}
		}
		// Reserve before acknowledging, so simultaneous requests cannot both succeed.
		if !jobMu.TryLock() {
			http.Error(w, "已有扫描或编辑任务在运行", 409)
			return
		}
		state.mu.Lock()
		state.Running = true
		state.Step = "等待扫描启动"
		state.mu.Unlock()
		go func() {
			defer jobMu.Unlock()
			if _, err := runScanLocked(base, baselinePath, lo, hi, req.Mode == "fast"); err != nil {
				logf("扫描失败：%v", err)
			}
		}()
		jsonResponse(w, map[string]bool{"started": true})
	})
	for _, action := range []string{"rename", "delete"} {
		action := action
		mux.HandleFunc("/api/"+action, func(w http.ResponseWriter, r *http.Request) {
			var req struct {
				Name    string `json:"name"`
				NewName string `json:"newName"`
				Group   string `json:"group"`
			}
			if !decodeRequest(w, r, &req) {
				return
			}
			if !validName(req.Name) || (action == "rename" && (!validName(req.NewName) || !validName(req.Group))) {
				http.Error(w, "名称或分组无效：不可为空、超过 200 字节或包含换行/双引号", 400)
				return
			}
			if !jobMu.TryLock() {
				http.Error(w, "扫描期间不能修改频道", 409)
				return
			}
			defer jobMu.Unlock()
			unlock := acquireLock()
			if unlock == nil {
				http.Error(w, "扫描锁被占用", 409)
				return
			}
			defer unlock()
			baseMu.RLock()
			work := cloneBaseline(base)
			baseMu.RUnlock()
			found := false
			for _, ch := range work.Channels {
				if action == "rename" && req.NewName != req.Name && ch.Name == req.NewName {
					http.Error(w, "频道名称已存在", 409)
					return
				}
			}
			for i := range work.Channels {
				if work.Channels[i].Name != req.Name {
					continue
				}
				if action == "delete" {
					work.Channels = append(work.Channels[:i], work.Channels[i+1:]...)
				} else {
					work.Channels[i].Name, work.Channels[i].Group = req.NewName, req.Group
				}
				found = true
				break
			}
			if !found {
				http.Error(w, "频道不存在", 404)
				return
			}
			if len(work.Channels) == 0 {
				http.Error(w, "不能删除最后一个频道", 400)
				return
			}
			if _, err := writePlaylist(work); err != nil {
				http.Error(w, err.Error(), 500)
				return
			}
			if err := saveBaseline(work, baselinePath); err != nil {
				http.Error(w, err.Error(), 500)
				return
			}
			baseMu.Lock()
			*base = *work
			baseMu.Unlock()
			if err := publishToWWW(); err != nil {
				http.Error(w, err.Error(), 500)
				return
			}
			jsonResponse(w, map[string]bool{"ok": true})
		})
	}
	mux.HandleFunc("/api/report", func(w http.ResponseWriter, r *http.Request) {
		data, err := os.ReadFile(filepath.Join(outDir, "扫描报告.txt"))
		if err != nil && !os.IsNotExist(err) {
			http.Error(w, err.Error(), 500)
			return
		}
		jsonResponse(w, map[string]string{"report": string(data)})
	})
	mux.HandleFunc("/download/m3u", func(w http.ResponseWriter, r *http.Request) {
		http.ServeFile(w, r, filepath.Join(outDir, "广西移动IPTV_去重版.m3u"))
	})
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// LuCI talks through rpcd/curl on loopback. Browsers must not bypass its
		// session/ACL checks through a cross-origin request to this backend.
		if r.Header.Get("Origin") != "" || r.Header.Get("Sec-Fetch-Site") == "cross-site" {
			http.Error(w, "通过 LuCI 操作", 403)
			return
		}
		if auth != "" {
			u, p, ok := r.BasicAuth()
			if !ok || subtle.ConstantTimeCompare([]byte(u+":"+p), []byte(auth)) != 1 {
				w.Header().Set("WWW-Authenticate", `Basic realm="gxmobile"`)
				http.Error(w, "需要认证", 401)
				return
			}
		}
		mux.ServeHTTP(w, r)
	})
}

func runWeb(addr, auth string, base *Baseline) {
	logf("后台 API：%s，输出目录：%s", addr, outDir)
	srv := &http.Server{Addr: addr, Handler: newWebHandler(auth, base), ReadHeaderTimeout: 5 * time.Second,
		ReadTimeout: 10 * time.Second, WriteTimeout: 15 * time.Second, IdleTimeout: 30 * time.Second}
	if err := srv.ListenAndServe(); err != nil {
		logf("后台退出：%v", err)
		os.Exit(1)
	}
}
